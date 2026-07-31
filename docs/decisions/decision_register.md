# DocIQ Decision Register

Rulings by Alex Bachowski, harvested one-at-a-time per the pre-handoff decision protocol.
Status: D-01 through D-06 RULED 2026-07-30; D-07 OPEN.

| # | Decision | Ruling | Date |
|---|---|---|---|
| D-01 | OCR engine (§3/§10 said Tesseract; reused MIP 3.9 code uses rapidocr) | **rapidocr + Sprint-1 bake-off** — build on rapidocr; empirically compare both engines on ~20 real scanned MPR pages against hand-checked ground truth in Sprint 1; swap to Tesseract only if it wins decisively on the actual corpus. Bake-off report becomes a methodology artifact. | 2026-07-30 |
| D-02 | Legacy .doc support (§14.4) | **Tier 2 for v1, flagged loudly** — .doc files inventoried/hashed on the Unsupported list with a "Save-As DOCX/PDF to include" remediation hint in the run summary. No COM automation, no bundled converter. Promote in v2 if matters demand. | 2026-07-30 |
| D-03 | Token estimator basis (§14.3) | **Calibrated chars-ratio, shown as a range** — ratio calibrated during development against the real Claude tokenizer on representative MPR text (expect ≈3.3–3.6 chars/token for table-heavy content); display a conservative range (e.g. "≈ 82–94K tokens"). No bundled foreign tokenizer. | 2026-07-30 |
| D-04 | Doc ID scheme (§14.2) | **OPEN** — Alex is supplying a sample LI internal document matrix; its numbering will be audited for stability, uniqueness, and rerun-determinism before ruling. Candidate default if unusable: sequential + type prefix (MPR-0001), Bates as index columns. | pending |
| D-05 | Profile library location (§14.1) | **Configurable folder, local default** — default `%APPDATA%\LI DocIQ\profiles`; settings field may point at a shared LI drive path. Per-matter copy in the output folder happens regardless. | 2026-07-30 |
| D-06 | Product name (§14.5) | **LI Document IQ** confirmed (short form "DocIQ"). | 2026-07-30 |
| D-07 | UI design direction (§9) | **Concept C "Counsel docket" + Concept B's capacity gauge** — light editorial chassis: white ground, hairline grid, single cobalt accent, oversized token headline, flags as chips; with the token capacity bar (fill + capacity marker + "% of direct-context capacity" mono caption) under the headline, and the "offline — no network" indicator in the window chrome. Placeholder cobalt/typography until LI brand assets are supplied. Concept sketches approved in-chat 2026-07-30. | 2026-07-30 |

## Amendments to requirements_v1.0 implied by these rulings

- §3 Tier-1 table: "Local OCR (Tesseract)" → "Local OCR (rapidocr, ONNX; per-page confidence recorded)" subject to the D-01 bake-off.
- §3 Tier-1 table: DOC moves to Tier 2 (listed + remediation hint) for v1.
- §10 stack line: Tesseract 5 → rapidocr_onnxruntime (+ PyMuPDF rasterization), pending bake-off.

These amendments will be folded into a requirements v1.1 once D-04 is ruled.
