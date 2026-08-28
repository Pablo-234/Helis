# HELIS Builder — Phase 2

The Builder converts a **validated** venture into a bounded preview artifact. It is deliberately not
a general-purpose autonomous coding shell.

## Boundary

A build may currently choose one of two reusable templates:

- `static_web_v1` — a local offer/landing preview with no external active code,
- `concierge_ops_v1` — an operating kit for delivering the value manually before software exists.

The model cannot add arbitrary paths, dependencies, shell commands, external scripts, credentials or
production deployment configuration. Generated files are constrained by a template allowlist and a
per-build file/byte cap.

## Pipeline

```text
VALIDATED venture
      ↓
bounded build brief
      ↓
whitelisted template
      ↓
model generates allowed text files
      ↓
deterministic verifier
  - path containment
  - file/byte caps
  - required files
  - secret scan
  - active-content checks
      ↓
isolated per-run workspace
      ↓
adversarial model review
      ↓
READY_PREVIEW manifest
```

A review score below 7/10 or any blocking issue fails the build. A deterministic verification failure
prevents the artifact from being written to the workspace at all.

## Why no arbitrary code execution yet?

Phase 2 starts with artifact generation, not an unrestricted terminal. A model-generated command is a
capability request, not trusted code. Executable builders can be added later as sandbox adapters with
resource/network limits and fixed test contracts.

## Publication boundary

`READY_PREVIEW` does **not** mean deployed. Publication is a separate external action and remains
behind HELIS policy. The preview manifest records the workspace, entrypoint and content hash so a
future deployment adapter can publish exactly the reviewed artifact rather than silently rebuilding
something different.
