# Track A — Sprint 1 verification record

**Date:** 2026-07-30 · **Branch:** `build/s1-track-a` · **Contract:** 1.0.0

What was proven, how, and what was NOT proven. Numbers here are reproducible
from the commands quoted; client-derived material is referenced by summary
number only and never committed.

## 1. The three inherited principle violations

The freeze doc (§Known hazards) named three, plus one latent. All four are
closed. Two further violations of the same principles were found during the
vendoring and are closed with them.

| # | violation | fix | evidence |
|---|---|---|---|
| 1 | `_ocr_engine()` called `enable_os_trust()` to permit a one-time OCR model download — a network call, Principle 4 | Removed. Models load from the installed `rapidocr_onnxruntime` package's `models/` directory, or `DOCIQ_OCR_MODEL_DIR`. Absence is a loud failure naming the missing file and the fix, never a fetch | `test_the_network_call_is_gone_from_the_vendored_module` (AST, not grep — the docstring names the removed call); `test_missing_models_fail_loudly_with_the_fix`; selftest runs a real OCR pass with `socket.socket` replaced by a raiser |
| 2 | `_ocr_array()` joined `line[1]` and discarded `line[2]`, the per-line confidence §4 Stage 2 requires | `ocr_lines()` returns box + text + confidence per line. Mean → `ocr_conf` (rounded 4dp at one place), sub-threshold count → `ocr_low_conf_lines`, count → `ocr_line_count` | `test_scanned_pdf_pages_are_ocr_with_confidence`; selftest asserts every OCR page carries a confidence and that it is 4dp-rounded; bake-off §3 quotes the measured distribution |
| 3 | `_CACHE_ON` wrote an extract cache to `~/.mip39/extract_cache` — outside the working folder, §10 | **Removed, not relocated.** See below | no cache code remains; §10 scratch check in `test_scratch_directory_does_not_survive_the_run` |
| 4 (latent) | the shared OCR thread pool returns pages out of completion order | Assembly is strictly `for i in range(n)` over an index-keyed dict. The contract's gapless-1..N check would catch a *missing* page but not a *permuted* one, so this is the primary defence | `tests/test_ocr_ordering.py` — 30 repeats, engine stubbed so later pages finish FIRST. **Fail-before established**: swapping assembly to `as_completed()` turned it red, then reverted |
| 5 (found here) | `_maybe_caption()` called a local vision model to describe photos | Removed. §12 puts AI processing out of scope; the contract's `PageKind.PHOTO` says "never AI-captioned in DocIQ". The deterministic EXIF `[PHOTO]` block is kept verbatim | no captioning code remains |
| 6 (found here) | `_extract_msg` wrote its scratch `.msg` to the system temp directory | The caller supplies `scratch_dir`; the walker points it at `<output_root>/.dociq/scratch`, which is removed at the end of the run | `test_scratch_directory_does_not_survive_the_run` |

### Why the cache was removed rather than relocated

Relocating it under the output root satisfies §10's letter and defeats the
determinism proof: runs 2..N of an identical-input repeat would replay cached
bytes rather than re-extract, so the proof would demonstrate only that a cache
is a cache. A content-hash-keyed cache is separately a correctness hazard — an
entry written by a different engine version replays old text under a new run's
identity. Nothing in Track A depends on it. Sprint 2 may reintroduce caching
inside the matter folder behind a flag the determinism harness disables.

## 2. Defects found and fixed in this package

Both were found by the sibling enumeration in §4, not by a failing test, and
both are determinism defects that the fixture corpus did not exercise.

