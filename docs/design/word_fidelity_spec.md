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

## Addendum, 2026-09-14: the reading rules as built (review-fix rounds 1 and 2, D-56)

This addendum does not rewrite the parts above; where it differs from them, it wins. Review round 2
found this spec behind its own code: the fix round had added reading rules it never recorded, so the
headline rule was false against the package's own test input. Everything below is held by the tests
named in the note table at the end, in `tests/test_word_fidelity.py` and `tests/test_word_embeddings.py`.

### A1. The rule the package makes true, restated

Every character in the main document part, and in every header, footer, footnotes, endnotes and
comments part the main part's relationships name, reaches the page text, the deletion list (A4), or
a note carrying an evidence marker. Excluded by name, with the reason:

* field codes: `w:instrText`, `w:delInstrText`, and any text between a complex field's `begin` and
  `separate` (a field nested in that code is code too, its cached result included); `w:fldSimple/@instr`;
* property containers (`w:pPr`, `w:rPr`, `w:sdtPr`, `w:sdtEndPr`, `w:trPr`, `w:tcPr`): a tab-stop list
  is not a tab and a deleted paragraph mark is not deleted text;
* `mc:Fallback` when an understood `mc:Choice` is taken (A2), and every Choice that is not taken;
* metadata the page does not show: `wp:docPr/@descr` and `@title` (alt text), `w:hyperlink/@w:tooltip`,
  `w:bookmarkStart/@w:name`, `w:ffData` names, help and status text, a text form field's
  `w:default`, drop-down entries not selected, `w:sdtPr` alias, tag and list items (the control's
  displayed value is its `w:sdtContent`), comment dates (`w:date`, part 6), core and custom document
  properties;
* the glossary document (building blocks): not the document's content;
* page numbers and date blocks Word computes at display (`w:pgNum`, `w:dayLong` and the like) store
  no text;
* a part no relationship chain from the main part reaches: Word neither shows nor keeps it; not
  read, and counted in a plain note when it holds text.

The derived class test (`test_every_w_t_fragment_reaches_the_page_text_or_is_excluded_by_name`) now
finds the parts through the relationships and checks `w:delText` and `v:textpath/@string` as well as
`w:t`. Auto-numbered list labels (`w:numPr`, rendered from the numbering part) are not rendered and
not disclosed: they are computed, not stored, text; that gap is recorded, not closed, here.

### A2. Reading rules added by the two fix rounds

1. **Parts by relationship, never by name.** The main part is the one `_rels/.rels` names; settings,
   footnotes, endnotes, comments, headers and footers are the parts the main part's relationships of
   those types name, whatever they are called (`footnotes2.xml` is as valid as `footnotes.xml`). Part
   names compare as OPC compares them: case-insensitively, and a target's percent-escapes are read
   either way. A target resolves against the folder of the part naming it; a leading `/` is
   package-absolute. The embedded-object reader scans the same related parts (comments included) in
   part-name order, then sweeps every `oleObject` or `package` relationship target and every part in
   the main part's `embeddings/` folder that no object referenced.
2. **Markup compatibility.** `mc:AlternateContent` resolves to the first `mc:Choice` whose `Requires`
   namespaces the reader understands (WordprocessingML and its 2010 and 2012 extensions, the 2010
   shape, group, canvas and drawing namespaces, `w16se`, the 2010 DrawingML extension and every chartex
   namespace), else to `mc:Fallback`. A Choice with no `Requires` is taken. Rows of a table are found
   through AlternateContent as through `w:sdt` and `w:customXml`.
3. **Fields.** A field's state is kept across the paragraphs of one story, so code that runs on past a
   paragraph mark stays code. A story that ends with a field's code still open is counted in a marked
   note.
4. **Symbols.** `w:sym` and `w16se:symEx`: in the Symbol font a code maps through the Adobe Symbol
   encoding (held equal to reportlab's `symbol` codec by a test; Adobe's private-use code points for the
   serif and sans-serif (c), (R), TM and the bracket, brace and arrow pieces become the characters they
   draw). In Wingdings, Webdings and the other symbol-encoded fonts, and for a private-use, surrogate or
   malformed code in any other font, the character keeps its place as U+FFFD and a marked note counts
   them and names the fonts. Any other code is the Unicode character. No Wingdings table ships with any
   dependency, so none is guessed.
