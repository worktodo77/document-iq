# LI Document IQ — Requirements Document (v1.1 — Ruled Baseline)

**Prepared for:** Long International, Inc.
**Owner:** Alex Bachowski, P.E.
**Date:** July 30, 2026 (v1.0 draft folded to v1.1 same day)
**Status:** Ruled baseline — v1.0 draft + decisions D-01..D-09 (see `docs/decisions/decision_register.md`)

**v1.1 amendment log** (all rulings 2026-07-30):
- OCR engine: Tesseract 5 → **rapidocr** (ONNX), subject to the Sprint-1 bake-off (D-01)
- Legacy DOC: Tier 1 → **Tier 2, flagged loudly** with remediation hint (D-02)
- Token estimate: **calibrated chars-ratio, displayed as a conservative range** (D-03)
- Doc ID scheme ruled: **LI File No. when master index supplied**, with determinism mitigations (D-04)
- Profile library: **configurable folder, local %APPDATA% default** (D-05)
- Name confirmed: **LI Document IQ** (D-06)
- UI direction, explorer icon, GUI logo ruled (D-07/D-08/D-09); LI brand assets committed in `assets/branding/`
- §5 reconciliation matching inverted to reflect the real Project 495 index audit: **filepath+filename primary** (proven 100% unique), hash secondary, Bates when present (the audited index has no Bates column)
- §11 reuse audit performed: decision **REUSE** of MIP 3.9 `docs_extract.py`

## 1. Purpose and Positioning

LI Document IQ ("DocIQ") is a standalone Windows desktop utility that converts large volumes of heterogeneous matter documents (monthly progress reports, correspondence, notices, spreadsheets) into a compact, fully traceable text corpus suitable for AI-assisted forensic analysis — specifically, the input format required by Long International's Expert Assist skill set (evidence-mining, causation, contradiction-finder).

DocIQ is a **deterministic reducer, not an interpreter**. It performs no AI extraction, summarization, or classification of content meaning. Its evidentiary status is equivalent to printing or photocopying: every output is mechanically derived from the source, and every transformation is logged. This constraint is a design requirement, not a limitation — it keeps the tool outside the scope of methodological challenge.

**Motivating problem.** A representative matter record (e.g., 38 MODEC MPRs at ~20 MB / ~150 pages each) totals roughly 3.4M tokens of extracted text — several times the capacity of a Claude Project even in RAG mode. After section-level reduction, the same record fits comfortably, and after downstream event-log extraction (performed by Expert Assist, not DocIQ) the entire multi-year record can be held in a model's working context simultaneously.

## 2. Core Principles (Non-Negotiable)

1. **No silent deletion.** Every page in equals a page accounted for in the processing log. Unclassified content is KEPT by default; only expert-approved profile rules may drop content.
2. **Original locators preserved.** All page markers reference the page number of the ORIGINAL native document, never the reduced output. Bates numbers, where detected, are carried alongside page numbers.
3. **Expert controls omissions.** The tool proposes; the expert disposes. All drop rules are created or approved by the expert via the profiling workflow (§6) and stored as a per-format profile that travels with the matter.
4. **Fully offline.** No network access of any kind. All OCR and processing runs locally. This must be verifiable (the exe makes no outbound calls) and is stated in user-facing documentation as a confidentiality feature for law-firm IT review.
5. **Deterministic and repeatable.** Same inputs + same profile = byte-identical outputs. Every run is hash-logged. *Per D-04, the optional master index is itself a hashed run input: the contract is "same folder + same profile + same master index = byte-identical."*

## 3. Supported Input Formats (v1)

| Tier | Formats | Handling |
|---|---|---|
| 1 — Full | PDF (native text) | Direct text extraction, layout-aware |
| 1 — Full | PDF (scanned/image) | Local OCR (**rapidocr**, ONNX; per-page confidence recorded) — D-01; Sprint-1 bake-off vs Tesseract on real MPR pages decides finally |
| 1 — Full | DOCX | Text extraction with page approximation noted in log |
| 1 — Full | XLSX, XLS, CSV | Sheet-by-sheet extraction to delimited text; sheet name = section |
| 1 — Full | MSG, EML | Message body + metadata (from/to/cc/date/subject); attachments extracted as child documents linked to the parent message ID |
| 1 — Full | TXT, RTF | Pass-through with normalization |
| 2 — Listed only | **DOC (legacy binary Word — D-02, with "open in Word and Save-As DOCX/PDF to include" remediation hint in the run summary)**, XER, MPP, DWG, images, unknown formats | Inventoried, hashed, and reported on the "Unsupported" list; never blocks a run |

