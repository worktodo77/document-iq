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

## Measured corpus token load (full corpus, 2026-07-30)

Measured over **all 298 PDFs / 17,732 pages** of the real MODEC/Petrobras MPR
corpus using Track B's pre-token proxy (`verify/tokens.measure`), which yields a
*hard lower bound* on token count for any byte-level BPE tokenizer:

| quantity | measured |
|---|---|
| characters | 49,031,833 |
| **pre-tokens (token floor)** | **19,388,495** |
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

Two knock-on notes: the density figure independently corroborates Track B's
refutation of D-03's 3.30–3.60 chars/token band (measured 2.53 here, 3.03 on
Track B's 40-PDF sample — both below the ruled floor), and every token figure in
the Sprint-1 UI mockups (3.4M → 850K) is now known to be far too small and must
not be carried into the build as if measured.

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
