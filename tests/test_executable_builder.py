from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from helis.budget import CycleBudget
from helis.build_execution import (
    BuildExecutionConfigurationError,
    BuildExecutionResult,
    DockerBuildExecutionBackend,
)
from helis.builder_machine import BuilderMachine
from helis.builder_planner import BuilderPlanner
from helis.builder_sandbox import BuildVerifier
from helis.domain import (
    BuildBundle,
    BuildFile,
    BuildSpec,
    BuildTemplate,
    Opportunity,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.store import HelisStore


@dataclass(slots=True)
class PayloadProvider:
    payloads: list[dict]
    calls: int = 0
    users: list[str] = field(default_factory=list)

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        self.users.append(user)
        return ModelResult(
            content=json.dumps(self.payloads.pop(0)),
            prompt_tokens=10,
            completion_tokens=10,
        )


@dataclass(slots=True)
class FakeExecutionBackend:
    results: list[BuildExecutionResult]
    calls: int = 0
    workspaces: list[Path] = field(default_factory=list)

    def execute(self, workspace: str | Path) -> BuildExecutionResult:
        self.calls += 1
        root = Path(workspace)
        self.workspaces.append(root)
        assert (root / "app.py").exists()
        assert (root / "test_app.py").exists()
        return self.results.pop(0)


def _engine(tmp_path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def _validated(engine: HelisEngine) -> Opportunity:
    opportunity = Opportunity(
        title="Quote workflow executable MVP",
        problem="Small service teams repeatedly lose time calculating simple quote totals manually.",
        customer="small service teams",
        proposed_value="turn validated quote inputs into a deterministic structured result",
        stage=VentureStage.VALIDATED,
    )
    engine.ingest(opportunity)
    return opportunity


def _python_plan() -> dict:
    return {
        "template": "python_service_v1",
        "name": "Quote calculation core",
        "goal": "Turn a bounded quote request into a deterministic structured calculation result.",
        "acceptance_criteria": [
            "Return a structured successful result for a valid quantity",
            "Return a structured validation failure for an invalid quantity",
        ],
    }


def _static_plan() -> dict:
    return {
        "template": "static_web_v1",
        "name": "Quote landing preview",
        "goal": "Explain the validated quote workflow in a small local-only offer page.",
        "acceptance_criteria": ["Explain the problem", "Explain the bounded value"],
    }


def _good_bundle() -> dict:
    return {
        "files": [
            {
                "path": "app.py",
                "content": (
                    "def handle(request: dict) -> dict:\n"
                    "    quantity = request.get('quantity') if isinstance(request, dict) else None\n"
                    "    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:\n"
                    "        return {'ok': False, 'error': 'quantity must be a positive integer'}\n"
                    "    return {'ok': True, 'total_cents': quantity * 1000}\n"
                ),
            },
            {
                "path": "test_app.py",
                "content": (
                    "import unittest\n"
                    "from app import handle\n\n"
                    "class HandleTests(unittest.TestCase):\n"
                    "    def test_valid_quantity(self):\n"
                    "        self.assertEqual(handle({'quantity': 3}), {'ok': True, 'total_cents': 3000})\n\n"
                    "    def test_invalid_quantity(self):\n"
                    "        self.assertEqual(handle({'quantity': 0})['ok'], False)\n"
                ),
            },
            {
                "path": "README.md",
                "content": (
                    "# Quote calculation core\n\n"
                    "This is a bounded sandbox-only MVP. Call `handle(request)` with a dictionary "
                    "containing a positive integer `quantity`. It returns a dictionary and performs "
                    "no network, persistence, deployment, credential or payment side effects."
                ),
            },
        ]
    }


def _review_pass() -> dict:
    return {
        "verdict": "pass",
        "score": 8.4,
        "blocking_issues": [],
        "warnings": ["This sandbox result is not production readiness evidence."],
        "summary": "The bounded executable core matches the stated workflow contract.",
    }


def _python_spec(opportunity_id) -> BuildSpec:
    return BuildSpec(
        opportunity_id=opportunity_id,
        template=BuildTemplate.PYTHON_SERVICE,
        name="Quote calculation core",
        goal="Turn a bounded quote request into a deterministic structured calculation result.",
        acceptance_criteria=[
            "Return a successful structured quote result",
            "Reject invalid quantity input",
        ],
        max_files=3,
        max_total_bytes=100_000,
    )


def _bundle(app: str, test_app: str | None = None) -> BuildBundle:
    tests = test_app or (
        "import unittest\n"
        "from app import handle\n\n"
        "class HandleTests(unittest.TestCase):\n"
        "    def test_success(self):\n"
        "        self.assertTrue(handle({'value': 1})['ok'])\n\n"
        "    def test_failure(self):\n"
        "        self.assertFalse(handle({})['ok'])\n"
    )
    return BuildBundle(
        files=[
            BuildFile(path="app.py", content=app),
            BuildFile(path="test_app.py", content=tests),
            BuildFile(
                path="README.md",
                content="A bounded dependency-free sandbox-only executable MVP contract.",
            ),
        ]
    )


def _check_map(spec: BuildSpec, bundle: BuildBundle) -> dict[str, bool]:
    return {item.name: item.passed for item in BuildVerifier().verify(spec, bundle)}


def test_docker_command_is_fixed_hardened_and_never_uses_shell(tmp_path) -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="2 tests passed", stderr="")

    backend = DockerBuildExecutionBackend(
        docker_binary="/usr/bin/docker",
        image="python@sha256:" + "a" * 64,
        timeout_seconds=12,
        memory_mb=128,
        cpus=0.5,
        pids_limit=32,
        runner=runner,
    )
    result = backend.execute(tmp_path)

    assert result.passed is True
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert ["--pull", "never"] == command[3:5]
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--memory") + 1] == "128m"
    assert command[command.index("--memory-swap") + 1] == "128m"
    assert command[command.index("--cpus") + 1] == "0.5"
    assert command[command.index("--pids-limit") + 1] == "32"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges=true"
    assert command[command.index("--user") + 1] == "65534:65534"
    mount = command[command.index("--mount") + 1]
    assert f"src={tmp_path.resolve()}" in mount
    assert "dst=/workspace" in mount and "readonly" in mount
    image_index = command.index(backend.image)
    assert command[image_index + 1 :] == [
        "python",
        "-I",
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        "/workspace",
        "-p",
        "test_*.py",
    ]
    assert "sh" not in command
    assert "bash" not in command
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 12
    assert kwargs["check"] is False


def test_docker_timeout_and_nonzero_exit_are_failures(tmp_path) -> None:
    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr="hung")

    timeout_backend = DockerBuildExecutionBackend(runner=timeout_runner)
    timeout = timeout_backend.execute(tmp_path)
    assert timeout.passed is False
    assert timeout.return_code is None
    assert "timed out" in timeout.details

    def failed_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="FAILED")

    failed = DockerBuildExecutionBackend(runner=failed_runner).execute(tmp_path)
    assert failed.passed is False
    assert failed.return_code == 1
    assert "FAILED" in failed.details


