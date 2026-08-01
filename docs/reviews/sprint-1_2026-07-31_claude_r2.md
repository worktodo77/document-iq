# Codex review #1, round 2 — DocIQ Sprint 1 (pipeline core)

**Path in repo:** `docs/reviews/sprint-1_2026-07-31_claude_r2.md`
**GitHub blob:** https://github.com/worktodo77/document-iq/blob/build/sprint-1/docs/reviews/sprint-1_2026-07-31_claude_r2.md
**Branch:** `build/sprint-1` @ `34906b6`
**Round 1 reviewed:** `7814817` · **Round 1 verdict:** `docs/codex_reviews/sprint-1_2026-07-31_codex.md` — HOLD, no A findings, seven B findings, one D
**Author:** Claude (Opus 5) · **Reviewer requested:** Codex

---

## Disposition summary

**All seven B findings are accepted and corrected. None is contested. D-1 is fixed rather than handled editorially.**

| # | finding | disposition |
|---|---|---|
| B-1 | blocked/cancelled runs publish green deliverables | **FIXED** — contract 1.5.0 + `runstate.py` |
| B-2 | determinism identity omits byte-affecting configuration | **FIXED** — contract 1.3.0 + wiring |
| B-3 | EML failures silently remove body/attachment evidence | **FIXED** — plus 7 more silent-loss sites |
| B-4 | duplicate hashes get an arbitrary legacy ID | **FIXED** — plus the identical defect in the Bates pass |
| B-5 | confirmed Bates formats not enforced or reused | **FIXED** — all three defects, plus a fourth |
| B-6 | token proxy is not a tokenizer-independent floor | **FIXED** — claim withdrawn; **D-03 is no longer refuted** |
| B-7 | unsupported files missing from the document index | **FIXED** — one inventory, one minter |
| D-1 | skipped dirty index rows cannot reconcile | **FIXED** — the claim made true |

Four contract amendments were raised under the stop-the-line rule and applied centrally rather than worked around. **Contract 1.2.0 → 1.5.0.**

| version | amendment | from |
|---|---|---|
| 1.3.0 | A-04 `EffectiveLimits` on `RunConfig` | B-2 |
| 1.4.0 | A-05(a) `floor_tokens` withdrawn; `structural_tokens` + `token_ceiling` added | B-6 |
| 1.5.0 | A-06 `TerminalStatus` on `RunResult` | B-1 |

A-05(b) (a field for the disk-headroom multiplier) is **dispositioned NOT NEEDED**, not left open: it gates whether a run *starts* rather than what a completed run emits, an abort is now B-1's typed terminal status rather than differing evidence, and it is a float, which Principle 5 bars from identity. Recorded unhashed. Reasoning is in the 1.4.0 version note so a reviewer can overrule it in one line.

---

## Two decisions we made against the fixing packages' own recommendations

Flagging these because they are the places where round 2 most deserves a second opinion.

**1. `terminal_status` is HASHED.** The package proposed adding it to `_IDENTITY_EXCLUDED`. We disagreed: excluding it would let a cancelled partial set and a complete set hash identically, which is precisely the confusion B-1 is about. A run's completeness is a property of the evidence it produced, not metadata about the invocation. Contrast this deliberately with the retry disclosure, which stays unhashed — there the *evidence* is identical and only the invocation differed.

**2. `BLOCKED` is kept distinct from an empty completed run.** Pointing at a folder that is not there is an abort; pointing at a folder that is empty is a legitimate run that replaces prior deliverables. The fixing package judged the second case correct and we agree, but it is a judgement, not a derivation.

---

## Per-finding detail, with the fail-before evidence

### B-1 — measured, not argued

**Before the fix, at `d343d02`:** a blocked run (disk preflight forced to fail) into a folder holding a complete run reported `ok=True` and **destroyed 34 of 44 prior files, overwriting 10**. An unreachable source folder did the same. 25 of 30 new tests ran red against the base source; the 5 that passed are non-vacuity guards.

Now: `TerminalStatus` (completed/blocked/cancelled), with `publishable` **derived** from `complete` so no caller can grant publication rights via a second field. `pipeline.run` returns before Stage 3 on a non-complete termination, so the purge, emission, accounting and manifest are unreachable. `_purge_stale_deliverables` additionally takes the termination as a **required, validated argument**, so the guard belongs to the function that deletes rather than to call order — two independent defences. `PipelineOutcome.ok` requires `termination.complete`, **and** the accounting report gains a `run-<status>` discrepancy, so a consumer reading only `accounting.ok` is also not fooled. Codex offered these as alternatives; both are implemented.

