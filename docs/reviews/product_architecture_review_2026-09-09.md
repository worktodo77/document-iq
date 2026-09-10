# LI DocIQ: product and architecture review

Reviewed September 9, 2026, for Alex Bachowski.

**Historical initial review.** The subsequent interview clarified that token efficiency and citation accuracy are coequal goals, discarded the context-window objective, and added launch SharePoint import/publishing and returned-report verification. Use the [confirmed scope](confirmed_scope_after_interview_2026-09-09.md) and [post-interview audit](scope_governance_architecture_audit_2026-09-09.md) for the current assessment. The synthetic findings below remain applicable; the initial product prioritization and manual-only first rollout are superseded where they conflict with those answers.

**Recommendation:** retain the offline ingestion and provenance foundation, but make the product's primary deliverable a searchable, verifiable evidence collection. Treat reduced text packages as optional, purpose-specific views. The priority Alex identified is reliable, source-backed analysis of project documents.

## Review scope and evidence

Reviewed the local `build/sprint-5` checkout at `5f18928`, its requirements, architecture, decision register, acceptance evidence, recent sprint close-out, extraction, identifiers, output publication, omission handling, and GUI source. GitHub's branch reference matched the local commit; GitHub main was `6e119a5`. The working tree was clean before review. This is a product and targeted architecture review, not an exhaustive security or code audit.

Ran the existing extraction, end-to-end, sections, and document-ID-assignment test files: **167 tests passed**. Separately ran three in-memory synthetic extraction probes described below. No application code was changed. Did not run a real client corpus, operate the packaged GUI, or execute Expert Assist. Historical measurements below remain historical; they were not reproduced in this review.

## Judgment

The concern is justified, but there is useful work to preserve. Default KEEP behavior, original PDF page numbering, structured omission approvals, input/output hashes, master-index reconciliation, and separation of pipeline and GUI are valuable foundations. The Sprint-5 selftest now exercises approved omission and withdrawal, correcting the earlier zero-drop diagnostic gap.

The larger issue is that the product has been optimized around mechanically producing and reducing text, while success now depends on reliably finding, understanding, and verifying evidence across a large record. Those are different acceptance criteria.

The corrected requirements document a roughly 14–15 million-token corpus under stated estimation assumptions. That invalidates the earlier expectation that ordinary section reductions make the entire record fit the project's 200,000-token reference line. Reading files from disk avoids an upload requirement, but does not establish that an analysis process searches all relevant material or notices contradictory evidence.

The acceptance note is unusually candid: its section 9.2 says **Expert Assist itself was not run**. The successful handoff checked layout, source-path resolution, and page-marker formatting. That is a useful interface check, not proof of the combined product's analytical performance. See [the acceptance note](../verification/acceptance_1_8_2026-08-01.md#92-what-path-bs-proof-does-and-does-not-cover).

## Confirmed fidelity problems

These are present-code observations with synthetic reproductions, not estimates of how often the client corpus is affected.

| Priority | Finding | Reproduction and consequence | Recommended correction |
|---|---|---|---|
| High | Mixed-content PDF pages can bypass necessary OCR | A PDF containing a native header longer than 40 characters and an image with substantive text did not request OCR. The image text was absent, and extraction returned no document note about it. | Assess meaningful image regions and native-text coverage within each page. Preserve original imagery and explicitly flag content that has not been represented. Avoid duplicating text when combining native extraction and OCR. |
| High | Word reading order changes | A document ordered paragraph → table → paragraph became paragraph → paragraph → table. This can detach a qualification or explanation from its table. | Traverse paragraphs and tables in document order. Preserve block identifiers and flag unsupported content such as objects that cannot be extracted. |
| High | Excel formulas can disappear without a specific warning | A workbook containing 10, 20, and `=SUM(A2:A3)` without a cached result emitted only 10 and 20. Its note only described synthetic pagination. | Read formulas and cached values separately; retain cell addresses and disclose missing cached results. Do not invent calculated values. |

