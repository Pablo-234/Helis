from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path, PurePosixPath

from helis.build_templates import get_template
from helis.domain import (
    BuildBundle,
    BuildCheck,
    BuildFile,
    BuildRun,
    BuildSpec,
    BuildTemplate,
)

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{8,}"),
]

_PYTHON_ALLOWED_IMPORTS = {
    "app",
    "base64",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "hmac",
    "itertools",
    "json",
    "math",
    "operator",
    "re",
    "statistics",
    "string",
    "typing",
    "unittest",
    "uuid",
}
_PYTHON_FORBIDDEN_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "input",
    "open",
}
_PYTHON_FORBIDDEN_NAMES = {"__builtins__", "__loader__", "__spec__"}
_PYTHON_FORBIDDEN_ATTRIBUTES = {
    "__base__",
    "__bases__",
    "__class__",
    "__closure__",
    "__code__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__subclasses__",
}


class UnsafeBuildArtifact(RuntimeError):
    pass


def _safe_path(path: str) -> bool:
    if "\\" in path:
        return False
    parsed = PurePosixPath(path)
    return bool(path) and not parsed.is_absolute() and ".." not in parsed.parts and "." not in parsed.parts


def _check(name: str, passed: bool, details: str) -> BuildCheck:
    return BuildCheck(name=name, passed=passed, details=details)


def _python_trees(bundle: BuildBundle) -> tuple[dict[str, ast.Module], list[str]]:
    trees: dict[str, ast.Module] = {}
    errors: list[str] = []
    for item in bundle.files:
        if not item.path.endswith(".py"):
            continue
        try:
            trees[item.path] = ast.parse(item.content, filename=item.path)
        except SyntaxError as exc:
            errors.append(f"{item.path}:{exc.lineno or '?'}:{exc.msg}")
    return trees, errors