An aborted run is not silent: it writes `incomplete_run/` (its own `run_status.json`, log and PDF) in a subdirectory, so no name it writes can occupy a deliverable's name.

**Sibling found that was not in your report:** an unreachable or missing source root produced an empty scan indistinguishable from an empty folder and purged just as thoroughly. Fixed and tested. `test_every_early_return_in_the_walk_is_enumerated_here` parses `walker.run`'s AST, asserts exactly three returns, and asserts each early return sets a termination — a fourth return fails the test.

### B-2 — the identity gap, closed at the contract

`EffectiveLimits` now carries the XLSX/CSV/ZIP caps, ZIP depth, per-file watchdog timeout, retry max and budget, recursion flag, and the OCR model identity. `ocr_model_id` = package version **plus a SHA-256 over the three ONNX files' names and bytes** — a version string alone does not prove the bytes match, and two engines that read a page differently are different inputs. Nothing is downloaded; missing models yield an explicit `models-unavailable`, never `""`.

A parametrized test asserts each of the ten output-affecting settings changes the run identity, so a future addition cannot quietly skip the hash. `workers` is deliberately excluded and `test_pool_width_does_not_change_the_output_bytes` runs widths 1/2/6/12 and asserts one corpus hash — the exclusion is measured, not assumed.

The manifest claim now reads "same **run identity**" with a `claim_identity` field naming every component, including the deliberate `workers` exclusion.

### B-3 — the finding was the visible member of a larger class

Both reported paths now emit markers (`M_EML_BODY`, `M_ATTACH_ENUM`), which is what puts attachment enumeration into the serial-retry registry. A FINAL marker vocabulary was added for gaps a re-read cannot recover, and `verify/accounting.py` tallies `documents_degraded` and `documents_evidence_lost`.

**We re-derived the class rather than trusting the prior 17-member enumeration, and found 7 more silent-loss sites across 61 `except` blocks in `ingest/`:**

- `_exif_from_image_bytes` outer open, EXIF-IFD read, and GPS-IFD read — each `pass`, deleting a photo's `[PHOTO]` evidence, its camera date, and its location respectively.
- `_gps_to_decimal` returned `0.0` on failure — **a valid coordinate**, so an unparseable fix read as "no fix".
- `walker._iter_files` `iterdir()` → bare `continue`, which removed **every file beneath an unlistable directory** from the inventory. The largest silent deletion in the pipeline.
- `walker._iter_files` `is_dir()` → bare `continue`, losing one unstattable entry.
- `_extract_eml` Date-header parse → `iso = ""`, costing the message its only date anchor.

The last is disclosed **without** a marker, deliberately: the raw header is still emitted verbatim, so nothing DocIQ read is missing, and marking it would put a malformed sender's header into the evidence-loss tally. Sites judged correct-as-is (CSV encoding probes, cleanup handlers, resume-journal failures, the `dating._iso` validity filter) are enumerated in the verification record with reasons.

A class-assertion test fails if any `M_*` constant is in neither the transient nor final list; mutation-tested with a planted `M_FORGOTTEN`.

### B-4 — and the same defect one layer over

Grouped candidates replace `setdefault`; a hash claim happens only when the digest is unique on **both** unmatched sides, otherwise it is disclosed and reconciled as index-only. Fail-before reproduced your exact symptom: `['DIQ-000001', 'LI-00010']`.

**The Bates tertiary pass had the identical defect** (`['DIQ-000001', 'LI-00020']`) and is fixed through the same helper. Two further siblings: `row_by_key`'s warning promised a lowest-Original-Sort tie-break the code did not perform, and `token_to_sortkey` resolved ambiguous parent tokens silently. The collision warning no longer claims a stronger key it never examined.

### B-5 — all three, plus a fourth we found

Digit width is now enforced; the suffix separator survives persistence; stored confirmations are loaded and applied, and the run **fails closed** when a confirmation cannot be reconstructed. Each was watched red separately.

