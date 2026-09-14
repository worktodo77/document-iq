# Word fidelity package: build spec (Sprint 5)

Committed 2026-09-14 from the scratchpad draft the package was built to, so that code and tests can cite it.
It was drafted under the working title "D-50" before that register number went to Alex's ruling on
embedded documents. Code and tests cite its parts as "Word spec part N". In the register, D-50 is only the
embedded-documents ruling (built as stage 2b), and D-53 is the increment that authorized the package.
The "Open for Alex" question below was answered by D-50: embedded documents are recovered as child documents.
Counts only; no corpus text (D-12).


Authorized under Increment 1 (Alex, Sprint 5). Branch `build/sprint-5`, base `a3d4c97`.
No contract change expected: DOCX stays one SYNTHETIC page; only its text and notes change.

## The rule the package makes true

Every character in a Word document's text-bearing parts reaches the page text, in source
order within its part, OR is named in a note carrying an evidence marker. Held by a DERIVED
class test over the fixture package: every `w:t` in document.xml, header*.xml, footer*.xml,
footnotes.xml, endnotes.xml and comments.xml is either in the output or covered by a marked
note. Field codes (`w:instrText`) are not content and are excluded by name.

## Why the whole extractor, not the reported defect

Today `_extract_docx` emits `paragraphs`, then every table, then one note claiming the file has
no page boundaries. Counted on the 53 acceptance-corpus .docx (counter validated on a known file):

| construct | files | occurrences | today |
|---|---|---|---|
| header/footer parts with text | 52 | 202 | dropped |
| rendered page breaks | 43 | 614 | note says there are none |
| tables | 28 | 61 | moved to the end |
| text boxes | 8 | 104 (each stored twice) | dropped |
| content controls | 3 | 8 | dropped |
| tracked changes | 0 | 0 | both sides dropped |
| footnotes, endnotes, comments | 0 | 0 | dropped (2 of 7 files beside the corpus have them) |
| embedded objects, all stored inside the file (`Type="Embed"`) | 49 | 289 | neither read nor disclosed |
| ... of which Excel workbooks (`Excel.Sheet.12`, macro-enabled) | 37 + 5 | 132 + 6 | |
| ... PDFs (four Acrobat ProgIDs) | up to 42 | 86 | |
| ... Word documents (`Word.Document.12`) | 44 | 48 | |
| ... `Package` (any file) / PowerPoint | 7 / 4 | 13 / 4 | |
| body pictures (DrawingML) | 52 | 363 | dropped; mostly logos |
| body pictures covering >= 25% of the page | 4 | 39 | dropped |
| pictures in headers/footers | 52 | 104 | dropped; letterhead logos |
| VML pictures outside an embedded object | 0 | 0 | (all 289 VML pictures are object previews) |
| hyperlinks | 3 | 51 | display text kept, target dropped |
| horizontally merged cells / vertical-merge continuations | 3 / 5 | 107 / 76 | text copied per spanned cell |
| tables nested in a cell | 1 | 1 | dropped |
| charts, SmartArt, altChunk | 0 | 0 | dropped |

Counts from `count_docx_constructs_2.py` and `count_docx_ole.py`, each validated on a file with
known answers before the corpus was opened. Embedded parts on disk: `.xlsx` 132, `.bin` 99 (the
PDFs and packages, OLE-wrapped), `.docx` 48, `.xlsm` 6, `.ppt` 2, `.pptx` 2.

**Open for Alex: what to do with embedded documents.** Recovering them as child documents, the
way email attachments are, changes document counts, the index and run identity; disclosing them
does not. Part 9 builds the disclosure either way.

## Parts

1. **Body walk in document order.** Children of `w:body`: `w:p` paragraph; `w:tbl` table;
   `w:sdt` recurse into `w:sdtContent`; `w:customXml` recurse; `w:altChunk` unread, disclosed;
   `w:sectPr` ignored. The same block walker serves table cells, text boxes, headers, footers,
   notes and comments.
