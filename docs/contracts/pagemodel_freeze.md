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

Byte-identical across runs with the same folder + profile + master index:

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