Mixed PDFs (native + scanned pages in one file) are handled page-by-page.

## 4. Processing Pipeline

**Stage 1 — Inventory.** Recursive scan of the selected folder. For each file: SHA-256 hash, filename, size, page count, format, detected date(s), duplicate detection (by hash). Output: preliminary inventory.

**Stage 2 — Triage.** Per-document, per-page: native-text vs. image detection. Image pages queued for OCR. OCR confidence recorded per page; pages below a configurable confidence threshold (default 85%) are flagged for human review in the summary report.

**Stage 3 — Bates detection.** OCR pattern-matching against page corners/footers for Bates stamps. Detected format (prefix + number) is confirmed with the user on first detection per document set, then applied automatically. Bates range recorded per document; per-page Bates carried into page markers. *Bates is expected to be absent on many matters (the audited Project 495 index has none); absence is normal, not an error.*

**Stage 3b — Doc ID assignment (D-04).** When a master index is supplied: Doc ID = `LI-<zero-padded Original Sort number>` for every file matched to an index row (match key: filepath+filename primary, SHA-256 secondary, Bates range when present). Container children (ZIP/RAR members, email attachments — which have no index row) get parent-derived IDs (`LI-06881.01`, assignment order deterministic). Folder files with no index match get IDs in a visually distinct `DIQ-` synthetic range that cannot collide with LI numbers. Without a master index: all IDs are DIQ-native (sequential, stable sort by path then hash). The processing log records which regime ran and the index snapshot's filename + hash; reconciliation WARNS if a later index snapshot renumbers previously issued IDs.

**Stage 4 — Section classification (recurring formats only).** Section headers matched against the active format profile (§6). Each section marked KEEP or DROP per profile. Documents with no matching profile pass through whole.

**Stage 5 — Emit.** Outputs written per §7.

**Stage 6 — Verify.** Post-run self-check: page accounting (pages in = pages kept + pages dropped, per document), output hash manifest, token count estimate.

## 5. Document Index Deliverable

The document index is a first-class deliverable, not a by-product.

**Fields:** Doc ID (per D-04) · **LI File No. (when master index supplied)** · Filename · Format · Date (detected) · Document type (from profile or filename pattern) · Page count · Bates start · Bates end · SHA-256 · Parent doc (for email attachments and archive members) · Processing status (Full / Partial-OCR-flagged / Unsupported) · Sections dropped (count).

**Formats:** `document_index.xlsx` (formatted, LI-styled) and `document_index.csv` (machine-readable).

**Optional master-index reconciliation.** The user may upload LI's internal document index (Excel/CSV, maintained by the document database manager). DocIQ matches on **filepath+filename (primary — proven 100% unique on the Project 495 index; filename alone is NOT unique, 693 duplicates), SHA-256 (secondary), and Bates range (when the index carries Bates)**, and produces a reconciliation report:

- Documents in both (matched, with any field discrepancies flagged)
- Documents in the folder but not the master index
- Documents in the master index but not the folder

This report serves as a production completeness check and is written as a separate tab of the index workbook.

## 6. Format Profiles and the Profiling Workflow

A format profile is an editable YAML file defining, for a recurring document format (e.g., "MODEC MPR"):

- Header patterns that identify the format
- Section header patterns and their KEEP/DROP disposition
- Bates stamp pattern (if known)
- Notes field (free text — why sections were dropped, who approved)

**Profiling run (first encounter with a new format):**

1. User selects "Profile new format" and points at a representative sample (one or several documents).
2. Tool detects candidate section headers across the sample and presents a checklist UI: each recurring section, its frequency across the sample, average page count, and a one-click preview of a sample instance.
3. Expert checks sections to DROP (e.g., photo logs, HSE statistics tables, org charts, transmittal sheets). Everything unchecked is KEPT. Default state for every section is KEEP.
4. Profile is saved with the expert's Windows username and timestamp, and stored (a) in the tool's profile library for reuse and (b) as a copy in the matter output folder as a record of what was excluded and by whose decision.

Profiles are versioned; re-running a matter with a modified profile produces a new run log entry noting the profile version used.

