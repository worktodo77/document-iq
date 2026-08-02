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

| D-10 | Roadmap velocity revision | **2 sprints, 3 parallel tracks, 2 Codex reviews** (adopts the p6-kernel endgame pattern). Contract-first: `pagemodel.py` frozen day one; Tracks A (ingestion spine), B (identity + deliverables), C (GUI shell + branding) build concurrently in worktrees; Codex #1 at pipeline-core integration, Codex #2 = merge gate. Scope trims on Project 495 evidence: RTF → Tier 2 (zero occurrences), RAR → Tier 2 listed-only (9 occurrences). Determinism proof, page-accounting gate, and Track-A critic depth explicitly NOT compressed. | 2026-07-30 |

| D-11 | Build environment / dependency location | **Dedicated venv at `document-iq\.venv`** — the full declared set (pypdf, pymupdf, rapidocr-onnxruntime, onnxruntime, opencv-python-headless, python-docx, openpyxl, extract-msg, python-pptx, xlrd, reportlab, PySide6, PyYAML, numpy, Pillow, pytest) installed there, not into system Python and not shared with the mip39 venv. Closes the reuse audit's §5 undeclared-dependency gap and keeps DocIQ decoupled from the mip39 repo's environment. Verified 2026-07-30: all 15 imports green on Python 3.14.5; `onnxruntime==1.28.0` publishes cp314 wheels, so there is no interpreter-version wall. | 2026-07-30 |
| D-12 | OCR bake-off corpus (D-01 / acceptance criterion 9) | **The real MODEC/Petrobras MPR corpus**, supplied by Alex mid-session at `Desktop\Petrobras\Petrobras\Project FIles`. Supersedes the interim "MNFV now, re-run on MODEC later" ruling taken minutes earlier when no MODEC set could be found — that two-cycle path is cancelled, the bake-off is **not** provisional, and D-01 is ruled at the end of Sprint 1 as originally planned. Corpus: 2.6 GB, 298 PDF / 53 DOCX / 17 PPTX / 7 DOC; 17,732 PDF pages, 461 scanned (2.6%), 21 mixed native+scanned PDFs, 0 fully-scanned. The ~20 bake-off pages are sampled from the scanned pages of `CER-1-145.pdf` (175 scanned of 222) and `CER-1-113.pdf` (91 of 228). Client data — never committed; only summary numbers quoted. | 2026-07-30 |
| D-13 | Bates acceptance corpus (acceptance criterion 4) | **The MNFV initial-disclosure production** (`Desktop\Files for Claude\20240529`) — genuinely Bates-stamped and image-only (e.g. `MNFV 0391 - 0696`, 306 pp; `MNFV 02684 - 02705`, 22 pp), so it is the only local set that can exercise the ≥99% Bates-accuracy criterion. A footer-zone probe over all 298 Petrobras PDFs found **no Bates stamps**, confirming §4 Stage 3's "absence is normal, not an error" against a real corpus — so the Petrobras set doubles as the negative case (Bates detection must return `None` throughout without flagging an error). Client data — never committed. | 2026-07-30 |