**D-A1 — `output_root` was inside the log's hashed `content` section.**
Two runs of identical inputs into different output folders produced different
corpus hashes. (Amended 2026-07-31 by contract v1.3.0 / A-04, from Codex review
#1 finding B-2: the identity also covers `RunConfig.limits` — caps, timeouts,
retry bounds, recursion and the OCR model bytes.) The determinism contract is
"same folder + same profile + same
master index"; where the operator chose to put the deliverables is not an input
to what they contain. `output_root` moved to the log's `run` section.
*Found by:* the first 3-run determinism probe, which went red immediately.
*Class fix:* absolute paths reaching hashed content is a family, not an
instance — `sanitize_message()` reduces any absolute path in an error or note
string to its basename, applied at the single point every `DocumentRecord` is
built. A parser reporting `C:\…\Temp\tmp61yhcl7p\x.msg` would otherwise have
put a per-run random string inside the byte-identical claim.

**D-A2 — the error list was ordered by thread completion, and capped on
arrival.** Extraction errors become `RunResult.warnings`, which reach the log's
hashed `content`. Arrival order is a function of the scheduler. Worse, the
2,000-entry cap applied as records arrived, so a corpus with more than 2,000
failures would keep a *different* 2,000 on every run — a defect invisible below
that threshold and guaranteed above it. Errors are now sorted by (file, error)
and the cap is applied after sorting, with the omission disclosed.
*Fail-before:* reverting the sort turned
`test_warning_order_is_deterministic_under_concurrent_failures` red across pool
widths 1/2/4/8/16, and `test_error_cap_keeps_a_deterministic_slice…` red on the
cap. Both restored to green after.

**D-A4 — an empty output root produced a stable, passing corpus hash.**
`manifest.build()` on a directory holding none of the claimed artifacts
returned an empty manifest — which hashes perfectly stably, so two runs that
produced *nothing* compared byte-identical and the determinism gate went green
on a pipeline that did no work. Found while spot-checking an artifact against a
mistyped path. `build()` now raises `EmptyOutputError` unless
`require_outputs=False`, and `determinism.prove()` records such a run as a
failure rather than skipping it. *Fail-before:* the new test asserts the raise;
before the guard, `build()` returned `corpus_sha256 ddb0faadea310a47…` for a
directory that did not exist.

**D-A5 — PPTX extraction manufactured notes slides in the source document.**
python-pptx's `slide.notes_slide` property *creates* a notes slide when none
exists (documented side effect). The vendored code read it unguarded on every
slide, so a 30-slide deck with 3 real notes pages had 27 notes-slide parts
built that the file never contained. Nothing is written back, so no file was
corrupted — but a tool whose claim is "every output is mechanically derived
from the source" should not be constructing parts of the source. Guarded with
`has_notes_slide`. *Measured:* it is **not** a material performance problem —
the largest deck in the corpus (34.6 MB, 30 slides) parsed in 0.16 s unguarded
and 0.09 s guarded. The initial hypothesis that PPTX was the corpus bottleneck
was wrong, and is recorded as wrong.
*Fail-before:* the probe replaces `notes_slide` with a raiser derived from
`BaseException` — the extractor wraps the notes read in a broad
`except Exception`, so an `AssertionError` probe would have been swallowed and
could never have failed. Unguarding the read turns it red.

**D-A3 — error messages were truncated silently** (`[:200]`, `[:300]`,
`[:400]`). A bound that removes text without a mark is a silent cap.
`clip_message()` appends `[…truncated at N chars]`; every site now uses it.

## 3. Determinism

Harness: `dociq.verify.determinism.prove()`. Each repetition runs the whole
pipeline in a **subprocess** with a distinct `PYTHONHASHSEED` — set in-process
it would be read after interpreter start and the varied-seed claim would be
unfalsifiable. Comparison is over the manifest, i.e. exactly the four artifacts
the claim names.

- **8 runs, 8 distinct seeds** — in the selftest, every invocation. Result:
  1 distinct corpus hash, 0 diffs.
- **30 runs, 30 distinct `PYTHONHASHSEED` values** — result: **1 distinct
  corpus hash, 0 diffs, 0 failures**, `corpus_sha256`
  `b587bb6af8b3c97dfdbf43e6832c44fb4480d70e218b9fe5cc17c547e9cacc6e`.
