# Sprint-1 integration — verification record

**Date:** 2026-07-30/31 · **Branch:** `build/sprint-1` · **Contract:** 1.2.0
(unchanged — nothing here needed an amendment)

What the integration closed, how it was proven, and what it did **not** prove.
Client-derived material is referenced by summary number only and is never
committed.

---

## 1. The stand-in emitter is retired

`src/dociq/verify/probe_emit.py` is **deleted**, not merged. It was Track A's
provisional writer of `clean_text/*`, `sources.json`, `document_index.csv` and
the log — built because the real emit layer lived in another worktree, and
because a determinism proof that skipped the four named artifacts would have
proven nothing about the claim. Its own docstring said it must not outlive the
thing it stood in for.

**The consequence that mattered:** until integration, the byte-identical claim
and the determinism proof covered the stand-in, not the emitters that ship. That
is a materially weaker claim than the sprint gate needs, and no amount of green
on the old proof would have made it the right one.

### What replaced it

A single orchestration, `src/dociq/pipeline.py`, running §4's six stages:

| stage | module |
|---|---|
| 1-2 walk, extract, normalize | `ingest/walker.py` |
| 3 Bates detection | `identify/bates.py` |
| 3b Doc ID assignment + §5 reconciliation | `docid/assign.py`, `docid/reconcile.py` |
| 4 section KEEP/DROP | `profiles/apply.py` |
| 5 emit | `emit/cleantext.py`, `indexbook.py`, `log.py`, `summary.py`, `handoff.py`, `paths.py` |
| 6 verify | `verify/accounting.py`, `verify/manifest.py`, `verify/tokens.py` |

`selftest.py` and `verify/determinism.py` both run it. There is deliberately one
orchestration: a second would be a second definition of what a run *is*, and the
two would drift — the same argument the contract makes for one serializer.

## 2. Determinism, re-proven against the real emitters

Harness unchanged in shape: each repetition runs the whole pipeline in a
**subprocess** with a distinct `PYTHONHASHSEED`, because the variable is read
once at interpreter start and setting it in-process would make the varied-seed
claim unfalsifiable. Comparison is over the output manifest.

- **8 runs, 8 distinct seeds** — in the self-test, on every invocation. Result:
  1 distinct corpus hash, 0 diffs, 42 checks passed, exit 0.
- **30 runs, 30 distinct `PYTHONHASHSEED` values, subprocess per run** — result:
  **1 distinct corpus hash, 0 diffs, 0 failures**, `corpus_sha256`
  `f3e297707c0f03a55ec0792d294639924ea8fd6030a16211e678047fa6f22330`.
  Re-run against the final tree after the last two fixes landed; an earlier
  30-run sweep mid-integration was equally clean but is superseded, because a
  determinism proof of a tree that no longer exists proves nothing about the one
  that does.

**A `corpus_sha256` is not portable between source folders, by design.** It
folds in the log's content hash, which contains `source_root` — and the folder
*is* one of the determinism contract's three inputs. Two runs over the same
corpus at two paths therefore differ, correctly. Compare hashes within a run
set, never across machines or across temporary fixture directories. Track A's
recorded `b587bb6a…` does not carry forward for that reason as well as because
it was the stand-in's output.

### Fail-before: the gate has been watched going red

A gate nobody has seen fail is not a gate. Three probes, each injecting one
run-varying byte into one artifact of the **real** emit layer, each run at 3
repetitions, with the tree restored afterwards and the baseline hash re-proven:

| probe | artifact | result |
|---|---|---|
| a timestamp appended in `emit/cleantext.render_document` | `clean_text/*.txt` — inside the claim | **RED**, naming the file and the log's content hash |
| a varying key added to the log's `content` | `processing_log.json[content]` — inside the claim | **RED**, naming the content hash |
| a timestamp appended by `IssuedIdLedger.write` | `doc_ids_issued.json` — adjacent, outside the claim | **RED**, and labelled "outside the four-artifact claim, still a finding" |

Baseline before the probes and after restoration: `ok=True`, 1 distinct hash,
same value both times.

### The claim, stated precisely (unchanged)

Inside: `clean_text/*.txt`, `sources.json`, `document_index.csv`, and the
`content` section of `processing_log.json`. Outside, with the reason recorded in
`output_manifest.json`: the log's `run` section, `run_summary.pdf` (embeds a
generation timestamp), `document_index.xlsx` (the container embeds a creation
time).

### One thing the integration had to add: `adjacent`

The full pipeline writes deliverables the freeze did not name —
`reconciliation.csv`, `doc_ids_issued.json`, the matter profile copy, and the
Path-A `upload_package/`. Under the old two-bucket manifest every one of them
would have been reported `unclassified`, and the self-test's "every output is
classified" check would have gone red.

