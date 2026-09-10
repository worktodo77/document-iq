# DocIQ — scope confirmed through Alex's interview

Recorded September 9, 2026. This captures Alex's answers in the current review conversation. It does not claim that these capabilities are implemented. It is the assessment baseline for the accompanying audit, not a silent rewrite of the historical decision register.

## Product purpose

Prepare faithful, traceable, token-efficient project evidence for mining with Claude, and verify quotations, figures, and citations returned by Claude against the original sources.

Two coequal goals:

1. Reduce the tokens needed to perform useful evidence mining without materially degrading evidence coverage or accuracy.
2. Achieve very high citation accuracy through source-bound references, verification, and explicit handling of unresolved evidence.

**Discard the goal of fitting the matter into a model context window.** Token efficiency remains central; context-window occupancy is not a product success criterion.

## Confirmed launch requirements

| ID | Requirement and selected default |
|---|---|
| S-01 | First acceptance deliverable: an issue-specific evidence table with exact quotations, numerical figures, and source citations. |
| S-02 | Coverage is user-selectable. Default: every materially relevant item in the selected document set, including supporting, contradictory, and qualifying evidence; disclose unreviewed material. A strongest-examples mode is also available and must not claim comprehensive coverage. |
| S-03 | Evidence with an unverified citation goes to a separate review queue and is excluded from the verified evidence table until resolved. |
| S-04 | Citations identify the source document and exact original location: PDF page, spreadsheet worksheet/cells, or Word paragraph/table. Include printed page, section, and Bates numbers when reliably verified. Different numbering systems must remain distinguishable. |
| S-05 | Token-reduction behavior is user-selectable. Default: compact formatting and repeated material while preserving substantive content. Substantive exclusions require explicit approval. |
| S-06 | Support local matter folders used through Claude Code/Cowork, browser Project uploads, and SharePoint access through corporate Claude accounts. Each route needs acceptance evidence; none substitutes for the others. |
| S-07 | Support both manual upload of prepared packages and direct, user-initiated SharePoint import and publishing at launch. |
| S-08 | Prepare citation-ready evidence before mining and verify returned tables/reports against originals afterward. |
| S-09 | For numerical figures, verify value, units, date/period, and row/column context. For calculated figures, identify source inputs and formula and independently recalculate. Unsupported or ambiguous calculations remain unresolved. |
| S-10 | Default: automatically check citations/calculations and separately flag possible misinterpretations for analyst review. Users can require analyst approval of every entry when the assignment demands it. |
| S-11 | The core workflow requires no Anthropic API key. Optional company-managed API integration for interpretive checks can follow launch. Existing corporate Claude interactions and imported results must remain a supported route. |
| S-12 | Preserve the exact source version supporting each table. Flag new or changed documents; update an analysis only when its user deliberately chooses. |
| S-13 | Launch format families: native and scanned PDFs, Word, Excel, and emails with attachments. Identify other formats for future builds. Unsupported content within a supported file must be disclosed. |
| S-14 | Continue processing when content cannot be extracted reliably. Queue affected content for review and disclose coverage gaps. Resolution or explicit acceptance of the limitation is required before presenting the table as verified, subject to the separate row-level rule in S-03. |
| S-15 | Launch target: thousands of documents and approximately 100,000 pages. Design for eventual productions with hundreds of thousands of documents or millions of pages. Distinguish demonstrated launch capacity from future scalability. |
| S-16 | One person prepares and updates a matter collection. Multiple colleagues independently mine its published version. A shared, simultaneous reviewer workspace is not required at launch. |
| S-17 | Every citation labeled verified must pass applicable location, quotation, and numerical checks. Unresolved entries remain separate. Also measure relevant-evidence coverage and the proportion successfully verified so rejecting difficult material cannot create a misleading success rate. |
| S-18 | Evaluate token efficiency by comparing Claude mining original documents against DocIQ outputs on the same questions. Measure token use, evidence coverage, and citation accuracy. Set savings targets after measuring a baseline. |
| S-19 | Compare incremental improvement, substantial architectural change, and a fresh implementation with an open mind. Preserve sound work when justified; no implementation option is mandated in advance. |
| S-20 | Publishing must preserve source access restrictions. Only publish where intended recipients are authorized for all included sources; separate packages when source permissions differ. |
| S-21 | Default return format: a standard Excel/CSV evidence table with dedicated fields. Also accept ordinary Word reports and pasted Claude responses at launch; identify claims/citations for verification and expose parsing uncertainty. |
| S-22 | Also support reports and source collections DocIQ did not originally prepare. Users supply the report and source collection; DocIQ indexes the latter and separates unverifiable items. |
| S-23 | Automatically correct a citation when its quotation has one exact match in the selected source version; record the correction. Ambiguous or approximate matches require approval. A location correction is not proof that the claim is supported. |
| S-24 | Flag uncited quotations, numerical claims, and other factual assertions for review, without claiming exhaustive detection. |
| S-25 | Acceptance uses both an existing report/table with known citation mistakes and its source documents, and the Petrobras/MODEC corpus with selected mining questions. |

