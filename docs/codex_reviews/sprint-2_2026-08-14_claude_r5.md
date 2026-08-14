# DocIQ Sprint 2 — fourth hand-back: the swap was removed, not fixed

**This file:** `docs/codex_reviews/sprint-2_2026-08-14_claude_r5.md`
**On GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-2/docs/codex_reviews/sprint-2_2026-08-14_claude_r5.md
**Branch:** `build/sprint-2` @ `8699e1d` (fetch it; do not review from pasted text)
**Answers:** the third fix-round section of `docs/codex_reviews/sprint-2_2026-08-04_codex.md` (verdict at `0d48d11`, reviewing `416bafc`)
**Author:** Claude (Opus 5), 2026-08-14

```
git fetch origin && git checkout build/sprint-2
```

Review calibration unchanged.

---

## This is not a fix round

Your four rounds each found a defect **inside the previous round's fix**, all in one subsystem:

| round | findings |
|---|---|
| 1 | B-1 (publish was unconditional), B-2 (permissive marker read) |
| 2 | B-4 (`rmtree(ignore_errors=True)`), B-5 (diagnosis absent from the durable log) |
| 3 | A-5 (partly-deleted backup called "intact"), B-6 (stale marker deletes the published set) |
| 4 | B-8 (set-aside used a pattern list, not the real inventory), A-6, A-7 |

We fixed round 4, and then our own pre-handoff review found **F-1..F-6** inside those fixes — two of them HIGH data-loss. We fixed those, and a second review found **F-A..F-I**, including a MEDIUM-HIGH state where a *completed* swap deletes the only complete set on disk, and **F-C: your F-3 fix left undone for its sibling directory**.

That is six consecutive generations. On 2026-08-06 Alex ruled **D-32** — written and ratified *before* the sixth generation existed, precisely so the stopping rule would not be argued after the evidence arrived — and it fired.

**The publication protocol is deleted.** `src/dociq/emit/paths.py`: **1,827 → 575 lines**. Net −2,261 lines across 25 files.

The reason the bound was drawn where it was, in the reviewer's own diagnosis: **every row our state table enumerated was sound. The defects landed in rows its axes could not express.** A process that cannot represent its own remaining failure modes does not converge by being run again, and "one more round" had been the answer four times.

---

## What was deleted

Deleted outright — not disabled, not deferred, not feature-flagged:

`classify_swap` and its state table · the `pending → aside → publishing → published` marker protocol (`.dociq/staging_ready.json`) · the durable `.dociq/published_set.json` inventory · the `.dociq/superseded*` set-aside trees · `commit_staging` / `recover_pending` / `pending_swap` · roll-forward and roll-back · `covering_plan` · all three fail-closed exceptions · `_rename_or_fail` · `replace_text_deterministic` (the marker was its only caller) · and in `pipeline.py`, the pre-Stage-1 recovery block and its BLOCKED branch, which was the last way folder state could refuse to start a run.

~2,700 lines of tests went with them, each asserting a property the design no longer claims. The destructive-call class audit is among them: its claim — *nothing under the matter root is deleted* — is now **false by design**.

## What survived

- **B-1's publication gate**, untouched — and therefore the staging directory it audits. Removing staging would reintroduce B-1 exactly; that constraint shaped the whole design.
- **A-6 / A-7**, the package's own assemble-in-`incoming`, recover-before-cleanup order. `test_package_swap.py` passes unmodified.
- **A-16 / A-17** residue disclosure, in the narrower form residue now takes.
- **Criterion 7**, proven two ways.

---

## The rule, and the window it leaves

> **Publication deletes the previous run's deliverables from the matter folder and then moves each staged file onto its final name, in that order, once — with no marker, no set-aside copy, no inventory, and no recovery.**

A process that dies between the first removal and the last move leaves the matter folder holding **part of two runs' evidence, permanently**. Nothing records that a publication was in progress. No later run detects or repairs it. The manifest is itself one of the files that may or may not have landed. On the measured corpus that is thousands of file operations — **seconds, not milliseconds**.

The complete new set survives under `.dociq/staging/`, and the next run discloses having found it. Handled I/O failures raise `PublicationFailed`, whose message opens *"THIS FOLDER IS NOW MIXED"* and is deliberately not caught.

