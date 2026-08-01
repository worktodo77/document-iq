# Codex review — DocIQ Sprint 1 (pipeline core), round 2

**Repository:** `worktodo77/document-iq`  
**Branch:** `build/sprint-1`  
**Reviewed commit:** `2723aecc43bcf52afb22069d0288acc83ee03f30`  
**Implementation-fix commit identified by the request:** `34906b6`  
**Round 1 reviewed:** `781481743dfa81f96e5ac6b8288b2156eeff383a`  
**Review date:** 2026-07-31

## Verdict

**HOLD — no A findings; three B findings remain at the amended-contract seams.**

The destructive core of B-1 is corrected: a blocked or cancelled walk returns before assignment, purge, normal emission, accounting publication, and manifest construction; `_purge_stale_deliverables` also independently refuses a non-complete termination. B-3, B-4, B-5, B-7, and D-1 are closed on source inspection. The substantive B-6 correction is also accepted: the pre-token count is no longer presented as a tokenizer-independent floor, the remaining ceiling is correctly scoped to byte-level tokenizers, and `ratio_refuted` is now expressly conditional on disclosed assumptions.

The hold is not for those corrections. It is for three places where centrally amended contract fields or identity claims were not wired through the shipping pipeline:

1. contract 1.5.0 says terminal status is on `RunResult` and hashed, but aborted shipping results still default to `COMPLETED`, while the persisted status remains deliberately unhashed;
2. the declared run identity still omits the ordered content of the profile set that changes emitted evidence; and
3. contract 1.4.0's `structural_tokens` and `token_ceiling` fields remain zero in the pipeline's `RunResult` projection even when both were measured.

Under the request's binding rule that non-convergence after round 2 is handled by **descope rather than a third review round**, the affected contract/claim portions must be removed from Sprint 1 scope before merge. This verdict does not request round 3.

## B-R2-1 — A-06 is not connected to the shipping result, log identity, or manifest

**Locations:**

- `src/dociq/contracts.py` — `TerminalStatus`, `RunResult.terminal_status`, `RunResult.terminal_status_reason`
- `src/dociq/runstate.py` — a second `TerminalStatus` plus `RunTermination`
- `src/dociq/ingest/walker.py` — early-return and cancellation paths
- `src/dociq/pipeline.py` — `_abort`, completed result construction, `build_log` call
- `src/dociq/emit/log.py` — unhashed `run` section versus hashed `content`
- `src/dociq/verify/manifest.py` — corpus hash and identity declaration
- `tests/test_incomplete_runs.py`
- `tests/test_contracts.py`

Contract 1.5.0 added a typed status to `RunResult`, but the shipping path continues to use the pre-amendment parallel type in `runstate.py`:

- `walker.run` sets `RunNotes.termination` with `dociq.runstate.TerminalStatus`, then returns a `RunResult` without setting either amended terminal field;
- `pipeline._abort` constructs another `RunResult` without setting either field;
- consequently, `PipelineOutcome.result.terminal_status` is `contracts.TerminalStatus.COMPLETED` for a blocked or cancelled run, because the additive default is still in force;
- `PipelineOutcome.termination` separately says `BLOCKED` or `CANCELLED`.

The machine-readable result therefore contains two answers to the same question, and the amended contract's answer is the wrong one on every abort path.

The persistence path also implements the opposite of the round-2 disposition. `pipeline.run` passes terminal status through `run_notes`; `emit.log.build_log` writes `run_notes` only into the unhashed `run` section; the hashed `content` section carries no terminal status; and the manifest corpus hash has no terminal-status component. `tests/test_incomplete_runs.py::test_the_terminal_status_is_in_the_machine_readable_result_of_every_run` expressly asserts that terminal status is **not** in hashed content and checks `PipelineOutcome.termination`, not `RunResult.terminal_status`. `tests/test_contracts.py::test_completeness_changes_the_run_identity` proves only that two manually constructed `RunResult` objects hash differently; it does not prove that the pipeline ever constructs the non-complete object.

This leaves four statements in conflict:

1. contract 1.5.0 says the status is on `RunResult`;
2. the round-2 request says the status is hashed;
3. shipping code keeps the status in a separate type and an unhashed log section; and
4. the focused regression test requires the unhashed behavior.

### What is accepted

The no-publication behavior is sound and materially closes the dangerous part of B-1. A blocked or cancelled attempt cannot replace the last complete deliverables through the normal pipeline path, and the delete function has its own completion proof. The separate `incomplete_run/` record is a defensible diagnostic namespace.

### Second opinion on the flagged decision

