# Spreadsheet fidelity package, review-fix round 4: disclosure by construction (2026-09-14, under D-55)

Worktree `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad\wt-excel`,
branch `build/sprint-5-excel` @ `1619fc7`. Stray untracked `out1.txt`, `out_regress.txt`: never add. Interpreter
`C:\Users\Alex\document-iq\.venv\Scripts\python.exe` from the worktree root; selftest `PYTHONPATH=src`. addopts carry
`-q`. Never `-k`. Real Excel is installed; earlier rounds drove it through COM for reference files. Close it after
use; never leave an Excel process running.

**D-55: fix and review alternate until a review confirms no A or B finding.** Rounds so far: 2A+8B, 9B, 4B, 5B.
The count is not falling because each round finds another Excel feature whose content is neither read nor named.
A list of features does not converge. **This round's central job is to make that class impossible by
construction**, then fix round 4's five findings on top.

## Read first

1. `...\scratchpad\review_excel_r4\findings.md` and `payload.json` (probes and real-Excel files under
   `review_excel_r4\`).
2. `...\scratchpad\excel_fix3\part_types.md`, `custom_views.md`, `notes_list.md`, and `excel_brief_fix3.md`.

## 1. Disclosure by construction (the class)

Every piece of a workbook the extractor does not explicitly read is NAMED, whatever it is, without anyone having
listed it in advance:

* **Package parts and relationships (`.xlsx`, `.xlsm`):** walk every relationship from the package, the workbook
  and every sheet, chartsheet and drawing part, and every part in `[Content_Types].xml`. Classify each by an
  explicit table in code: READ (the extractor renders its content), NO EVIDENCE (styles, theme, calcChain,
  printerSettings and the like, each with a one-line reason in the table), or anything else. Anything else, known or
  unknown, including `vbaProject`, revision logs, rich-data parts, customXml, webExtensions, pivot caches, external
  links, connections and future Office part types, gets ONE FINAL note per workbook naming each unread part type
  and its count. A new part type Excel adds tomorrow is disclosed without a code change.
* **Cells:** any cell attribute or child the renderer does not understand (`vm`, `cm`, `ph`, unknown `t` values,
  `is` rich runs with phonetic `rPh`, extension lists) is counted and named, per sheet, not silently rendered as
  its cached value. A cached value shown for a cell whose real content is elsewhere (an in-cell picture) must never
  pass as the value.
* **Sheet XML and drawing XML elements:** the streamed readers keep a set of element names they handle; any
  content-bearing element outside it (under `sheetData`, `drawing` shapes, text bodies) is counted and named.
  Say which elements are structural (no content) and why.
* **`.xls` (BIFF):** the record-stream walkers keep the set of record types they handle; content-bearing record
  types outside it (revision log stream, OBJ subtypes, TXO without text, unknown records inside a sheet substream,
  and every OLE stream in the compound file other than `Workbook`/`Book`, `SummaryInformation`,
  `DocumentSummaryInformation`) are counted and named.
* **Tests:** a derived guard that builds workbooks with parts, cell attributes, elements and BIFF records the
  extractor has never seen (invented names) and asserts each is named in a FINAL note. Show it failing on
  `1619fc7`. Replace `part_types.md`'s prose with a pointer to the code table.

## 2. Round 4's five findings, each red on `1619fc7`

1. Undeclared first-page and even-page header/footer text (flags off) printed as the sheet's own: honor
   `differentFirst`/`differentOddEven` (`.xlsx`) and the HEADERFOOTER flag bits (`.xls`); undeclared text is named
   in a note (unmarked? it is not printed by Excel: say which and why), never printed as the header. Fix the twin
   test that pins the bug.
2. A picture in a cell (Excel 365 rich value, `t="e" vm="1"`): render `[picture in cell]` (or a stated
   placeholder) instead of `#VALUE!`, disclose, and keep a genuine `#VALUE!` distinct.
3. Shared-workbook revision history (`.xlsx` revision parts, `.xls` "Revision Log" stream): a FINAL note with the
   count of revision records (item 1's mechanism may cover it; show it).
4. A hyperlink on a text box, shape or picture: resolve `hlinkClick`/`hlinkHover` through the drawing's
   relationships and append ` <URL>` to the shape's line as cell links do; `.xls`: count linked shapes in the drawing
   note.
5. An equation (`a14:m`, OMML `m:t`) in a worksheet text box: read its text in order; use the plain-text fallback
   Excel stores when the math cannot be read; pin both formats agreeing on the reviewer's real-Excel file.

**The eight C items:** fix each that is a false note or a silent drop (the `&G` count from text alone; a hidden
text box printed as visible; text boxes on chartsheets on no page; the two overstated prose claims; the percent
negative-section decimals if the spec allows; the last-half-second date-time) and give one line for any that waits.

## Gates, in order

1. Red lines for every item (or named mutants caught); mutate COPIES under `...\scratchpad\excel_fix4\`; re-run the
   prior rounds' mutants.
2. `tests/test_spreadsheet_fidelity.py`, `tests/test_extract.py`, `tests/test_load_dependent.py`,
   `tests/test_pipeline.py`, `tests/test_emit.py`, `tests/test_walker.py`, `tests/test_amendments.py`,
   `tests/test_gui_states.py::test_the_chrome_is_us_english`, each in its own process; the selftest.
3. Corpus, counts only (reuse `...\scratchpad\excel_fix3\x10_corpus_measure`): the 138 embedded workbooks, status,
   pages, notes by marker, and each unread part/attribute/element/record type named, with counts.
4. The full suite once: count line and every failure verbatim.
5. Commit on `build/sprint-5-excel`, files listed explicitly, message ending
   `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Do not push.

## Rules

No `pip install`. No deleting files. No git `checkout --`, `reset`, `rebase`, `stash`. Never touch
`C:\Users\Alex\document-iq` except to read the register, and never another worktree. Helper scripts in files under
`...\scratchpad\excel_fix4\`, never heredocs. Invented text only (D-12). US English.

## Report, under 1,500 words

First: failed gates, findings you believe are wrong, existing tests changed and why. Then the construction (the
tables in code, what counts as structural, the derived guard and its red line); each finding; the C dispositions;
corpus counts; gates; commit.
