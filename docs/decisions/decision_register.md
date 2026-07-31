# DocIQ Decision Register

Rulings by Alex Bachowski, harvested one-at-a-time per the pre-handoff decision protocol.
Status: D-01 through D-06 RULED 2026-07-30; D-07 OPEN.

| # | Decision | Ruling | Date |
|---|---|---|---|
| D-01 | OCR engine (§3/§10 said Tesseract; reused MIP 3.9 code uses rapidocr) | **rapidocr + Sprint-1 bake-off** — build on rapidocr; empirically compare both engines on ~20 real scanned MPR pages against hand-checked ground truth in Sprint 1; swap to Tesseract only if it wins decisively on the actual corpus. Bake-off report becomes a methodology artifact. | 2026-07-30 |
| D-02 | Legacy .doc support (§14.4) | **Tier 2 for v1, flagged loudly** — .doc files inventoried/hashed on the Unsupported list with a "Save-As DOCX/PDF to include" remediation hint in the run summary. No COM automation, no bundled converter. Promote in v2 if matters demand. | 2026-07-30 |
| D-03 | Token estimator basis (§14.3) | **Calibrated chars-ratio, shown as a range** — ratio calibrated during development against the real Claude tokenizer on representative MPR text (expect ≈3.3–3.6 chars/token for table-heavy content); display a conservative range (e.g. "≈ 82–94K tokens"). No bundled foreign tokenizer. | 2026-07-30 |
| D-04 | Doc ID scheme (§14.2) | **Adopt the LI File No. when a master index is supplied** (ruled against recommendation, with mitigations). Audited on the real Project 495 index (9,259 rows): "Original Sort" is a perfect 1..9,259 sequence (0 gaps/dups); filepath+filename is 100% unique (filename alone is NOT — 693 dups); NO Bates column. Scheme: Doc ID = `LI-<zero-padded Original Sort>` for rows matched to the master index (match key: filepath+filename primary, hash secondary, Bates when present); container children (zip/RAR members, email attachments — no LI row exists) get parent-derived IDs (`LI-06881.01`); unmatched folder files get a distinct `DIQ-` synthetic range (never collides with LI numeric space). Mitigations binding on the build: (a) the master index is a HASHED RUN INPUT — determinism contract becomes "same folder + same profile + same index = byte-identical"; running without the index yields DIQ-native IDs and the log records which regime; (b) processing_log records the index snapshot filename+hash; reconciliation WARNS if a later snapshot renumbers IDs already issued. | 2026-07-30 |
| D-05 | Profile library location (§14.1) | **Configurable folder, local default** — default `%APPDATA%\LI DocIQ\profiles`; settings field may point at a shared LI drive path. Per-matter copy in the output folder happens regardless. | 2026-07-30 |
| D-06 | Product name (§14.5) | **LI Document IQ** confirmed (short form "DocIQ"). | 2026-07-30 |
| D-07 | UI design direction (§9) | **Concept C "Counsel docket" + Concept B's capacity gauge** — light editorial chassis: white ground, hairline grid, oversized token headline, flags as chips; with the token capacity bar (fill + capacity marker + "% of direct-context capacity" mono caption) under the headline, and the "offline — no network" indicator in the window chrome. Accent = LI light blue #2E9FD4 with navy #0E4D80 structure (brand art supplied 2026-07-30); LONG INTERNATIONAL wordmark (`assets/branding/li_logo.png`) drawn in the window header, PDF Cleaner-style. Concept sketches approved in-chat 2026-07-30. | 2026-07-30 |
| D-08 | Explorer icon concept | **Concept 2 "Fanned corpus stack"** — LI monogram tile (white monogram + globe on navy, per family grammar) with a fanned three-page stack overlapping lower-right, front page carrying a light-blue "IQ" tab. Generated from `assets/branding/li_monogram_source.png` via the PDF Cleaner `make_brand_icon.py` recipe (multi-size .ico 16–256 px + preview ladder). Below 64 px the overlay simplifies per the family's small-size rule — collapse the fan to a single page silhouette, no lettering — judged on the ladder, same silhouette and color blocking throughout. | 2026-07-30 |

| D-09 | GUI logo lockup | **L1 "App badge lockup"** — the D-08 icon tile (fanned corpus stack, high-res render from the icon generator) at left of the window header, beside "Document IQ" (navy, "IQ" in light blue) over a letterspaced LONG INTERNATIONAL caption. One mark shared across Explorer, taskbar, and window header. Composed deterministically from `li_monogram_source.png` + typeset name; rendered to a shipped PNG in `assets/branding/`; nothing hand-drawn, brand refresh = re-run. | 2026-07-30 |

## Amendments to requirements_v1.0 implied by these rulings

- §3 Tier-1 table: "Local OCR (Tesseract)" → "Local OCR (rapidocr, ONNX; per-page confidence recorded)" subject to the D-01 bake-off.
- §3 Tier-1 table: DOC moves to Tier 2 (listed + remediation hint) for v1.
- §10 stack line: Tesseract 5 → rapidocr_onnxruntime (+ PyMuPDF rasterization), pending bake-off.

These amendments will be folded into a requirements v1.1 once D-04 is ruled.