| D-14 | Token capacity headline design (refines D-07's "capacity gauge") | **Concept B — the reduction waterfall, with clickable rows.** BEFORE → AFTER headline over a stack of shrinking bars, one per reduction lever, with capacity as a fixed dashed reference row. **The rows are the section picker**, not a readout — clicking a lever toggles KEEP/DROP and the waterfall re-flows live, so the picture and the next action are the same object. Expert-approved drops render as interactive accent rows; the tool's own automatic savings (duplicate removal, page furniture) render as a locked muted row and are **never merged into the same number** — the profile system exists to keep "the expert approved this omission" distinct from "the tool did this mechanically". Concepts A (paired rails), C (capacity units) and D (log ruler) were shown and not selected; C remains the strongest option if an explain-to-a-third-party view is ever wanted. | 2026-07-30 |
| D-15 | Over-capacity is the expected state, not an error | On the real corpus (17,732 pages) the record does **not** fit direct context even fully reduced. The UI must therefore design the over-capacity case **first**, with no alarm treatment and no blocked action: state the shortfall factor plainly and pair it with the §8 Path B escape route (Expert Assist / Cowork reads the matter folder from disk, where the container limit does not apply). A dead end here is a design bug. | 2026-07-30 |
| D-16 | Action-button wording and screen sequencing | Alex judged the first sketch "not user friendly or intuitive": "Run" names a mechanism rather than an outcome, and the peer button "Review what gets dropped" read as a prerequisite step. Rulings: the primary button names the **outcome** ("Build the reduced corpus") with scope + time estimate beside it; there is **exactly one forward action per screen**; "Review what gets dropped" is removed as a button because reviewing drops is the whole economizing step, now carried by the D-14 waterfall itself. | 2026-07-30 |

## Open question — does local-folder reading dissolve the capacity problem? (raised by Alex 2026-07-30)

Partly, and the residue matters. Pointing Claude at a local matter folder removes the **container** limits — upload ceiling, project-knowledge cap, and the direct-context-vs-retrieval distinction. It does **not** remove the context window: 17,732 pages cannot occupy one turn, so the constraint changes shape from "does it fit in a box" to "how many turns, and how much drift, before the agent has seen what matters." Reduction still pays on that path, in turns and cost rather than in admission.

Consequence for the product's positioning, flagged: §7 and D-03 make the token estimate *the headline feature*, yet on the recommended route (Path B) it is the metric that binds least. DocIQ's durable value on that path is evidentiary rather than dimensional — original-pagination markers, Bates, the index, zero-discrepancy accounting, byte-identical reruns.

**RULED 2026-07-30 (D-17): keep the GUI as previously decided.** Three summary-screen treatments were drawn and compared — evidentiary lead with tokens demoted, a split headline, and the ruled token lead with an audit panel below. Alex ruled to **stick with the previous decisions**: §7 and D-03 are **not** amended, the token before/after remains the summary headline, and the evidentiary claims (page accounting, omission attribution, run fingerprint, index reconciliation, review flags) stay as a supporting panel. The evidentiary-lead proposal is **declined, not deferred** — do not re-open it in Sprint 2 without a new instruction. D-07, D-14, D-15 and D-16 all stand unchanged; Track C's brief already reflects them and required no correction.

| D-18 | §7 page-marker format — keep or compact | **KEEP §7's format unchanged.** Alex ruled "measure it properly first"; the measurement settles it against changing. Full-corpus measurement (all 298 PDFs / 17,732 pages, via Track B's pre-token proxy): markers are **0.5% of the corpus without Bates, 1.2% with**. Compaction buys almost nothing — `[p1234]` and `===== PAGE 1234 =====` both yield **5 pre-tokens**, because pre-token count tracks the number of symbol runs, not string length; only the Bates variant compacts at all (13 → 10). A two-repo change against DocIQ *and* Expert Assist's parser to recover ~0.5% is not worth it. **Correction on the record:** the initial estimate that framed markers as the single largest economization lever used a chars÷3.5 approximation and was wrong in framing — the absolute figure (~230K pre-tokens for the Bates form) held, but the share did not. Character compaction does not imply token savings. | 2026-07-30 |

| D-19 | Tesseract / the D-01 bake-off | **Written off — rapidocr is the engine, full stop (Alex, 2026-07-31).** D-01's conditional swap and acceptance criterion 9's comparison are both **cancelled, not deferred**: there is no pending Tesseract evaluation, and no future sprint owes one. Tesseract was never installed (installing it needed authorization that was not given, and the build correctly refused to install it unilaterally), so the Sprint-1 bake-off is a **rapidocr characterization** rather than a comparison — and that is now its final form. Measured on 20 real scanned MPR pages from D-12's corpus: mean page confidence **0.8628**, 3 of 17 pages below the 85% review threshold, 37% of *lines* below it, **5.74 s/page**; 3 zero-character pages verified genuinely blank (uniform white, no embedded images) rather than misses. `docs/bakeoff/ocr_bakeoff_2026-07-30.md` stands as the methodology artifact D-01 asked for, retitled to what it is. **Consequence to state plainly rather than bury: §3 and §10's amended wording already name rapidocr, so nothing in the build changes — but the tool now ships an OCR engine chosen on in-house familiarity and ONNX bundling convenience, never benchmarked against an alternative on this corpus. If OCR quality is ever challenged, that is the honest answer, and "Tesseract is the industry-recognizable name for law-firm IT review" (the original argument for the bake-off) remains unaddressed.** | 2026-07-31 |

