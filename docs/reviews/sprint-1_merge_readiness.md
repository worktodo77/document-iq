# Sprint 1 — merge readiness

**Branch:** `build/sprint-1` @ `5c12eab` · **Contract:** 1.6.0
**Date:** 2026-08-01 · **Decision owner:** Alex

Sprint 1 is complete and, in my assessment, merge-ready. **The merge to `main`
is yours to authorize; nothing here presumes it.** Note that D-10 designates
Codex review #2 (end of Sprint 2) as the *merge gate* for the whole build —
merging Sprint 1 to `main` now is a separate call about consolidating a
finished sprint, not a bypass of that gate.

## What is proven

| criterion | status | evidence |
|---|---|---|
| 2 — page accounting, zero discrepancy | **PASS, real corpus** | 18,521 in = 18,521 kept + 0 dropped over 368 documents |
| 3 — markers resolve to original pages | **PASS** | 439/439, judged against a different extractor |
| 5 — Doc ID assignment, no collisions | **PASS, real index** | 9,705 IDs vs the real 9,259-row Project 495 index; 0 collisions; 9,259/9,259 matched; stable over 8 shuffled orders |
| 7 — byte-identical repeat runs | **PASS** | fixture 30 runs × 30 hash seeds → 1 hash; sample corpus 2 OCR-enabled runs, 2 destinations → identical corpus hash, run identity and log content; 0 per-file diffs across 86 files |

Suite green. `python -m dociq.selftest` exit 0, 66 checks. Contract 1.6.0 with
six amendments (A-01..A-10), every one raised under the stop-the-line rule and
applied centrally rather than worked around.

## What is NOT proven — read before merging

1. **Criterion 7's real-corpus proof is a 45-file stratified sample**, not the
   full 368-document corpus. The full-corpus pair from round 1 is void: the
   identity changed underneath it. The sample keeps all 19 mixed native+scanned
   PDFs, all 7 Tier-2 `.doc` and the unreproduced-fault `.pptx`, but breadth is
   not depth.
2. **The watchdog-timeout-then-serial-retry path has no real-material evidence
   under the current identity.** It fired in round 1 on `CER-1-145.pdf` and
   saved 222 pages; that run's identity no longer exists. The sample has too
   little pool contention to trigger it.
3. **The `.pptx` lxml namespace fault was never reproduced** across four
   attempts. Seen once in three full OCR runs. No speculative fix applied; the
   class is made safe by the serial re-read instead.
4. **Criterion 4 (Bates ≥99%) was never attempted.** The detector is proven on
   synthetic stamps in both MNFV digit widths and on the real negative case,
   not on the stamped production.
5. **Criterion 9 is cancelled, not met** (D-19). rapidocr ships unbenchmarked
   against any alternative on this corpus.
6. **§10's 60-minute target is missed** — ~80 minutes measured — and was
   deliberately not restated as a target.
7. **Criteria 1, 6 and 8** (end-to-end acceptance, verified-offline run,
   Claude handoff) are Sprint 2 by design.
8. **A crash during emit, after the purge, can still leave a partly-replaced
   output folder.** Distinct from the aborted-walk case that B-1 closed; it
   needs write-to-staging-then-swap.

   > **CLOSED IN PART, AND THE REMAINDER ACCEPTED — D-32, 2026-08-06.** The
   > *emit* half is closed: deliverables are built in `.dociq/staging/` and a
   > crash anywhere in Stage 5 leaves the previous run byte-for-byte intact. The
   > *swap* half is **not** closed and will not be. Six review generations of
   > defects in the publication protocol led Alex to descope it; publication now
   > deletes the previous deliverables and then moves the staged set in, with no
   > marker, no set-aside copy and no recovery, so **a crash between the first
   > removal and the last move leaves a partly-replaced output folder
   > permanently, and nothing detects it.** That is a narrower window than this
   > item described (it is publication, not the whole of emit) and a permanent
   > one. See `docs/verification/d32_descope_2026-08-06.md`.

## Review history

Two Codex rounds, no A findings in either.

- Round 1 (`7814817`): HOLD, seven B findings and one D. All accepted, none
  contested, all closed.
- Round 2 (`2723aec`): HOLD, three B findings and one D at the amended-contract
  seams — places where centrally added contract fields were never wired
  through. All closed. Codex prescribed **descope** under the no-round-3 rule;
  **we fixed instead**, on all three. Nothing depends on the offered scope
  reductions, and Codex requested no round 3.

Codex reversed one decision of mine and was right: terminal status does not
belong in the corpus hash, because a cancelled attempt publishes no corpus and
therefore cannot collide with one.

## Recommendation

Merge `build/sprint-1` to `main` on your authorization, then open Sprint 2 from
`main`. The branch is self-consistent, the gaps above are documented rather
than latent, and carrying an unmerged sprint through Sprint 2's packaging work
adds integration risk without buying anything.

If you would rather not merge until Codex review #2, that is equally defensible
— the cost is that Sprint 2 builds on a long-lived branch.

## Sprint 2 scope, as it now stands

1. Real pipeline wiring under the GUI (the seam is `gui/pipeline.py`;
   `get_pipeline()` is the whole swap).
2. Profiling checklist UI live; the D-14 waterfall driven by real figures.
3. Analyze-in-Claude Paths A and B.
4. PyInstaller single executable with bundled ONNX models. — **AMENDED by D-22
   (2026-08-01), after this note was written: a one-folder build shipped as a
   zip, not `--onefile`.**
5. Offline verification with network interfaces disabled (criterion 6).
6. Full MODEC end-to-end acceptance run (criterion 1), Bates acceptance on the
   MNFV production (criterion 4), handoff acceptance (criterion 8).
7. Codex review #2 — the merge gate.

Two carried decisions: **D-03 stands as ruled** (the refutation was withdrawn
after Codex B-6), and the emit-atomicity gap in item 8 above should be scheduled
rather than discovered.
