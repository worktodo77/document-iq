# Word fidelity package, review-fix round 3 (2026-09-14, under D-57)

Worktree `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad\wt-word`,
branch `build/sprint-5-word` @ `2671947` (clean). Interpreter `C:\Users\Alex\document-iq\.venv\Scripts\python.exe`
from the worktree root; selftest `PYTHONPATH=src`. addopts carry `-q`. Never `-k`.

**D-57: fix and review alternate until a review confirms no A or B finding.** Round 1: 2 A + 5 B; round 2: 6 B;
round 3: 4 B. Leave round 4 nothing serious in these classes.

## Read first

1. `...\scratchpad\review_word_r3\findings.md` and `payload.json` (probes, including a corpus census of symbol-font
   runs, under `review_word_r3\`).
2. `...\scratchpad\word_brief_fix2.md`, `...\scratchpad\word_fix2\`, and `docs/design/word_fidelity_spec.md`
   (addenda §A1-§A6).
3. D-49, D-50, D-56, D-57 in `C:\Users\Alex\document-iq\docs\decisions\decision_register.md` (read-only).

## Fix, each test watched RED on `2671947` by full node id

1. **The Bates zone's deletion-label skip reaches every document type**, and typed text triggers it (a wrong
   locator on a stampless page; a real footer stamp refused). The zone must exclude only the lines the Word reader
   emitted as its deletion list, never a line because of what it says. Carry that knowledge structurally (for
   example a per-page line span or count the Word reader sets, the way D-49's `image_line_span` marks image lines;
   say whether that is a contract field and, if it is, stop and report instead of adding it), or place the list
   where the zone cannot reach it. Class deliverable: enumerate every consumer that decides something from a
   page's text by pattern (Bates zones and near-miss repair, footer re-OCR selection, `_dated`, section recognition,
   tokens) and show none is steered by text any document can type.
2. **A deleted embedded object** becomes an ordinary child document, and the parent's note says its text is listed
   with the deletions (false for OLE objects, charts, SmartArt). Mark the child as coming from deleted content
   (a note on the child naming the deletion and its author), make the parent's note true for every shape, and
   update §A4 and the pinned test wording.
3. **Symbol-font characters in ordinary runs** (`w:t` whose run font, direct or by style, is Symbol, Wingdings,
   Webdings or ZapfDingbats, including `w:rFonts` `w:ascii`/`w:hAnsi`/`w:cs`/`w:eastAsia` and the private-use
   `F0xx` range) are copied through as Latin letters. Apply the same mapping and note as `w:sym`. ZapfDingbats: a
   table ships with reportlab (already a dependency; confirm, do not install anything). The corpus census shows 125
   Wingdings glyphs typed this way in 8 of the 53 `.docx`: re-measure after the fix (counts only). Class
   deliverable: every place the reader turns a character into text, and whether font can change its meaning there.
4. **Three behaviours no test holds** (eight or more deletion passages above a real stamp; a table nested inside
   deleted content; a hidden first-page header holding only a chart): an exact-output test each, shown failing on
   its mutant.

**Every C item in the r3 payload** (eleven), each fixed or given one line on why it waits. At least: a ZIP whose
root is an Office package while holding other members (none may vanish); a stored name containing marker-phrase
text miscounted as an evidence gap (quoted names must never be read as markers); comment authors and hyperlink
targets able to forge page lines (clean every label and target the way deletion authors are cleaned; enumerate
every value from the file that is interpolated into a line prefix or a note); a ZIP, PDF or compound file under a
text-like name silently read as raw text; adjacent deleted and moved-from text by one author split into two
passages; the three overstated note texts; `sanitize_message`'s stability claim.

## Gates, in order

1. Red lines for every item (or a named mutant caught); mutate COPIES under `...\scratchpad\word_fix3\`; re-run the
   103 round-2 mutants.
2. The 16 files from `word_brief_fix2.md` gate 2 plus `tests/test_ocr_ordering.py` and `tools/bates_acceptance.py`'s
   own tests if any, each in its own process; the selftest.
3. Corpus re-measure, counts only (reuse `...\scratchpad\word_fix2\measure\`): children 289, new Bates candidates
   vs `ea4f1cb`, changed first dates vs `ea4f1cb`, marked notes by marker, symbol glyphs mapped vs placeholdered,
   failing documents.
4. The full suite once: count line and every failure verbatim.
5. Commit on `build/sprint-5-word`, files listed explicitly, message ending
   `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Do not push.

## Rules

No `pip install`. No deleting files. No git `checkout --`, `reset`, `rebase`, `stash`. Never touch
`C:\Users\Alex\document-iq` except to read the register, and never another worktree. Helper scripts in files under
`...\scratchpad\word_fix3\`, never heredocs. Invented text only (D-12). US English.

## Report, under 1,500 words

First: failed gates, findings you believe are wrong, existing tests changed and why, and anything you stopped on
(a contract field). Then each item with its class deliverable; the C dispositions; corpus counts; gates; commit.
