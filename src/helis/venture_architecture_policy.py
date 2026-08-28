from __future__ import annotations

from collections import defaultdict, deque

from helis.policy import ActionKind
from helis.venture_architecture_domain import CapabilityImplementation, CapabilityNode


class UnsafeVentureArchitecture(ValueError):
    pass


class VentureArchitecturePolicy:
    max_capabilities = 12
    max_ai_agents = 6

    def validate(self, capabilities: list[CapabilityNode]) -> None:
        if not capabilities:
            raise UnsafeVentureArchitecture("venture architecture must contain capabilities")
        if len(capabilities) > self.max_capabilities:
            raise UnsafeVentureArchitecture("venture architecture exceeds capability cap")

        by_key = {item.key: item for item in capabilities}
        if len(by_key) != len(capabilities):
            raise UnsafeVentureArchitecture("capability keys must be unique")

        ai_agents = sum(
            item.implementation == CapabilityImplementation.AI_AGENT for item in capabilities
        )
        if ai_agents > self.max_ai_agents:
            raise UnsafeVentureArchitecture("venture architecture exceeds AI-agent cap")

        for item in capabilities:
            if item.key in item.depends_on:
                raise UnsafeVentureArchitecture(f"capability {item.key} depends on itself")
            missing = [dependency for dependency in item.depends_on if dependency not in by_key]
            if missing:
                raise UnsafeVentureArchitecture(
                    f"capability {item.key} has missing dependencies: {missing}"
                )
            if ActionKind.SELF_MODIFY in item.required_actions:
                raise UnsafeVentureArchitecture(
                    "child venture capabilities may not require HELIS self-modification"
                )
            if not item.venture_isolation_required:
                raise UnsafeVentureArchitecture(
                    "every venture capability must remain inside venture isolation boundaries"
                )

        self._reject_cycles(capabilities)

    @staticmethod
    def _reject_cycles(capabilities: list[CapabilityNode]) -> None:
        indegree = {item.key: 0 for item in capabilities}
        children: dict[str, list[str]] = defaultdict(list)
        for item in capabilities:
            for dependency in item.depends_on:
                children[dependency].append(item.key)
                indegree[item.key] += 1

        queue = deque(key for key, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            key = queue.popleft()
            visited += 1
            for child in children[key]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        if visited != len(capabilities):
            raise UnsafeVentureArchitecture("venture capability graph must be acyclic")