def _python_import_violations(trees: dict[str, ast.Module]) -> list[str]:
    violations: list[str] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in _PYTHON_ALLOWED_IMPORTS:
                        violations.append(f"{path}:import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if node.level or root not in _PYTHON_ALLOWED_IMPORTS:
                    violations.append(f"{path}:from {node.module or '.'}")
    return violations


def _python_dangerous_nodes(trees: dict[str, ast.Module]) -> list[str]:
    violations: list[str] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _PYTHON_FORBIDDEN_CALLS:
                    violations.append(f"{path}:call {node.func.id}")
            elif isinstance(node, ast.Name) and node.id in _PYTHON_FORBIDDEN_NAMES:
                violations.append(f"{path}:name {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in _PYTHON_FORBIDDEN_ATTRIBUTES:
                violations.append(f"{path}:attribute {node.attr}")
    return violations


def _literal_assignment(node: ast.Assign | ast.AnnAssign) -> bool:
    value = node.value
    if value is None:
        return True
    try:
        ast.literal_eval(value)
    except (ValueError, TypeError):
        return False
    return True


def _python_top_level_safe(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and _literal_assignment(node):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        return False
    return True


def _python_entrypoint_ok(tree: ast.Module | None) -> bool:
    if tree is None:
        return False
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "handle":
            continue
        args = node.args
        return (
            len(args.posonlyargs) + len(args.args) == 1
            and not args.vararg
            and not args.kwarg
            and not args.kwonlyargs
        )
    return False


def _python_test_contract(tree: ast.Module | None) -> tuple[bool, bool]:
    if tree is None:
        return False, False
    has_test = False
    calls_handle = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            has_test = True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "handle":
                calls_handle = True
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "handle":
                calls_handle = True
    return has_test, calls_handle


class BuildVerifier:
    def verify(self, spec: BuildSpec, bundle: BuildBundle) -> list[BuildCheck]:
        definition = get_template(spec.template)
        paths = [item.path for item in bundle.files]
        total_bytes = sum(len(item.content.encode("utf-8")) for item in bundle.files)
        text = "\n".join(item.content for item in bundle.files)

        checks = [
            _check(
                "file_count",
                0 < len(bundle.files) <= spec.max_files,
                f"files={len(bundle.files)} cap={spec.max_files}",
            ),
            _check(
                "total_bytes",
                total_bytes <= spec.max_total_bytes,
                f"bytes={total_bytes} cap={spec.max_total_bytes}",
            ),
            _check(
                "safe_paths",
                all(_safe_path(path) for path in paths),
                "all paths must stay inside the venture workspace",
            ),
            _check(
                "allowed_paths",
                set(paths) <= definition.allowed_paths,
                f"allowed={sorted(definition.allowed_paths)}",
            ),
            _check(
                "required_files",
                definition.required_paths <= set(paths),
                f"required={sorted(definition.required_paths)}",
            ),
            _check(
                "unique_paths",
                len(paths) == len(set(paths)),
                "duplicate file paths are forbidden",
            ),
            _check(
                "secret_scan",
                not any(pattern.search(text) for pattern in _SECRET_PATTERNS),
                "generated artifacts must not contain credential-like material",
            ),
        ]

        if spec.template == BuildTemplate.STATIC_WEB:
            index = next((item.content.lower() for item in bundle.files if item.path == "index.html"), "")
            active_external = bool(
                re.search(r"<(script|iframe)\b", index)
                or re.search(r"\b(action|src)\s*=\s*['\"]https?://", index)
            )
            checks.extend(
                [
                    _check(
                        "html_document",
                        "<html" in index and "<body" in index,
                        "index.html must contain an HTML document",
                    ),
                    _check(
                        "no_active_external_content",
                        not active_external,
                        "scripts, iframes and remote active form/assets are forbidden",
                    ),
                ]
            )

        if spec.template == BuildTemplate.CONCIERGE_OPS:
            by_path = {item.path: item.content for item in bundle.files}
            substantive = all(
                len(by_path.get(path, "").strip()) >= 80 for path in definition.required_paths
            )
            checks.append(
                _check(
                    "substantive_ops_docs",
                    substantive,
                    "required operating documents must contain actionable content",
                )
            )

        if spec.template == BuildTemplate.PYTHON_SERVICE:
            trees, syntax_errors = _python_trees(bundle)
            import_violations = _python_import_violations(trees)
            dangerous = _python_dangerous_nodes(trees)
            tests_present, tests_exercise = _python_test_contract(trees.get("test_app.py"))
            checks.extend(
                [
                    _check(
                        "python_syntax",
                        not syntax_errors and {"app.py", "test_app.py"} <= set(trees),
                        "; ".join(syntax_errors) if syntax_errors else "Python files parse successfully",
                    ),
                    _check(
                        "python_import_allowlist",
                        not import_violations,
                        "; ".join(import_violations) if import_violations else "imports are allowlisted",
                    ),
                    _check(
                        "python_no_dangerous_introspection",
                        not dangerous,
                        "; ".join(dangerous) if dangerous else "no forbidden calls/names/attributes",
                    ),
                    _check(
                        "python_no_top_level_side_effects",
                        _python_top_level_safe(trees.get("app.py", ast.Module(body=[], type_ignores=[]))),
                        "app.py top level may contain only imports, definitions and literal constants",
                    ),
                    _check(
                        "python_entrypoint_contract",
                        _python_entrypoint_ok(trees.get("app.py")),
                        "app.py must define handle with exactly one positional request argument",
                    ),
                    _check(
                        "python_tests_present",
                        tests_present,
                        "test_app.py must contain at least one test_* function or method",
                    ),
                    _check(
                        "python_tests_exercise_entrypoint",
                        tests_exercise,
                        "test_app.py must directly call handle",
                    ),
                ]
            )
        return checks


class BuildSandbox:
    def __init__(self, root: str | Path = ".helis/workspaces") -> None:
        self.root = Path(root).resolve()

    def workspace_for(self, run: BuildRun) -> Path:
        return self.root / str(run.opportunity_id) / str(run.id)

    def write(self, run: BuildRun, bundle: BuildBundle) -> Path:
        workspace = self.workspace_for(run)
        if workspace.exists() and workspace.is_symlink():
            raise UnsafeBuildArtifact("workspace may not be a symlink")
        workspace.mkdir(parents=True, exist_ok=True)
        root = workspace.resolve()
        for item in bundle.files:
            if not _safe_path(item.path):
                raise UnsafeBuildArtifact(f"unsafe generated path: {item.path}")
            destination = (root / item.path).resolve()
            if root != destination and root not in destination.parents:
                raise UnsafeBuildArtifact(f"path escaped workspace: {item.path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.is_symlink():
                raise UnsafeBuildArtifact(f"refusing to overwrite symlink: {item.path}")
            destination.write_text(item.content, encoding="utf-8")
        return root

    def read(self, run: BuildRun) -> BuildBundle:
        if not run.workspace:
            raise UnsafeBuildArtifact("build run has no workspace")
        root = Path(run.workspace).resolve()
        if self.root != root and self.root not in root.parents:
            raise UnsafeBuildArtifact("stored workspace is outside configured sandbox")
        files: list[BuildFile] = []
        for path in run.file_paths:
            if not _safe_path(path):
                raise UnsafeBuildArtifact(f"unsafe stored path: {path}")
            source = (root / path).resolve()
            if root != source and root not in source.parents:
                raise UnsafeBuildArtifact(f"stored path escaped workspace: {path}")
            files.append(BuildFile(path=path, content=source.read_text(encoding="utf-8")))
        return BuildBundle(files=files)


def bundle_hash(bundle: BuildBundle) -> str:
    digest = hashlib.sha256()
    for item in sorted(bundle.files, key=lambda file: file.path):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