**Profile library location (D-05):** default `%APPDATA%\LI DocIQ\profiles` on the local machine; a settings field may point the library at a shared LI drive path. The per-matter copy in the output folder happens regardless, so a matter is always self-documenting even if the shared library is unreachable.

## 7. Outputs

Written to a user-selected matter output folder:

| Output | Purpose |
|---|---|
| `clean_text/<doc_id>.txt` | One text file per document. Page markers in the form `===== PAGE n [BATES: XXX-000123] =====` (Bates segment present only when detected) using ORIGINAL page numbers. This is the input format required by Expert Assist evidence-mining. |
| `sources.json` | `{doc_id: clean_text_path}` manifest read directly by Expert Assist. |
| `document_index.xlsx` / `.csv` | The index deliverable (§5), including the reconciliation tab when a master index was supplied. |
| `processing_log.json` | Complete audit: per-document page accounting, sections dropped and under which profile rule, OCR confidence flags, unsupported files, hashes, profile version, **Doc ID regime + master-index snapshot hash (D-04)**, run timestamp, and operator. |
| `run_summary.pdf` | One-page human-readable summary (LI-branded): documents processed, pages in/out, token estimate before/after, low-confidence OCR pages requiring review, unsupported files. |

**Token estimate is a headline feature (D-03).** Estimate = characters ÷ a ratio **calibrated during development against the real Claude tokenizer on representative MPR text** (dense tables tokenize heavier than prose; expect ≈3.3–3.6 chars/token). Displayed as a conservative range (e.g., "≈ 82–94K tokens") with a plain-language capacity statement ("fits directly in a Claude Project without retrieval mode"). No bundled third-party tokenizer.

## 8. Claude Handoff

DocIQ must make the step from "outputs on disk" to "analysis in Claude" a one-click action. Because Claude.ai Projects expose no public API for programmatic file upload (and session-key-based third-party sync tools are excluded on security and terms-of-service grounds), the handoff uses two sanctioned paths, selected via a single "Analyze in Claude" button on the summary screen:

**Path A — Claude Project package (browser; general queries).**

- DocIQ assembles an `upload_package/` subfolder containing only the files intended for upload: `clean_text/*.txt`, `sources.json`, and `document_index.csv`. The processing log and run summary are excluded (they remain in the matter folder).
- The package is checked against Claude Project constraints (per-file size, file count) and the summary screen states the estimated token load and whether the project will operate in direct-context or RAG mode.
- DocIQ auto-generates `README_START_HERE.txt` in the package: a short block of suggested project instructions for the matter (matter name, document set description, date range, note that page markers cite original pagination and Bates numbers) that the user can paste into the Project's instructions field.
- The button opens the `upload_package/` folder in Explorer and claude.ai/projects in the default browser side by side; the user drags the folder contents into project knowledge.

**Path B — Expert Assist via Claude Cowork (no upload).**

- Expert Assist skills execute scripts and build workbooks, which requires a Claude surface with filesystem access (Claude Cowork or Claude Code) — not the browser chat. In that environment Claude reads the matter folder directly from disk; no upload occurs and Project capacity limits do not apply.
- DocIQ therefore writes its outputs in the Expert Assist matter-folder structure (`clean_text/`, `sources.json` at the locations evidence-mining expects) so the matter is analysis-ready with zero rearrangement.
- The button launches Claude Cowork (if installed) pointed at the matter folder, or displays the folder path with brief instructions if not.

Path B is the recommended route for forensic matters (full audit trail stays local beside the evidence); Path A serves quick general-query use and mobile/browser access.

## 9. User Interface

- Single-window desktop GUI, LI branding per the ruled design system (D-07/D-08/D-09):
  - **Design direction (D-07):** "Counsel docket" light editorial chassis — white ground, hairline grid, oversized token headline, flags as chips — with the **token capacity gauge** (fill + capacity marker + "% of direct-context capacity" mono caption) under the headline, and a standing "offline — no network" indicator in the window chrome. Structure navy `#0E4D80` family; accent light blue `#2E9FD4` family (exact values sampled from brand art at build time).
  - **GUI logo (D-09):** the L1 app badge lockup — the D-08 icon tile beside "Document IQ" over a letterspaced LONG INTERNATIONAL caption — composed deterministically from `assets/branding/li_monogram_source.png` + typeset name, shipped as a rendered PNG.
  - **Explorer icon (D-08):** "fanned corpus stack" on the LI monogram tile, generated (not hand-drawn) via the PDF Cleaner `make_brand_icon.py` recipe; multi-size .ico with sub-64 px simplification to a single page silhouette.
