# Codex review — DocIQ Sprint 1 (pipeline core)

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-1`
**Reviewed commit:** `781481743dfa81f96e5ac6b8288b2156eeff383a`
**Review date:** 2026-07-31
**Round:** 1

## Verdict

**HOLD — no A findings; seven B findings.**

The submitted verification is substantial and reproducible: the full suite passes, the self-test passes, and the published determinism result is supported. The hold is for production-path behaviors that the current tests do not cover: incomplete runs can pass the normal gates, output-affecting configuration is absent from the determinism identity, supported EML content can disappear without a retry marker, duplicate hashes can receive an arbitrary legacy ID, confirmed Bates formats are not actually enforced or reused, the token “hard floor” overstates what the estimator proves, and unsupported inventory is absent from the document index.

Per the request's severity gate, the B findings require correction or an explicit disposition and re-review. The disclosed acceptance gaps are not independently refiled unless the implementation creates a separate correctness defect.

## What the review accepts

- The core retry-disclosure decision is correct. When pooled and serial extraction produce identical final evidence, the fact that a retry occurred is invocation metadata and belongs outside the content hash. Including that fact in content would make the retry mechanism self-defeating under criterion 7. Final document records, errors, and extracted evidence remain content.
- The serial re-read is a defensible mitigation for the observed presentation-extraction stall. I found no code-supported basis for attributing the behavior specifically to the `.pptx` parser lock described as unconfirmed in the request.
- The watchdog-thread limitation is honestly disclosed: a timed-out worker cannot be killed and may overlap the serial retry. This review does not promote that disclosed limitation to a new finding without evidence of corrupted output.
- The Track A and Track B verification artifacts are useful evidence. The full suite and self-test reproduced successfully; passing tests do not, however, exercise the findings below.

## B-1 — Blocked or cancelled runs can publish normal-looking, green deliverables

**Locations:** `src/dociq/ingest/walker.py:905`, `src/dociq/ingest/walker.py:1075`, `src/dociq/pipeline.py:158`

The disk preflight failure path returns an empty `RunResult` with a warning. Cancellation similarly returns the documents accumulated so far plus an invocation note. The pipeline does not distinguish either result from a completed walk: it continues through assignment, stale-deliverable purge, emission, accounting, manifest construction, and summary generation.

`PipelineOutcome.ok` is derived from accounting success and the absence of unclassified deliverables. An empty blocked run can satisfy zero-equals-zero accounting, while a partial cancelled run can balance against only the partial set it produced. Both can therefore report `ok=True` and replace a prior complete output set with empty or partial deterministic artifacts.

This is a provenance and delivery-state defect, not merely a UI status issue. A preflight rejection or cancellation must have a typed terminal status that prevents normal publication, or it must make the correctness gates fail while preserving the last complete deliverables. The status must also be visible in the machine-readable run result, manifest, log, PDF, and GUI.

## B-2 — The determinism identity omits byte-affecting effective configuration

**Locations:** `src/dociq/contracts.py:323`, `src/dociq/ingest/extract.py:148`, `src/dociq/ingest/walker.py:60`, `src/dociq/ingest/walker.py:826`

`RunConfig` is documented as containing all output-affecting settings, but several effective inputs live outside it:

- environment-controlled XLSX, CSV, ZIP-member, ZIP-depth, and ZIP-byte caps;
- retry maximums and retry-budget decisions;
- walk options such as recursion and per-file timeout;
- OCR model overrides and the bytes/version of the selected model.

When a cap, timeout, retry bound, or model choice bites, the same source folder, profile, and index can produce different evidence while presenting the same hashed run configuration. Per-document truncation notes are valuable, but they do not make the manifest's determinism identity complete.

The retry *event* may remain invocation metadata when final evidence is identical. The retry policy and all other settings capable of changing final evidence are different: they must be frozen or represented in the hashed effective configuration. Model artifacts should be identified by stable version and/or content hash, and the manifest claim should name the full identity it covers.

## B-3 — EML failures can silently remove body or attachment evidence

**Locations:** `src/dociq/ingest/extract.py:848`, `src/dociq/ingest/extract.py:884`, `src/dociq/ingest/extract.py:970`, `src/dociq/ingest/extract.py:990`

The EML body walk has a broad exception handler that replaces the body with an empty string without a canonical marker or explanatory note. Attachment expansion separately catches message parsing failures and returns an empty `ZipExpansion`, again without a marker. The parent record is then emitted with zero child attachments.

Because neither catch emits the transient attachment-enumeration marker, the walker's serial retry registry is bypassed. A supported input can therefore lose both readable body content and all attachments while appearing successfully processed.

Every exception path that emits less EML evidence must produce a stable disclosure and an appropriate transient/final error marker. Attachment enumeration must participate in retry, and a final failure must remain auditable in the parent record and accounting. The same audit should cover sibling broad catches that can suppress date or GPS evidence.

## B-4 — Duplicate content hashes can receive an arbitrary legacy ID

**Location:** `src/dociq/docid/assign.py:357-417`

The secondary D-04 lookup stores a single master-index row per SHA-256 using `setdefault`. After exact path matching, every unmatched document with that digest is offered that one row. This is unsafe when both the inventory and index legitimately contain duplicate content.

A targeted reproduction used two moved source files with the same digest and two master-index rows with the same digest but different legacy IDs. The first document received `LI-00010`; the second received a new `DIQ-...` ID; the other valid legacy row was left unused. The warning also claimed the row had been claimed via a stronger key, although the ambiguity was created by the scalar hash map.

Hash fallback must match only when the digest is unique on both unmatched sides. Duplicate groups must be treated as ambiguous, disclosed, and reconciled without assigning an arbitrary legacy ID. The implementation should retain grouped candidates rather than a single row per digest.

## B-5 — Confirmed Bates formats are neither fully enforced nor reused

**Locations:** `src/dociq/identify/bates.py:117-121`, `src/dociq/identify/bates.py:392-421`, `src/dociq/pipeline.py:262`, `src/dociq/pipeline.py:421`

`BatesFormat` records allowed digit widths and builds a pattern, but `apply_bates` validates only prefix, separator, and suffix. It does not enforce digit width. A confirmed `MNFV` format allowing four or five digits therefore accepted `MNFV 1234567890`.

The confirmation model also loses the separator before a suffix. A proposal inferred from `MNFV 000391-CONF` retained suffix `CONF` but persisted a pattern equivalent to `MNFV 000391CONF`, without the hyphen.

Finally, the pipeline does not consume `RunConfig.bates_pattern` or a profile's stored Bates pattern to make the next decision. It can record the old pattern when there is no new decision, but it does not deserialize and apply that confirmation. A rerun can therefore silently accept out-of-format locators or fail to reuse the approved format.

The persisted confirmation must represent the complete grammar, including prefix, separators, suffix separator, suffix, and exact allowed digit widths. Application must validate the complete grammar, and stored run/profile confirmation must be loaded and applied—or the run must fail closed when it cannot be reconstructed.

## B-6 — The token proxy is not a tokenizer-independent hard floor

**Locations:** `src/dociq/verify/tokens.py:107`, `src/dociq/verify/tokens.py:256`, `src/dociq/verify/tokens.py:372`, `src/dociq/emit/summary.py:323`

The measured count is based on DocIQ's approximate pre-token regular expression. A byte-level BPE cannot merge across *its own tokenizer's* pre-token boundaries; that fact does not prove it cannot merge across boundaries invented by a different approximate regex. A target tokenizer with coarser pre-tokenization can therefore produce fewer tokens than this proxy.

Consequently, this count is not established as a hard lower bound for every byte-level BPE tokenizer and cannot, on its own, refute the D-03 ratio band. The current `ratio_refuted`, `floor_tokens`, GUI “tokens at least,” and 17-million hard-floor wording overstate the evidence.

The PDF compounds the provenance issue by always saying token figures come from a calibrated character ratio, including the default uncalibrated path and the path where the ratio is declared refuted. The exact measured provenance is not rendered there.

Until checked against a specified tokenizer and its actual pre-tokenizer artifact, the proxy should be labeled as a characterization with explicit assumptions, not a universal hard bound or refutation. The PDF, processing log, and GUI must render the actual method and calibration provenance used for that run.

## B-7 — Unsupported source files are missing from the first-class document index

**Locations:** `src/dociq/pipeline.py:473-540`, `src/dociq/gui/view_models.py:160`

Unsupported Tier-2 files are retained separately for the processing log and summary, but only supported `documents` are sent through ID assignment and `build_index_rows`. Unsupported records therefore keep an empty document ID and do not appear in `document_index.csv` or `document_index.xlsx`.

This contradicts the required `Unsupported` processing status in the document index and the GUI's explicit claim that unsupported files are recorded there. In the submitted real run, the seven legacy `.doc` files are reported in log/summary counts but absent from the first-class index deliverable.

Every inventoried source entry needs an auditable index row and stable ID, including unsupported items. Such rows should carry `Unsupported` status and no clean-text reference unless a deliberate fallback artifact is defined. The index, log, summary, reconciliation, and accounting views must agree on the same inventory.

## D-1 — Skipped dirty index rows cannot later reconcile as index-only

**Location:** `src/dociq/docid/masterindex.py`

Invalid, negative, and duplicate Original Sort rows are warned about and skipped during master-index loading. Some comments and evidence language say these rows will later reconcile as index-only, but skipped rows are not retained in `MasterIndex.rows` and cannot appear in reconciliation. The warning prevents this from being silent source loss, so this is nonblocking here; either retain a quarantined row representation for reconciliation or correct the claim.

## Verification performed

- Read the request and its complete mandated read list from `build/sprint-1` at `7814817`, including requirements v1.1, the decision register and amendments, PageModel freeze, contracts v1.2.0, Track A/Track B notes, and the integration verification report.
- Inspected the shipping pipeline, ingest, Bates, DocID, profiles, emitters, verification, GUI seam, branding, and relevant tests.
- `python -m pytest`: **481 passed in 512.96s**.
- `python -m dociq.selftest`: **44 checks passed**, 25 pages across 17 documents, 2 inventoried; determinism exercise reported 8 runs and 1 hash.
- Reproduced the Bates digit-width and suffix-separator failures in memory.
- Reproduced duplicate-hash master-index ambiguity with two moved documents and two legacy rows sharing one digest.

## Merge condition

Resolve or explicitly disposition B-1 through B-7, add focused regression coverage for each corrected path, refresh the verification evidence, and request round 2. D-1 may be handled editorially or tracked separately and does not by itself require re-review.
