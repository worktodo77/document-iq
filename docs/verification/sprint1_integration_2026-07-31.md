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
  `5544e622d46b3747b0ed9cf4bb267a796c46797226cccbf0e34f959206aacea0`.
  (Track A's recorded hash `b587bb6a…` does not carry forward and should not be
  compared against: it was the *stand-in's* output, and the fixture corpus has
  since gained the email-with-attachment case.)

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

FULLRUN

## 6. Acceptance criteria

CRITERIA

## 7. What is still NOT proven

1. **D-01 / acceptance criterion 9 is not closed.** Tesseract is not installed
   and was correctly not installed to close it. The bake-off remains
   rapidocr-only.
2. **Acceptance criterion 4 (Bates ≥99%) is not closed here.** It needs a run
   against the MNFV production (D-13), not this corpus — the Petrobras record is
   the *negative* case and correctly yields no stamps at all.
3. **Determinism is proven on the synthetic fixture corpus**, not on the real
   one. Thirty repeats of a 17,732-page corpus is not affordable in this sprint,
   and the fixture corpus does exercise OCR, mixed native/scanned routing, nested
   ZIP expansion, email-attachment expansion, content-sniff recovery and a blank
   page.
4. **`.msg` remains vendored-but-unexercised** — no library in the dependency
   set writes one, and a real one is client data. Unchanged from Track A's §6.
5. **The GUI still runs on the mock pipeline.** `gui/pipeline.get_pipeline()` is
   the documented one-line swap and it is Sprint 2's, per the seam's own
   docstring. Nothing in this integration touched it.
