from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from helis.build_domain import BuildBundle, BuildSpec


class WorkspaceViolation(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    path: Path
    file_count: int
    total_bytes: int
    digest: str


class WorkspaceManager:
    def __init__(self, root: str | Path = "helis-workspaces") -> None:
        self.root = Path(root).resolve()

    def create(self, run_id: UUID, spec: BuildSpec, bundle: BuildBundle) -> WorkspaceSnapshot:
        self.root.mkdir(parents=True, exist_ok=True)
        venture_dir = self.root / str(spec.opportunity_id)
        venture_dir.mkdir(parents=True, exist_ok=True)
        target = (venture_dir / str(run_id)).resolve()
        if self.root not in target.parents:
            raise WorkspaceViolation("workspace escaped configured root")
        if target.exists():
            raise WorkspaceViolation("build workspace already exists")
        temp = target.with_name(target.name + ".tmp")
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True)

        hashes: dict[str, str] = {}
        total = 0
        try:
            for file in bundle.files:
                relative = PurePosixPath(file.path)
                destination = temp.joinpath(*relative.parts).resolve()
                if temp not in destination.parents:
                    raise WorkspaceViolation("generated file escaped build workspace")
                destination.parent.mkdir(parents=True, exist_ok=True)
                content = file.content.encode("utf-8")
                destination.write_bytes(content)
                hashes[file.path] = hashlib.sha256(content).hexdigest()
                total += len(content)

            digest_input = "\n".join(f"{path}:{hashes[path]}" for path in sorted(hashes))
            digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
            manifest = {
                "run_id": str(run_id),
                "spec": spec.model_dump(mode="json"),
                "files": hashes,
                "bundle_digest": digest,
            }
            (temp / "helis-build-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(target)
        except Exception:
            if temp.exists():
                shutil.rmtree(temp)
            raise

        return WorkspaceSnapshot(
            path=target,
            file_count=len(bundle.files),
            total_bytes=total,
            digest=digest,
        )
