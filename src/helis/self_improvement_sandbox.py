from __future__ import annotations

import hashlib
import json
from pathlib import Path

from helis.self_improvement_domain import CandidateFile, SelfImprovementCandidate


class UnsafeSelfImprovementWorkspace(RuntimeError):
    pass


def candidate_hash(files: list[CandidateFile]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda file: file.path):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.original_sha256.encode("ascii"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class SelfImprovementSandbox:
    def __init__(self, root: str | Path = ".helis/self-improvement") -> None:
        self.root = Path(root).resolve()

    def write(self, proposal_id, files: list[CandidateFile]) -> SelfImprovementCandidate:
        digest = candidate_hash(files)
        workspace = self.root / str(proposal_id) / digest
        if workspace.exists() and workspace.is_symlink():
            raise UnsafeSelfImprovementWorkspace("candidate workspace may not be a symlink")
        workspace.mkdir(parents=True, exist_ok=True)
        root = workspace.resolve()
        if self.root != root and self.root not in root.parents:
            raise UnsafeSelfImprovementWorkspace("candidate workspace escaped configured sandbox")

        candidate_root = root / "candidate"
        candidate_root.mkdir(parents=True, exist_ok=True)
        for item in files:
            destination = (candidate_root / item.path).resolve()
            if candidate_root != destination and candidate_root not in destination.parents:
                raise UnsafeSelfImprovementWorkspace(f"candidate path escaped sandbox: {item.path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.is_symlink():
                raise UnsafeSelfImprovementWorkspace(f"refusing to overwrite symlink: {item.path}")
            destination.write_text(item.content, encoding="utf-8")

        manifest = {
            "proposal_id": str(proposal_id),
            "candidate_hash": digest,
            "files": [
                {"path": item.path, "original_sha256": item.original_sha256}
                for item in sorted(files, key=lambda file: file.path)
            ],
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return SelfImprovementCandidate(
            proposal_id=proposal_id,
            files=files,
            candidate_hash=digest,
            workspace=str(root),
        )

    def verify(self, candidate: SelfImprovementCandidate) -> None:
        root = Path(candidate.workspace).resolve()
        if self.root != root and self.root not in root.parents:
            raise UnsafeSelfImprovementWorkspace("stored candidate workspace is outside sandbox")
        if candidate_hash(candidate.files) != candidate.candidate_hash:
            raise UnsafeSelfImprovementWorkspace("candidate payload hash mismatch")
        for item in candidate.files:
            source = (root / "candidate" / item.path).resolve()
            candidate_root = (root / "candidate").resolve()
            if candidate_root != source and candidate_root not in source.parents:
                raise UnsafeSelfImprovementWorkspace(f"stored candidate path escaped: {item.path}")
            if source.is_symlink():
                raise UnsafeSelfImprovementWorkspace(f"candidate file became a symlink: {item.path}")
            content = source.read_text(encoding="utf-8")
            if content != item.content:
                raise UnsafeSelfImprovementWorkspace(f"candidate bytes changed after materialization: {item.path}")
