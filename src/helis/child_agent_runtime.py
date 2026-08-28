from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

from helis.agent_spec_domain import ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.budget import BudgetExceeded, CycleBudget
from helis.child_agent_domain import ChildAgentArtifact, ChildAgentRunResult, ChildAgentRunStatus
from helis.child_agent_factory import ChildAgentArtifactTampered, ChildAgentFactory
from helis.child_agent_store import ChildAgentArtifactStore
from helis.domain import AuditEvent
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider


class AgentTurnPayload(BaseModel):
    state: str = Field(pattern=r"^(continue|completed|blocked)$")
    output: str = Field(default="", max_length=12_000)
    next_step: str | None = Field(default=None, max_length=2_000)
    needs_tool: str | None = Field(default=None, max_length=80)


SYSTEM_PROMPT = """You are one isolated HELIS child agent running under an immutable venture-owned spec.
Perform only the supplied task and stay inside the exact capability contract.

Critical runtime rules:
- This is reasoning_only_v1. You have NO executable tools, shell, browser, network tools, credentials,
  customer-contact transport, publication ability, spending ability or code execution authority.
- allowed_tools in the spec are declarations for a future runtime only. They are NOT available now.
- Never claim that you called, searched, sent, published, paid, booked, modified, fetched or executed
  anything unless the task input itself already contains the corresponding result.
- If completion genuinely requires one of the declared tools, set state=blocked and needs_tool to its key.
- Never request or invent an undeclared tool.
- Respect the supplied constraints and stop conditions.
- Do not broaden the task, change the business, modify HELIS or access another venture.
- Keep output directly useful and concise.

Return JSON only:
{
  "state": "continue|completed|blocked",
  "output": "current useful result",
  "next_step": "what to reason about next, or null",
  "needs_tool": "declared_tool_key_or_null"
}
"""