def test_docker_configuration_rejects_injected_image_and_unbounded_resources() -> None:
    with pytest.raises(BuildExecutionConfigurationError):
        DockerBuildExecutionBackend(image="python:3.12 --network host")
    with pytest.raises(BuildExecutionConfigurationError):
        DockerBuildExecutionBackend(timeout_seconds=31)
    with pytest.raises(BuildExecutionConfigurationError):
        DockerBuildExecutionBackend(memory_mb=1024)
    with pytest.raises(BuildExecutionConfigurationError):
        DockerBuildExecutionBackend(cpus=2)
    with pytest.raises(BuildExecutionConfigurationError):
        DockerBuildExecutionBackend(pids_limit=512)


def test_default_planner_catalog_hides_executable_template() -> None:
    opportunity = Opportunity(
        title="Bounded planner",
        problem="A validated workflow needs the smallest possible test artifact before more work.",
        customer="service teams",
        proposed_value="test the workflow cheaply",
        stage=VentureStage.VALIDATED,
    )
    provider = PayloadProvider([_static_plan()])
    planner = BuilderPlanner(provider, CycleBudget(max_model_calls=1))

    spec = planner.plan(opportunity, [])

    assert spec.template == BuildTemplate.STATIC_WEB
    request = json.loads(provider.users[0])
    templates = {item["template"] for item in request["template_catalog"]}
    assert templates == {"static_web_v1", "concierge_ops_v1"}
    assert "python_service_v1" not in templates


def test_python_verifier_accepts_bounded_contract_and_rejects_escape_patterns() -> None:
    opportunity = Opportunity(
        title="Verifier target",
        problem="A sufficiently detailed validated workflow needs a bounded executable core.",
        customer="teams",
        proposed_value="return deterministic results",
    )
    spec = _python_spec(opportunity.id)
    good_app = (
        "def handle(request: dict) -> dict:\n"
        "    value = request.get('value') if isinstance(request, dict) else None\n"
        "    return {'ok': isinstance(value, int) and value > 0}\n"
    )
    checks = _check_map(spec, _bundle(good_app))
    for name in (
        "python_syntax",
        "python_import_allowlist",
        "python_no_dangerous_introspection",
        "python_no_top_level_side_effects",
        "python_entrypoint_contract",
        "python_tests_present",
        "python_tests_exercise_entrypoint",
        "python_tests_assert_behavior",
    ):
        assert checks[name] is True

    assert _check_map(spec, _bundle("import os\n" + good_app))["python_import_allowlist"] is False
    assert _check_map(
        spec,
        _bundle("def handle(request):\n    open('x')\n    return {'ok': True}\n"),
    )["python_no_dangerous_introspection"] is False
    assert _check_map(
        spec,
        _bundle("def handle(request):\n    return {'ok': getattr(request, '__class__', None)}\n"),
    )["python_no_dangerous_introspection"] is False
    assert _check_map(
        spec,
        _bundle("class Trigger:\n    value = print('side effect')\n\ndef handle(request):\n    return {'ok': True}\n"),
    )["python_no_top_level_side_effects"] is False
    assert _check_map(
        spec,
        _bundle("@print('decorator')\ndef helper():\n    pass\n\ndef handle(request):\n    return {'ok': True}\n"),
    )["python_no_top_level_side_effects"] is False