The fixing package's recommendation was the better fit for this architecture: **termination status should not enter the deterministic corpus hash.** A cancelled attempt publishes no corpus and no new corpus manifest. It therefore cannot legitimately collide with a completed corpus hash; the previous completed manifest simply survives. If an independently verifiable identity for the failed attempt is wanted, hash the incomplete-run audit record or define a separate result/attempt identity. Do not call invocation termination part of a corpus identity for a corpus that was never published.

`BLOCKED` should remain distinct from an empty completed run. The request's second flagged decision is correct: an unavailable source root is an abort; a readable source root containing zero files is a complete empty corpus.

### Required round-2 disposition

Per the no-round-3 rule, **descope A-06/contract 1.5.0's `RunResult` and hashed-terminal-status claim from Sprint 1**. Retain the proven publication guard and incomplete-run diagnostics as the Sprint 1 behavior. A later contract pass can unify the duplicate status types and define a separate attempt identity without changing the corpus determinism claim.

## B-R2-2 — the effective run identity still omits output-affecting profile content and order

**Locations:**

- `src/dociq/pipeline.py` — construction of `effective`
- `src/dociq/profiles/apply.py` — ordered first-claimant profile application
- `src/dociq/profiles/model.py` — `FormatProfile.profile_hash`
- `src/dociq/contracts.py` — `RunConfig`
- `src/dociq/emit/log.py` — hashed profile records
- `src/dociq/verify/manifest.py` — `IDENTITY_NOTE`
- `tests/test_run_identity.py`

A-04 successfully captures the environment-controlled caps, retry policy, recursion flag, watchdog limit, and OCR model bytes. I accept that correction, including the measured exclusion of worker count.

The broader `RunConfig` contract remains incomplete, however. `PipelineOptions.profiles` is an ordered sequence, and `apply_profiles` can use any member of that sequence; claimant order and every profile's header patterns and section rules can change which pages are dropped and therefore change `clean_text`, the index, the sources map, the processing-log content, and the corpus hash.

The effective config records only:

```text
profile_id      = opts.profiles[0].profile_id
profile_version = opts.profiles[0].version
```

It does not record:

- the ordered set of profiles;
- the content hash of even the first profile;
- the content or version of later profiles; or
- the precedence order that resolves multiple claimants.

`FormatProfile.profile_hash` already exists, and `emit.log.build_log` writes each profile and its hash into hashed log content. That confirms profile content is treated as evidence-affecting data after the fact. But it is absent from the `RunConfig` projection that the manifest calls the run identity.

A direct counterexample requires no attacker model: keep profile 1's ID/version unchanged, alter profile 2's rule or order, and run the same corpus. `content_hash(effective_config)` remains unchanged while emitted evidence changes. The same failure exists with a single profile edited without a version bump; version immutability is not enforced.

There is also an internal identity-description mismatch: `RunConfig` hashing includes `output_root`, and the manifest's `claim_identity` says the output folder is part of the run identity, while `emit.log` deliberately excludes `output_root` from hashed content and the criterion-7 harness uses separate output destinations. No durable `run_identity_sha256` is persisted to resolve which projection is authoritative.

The new tests prove that each `EffectiveLimits` member changes a directly computed `RunConfig` hash. They do not prove that the full profile decision input is represented, nor that the identity described by the manifest is the identity actually persisted and compared.

### Required round-2 disposition

Per the no-round-3 rule, **descope profiled runs from Sprint 1's “same run identity” determinism claim**. The manifest must not assert that profile ID/version is a complete identity for profile-driven output. A later contract can add an ordered tuple of immutable profile snapshots (`profile_id`, `version`, `profile_hash`) and one persisted canonical run-identity hash, while excluding destination path consistently.

The criterion-7 sample remains useful evidence that the exercised build was repeatable. It is not proof of the broader identity claim currently written in the manifest.

## B-R2-3 — A-05's replacement token fields are measured but not populated on `RunResult`

**Locations:**

- `src/dociq/contracts.py` — contract `TokenEstimate.structural_tokens` and `token_ceiling`
- `src/dociq/verify/tokens.py` — measured structure, assumed structural range, sound ceiling
- `src/dociq/pipeline.py` — `_to_contract_estimate`
- `src/dociq/emit/log.py` — full internal token-estimate record
- `tests/test_pipeline.py`
- `tests/test_tokens.py`

The mathematical and evidentiary correction to B-6 is accepted. The old floor is withdrawn, the proxy is labeled as a characterization under assumptions, and the only asserted tokenizer-independent bound is the UTF-8-byte ceiling for byte-level tokenizers.

Contract 1.4.0 then added two fields to carry the honest replacement measurements:

