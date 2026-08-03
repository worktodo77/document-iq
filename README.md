# LI Document IQ

**A deterministic document-corpus reducer for AI-assisted forensic analysis.**

LI Document IQ ("DocIQ") is a standalone, fully offline Windows desktop utility that converts large volumes of heterogeneous matter documents (monthly progress reports, correspondence, notices, spreadsheets) into a compact, fully traceable text corpus — the input format required by Long International's Expert Assist skill set (evidence-mining, causation, contradiction-finder).

DocIQ is a **reducer, not an interpreter**. It performs no AI extraction, summarization, or classification of content meaning. Every output is mechanically derived from the source and every transformation is logged; its evidentiary status is equivalent to printing or photocopying.

## Core principles (non-negotiable)

1. **No silent deletion** — every page in equals a page accounted for in the processing log.
2. **Original locators preserved** — page markers reference the original document's pagination; Bates numbers carried alongside.
3. **Expert controls omissions** — all drop rules are created or approved by the expert via versioned format profiles.
4. **Fully offline** — no network access of any kind; all OCR and processing runs locally.
5. **Deterministic and repeatable** — same inputs + same profile = byte-identical outputs; every run hash-logged.

## Documents

- [Requirements v1.1](docs/requirements/requirements_v1.1.md) — **the ruled baseline**, amended in place by decisions D-01 onward. Start here.
- [Decision register](docs/decisions/decision_register.md) — every ruling, and the current value of every measured figure. **The register wins over any other file.**
- [Architecture](docs/architecture.md) · [Section taxonomy](docs/design/section_taxonomy.md) · [Packaging](docs/build/packaging.md)
- [Requirements v1.0](docs/requirements/requirements_v1.0.md) — **superseded historical draft.** It carries the withdrawn "3.4M tokens, fits after reduction" premise and the pre-D-22 packaging wording; do not quote a figure from it.
- [MIP 3.9 ingestion reuse audit](docs/reuse_audit/mip39_ingestion_audit_2026-07-30.md) — §11 audit; decision: **REUSE**.

## Status

**Sprint 2 — integration, packaging and acceptance** (as of 2026-08-03). The
pipeline, the GUI and the packaged build exist and have been run end to end on
the real 368-document record. This section read "Pre-development. Requirements
ingested 2026-07-30 … §14 open decisions pending" until 2026-08-03, and pointed
at v1.0 as authoritative — corrected here rather than left for a reader to
discover.

Acceptance criteria, current — **stated with their limits, because every one of
these has a boundary and a bare "PASS" hides it**:

| # | state | the limit on the claim |
|---|---|---|
| 1, 8 | **discharged** 2026-08-02 | Path B at full scale; **Path A on a deliberately scoped subset** (D-20). "Accepted by a Claude Project" was **not** observed — uploading is a network action Principle 4 forbids |
| 2 | **PASS, real corpus** | 18,556 in = 18,556 kept + 0 dropped |
| 3 | **PASS** | 439/439 markers, judged against a different extractor |
| 5 | **PASS, real index** | 0 collisions, 9,259/9,259 matched |
| **4** | **NOT MET — ships as a known open item (D-29)** | 593/648 = **91.512%** measured full-corpus; **100.000%** native-text, **31.250%** OCR'd; **zero wrong, zero false positives**. Through the shipped GUI it is **0%**, because nothing yet confirms a Bates format |
| 6 | **substantially done, one gap** | zero outbound attempts proven in-process and at OS level; a genuinely **network-adapter-disabled run has not been executed** |
| 7 | **PASS on the fixture corpus; a stratified sample on the real one** | see `docs/verification/claims_sweep_2026-08-03.md` §6 for exactly what the proof covers and what it does not |
| **9** | **CANCELLED, not met** (D-19) | Tesseract written off; the shipped OCR engine has **never been benchmarked against an alternative** on this corpus |

Read the register's D-19, D-20, D-25, D-28 and D-29 entries before quoting any
accuracy figure.