5. **Text kept outside `w:t`.** A VML `v:textpath/@string` (a watermark, WordArt) is a line of its own
   directly after the paragraph that anchors it, as a text box is (part 3). A legacy check box shows
   U+2612 or U+2610 from `w:checked`, else `w:default`; a drop-down shows its `w:result` entry, else its
   `w:default`, else the first. Ruby text follows its base in parentheses; `w:ptab` is a tab;
   `w:noBreakHyphen` a hyphen.
6. **Headers and footers no section displays** (part 5): a part referenced only under a type the
   section does not show, and a part the main part relates as a header or footer that no section
   references, are counted in one marked note when they hold content. Whether a part holds content is
   decided by reading it with the same walker (text, a watermark, a deletion, a symbol, or a construct a
   note would count), and a part that will not parse counts.
7. **Pictures** (part 9) are measured at the size they are drawn: inside a group, a child's `a:ext`
   times each enclosing group's `ext / chExt`. The page area is the final section's. A picture covering
   at least 25% of it is disclosed; one with no usable extent, or on a page with no usable size, is
   disclosed as unmeasured. `w:subDoc` is disclosed.

### A3. A Word package under any name

A Word, Excel or PowerPoint package is read as the document it is wherever a `.zip` name would
otherwise have it unpacked as an archive: at the top level, inside an archive, attached to an email or
`.msg`, and held in a Package object. A PDF, zip or compound file named `.txt`, `.md`, `.log`, `.csv`,
`.eml` or `.email` is tried by content first, since those readers never fail. A second archive with a
name already used in one container is listed under a numbered name with a plain note.

### A4. D-56: tracked deletions

* **Order of the page.** Header parts, the body, footnotes, endnotes, comments, the deletion list, then
  footer parts. The footer still ends the page.
* **Form.** One line per deleted passage: `[deleted by <author>] <text>`, no dates. Line breaks inside
  a passage are spaces, so the label stands on every line a passage occupies. An author missing from
  the file is empty.
* **Order of the list.** Page-text order: the header parts' deletions, the body's (a table cell's in
  place, a text box's after its anchoring paragraph's own), the notes' and comments', the footer parts'.
* **Grouping.** Consecutive deleted characters by one author, in one paragraph, form one passage.
  Formatting that splits a deletion into several `w:del` runs does not split the passage, and neither
  does markup holding no character (bookmarks, properties). A kept character, another author, or the
  paragraph's end starts the next passage.
* **Moved text.** Moved-from text whose move names a `w:moveToRangeStart` of the same name somewhere
  the reader read is not listed: its words stand in the body where they were moved to, and listing them
  again would put every date and number in them into the output twice; a plain note counts such moves.
  Moved-from text with no such destination reads, with changes accepted, as deleted, and is listed.
* **Nesting.** A deletion inside an insertion, and an insertion inside a deletion, are deleted text by
  the innermost deletion's author.
* **Fields.** A deleted field result is listed; its code is not.
* **Paragraph marks, rows, cells.** A deleted paragraph mark holds no character: nothing is listed and
  the two paragraphs keep their own lines. A deleted row (`w:trPr/w:del`) or cell (`w:tcPr/w:cellDel`)
  leaves the body and its text is listed once.
* **Everywhere.** Headers, footers, footnotes, endnotes, comments, tables and text boxes, the same way.
* **Graphics.** A deleted picture, chart or object has no text to list; it is counted, once per
  outermost graphic, under `M_WORD_TRACKED_DELETION`, and any text box inside it is listed.
* **The count note.** A listed deletion carries no note: it is in the output, not missing, and
  accounting does not count it as lost. `M_WORD_TRACKED_DELETION` remains only for deleted graphics.
* **Dates and Bates.** A date in the list is found by date detection but, placed after the body, is
  never the first date while the header, body or notes carry one. No line of the list is ever a Bates
  zone line: `BatesZone.slice_lines` skips every line starting `[deleted by ` and chooses the head and
  tail zones from the others, so a Word page's zone is the one it would have without its list, and a
  stamp-shaped number in deleted text can neither become a locator nor make the real one ambiguous.

### A5. Stored names of container children