2. **Paragraph text from the XML, not `Paragraph.text`.** All `w:t` at any depth, so runs inside
   `w:ins`, `w:hyperlink`, `w:smartTag`, inline `w:sdt`, `w:fldSimple` and `w:customXml` count.
   `w:tab` gives a tab, `w:br`/`w:cr` a newline, `w:noBreakHyphen` a hyphen. Skipped by name:
   `w:instrText`; anything under `w:txbxContent` (part 3); `mc:Fallback` when its
   `mc:AlternateContent` has an `mc:Choice`; deleted text per part 8. An external
   `w:hyperlink` target (resolved through the part's relationships) follows its display text
   in angle brackets when the two differ, so a citation keeps its referent.
3. **Text boxes.** Each `w:txbxContent` is walked as block content and emitted directly after the
   paragraph that anchors it. The Word 2010+ box stored as Choice + Fallback is read once.
4. **Tables from `w:tc`, not `row.cells`.** A cell with `w:gridSpan` is emitted once. A
   `w:vMerge` continuation cell (no `w:val="restart"`) is emitted empty, never as a copy.
   Nested tables in a cell are walked in order with the cell's paragraphs. Row = cells joined by tab.
5. **Headers and footers.** Per section in order: default always; first-page when `w:titlePg`;
   even-page when settings `w:evenAndOddHeaders`. Each part emitted once (dedupe by part name).
   Order within the header block: first-page, default, even; footers in the same order.
   Header text goes at the START of the page text, footer text at the END, with NO label lines:
   the Bates head zone is 3 lines and a label could push a stamp out of it. A footer is the
   document's own layer, so reading it for stamps is inside D-49. The probe
   `probe_bates_footer.py` shows a stamp is detected once it is in page text.
6. **Footnotes, endnotes, comments.** Real notes only (`w:type` absent or `normal`). Appended
   after the body and before the footer text: `[footnote N]`, `[endnote N]`,
   `[comment by AUTHOR]`. Comment DATES are not rendered: `_dated` takes a page's first date.
7. **The page note says something true.** Replace "DOCX carries no page boundaries" with wording
   that holds for every file (layout not reproduced; one synthetic page). Not an evidence marker.
8. **Tracked changes.** Build the final view (insertions kept, deletions and move-from omitted)
   and disclose omitted deleted text with a marker and a count. Whether deleted text should ALSO
   be emitted is Alex's call. Zero corpus exposure, so ask at the end of the package, not before.
9. **Unread parts disclosed.** New FINAL marker for Word content not read: altChunk, embedded
   OLE objects, charts, SmartArt, and images per the policy fixed after the count. Each note
   names the construct and its count.
10. **The two unmarked OCR notes in `_extract_pdf`** (OCR disabled, OCR unavailable) get
    markers, closing the gap recorded under D-49.

## Fixture and tests

* `16_word_constructs.docx`, built in `make_fixtures.py` (python-docx plus injected OOXML, as
  scratchpad `build_docx.py` does), invented text with one sentinel word per construct, pinned
  with `_pin_ooxml`. Selftest `_EXPECTED` gains `(1, {PageKind.SYNTHETIC})`. Never corpus text (D-12).
* One content test per part, each WATCHED RED on today's extractor by full node id before its
  part lands: order, each construct's sentinel, text box read once, merged cells not duplicated,
  footer stamp in the tail zone, header in the head zone, deleted text disclosed, true page note,
  both OCR notes marked.
* The derived class test above. `test_docx_is_one_synthetic_page_with_the_approximation_disclosed`
  asserts the old claim and changes with part 7.

## Corpus before/after (counts only, D-12)

Over the 53 .docx: characters extracted; documents whose first date changes; Bates candidates
found; notes with a marker. A changed first date or a new Bates candidate is a finding to
explain before commit, not a number to report.

## Delegation

Counter extension for the pending row: Haiku, validated on a known-answer file first.
Fixture and red tests: Sonnet, worktree, spec above. Implementation: Sonnet, same worktree,
gates in order. Adversarial review of the diff: Opus, hunting regressions in Bates and dates.
Gates, full suite, selftest, register and commit: main session.