Code anchors: [PDF extraction](../../src/dociq/ingest/extract.py#L1258), [Word extraction](../../src/dociq/ingest/extract.py#L1399), [Excel extraction](../../src/dociq/ingest/extract.py#L1429).

The PDF probe intercepted the OCR function to record whether it was called; it did not measure OCR accuracy. The result establishes a routing gap, not whether a particular OCR engine would read the image correctly. The Word and Excel probes exercised the actual extractors directly.

All three cases illustrate the same distinction: **accounting for every page does not establish that every material fact on those pages survived extraction.** Byte-identical extraction also does not establish correct interpretation or fidelity. Product language should distinguish those properties. The software tests do not substantiate the requirements' broad comparison with printing or photocopying.

## Recommended product structure

### 1. Preserve the full evidence collection; make reduction a view

Today the clean-text renderer skips DROP pages and `sources.json` maps IDs to the resulting text files. Audit records retain omission information, and the originals are not deleted, but the ordinary downstream text interface contains only the selected material.

Introduce a durable collection containing references to every original, every extracted page/block, extraction limitations, and all omission decisions. Generate named views such as “full record,” “correspondence on issue X,” and “browser export.” Bind each view to its collection version and include a machine-readable account of exclusions.

An analyst studying delay might need a schedule table or progress photograph that was reasonably excluded from a different task. A global section omission should not become the only convenient route into the evidence.

Preserve existing `clean_text/` and `sources.json` exports for compatibility. This is an additive transition; downstream tools can migrate deliberately.

### 2. Add a searchable evidence interface between extraction and analysis

Provide local search over the complete collection, with document/date/section filters, exact phrases, identifiers, and surrounding pages. Each result should return the original locator, source version, extraction status, and any exclusion status in the current view.

Start with full-text search and explicit filters. SQLite FTS5 is a plausible small-footprint option: it supports local full-text indexing, ranked matches, and snippets. Its actual performance and packaging should be tested on the representative corpus before selection. See [SQLite's official FTS5 documentation](https://www.sqlite.org/fts5.html).

Add semantic retrieval only if evaluation identifies important misses that justify it. Neither keyword nor semantic search proves exhaustive coverage. Tasks claiming completeness should traverse a defined scope and record what was processed, failed, and excluded, rather than only returning a few ranked passages.

Expert Assist can remain responsible for interpretation. DocIQ should provide reliable evidence access rather than acquire its own general-purpose AI analysis feature.

### 3. Make citations durable and directly verifiable

A citation should identify the source version and a meaningful location: PDF page and region, Excel worksheet and cell range, Word paragraph/table block, slide, or email attachment. Preserve Bates numbers alongside these locators where available.

The current page model has valuable PDF locators, but synthetic formats are still rendered with generic PAGE markers. A worksheet ordinal or an entire Word document labeled page 1 should not be mistaken for original pagination. See [the page model](../../src/dociq/contracts.py#L437) and [the text renderer](../../src/dociq/emit/cleantext.py#L51).

Separate persistent internal document identity, source-file hash/version, and displayed LI/DIQ number. Unmatched DIQ numbers are currently assigned by a counter over the run's documents; inserting an earlier-sorting document can move later numbers. Reproducibility for an unchanged input set is a different requirement from preserving citations when a matter grows. Maintain aliases and record source occurrences so duplicate files in different productions retain their provenance. See [ID assignment](../../src/dociq/docid/assign.py#L511).

Add a citation-checking interface that resolves a citation, verifies source hashes and quoted text, and opens the original location. Keep “quotation located” distinct from “the evidence supports this claim.” The second judgment still requires contextual review, including qualifications and contrary material.

### 4. Turn warning lists into an actionable evidence-review workspace

The current GUI source includes flag detail lists and omission-family descriptions, but the inspected flag-detail screen renders text labels, not a source comparison and resolution workflow. See [DetailScreen](../../src/dociq/gui/screens.py#L991).

Provide an original-page view beside extracted text, with next/previous flagged item, retry extraction, record a correction, and accept-with-limitation actions. Corrections should be a versioned layer above the original extraction, not silent replacements. An omission preview should show actual affected pages and boundary examples from this matter, not only a family description.

Lead the summary with extraction coverage, unresolved limitations, scope, and citation readiness. Token estimates remain useful for export sizing but should not dominate the product's success message. OCR confidence is an engine score, not a measured probability that an important date, amount, or name is correct.

### 5. Use versioned outputs and a simple single-writer guard

The current staging directory is shared for a matter; `staging_layout()` removes an existing staging directory. Publication then replaces the prior deliverables. The Sprint-4 close-out explicitly records the absence of an inter-process lock. I did not reproduce a concurrent-run failure during this review.

For a durable evidence collection, use unique run directories, validate a run before marking it current, retain the previous valid run, and refuse a second writer to the same matter. Bind analysis and citations to a particular completed run. That makes changes in extraction, evidence, and omission decisions visible and prevents analysis from silently changing underneath an existing report.

This is a proposed change to the currently accepted D-32 design, not a claim that a deferred requirement was secretly implemented or an instruction to reopen it without a scope decision. Favor a small versioned-output design over another elaborate replacement/recovery protocol. Existing resume support is useful; retain it and evaluate cache keys by stage so changing an export selection does not unnecessarily repeat extraction.

## Prove the analyst outcome before expanding the feature list

Create a modest expert-reviewed benchmark, for example 20–30 real questions across a representative set of document types. Include known supporting passages, contradictory evidence, changed dates, negation, tables, scanned content, and cases where the correct answer is “not established.” Keep a holdout set that does not guide implementation.

Measure independently:

| Property | What to establish |
|---|---|
| Extraction fidelity | Important names, dates, quantities, qualifications, and table relationships survive; unsupported material is visible. |
| Retrieval coverage | Known relevant and contradictory passages are found within the declared scope. |
| Citation correctness | Each citation resolves to the intended original and the quoted words agree, allowing only declared normalization. |
| Analytical support | The cited context actually supports the stated claim; a matching phrase alone does not pass. |
| Honest uncertainty | Missing, ambiguous, or conflicting evidence produces an appropriately limited answer. |
| Usability | A colleague can prepare the matter, resolve a flag, inspect a citation, and rerun after a change without a developer. |

Run the actual DocIQ → Expert Assist workflow against this benchmark, first with the full collection and then with proposed reduced views. Measure what reduction saves and which relevant passages it removes. Do not equate a high reduction percentage with improved analysis.

Choose release thresholds with the domain expert before scoring. Existing engineering tests remain necessary, but a growing test count and repeated green runs cannot substitute for these outcome checks. The present synthetic defects alongside a passing selected suite demonstrate why.

## Suggested implementation order

1. **Immediate reliability work:** correct the three reproduced extraction gaps, add regression cases, and build the expert-reviewed benchmark. Complete the already-planned hands-on session with the packaged application. Bring README and architecture documentation into line with the current template-based implementation; they still describe superseded profile machinery and sprint status.
2. **First useful product increment:** add the complete evidence collection, typed source locators, basic local search, and direct original-source inspection. Keep current exports working. Connect one real Expert Assist task to this interface and measure it.
3. **Make ongoing matters dependable:** persistent IDs/version aliases, retained completed runs, single-writer protection, review decisions, and change reporting.
4. **Expand only on evidence:** richer retrieval, additional section recognition, and specialized exports after the benchmark and colleague session identify the limiting factor.

These are ordered increments, not duration estimates. The highest-value next investment is proving faithful extraction and one verifiable analyst workflow. Further omission-taxonomy breadth or token-gauge refinements should follow evidence that they improve that workflow.

## Decisions still to resolve

Alex confirmed reliable, source-backed analysis as the primary outcome. The first analyst task remains to be selected: chronology construction, finding supporting and contradictory evidence for an issue, or checking citations in an existing draft. The proposed foundation serves all three, but the choice should determine the first end-to-end benchmark and user workflow.

No client documents were uploaded or used in the synthetic probes. External research was limited to generic technical documentation. The PDF library's own documentation explains why text extraction and image/OCR interpretation are distinct and why layout is difficult: [pypdf extraction documentation](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md).