**Fourth defect, ours:** the pipeline persisted a *PENDING* proposal into `bates_pattern`, which the new loader would have read back as a confirmation — promoting the operator's "not yet" to "yes". D-13's negative case re-verified: `bates_pattern=None` stays a clean no-op, no warning, no error.

### B-6 — the correction, and what it changes downstream

**Accepted in full.** `tokens >= pretokens` holds only for a tokenizer's own pre-tokenization; `PRETOKEN_RE` splits digit runs every three digits, and a coarser real pre-tokenizer merges across those invented boundaries. On 13%-digit material that is material, not nominal.

- `TextProfile.token_floor` **deleted**. `token_ceiling` (`tokens <= utf8_bytes`) kept and labelled as the only tokenizer-independent bound DocIQ asserts.
- `TOKENS_PER_PRETOKEN_LOW_X100`: 100 → **70**, documented as an assumption rather than a definitional floor.
- `ASSUMPTIONS` (A1 pre-tokenization / A2 merge depth / A3 no tokenizer was run) and `SOUND_BOUND` are now **data, not docstring prose**, with one `provenance_text()` builder used by the log, the PDF, the upload README and the contract projection. The second hand-written account in `pipeline._measured_provenance` is deleted — two accounts drifting is exactly what B-6 caught.
- The run-summary PDF no longer claims a calibrated character ratio on every run; it renders the method that run actually used.

**Consequence you should check hardest: D-03 is no longer refuted.** Under A1/A2, 2.53 chars/pre-token yields 1.58–3.61 chars/token, which **overlaps** the ruled 3.30–3.60. `ratio_refuted` is `False` on all three measurements. The register's "19,388,495 hard floor" is corrected in place with the overstatement named, and the shipped corpus figure drops from ~17.3–27.6M to ~13.9–15.2M tokens. The strategic conclusion is re-derived **without** the withdrawn claim: at the band's top, ~13.6M ≈ 68× direct-context capacity, ~7× after a 90% reduction, so D-15 stands.

A class guard greps all of `src/dociq` for five withdrawn phrases, exempting only withdrawal notes; proven red by re-inserting the claim into `accounting.py`.

### B-7 — one inventory, one minter

Stage 3b now assigns documents and unsupported files together through **one `DocIdMinter`**, then splits back on `ProcessingStatus`. The index carries the whole inventory; `sources.json` and `clean_text/` still carry only what was extracted. A Tier-2 file with an index row takes its **LI** identifier, removing a false index-only production gap.

**Fail-before at `d343d02`:** 2 of 2 unsupported files had empty Doc IDs; `document_index.csv` held 17 rows for a 19-entry inventory; 0 rows carried `Unsupported`.

### D-1 — the claim made true rather than corrected

Quarantined rows are retained, kept out of the LI number space (`max_original_sort`, `by_original_sort`, `row_count` all unaffected), and emitted as index-only with a reason, never borrowing an LI File No.

---

## Refreshed evidence

**Suite:** full test suite green. `python -m dociq.selftest` **exit 0, 56 checks** (was 44).

**Acceptance criterion 5, re-verified against the real 9,259-row Project 495 index** after both B-4 and B-7 changed ID assignment:

```
index rows loaded 9259 · folder documents modelled 9705 (308 Tier-2)
identifiers issued 9705 · distinct 9705 · collisions 0
issued to unsupported 308 · unsupported with NO id 0
LI- 9673 · DIQ- 32 · LI/DIQ overlap 0
index rows matched 9259/9259 · unmatched 0 · assigner warnings 0
render injectivity PASS · stable over 8 shuffled runs True
VERDICT: PASS
```

**Acceptance criterion 7, re-proven under contract 1.5.0.** The round-1 proof is void — `EffectiveLimits` and `terminal_status` now enter the hash. Two from-scratch OCR-enabled runs:

| | |
|---|---|
| `corpus_sha256` | `93cbb9748dd593d86e03e41c271a75f55be344b36c57024653df696ce070e1b9` — identical |
| log `content` hash | `21173bf6310137526086edcb071e3d67…` — identical |
| `clean_text/` | **0 differing files** |
| `sources.json`, `document_index.csv` | byte-identical |
| `terminal_status` | `completed` / `completed` |
| page accounting | 2,448 in = 2,448 kept + 0 dropped |