- **rapidocr engine stability: 30 repeats on a real scanned MPR page → 1
  distinct result**, identical text and identical per-line confidences to 6
  decimal places (bake-off §3). This is the precondition for the claim over any
  corpus with scanned pages, and it is measured rather than assumed.
- **OCR page-assembly ordering: 30 repeats × 2 probes**, with the engine
  stubbed so completion order is the reverse of page order.

### What the claim covers, stated precisely

`clean_text/*.txt`, `sources.json`, `document_index.csv`, and the `content`
section of `processing_log.json`. Explicitly outside it, and recorded as such
in `output_manifest.json` with the reason: the log's `run` section,
`run_summary.pdf` (embeds a generation timestamp), `document_index.xlsx` (the
container embeds a creation time). Any output matching neither list is reported
as `unclassified` rather than assumed either way — an output nobody decided
about is a finding.

### Bounds on the proof — disclosed

- The repeat proof runs on the **synthetic corpus**, which does exercise the
  OCR path, mixed native/scanned routing, nested ZIP expansion, content-sniff
  recovery and a blank page. It is not the real corpus: eight repeats of the
  real 17,732-page corpus is not affordable in one sprint.
- The `clean_text` / `sources.json` / `document_index.csv` artifacts are
  produced by `verify/probe_emit.py`, a **provisional** stand-in for Track B's
  emit layer (see §5). The determinism of the *records* those files are derived
  from is proven directly; the determinism of Track B's real writers is Track
  B's to prove against the same harness.

## 4. Sibling enumerations

The standing rule is that a fix names every member of its class, including the
members that were already correct.

### Class A — network calls (Principle 4)

Grepped `src/dociq` for `urllib|requests|http|socket|ssl|certifi|urlopen|download|enable_os_trust`.

| site | status |
|---|---|
| `extract._ocr_engine` | **fixed** — `enable_os_trust()` removed |
| `extract.ocr_model_dir` / `ocr_models_present` | correct — filesystem only; the error text states DocIQ never downloads |
| `selftest._check_no_network` | correct — the only `socket` reference in the package, and it *blocks* sockets |
| everything else in `ingest/`, `verify/` | correct — no network-capable import anywhere |

### Class B — persistent state outside the working folder (§10)

| site | status |
|---|---|
| `docs_extract._CACHE_DIR` (`~/.mip39/extract_cache`) | **fixed** — not vendored |
| `extract._extract_msg` scratch file | **fixed** — `ExtractOptions.scratch_dir`, unlinked in `finally` |
| `walker` run state | correct by design — `<output_root>/.dociq/`, and the scratch subtree is removed at run end |
| `selftest`, `verify.determinism` default workdir | acceptable — developer harnesses, not the product run path; both take an explicit `workdir` |

### Class C — discarded engine result fields

| site | status |
|---|---|
| `_ocr_array` dropping `line[2]` (confidence) | **fixed** — captured |
| `_ocr_array` dropping `line[0]` (bounding box) | **fixed** — `ocr_lines()` returns it. §4 Stage 3 matches Bates against page corners and footers, which is a geometry question; without this Track B would have to stand up a second OCR engine to ask it. No contract field is involved and nothing reaches disk |
| `_photo_block` reading only the largest embedded image's EXIF | correct as inherited — deliberate, and disclosed in the block text |

### Class D — completion-order consumption

| site | status |
|---|---|
| `extract._ocr_pdf_pages` | correct — index-keyed dict, assembled `for i in range(n)`; probed 30× |
| `walker.run` document collection | correct — collected in completion order, then `documents.sort(key=document_sort_key)` before the result is built |
| `walker._Errors` | **fixed** (D-A2) |
| `walker` progress callback | correct — advisory only, nothing it reports reaches disk |

### Class E — absolute paths reaching hashed content

