# HELIS controlled self-improvement

HELIS self-improvement is a sequence of independent, persisted trust boundaries. No model response is allowed to jump directly from an idea to live code.

## Trust chain

```text
audit signal / explicit objective
  ↓
bounded proposal
  ↓
isolated candidate workspace
  ↓
exact baseline-vs-candidate evaluation
  ↓
WAITING_MERGE_APPROVAL
  ↓ explicit branch approval
review branch materialization
  ↓
fresh green CI attestation
  ↓ explicit second merge approval
fresh matching CI re-attestation
  ↓
base-locked merge
```

## Candidate boundary

Phase-5 candidates are restricted to a deliberately small allowlist of low-authority logic modules. They cannot edit tests, policy, gateways, credentials, cash/resource accounting, deployment, CI configuration or the self-improvement guardrails themselves. A candidate may replace at most two approved source files, cannot add imports/dependencies or dynamic execution, and is written only below `.helis/self-improvement/`.

The candidate stores the SHA-256 of every baseline source file. The full candidate bundle has its own deterministic SHA-256. Mutating any candidate byte after materialization invalidates the sandbox verification.

## Evaluation boundary

`HELIS_SELF_EVAL_GATEWAY_URL` points to an operator-owned evaluator. The evaluator receives the exact candidate and must attest the exact baseline source hashes. Baseline and candidate must use the same immutable test suite. The baseline must be healthy, the candidate cannot execute fewer tests, reported regressions reject the candidate, and Phase 5 v1 only accepts an explicit `higher_is_better` metric improvement.

A successful evaluation does not modify git. It ends at `WAITING_MERGE_APPROVAL`.

## Review-branch boundary

`prepare-branch` binds a candidate to:

- its exact candidate hash;
- an exact 40-character base commit SHA;
- a deterministic review-branch name.

`approve-branch` approves only that persisted run. `materialize-branch` then calls the operator-owned `HELIS_SELF_BRANCH_GATEWAY_URL`. The gateway must verify the target files at the base commit still match the original baseline hashes before it writes the exact candidate to the review branch. It is forbidden to write the default branch or merge.

## CI boundary

A merge run starts in `WAITING_CI`. `HELIS_SELF_CI_GATEWAY_URL` is read-only and must freshly attest:

- exact candidate hash;
- exact base revision;
- exact review-branch name and head revision;
- SHA-256 of candidate source files on that branch;
- a nonzero test count;
- green `ruff` and `pytest` checks;
- no failed reported checks.

The persisted CI hash excludes the timestamp and canonicalizes check order, so a fresh semantically identical attestation matches while any change to branch head, files, checks or test count changes the binding.

## Final merge boundary

`approve-merge` is a second explicit `SELF_MODIFY` approval and is separate from `approve-branch`.

`merge-approved` does not trust the earlier CI result blindly. Immediately before any merge side effect HELIS requests another fresh CI attestation. It must match the approved stable attestation and exact branch head. Only then may `HELIS_SELF_MERGE_GATEWAY_URL` perform the merge.

The merge gateway must additionally attest that the default branch head immediately before merge is still exactly the candidate's approved `base_revision`. If main advanced meanwhile, the run is blocked rather than rebased, force-pushed or silently applied to newer code.

## Operator sequence

```bash
helis-self propose
helis-self materialize <PROPOSAL_ID>
helis-self evaluate <PROPOSAL_ID>

helis-self prepare-branch <PROPOSAL_ID> <BASE_COMMIT_SHA>
helis-self approve-branch <BRANCH_RUN_ID>
helis-self materialize-branch <BRANCH_RUN_ID>

helis-self prepare-merge <BRANCH_RUN_ID>
helis-self attest-merge-ci <MERGE_RUN_ID>
helis-self approve-merge <MERGE_RUN_ID>
helis-self merge-approved <MERGE_RUN_ID>
```

Each approval applies to one persisted run only. None of these commands weakens the global policy for future candidates.
