# Codex review — DocIQ Sprint 1 (pipeline core), Round 2

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-1`
**Reviewed commit:** `2723aecc43bcf52afb22069d0288acc83ee03f30`
**Corrected-code integration commit:** `34906b6`
**Round 1 reviewed:** `781481743dfa81f96e5ac6b8288b2156eeff383a`
**Review date:** 2026-08-01

## Verdict

**HOLD — no A findings; four Round 1 B classes remain partially open.**

Round 2 closes B-4, B-5, B-7, and D-1. The operator-facing part of B-6 is substantially corrected, and the explicit missing-root, disk-blocked, and cancelled paths in B-1 no longer publish. The complete suite and self-test are green.

The hold is for reachable production behavior and machine-contract inconsistencies outside that coverage:

1. an inventory traversal failure can still publish a partial or empty run, and an aborted `RunResult` still says `COMPLETED`;
2. resume can replay evidence produced under a different effective configuration, and distinct fractional deadlines collapse to one identity;
3. the non-recursive walker still has a silent top-level inventory-loss path; and
4. contract 1.4's structural-token and ceiling fields are never populated.

The binding preamble says that non-convergence after two rounds on one item is resolved by descope, not a third review. Accordingly, this verdict does **not** request Round 3. The final section gives narrow Sprint-1 descope conditions that remove the affected paths and claims.

## Round 2 disposition

| Round 1 item | Round 2 disposition |
|---|---|
| B-1 — incomplete runs can publish | **PARTIAL — remains B** |
| B-2 — effective identity incomplete | **PARTIAL — remains B** |
| B-3 — silent evidence loss | **PARTIAL — remains B** |
| B-4 — arbitrary duplicate-hash legacy ID | **CLOSED** |
| B-5 — Bates confirmation not enforced/reused | **CLOSED** |
| B-6 — unsound token floor/provenance | **PARTIAL — remains B at the machine seam** |
| B-7 — unsupported files absent from index | **CLOSED** |
| D-1 — dirty index rows cannot reconcile | **CLOSED** |

## Second opinions requested

### Hashing `terminal_status`

**Agree.** Completeness is a property of the evidence set represented by a `RunResult`. A cancelled partial result and a complete result with the same records must not have the same result identity. This is intentionally different from a retry disclosure: the retry can leave the evidence identical, while cancellation changes the completeness claim.

Only the canonical enum is needed to establish that distinction. The free-form `terminal_status_reason` is invocation prose and does not need to participate in identity; excluding that string would avoid a wording edit changing a diagnostic result hash. That is advisory here because incomplete runs publish no corpus manifest.

The implementation does not yet realize the accepted decision: the canonical fields are left at their completed defaults on aborted results, as B-1 below demonstrates.

### `BLOCKED` versus a genuinely empty completed run

**Agree, with one necessary boundary.** A path that does not exist or cannot be reached is blocked. A directory that is successfully enumerated and contains no files is a legitimate empty completed run and may replace prior deliverables.

“Successfully enumerated” is load-bearing. If `iterdir()` fails on the root or a subtree, DocIQ has not established that the folder is empty or complete. The current code nevertheless leaves the run completed; that is the remaining B-1 path.

### A-05(b), disk-headroom multiplier outside identity

**Agree.** The functional reason is sufficient: the multiplier decides whether extraction begins; it does not change the evidence of a run that completes. A blocked attempt has a terminal status and no corpus publication. Recording the multiplier in invocation metadata is appropriate. The prohibition on floats is not, by itself, a reason to omit an output-affecting value—an exact integer or decimal-string representation would be available—but this value does not affect completed evidence.

## B-1 — The typed status and publication boundary are still incomplete

**Locations:** `src/dociq/contracts.py:116`, `src/dociq/contracts.py:596`, `src/dociq/runstate.py:58`, `src/dociq/ingest/walker.py:1008`, `src/dociq/ingest/walker.py:1024`, `src/dociq/pipeline.py:520`, `src/dociq/pipeline.py:675`

The explicit safety structure is sound as far as it goes:

- `pipeline.run` returns before Stage 3 when `RunTermination.publishable` is false;
- the stale-output purge independently requires and validates a complete termination;
- blocked/cancelled accounting is forced red;
- the incomplete-run record cannot shadow a normal deliverable; and
- a later completed run removes that diagnostic directory.

Two gaps remain.

First, contract 1.5 and `runstate.py` define two different `TerminalStatus` enum classes. The walker carries the `runstate` type, while `RunResult` declares the contract type. More importantly, neither blocked walker return supplies `terminal_status` or `terminal_status_reason`, and `pipeline._abort` constructs another `RunResult` without them. All three therefore take the contract defaults.

A direct missing-root probe returned:

```text
RunNotes termination = blocked
RunResult terminal_status = completed
RunResult terminal_status_reason = ''
```

The test named `test_the_terminal_status_is_in_the_machine_readable_result_of_every_run` asserts `PipelineOutcome.termination` and the log, not `PipelineOutcome.result.terminal_status`, so it does not cover the amended contract field. A consumer holding the machine contract is told the opposite of the outcome wrapper.

Second, `walker._iter_files` catches an `iterdir()` failure, appends a warning, and returns whatever it found elsewhere. It does not set a non-complete termination. If the root becomes unavailable after `root.is_dir()` succeeds, the walk returns zero documents with `COMPLETED`; if a subtree becomes unavailable, it returns a partial inventory with `COMPLETED`. Both continue through the publication path.

A direct root-enumeration probe produced:

```text
termination = completed
documents = 0
warnings = ("a folder could not be listed and NOTHING inside it was inventoried: '.' ...",)
```

A warning does not make an incomplete corpus safe to publish. Any failed inventory enumeration must make the run non-publishable. The contract enum must have one canonical definition, and every returned `RunResult` must carry the same status and reason as the outcome and diagnostic artifacts.

## B-2 — Effective identity is not yet the identity of resumed evidence

**Locations:** `src/dociq/ingest/walker.py:148-196`, `src/dociq/ingest/walker.py:471-488`, `src/dociq/ingest/walker.py:1041`, `src/dociq/pipeline.py:667`, `src/dociq/pipeline.py:774`

`EffectiveLimits` is a good contract addition. It now covers the five content caps, recursion, the watchdog and retry controls, worker recording, and an OCR model identity based on package version plus model bytes. The deterministic manifest names the effective identity, and the worker-width exclusion is backed by a multi-width byte comparison.

The resume path bypasses that work. `pipeline.run` passes the caller's original `RunConfig` to `walker.run`; only after the walk completes does it attach `walker.effective_limits(...)` and the actual OCR-enabled/disabled identity. Inside the walker, the resume journal is loaded and created from the earlier config.

`_resume_identity` also manually selects fields and does not include `config.limits` even when a caller supplied it. A direct comparison confirmed that two configs with different timeout and recursion limits produce the same resume identity:

```text
resume_identity_ignores_limits = True
```

This permits a clean cached record produced with OCR disabled, a different OCR model, or different XLSX/CSV/ZIP caps to be replayed under a run whose final config records the new settings. “OCR disabled” is not a transient marker, and a successfully truncated spreadsheet is not degraded, so those cached records are eligible for replay. The final manifest can therefore honestly hash the *new configuration* while the documents were produced under the old one.

There is a second identity collision. `WalkOptions.file_timeout_s` and `DOCIQ_RETRY_BUDGET_S` are floats used as float-valued deadlines, but `effective_limits` rounds them to whole seconds. The probe `1.1 s` versus `1.4 s` produced:

```text
recorded file_timeout_s = 1 / 1
effective limits equal = True
```

Those deadlines can abandon different files while presenting the same identity. Represent them exactly as integer milliseconds (or reject fractional values), and build the full effective extraction config before resume lookup. The resume key must use the same identity projection that validates the evidence it replays.

## B-3 — The non-recursive inventory still has a silent-loss branch

**Location:** `src/dociq/ingest/walker.py:302`

The EML body and attachment markers are correctly wired into retry, and the broader sweep materially improves the class: EXIF/date/GPS, slide notes, archive and attachment reads, and recursive-directory failures now carry disclosures or stable markers. The transient/final marker vocabulary and evidence-loss accounting are useful safeguards.

However, the `recursive=False` branch returns only entries for which `Path.is_file()` is true. On current pathlib behavior, an entry that cannot be statted can yield false rather than raise. That entry is dropped before `scan` can create the unreadable Tier-2 record, and the recursive branch's new note is never reached. The completed run then has no warning, index row, or accounting representation for that source entry.

Use the same guarded iterator/classification path for recursive and top-level-only walks. Under the two-round rule, the narrow Sprint-1 descope is to force recursive walking and reject `recursive=False`.

## B-6 — Contract 1.4's replacement token fields remain empty

**Locations:** `src/dociq/contracts.py:501`, `src/dociq/contracts.py:506`, `src/dociq/pipeline.py:204-234`

The substantive correction is accepted:

- the universal hard-floor claim is withdrawn;
- D-03 is no longer claimed refuted on the corpus;
- `tokens <= UTF-8 bytes` is correctly labeled as the only asserted tokenizer-independent bound;
- the 0.70–1.60 tokens-per-pre-token interval is labeled as an assumption;
- `ratio_refuted` now means a conditional, one-direction inconsistency under stated assumptions;
- one `provenance_text()` builder supplies the log/PDF/handoff account; and
- the operator-facing outputs no longer claim every run used a calibrated character ratio.

The contract projection was not updated after amendment 1.4. `_to_contract_estimate` explicitly sets the retired `floor_tokens` to zero but never sets `structural_tokens` or `token_ceiling`. Both new fields therefore remain at their “not measured” defaults even though the same run measured them and writes them into other artifacts.

A direct projection of text with five pre-tokens and 20 UTF-8 bytes returned:

```text
MeasuredEstimate: pretokens=5, utf8_bytes=20
contract TokenEstimate: structural_tokens=0, token_ceiling=0
```

This makes the machine contract disagree with the processing log and leaves a future real GUI adapter with no structured value to read. Populate the fields from `est.profile.pretokens` and `est.profile.token_ceiling`, or descope those machine fields and any consumer claim that Sprint 1 supplies them.

## Corrections accepted

### B-4

Hash and Bates fallback now retain candidate groups and claim only a one-document/one-free-row key. Ambiguous groups fall through to DIQ and index-only reconciliation with an accurate warning. The Bates sibling and the promised lowest-Original-Sort path tie-break are corrected.

### B-5

The persisted Bates token round-trips the complete grammar, including exact digit widths and suffix separator. Application enforces all fields; stored confirmation is loaded; unreadable stored confirmation fails closed; and pending/rejected decisions are not promoted into confirmation.

### B-7

Supported and unsupported records pass through one assignment/minter operation and are split back by `ProcessingStatus`. Unsupported entries receive stable IDs and index rows while remaining absent from `clean_text/` and `sources.json`. The real-index criterion-5 evidence is consistent with this design.

### D-1

Dirty rows are retained as quarantined reconciliation entries, excluded from the LI number space and snapshot row count, and emitted index-only with their reason and raw cells. The prior claim is now true.

## Verification performed

- Read the Round 2 request from `build/sprint-1` and reviewed the correction range from the Round 1 base through `2723aec`.
- Read contract amendments 1.3 through 1.5, the corrected production paths, emit/GUI seams, and the new regression tests.
- Focused correction suite: passed.
- Full project suite under the repository `.venv`: **592 tests passed in 706.4 seconds**.
- `python -m dociq.selftest`: **56 checks passed**, 25 pages across 17 documents, 2 inventoried; determinism exercise reported 8 runs and 1 corpus hash.
- Reproduced the contradictory abort status, completed-on-enumeration-failure state, fractional-timeout identity collision, resume-identity collision, and empty contract token fields directly.

The initial system Python lacked declared document/PDF dependencies; all authoritative validation above used `C:\Users\Alex\document-iq\.venv\Scripts\python.exe`.

The refreshed criterion-7 evidence is a useful stratified-sample proof, not a full-corpus proof under contract 1.5. The request discloses that limitation accurately. The old whole-corpus pair is correctly treated as void after the identity change. The unreproduced watchdog retry, unreproduced PPTX fault, unattempted Bates acceptance criterion, cancelled OCR benchmark, advisory runtime miss, and emit-crash atomicity gap remain disclosed scope/evidence limitations rather than new findings in this review.

## Binding Sprint-1 descope condition

No Round 3 is requested. To comply with the preamble's two-round rule, Sprint 1 must remove the remaining affected surfaces from its supported claim:

1. **Incomplete traversal:** any root/subtree enumeration failure must terminate without publication; until the canonical contract field is wired, do not expose an aborted `RunResult` as a completed machine result.
2. **Resume identity:** disable resume for Sprint 1. A future reviewed version may restore it after the journal key uses the pre-walk effective identity.
3. **Fractional deadlines:** accept only exactly represented whole-second deadline/budget values, or freeze the overrides; do not accept distinct values that hash identically.
4. **Non-recursive walk:** force recursive mode and reject `recursive=False` for Sprint 1.
5. **Token machine seam:** do not advertise or consume `RunResult.structural_tokens` / `token_ceiling` as populated Sprint-1 fields while they remain zero; operator-facing figures may continue to use the corrected measured-estimate path and its provenance.

With those paths and claims explicitly removed, the accepted B-4/B-5/B-7/D-1 corrections and the operator-facing B-6 correction do not require a third review.