`walker._child_names`: NFC and `\` as a separator (not noted); empty, `.` and `..` parts removed; trailing
dots and spaces removed; each `:` becomes `_`; a part whose text before its first dot is a Windows device
name (CON, PRN, AUX, NUL, COM0-9, LPT0-9, COM and LPT with superscript 1-3) gets `_` in front. A changed
folder is one folder for all its members, and yields to a stored folder already holding its new name
(`_Aux__2/`); repeated whole names are numbered after the first; a name with nothing left is
`unnamed_child_<order+1>`. Whenever the recorded name differs from the stored one other than by NFC or
separators, the child's first note quotes the stored name exactly (not scrubbed) and gives the reasons. An
Ole10Native stored path is cut to its basename in the embedded-object reader first; its folders are the
author's own path, which the message scrubber would remove from any note.

### A6. Note table: every note the package emits

`[M]` marks an evidence marker (`FINAL` unless shown `TRANSIENT`). Each line names the test that fails
when the note is deleted, or, where marked, unmarked (the mutation run of 2026-09-14 on a copy of the
tree: every one killed).

| Code path | Note | Marker | Test |
|---|---|---|---|
| `_extract_docx` | Word's page layout is not reproduced ... one synthetic page | plain | fidelity `test_the_true_page_note_is_on_every_word_record_and_carries_no_marker` |
| `disclosure_notes` | N altChunk(s) | [M] WORD_UNREAD | fidelity `test_altchunk_and_chart_are_disclosed_as_unread` |
| `disclosure_notes` | N chart(s) | [M] WORD_UNREAD | fidelity `test_altchunk_and_chart_are_disclosed_as_unread` |
| `disclosure_notes` | N SmartArt drawing(s) | [M] WORD_UNREAD | fidelity `test_unread_constructs_in_footer_endnote_and_comment` |
| `disclosure_notes` | N picture(s), each covering at least 25% | [M] WORD_UNREAD | fidelity `test_a_large_picture_is_disclosed_and_a_small_one_is_not` |
| `disclosure_notes` | N picture(s) could not be measured | [M] IMAGE_UNREAD | fidelity `test_an_unmeasurable_picture_is_disclosed_as_unmeasured` |
| `disclosure_notes` | N symbol character(s) with no standard text mapping (font(s): ...) | [M] WORD_UNREAD | fidelity `test_an_unmapped_symbol_keeps_its_place_and_is_disclosed` |
| `disclosure_notes` | N sub-document link(s) | [M] WORD_UNREAD | fidelity `test_ruby_nested_field_codes_block_alternate_content_and_subdocuments` |
| `disclosure_notes` | N header/footer part(s) that no section displays | [M] WORD_UNREAD | fidelity `test_a_header_part_no_section_displays_is_disclosed`, `test_header_and_footer_parts_no_section_references_are_disclosed` |
| `disclosure_notes` | N field code(s) never ended | [M] WORD_UNREAD | fidelity `test_a_field_code_running_on_past_a_paragraph_mark_stays_code` |
| `disclosure_notes` | N deleted drawing(s), picture(s) or object(s) | [M] WORD_TRACKED_DELETION | fidelity `test_a_deleted_chart_is_counted_as_a_deletion_not_as_an_unread_chart` |
| `disclosure_notes` | N passage(s) moved under tracked changes are shown only where they were moved to | plain | fidelity `test_moved_text_is_listed_only_when_its_destination_is_not_in_the_document` |
| `disclosure_notes` | N Word part(s) holding text that no relationship ... reaches were not read | plain | fidelity `test_notes_comments_and_settings_are_found_through_the_main_parts_relationships` |
| `_extract_pdf` | OCR disabled / OCR is unavailable (part 10) | [M] | fidelity `test_ocr_disabled_note_carries_an_evidence_marker`, `test_ocr_unavailable_note_carries_an_evidence_marker` |
| `expand_docx_embeddings` | embedded objects truncated at N members | [M] ATTACH_SKIPPED | embeddings `test_embeddings_member_cap_is_disclosed` |
| `expand_docx_embeddings` | embedded objects truncated at N MB | [M] ATTACH_SKIPPED | embeddings `test_embeddings_byte_cap_is_disclosed` |
| `expand_docx_embeddings` | '<part>' could not be parsed | [M] ATTACH_SKIPPED | embeddings `test_a_malformed_part_loses_only_its_own_objects` |
| `expand_docx_embeddings` | the relationships of '<part>' could not be parsed | [M] ATTACH_SKIPPED | embeddings `test_unparsable_relationships_of_a_related_part_are_named_in_a_marked_note` |
| `expand_docx_embeddings` | an embedded object ... is a link | [M] ATTACH_SKIPPED | embeddings `test_link_type_object_is_not_recovered` |
| `expand_docx_embeddings` | ... has no relationship id | [M] ATTACH_SKIPPED | embeddings `test_missing_relationship_id_is_not_recovered` |
| `expand_docx_embeddings` | ... names relationship id '...', which does not exist | [M] ATTACH_SKIPPED | embeddings `test_an_object_naming_a_relationship_id_that_does_not_exist_is_named` |
| `expand_docx_embeddings` | ... links to an external target | [M] ATTACH_SKIPPED | embeddings `test_external_target_is_not_recovered` |
| `expand_docx_embeddings` | ... names part '...', which is not in the package | [M] ATTACH_SKIPPED | embeddings `test_relationship_to_missing_part_is_not_recovered` |
| `expand_docx_embeddings` | archive member unreadable: '<part>' | [M] TRANSIENT ZIP_MEMBER | embeddings `test_an_embedded_part_that_cannot_be_decompressed_is_named_for_a_retry` |
| `_unwrap_embedding` | '<part>' is neither a ZIP nor a compound file | [M] ATTACH_SKIPPED | embeddings `test_object5_is_neither_zip_nor_compound_file_marked_note_no_child` |
| `_unwrap_embedding` | ... 'olefile' is not installed | [M] ATTACH_SKIPPED | embeddings `test_a_compound_file_without_its_reader_installed_is_named` |
| `_unwrap_embedding` | ... carries a malformed Ole10Native stream | [M] ATTACH_SKIPPED | embeddings `test_malformed_ole10native_stream_is_a_marked_note_not_an_exception` |
| `_unwrap_embedding` | ... is a compound file that could not be read | [M] ATTACH_SKIPPED | embeddings `test_a_compound_file_that_cannot_be_opened_is_named` |
| `_unwrap_embedding` | ... holding no PDF, no Package object and no legacy Office document | [M] ATTACH_SKIPPED | embeddings `test_compound_files_are_recovered_by_what_they_hold` |
| `_add_embedded_member` | embedded archive '...' could not be read | [M] ATTACH_SKIPPED | embeddings `test_an_embedded_archive_that_cannot_be_read_is_named_in_a_marked_note` |
| `_add_embedded_member` | embedded archive '...': <the archive's own unmarked note> | [M] ATTACH_SKIPPED | embeddings `test_an_embedded_archives_own_unmarked_notes_are_marked` |
| `_unused_prefix` | <what> '...' has the name of one already listed; its members are listed under '...' | plain | embeddings `test_two_different_archives_with_one_name_are_filed_apart`, `test_two_inner_archives_with_one_name_in_one_archive_are_filed_apart` |
| `_flatten_attached_archives` | attachment '...' is a zip that could not be read | [M] TRANSIENT ZIP_ATTACH | embeddings `test_an_attached_archive_that_cannot_be_read_is_named` |
| `walker._count_notes` | N embedded document(s) / attachment(s) extracted as child document(s) | plain | embeddings `test_embedded_document_count_note_present_when_recovered` |
| `walker._count_notes` | N ... inventoried as child document(s) only, in a format DocIQ does not read | plain | embeddings `test_compound_files_are_recovered_by_what_they_hold`, `test_attachment_counts_say_how_many_were_read_and_how_many_only_inventoried` |
| `walker._child_records` | nesting deeper than N container(s) was not expanded (...) | [M] ATTACH_SKIPPED | embeddings `test_nesting_chain_past_zip_max_depth_yields_a_marked_note` |
| `walker._child_records` | inside a container at the nesting limit: <note> | [M] ATTACH_SKIPPED | embeddings `test_at_the_nesting_limit_a_containers_unmarked_notes_are_marked` |
| `walker._expansion_failure_note` | the documents embedded in this Word file could not be listed | [M] TRANSIENT ATTACH_ENUM | embeddings `test_walker_marks_the_word_record_when_expansion_raises` |
| `walker._child_names` | stored name '...' is recorded as '...': <reasons> | plain, unscrubbed | embeddings `test_a_changed_stored_name_loses_as_little_as_possible_and_is_quoted_exactly` |
| `extract` (content sniff) | extension .X but content is ...; recovered via ... extractor | plain | embeddings `test_a_word_package_under_a_text_name_is_read_as_word` |

`sanitize_message` now also removes an object repr's memory address and a `tempfile` name
(`test_sanitize_message_removes_addresses_and_temporary_names`,
`test_a_rejected_word_main_part_hashes_identically_in_two_interpreters`).
