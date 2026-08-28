from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from helis.agent_spec_domain import AgentSpecBundle, ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.child_agent_domain import ChildAgentArtifact
from helis.child_agent_store import ChildAgentArtifactStore
from helis.domain import AuditEvent, VentureStage
from helis.engine import HelisEngine
from helis.venture_architecture_store import VentureArchitectureStore


class ChildAgentArtifactTampered(RuntimeError):
    pass


@dataclass(slots=True)
class ChildAgentFactoryReport:
    opportunity_id: UUID
    artifacts: list[ChildAgentArtifact]
    created_count: int = 0
    blocked_reason: str | None = None

    @property
    def did_work(self) -> bool:
        return self.created_count > 0


def child_agent_spec_hash(spec: ChildAgentSpec) -> str:
    payload = spec.model_dump(mode="json", exclude={"id"})
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_bytes(bundle: AgentSpecBundle, spec: ChildAgentSpec) -> bytes:
    payload = {
        "schema_version": 1,
        "bundle_id": str(bundle.id),
        "bundle_hash": bundle.bundle_hash,
        "architecture_id": str(bundle.architecture_id),
        "architecture_input_hash": bundle.architecture_input_hash,
        "opportunity_id": str(bundle.opportunity_id),
        "spec_hash": child_agent_spec_hash(spec),
        "spec": spec.model_dump(mode="json"),
        "runtime_contract": {
            "tools_enabled": False,
            "side_effects_enabled": False,
            "network_tools_enabled": False,
            "mode": "reasoning_only_v1",
        },
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class ChildAgentFactory:
    def __init__(
        self,
        engine: HelisEngine,
        *,
        workspace_root: str | Path = ".helis/ventures",
    ) -> None:
        self.engine = engine
        self.workspace_root = Path(workspace_root)
        self.specs = AgentSpecStore(engine.store)
        self.architectures = VentureArchitectureStore(engine.store)
        self.artifacts = ChildAgentArtifactStore(engine.store)

    def materialize_if_needed(self, opportunity_id: UUID) -> ChildAgentFactoryReport:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return ChildAgentFactoryReport(opportunity_id, [], blocked_reason="opportunity_not_found")
        if opportunity.stage != VentureStage.VALIDATED:
            return ChildAgentFactoryReport(opportunity_id, [], blocked_reason="venture_not_validated")

        architecture = self.architectures.latest(opportunity_id)
        bundle = self.specs.latest(opportunity_id)
        if architecture is None:
            return ChildAgentFactoryReport(opportunity_id, [], blocked_reason="architecture_missing")
        if bundle is None:
            return ChildAgentFactoryReport(opportunity_id, [], blocked_reason="agent_specs_missing")

        results = self.engine.store.list_validation_results(opportunity_id)
        current_hash = architecture_input_hash(opportunity, results)
        if architecture.input_hash != current_hash:
            return ChildAgentFactoryReport(opportunity_id, [], blocked_reason="architecture_stale")
        if (
            bundle.architecture_id != architecture.id
            or bundle.architecture_input_hash != architecture.input_hash
        ):
            return ChildAgentFactoryReport(opportunity_id, [], blocked_reason="agent_specs_stale")

        materialized: list[ChildAgentArtifact] = []
        created_count = 0
        for spec in bundle.agent_specs:
            existing = self.artifacts.get_for_spec(spec.id)
            if existing is not None:
                self.verify(existing)
                materialized.append(existing)
                continue
            artifact = self._materialize(bundle, spec)
            materialized.append(artifact)
            created_count += 1

        return ChildAgentFactoryReport(
            opportunity_id=opportunity_id,
            artifacts=materialized,
            created_count=created_count,
        )

    def verify(self, artifact: ChildAgentArtifact) -> Path:
        target = self._target_from_relative(artifact.manifest_path)
        if not target.is_file():
            raise ChildAgentArtifactTampered(f"child-agent manifest missing: {artifact.id}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != artifact.artifact_hash:
            raise ChildAgentArtifactTampered(
                f"child-agent manifest hash mismatch: expected {artifact.artifact_hash}, got {actual}"
            )
        return target

    def _materialize(self, bundle: AgentSpecBundle, spec: ChildAgentSpec) -> ChildAgentArtifact:
        body = _manifest_bytes(bundle, spec)
        artifact_hash = hashlib.sha256(body).hexdigest()
        relative = Path(str(bundle.opportunity_id)) / "agents" / spec.capability_key / artifact_hash / "agent.json"
        target = self._target_from_relative(relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != artifact_hash:
                raise ChildAgentArtifactTampered(f"pre-existing child-agent path is not exact: {target}")
        else:
            temporary = target.with_name("agent.json.tmp")
            temporary.write_bytes(body)
            temporary.replace(target)

        artifact = ChildAgentArtifact(
            bundle_id=bundle.id,
            spec_id=spec.id,
            architecture_id=bundle.architecture_id,
            opportunity_id=bundle.opportunity_id,
            capability_key=spec.capability_key,
            bundle_hash=bundle.bundle_hash,
            spec_hash=child_agent_spec_hash(spec),
            artifact_hash=artifact_hash,
            manifest_path=relative.as_posix(),
        )
        self.artifacts.save(artifact)
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.child_agent_materialized",
                entity_id=artifact.id,
                data={
                    "opportunity_id": str(artifact.opportunity_id),
                    "capability_key": artifact.capability_key,
                    "bundle_hash": artifact.bundle_hash,
                    "spec_hash": artifact.spec_hash,
                    "artifact_hash": artifact.artifact_hash,
                    "runtime_mode": "reasoning_only_v1",
                },
            )
        )
        return artifact

    def _target_from_relative(self, relative: str) -> Path:
        root = self.workspace_root.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ChildAgentArtifactTampered("child-agent path escapes venture workspace") from exc
        return target