## Sprint-2 kickoff rulings (Alex, 2026-08-01)

| D-20 | Acceptance criterion 1 — how "loads into a Claude Project" is proven | **Split the criterion along the measured reality.** Criterion 1 as written assumes a matter fits a Project's direct context; Sprint 1 measured the real corpus at ~13.9–15.2M tokens, 70–100× that. **Path B (Expert Assist / Cowork reads the matter folder from disk) is proven at full scale — all 368 documents** — and is the route the criterion's "consumed by evidence-mining without format errors" clause is discharged on. **Path A (browser upload package) is proven on a deliberately scoped subset** (date- or type-scoped) that genuinely fits, and the acceptance note states the scope rather than implying full-corpus coverage. Rejected: uploading the full corpus and counting "no size rejection" as a pass — that would rest the criterion on retrieval-mode recall over 17,732 pages, which is unmeasured and would set an expectation for the analyst the tool cannot back. Criterion 1's wording is amended in requirements to match. | 2026-08-01 |

| D-21 | The reduction waterfall's capacity line (`DIRECT_CONTEXT_TOKENS`, UNCONFIRMED since Sprint 1) | **Keep 200,000, and render it as a NAMED, SOURCED REFERENCE LINE — "Claude Project direct context" — never as a budget or a target.** The constant stays exactly where the seam put it (one named symbol, no inlined literals) so a future change is one line. The waterfall must not imply that getting under the line is the goal: D-15 already rules over-capacity the **expected** state, and D-20 makes Path B the route that does not care about the line at all. Wording on screen must let an expert who drops four section types describe *what was dropped and why*, not "reduced to fit". Rejected: making the line operator-settable (a knob whose wrong setting silently rescales every figure on screen), and removing it (guts D-14's point — the operator loses the only signal that the reduction accomplished anything). | 2026-08-01 |

| D-22 | §10 "PyInstaller single exe" vs. the bundled ONNX payload | **AMENDED to a one-folder build shipped as a zip** (`--onedir`: `DocumentIQ.exe` plus its payload, unpacked once). The bundled OCR models push the payload past ~100 MB, and `--onefile` unpacks the whole of that to a temp directory on **every** launch — multi-second cold starts, and a temp-extract-then-execute pattern that is precisely what endpoint protection on a locked-down law-firm machine quarantines. A tool that does not open at a client site has no other qualities. §10's "single exe" wording is amended with this reasoning recorded; the deliverable is one zip, which preserves the intent (one thing to hand over) without the failure mode. Deferred, not rejected: an Inno Setup installer with the D-08 icon registered — it adds an installer toolchain and a code-signing question Sprint 2 has not budgeted. | 2026-08-01 |

| D-23 | Acceptance criterion 4 — how Bates ground truth is established on MNFV | **Sequence-continuity proof over the whole set + a hand-checked stratified sample of ~100 pages.** MNFV is image-only, so every candidate ground-truth number is itself OCR of a footer stamp; a sample alone cannot see a systematic off-by-one or a document-boundary error, and a full hand-check does not scale. So: machine-verify the *structural* property over every stamped page — each document's Bates range monotonic, gapless within the document, non-overlapping across documents, contiguous across the production — which catches the whole error class; then hand-verify a stratified sample against the page images for absolute correctness. The ≥99% figure is stated **with its method**, and misses are flagged rather than silently corrected (§4). The acceptance note must state plainly that ground truth is OCR-derived and what the continuity proof does and does not establish. | 2026-08-01 |

### CORRECTION to D-13 and D-23's premise — the MNFV ground truth is not all OCR-derived (Track F, 2026-08-01)