- `structural_tokens` — the structure-based figure under stated assumptions; and
- `token_ceiling` — the sound UTF-8-byte ceiling.

The pipeline computes the source data, and the processing log records `pretokens`, `token_ceiling`, method, assumptions, widening, and refutation status. But `_to_contract_estimate` populates neither amended field. Both therefore remain at their default `0`, which the contract documents as “not measured,” even on a run that measured them.

The focused pipeline test checks only that `floor_tokens == 0`, provenance text exists, and `ratio_refuted` agrees. It never asserts either replacement field. This is the same central-amendment wiring failure as B-R2-1.

The consequence is not merely cosmetic. When `ratio_refuted` is true, the contract says the ruled ratio was not the method used, but a consumer holding only `RunResult.tokens_before` receives no nonzero structural figure and no ceiling. A GUI adapter can then fall back to the ruled ratio and display a number the pipeline expressly says it did not use. The log and PDF remain honest; the machine-readable contract projection does not.

### Required round-2 disposition

Per the no-round-3 rule, **descope contract-level structural token figures and their GUI consumption from Sprint 1**. Keep the processing-log and PDF figures, which carry the actual measured method and provenance. Populate and define the contract fields in a later contract pass rather than shipping zeros that mean “not measured.”

## Corrections accepted as closed

### B-3 — evidence-loss markers

The reported EML body and attachment-enumeration paths now emit canonical markers, attachment enumeration enters the serial retry registry, final gaps are machine-countable, and the sibling exception audit materially improved coverage. The recursive folder-walk loss paths now disclose missing subtrees or entries. I did not establish an additional B-severity silent-loss path in the inspected shipping code.

### B-4 — duplicate fallback keys

Digest and Bates fallback candidates are grouped and claimed only when unique on both unmatched sides. Ambiguous groups remain DIQ/index-only and are disclosed. The helper also removes the same scalar-map defect from the Bates pass.

### B-5 — Bates grammar and reuse

The persisted token round-trips the full grammar, including exact digit-width alternatives and suffix separator. Application validates the complete format. Stored confirmations are reconstructed and applied, and an unreconstructible stored confirmation fails closed. Pending/rejected decisions are not promoted into confirmations.

### B-7 — unsupported inventory

Supported and unsupported records enter one assignment pass and one minter, then split by processing status. Unsupported records receive stable IDs and participate in the first-class index without being emitted as clean text.

### D-1 — quarantined master-index rows

Dirty rows are retained separately from the LI identifier space and can now reconcile as index-only with the reason and raw row context.

## D-R2-1 — the amendment register still describes A-06 as not applied

`docs/contracts/amendments.md` says A-06 is “RAISED — stop-the-line, not applied,” says the run-status branch did not modify `contracts.py`, and says the future amendment should turn `runstate.TerminalStatus` into a re-export. The round-2 request and `contracts.py` say A-06 was applied as 1.5.0, but the predicted re-export and wiring never occurred. This stale register text is editorial evidence of the unresolved integration above. It is D severity by itself and does not add a separate gate.

## Verification and disclosed limitations

I read the round-2 request from the branch and inspected the amended contract, run-state model, pipeline, walker, profile application/model, log and manifest emitters, token estimator and projection, Bates implementation, Doc ID assignment, master-index quarantine, GUI seam, and focused regression tests at `2723aec`.

I could not independently rerun pytest or the self-test in this runtime: the GitHub CLI is unavailable and outbound Git/DNS access from the execution container is blocked. The submitted green suite, 56-check self-test, criterion-5 result, and 45-file criterion-7 sample are therefore treated as submitted evidence, not as independently reproduced evidence. The source-level findings above do not depend on a failing test run; the focused tests themselves encode the contradictory or missing assertions.

The disclosed criterion-7 sample limitation, unreproduced PPTX fault, unexercised real watchdog retry this round, cancelled criterion 9, unattempted criterion 4, and advisory runtime miss are not independently promoted here. The separate crash-during-emit atomicity gap remains a valid disclosed future item, not a reopened B-1 path.

## Merge disposition

Current head is not mergeable under the round-2 request's own gate. Do not open a third review round. Before merging Sprint 1, the coordinator must explicitly descope:

1. A-06/contract 1.5.0's `RunResult` and hashed-terminal-status guarantee, while retaining the proven no-publication guard;
2. profiled runs from the manifest's complete-run-identity determinism claim; and
3. contract/GUI consumption of `structural_tokens` and `token_ceiling`, while retaining the honest log/PDF token reporting.

If those scope reductions are unacceptable, Sprint 1 remains on hold.