Neither available answer was honest: they are reproducible, so "excluded because
it cannot be byte-identical" is false; and folding them into the claim would
widen a frozen claim quietly. They are therefore hashed, compared between runs,
and kept out of `corpus_sha256`. A difference in one is reported as a finding.
The four-artifact claim is unchanged, and the freeze document says so.

## 3. Defects found and fixed

### D-I1 — the assigner never remapped `parent_doc_id` (the cross-track item)

Track A sets `parent_doc_id` to the parent's `rel_path`, because Doc IDs do not
exist before Stage 3b, and its verification record §5 asked Track B's assigner to
remap it. Track B's integration note §1 said the assigner "resolves" both
conventions — and it does, for the purpose of *finding* the parent. It did not
rewrite the field.

So the emitted records, the index deliverable and the processing log all shipped
a filesystem path in the "Parent doc" column while every other identifier column
shipped a Doc ID. Nothing resolved one against the other.

Fixed in `docid/assign._assign_children`, at the one moment both halves exist.
*Fail-before:* three new tests in `tests/test_docid_assign.py` red before the
fix, green after; a fourth (idempotence over an already-assigned corpus) was
green both times and is recorded as an already-correct member of the class.

**Sibling enumeration — every reader and writer of `parent_doc_id`:**

| site | status |
|---|---|
| `ingest/walker._record` (archive members) | correct — the producer, and the convention is now written down |
| `ingest/extract` (email attachments) | correct — same producer path, proven separately end to end |
| `docid/assign._assign_children` | **fixed** — performs the remap |
| `docid/assign` orphan / cycle-detached members | **fixed** — `parent_doc_id` cleared to `None`, so the record agrees with the warning that calls them top-level files |
| `docid/assign._pre_assignment_token` | correct, and both branches are now load-bearing — after the remap a re-assigned corpus names its parent by Doc ID |
| `verify/accounting._check_document` | **fixed** — the orphan check accepts both forms, because the gate runs on both sides of Stage 3b |
| `docid/reconcile.reconcile` | correct — tests only for presence; now consistent, because detached members no longer claim a parent |
| `emit/indexbook.build_index_rows` | correct — reads the field; now emits a Doc ID |
| `emit/log._document_entry` | correct — same |
| `contracts.DocumentRecord.validate` | correct — enforces `container_order` alongside a parent, unchanged |
| `selftest` | **strengthened** — asserts every member's parent resolves to a Doc ID in the run |

The contract docstring is deliberately **not** amended. The field is a string and
holds one under either convention; what was underspecified is the handover point,
which is a coordination rule. It is recorded in `pagemodel_freeze.md`.

### D-I3 — D-04's renumbering check cried wolf on any corpus with duplicate content

`detect_renumbering` keyed the previous run's ledger by SHA-256 alone. Duplicate
content is ordinary on a matter record — the walker detects and reports it, and a
file that also appears inside an archive is the same bytes at two paths — so one
twin overwrote the other in the dict and every other twin read as "this file's
identifier changed".

**Measured:** two consecutive, identical runs over the fixture corpus produced
**three phantom `id-moved` warnings**, telling the operator that identifiers had
moved when nothing had changed at all. D-04 accepted renumbering as its single
biggest risk and gave it a loud check; a check that fires on every re-run is a
check people learn to ignore.

Matching is now `(sha256, rel_path)` first, falling back to SHA-256 alone only
where that hash names exactly one previous file. Where twins are genuinely
ambiguous nothing is reported, because guessing would manufacture the warning
the pass exists to avoid. A real move with an unambiguous hash is still
reported, and has its own test so the fix cannot buy silence by refusing to look.
*Fail-before:* both anchors reverted → red; restored → green.

### D-I4 — the byte-identical claim was hostage to the destination folder

Renumbering warnings were written into the log's **hashed** `content`. They are a
comparison against a ledger that the *destination* happens to hold, and the
destination is not one of the determinism contract's inputs — the same finding
D-A1 already made about `output_root`. A corrupt leftover ledger would have
broken the byte-identical claim with no input change anywhere.

Moved to the log's `run` section, still written, still in `RunResult.warnings`
and still on the run summary. *Fail-before:* a test that plants a corrupt ledger
in the destination, asserts the `ledger-unusable` warning is raised, and asserts
the content hash still equals a clean run's — red with the section back in
`content`.

### D-I5 — a re-run into a used output folder inherited the previous run's residue

Both the manifest and the log's `output_hashes` were built by globbing the output
root. A `clean_text/LI-06881.txt` left behind by a run against an older master
index would therefore be hashed as if this run had produced it — and it would
still be sitting in the folder Expert Assist reads, under an identifier this run
gave to a different document.

