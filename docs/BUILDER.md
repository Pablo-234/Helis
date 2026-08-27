# HELIS isolated builder

## Purpose

The builder converts a **validated** venture into the smallest testable MVP without granting generated code access to the HELIS process, production credentials, the network, or arbitrary shell execution.

```text
validated venture
      ↓
bounded BuildSpec
      ↓
file-only generation
      ↓
path / extension / size gate
      ↓
per-run workspace + hash manifest
      ↓
fixed verifier
      ↓
TESTED / FAILED / BLOCKED
      ↓
preview deployment (future phase slice)
```

## Build eligibility

Only ventures in `validated` or already-claimed `building` state are visible to `BuilderMachine`. The first successful BuildSpec claim moves a venture from `validated` to `building` and appends a `build.claimed` event.

A venture cannot jump directly from discovery/validation into generated code.

## Runtimes

### `static_web`

Generated files may contain local HTML/CSS/JS/JSON/text only. The bundle must contain `index.html`.

The static verifier does **not execute JavaScript**. It checks the local bundle and fails if it sees external HTTP(S)/protocol-relative references or common browser network primitives such as `fetch`, `XMLHttpRequest`, `WebSocket`, or `sendBeacon`.

This is the preferred v0 runtime when a local interactive prototype is enough.

### `python_stdlib`

Generated Python may use only the standard-library runtime assumption and must include `tests/test_*.py`.

HELIS never accepts a model-provided test command. Verification uses one fixed command inside Docker:

```text
python -m unittest discover -s tests -p test_*.py
```

The container is launched with:

- network disabled,
- read-only root filesystem,
- all Linux capabilities dropped,
- `no-new-privileges`,
- PID limit,
- memory and CPU limits,
- unprivileged user,
- read-only workspace mount,
- bounded timeout.

If Docker is unavailable or the configured image is not already present locally, the run becomes **BLOCKED**. HELIS does not auto-pull an image and never falls back to running generated Python on the host.

## Generated-file boundary

The generator returns a JSON `BuildBundle`; it never returns shell commands.

HELIS rejects:

- absolute paths,
- `..` traversal,
- backslash/NUL paths,
- `.git`, `.github`, `.ssh`, `__pycache__`,
- `.env`,
- unsupported extensions,
- duplicate paths,
- binary/NUL content,
- excessive file counts and byte sizes,
- bundles whose `spec_id` does not match the persisted BuildSpec.

Workspaces are created under:

```text
helis-workspaces/<opportunity_id>/<build_run_id>/
```

Files are written to a temporary directory first. HELIS then writes `helis-build-manifest.json` with per-file SHA-256 hashes and a deterministic bundle digest before atomically promoting the directory to the final run path.

## Model boundary

The Build Planner may choose only `static_web` or `python_stdlib` and is instructed to exclude:

- payments,
- credentials,
- customer outreach,
- production deployment,
- external APIs,
- package dependencies,
- shell commands,
- infrastructure.

The Build Generator can propose source file contents only. Runtime verification and workspace policy are ordinary code outside the model prompt.

## Commands

```bash
helis build
helis build --opportunity-id <UUID>
helis build-status
helis build-status --opportunity-id <UUID>
```

`helis build` uses a separate bounded model budget. If the budget is exhausted, the build is deferred rather than recorded as a broken MVP.

## Current non-goals

Builder v0 deliberately does **not**:

- deploy to the public internet,
- access production secrets,
- install packages,
- repair failed code automatically,
- run generated shell commands,
- modify HELIS itself.

Those capabilities require later, separately auditable boundaries.