D-13 calls the MNFV set "image-only" and D-23 builds its whole method on the
consequence: "every candidate ground-truth number is itself OCR of a footer
stamp". **That is true of part of the set and false of the larger part**, and
the correction is recorded here rather than left in a verification note because
D-23's ruling text asserts it.

`Desktop\Files for Claude\20240529` holds two different things:

| set | documents | pages | ground truth |
|---|---|---|---|
| `20240510 Initial Discl` — a standard e-discovery production, prefix `iiCON` | 2,138 | 11,561 | its own **load files** (`.OPT` / `.LFP` / `.DAT` / `.LST`) — the producing party's authoritative page-level numbering, generated when the stamps were burned in, **not** a re-reading of them. The PDFs also carry the vendor's embedded text layer, so DocIQ reads their stamps on the native path, not through OCR. |
| `Initial Disclosures` + `Supplemental` — combined PDFs, prefix `MNFV` | 16 PDFs | 2,963 | **filename ranges only** — document-level and weak. D-23's caveat applies here in full, and was measured to bite: the filenames pad to four digits (`MNFV 0919`) while the stamp burned into the page pads to five (`MNFV 00919`). |

**D-23's METHOD is unaffected and was followed as ruled** — continuity over the
whole set, plus a hand-checked stratified sample. What changes is the strength
of the resulting number: the page-level figure for the 2,138-document
production rests on authoritative, non-OCR ground truth. The two sets are
reported separately and never averaged, because the average would be the
flattering figure and the less honest one. Detail and results in
`docs/verification/track_f_sprint2_2026-08-01.md` §4.

## Sprint-1 verification log