Two fixes, because there are two failures: the log now hashes an explicit list of
what this run wrote, and the pipeline purges the previous run's deliverables
before emit. The purge is recorded in the log's `run` section — nothing is
deleted silently — and `doc_ids_issued.json` is deliberately **not** purged,
because it is this run's input to the D-04 check. *Fail-before:* a planted ghost
file survives and changes `corpus_sha256` with the purge disabled.

### D-I2 — renumbering warnings reached the log but not the run result

D-04 mitigation (b) detects that a newer index snapshot has renumbered issued
identifiers. The first draft of the pipeline ran the check inside the emit block,
after `RunResult` had been assembled, so the warnings reached
`processing_log.json` and not `RunResult.warnings` — the summary screen and the
audit trail would have told an operator different things about whether
identifiers moved. The check now runs before the result is assembled; only the
ledger *write* stays in emit, and it necessarily happens after the previous
ledger is read.

### D-I6 — the fixture corpus never exercised email-attachment expansion

Track A's critic added attachment expansion to the walker (§3 requires it: an
attachment that vanishes is a silent deletion). The unit test covers it. The
**corpus** did not: `09_notice.eml` carries no attachment, so the self-test, the
determinism proof and every end-to-end assertion ran past the path entirely.

"The corpus doesn't exercise it" is not a reason to leave a path uncovered — it
is a reason to put the case in the corpus. `14_transmittal.eml` now carries a
PDF attachment, with its MIME boundary and headers written by hand for the same
reason the other email is: a library's own boundary and Message-ID generation is
clock- and random-seeded, and these bytes must be identical on every
regeneration. The attachment case is now inside the 30-run determinism proof.

## 4. The amended contract fields are populated

`RunResult.tokens_before`, `tokens_after` and `reconciliation` exist because
Track C raised amendments A-01/A-02 under the stop-the-line rule. They were
`None` everywhere until integration.

- **`tokens_before` / `tokens_after`** come from `verify/tokens.py` — before is
  every page, after is the KEEP pages. `floor_tokens` carries the measured
  pre-token count, which is a hard lower bound for any byte-level BPE tokenizer
  and is the figure to display where only one can be shown.
