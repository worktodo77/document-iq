# Word fidelity package, fix round 4: build D-59 (2026-09-23, under D-57)

SP = `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad`.
Worktree `SP\wt-word`, branch `build/sprint-5-word` @ `347df32` (clean, pushed). Interpreter
`C:\Users\Alex\document-iq\.venv\Scripts\python.exe` from the worktree root; selftest `PYTHONPATH=src`. addopts
carry `-q`. Never `-k`. Helpers as files under `SP\recovered\word_fix4\`, never heredocs.

## The ruling

Read D-59 (and D-49, D-56) in `C:\Users\Alex\document-iq\docs\decisions\decision_register.md` (read-only;
`build/sprint-5` @ `14b3ebe`). In short: `PageRecord` gains a span naming the lines the Word reader wrote as its
deletion list (D-56), set by that reader only, like D-49's `image_line_span`. `BatesZone.slice_lines` skips
exactly those lines and nothing else: remove the `[deleted by ` text match entirely. The field belongs to amendment
**A-25**, which lives on another branch (`build/sprint-5-d51`: `docs/contracts/amendments.md` A-25,
`amendments.toml` `[amendment.A-25]`, `src/dociq/contracts.py` near line 288). Read those with `git show
build/sprint-5-d51:<path>`, never check it out.

## What to build

1. The field: name, type, validation in `PageRecord` (mirror `image_line_span`'s: which pages may carry it; a span
   inside the page's lines; what it means with `image_line_span` on the same page, if both can occur), serialization
   and hashing (it changes the page's identity only if the text it marks could change), and the fingerprint/run
   identity inputs D-49's field touched. Enumerate every place `image_line_span` is read or written and say for each
   whether the new field needs the same treatment.
2. The Word reader sets it on the page where it appends the deletion list; nothing else sets it.
3. `slice_lines` uses the span. Every zone consumer (`detect_candidates`, `zone_has_candidate`, `_zone_stamp`,
   `_zone_near_miss`) inherits it; show each with a test or say why the one test covers it.
4. The contract text: write the A-25 addition so that it merges with the d51 branch's A-25 as a pure union (a
   clearly separated paragraph or list entry; the same `CONTRACT_VERSION`, 2.3.0). If the amendment registry test
   on this branch requires the entry to exist here, add only the minimal fragment and say exactly what the merge
   must do. Do not flip A-25.
5. Spec §A4 and `slice_lines`' docstring: withdraw the "open defect" text round 3 added; grep for every assertion of
   the old text-based skip (docs, tests, docstrings, help text) and fix each.

## Tests, each watched RED on `347df32` by full node id

* A PDF page, an `.eml`, a `.txt` and an OCR'd page whose own text has a line starting `[deleted by ` keep that line
  in the zone (a stamp on it is found; a locator does not shift).
* A Word page's deletion list is still left out of the zone, including a deletion list in the last 8 lines and a page
  shorter than the zone.
* Validation refuses a span on the wrong page kind or out of range; a mutant that sets the span from text is killed.
* Round 3's 146 mutants re-run on a COPY (`SP\recovered\word_fix3\mutants.py`, pointed at a new copy dir), all
  killed or explained.

## Gates, in order

1. Red lines above; mutants.
2. The 18 gate-2 files from round 3 (`SP\recovered\word_fix3\gate2.py` or its list), each in its own process; the
   selftest.
3. Corpus, counts only (`SP\recovered\word_fix3\measure\`): new Bates candidates and changed first dates vs `347df32`
   and vs `ea4f1cb`, over the 53 `.docx` and their 289 children; and over the acceptance corpus's PDFs, how many
   pages have a line starting `[deleted by ` at all (expect 0; say so).
4. The full suite once: count line and every failure verbatim.
5. Commit on `build/sprint-5-word`, files listed explicitly, message ending
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Rules

No `pip install`. No deleting files. No git `checkout --`, `reset`, `rebase`, `stash`. Never touch
`C:\Users\Alex\document-iq` except reading. Never another worktree (read other branches with `git show`). Invented
text only (D-12). US English. Fix the class, not the repro.

## Report, under 1,000 words

First: failed gates, anything you stopped on, existing tests changed and why, and exactly what the merge with
`build/sprint-5-d51` must do for A-25. Then the field's design, the enumeration, tests, corpus counts, gates, commit.
