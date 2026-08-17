# Sprint-1 Contract Freeze — `src/dociq/contracts.py`

**Frozen:** 2026-07-30, day one of Sprint 1, per the D-10 contract-first rule.
**Contract version:** 1.0.0
**Applies to:** Track A (ingestion spine), Track B (identity & deliverables),
Track C (GUI shell) — all three build against this module in separate
worktrees. Only Track A implements it for real.

## Why this exists

D-10 runs three tracks concurrently. Without a frozen interface the tracks
diverge silently and integration becomes a rewrite. This document is the
contract's prose half: the code says *what* the types are, this says *what they
mean* and *what you may not do to them*.

## The stop-the-line rule

**Any change to a type in `contracts.py` after this freeze is a cross-track
stop-the-line event, not a local edit.** A track that discovers the contract is
wrong stops, reports, and waits — it does not work around it locally, and it
does not "just add a field".

### Amendment procedure

1. The discovering track writes the problem to `docs/contracts/amendments.md`:
   the concrete case the contract cannot express, and why a local workaround
   would be wrong.
2. All three tracks pause work that touches the affected type.
3. The amendment is applied once, centrally, on the sprint branch.
4. `CONTRACT_VERSION` is bumped: patch for a docstring, **minor** for an
   additive field with a safe default, **major** for anything else.
5. Tracks rebase and resume.

Additive-with-safe-default changes are the only ones expected to be cheap.
Renaming an enum value is a **major** change even though it looks cosmetic:
enum values reach disk, so a rename changes output bytes and breaks the
byte-identical claim against prior runs.

## Normalization — binding on every extractor

Every `PageRecord.text` reaching the contract has already been normalized.
Track A owns this; Tracks B and C may assume it.

| rule | value |
|---|---|
| Unicode form | NFC |
| Line endings | LF only (`\r\n` and `\r` converted) |
| Trailing whitespace | stripped per line |
| Leading/trailing blank lines | stripped from the page |
| Interior blank lines | collapsed to at most two consecutive |
| Non-breaking space `U+00A0` | converted to `U+0020` |
| Zero-width chars (`U+200B`–`U+200D`, `U+FEFF`) | removed |
| Tabs | preserved (they carry table structure) |

Normalization is idempotent — applying it twice must equal applying it once,
and there is a test that proves it. It runs **before** the record is built, so
`content_hash` is computed over normalized text and reruns cannot drift on
encoding alone.

**Page markers are never in `text`.** `===== PAGE n [BATES: …] =====` is
rendered by `emit/cleantext.py` and nowhere else. An extractor that embeds a
marker in page text has broken the contract: the marker would then be hashed as
content, and Stage 4 could drop a page whose marker text still implied it was
present.

## What each track may and may not do

### Track A — the only implementer

- Produces `PageRecord` / `DocumentRecord` / `RunResult`.
- Owns normalization, page numbering, `PageKind`, and OCR confidence capture.
- Calls `.validate()` at every stage boundary — cheap, and it localizes an
  accounting break to the stage that caused it.

### Track B — consumer + enricher

- Consumes documents; enriches via `PageRecord.evolve()` / `dataclasses.replace`.
  Never mutates (the dataclasses are frozen, so an attempt raises).
- Owns `doc_id`, `li_file_no`, `bates`, `section`, `disposition`, `drop_rule`.
- Builds against `tests/fixtures/` stub records, not against Track A's code.
- **May not** widen a type or add a field to carry its own state — use its own
  side structures keyed by `doc_id`.

### Track C — display only

- Consumes `RunResult` through a mocked pipeline API returning contract objects.
- **May not** import anything from `ingest/`, `identify/`, `docid/`, `profiles/`,
  `emit/`, or `verify/`. The GUI orchestrates and displays; it holds no pipeline
  logic. Enforced by a test that asserts the import graph.
- **May not** compute page accounting itself — read the derived properties.

## `parent_doc_id` — the convention, settled at integration (2026-07-31)