- Primary flow: folder picker → format profile selector (or "Profile new format") → optional master index upload → Run → progress bar with per-document status → summary screen.
- Summary screen shows: token count headline + capacity gauge, pages kept/dropped, flagged items (low-confidence OCR, unsupported formats, reconciliation mismatches) with click-through to detail.
- No command-line knowledge required. A CLI mode may be added later for batch/scripted use but is out of scope for v1.

## 10. Technical Requirements

- **Platform:** Windows 10/11, 64-bit. Single-file executable (PyInstaller or equivalent). No installer, no admin rights, no external runtime dependencies.
- **Stack (indicative):** Python 3.11+; pypdf + PyMuPDF (PDF; per the reused MIP 3.9 layer); **rapidocr_onnxruntime (OCR — D-01)**; python-docx, extract-msg, openpyxl (other formats; full dependency set declared explicitly — the MIP 3.9 pyproject omits several used libraries, see reuse audit); a lightweight GUI framework consistent with LI's existing tools (PySide6 family).
- **Network:** None. The application must function with all network interfaces disabled and must make no outbound connections.
- **Performance target:** A 38-document / ~5,700-page matter set with ~50% scanned pages completes in under 60 minutes on a standard business laptop (OCR-dominated). Native-text sets should complete in minutes.
- **Data handling:** All processing in the user-selected working folder; no temp files outside it that persist after the run; no telemetry.

## 11. Code Reuse — MIP 3.9 Tool Ingestion Module

Audit performed 2026-07-30 — see [mip39_ingestion_audit_2026-07-30.md](../reuse_audit/mip39_ingestion_audit_2026-07-30.md). **Decision: REUSE** `src/mip39/api/docs_extract.py` (+ `ingest_folder.py`, parts of `doc_census.py`) per the §11 decision rule: OCR-vs-native detection PASS, page-locator preservation PASS, robustness ACCEPTABLE (strong). Gap list (new build regardless): per-page OCR confidence capture, marker format conversion, Bates detection, section KEEP/DROP profiles, index/reconciliation/log/summary deliverables, OCR-path tests, determinism audit, full dependency declaration.

## 12. Out of Scope (v1)

- Any AI/LLM processing, summarization, entity extraction, or event logging (Expert Assist's role)
- P6/XER schedule parsing
- Cloud storage, network features, multi-user coordination
- macOS/Linux builds
- Redaction or privilege review
- De-duplication beyond exact hash match (no near-duplicate detection in v1)
- Programmatic upload into Claude.ai Projects via session keys or unofficial APIs (excluded on security/ToS grounds; revisit if Anthropic ships an official Projects API)

## 13. Acceptance Criteria

1. Processes the reference MODEC set (38 MPRs, ~20 MB each, mixed native/scanned) end-to-end without manual intervention; output corpus loads into a Claude Project and is consumed by Expert Assist evidence-mining without format errors.
2. Page accounting reconciles to zero discrepancy across the full set.
3. Every page marker in clean_text resolves to the correct page of the original PDF on spot-check (sample: 50 random markers).
4. Bates detection ≥ 99% accuracy on stamped sets; all misses flagged, none silently wrong.
5. Master-index reconciliation correctly categorizes seeded test discrepancies (missing docs, extra docs, mismatched Bates ranges), and Doc ID assignment against the real Project 495 index (9,259 rows) matches every indexable file and issues zero colliding IDs.
6. Runs fully offline (verified with network disabled).
7. Byte-identical outputs on repeat runs with identical inputs, profile, and master index (D-04 determinism contract).
8. Handoff: Path A produces a package accepted by a Claude Project without file-type or size rejections, and Path B's folder structure is consumed by Expert Assist evidence-mining with no manual rearrangement.
9. OCR bake-off (D-01) performed on ~20 representative scanned MPR pages with hand-checked ground truth; engine choice confirmed or swapped on that evidence.

## 14. Open Decisions

All v1.0 open decisions are RULED — see `docs/decisions/decision_register.md` (D-01..D-09). This section is retained for numbering stability only.
