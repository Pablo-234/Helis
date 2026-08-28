from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.model_provider import ModelProvider
from helis.self_improvement_domain import CandidateFile, SelfImprovementCandidate, SelfImprovementProposal
from helis.self_improvement_policy import SelfImprovementPolicy, UnsafeSelfImprovement
from helis.self_improvement_sandbox import SelfImprovementSandbox


class ReplacementFilePayload(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=40_000)


class ReplacementPayload(BaseModel):
    files: list[ReplacementFilePayload] = Field(min_length=1, max_length=2)


SYSTEM_PROMPT = """You are HELIS Self-Improvement Patch Generator.
Implement exactly the supplied low-risk proposal by returning FULL replacement content for exactly
the supplied target files. You cannot modify tests or any other file. Preserve all existing imports;
do not add dependencies, imports, network access, dynamic execution, subprocesses, credentials,
or authority. Keep the change narrow. Do not weaken validation, assertions, safety checks, evidence
binding, approval gates, budgets, or audit behavior. Return JSON only: {\"files\":[{\"path\":...,\"content\":...}]}.
"""


class SelfImprovementGenerator:
    def __init__(
        self,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        repo_root: str | Path = ".",
        sandbox_root: str | Path = ".helis/self-improvement",
        policy: SelfImprovementPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.repo_root = Path(repo_root).resolve()
        self.policy = policy or SelfImprovementPolicy()
        self.sandbox = SelfImprovementSandbox(sandbox_root)

    def materialize(self, proposal: SelfImprovementProposal) -> SelfImprovementCandidate:
        self.policy.validate_proposal(proposal, self.repo_root)
        baselines: dict[str, str] = {}
        source_payload: list[dict[str, str]] = []
        for path in proposal.target_files:
            source = (self.repo_root / path).resolve()
            if self.repo_root != source and self.repo_root not in source.parents:
                raise UnsafeSelfImprovement(f"source path escaped repository: {path}")
            if source.is_symlink() or not source.is_file():
                raise UnsafeSelfImprovement(f"source file is unavailable or symlinked: {path}")
            content = source.read_text(encoding="utf-8")
            baselines[path] = content
            source_payload.append(
                {
                    "path": path,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "content": content,
                }
            )

        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "source_files": source_payload,
                    "constraints": {
                        "replace_exactly_target_files": True,
                        "tests_immutable": True,
                        "no_new_imports": True,
                        "max_total_bytes": self.policy.MAX_TOTAL_BYTES,
                    },
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = ReplacementPayload.model_validate_json(result.content)
        returned_paths = [item.path for item in payload.files]
        if len(returned_paths) != len(set(returned_paths)):
            raise UnsafeSelfImprovement("generator returned duplicate file paths")
        if set(returned_paths) != set(proposal.target_files):
            raise UnsafeSelfImprovement("generator must replace exactly the approved target files")

        files: list[CandidateFile] = []
        for item in payload.files:
            baseline = baselines[item.path]
            candidate = CandidateFile(
                path=item.path,
                original_sha256=hashlib.sha256(baseline.encode("utf-8")).hexdigest(),
                content=item.content,
            )
            self.policy.validate_candidate_file(candidate, baseline_content=baseline)
            files.append(candidate)
        self.policy.validate_total_size(files)
        return self.sandbox.write(proposal.id, files)
