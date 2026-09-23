## Package: Word fidelity, review ROUND 4 under D-57, `<package>` = `word_r4`

* Worktree `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad\wt-word`,
  branch `build/sprint-5-word`, clean. Round 3 reviewed `2671947`; fix round 3 is `347df32`, fix round 4 (D-59) is
  **`85efb95`**. Review `git diff 2671947 85efb95`, then the whole package `git diff ea4f1cb 85efb95`.
* **D-57 (Alex): the package alternates fix and review until a review confirms no A or B finding.** Round 1: 2A+5B;
  round 2: 6B; round 3: 4B. Your verdict decides whether it continues. Rate honestly both ways.
* Earlier rounds: `...\scratchpad\recovered\review_word\`, `review_word_r2\`, `review_word_r3\` (findings.md,
  payload.json, probes). Fix briefs `word_brief_fix3.md`, `word_brief_fix3_resume.md`, `word_brief_fix4.md`;
  builder evidence `...\recovered\word_fix3\`, `word_fix4\` (red runs, 163 mutants, corpus measure). Note: fix round 3's
  uncommitted draft was lost to a Temp wipe and replayed exactly from its transcript before being finished; treat
  the whole `2671947..347df32` diff as ordinary new code.
* Spec `docs/design/word_fidelity_spec.md` (§A1-§A6; §A4 is D-56's design).
* Rulings in `C:\Users\Alex\document-iq\docs\decisions\decision_register.md` (read-only, `build/sprint-5` @ `14b3ebe`):
  D-49, D-50, D-56, D-57, **D-59** (the deletion list's lines are a `PageRecord` span under A-25; the zone skips by
  position, never by text).

### What round 4 must establish

1. Every round-3 finding and C item closed: re-run each round-3 probe against `85efb95`. The one C item left open by
   design (a deleted date can become the first date of a document dated only in its footer, recorded in §A4) is not
   a finding unless the recording is untrue.
2. **D-59 as built:** `PageRecord.deletion_line_span` (SYNTHETIC only, bounds, every named line must start with the
   label), `make_page`, the Word reader as sole setter, `BatesZone.page_lines`/`slice_lines`, every zone consumer,
   `zone_has_candidate` before a page record exists, the journal (`_page_from_jsonable` requires the key: an older
   journal is discarded, is that what happens end to end, with what message?), `tools/bates_acceptance.zone_only`,
   and hashing (every page's identity moves once). Can any input make the span name a non-list line, or a list line
   escape it? Does anything still decide from the `[deleted by ` text?
3. **Symbol fonts in runs:** resolution through run, character/paragraph/table styles with `basedOn`, document
   defaults and theme; `w:rFonts` hint and `w:cs`/`w:eastAsia`; the Zapf Dingbats table vs reportlab; placeholders
   and the note reaching accounting; do placeholders break `_dated`, Bates or tokens? Corpus: 496 placeholders in 49
   documents.
4. **Deleted embedded objects and quoted values:** the child's deletion note, the parent's per-shape note, `extract.quoted`
   everywhere a file value enters a note or line prefix (enumerate them), and whether any marker test can still read
   inside a quoted value. `.eml`/`.msg` header one-lining (RFC 2047 encoded line breaks).
5. Everything else rounds 3-4 added: Office-package-rooted `.zip` naming other members; `CONTENT_UNREAD` for a
   binary under a text name; block-level run content and orphan parts by root element; `sanitize_message`'s
   narrowed stability claim.
6. Mutants: re-run 10 of the builder's 163 on a fresh copy and try 5 of your own.

### Not findings

Auto-numbered list labels (`w:numPr`) not rendered (a recorded known gap; say only whether it is disclosed).
The `[sheet: ...]` label newline sibling (spreadsheet branch replaces that reader). A-25's merge seam with
`build/sprint-5-d51` (main session, at merge). Stale-journal replay across builds in general (main session).