- **`provenance`** is assembled from what the run actually did, not quoted from
  a constant: it repeats that no tokenizer was run (D-03's "calibrated against
  the real Claude tokenizer" cannot be honored offline), and it states this run's
  measured characters, pre-tokens and reported range.
- **`ratio_refuted`** is copied from the estimator's own verdict. The contract
  says a consumer must never infer it; the only way to keep that true is for the
  producer to be the one place it is decided, and a test asserts the field equals
  what `estimate_for_texts` returned rather than a re-derivation.
- **`reconciliation`** is the A-02 projection of the §5 report, and is `None`
  — not an empty report — when no master index was supplied.

## 5. Full-corpus run (D-12 corpus)

**It finished.** 298 PDF / 53 DOCX / 17 PPTX / 7 DOC, 2.6 GB, at
`Desktop\Petrobras\Petrobras\Project FIles`. Outputs were written outside the
repository; no client text is quoted anywhere here or in the repo.

### What it took — three attempts, and the middle one is a finding

| attempt | configuration | outcome |
|---|---|---|
| 1 | shipped defaults | **62 of 368 documents (6,892 pages) in 3,109 s**, then stopped by hand. 173 OS threads, ~12.5 of 32 cores busy. Part of this window overlapped the test suite and the 30-run determinism sweep, so it is an upper bound rather than a clean benchmark |
| 2 | `DOCIQ_WORKERS=4`, `DOCIQ_OCR_WORKERS=6` | **worse** — 142 threads at ~2 cores busy, 2 documents in 542 s. Stopped after ~10 min |
| 3 | defaults + `DOCIQ_FILE_TIMEOUT=14400`, resumed from attempt 1's journal | **COMPLETED in 2,874 s (47.9 min)**, nothing else running |

Attempt 2 is worth recording because it *refutes* something. Track A left thread
oversubscription as an unquantified hypothesis with an obvious-looking remedy;
cutting the worker counts made throughput roughly six times worse. Whatever
limits this workload, it is not the number of document workers, and the remedy
should not be applied on the strength of the thread count alone.

The raised file timeout was necessary, and that is a finding of its own: two
documents exceeded the shipped 3,600 s per-file limit (`CER-1-135.pdf` ran 3,100 s
in attempt 1 without finishing, `CER-1-113.pdf` longer still). Under the shipped
default they would have been **abandoned** — recorded as FAILED with a message,
never silently, but abandoned. The default is too tight for this corpus.

**A from-scratch full-corpus wall clock is therefore NOT established.** Attempt 3
replayed 62 documents from attempt 1's journal for free. Adding attempt 1's
3,109 s of extraction to attempt 3's 2,874 s gives **≈ 100 minutes** as the best
available figure, and attempt 1 ran under concurrent load. It is not restated as
a clean benchmark and it must not be quoted as one.

### What the run produced

| quantity | measured |
|---|---|
| documents | 368 |
| unsupported (Tier 2) | 7 — all `.doc`, matching D-12 exactly |
| pages in / kept / dropped | **18,521 / 18,521 / 0** |
| page kinds | native 17,271 · synthetic 710 · **OCR 400** · empty 140 |
| pages by format | `.pdf` **17,732** · `.pptx` 737 · `.docx` 52 |
| documents FAILED | 2, both with an actionable message; neither blocked the run |
| documents flagged partial-OCR | 21 |
| warnings | 36 — 30 duplicate-content groups, 2 extraction failures, 1 resume note, 1 Bates note |
| manifest | 370 deterministic files, 372 adjacent, **0 unclassified** |
| `corpus_sha256` | `1c71c9de990715a5ff8857a3e9311c2ab7d6743e179b2858084e92b5f4ca61c6` |
| log `content_sha256` | `c54ff42e05d13bded1967a7e8600808807c0e958027e8e63586da606c167efd2` |

The `.pdf` page count reproduces D-12's independently measured 17,732 exactly —
the two counts were taken by different code on different days, so the agreement
is worth something.

The two failures were a PowerPoint file with a malformed XML namespace and a
`.docx` that is not a zip container. Both were content-sniffed and retried
through the other office readers before being recorded, both carry a message an
operator can act on, and the other 366 documents completed regardless. That is
§3's "never blocks a run" behaving as specified on real damaged files.

Zero pages were dropped because no format profile was supplied. That is
Principle 1's default working correctly, not a reduction failure.

### §10 restated against measured numbers

The §10 target — "~5,700 pages with ~50% scanned in under 60 minutes" — does not
describe this corpus, as the register already recorded. Measured against what
actually exists:

| stage | measured (attempt 3) |
|---|---|
| walk + extract (Stages 1-2) | **2,848.5 s** |
| Bates detection (Stage 3) | 0.36 s |
| Doc ID assignment + §5 reconciliation (Stage 3b) | 0.006 s |
| section classification (Stage 4) | 0.001 s |
| token measurement | 23.3 s |
| emit — all of §7 and §8 (Stage 5) | 1.4 s |
| verify — accounting, manifest (Stage 6) | 0.64 s |

**99.1% of the run is extraction.** Everything DocIQ was built to add on top of
extraction — identity, Bates, reconciliation, classification, every deliverable
in §7, the Path-A package, the accounting gate and the hash manifest — costs
**25.7 seconds on 18,521 pages**. Optimizing anything but extraction is
optimizing 0.9% of the run.

**The OCR / non-OCR split, measured rather than projected.** The corpus was run
a second time from scratch with OCR disabled, on an idle machine, so the
difference between the two is the OCR cost on this material rather than a
per-page figure carried over from the bake-off:

| run | configuration | wall clock | pages |
|---|---|---|---|
| OCR off, from scratch, idle machine | `ocr_enabled=False` | **3,046.7 s (50.8 min)** | 18,556 |
| OCR on | see the three attempts above | ≈ 100 min (upper bound) | 18,521 |

Because the OCR-on figure spans two attempts, the honest comparison is over the
**identical first 62 documents in identical scan order**, both from scratch:

| documents completed | OCR on | OCR off | ratio |
|---|---|---|---|
| 30 | 1,055 s | 461 s | 2.29 |
| 40 | 1,773 s | 757 s | 2.34 |
| 50 | 2,273 s | 1,027 s | 2.21 |
| 62 | 3,046 s | 1,491 s | 2.04 |

**OCR roughly doubles extraction time on this corpus** — and it does so for
**400 pages out of 18,521, 2.2% of the record**. The prefix is representative:
those 62 documents carry 39% of the corpus's OCR pages and 37% of its pages. The
OCR-on side of this comparison ran partly under concurrent load, so 2.0–2.3× is
an upper bound on OCR's share; the direction and the order of magnitude are not
in doubt.

That is the finding §10 needs: the requirement assumes an OCR-dominated workload
and this corpus is 97.8% native text, yet OCR still accounts for about half the
run. The non-OCR path over 17,369 native pages takes **50 minutes on its own**,
and that is the number to attack.

The performance requirement should be restated as: *the D-12 corpus (368
documents, 18,521 pages, 2.2% scanned) completes end to end in roughly 100
minutes on a 32-core workstation, extraction-bound, with the per-file timeout
raised.* It is deliberately not restated as a general target until a
from-scratch run on an idle machine is available.

### Token load — the register's figure chased down

The register records **19,388,495 pre-tokens over 49,031,833 characters**
(2.53 chars/pre-token) for the same corpus. This run measured **17,252,003
pre-tokens over 50,190,410 characters** (2.91) of page text, and **17,380,982
over 50,598,897** for the emitted `clean_text/` including page markers.

More characters, 11% fewer pre-tokens. That is a real disagreement, and it is
not an estimator disagreement — both numbers come from the same
`verify/tokens.measure`. **They measure different text.**

- `tools/calibrate_tokens.py`, which produced the register's figure, extracts
  with **PyMuPDF `page.get_text()`**, skips whitespace-only pages, applies no
  normalization, runs no OCR, and covers the 298 PDFs only.
- The pipeline measures its own normalized `PageRecord.text` across all 368
  documents, extracted with **pypdf**, plus OCR text for 400 pages and 789 pages
  of DOCX/PPTX the register never saw.

Measured on 131 identical pages of 6 randomly sampled corpus PDFs:

| text | chars | pre-tokens | density |
|---|---|---|---|
| PyMuPDF `get_text()` — the register's method | 249,267 | 75,485 | 3.302 |
| pypdf `extract_text()` — what DocIQ extracts with | 244,713 | 64,679 | 3.784 |
| pypdf + contract normalization — what DocIQ emits | 235,873 | 61,274 | 3.849 |

So **the extractor accounts for most of the gap** (−14.3% pre-tokens on nearly
identical character counts) and normalization for the rest (−5.3% further). The
two figures were never measuring the same thing.

**Consequence:** the pipeline's number is the one that describes the corpus
DocIQ actually ships, and it should supersede the register's for any statement
about the deliverable. The register's figure remains valid as what the *source
PDFs* contain under a different reader. Neither is a Claude token count, and
`ratio_refuted` is `True` on both estimates — D-03's 3.30–3.60 band remains
unreachable on this material, now on a third independent measurement.

Two smaller results fall out of the same numbers:

- Page markers add **+0.81% characters and +0.75% pre-tokens** over the whole
  corpus (page text 50,190,410 / 17,252,003 → emitted clean text 50,598,897 /
  17,380,982). That independently corroborates D-18's measured 0.5–1.2% and its
  ruling to leave the §7 marker format alone.
- The record is **~86× the 200K direct-context working figure** even before
  reduction, which strengthens D-15 exactly as the register predicted.

### A defect the real corpus found: Stage 3 proposed Bates on the negative case

D-13 designates the Petrobras record as the **negative** case for Bates
detection: a footer-zone probe over all 298 PDFs found no stamps, so detection
must come back empty without flagging an error. It did not. `propose_format`
proposed a `CP0001`-shaped format on the strength of **two lines in 18,521
pages**, because its only bar was an absolute count of two matched pages.

The corpus-wide coverage figure could not be the fix: a fully stamped 306-page
disclosure inside an 18,000-page record — D-13's MNFV set, exactly — is 1.7% of
the corpus and would be thrown away by any corpus-wide floor. The test is
therefore applied **per document**: a format must stamp at least
`MIN_DOCUMENT_COVERAGE_PCT` (50%) of the pages of at least one document. Both
thresholds are named constants carrying their rationale.

*Fail-before:* the new test reproduces the corpus case in miniature — 200 pages,
two stray `CP` lines — and is red without the change. A second test proves the
fix does not buy silence by refusing to look: a fully stamped 8-page document
among 300 unstamped pages is still proposed, at 3% corpus coverage and 100%
document coverage.

Note the run was never at risk of *applying* the wrong pattern: with no operator
to confirm it, the decision stayed `pending` and no page was stamped. The defect
was that a run would have prompted a human to confirm a format that does not
exist — which is how an operator learns to click through the prompt.

## 6. Acceptance criteria

| # | criterion | status |
|---|---|---|
| 1 | reference set processes end to end without manual intervention | **PASS for the processing half** — 368 documents, 18,521 pages, no intervention, two damaged files recorded rather than blocking. The other half (the corpus loads into a Claude Project and is consumed by Expert Assist) cannot be tested offline and is not claimed |
| 2 | **page accounting reconciles to zero discrepancy across the full set** | **PASS** — `pages in 18,521 = kept 18,521 + dropped 0 across 368 documents; 7 unsupported, 2 failed`, zero discrepancies, on the real corpus. This is the first time the corpus-wide gate has run to completion on real material |
| 3 | every page marker resolves to the correct original page (sample: 50) | **PASS** — `tools/check_markers.py`, **nine independent samples, 439 judged markers, 439 correct, 0 misaligned, 0 markers missing, 0 undiscriminable ties**, median Jaccard margin 0.711 over the nearer neighbour, judged against a different extractor from the one that produced the text |
| 4 | Bates ≥99% on stamped sets | **NOT ATTEMPTED** — needs the MNFV production (D-13), not this corpus. What this run *did* establish is the negative half: after the fix above, the unstamped record correctly yields no proposal and no error |
| 5 | reconciliation categorizes seeded discrepancies; no colliding IDs | **PARTIAL** — the three categories and the LI/DIQ split are proven end to end through the pipeline on the fixture corpus (`test_pipeline.py`), and Track B audited the real 9,259-row index separately. Not re-run against the real index here |
| 6 | runs fully offline | **PASS** — the self-test performs a cold OCR engine construction with `socket.socket` replaced by a raiser |
| 7 | **byte-identical outputs on repeat runs** | **PASS on the fixture corpus AND on the real one** — 30 runs / 30 seeds on the fixtures, with the gate watched going red under three injected-nondeterminism probes; and **two full from-scratch OCR-enabled runs over the real 368-document, 18,556-page record produced one `corpus_sha256` (`bdb7d498…`), one log `content_sha256` (`5601badd…`), 370/370 deliverable files byte-identical, 372/372 adjacent files byte-identical, 0 unclassified**. See §6a |
| 8 | handoff packages | **PARTIAL** — `upload_package/` with its README, `sources.json` and `document_index.csv` is built on every run and asserted present on the real corpus; the §8 Path-B layout check passes. Acceptance by an actual Claude Project is not testable offline |
| 9 | OCR bake-off decides D-01 | **CANCELLED by D-19** (Alex, 2026-07-31) — Tesseract written off; rapidocr is the engine unconditionally. Closed as cancelled, **not** as met: the artifact is a rapidocr characterization, never a comparison |

### D-I7 — a run that skipped OCR recorded that it had used rapidocr

The two full-corpus runs made this visible and it could not have been found any
other way. `RunConfig`'s own docstring defines the bug: "Anything that
influences output and is NOT in this dataclass is a determinism bug."
`WalkOptions.ocr_enabled` is not in it.

Measured: the same folder, with **byte-identical `RunConfig`**, produced 400 OCR
pages one way and 400 more EMPTY pages the other, a different hashed content
section, and 17,252,003 vs 17,162,630 pre-tokens — while both runs' recorded
configuration said `rapidocr 1.2.3`. A reader of the two processing logs could
not have told which run read the scanned pages.

Fixed without touching the frozen contract: `RunConfig.ocr_engine` already
exists to say which engine produced the text, and "none" is an answer it can
give, so the pipeline stamps `ocr_engine="disabled"` and clears the version when
no OCR ran. *Fail-before:* the new test is red without it.

### CLOSED — one document extracted differently on two runs of the same corpus

**Was the most serious thing on this page. It is now fixed, and the fix was
exercised by the very run that proves criterion 7.**

#### What was observed

`CER-1-433.pptx` (9.3 MB, 35 slides) came out of the OCR-on run as a **FAILED
record with 0 pages** — "Namespace prefix xmlns for a on sldLayout is not
defined, line 2, column 238" — and out of the OCR-off run as a **`full` record
with 35 pages**. Same file, same bytes, same pipeline.

Ruled out, and still ruled out: a damaged file (10/10 clean parses in
isolation); the OCR setting itself; and concurrent office-XML parsing at the
`python-pptx` level (72 concurrent attempts on the target across 12 rounds of 16
workers, over all 17 corpus `.pptx` and 40 `.docx` — **0 failures**). Reducing
the worker count is not the remedy: it made throughput ~6x worse and treats the
symptom.

#### Why the mechanism stopped being the question

A failure caused by LOAD was written into the deliverables as a property of the
EVIDENCE — "this document is unreadable" — so the corpus a reviewer receives
depended on how busy the machine was when it was built. Principle 1 held: the
failure was recorded, loudly, and 35 pages were never silently dropped.
Principle 5 did not: two runs over identical inputs produced different outputs.

The remedy for that is decidable without the mechanism. **Nothing that did not
read cleanly inside the extraction pool is written off until it has been re-read
once, serially, alone** — still under the per-file watchdog, so a hang is not
traded for a race. The serial reading wins ties and improvements; a retry that
comes out strictly worse is disclosed and discarded rather than allowed to lose
evidence. Both outcomes are named either way.

#### Where the disclosure lives, and why it has to live there

In `processing_log.json`'s **`run`** section, as `load_dependent_extraction`,
beside the timestamp, the operator, `output_root`, the stale-output list and the
D-04 renumbering warnings. **Never in the hashed `content`.**

The reasoning is the subtle part, and it is the same argument the `run` /
`content` split was built on. A run that needed a retry and a run that did not
are, by construction, two runs whose invocations differed and whose *evidence*
did not. Recording the retry inside `content` would make those two runs produce
different bytes — so the disclosure defending the byte-identical claim would be
the thing that breaks it. The same reasoning moved the resume and cancellation
notes out of the hashed warnings, where they had been sitting: a matter reduced
in one pass and the same matter reduced after a crash were producing different
`content` bytes with no difference in the evidence anywhere.

It is not hidden by being un-hashed. It is rendered **first** in
`RunResult.warnings` — the run summary shows four warnings and folds the rest
into a count, so appending it would have satisfied the letter of "recorded" and
none of the point — it is in the log's `run` section, and the operator sees it
on screen.

#### The class, enumerated — every member, including the ones already correct

| # | site | before | now |
|---|---|---|---|
| 1 | extraction failure inside the pool (`extract()` → FAILED) | permanent | **re-read serially** |
| 2 | `_extract_one` read error (`OSError` on the source) | permanent | **re-read serially** |
| 3 | archive that would not open (`expand_zip` raised) | permanent | **re-read serially** |
| 4 | **watchdog timeout** | permanent — *and* it wrote elapsed seconds into `DocumentRecord.error`, which is hashed content | **re-read serially**; the record now says only that the limit was reached, and the elapsed time goes to the run notes |
| 5 | OCR **page** failure | quiet: empty page, document merely flagged | **marker → re-read serially** |
| 6 | whole-document OCR pass failure | quiet note | **marker → re-read serially** |
| 7 | ZIP **member** that would not read | quiet note, member dropped | **marker → re-read serially** |
| 8 | EML/MSG attachment list that would not enumerate | quiet note, attachments absent | **marker → re-read serially** |
| 9 | an attachment that would not decode | quiet note | **marker → re-read serially** |
| 10 | `[PHOTO]` EXIF probe | **bare `except: return ""`** — the block and the camera date vanished with no record at all | discloses, and **marker → re-read serially** |
| 11 | PPTX speaker notes | **bare `except: pass`** — notes vanished with no record at all | discloses, and **marker → re-read serially** |
| 12 | hash-time read error in `scan()` | file demoted to Tier 2 permanently, zero hash, `-1` size, and labelled "Unrecognized format" | **second attempt after 100 ms**; if it still will not open it is inventoried with an honest message that says it was not hashed |
| 13 | **resume** replaying a recorded failure | cemented it — and put it beyond the retry's reach forever | a cached record carrying a failure or a degradation marker is **re-extracted, never replayed** |
| 14 | **resume journal** holding two batches for one file | both replayed → duplicate document, resurrected failure | batch-tokened; **last batch wins** |
| 15 | resume / cancellation notes | in the hashed warnings | in the `run` section |
| 16 | disk preflight | — | **already correct**: it aborts the whole run loudly and records no per-document outcome |
| 17 | `_Errors` cap ordering | — | **already correct**: capped after sorting, not on arrival |

Members 5–11 work off one registry of marker constants
(`extract.TRANSIENT_MARKERS`), used in the f-strings at the emission sites, so a
reworded note cannot silently drift out of the retry's sight. A test asserts
every constant in the registry matches.

Both retry bounds are disclosed whenever they bite: `DOCIQ_RETRY_MAX` (500
documents) and `DOCIQ_RETRY_BUDGET_S` (1800 s).

#### The fix was exercised, on real material, on both proof runs

Neither criterion-7 run reproduced the `.pptx` fault. **Both hit a different
member of the same class**, and the retry absorbed it identically on both:

> `CER-1-145.pdf` (54 MB, 222 scanned pages) **exceeded the 3600 s per-file
> watchdog inside the pool on both runs** — at 3604 s on run A and 3602 s on run
> B — and was re-read serially, alone, where it completed with **222 pages**.

Without the retry, both runs would have recorded that document FAILED with zero
pages and the corpus would have lost 222 pages of a monthly progress report.
Without the wall-clock fix, the two runs would have recorded `abandoned after
3604s` and `abandoned after 3602s` — **different hashed content, and criterion 7
would have failed on a two-second difference in machine load.** Both defects
landed on the same document, on the same pair of runs.

#### Honest residue

* The `.pptx` mechanism is **still not identified**, and a third attempt to
  reproduce it failed. This one went through the **shipped pipeline** rather
  than calling `python-pptx` directly — the earlier probe only ever ruled out
  the naive shared-parser theory — over an 18-file subset chosen to recreate
  the conditions the fault was seen under: the failing deck's own folder plus
  the corpus's OCR-heaviest scans, with rapidocr and onnxruntime resident and
  rasterized pages in flight. **5 rounds, 1 distinct corpus hash, 0 documents
  degraded inside the pool, 0 failures.** (The 222-page scan was excluded from
  the probe corpus and it is said here rather than left to be noticed: it alone
  exceeds the 3600 s watchdog on this machine and would have made repetition
  unaffordable. The OCR pressure came from the other five scans.)

  The instrument is worth keeping: the retry disclosure fires whenever a
  document did not read cleanly *inside the pool*, whether or not the serial
  re-read then repaired it, so a future recurrence is now visible in the log
  even though it no longer reaches the deliverables. Standing count: observed
  once in three full OCR-enabled runs, never in 5 pipeline rounds or 72
  targeted concurrent attempts.

  No speculative fix has been applied, and none should be: a lock or a parser
  change added on the strength of an unreproduced fault is a change nobody can
  justify keeping, or later justify removing.
* The retry makes a *load-dependent* failure survivable. It does not make a
  *deterministic* failure disappear — `CER-1-345.docx` failed in the pool and
  failed again alone, on both runs, and is correctly still recorded FAILED.
* A watchdog-abandoned worker cannot be killed in Python, so for member 4
  specifically the serial re-read may overlap a thread from the abandoned
  attempt. "Alone" is exact for every other member and approximate for that one.

## 6a. Acceptance criterion 7 on the real corpus

| | |
|---|---|
| runs | **2**, both full, both from scratch, both **OCR-enabled** — the shipping configuration. The earlier disagreeing pair had OCR on for one and off for the other, so it could never have settled the claim even had the hashes matched |
| source | the real 368-document Petrobras/MODEC record; byte-identical inputs, identical settings, pinned operator stamp, separate fresh output roots |
| wall clock | 4 844 s and 4 780 s |
| documents / pages | 368 / 18 556 on both; 7 unsupported; 1 failed (the genuinely damaged `.docx`) |
| `corpus_sha256` | `bdb7d49848d9dde119dc04d5eb8ae835b06500a399a64466d0bd0bccb37e17e3` — **identical** |
| log `content_sha256` | `5601badd7242033f5888fa18a7c10900f10be083e7e11160c220bf02173b3a1b` — **identical** |
| per-file, inside the claim | **370 files, 0 differing** |
| per-file, adjacent | **372 files, 0 differing** |
| unclassified outputs | 0 |

The per-file comparison is not redundant with the hash. A matching
`corpus_sha256` computed twice by the same code proves less than the two
directories agreeing file by file, and the second check is what rules out the
manifest itself being the only thing that agrees.

**What this does and does not license.** Two runs are not thirty. What is now
supported on real material is that the corpus, reduced twice at ~80 minutes a
run, came out byte-identical — including through a real load-dependent failure
that occurred on both runs and was absorbed. Thirty repeats of an 18 556-page
corpus remains unaffordable; the 30-run, 30-seed proof stays on the fixture
corpus, and the two are complementary rather than interchangeable.


## 7. What is still NOT proven

1. **D-01 / acceptance criterion 9 is not closed.** Tesseract is not installed
   and was correctly not installed to close it. The bake-off remains
   rapidocr-only.
2. **Acceptance criterion 4 (Bates ≥99%) is not closed here.** It needs a run
   against the MNFV production (D-13), not this corpus — the Petrobras record is
   the *negative* case and correctly yields no stamps at all.
3. **Determinism is proven 30 ways on the fixture corpus and 2 ways on the real
   one** — see §6a. What is NOT proven is that two runs are as strong as
   thirty. The one real divergence ever observed (`CER-1-433.pptx`) has been
   made survivable rather than understood: the retry absorbs it, and a run that
   needs the retry says so, but the mechanism is still unidentified and could
   in principle produce a *deterministic-looking* wrong reading that the retry
   would not catch — a serial re-read that failed the same way twice is
   recorded FAILED, which is correct, and a serial re-read that succeeded
   differently both times is not something any observation so far suggests.
   Thirty repeats of an 18,556-page corpus at ~80 minutes each remains
   unaffordable in this sprint.
4. **`.msg` remains vendored-but-unexercised** — no library in the dependency
   set writes one, and a real one is client data. Unchanged from Track A's §6.
5. **The GUI still runs on the mock pipeline.** `gui/pipeline.get_pipeline()` is
   the documented one-line swap and it is Sprint 2's, per the seam's own
   docstring. Nothing in this integration touched it.
