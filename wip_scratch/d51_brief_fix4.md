# D-51 package, review-fix round 4: build D-60 (2026-09-23, under D-58/D-60)

SP = `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad`.
Worktree `SP\wt-d51`, branch `build/sprint-5-d51` @ `4105aac` (clean). Interpreter
`C:\Users\Alex\document-iq\.venv\Scripts\python.exe` from the worktree root; selftest `PYTHONPATH=src`. addopts carry
`-q`. Never `-k`. Helpers as files under `SP\recovered\d51_fix4\`, never heredocs. The old scratchpad was wiped and
rebuilt from transcripts under `SP\recovered\` (review rounds, `d51_fix3\` helpers, census scripts); snapshot
worktrees were not rebuilt: make them with `git worktree add --detach` under `SP\recovered\d51_fix4\`.

## Read first

1. **D-60** (and D-48, D-49, D-51, D-54, D-58) in `C:\Users\Alex\document-iq\docs\decisions\decision_register.md`
   (read-only; `build/sprint-5` @ `46d36f6`; the branch's copy is older).
2. `SP\recovered\review_d51_r4\findings.md` and `payload.json` (7 B + 3 C), and the reviewers' probe folders under
   `SP\recovered\review_d51_r4\` if present (the review ran in this session; its probes may also be under
   `SP\recovered\review_d51_r4\{wiring,spec,tests,verify_*}`).
3. `SP\recovered\d51_brief_fix3.md` and `SP\recovered\d51_fix3\`.

## 1. D-60, by construction (the class)

Region OCR on a MIXED page is used only when every image draw's area (as the MuPDF-device counter measures it,
clipped to the page) is covered by regions that were actually read (rasterized and passed to OCR without an
exception). Anything else, for any reason, including reasons nobody has listed yet, sends the page to whole-page
OCR the way a scan is read, with a note naming the page and why (the cause as a short code: cap, floor,
unmeasurable, uncovered area, OCR error...). Design it so that coverage is COMPUTED (area of draws minus area covered
by read regions, with a stated tolerance), not a list of known failure cases. Decide and state:
* what the fallback reads (the whole rendered page, text layer kept as D-49 says for the locator) and how its text is
  placed relative to the text layer, and whether `image_line_span` still holds (D-49's locator rule must not change);
* how a page that falls back is marked, counted in `ocr_yield`, and shown (D-52 later); whether the fallback is
  retryable when caused by an OCR error (round-4 finding 3: transient failures must be retried like whole-page OCR);
* what happens on a run with D-51's skip switch on (the default skips image reading on text pages: a fallback must
  not bring skipped pages back, and a skipped page stays `M_IMAGE_UNREAD` with its reason);
* the time cost: count, over the acceptance corpus (298 PDFs, 17,732 pages, counts only, no OCR run), how many pages
  would fall back and why, by cause.

A derived guard: pages built with draws the counter measures but no region covers (invented shapes: bands, pattern
fills, a draw under the floor, more than the cap) must each fall back and be named; show it red on `4105aac`.

## 2. Round 4's seven findings, each red on `4105aac` by full node id (or a named mutant caught)

1. Cap/floor drops: never called OCR failures; they cause fallback under D-60; a floor-only page never raises
   `OCR_DEAD_ENGINE`.
2. Banded scans: no line lost at band edges (the fallback, or a gap-tolerant merge, or both); the D-58 note never
   says "nothing is left out" when it is not true.
3. Region-OCR exceptions are transient and retried as whole-page ones are.
4. `IMAGE_TEXT_MAY_REPEAT`: overlap tested against glyph ink (or center-in-region), only against regions that yielded
   text; register/docstring wording aligned.
5. Stencil masks filled with a shading or tiling pattern are measured (hook `clip_image_mask` and friends) and read.
6. Restore `== 1` exact counts for OCR-only words (stamped scan body tokens, annotation date).
7. Tests for the four unheld behaviours (solid-fill stencil, `pdf_spans` on the 2,047-nested-state page, the
   merge-count on the dense-draw page, a 3-picture/cap-1 case asserting the count).

The three C items: fix each or one line on why it waits.

## Gates, in order

1. Red lines; mutants on COPIES (round 3's 21 plus the reviewers', plus your own for the coverage computation).
2. The gate-2 files from `d51_brief_fix3.md`, each in its own process; the selftest.
3. Corpus counts (reuse `SP\recovered\d51_fix3\census.py`): MIXED pages, pages falling back by cause, pages at share
   >= 0.90, duplication-note upper bound, all vs `4105aac`.
4. The full suite once: count line and every failure verbatim.
5. Commit on `build/sprint-5-d51`, files listed explicitly, message ending
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Rules

No `pip install`. No deleting files. No git `checkout --`, `reset`, `rebase`, `stash`. Never touch
`C:\Users\Alex\document-iq` except reading. Never another worktree (wt-word, wt-excel belong to others). Invented
text only (D-12). US English. Fix the class, not the repro; enumerate siblings. No contract field without stopping
to report first (A-25 is the open amendment; say if D-60 needs anything in it).

## Report, under 1,500 words

First: failed gates, findings you believe are wrong, existing tests changed and why, anything you stopped on. Then
the construction (the coverage computation, tolerance, fallback, marks), the corpus fallback counts by cause, each
finding, C dispositions, gates, commit.