| site | status |
|---|---|
| `RunConfig.output_root` in the log's content | **fixed** (D-A1) |
| `DocumentRecord.error` from `read failed: {exc}` (OSError carries the path) | **fixed** — `sanitize_message` at `_record()` |
| `DocumentRecord.error` from `.msg` parse failures (carries the scratch temp name) | **fixed** — same |
| `DocumentRecord.notes` | **fixed** — same |
| `RunResult.warnings` | correct — derived from already-sanitized record errors and from relative paths |
| `rel_path`, `filename` | correct by construction — relative, POSIX, NFC |

### Class F — caps and bounds (no silent caps)

| site | disclosure |
|---|---|
| `_XLSX_MAX_ROWS` (50,000) | in the page text AND as a document note |
| `_CSV_MAX_ROWS` (50,000) | in the page text AND as a document note |
| `_ZIP_MAX_MEMBERS` (2,000) | note, with the count |
| `_ZIP_MAX_MB` (500) | note, naming the member it stopped at |
| `_ZIP_MAX_DEPTH` (3) | note |
| `_MAX_DATES_PER_DOC` (200) | document note |
| `_Errors.cap` (2,000) | trailing row stating how many were omitted and on what order |
| error string length | **fixed** (D-A3) — `clip_message` marks the truncation |
| `_NATIVE_TEXT_FLOOR` (40) | not a cap — a routing threshold; nothing is dropped |
| CSV sniffer window (8 KB / 200 rows) | not a cap — heuristic input for delimiter choice; no data dropped |
| bake-off sample (20 of 461 scanned pages) | stated in the bake-off report |

### Class G — AI/LLM hooks (§12)

`_maybe_caption` was the only one, and it is gone. No import of any model
runtime other than `onnxruntime` (the OCR engine) exists in the package.

### Class H — module-level mutable state that could change output

`_OCR_ENGINE` and `_OCR_POOL` are lazily-built singletons whose identity does
not affect output; construction is locked, as inherited. Everything that can
change extracted bytes travels in `ExtractOptions` / `RunConfig`. MIP 3.9's
mutable `_DATE_CONVENTION` global was **not** vendored — the convention is a
parameter in `dating.detect_dates`.

## 4a. Performance findings from the real-corpus run

Measured on the D-12 corpus (298 PDF / 53 DOCX / 17 PPTX / 7 DOC; 17,732 PDF
pages, 461 scanned) on the shared build machine — other tracks' test suites
were running concurrently, so every wall-clock figure here is an upper bound
rather than a clean benchmark.

**Clean numbers:**

- **Inventory (walk + SHA-256 + tiering): 375 files, ~2.6 GB, in 5.2 s.**
  Stage 1 is not a cost centre.
- **OCR: 5.74 s/page mean, 5.1 s median, 23.3 s max** (bake-off, single
  threaded, 200 dpi). 461 scanned pages projects to ~44 minutes of OCR *work*.

**A wrong conclusion, recorded because it was nearly reported as a finding.**
Watching the progress log against wall-clock, it appeared that the
coordinating loop had stalled for tens of minutes while extraction continued —
the "GIL-starved main thread" story, and a plausible one. It was false.
Checking the process creation time against the last logged `elapsed_s` showed
the log 12 seconds behind reality: the loop was reporting normally and my
sense of elapsed time was wrong. The claim is recorded here as **withdrawn**
rather than deleted, because the same mistake is easy to make again from the
same evidence, and because the fix it motivated (below) survives on its own
measurement.

**Thread oversubscription is real and unquantified.** The process carried
**175 OS threads** on a 32-core box while using ~13.7 cores. RapidOCR holds
three ONNX sessions, each defaulting to one intra-op thread per core, and up to
16 page OCRs run concurrently; the product is far more runnable threads than
cores. Pinning `intra_op_num_threads=1` and letting the page pool supply the
parallelism is the standard remedy — but it is a hypothesis, not a finding,
until it is measured, and nothing here measures it.

