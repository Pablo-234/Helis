# HELIS preview publication

A `READY_PREVIEW` artifact is reviewed, but it is still local. Publication is a separate external
capability with its own run-scoped approval and audit trail.

## Flow

```text
READY_PREVIEW
      ↓
prepare publication run
      ↓
WAITING_APPROVAL
      ↓ owner/operator approves this exact run
READY
      ↓
re-read workspace
      ↓
recompute SHA-256
      ↓
verify hash == reviewed PreviewManifest hash
      ↓
verify source BuildRun == READY_PREVIEW
      ↓
verify adversarial BuildReview == PASS
      ↓
operator-configured HTTPS gateway
      ↓
PUBLISHED
```

Any modification to the artifact after review changes its hash and blocks publication before the
gateway is called.

## Gateway contract

Configure:

```text
HELIS_PREVIEW_GATEWAY_URL=https://...
HELIS_PREVIEW_GATEWAY_TOKEN=...
```

HTTP is rejected except localhost when `HELIS_ALLOW_INSECURE_LOCAL_PREVIEW_GATEWAY=1` is explicitly
set for development.

HELIS sends contract version 1 with the publication run, preview manifest, exact reviewed SHA-256,
entrypoint and bounded artifact files. Requests contain an idempotency key equal to the publication
run ID. The gateway must return JSON containing `accepted`, `dispatch_id`, optional `preview_url` and
optional metadata.

The gateway destination is configured by the operator. It is never chosen by an LLM.

## CLI

The preview publisher intentionally has its own command surface:

```bash
helis-preview prepare
helis-preview approve <RUN_ID>
helis-preview publish <RUN_ID>
helis-preview status
helis-preview gateway-status
```

`prepare` is side-effect free. `approve` only changes local permission state. `publish` is the only
command that performs the external publication action.