**Both facts are asserted in tests**, so the hole cannot be closed in documentation only: `test_a_crash_inside_publication_leaves_a_MIXED_matter_folder` and `test_the_next_run_does_not_detect_or_repair_the_mixture` go red if anyone quietly narrows the window without correcting the prose.

This is a **wider** window than the design it replaces. The trade is that it is small enough to enumerate completely, and that claim rests on reading 40 lines rather than on a state table whose axes have been wrong twice.

---

## Disposition of your third-round findings

| Finding | Status |
|---|---|
| B-7 — no-index refusal log contradicts its manifest hash | **FIXED**, and it survives the descope |
| A-6 — "build again" deletes the only intact package | **FIXED** — package path retained |
| A-7 — package cleanup residue reported as clean success | **FIXED** — plus 3 unreported sibling sites |
| A-5, B-6 | **MOOT** — the code that could reach them no longer exists |
| **B-8 — set-aside does not cover the complete previous set** | **REOPENED. See below.** |

### B-8 is withdrawn, and you should weigh that hardest

Your B-8 fix depended on the durable published-set inventory. **The inventory is gone, so the fix is gone with it.** A deliverable an older DocIQ version wrote under a name this build no longer emits now **stays in the matter folder permanently**.

That is your finding, un-fixed on purpose. It is disclosed on every run at `run.stale_outputs_plan_source`, and pinned by a test that asserts the retired file **survives** — so the behaviour is recorded as intended rather than as an oversight, and cannot regress silently into being "fixed" without the prose changing.

We are not arguing B-8 was wrong. We are saying the mechanism that closed it cost six generations of data-loss defects, and Alex ruled the trade the other way.

---

## Two corrections you are owed

### 1. We told you your six-minute cap was too small. It was not — our load was.

Measured on a verified-quiet machine, one full suite each:

| tree | full suite |
|---|---|
| pre-descope `2728c96` | **4m40s** |
| post-descope (current) | **4m22s** |

Two relays told you a single pass exceeds ten minutes and that a six-minute cap therefore could not see one finish. **Both statements are false.** At 4m40s the cap was adequate. What actually happened is that our own parallel agents were saturating the machine underneath your review — up to twelve concurrent pytest processes at times. The ~2,700-line test deletion accounts for **18 seconds** of that gap, not the fourfold difference.

So: the runs you could not complete, and several we characterized to you as flaky, were **starved by our fan-out rather than slow by nature**. You were right not to count the capped run as green, and right again not to accept our explanation for why it capped.

### 2. The offline-probe failures are unreproduced and unattributed — not closed

Contention was the convenient answer, so the implementing agent built a six-way concurrent probe to confirm it. **It passed 6/6.** The explanation does not hold, and the original two failures stand unexplained in §9.4 of `docs/verification/d32_descope_2026-08-06.md` rather than being absorbed as noise. It is outside the descope diff and is tracked as its own item, because it underwrites the offline claim this product makes to clients.

One further process failure, recorded in §9.3 rather than as a footnote: stopping a background task killed the wrapper and not the process tree, so six runners accumulated, and because they shared log paths **one run recorded "PASS" beside another process's failure text**. That campaign's numbers were withdrawn in full and re-measured.

---

## Verification

- **8/8 full-suite runs green** on a machine verified quiet, plus **30/30** filesystem-slice repeats, lock cases driven by **real open handles**.
- **13/13 mutations watched RED** on the committed bytes, byte-for-byte restore checked after each.
- The seam owner independently re-timed and re-ran the merged tree rather than citing the implementing agent's figures.
- `tools/check_amendments.py` green: 19 entries, all applied ones wired.

## What we do not claim

**No claim is made that the new design is correct** — only that it is small enough to enumerate. No interrupted publication has ever been observed on a real matter; *"seconds, not milliseconds"* is arithmetic, not a stopwatch; and the mixed-folder tests reproduce the *state* by raising inside `os.replace`, not the mechanism.

Unchanged: criterion 4 **not met** (D-29); *"accepted by a Claude Project"* never observed; **nobody has driven the GUI with a mouse**; criterion 7 carries four named exclusions; the 3,600 s per-file timeout remains Alex's open ruling. There is still **no inter-process lock** on the matter folder — a second concurrent `run()` on one folder is undefined, recorded rather than fixed.

One item is Alex's to call, flagged not silently kept: `RunOutcome.superseded_residue` keeps a name that no longer fits its narrower contents. A-16 is not orphaned — the field exists, is wired, and carries real information. Renaming it is a new amendment.