**A measured inefficiency, fixed: the resume journal serialized every document
three times.** `canonical_json({..., "doc": json.loads(canonical_json(d))})`
walks the document's entire text twice, parses the result, and dumps it again —
on the coordinating thread, under a lock. Replaced with one conversion and one
dump. Measured on a synthetic 900-page scanned document (2.1 MB of JSON):
**255 ms → 88 ms, a 2.9× reduction**, median of 5 runs, outputs asserted equal.
The same triple round-trip was present in the probe emitter's log writer and is
fixed with it.

**The full-corpus run did not finish inside this work session, and the §10
target is therefore NOT restated.** Last observed state: 27 of 368 Tier-1
documents complete at 884 s elapsed, ~13.7 of 32 cores busy. Extrapolating
from that would be extrapolating from the corpus's largest documents, which
sort first — the reported rate is not the steady-state rate and quoting it as
one would be worse than quoting nothing. What is established, and stands:

- Stage 1 is free (5.2 s for 2.6 GB).
- OCR is not the cost centre on this corpus — 461 scanned pages, not the
  ~2,850 the §10 target assumes — which is what the decision register's
  "corpus reality" section predicted and this run's page-kind split confirms.
- The non-OCR path over 17,732 pages is the real cost, and it is
  pypdf-bound and pure Python.

Restating §10 needs one uninterrupted full run on an otherwise idle machine,
with the thread-pinning question settled first. That is a Sprint-2 task with a
known shape, not an open question.

## 5. Coordination note for Track B — `parent_doc_id` before Stage 3b

Track A produces records **before** doc IDs exist: `doc_id` is `""` on every
record it emits, because Stage 3b is Track B's. An archive member therefore
cannot carry its parent's *doc ID* — that ID has not been assigned yet.

Track A sets `parent_doc_id` to the **parent's `rel_path`** and
`container_order` to the member's position in `infolist()` order. Track B's ID
assigner must remap `parent_doc_id` from rel_path to the assigned doc ID at
the same moment it assigns IDs.

This is not a contract violation — the field is a string and holds one — but
the field name invites the assumption that it already holds a doc ID, and an
assigner that assumes so would emit `LI-06881.01` children hung off nothing.
Flagged rather than worked around. It is not raised as a stop-the-line event
because the contract *can* express the linkage; what is underspecified is the
handover point, which is a coordination question.

## 6. Known gaps, stated rather than papered over

1. **`verify/probe_emit.py` is provisional and must be deleted at
   integration**, not merged. Track B owns `emit/`. It exists because a
   determinism proof that skipped the four named artifacts because their writer
   lives in another worktree would prove nothing about the claim. Its doc IDs
   are positional `DIQ-` placeholders.
2. **The D-01 bake-off is rapidocr-only.** Tesseract is not installed on this
   machine and was not installed to run it. See `docs/bakeoff/`.
3. **Determinism is proven on the synthetic corpus**, not on the real one (§3).
4. **`.xls` is Tier 1 and implemented via `xlrd`, but no `.xls` fixture is
   generated** — no library in the dependency set writes the legacy format.
   The code path is exercised only through the content-sniff retry chain.
5. **The full-corpus timed run did not complete** (§4a). The §10 performance
   target is not restated; the inputs needed to restate it are named.
6. **The corpus accounting gate has not been exercised end to end on the real
   corpus** — it passes on the synthetic corpus and per-document `validate()`
   runs on every real record produced so far, but the corpus-wide
   `accounting.check()` over all 17,732 pages runs only at the end of a
   completed run, and no run has completed. Same blocker as (5).
7. **`.msg` has no fixture and no test.** Nothing in the dependency set writes
   an Outlook `.msg` — `extract-msg` reads only — so a synthetic one cannot be
   generated in-repo, and a real one is client data. The `.eml` path IS
   covered (`09_notice.eml`, generated byte-pinned). `.msg` extraction is
   therefore vendored-but-unexercised, and it is the one Tier-1 format where
   "the corpus doesn't exercise it" is currently doing work it should not be
   allowed to do. Loudest available mitigation is already in place: a `.msg`
   failure is a FAILED record with an actionable message, never a silent skip.