**Track B master-index robustness (2026-07-30).** The Track B critic found a
High-severity defect: a negative `Original Sort` value loaded cleanly into a
`MasterIndexRow` but violated `DocId`'s `base >= 0` requirement at assignment
time, raising an unhandled `ContractViolation` that named neither the offending
row nor the file — taking down identifier assignment for *every* document in the
corpus, not just the bad row. Fixed at `5eb1f7a` (negative sort treated as "no
usable value": skipped with a warning, row reconciles as index-only), with a
regression test confirmed to go red on `a78f1b5`.

The critic could not test this against the real index, correctly refusing to
touch client data. Audited separately: the real Project 495 index is **clean** —
9,259 rows, a perfect 1..9,259 sequence, zero non-numeric, zero negative, zero
duplicate values. So the builder's "9,698 IDs, 0 collisions" claim is genuine,
but it passed *because the file happened to be clean*, not because the code was
robust to a dirty one. Recorded because it is a textbook instance of "the corpus
doesn't exercise it selects nothing": the fix is warranted by the failure being
loud and total, not by evidence from this corpus.

**Sprint-1 integration (2026-07-31).** The stand-in emitter `verify/probe_emit.py`
is deleted and the determinism proof re-run against the emitters that ship: 30
runs, 30 distinct `PYTHONHASHSEED` values, subprocess per run, **1 distinct
corpus hash, 0 diffs, 0 failures**, and the harness watched going red under three
injected-nondeterminism probes. Seven defects found and closed at the seam, each
with a test proven red beforehand — the un-remapped `parent_doc_id`, D-04's
renumbering check crying wolf on duplicate content, renumbering warnings inside
the hashed log content, a re-run inheriting the previous run's residue, a corpus
that never exercised email-attachment expansion, Stage 3 proposing a Bates format
on the set D-13 designates as the negative case, and a run that skipped OCR still
recording that it had used rapidocr. **One finding is OPEN and material:** a
9 MB PowerPoint extracted as 35 pages on one full-corpus run and failed on
another, so the byte-identical claim is demonstrated on the fixture corpus and
is **not** demonstrated on the real one. Full account in
`docs/verification/sprint1_integration_2026-07-31.md`.

**A note on thread configuration, measured rather than assumed (2026-07-31).**
Track A recorded thread oversubscription as an unquantified hypothesis. It was
partly tested during the full-corpus run: the shipped defaults put **173 OS
threads** on a 32-core box at **~12.5 cores busy**. Reducing to 4 document
workers and 6 OCR page workers made throughput *worse* — 142 threads at **~2
cores busy** — so the naive "pin the threads" remedy is refuted for this
workload, and the run was returned to the defaults. What limits throughput here
is not yet identified; it is a Sprint-2 performance question, and the one thing
now established is that fewer workers is not the answer.

## Measured corpus token load (full corpus, 2026-07-30)

> **CORRECTED 2026-07-31 (Codex review #1, finding B-6).** This section
> originally called the pre-token count a *hard lower bound* on token count for
> any byte-level BPE tokenizer, and treated it as refuting D-03. **Both claims
> were overstated and are withdrawn.** The correction is at the foot of this
> section; the measurements themselves are unchanged and still stand.

Measured over **all 298 PDFs / 17,732 pages** of the real MODEC/Petrobras MPR
corpus using Track B's pre-token proxy (`verify/tokens.measure`):

| quantity | measured |
|---|---|
| characters | 49,031,833 |
| **pre-tokens (DocIQ's own split — see the correction below)** | **19,388,495** |
| density | 2.53 chars/pre-token |
| per page | 2,765 chars / 1,093 pre-tokens |

**This is ~6× the requirements' 3.4M assumption (§1) and ~97× the 200K
direct-context working figure.** The consequence is strategic and should not be
softened: **no combination of reductions brings this record into direct
context.** A 90% reduction still leaves ~2M tokens, 10× over. Path B (Claude
reading the matter folder from disk) is therefore not merely the *recommended*
route for forensic matters — at this scale it is the only viable one, and the
UI must treat direct-context capacity as an aspiration that this class of matter
will not meet. This strengthens D-15 rather than contradicting it.

**SUPERSEDED FOR ANY STATEMENT ABOUT THE DELIVERABLE (2026-07-31).** The first
full pipeline run measured **17,252,003 pre-tokens over 50,190,410 characters**
of its own emitted page text across all 368 documents (2.91 chars/pre-token),
and 17,380,982 over 50,598,897 for `clean_text/` including page markers. More
characters, 11% fewer pre-tokens — because the two figures measure **different
text**, not because the estimator changed. The figure above came from
`tools/calibrate_tokens.py`, which reads with **PyMuPDF `get_text()`**, skips
whitespace-only pages, applies no normalization, runs no OCR and covers the 298
PDFs only. On 131 identical pages, PyMuPDF yields 16.7% more pre-tokens than the
pypdf text DocIQ actually extracts, and contract normalization removes about 5%
more. Use the pipeline's number for the corpus DocIQ ships; the figure above
remains valid as what the source PDFs contain under a different reader. Detail
in `docs/verification/sprint1_integration_2026-07-31.md` §5.

Every token figure in the Sprint-1 UI mockups (3.4M → 850K) remains known to be
far too small and must not be carried into the build as if measured.

### CORRECTION — the "19.4M token floor" and the D-03 refutation (2026-07-31)

Codex review #1 finding B-6 is accepted in full. Two things this section
asserted are not supported by the evidence:

**1. There is no 19,388,495-token floor.** The argument was that a byte-level
BPE tokenizer cannot merge across a pre-token boundary, so it cannot emit fewer
tokens than the text has pre-tokens. That holds for a tokenizer's **own**
pre-tokenization. It does not hold for boundaries DocIQ's approximate regex
invented: `PRETOKEN_RE` splits digit runs every three digits, and a real
tokenizer that keeps longer digit runs together merges straight across those
splits and emits fewer tokens than DocIQ counted pre-tokens. The corpus is
**13% digits**, so this is where the material actually is, not a corner case.

19,388,495 is a **pre-token count under DocIQ's own split** — a characterization
of the text's structure, not a bound on any tokenizer's output.

**The one bound that does survive** is the ceiling: `tokens <= UTF-8 bytes`,
because a byte-level vocabulary always contains single-byte fallbacks. That
holds for any such tokenizer and DocIQ still asserts it.

**2. D-03 is NOT established as refuted, and its status returns to RULED.** With
the assumed allowance for coarser pre-tokenization (0.70–1.60 tokens per DocIQ
pre-token, both assumed and both stated in `verify/tokens.ASSUMPTIONS`), 2.53
chars/pre-token implies roughly **1.58–3.61 chars/token** — which *overlaps*
D-03's ruled 3.30–3.60 band. At the coarse end the corpus lands near 3.5
chars/token, inside the band. The same arithmetic on the 40-PDF sample (3.03)
and on the pipeline's own emitted text (2.91) also overlaps.

What survives, and is worth keeping: this material is far denser than ordinary
prose (4.4–4.5 chars/pre-token), so D-03's band sits at the **coarse end** of
what the structure allows rather than in the middle of it, and a figure built on
the band should be read as an optimistic member of a wide range. That is a
characterization, not a refutation.

**3. The strategic conclusion is unchanged and does not depend on the withdrawn
claims.** Even at the top of the ruled band (3.60 chars/token) the corpus is
~13.6M tokens, ~68× the 200K direct-context working figure; a 90% reduction
still leaves ~1.4M, ~7× over. **No combination of reductions brings this record
into direct context.** Path B (Claude reading the matter folder from disk)
remains the only viable route at this scale, and D-15 stands.

**4. What is needed to settle it.** Someone with network access must count real
Claude tokens on a sample of this material. Until then every DocIQ token figure
is an estimate under stated assumptions, and the deliverables say so — the
processing log carries the assumptions verbatim, the run summary PDF names the
method that run used, and the GUI no longer renders "tokens at least".

## §10 restated against a completed full-corpus run (2026-07-31)

The first end-to-end run of the whole D-12 corpus finished: **368 documents,
18,521 pages, page accounting reconciling to zero discrepancy** (acceptance
criterion 2, on real material, for the first time). Measured:

| | measured |
|---|---|
| walk + extract (Stages 1-2) | 2,848.5 s — **99.1% of the run** |
| everything else — Bates, Doc IDs, reconciliation, classification, all of §7 and §8, the accounting gate, the hash manifest | **25.7 s combined** |
| same corpus with OCR disabled, from scratch, idle machine | **3,046.7 s (50.8 min)** |
| OCR's share, over the identical first 62 documents both ways | **≈ 2.0–2.3× — OCR roughly doubles extraction, for 2.2% of the pages** |

A clean from-scratch OCR-on wall clock is **not** established (the completing run
resumed 62 documents from an interrupted first attempt); ≈100 minutes is the best
available figure and is an upper bound. §10's "under 60 minutes" is not met and
is not restated as a general target until a clean run exists. Two documents also
exceeded the shipped 3,600 s per-file timeout and would have been abandoned —
loudly, never silently — so that default is too tight for this corpus.

The consequence for where effort goes is unambiguous: **optimizing anything but
extraction is optimizing 0.9% of the run.**

## Corpus reality vs. the spec's assumption (recorded 2026-07-30)

The requirements' motivating figures (§1: "38 MODEC MPRs at ~20 MB / ~150 pages
each"; §10 performance target: "~5,700 pages with ~50% scanned in under 60
minutes") do not match the corpus that actually exists:

| | spec assumption | measured |
|---|---|---|
| documents | 38 MPRs | 298 PDF + 53 DOCX + 17 PPTX + 7 DOC |
| PDF pages | ~5,700 | 17,732 |
| scanned share | ~50% | 2.6% (461 pages) |

Consequences carried into the build rather than left implicit: the OCR-dominated
performance target is far easier than specified (461 pages to OCR, not ~2,850),
but the corpus is **3× larger in page count**, so the non-OCR path — extraction,
hashing, accounting, emit — is the real performance risk, not OCR. The §10
target should be restated against measured numbers once Sprint 1 has a timed
end-to-end run; it is not amended here on prediction.

## Amendments to requirements_v1.0 implied by these rulings

- §3 Tier-1 table: "Local OCR (Tesseract)" → "Local OCR (rapidocr, ONNX; per-page confidence recorded)" subject to the D-01 bake-off.
- §3 Tier-1 table: DOC moves to Tier 2 (listed + remediation hint) for v1.
- §10 stack line: Tesseract 5 → rapidocr_onnxruntime (+ PyMuPDF rasterization), pending bake-off.

These amendments will be folded into a requirements v1.1 once D-04 is ruled.
