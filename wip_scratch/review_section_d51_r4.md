## Package: D-51 + D-54 + D-58, review ROUND 4, `<package>` = `d51_r4`

* Worktree `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad\wt-d51`,
  branch `build/sprint-5-d51`, clean. Round 3 reviewed `61baefd`; fix round 3 is **`4105aac`**. Review
  `git diff 61baefd 4105aac`, then `git diff 0a29e30 4105aac` (everything since the switch itself was accepted).
* **D-58 (Alex): the package is reviewed again as D-55 rules, alternating with fixes until a review confirms no
  A or B finding.** Round 3 found 1 A + 4 B. Rate honestly both ways.
* Rulings in `C:\Users\Alex\document-iq\docs\decisions\decision_register.md` (read-only; the branch's own copy is
  older): D-48 (its "impossible by construction" claim now withdrawn in place), D-49, D-51, D-54, **D-58** (with
  the build's corpus recount).
* Round 3: `...\scratchpad\review_d51_r3\` (findings, payload, probes). Fix brief `d51_brief_fix3.md`; builder
  evidence `...\scratchpad\d51_fix3\` (red runs, 21 mutants, corpus recount).

### What round 4 must establish

1. Every round-3 finding closed (re-run each probe against `4105aac`), including that the diagonal-watermark scan
   and the garbled-invisible-layer scan now read at least as much as at `0a29e30`.
2. **The mask is fully gone** and nothing still depends on it (dead helpers, stale tests, docs, help text); every
   assertion of "duplication impossible" or "exactly once" for text over an image is withdrawn in `src/`,
   `docs/`, `tests/`, `tools/`.
3. **The new MuPDF-device draw counting** (soft masks excluded, tiling-pattern fill area, clipping to the page,
   the sweep merge with its 64 x 64 grid fallback past 2,048 draws): wrong shares on shapes of your choosing
   (rotated and skewed draws, clip paths, nested Form XObjects, patterns with a matrix, images under a shading,
   Type 3 fonts drawing images, huge draw counts), the time on large pages, and what `pdf_spans` (Tier 3) now
   reports.
4. **Per-page measurement and marking:** `M_IMAGE_UNMEASURED`, the "k of n image region(s)" marks for the cap,
   the 8-pixel floor and OCR exceptions: true for every input that reaches them, counted once, and not counted
   as OCR attempts where they should not be (`ocr_yield`); the reading-run upper bound of 77 capped pages is a
   new disclosure on real corpus pages: is its wording right for an operator?
5. **The `IMAGE_TEXT_MAY_REPEAT` note:** fires exactly when the text may repeat, never on a chart beside text,
   names pages correctly, is deterministic, and does not reach Bates zones, first dates or accounting as a loss.
6. Determinism over repeated and concurrent runs on the fixtures; `tests/test_ocr_ordering.py` under load.
7. Mutants: re-run 10 of the builder's 21 on a fresh copy; try 5 of your own.

### Not findings

The recognition fingerprint staying `v2` (decided under D-54); marker merge conflicts; stale-journal replay
across builds (main session, at merge).
