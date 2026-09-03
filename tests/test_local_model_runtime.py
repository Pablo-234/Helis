from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from helis.local_model_runtime import LocalModelInspector, LocalModelState
from helis.model_provider import ModelResponseError, OpenAICompatibleProvider, normalize_json_object


class Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _provider(**updates) -> OpenAICompatibleProvider:
    values = {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3.5:9b",
    }
    values.update(updates)
    return OpenAICompatibleProvider(**values)


def test_unreachable_endpoint_reports_install_or_serve_action() -> None:
    def unavailable(request, *, timeout):
        raise URLError("connection refused")

    without_cli = LocalModelInspector(
        _provider(),
        opener=unavailable,
        which=lambda name: None,
    ).inspect()
    with_cli = LocalModelInspector(
        _provider(),
        opener=unavailable,
        which=lambda name: "/usr/bin/ollama",
    ).inspect()

    assert without_cli.state == LocalModelState.ENDPOINT_DOWN
    assert without_cli.next_command == "install Ollama from https://ollama.com/download"
    assert with_cli.next_command == "ollama serve"


def test_healthy_endpoint_reports_exact_missing_model_and_pull_command() -> None:
    report = LocalModelInspector(
        _provider(),
        opener=lambda request, timeout: Response({"data": [{"id": "qwen3.5:4b"}]}),
        which=lambda name: "/usr/bin/ollama",
    ).inspect()

    assert report.state == LocalModelState.MODEL_MISSING
    assert report.endpoint_reachable is True
    assert report.model_available is False
    assert report.available_models == ["qwen3.5:4b"]
    assert report.next_command == "ollama pull qwen3.5:9b"


def test_exact_configured_model_is_ready() -> None:
    report = LocalModelInspector(
        _provider(),
        opener=lambda request, timeout: Response(
            {"data": [{"id": "qwen3.5:9b"}, {"id": "qwen3.5:4b"}]}
        ),
        which=lambda name: None,
    ).inspect()

    assert report.state == LocalModelState.READY
    assert report.endpoint_reachable is True
    assert report.model_available is True
    assert report.next_command == "helis-live model-smoke"


def test_reachable_non_openai_inventory_is_reported_as_incompatible() -> None:
    report = LocalModelInspector(
        _provider(),
        opener=lambda request, timeout: Response({"models": ["qwen3.5:9b"]}),
        which=lambda name: "/usr/bin/ollama",
    ).inspect()

    assert report.state == LocalModelState.INCOMPATIBLE
    assert report.endpoint_reachable is True
    assert "OpenAI compatibility" in report.next_command


def test_smoke_uses_one_capped_completion_and_validates_json() -> None:
    requests = []
    responses = [
        Response({"data": [{"id": "qwen3.5:9b"}]}),
        Response({"choices": [{"message": {"content": '{"status":"ok"}'}}]}),
    ]

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return responses.pop(0)

    report = LocalModelInspector(
        _provider(),
        opener=opener,
        which=lambda name: "/usr/bin/ollama",
    ).smoke(timeout_seconds=12)

    assert report.success is True
    assert report.response_status == "ok"
    assert len(requests) == 2
    completion, timeout = requests[1]
    payload = json.loads(completion.data.decode("utf-8"))
    assert completion.full_url == "http://localhost:11434/v1/chat/completions"
    assert timeout == 12
    assert payload["model"] == "qwen3.5:9b"
    assert payload["max_tokens"] == 96
    assert "Authorization" not in completion.headers


def test_reasoning_effort_is_forwarded_to_smoke_and_normal_completions() -> None:
    provider = _provider(reasoning_effort="NONE")

    smoke_payload = provider.completion_payload(
        system="system",
        user="user",
        temperature=0,
        max_tokens=96,
    )
    normal_payload = provider.completion_payload(system="system", user="user")

    assert provider.reasoning_effort == "none"
    assert smoke_payload["reasoning_effort"] == "none"
    assert normal_payload["reasoning_effort"] == "none"


def test_reasoning_effort_is_optional_and_rejects_unknown_values() -> None:
    payload = _provider().completion_payload(system="system", user="user")

    assert "reasoning_effort" not in payload
    with pytest.raises(ValueError, match="HELIS_LLM_REASONING_EFFORT"):
        _provider(reasoning_effort="turbo")


def test_default_local_qwen35_env_disables_reasoning_without_affecting_other_models(
    monkeypatch,
) -> None:
    monkeypatch.delenv("HELIS_LLM_REASONING_EFFORT", raising=False)
    monkeypatch.setenv("HELIS_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("HELIS_LLM_MODEL", "qwen3.5:9b")

    assert OpenAICompatibleProvider.from_env().reasoning_effort == "none"

    monkeypatch.setenv("HELIS_LLM_MODEL", "another-model")
    assert OpenAICompatibleProvider.from_env().reasoning_effort is None


def test_explicit_empty_reasoning_effort_overrides_local_qwen35_default(monkeypatch) -> None:
    monkeypatch.setenv("HELIS_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("HELIS_LLM_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("HELIS_LLM_REASONING_EFFORT", "")

    assert OpenAICompatibleProvider.from_env().reasoning_effort is None


def test_model_timeout_has_local_default_and_explicit_env_override(monkeypatch) -> None:
    monkeypatch.delenv("HELIS_LLM_TIMEOUT_SECONDS", raising=False)

    assert OpenAICompatibleProvider.from_env().timeout_seconds == 300

    monkeypatch.setenv("HELIS_LLM_TIMEOUT_SECONDS", "420")
    assert OpenAICompatibleProvider.from_env().timeout_seconds == 420


def test_structured_response_accepts_json_fence_and_rejects_empty_content() -> None:
    assert normalize_json_object('```json\n{"status":"ok"}\n```') == '{"status":"ok"}'

    with pytest.raises(ModelResponseError, match="HELIS_LLM_REASONING_EFFORT=none"):
        normalize_json_object("  ")


def test_smoke_reports_malformed_model_contract() -> None:
    responses = [
        Response({"data": [{"id": "qwen3.5:9b"}]}),
        Response({"choices": [{"message": {"content": "not-json"}}]}),
    ]

    report = LocalModelInspector(
        _provider(),
        opener=lambda request, timeout: responses.pop(0),
        which=lambda name: "/usr/bin/ollama",
    ).smoke()

    assert report.success is False
    assert report.error is not None and "ModelResponseError" in report.error
    assert "invalid JSON" in report.error


def test_smoke_explains_empty_final_content_from_thinking_model() -> None:
    responses = [
        Response({"data": [{"id": "qwen3.5:9b"}]}),
        Response({"choices": [{"message": {"content": ""}}]}),
    ]

    report = LocalModelInspector(
        _provider(),
        opener=lambda request, timeout: responses.pop(0),
        which=lambda name: "/usr/bin/ollama",
    ).smoke()

    assert report.success is False
    assert report.error is not None
    assert "HELIS_LLM_REASONING_EFFORT=none" in report.error


def test_remote_or_credentialed_config_fails_before_network() -> None:
    calls = 0

    def forbidden(request, *, timeout):
        nonlocal calls
        calls += 1
        raise AssertionError("network must not be reached")

    for provider in (
        _provider(base_url="https://remote.example/v1"),
        _provider(api_key="secret"),
        _provider(input_cost_per_million_tokens=1),
    ):
        report = LocalModelInspector(provider, opener=forbidden).inspect()
        assert report.state == LocalModelState.INVALID_CONFIG

    assert calls == 0