## Explicitly deferred

Automatic SharePoint monitoring, background preparation of updates, and automatic publishing are **future-version work**, following Alex's later instruction to defer auto-updates because of extensive testing needs. This supersedes the earlier launch selection of monitoring.

For that future version, the preferred design is a company-managed service that can operate independently of an analyst's computer. Updates may publish automatically only if all checks pass and no new review items arise; otherwise the matter owner must approve. New collection publication must not silently rebind existing evidence tables to different source versions.

Direct SharePoint import and publishing initiated by a user remain launch requirements. Optional API-based interpretation is also deferred and must not become a prerequisite for the core product.

## Clarifications needed to implement the confirmed scope faithfully

These are implementation recommendations or unresolved details, not additional answers attributed to Alex:

- **Verification is multidimensional.** An accepted coverage limitation does not pass an unresolved citation. A table can state “citation checks passed for included rows; coverage limited; limitation accepted by [reviewer].” Claim support and human approval must have their own statuses.
- **No-API interpretation.** Local checks can establish locations, strings, and supported calculations. Without optional AI integration, broad claim detection and support assessment need heuristics, imported Claude assessments, and/or analyst review; they must never be advertised as exhaustive semantic verification.
- **Exact quotations.** Define permitted normalization and the treatment of ellipses, brackets, partial quotations, OCR uncertainty, and translations before coding the verifier. Normalization must not erase a decimal, minus sign, unit, negation, or qualifier.
- **Format editions.** Word/Excel support is confirmed at the family level. Specify legacy `.doc`, `.xls`, macro-enabled files, password-protected documents, external workbook links, email archives, and embedded objects in the release format matrix. The existing `.doc` limitation cannot remain hidden beneath “Word supported.”
- **Source-version retention.** Hashes identify bytes but do not retain them. Determine approved storage and retention for the exact original bytes, including imported email attachments and SharePoint versions. Access checks still apply when inspecting a historical snapshot.
- **Permissions after publication.** Direct publishing can check destination access. A manually copied package cannot technically prevent later unauthorized redistribution or guarantee revocation of already downloaded copies. Document this boundary and use approved destinations; do not claim continuing access enforcement over arbitrary copies.
- **Scale and speed.** No maximum duration, hardware baseline, memory budget, or verified throughput target was selected. Measure representative work, then set the release envelope. A 100,000-page fixture without realistic tables, scans, and attachments is not sufficient evidence.
- **Acceptance inputs.** The existing report with known mistakes and its source set have not yet been identified by path in this conversation. Neither the exact mining issues nor expert answer keys have been selected. The audit can be completed without these; outcome acceptance cannot.

## What this replaces in the prior framing

The old “fully offline deterministic reducer, no network, no interpretation” description remains useful history but is no longer a sufficient product definition. Preserve a local deterministic preparation/checking core, with explicitly controlled SharePoint exchange and separate interpretive review.

The earlier review's suggestion to demote token efficiency was corrected in the interview: token efficiency is a central objective. Its recommended first rollout of manual SharePoint publishing was also superseded: direct user-initiated import and publishing are required at launch.

This scope authorizes assessment and recommendations in this task. It does not itself install dependencies, connect accounts, upload client files, deploy services, commit changes, or rewrite prior approved records.
