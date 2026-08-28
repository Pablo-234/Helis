from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import ClassVar

from helis.self_improvement_domain import CandidateFile, SelfImprovementProposal


class UnsafeSelfImprovement(RuntimeError):
    pass


class SelfImprovementPolicy:
    """Very small Phase-5 allowlist. Candidate code never gets to edit its own guardrails."""

    MAX_FILES: ClassVar[int] = 2
    MAX_TOTAL_BYTES: ClassVar[int] = 50_000
    MAX_FILE_BYTES: ClassVar[int] = 35_000
    ALLOWED_SOURCE_FILES: ClassVar[frozenset[str]] = frozenset(
        {
            "src/helis/dedup.py",
            "src/helis/scoring.py",
            "src/helis/decision.py",
            "src/helis/gtm_metrics.py",
            "src/helis/gtm_decision.py",
        }
    )
    FORBIDDEN_TEXT_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    )
    FORBIDDEN_CALLS: ClassVar[frozenset[str]] = frozenset(
        {"eval", "exec", "compile", "__import__"}
    )

    def catalog(self, repo_root: str | Path) -> list[str]:
        root = Path(repo_root).resolve()
        return sorted(
            path
            for path in self.ALLOWED_SOURCE_FILES
            if (root / path).is_file() and not (root / path).is_symlink()
        )

    def validate_proposal(self, proposal: SelfImprovementProposal, repo_root: str | Path) -> None:
        catalog = set(self.catalog(repo_root))
        if not proposal.target_files:
            raise UnsafeSelfImprovement("proposal must target at least one file")
        if len(proposal.target_files) > self.MAX_FILES:
            raise UnsafeSelfImprovement("proposal targets too many files")
        if len(set(proposal.target_files)) != len(proposal.target_files):
            raise UnsafeSelfImprovement("proposal contains duplicate target files")
        if not set(proposal.target_files) <= catalog:
            raise UnsafeSelfImprovement("proposal targets a file outside the Phase-5 allowlist")

    def validate_candidate_file(
        self,
        candidate: CandidateFile,
        *,
        baseline_content: str,
    ) -> None:
        if candidate.path not in self.ALLOWED_SOURCE_FILES:
            raise UnsafeSelfImprovement(f"candidate path is not allowlisted: {candidate.path}")
        encoded = candidate.content.encode("utf-8")
        if len(encoded) > self.MAX_FILE_BYTES:
            raise UnsafeSelfImprovement(f"candidate file exceeds byte cap: {candidate.path}")
        expected_hash = hashlib.sha256(baseline_content.encode("utf-8")).hexdigest()
        if candidate.original_sha256 != expected_hash:
            raise UnsafeSelfImprovement(f"baseline hash mismatch: {candidate.path}")
        if candidate.content == baseline_content:
            raise UnsafeSelfImprovement(f"candidate does not change file: {candidate.path}")
        if any(pattern.search(candidate.content) for pattern in self.FORBIDDEN_TEXT_PATTERNS):
            raise UnsafeSelfImprovement(f"candidate contains credential-like material: {candidate.path}")

        try:
            baseline_tree = ast.parse(baseline_content, filename=candidate.path)
            candidate_tree = ast.parse(candidate.content, filename=candidate.path)
        except SyntaxError as exc:
            raise UnsafeSelfImprovement(f"candidate Python syntax is invalid: {candidate.path}") from exc

        baseline_imports = self._imports(baseline_tree)
        candidate_imports = self._imports(candidate_tree)
        if not candidate_imports <= baseline_imports:
            added = sorted(candidate_imports - baseline_imports)
            raise UnsafeSelfImprovement(
                f"candidate may not add imports in phase 5: {candidate.path} added={added}"
            )

        forbidden = sorted(
            {
                node.func.id
                for node in ast.walk(candidate_tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in self.FORBIDDEN_CALLS
            }
        )
        if forbidden:
            raise UnsafeSelfImprovement(
                f"candidate contains forbidden dynamic execution calls: {forbidden}"
            )

    def validate_total_size(self, files: list[CandidateFile]) -> None:
        total = sum(len(item.content.encode("utf-8")) for item in files)
        if len(files) > self.MAX_FILES:
            raise UnsafeSelfImprovement("candidate contains too many files")
        if total > self.MAX_TOTAL_BYTES:
            raise UnsafeSelfImprovement("candidate exceeds total byte cap")

    @staticmethod
    def _imports(tree: ast.AST) -> set[str]:
        output: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                output.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                output.add(module)
        return output
