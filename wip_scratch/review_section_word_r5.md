## Package: Word fidelity, review ROUND 5 under D-57, `<package>` = `word_r5`

* Worktree `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad\wt-word`,
  branch `build/sprint-5-word`, clean. Round 4 reviewed `85efb95`; fix round 5 is **`9d05238`**. Review
  `git diff 85efb95 9d05238`, then the whole package `git diff ea4f1cb 9d05238` with fresh eyes.
* **D-57: the package alternates fix and review until a review confirms no A or B finding.** Rounds: 2A+5B, 6B, 4B,
  1B. Your verdict decides whether it closes. Rate honestly both ways: do not inflate a C to keep the loop going,
  and do not deflate a real silent loss to end it.
* Earlier rounds under `...\scratchpad\recovered\review_word*\`. Fix brief `word_brief_fix5.md`; builder evidence
  `...\recovered\word_fix5\` (red run, 195 mutants, corpus measure, font census).
* Spec `docs/design/word_fidelity_spec.md` (§A1-§A6). Rulings (read-only register, `build/sprint-5` @ `46d36f6`):
  D-49, D-50, D-56, D-57, D-59.

### What round 5 must establish

1. Round 4's B (zone bounds with the deletion list) and all eight C items closed: re-run round 4's probes and mutants
   (R4, R4M1-R4M8) against `9d05238`.
2. `_deletion_list_line` normalization by construction: can any deleted text the reader emits still violate the
   span precondition (FAIL with pages=0)? Fuzz it.
3. The two changes beyond the brief: a Word package under a `.pdf` name read by content first (does anything
   legitimately a PDF now get misread? a PDF with an embedded zip?); NEL/LS/PS as spaces in deleted text.
4. `quoted(value, limit)` and every note carrying a file value; marker phrases in exception text (spec A5's claim that
   a phrase can only move a note between retryable and final).
5. Symbol-font family matching (affixes MT/ITC/Regular, `w:charset="02"`): false positives on ordinary fonts?
6. The package-extras note under foreign names; nested-graphic deletion counts; the hidden-header child note.
7. Mutants: re-run 10 of the builder's 195 on a fresh copy and try 5 of your own on the newest code.

### Not findings

Auto-numbered list labels (`w:numPr`), a recorded known gap. The `[sheet: ...]` newline sibling (spreadsheet
branch). The A-25 merge seam and stale-journal replay in general (main session, at merge). The accepted D-56
limitation (a deleted date as first date of a footer-only-dated document), unless its recording is untrue.