class ChildAgentRuntime:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        workspace_root: str | Path = ".helis/ventures",
        hard_turn_cap: int = 6,
    ) -> None:
        if hard_turn_cap < 1 or hard_turn_cap > 12:
            raise ValueError("hard_turn_cap must be between 1 and 12")
        self.engine = engine
        self.provider = provider
        self.budget = budget
        self.workspace_root = Path(workspace_root)
        self.hard_turn_cap = hard_turn_cap
        self.factory = ChildAgentFactory(engine, workspace_root=self.workspace_root)
        self.artifacts = ChildAgentArtifactStore(engine.store)
        self.specs = AgentSpecStore(engine.store)

    def run(self, artifact_id: UUID, task: str) -> ChildAgentRunResult:
        task = task.strip()
        if not task:
            raise ValueError("child-agent task cannot be empty")
        if len(task) > 12_000:
            raise ValueError("child-agent task exceeds 12000 characters")

        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            raise ValueError(f"child-agent artifact not found: {artifact_id}")
        manifest_path = self.factory.verify(artifact)
        spec = self._load_current_spec(artifact, manifest_path)
        task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
        max_turns = min(spec.max_model_turns, self.hard_turn_cap, self.budget.max_model_calls)
        transcript: list[dict[str, str | None]] = []

        final_status = ChildAgentRunStatus.BLOCKED
        final_output = ""
        stop_reason = "turn_limit_reached"
        turns_used = 0

        for turn in range(1, max_turns + 1):
            try:
                self.budget.ensure_call_available()
            except BudgetExceeded:
                stop_reason = "model_budget_exhausted"
                break

            result = self.provider.complete(
                system=SYSTEM_PROMPT,
                user="CHILD_AGENT_CONTRACT:\n"
                + json.dumps(
                    {
                        "artifact_id": str(artifact.id),
                        "opportunity_id": str(artifact.opportunity_id),
                        "capability_key": spec.capability_key,
                        "goal": spec.goal,
                        "inputs": spec.inputs,
                        "outputs": spec.outputs,
                        "constraints": spec.constraints,
                        "stop_conditions": spec.stop_conditions,
                        "success_metric": spec.success_metric,
                        "memory_scope": spec.memory_scope.value,
                        "allowed_tools_unavailable_in_v1": [
                            item.model_dump(mode="json") for item in spec.allowed_tools
                        ],
                        "task": task,
                        "prior_turns": transcript,
                        "turn": turn,
                        "max_turns": max_turns,
                    },
                    ensure_ascii=False,
                ),
            )
            turns_used = turn
            try:
                self.budget.record(result)
            except BudgetExceeded:
                final_status = ChildAgentRunStatus.BLOCKED
                stop_reason = "model_budget_exceeded_after_call"
                break

            payload = AgentTurnPayload.model_validate_json(result.content)
            if payload.needs_tool is not None:
                declared = {item.key for item in spec.allowed_tools}
                if payload.needs_tool not in declared:
                    final_status = ChildAgentRunStatus.FAILED
                    final_output = payload.output
                    stop_reason = "undeclared_tool_requested"
                    break
                final_status = ChildAgentRunStatus.BLOCKED
                final_output = payload.output
                stop_reason = f"tool_required_unavailable:{payload.needs_tool}"
                break

            if payload.state == "completed":
                final_status = ChildAgentRunStatus.COMPLETED
                final_output = payload.output
                stop_reason = "completed"
                break
            if payload.state == "blocked":
                final_status = ChildAgentRunStatus.BLOCKED
                final_output = payload.output
                stop_reason = "model_reported_blocked"
                break

            transcript.append(
                {
                    "output": payload.output,
                    "next_step": payload.next_step,
                }
            )
            final_output = payload.output

        run = ChildAgentRunResult(
            artifact_id=artifact.id,
            opportunity_id=artifact.opportunity_id,
            capability_key=artifact.capability_key,
            task_hash=task_hash,
            status=final_status,
            output=final_output,
            stop_reason=stop_reason,
            turns_used=turns_used,
            run_path="pending",
        )
        relative = (
            Path(str(artifact.opportunity_id))
            / "agent-runs"
            / artifact.capability_key
            / f"{run.id}.json"
        )
        run = run.model_copy(update={"run_path": relative.as_posix()})
        target = self._safe_target(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(run.model_dump_json(indent=2), encoding="utf-8")

        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.child_agent_run",
                entity_id=run.id,
                data={
                    "artifact_id": str(artifact.id),
                    "opportunity_id": str(artifact.opportunity_id),
                    "capability_key": artifact.capability_key,
                    "task_hash": task_hash,
                    "status": run.status.value,
                    "stop_reason": run.stop_reason,
                    "turns_used": run.turns_used,
                    "model_calls": self.budget.model_calls,
                },
            )
        )
        return run

    def _load_current_spec(self, artifact: ChildAgentArtifact, manifest_path: Path) -> ChildAgentSpec:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChildAgentArtifactTampered("child-agent manifest is unreadable") from exc
        spec = ChildAgentSpec.model_validate(manifest.get("spec"))
        if spec.id != artifact.spec_id:
            raise ChildAgentArtifactTampered("manifest spec id does not match artifact")

        latest = self.specs.latest(artifact.opportunity_id)
        if latest is None:
            raise ChildAgentArtifactTampered("current agent spec bundle is missing")
        if latest.id != artifact.bundle_id or latest.bundle_hash != artifact.bundle_hash:
            raise ChildAgentArtifactTampered("child-agent artifact is stale against current spec bundle")
        current = next((item for item in latest.agent_specs if item.id == artifact.spec_id), None)
        if current is None or current != spec:
            raise ChildAgentArtifactTampered("manifest spec does not match current persisted spec")
        return spec

    def _safe_target(self, relative: Path) -> Path:
        root = self.workspace_root.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ChildAgentArtifactTampered("child-agent run path escapes venture workspace") from exc
        return target