The contract types `parent_doc_id` as a Doc ID, but Doc IDs do not exist until
Stage 3b. Both tracks flagged the gap (Track A's verification record §5, Track
B's integration note §1). It is settled as follows, and the settlement is a
handover rule rather than a contract change — the field is a string and holds
one either way:

| stage | what `parent_doc_id` holds |
|---|---|
| after Stage 1 (`ingest/walker`) | the **parent's `rel_path`** |
| after Stage 3b (`docid/assign`) | the **parent's assigned Doc ID** |

Stage 3b performs the swap, at the same moment it mints the child's own ID —
the first moment both halves exist. Consumers therefore see a Doc ID, which is
what the field name promises and what the index's "Parent doc" column ships.

Two consequences, both of which were defects until integration closed them:

- `docid/assign` still resolves **both** conventions. That branch is not dead
  code: after the remap, a corpus fed through Stage 3b a second time (a resumed
  run, a re-assignment against a newer index) names its parent the other way,
  and dropping the branch would orphan every container member on the second
  pass.
- `verify/accounting`'s orphan-child check accepts both forms, because the gate
  runs on both sides of the boundary.

A container member whose parent is **not** in the run is identified as a
top-level file, and its `parent_doc_id` is cleared to `None` so the record says
the same thing the warning says. The provenance survives in the assignment note
and in the warning naming the parent that was not scanned.

## Invariants the whole pipeline rests on

1. **Page numbering is a gapless 1..N sequence in order**, per document,
   reflecting the ORIGINAL document (Principle 2). Empty pages are still
   pages — `PageKind.EMPTY`, never omitted. `DocumentRecord.validate()`
   enforces this, and it is the reason silent page loss cannot survive to emit.
2. **KEEP is the default and requires no justification; DROP requires a
   `drop_rule`.** Enforced in `PageRecord.validate()`. Principle 1 means an
   unattributable drop is a contract violation, not a warning.
3. **`pages_in == pages_kept + pages_dropped`**, per document and corpus-wide.
   The §4 Stage-6 gate.
4. **`ocr_conf` is reporting-only and never an identity input.** It is a float;
   Principle 5 forbids floats in identity fields. `to_jsonable(for_identity=True)`
   drops it, and *raises* on any other float it encounters — so a future float
   field cannot quietly enter the hash.
5. **One serializer for both hashing and persistence** (`to_jsonable`). Two
   serializers eventually disagree; there is only one.
6. **One document order** (`document_sort_key`): relative path, then SHA-256,
   then container order. Every emitter, the index, the log and the ID assigner
   use it.

## The byte-identical claim, stated precisely

Byte-identical across runs with the same **run identity** — folder, profile,
master index, and (contract v1.3.0, amendment A-04, from Codex review #1
finding B-2) `RunConfig.limits`: the XLSX/CSV/ZIP caps, ZIP depth, per-file
timeout, retry bounds, recursion flag and OCR model identity — the timeout and
retry budget in exact integer **milliseconds** since v1.6.0 (A-09), because
rounding two different float deadlines to the same whole second was a collision
inside the field that exists to prevent one.

"Profile" means the **ordered tuple of profile snapshots** since v1.6.0 (A-08,
from round-2 finding B-R2-2), each carrying `profile_id`, `version` and
`profile_hash`. Naming only the first profile's id and version was not naming
the input: Stage 4 claimed a document with the first profile whose header
patterns matched, so every profile's content and their precedence order decided
which pages dropped. *(Past tense since 2026-08-17: D-35 deleted that engine and
A-19 put the input that decides today — the omissions an expert approved — into
the same projection. The snapshots stay hashed; what changed is which input the
identity has to cover, not whether it covers it.)* Measured before the fix — editing a second profile's rule
without bumping its version, and separately swapping two profiles' order —
each moved the corpus hash and left the recorded identity byte-identical.

**Deliberately excluded**, each for a stated reason: thread-pool width (it must
not change output); the **output folder** (v1.6.0, A-08 — it is the destination,
not an input, and the acceptance harness proves one identity across two
destinations); and the run's **terminal status and reason** (v1.6.0, A-07,
reversing v1.5.0 — an incomplete run publishes no corpus and no manifest, so
termination cannot collide with a completed corpus identity; the previous
completed manifest survives instead).

There is exactly one projection, `dociq.contracts.run_identity()`, and its value
is persisted as `run_identity_sha256` in both `output_manifest.json` and the
processing log's hashed content, so "which hash is the run identity" has one
answer on disk. `output_manifest.json` states the full identity in its
`claim_identity` field:

- `clean_text/*.txt`
- `sources.json`
- `document_index.csv`
- the `content` section of `processing_log.json`

Explicitly **not** byte-identical, and excluded from the content hash:

- the `run` section of `processing_log.json` (timestamp, operator, host)
- `run_summary.pdf` (embeds a generation timestamp)
- `document_index.xlsx` (the format embeds a creation timestamp)

The hash manifest states this split explicitly rather than leaving the claim
ambiguous. A claim that is true only of some files must say which.

**Third category, added at integration (2026-07-31): `adjacent`.** The full
pipeline writes §7/§8 deliverables the freeze did not name — `reconciliation.csv`,
`doc_ids_issued.json`, the matter profile copy, and the Path-A
`upload_package/`. They *are* reproducible, so declaring them "excluded because
they cannot be byte-identical" would be false; folding them into the claim would
widen a frozen claim quietly. The manifest therefore hashes and compares them,
and keeps them out of `corpus_sha256`. A difference in an adjacent file is
reported as a finding, not ignored. The claim above is unchanged.

## Known hazards inherited from the vendored MIP 3.9 extractor

Found during the freeze read of `mip39/api/docs_extract.py`. All three are
Track A obligations, and all three are principle violations rather than
preferences:

1. **`_ocr_engine()` calls `enable_os_trust()` to permit a one-time OCR model
   download.** That is a network call, and Principle 4 admits none. The
   vendored copy must strip it and load models from a bundled path, failing
   loudly if they are absent. Track A proves this with the network disabled.
2. **`_ocr_array()` discards `line[2]`** — the per-line confidence, which is
   exactly what §4 Stage 2 requires. Capture per-line confidences, record the
   mean as `ocr_conf` and the sub-threshold count as `ocr_low_conf_lines`.
3. **`_CACHE_ON` writes an extract cache outside the matter folder.** §10
   forbids persistent temp files outside the working folder. Either relocate
   the cache under the output root or remove it.

A fourth, latent: the shared OCR thread pool means page results arrive out of
order. Track A must reassemble by page index, not by completion order, or the
determinism proof will fail intermittently — which is the worst way for it to
fail.