def test_python_verifier_rejects_fake_or_incomplete_tests() -> None:
    opportunity = Opportunity(
        title="Test contract",
        problem="A sufficiently detailed validated workflow needs meaningful deterministic tests.",
        customer="teams",
        proposed_value="prove bounded behavior",
    )
    spec = _python_spec(opportunity.id)
    app = "def handle(request):\n    return {'ok': True}\n"
    weak_tests = (
        "import unittest\n"
        "from app import handle\n\n"
        "class Tests(unittest.TestCase):\n"
        "    def test_only_one(self):\n"
        "        handle({})\n"
    )
    checks = _check_map(spec, _bundle(app, weak_tests))
    assert checks["python_tests_present"] is False
    assert checks["python_tests_exercise_entrypoint"] is True
    assert checks["python_tests_assert_behavior"] is False


def test_executable_builder_requires_sandbox_then_review_before_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HELIS_EXECUTABLE_SANDBOX", raising=False)
    engine = _engine(tmp_path)
    opportunity = _validated(engine)
    provider = PayloadProvider([_python_plan(), _good_bundle(), _review_pass()])
    backend = FakeExecutionBackend(
        [BuildExecutionResult(passed=True, return_code=0, details="2 sandbox tests passed")]
    )

    report = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        workspace_root=tmp_path / "workspaces",
        execution_backend=backend,
    ).tick(opportunity.id)

    assert report.preview is not None
    assert report.preview.entrypoint == "app.py"
    assert report.run is not None and report.run.status.value == "ready_preview"
    assert backend.calls == 1
    assert provider.calls == 3
    checks = engine.store.list_build_checks(report.run.id)
    sandbox = next(item for item in checks if item.name == "sandbox_execution")
    assert sandbox.passed is True
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.READY_PREVIEW


def test_failed_execution_preserves_workspace_for_bounded_repair(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HELIS_EXECUTABLE_SANDBOX", raising=False)
    engine = _engine(tmp_path)
    opportunity = _validated(engine)
    repaired_bundle = _good_bundle()
    provider = PayloadProvider(
        [
            _python_plan(),
            _good_bundle(),
            repaired_bundle,
            _review_pass(),
        ]
    )
    backend = FakeExecutionBackend(
        [
            BuildExecutionResult(False, 1, "assertion failed"),
            BuildExecutionResult(True, 0, "2 sandbox tests passed"),
        ]
    )
    machine = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=4),
        workspace_root=tmp_path / "workspaces",
        execution_backend=backend,
        max_attempts=2,
    )

    first = machine.tick(opportunity.id)
    assert first.run is not None and first.run.status.value == "failed"
    assert first.run.workspace is not None
    assert (Path(first.run.workspace) / "app.py").exists()
    assert first.review is None
    assert provider.calls == 2

    second = machine.tick(opportunity.id)
    assert second.preview is not None
    assert second.run is not None and second.run.attempt == 2
    assert second.repair_attempted is True
    assert backend.calls == 2
    assert provider.calls == 4
    repair_request = json.loads(provider.users[2])
    assert {item["path"] for item in repair_request["previous_files"]} == {
        "app.py",
        "test_app.py",
        "README.md",
    }
    failed_checks = {item["name"]: item for item in repair_request["failed_checks"]}
    assert failed_checks["sandbox_execution"]["passed"] is False


def test_persisted_executable_spec_blocks_without_backend_and_spends_no_model_calls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("HELIS_EXECUTABLE_SANDBOX", raising=False)
    engine = _engine(tmp_path)
    opportunity = _validated(engine)
    spec = _python_spec(opportunity.id)
    engine.record_build_spec(spec)
    provider = PayloadProvider([])

    report = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        workspace_root=tmp_path / "workspaces",
    ).tick(opportunity.id)

    assert report.spec is not None and report.spec.id == spec.id
    assert report.run is not None and report.run.status.value == "planned"
    assert report.blocked_reason == "executable build sandbox backend is not configured"
    assert provider.calls == 0
