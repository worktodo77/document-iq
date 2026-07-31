# §11 Reuse Audit — MIP 3.9 Ingestion Module

**Date:** 2026-07-30
**Auditor:** Claude (reconnaissance pass over `worktodo77/mip39-schedule-analysis-tool` @ f8a6b60)
**Subject:** document ingestion layer, primarily `src/mip39/api/docs_extract.py`
**Decision (per §11 rule):** **REUSE** — items 1 and 2 pass, item 4 is acceptable. Gaps listed below are additive build, not rework.

## Audit items

### 1. OCR-vs-native page detection — PASS

`docs_extract._extract_pdf` does hybrid per-page routing: native text is extracted per page via pypdf; pages with fewer than 40 characters of native text are flagged and only those page indices go through OCR (`_ocr_pdf_pages`: PyMuPDF rasterization → rapidocr_onnxruntime + OpenCV). OCR'd pages are merged back in place; good native pages are untouched. This is exactly the mixed native/scanned handling §3 requires. A separate `_photo_block` heuristic detects photo-printout PDFs (trivial text layer + one dominant embedded image) and emits a `[PHOTO]` marker with EXIF date/GPS. `ocr_available()` gates the OCR path on optional imports.

### 2. Page-locator preservation — PASS

`_join_pages()` emits an inline `[page N]` marker for **every** page, including empty ones, so numbering stays aligned with the physical document. Downstream consumers parse it back with a regex. Equivalent inline markers exist for other formats: `[slide N]` (PPTX), `[sheet: name]` (XLSX), `[file: member]` (ZIP), `[header: …]` (CSV). Converting to DocIQ's `===== PAGE n [BATES: …] =====` convention is a formatting change at the join point, not a redesign. DOCX/EML/MSG carry no page numbers (library limitation) — consistent with §3's "page approximation noted in log."

### 3. Format breadth — exceeds v1 needs

`SUPPORTED_EXTENSIONS`: `.txt .md .csv .log`, `.pdf`, `.docx`, `.pptx`, `.xlsx .xlsm .xls`, `.eml .email .msg`, `.zip` (recursive to depth 3), and standalone images (OCR-only path). Also a content-sniffing recovery layer (`_sniff_kind` / `_retry_by_content`): on extractor failure it checks magic bytes (`%PDF`, zip family, OLE) and retries the matching extractor chain — handles misnamed files common in litigation productions, with the recovery annotated in an audit note. Not covered: RTF, legacy `.doc` (both flagged as clear errors, not silent failures).

### 4. Robustness to malformed input — ACCEPTABLE (strong)

Built for messy productions, not clean documents:

- Central `ExtractionError` type; every extractor wraps parsing with actionable error messages.
- `extract_path()` (the pool worker) never raises — always returns `{"ok": bool, ...}`, safe for pool consumption.
- Per-page OCR failure isolation ("one bad page must not sink the document").
- ZIP anti-DoS guards: max uncompressed MB (500), max member count (2000), max nesting depth (3), env-configurable; members read in memory only.
- XLSX/CSV row caps with **disclosed** truncation markers; CSV multi-encoding fallback (utf-8-sig → utf-8 → latin-1) and delimiter auto-detection with fallback probe.
- Bulk-ingest level (`ingest_folder.py`, 361 ln): per-file watchdog timeout on actual execution time, disk-space preflight, capped error log, resume-by-relative-path after a crash.

Degradation is disclosed (truncation/skip markers) rather than silent — matching DocIQ Core Principle 1.

### 5. Licensing / dependency hygiene — CONDITIONAL

Declared in pyproject: `pypdf`, `python-docx`, `openpyxl` (core), `pymupdf` + `rapidocr-onnxruntime` (`[ocr]` extra). **Used but NOT declared anywhere:** `python-pptx`, `extract-msg`, `xlrd`, `opencv-python`, `numpy`, `Pillow` — optional at call sites (try/except ImportError with clear messages), but a naive dependency on mip39 will not pull them in. DocIQ must declare its own complete dependency set. All named libraries are permissively licensed (MIT/BSD/Apache-family) — compatible with standalone distribution; verify exact versions at packaging time.

## Additional reusable components

- `src/mip39/api/ingest_folder.py` — out-of-process bulk-ingest worker: folder walk, thread pool fan-out, resume/cancel/watchdog/disk-cap, status + error sidecars. Directly relevant to Stage 1/2 batch processing.
- `src/mip39/api/doc_census.py` (483 ln) — post-extraction classification/dating (record type, date, author/recipient, Bates, page count). Partial overlap with Stage 1 date detection and Stage 3 Bates work.

## Gaps — new build required regardless of reuse

1. **OCR engine divergence:** mip39 uses rapidocr_onnxruntime; the spec (§3, §10) names bundled Tesseract 5. Open decision — rapidocr is already proven in-house and ONNX-based (bundles cleanly); Tesseract is the industry-recognizable name for law-firm IT review. One must be chosen before Stage 2 build.
2. **Per-page OCR confidence is not recorded** — spec requires it with an 85% review-flag threshold (§4 Stage 2). rapidocr returns per-line confidences; capture and aggregation must be added.
3. **Marker format:** `[page N]` → `===== PAGE n [BATES: …] =====` conversion at the join point.
4. **Bates detection (Stage 3)** — not present in the extract layer (census has partial patterns); the corner/footer pattern-matching + user-confirmation flow is new.
5. **Section classification / KEEP-DROP profiles (Stage 4, §6)** — entirely new.
6. **Index deliverable + master-index reconciliation (§5), processing log, run summary, verify stage (§4/§7)** — entirely new.
7. **No unit tests cover the OCR path** (`_ocr_pdf`, `_ocr_pdf_pages`) or the hybrid native/OCR routing with a mixed-content fixture — must be added if reused, per the "green proves nothing" rule.
8. **Determinism audit needed:** byte-identical-output requirement (§2.5) has not been verified against the reused code paths (e.g., dict ordering, OCR nondeterminism, timestamps in output).

## Test coverage of reused code (as-is)

~490 lines across `tests/test_docs_extract.py` (283 — EML, XLSX, ZIP incl. nested, marker alignment incl. empty pages, real 2-page PDF round-trip, CSV sniffing, content-recovery chain ×5), `test_doc_census.py` (138), `test_ingest_folder.py` (69). OCR path untested (optional-extra dependent).
