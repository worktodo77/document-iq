# Sprint 5 merge notes (d51 -> excel -> word into build/sprint-5)

Known union conflicts (trial merge 2026-09-14): marker constants, FINAL_MARKERS, selftest `_EXPECTED`, test_walker tail.
Guard `tests/test_no_shadowed_definitions.py` must pass (Word `_docx_local` vs Excel `_local`).

## A-25 seam (from Word fix round 4, 85efb95, D-59)

1. `docs/contracts/amendments.md`: keep d51's A-25 section; drop word's copy of the `## A-25 —` heading and its
   status paragraph; append word's `### Also under A-25: the Word deletion list's lines (D-59, ...)` subsection to
   the end of d51's A-25.
2. `docs/contracts/amendments.toml`: keep d51's `[amendment.A-25]`, drop word's fragment, then:
   * existing `wired_in` keys add: contracts.py `PageRecord`, `TRACKED_DELETION_LABEL`; extract.py `_extract_docx`,
     `_deletion_list_span`; walker.py `_page_from_jsonable`; tools/bates_acceptance.py `zone_only`.
   * new keys: pagemodel.py `make_page`; bates.py `BatesZone`, `page_lines`, `slice_lines`, `detect_candidates`,
     `apply_bates_reported`.
   * `tests` add `tests/test_bates_deletion_span.py`, `tests/test_walker.py`, `tests/test_word_fidelity.py`.
   * `raised_by` append "and 2026-09-23 (D-59)".
3. `src/dociq/contracts.py` docstring: both branches add an "Also under 2.3.0, amendment A-25" paragraph after
   D-49's; keep both, d51's first.
4. CONTRACT_VERSION stays 2.3.0; flip A-25 in its own commit after the merge.

## Other merge-time TODOs (from memory note)

* Extraction-revision guard: a resume journal from an earlier build must be refused (journal keys on contract +
  config only). Note: Word fix 4 made `deletion_line_span` a required journal key, so pre-D-59 journals already
  raise KeyError -> discarded; the general guard is still owed.
* Ask Alex the migration question (earlier builds' runs don't reproduce; old journals refused; rec accept).
* Register status lines: D-50, D-51, D-49 "two unmarked notes", D-58, D-59.
* Full suite, selftest, determinism.prove(concurrency=N); push.
* Word: selftest.py comment still cites "D-50" for fixture 16. `[sheet: ...]` label newline sibling (Excel branch).
