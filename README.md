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

- [Requirements v1.0](docs/requirements/requirements_v1.0.md) — the authoritative product specification.
- [MIP 3.9 ingestion reuse audit](docs/reuse_audit/mip39_ingestion_audit_2026-07-30.md) — §11 audit; decision: **REUSE**.

## Status

Pre-development. Requirements ingested 2026-07-30; ingestion-layer reuse audit complete; §14 open decisions pending.
