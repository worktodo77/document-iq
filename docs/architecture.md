# DocIQ Architecture & Sprint Roadmap (v1)

Drafted 2026-07-30 against requirements v1.1 (ruled baseline) and the decision
register D-01..D-09. Status: awaiting Alex's approval — no build code until then.

## 1. Shape of the system

A single Python package `src/dociq/` with a strict pipeline core (pure, GUI-free,
deterministic) and a thin PySide6 shell. The pipeline core is testable end-to-end
without any UI; the GUI only orchestrates and displays. This mirrors the
LI PDF Cleaner / Schedule IQ layering and keeps the "load-bearing wall" (§11)
independently provable.

```
src/dociq/
  ingest/     extract.py   — vendored+adapted mip39 docs_extract (REUSE per audit)
              walker.py    — folder walk, pool fan-out, resume, watchdog (from ingest_folder)
              pagemodel.py — per-page record: (page_no, text, kind native|ocr, ocr_conf)
  identify/   bates.py     — corner/footer Bates pattern detection + user confirmation flow
              dating.py    — document date detection (from doc_census patterns)
  docid/      masterindex.py — LI index loader (xlsx/csv) + hashed-snapshot record
              assign.py      — Stage 3b: LI-/child-/DIQ- ID assignment, collision-proof
  profiles/   model.py     — YAML profile schema, versioning, operator stamp
              detect.py    — candidate section-header detection across a sample
              apply.py     — KEEP/DROP engine with per-drop log entries
  emit/       cleantext.py — ===== PAGE n [BATES: …] ===== writer (original page numbers)
              indexbook.py — document_index.xlsx/.csv + reconciliation tab (openpyxl)
              log.py       — processing_log.json
              summary.py   — run_summary.pdf (reportlab, LI-branded)
              handoff.py   — upload_package builder, README generator, Cowork launch
  verify/     accounting.py — pages in = kept + dropped, zero-discrepancy gate
              manifest.py   — output hash manifest
              tokens.py     — calibrated chars-ratio estimate, range display
  gui/        app.py, screens/ — PySide6, D-07 "Counsel docket" design system
  branding/   make_icon.py, make_logo.py — generators adapted from Cleaner recipe
  selftest.py — end-to-end self test on a bundled synthetic fixture set (exit 0 gate)
```

### Key adaptation of the reused extractor

mip39's `extract_text()` returns one joined string with inline `[page N]`
markers. DocIQ needs per-page OCR confidence (§4 Stage 2) and per-page
KEEP/DROP (§4 Stage 4), so the vendored extractor is refactored to return a
list of page records; marker text is rendered only at the emit layer. The
hybrid native/OCR routing, content-sniffing recovery, and defensive guards are
kept verbatim. The vendored copy lives in-repo (no dependency on mip39-tool)
with its full dependency set declared — closing the undeclared-deps gap from
the reuse audit.

### Determinism spine (Principle 5, D-04)

- One canonical serializer for anything hashed or persisted; sorted keys; LF
  newlines; UTF-8; no floats in identity fields.
- Stable orders everywhere: files sorted by (relative path, SHA-256); zip/RAR
  members by archive order; child IDs by that order.
- rapidocr is seeded/thread-pinned; the bake-off (D-01) must also prove OCR
  output stability across ≥8 identical runs — if the engine is nondeterministic,
  OCR results are cached by (file hash, page, engine version) so reruns replay.
- Precision of the byte-identical claim: `clean_text/*`, `sources.json`,
  `document_index.csv`, and the log's `content` section are byte-identical;
  run timestamp + operator live in a separate `run` section excluded from the
  content hash. The hash manifest states this split explicitly.

### Client-data hygiene

Real matter documents and real LI indexes never enter the repo (gitignore
already blocks `matter_data/`, `test_matters/`, `*.msg`, `*.eml`). Tests run on
synthetic fixtures generated in-repo; acceptance runs on real sets happen
outside the repo tree with only summary numbers quoted in docs.

## 2. Sprint roadmap (velocity-revised 2026-07-30, ruling D-10: 2 sprints,
## 3 parallel tracks, 2 Codex reviews)

**Contract-first rule.** `pagemodel.py` (the per-page record dataclass) and the
pipeline stage interfaces are written and frozen on day one of Sprint 1. All
three tracks build against that contract in separate worktrees; only Track A
implements it for real. Any contract change after freeze is a cross-track
stop-the-line event, not a local edit.

**Sprint 1 — everything but final assembly (3 concurrent tracks).**

- **Track A — Ingestion spine** (load-bearing wall; deepest critic rounds):
  vendored extractor → per-page records + OCR confidence, walker, Stages 1–2,
  accounting, determinism proof (≥8 identical runs), selftest harness, and the
  D-01 OCR bake-off (timeboxed; runs in parallel — routing is engine-agnostic).
- **Track B — Identity & deliverables** (against the frozen contract + stub
  fixtures): Bates detection + confirmation flow; master-index loading +
  Stage 3b ID assignment with local acceptance against the real 9,259-row
  Project 495 index; reconciliation tab; profiles (model/detect/apply) with
  per-drop logging; clean_text/index/log/summary emitters; token-ratio
  calibration (dev-time, against the real Claude tokenizer).
- **Track C — GUI shell + branding**: PySide6 screens per the approved D-07
  sketches against a mocked pipeline API; icon + logo generators (D-08/D-09).

Sprint-1 exit gate: tracks integrated on the sprint branch, selftest exit 0,
a real mixed native/scanned set reduces to a zero-discrepancy byte-identical
corpus, acceptance criteria 2–5 pass on seeded fixtures + the real index,
bake-off ruling folded → **Codex review #1** (whole pipeline core).

**Sprint 2 — integration, packaging, acceptance.**
Real-pipeline wiring under the GUI; profiling checklist UI live; Analyze-in-
Claude Paths A/B; PyInstaller single exe with bundled ONNX models; offline
verification (network disabled); full MODEC end-to-end acceptance run →
**Codex review #2 = merge gate**, merge on Alex's authorization. Exit gate:
acceptance criteria 1, 6–9.

**Scope trims backing the cut (D-10, evidence: Project 495 index, 9,259 rows):**
RTF demoted to Tier 2 (zero RTF files in the audited record); RAR added to the
Tier 2 listed-only set alongside XER/MPP/DWG (9 occurrences, listed + hashed,
never blocks).

Build mechanics per standing rules: Opus subagents implement spec'd packages,
Sonnet critics run adversarial review loops until findings dry up, class-scope
enumeration on every fix package, no green-run claims without repetition
(≥8 runs; 30 for anything timing/ordering-sensitive). Codex reviews land at
sprint boundaries only, via tracked relay files under `docs/reviews/`.