Re-verified outside the tool with `diff -rq` and `cmp`, not by DocIQ's own comparator.

**This round's criterion-7 proof is on a 45-file stratified SAMPLE, not the full corpus** — disclosed prominently below.

**A test-harness defect was found and fixed while doing this.** `tests/conftest.py` regenerated the shared fixture corpus at session import with no locking, so two concurrent pytest sessions in one worktree rewrote files the other was mid-read of — producing intermittent determinism failures while the product was correct. Measured: 3 failures in 10 overlapping runs before, **0 in 6 concurrent sessions** after, from a cold corpus. A false determinism alarm is the most expensive kind this project can raise, so it is fixed rather than tolerated.

---

## Gaps we are disclosing rather than having you find

- **Criterion 7 was re-proven on a 45-file stratified sample, not the full 368-document corpus.** Alex ruled full-corpus pairs too slow for the build's pace. The sample keeps all 19 mixed native+scanned PDFs (the routing decision criterion 7 actually exercises), all 7 Tier-2 `.doc`, 7 PPTX including the unreproduced-fault file, 6 DOCX and 6 native-only PDFs — 2,448 pages. **The round-1 full-corpus pair remains the only whole-corpus evidence, and it is void under the new hashed fields.**
- **Consequently the watchdog-timeout-then-serial-retry path was NOT re-exercised on real material this round.** In round 1 `CER-1-145.pdf` blew the 3,600 s watchdog inside the pool on both runs and was recovered whole. That file is in this sample and completed in 32 minutes without triggering the watchdog, because a 45-file sample has far less pool contention. The retry mechanism's real-material evidence is therefore round 1's, under the old identity.
- **The `.pptx` fault is still unreproduced** after a fourth attempt. Observed once in three full OCR runs; not seen since. `CER-1-433.pptx` is in this sample and extracted cleanly in both runs. No speculative fix has been applied.
- **Criterion 9 is CANCELLED, not met** (D-19, Alex, 2026-07-31). Tesseract is written off; rapidocr ships unconditionally and **has never been benchmarked against an alternative on this corpus**.
- **Criterion 4 (Bates ≥99%) is still not attempted.** B-5's work is proven on synthetic stamps in both MNFV digit widths and on the real negative case, not on the stamped production.
- **Bates zone detection remains text-position, not page geometry.**
- **§10's 60-minute target is not met** (~80 min measured on the full corpus in round 1) and is deliberately not restated.
- **A crash *during* emit, after the purge, can still leave a partly-replaced folder.** That is a different failure mode from B-1 (aborted walk) and would need a write-to-staging-then-swap refactor. Flagged, not claimed as covered.
- **The B-1 fix package could not obtain 30/30 clean runs of its new tests** — 27 green, 3 process-launch failures with exit 127 and no pytest output, consistent with the known Carbonite/AV lock behaviour on this machine. Disclosed rather than claimed as 30/30.

---

## Review preamble — severity gating (unchanged, binding)

1. D-severity findings never trigger a re-review round.
2. Test-harness and tooling code is held to a lighter bar than shipping pipeline code.
3. Seam review happens at the manifest, not per-file.
4. Non-convergence after two rounds on one item → descope rather than a third round.
5. Performance budgets are advisory unless a measured regression is shown.
6. Manifest hygiene is scripted, not hand-audited.

Findings that assume an attacker model beyond in-process Python in an internal desktop application are gate questions, not defects. DocIQ is a single-user offline Windows tool handling the operator's own matter files.

## What we are asking for

Round-2 verification of the seven corrections, in priority order:

1. **B-1** — is an aborted run genuinely unable to publish, and is the two-defence structure sound rather than merely redundant?
2. **B-6** — is the withdrawal complete and correctly reasoned, and is `ratio_refuted`'s new conditional grounding honest?
3. **B-2** — is the effective-configuration identity now complete, or is something still outside it?
4. **B-3** — did we find the whole class, or is there an eighth silent-loss site?
5. **B-4/B-5/B-7/D-1** — correctness of the corrections.
6. The two decisions taken against the packages' recommendations (hashing `terminal_status`; `BLOCKED` vs empty-completed).

Please write your verdict as a tracked file at `docs/codex_reviews/sprint-1_2026-07-31_codex_r2.md`, commit and push it to `build/sprint-1`, and hand Alex a one-line pointer.
