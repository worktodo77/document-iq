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

## 2. Sprint roadmap (3 sprints — velocity mandate; Codex at sprint ends only)

**Sprint 1 — Ingestion core + determinism spine.**
Vendor and adapt the extractor (per-page records, confidence capture), walker,
Stages 1–2, clean_text + sources.json + accounting + minimal log, selftest
harness, determinism proof (≥8 identical runs), and the D-01 OCR bake-off
(~20 real scanned MPR pages, ground-truthed, report committed). Also the two
branding generators (icon + logo lockup) since they're mechanical and unblock
GUI work. Exit gate: a real mixed native/scanned PDF set reduces to a
zero-discrepancy corpus, byte-identical on rerun; bake-off ruling folded.

**Sprint 2 — Identification, IDs, reduction, deliverables.**
Bates detection + confirmation flow; master-index loading + Stage 3b ID
assignment (acceptance against the real 9,259-row Project 495 index, run
locally); reconciliation tab; profile model + section detection + KEEP/DROP
with per-drop logging; document_index.xlsx/csv; token-ratio calibration
(against the real Claude tokenizer, dev-time only); run_summary.pdf. Exit
gate: acceptance criteria 2–5 pass on seeded discrepancy fixtures + the real
index; v1.1 §13.5 zero colliding IDs.

**Sprint 3 — GUI, handoff, packaging, acceptance.**
PySide6 app per D-07 (folder picker → profile → index → run → progress →
summary with capacity gauge), profiling checklist UI, Analyze-in-Claude Paths
A/B, PyInstaller single exe with bundled ONNX models, offline verification
(network disabled), full MODEC end-to-end acceptance, Codex review relay,
merge on Alex's authorization. Exit gate: acceptance criteria 1, 6–9.

Build mechanics per standing rules: Opus subagents implement spec'd packages,
Sonnet critics run adversarial review loops until findings dry up, class-scope
enumeration on every fix package, no green-run claims without repetition
(≥8 runs; 30 for anything timing/ordering-sensitive). Codex reviews land at
sprint boundaries only, via tracked relay files under `docs/reviews/`.
