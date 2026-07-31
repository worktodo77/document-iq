# Codex review #1 — DocIQ Sprint 1 (pipeline core)

**Path in repo:** `docs/reviews/sprint-1_2026-07-31_claude.md`
**GitHub blob:** https://github.com/worktodo77/document-iq/blob/build/sprint-1/docs/reviews/sprint-1_2026-07-31_claude.md
**Branch:** `build/sprint-1` @ `cabb19d`
**Author:** Claude (Opus 5) · **Reviewer requested:** Codex
**Gate:** D-10 review #1 — the whole pipeline core. This is **not** the merge gate; Codex review #2 at the end of Sprint 2 is.

---

## Read list, in this order

1. `docs/requirements/requirements_v1.1.md` — the ruled baseline
2. `docs/decisions/decision_register.md` — D-01..D-19 plus the measured-corpus sections
3. `docs/contracts/pagemodel_freeze.md` — the frozen contract's prose half
4. `src/dociq/contracts.py` — contract **v1.2.0**
5. `docs/contracts/amendments.md` — A-01/A-02/A-03, all applied
6. `docs/verification/track_a_sprint1_2026-07-30.md`, `docs/contracts/integration_notes_track_b.md`, `docs/verification/sprint1_integration_2026-07-31.md`
7. Then the code: `src/dociq/{contracts,pipeline,selftest}.py`, `ingest/`, `identify/`, `docid/`, `profiles/`, `emit/`, `verify/`, `gui/`, `branding/`

Please read files **from the branch** (`git fetch && git show build/sprint-1:<path>`), not from pasted text.

---

## What was built

A single Python package `src/dociq/` — a deterministic, fully offline document-corpus reducer feeding Long International's Expert Assist skills. Sprint 1 built the pipeline core plus a GUI shell against a mocked pipeline; Sprint 2 does real wiring, packaging and acceptance.

Built contract-first (D-10): `contracts.py` was frozen on day one and three tracks built concurrently in separate worktrees — A ingestion spine, B identity + deliverables, C GUI shell + branding — then integrated. Each track was reviewed by an independent adversarial critic before integration.

| area | modules |
|---|---|
| ingest | `extract.py` (vendored+adapted MIP 3.9 extractor), `pagemodel.py`, `walker.py`, `dating.py` |
| identify | `bates.py` |
| docid | `ids.py`, `masterindex.py`, `assign.py`, `reconcile.py` |
| profiles | `model.py`, `detect.py`, `apply.py` |
| emit | `cleantext.py`, `indexbook.py`, `log.py`, `summary.py`, `handoff.py`, `paths.py` |
| verify | `accounting.py`, `manifest.py`, `tokens.py`, `determinism.py` |
| orchestration | `pipeline.py`, `selftest.py` |
| GUI / brand | `gui/*` (PySide6, mocked pipeline), `branding/make_icon.py`, `make_logo.py`, `palette.py` |

---

## Evidence

**Suite:** full test suite green; `python -m dociq.selftest` **exit 0**, 44 checks.

