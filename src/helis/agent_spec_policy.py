from __future__ import annotations

from helis.agent_spec_domain import AgentMemoryScope, ChildAgentSpec
from helis.policy import ActionKind
from helis.venture_architecture_domain import CapabilityImplementation, VentureArchitecture


class UnsafeAgentSpec(ValueError):
    pass


class AgentSpecPolicy:
    def validate(self, architecture: VentureArchitecture, specs: list[ChildAgentSpec]) -> None:
        targets = {
            item.key: item
            for item in architecture.capabilities
            if item.implementation == CapabilityImplementation.AI_AGENT
        }
        by_key = {item.capability_key: item for item in specs}
        if len(by_key) != len(specs):
            raise UnsafeAgentSpec("child-agent capability keys must be unique")
        if set(by_key) != set(targets):
            raise UnsafeAgentSpec(
                "child-agent specs must match AI-agent capabilities exactly; no extras or omissions"
            )

        for key, spec in by_key.items():
            capability = targets[key]
            if spec.architecture_id != architecture.id:
                raise UnsafeAgentSpec("child-agent spec architecture id mismatch")
            if spec.opportunity_id != architecture.opportunity_id:
                raise UnsafeAgentSpec("child-agent spec opportunity id mismatch")
            if spec.goal != capability.goal:
                raise UnsafeAgentSpec(f"child-agent {key} may not change capability goal")
            if spec.inputs != capability.inputs or spec.outputs != capability.outputs:
                raise UnsafeAgentSpec(f"child-agent {key} may not change capability IO contract")
            if spec.success_metric != capability.success_metric:
                raise UnsafeAgentSpec(f"child-agent {key} may not change success metric")
            if spec.handles_customer_data != capability.handles_customer_data:
                raise UnsafeAgentSpec(f"child-agent {key} customer-data flag mismatch")
            if not spec.venture_isolation_required:
                raise UnsafeAgentSpec("child-agent venture isolation is mandatory")
            if capability.handles_customer_data and spec.memory_scope == AgentMemoryScope.VENTURE:
                raise UnsafeAgentSpec(
                    "customer-data agent may not use venture-wide conversational memory"
                )

            allowed_actions = set(capability.required_actions)
            tool_keys: set[str] = set()
            for tool in spec.allowed_tools:
                if tool.key in tool_keys:
                    raise UnsafeAgentSpec(f"child-agent {key} tool keys must be unique")
                tool_keys.add(tool.key)
                if tool.action == ActionKind.SELF_MODIFY:
                    raise UnsafeAgentSpec("child agents may never request self-modification")
                if tool.action not in allowed_actions:
                    raise UnsafeAgentSpec(
                        f"child-agent {key} tool action {tool.action.value} exceeds capability authority"
                    )
                if (
                    tool.credential_alias is not None
                    and ActionKind.CREDENTIAL_ACCESS not in allowed_actions
                ):
                    raise UnsafeAgentSpec(
                        f"child-agent {key} credential alias exceeds capability authority"
                    )

            if spec.max_tool_calls_per_run == 0 and spec.allowed_tools:
                raise UnsafeAgentSpec(
                    f"child-agent {key} with tools must allow at least one tool call"
                )