**Determinism, fixture corpus:** 30 runs × 30 distinct `PYTHONHASHSEED`, subprocess per run → **1 corpus hash**. Three injected-byte probes (into `clean_text`, the log's `content`, and an adjacent file) each turned it **red** and named the file.

**Determinism, real corpus (acceptance criterion 7):** two from-scratch OCR-enabled runs over the real 368-document / 18,556-page MODEC/Petrobras record:

- `corpus_sha256` identical: `bdb7d498…37e17e3`
- log `content_sha256` identical: `5601badd…173b3a1b`
- **370 files inside the claim, 0 differing; 372 adjacent files, 0 differing; 0 unclassified**
- wall clock 4,844 s and 4,780 s
- independently re-verified outside the tool with `diff -rq` and `cmp`

**Acceptance criteria:**

| # | criterion | status |
|---|---|---|
| 2 | page accounting reconciles to zero discrepancy | **PASS on the real corpus** — 18,521 in = 18,521 kept + 0 dropped |
| 3 | page markers resolve to the correct original page | **PASS** — 439/439, judged against a different extractor |
| 5 | Doc ID assignment + reconciliation | **PASS** — 9,698 IDs against the real 9,259-row Project 495 index, **0 collisions**, all rows matched, stable over 8 shuffled orders |
| 7 | byte-identical repeat runs | **PASS** — fixture (30 runs) and real corpus (2 runs), above |
| 1, 6, 8 | end-to-end / offline / handoff | Sprint 2 |
| 4 | Bates ≥ 99% on a stamped set | **NOT ATTEMPTED** — see gaps |
| 9 | OCR bake-off | **CANCELLED by D-19**, not met — see gaps |

**§10 performance, measured:** extraction is **99.1%** of the run. Everything DocIQ adds — identity, Bates, reconciliation, classification, all §7/§8 outputs, the accounting gate, the manifest — costs **25.7 s across 18,521 pages**. A from-scratch OCR-off run took 50.8 min; OCR roughly doubles extraction for 2.2% of pages. **§10's 60-minute target is NOT met** (~80 min measured) and has deliberately not been restated as a target.

---

## Findings we want pressed hardest

**1. The `.pptx` fault we could not reproduce.** `CER-1-433.pptx` extracted 35 pages on one full run and failed with an lxml DrawingML namespace error on another. Ruled out: a damaged file (10/10 clean in isolation), the OCR setting, `python-pptx`-level concurrency (72 targeted attempts at 16-way contention), and the shipped pipeline under OCR load (5 rounds, 1 hash, 0 failures). **Observed once in three full runs; mechanism unknown; no speculative fix applied.** The class was instead made safe (below). We would rather Codex attack this than accept it.

**2. Load-dependent extraction is now disclosed, not silently absorbed.** Any document that fails in the pool is re-read once **serially and alone** before being written off; the better outcome wins by a comparison rule, and a strictly worse retry is discarded and disclosed rather than allowed to lose evidence. **This fired on real material:** `CER-1-145.pdf` (54 MB, 222 scanned pages) exceeded the 3,600 s watchdog *inside the pool on both runs* and was recovered whole by the serial re-read. Without it, both runs would have silently lost 222 pages of a monthly report.

**3. Where the retry disclosure lives is load-bearing.** It sits in `processing_log.json`'s **`run`** section, never in the hashed `content`. Putting it in `content` would mean the disclosure defending the byte-identical claim was the thing that broke it — a run that needed a retry and one that did not differ in *invocation*, not in *evidence*. Two consequences were implemented: the per-file error line for a repaired failure derives from the final records, and resume/cancel notes had to move to the same channel because they were already in the hashed warnings (a matter reduced in one pass and the same matter reduced after a crash were producing different `content` bytes). **Please test this reasoning adversarially** — we believe it is right and it is the subtlest call in the sprint.

**4. Wall-clock time was reaching hashed content.** The watchdog wrote `"abandoned after 3604s"` into `DocumentRecord.error`. Two runs timing out on the same file produced different bytes because one machine was busier. Without this fix criterion 7 would have failed on **two seconds** of machine load.

**5. D-03's ratio band is refuted by measurement and the build refuses to display it.** D-03 rules "calibrated against the real Claude tokenizer at ≈3.3–3.6 chars/token". That calibration **cannot be performed** under Principle 4 (no network; no tokenizer library offline), and it is **not claimed** — `verify/tokens.py` ships a pre-token proxy stamped `PROXY, NOT A TOKENIZER MEASUREMENT` with a test asserting it never claims otherwise. Measured density is **2.53 full-corpus** and **3.03** on a 40-PDF sample, both *below* the ruled floor, so the band is unreachable rather than optimistic. The estimator sets `ratio_refuted` and rebuilds the range from the text's own structure; the GUI states the basis and is guarded by a test that **the basis line contains no digits at all**. **D-03 is pending re-ruling by Alex** — recommendation ≈2.3–3.0 for table-heavy MPR text.

**6. Three inherited principle violations in the vendored MIP 3.9 extractor, all fixed.** (a) OCR engine construction called `enable_os_trust()` to permit a model download — a network call, and Principle 4 admits none; models now load locally and the selftest proves cold construction with sockets disabled. (b) Per-line OCR confidence was discarded — now captured. (c) An extract cache was written outside the matter folder — **removed, not relocated**, because relocating satisfies §10's letter while defeating the determinism proof (runs 2..N would replay cached bytes). Two more found: an AI captioning hook (§12 forbids it) and a `.msg` scratch file outside the output root.

---

## Gaps we are disclosing rather than having Codex find

- **Acceptance criterion 9 is CANCELLED, not met** (D-19, Alex, 2026-07-31). Tesseract was written off; rapidocr ships unconditionally. The artifact is a rapidocr *characterization* (mean page confidence 0.8628; 3 of 17 pages below the 85% threshold; 5.74 s/page), never a comparison. **The shipped OCR engine has never been benchmarked against an alternative on this corpus.**
- **Acceptance criterion 4 (Bates ≥99%) was not attempted.** It needs the MNFV stamped production (D-13); with an open finding we did not spend an OCR-heavy run on it. The detector is proven on synthetic stamps in both MNFV digit widths and on the real negative case.
- **Bates zone detection is text-position, not page geometry** — first 3 / last 4 lines. Finds header/footer stamps; would miss a mid-page stamp. Judged not worth a contract amendment; disclosed.
- **Two runs are not thirty.** The 30-run/30-seed proof is on the fixture corpus; the real corpus has two. Complementary, not equivalent.
- **`.msg` is vendored but unexercised** — nothing in the dependency set can author one.
- **Sprint-1 GUI runs on a mocked pipeline.** Real wiring is Sprint 2. A standing, non-dismissible amber bar states on screen that the figures are fixture data, so a screenshot cannot escape as if measured.
- **§10's 60-minute target is not met** (~80 min measured) and was not restated as a target.
- **`DOCIQ_RETRY_MAX` (500) and `DOCIQ_RETRY_BUDGET_S` (1800)** bound the retry pass and are disclosed when they bite.

---

## Review preamble — severity gating (ratified 2026-07-20, binding on this review)

1. **D-severity findings never trigger a re-review round.** Record them; they are folded at convenience.
2. **Test-harness and tooling code is held to a lighter bar** than shipping pipeline code.
3. **Seam review happens at the manifest**, not per-file.
4. **Non-convergence after two rounds on one item → descope it**, rather than a third round.
5. **Performance budgets are advisory** unless a measured regression is shown.
6. **Manifest hygiene is scripted**, not hand-audited.

Additionally, per a standing ruling: findings that assume an attacker model **beyond in-process Python in an internal desktop application** are gate questions, not defects. DocIQ is a single-user offline Windows tool handling the operator's own matter files. We will contest such findings as scope questions and close genuine defects gladly.

## What we are asking for

A **pipeline-core review**, not a merge gate. Priority order: (1) the determinism argument and where the retry disclosure lives; (2) Principle 1 — any path where a page or document can vanish without a log entry; (3) the `.pptx` fault, if you can find a mechanism we could not; (4) D-04 identity assignment and collision-freedom; (5) the honesty of the token estimator's provenance claims.

Please write your verdict as a tracked file at `docs/codex_reviews/sprint-1_2026-07-31_codex.md`, commit and push it to `build/sprint-1`, and hand Alex a one-line pointer.
