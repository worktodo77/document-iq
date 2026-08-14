# DocIQ Sprint 2 — Codex review #2 (the merge gate)

**This file:** `docs/codex_reviews/sprint-2_2026-08-04_claude.md`
**On GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-2/docs/codex_reviews/sprint-2_2026-08-04_claude.md
**Branch under review:** `build/sprint-2` (fetch it; do not review from pasted chat text)
**Base:** `main` @ `e7fd4eb` (Sprint 1) → 88 files changed, +19,339 / −381
**Author:** Claude (Opus 5), 2026-08-04
**Role split:** Claude implements · Codex reviews · Alex gates and authorizes every merge to `main`.

```
git fetch origin && git checkout build/sprint-2
```

---

## Review calibration — please apply this

> Review calibration (Alex-ratified 2026-07-20): classify each finding **A** (real
> user-facing bug in ordinary internal use) / **B** (evidentiary-integrity: silent
> exhibit or data divergence) / **C** (adversarial-only: hostile inputs or deliberate
> internal-API misuse — out of scope, raise as a gate QUESTION not a defect) / **D**
> (process/theoretical: probes, perf plumbing, doc staleness). Verdict "NOT PASSED /
> fix round required" only on **A/B** findings. **D** findings: list them; they will be
> batch-fixed and disclosed at the next hand-back without a dedicated re-review round.
> Test-harness and probe code is reviewed for correctness of **what it claims to
> prove**, not adversarially hardened. Perf budgets are advisory-with-disclosure, not
> blockers.

D-10 designates **this** review as the merge gate for the whole programme.

---

## Read in this order

1. `docs/decisions/decision_register.md` — **D-20 … D-29** are this sprint's rulings, plus the sections following them. Read D-29 carefully; its headline figure is a projection and says so.
2. `docs/contracts/amendments.md` — **A-11, A-11b, A-12, A-13, A-14** and the "APPLIED" section.
3. `docs/design/section_taxonomy.md` — the omission taxonomy, measured rather than assumed.
4. `docs/verification/` — `track_{d,e,f}_sprint2`, `acceptance_1_8`, `bates_d25`, `bates_confirmation`, `rehearsal_fixes`, `claims_sweep`, and the machine output under `artifacts/`.
5. Code: `src/dociq/adapter.py`, `src/dociq/gui/pipeline.py` (the frozen seam), `src/dociq/emit/handoff.py`, `src/dociq/identify/bates.py`, `src/dociq/verify/offline.py`, `packaging/`.

---

## What Sprint 2 delivered

- **The real pipeline under the GUI.** `get_pipeline()` returns `dociq.adapter.RealPipeline`. It lives at `src/dociq/adapter.py`, not in `gui/`, because `tests/test_import_graph.py` forbids exactly the packages it needs and exempting a file from that check would have hollowed it out.
- **Staged, atomic emit.** Deliverables are built in `.dociq/staging/`, marked ready, then swapped;
  an interrupted swap is rolled forward by the next run before it reads anything. This closed the
  one gap Sprint 1 wrote down as unproven rather than leaving to be discovered.
  > **CORRECTION (2026-08-04).** This bullet originally read "gated there, marked, then swapped".
  > That was FALSE and Codex review #2 filed it as B-1: Stage 6 computed page accounting and built
  > the manifest and then marked ready and swapped **unconditionally**, so a red set replaced the
  > last good deliverables and `PipelineOutcome.ok` merely reported it afterwards. The gate now
  > exists (`dociq.pipeline._refuse_publication`); the claim is corrected here rather than quietly
  > made true, because this sentence is what a reviewer was asked to rely on.
- **The §6 profiling checklist and the D-14 waterfall** on real figures, with D-21's capacity line as a named, sourced reference and never a target.
- **§8 Paths A and B**, including the first upload package this project has ever built.
- **PyInstaller one-folder packaging** with bundled ONNX models (D-22), measured: 393.1 MB payload, 178.2 MB zip, warm launch 0.320–0.962 s over 29 launches, and `--selftest` passing **inside the frozen exe** (70 checks, byte-identical over 8 seeds).
- **§4 Stage-3 Bates confirmation** (A-14), which is what makes Bates work at all — see below.

**Suite:** 1,187 tests. **8 consecutive full-suite runs on the fully merged tree at `c25d8e0`: 8/8 green, zero failures** — completed 2026-08-04 and recorded here only once it had. Every constituent branch also ran ≥8 green before merge; `python -m dociq.selftest` exits 0 with 70 checks.

*(This line originally asserted that result while the sequence was still executing, was corrected to say so, and is now stated with the measurement behind it. Writing a verification result from the plan rather than the measurement is the exact defect class this sprint's rehearsal review spent its time closing, and a relay that commits it is not one a reviewer should trust. Note also: a single full pass now exceeds ten minutes — the claims sweep added concurrent determinism repetitions and an 8-thread OCR stability probe — so a six-minute cap will not see one finish.)*

---

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | MODEC end-to-end | **PASS** — 368 documents, 103.0 min, one from-scratch run through `RealPipeline` |
| 2 | Page accounting to zero | **PASS** — 18,556 in = 18,556 kept + 0 dropped |
| 3 | Markers resolve | **PASS** — 18,556 markers parsed, zero format errors |
| 4 | Bates ≥ 99% | **NOT MET — 92.130% (projected)**, ruled shipped-as-is by D-29 |
| 5 | Master-index reconciliation | **PASS** (Sprint 1, 9,705 IDs vs the real 9,259-row index, 0 collisions) |
| 6 | Fully offline | **PASS** — 0 network attempts, unpackaged and packaged, corroborated at OS level |
| 7 | Byte-identical repeat runs | **PASS**, with a narrowed claim — see "what we do not claim" |
| 8 | Handoff A + B | **PASS**, with one half unobserved — see below |
| 9 | OCR bake-off | **CANCELLED** by D-19, not met |

---

## The four things most worth your attention

### 1. Criterion 4's headline is a PROJECTION, and D-29 now says so
**597/648 = 92.130%** is `568 + 29`: the 568 native is Track F's earlier full-corpus measurement, the 29 OCR'd is measured on a deliberately OCR-heavy 61-document subset whose own headline accuracy is 58.197% and is **not** comparable to a full-corpus figure. The last end-to-end **measured** full-corpus number is **593/648 = 91.512%**. Constant across every variant tried: **0 wrong, 0 false positives** — every failure is an absent locator, never an incorrect one.

An earlier version of the register stated 92.130% flatly as "measured" while the JSON sat untracked in a worktree and the verification note still read "did not complete". Our own rehearsal review found it in one grep. Both artifacts are now committed under `docs/verification/artifacts/`.

### 2. Bates produced 0.000% through the shipped GUI, for the length of the sprint
§4 Stage 3 requires the operator to confirm the detected format. Track D raised the missing callback as stop-the-line; it was acknowledged and never applied. The adapter passed `auto_confirm_bates=False`, no screen asked, the format never reached CONFIRMED, and `apply_bates_reported` returned every document unchanged. Measured on real MNFV production (10 documents / 369 pages) through `RealPipeline`:

| `confirm_bates` | pages with a locator |
|---|---:|
| `None` — the state we were about to ship | **0 — 0.000%** |
| operator confirms | **328 — 88.889%** |

Both `None` runs warned that a format **was detected and not applied**. Closed by A-14. The general lesson is not about Bates: **raising an amendment is not adopting one**, and nothing in the process was watching that gap — the same failure hit A-12, raised by two tracks and applied by neither, which left Path A's button permanently disabled behind its own polite explanation.

### 3. What criterion 7's proof actually covers
`verify/determinism.py::prove()` now runs repetitions **concurrently** as well as sequentially, and a new probe exercises the shared RapidOCR session across 8 threads. Measured over 32 real pipeline runs: sequential and contended both produced one hash. But pinning ONNX threading was **considered and rejected** — it would require monkeypatching rapidocr's session construction, and a silent no-op on the next release is worse than a disclosed gap. The claim is narrowed explicitly in the module docstring with four named exclusions rather than left to imply more than it proves.

### 4. The reduction feature is smaller than the mockups implied, and the taxonomy says so
Measured over 36 documents / 1,535 pages / 3.34M characters: schedule and activity tables are **33.9%** of corpus text; page furniture **8.0%**; TOC **5.4%**; **photographs 0.2%**. Our own Sprint-1 mockups advertised photo logs as the largest lever at −2.49M tokens; that was wrong by about two orders of magnitude, because a photo page carries almost no text. The honest reduction story is furniture + TOC (13.4%, largely automatic) plus schedule tables (33.9%, the expert's call under D-27) — and D-20 had already concluded by a different route that nothing makes a matter of this class fit direct context.

On recognition: 40% of PDFs carry a **PDF outline** with a real section vocabulary, which is a lookup rather than an inference. Heading-text matching was **excluded on measurement** — it returns `FPSO ALMIRANTE BARROSO MV32` 1,017 times and `PETROBRAS` 981 times. It finds letterhead, not sections, and would have passed review.

---

## Pre-handoff: our own adversarial review, and what it found

Per standing rule, an independent adversarial reviewer ran before this hand-off. It produced **4 A-findings, 6 B, 7 C**. All are fixed and merged; they are disclosed here rather than presented as a clean sheet.

- **A1/A2** — the register asserted Bates figures the repository could not support (above).
- **A3** — a subset upload package fell back to `shutil.copyfile` on any read failure, shipping the **whole matter's** `sources.json` into a 2-document package. `assert_only_sanctioned` could not catch it, because `sources.json` is a sanctioned *name*. It now raises. Reproduced, not argued.
- **A4** — Bates unreachable through the GUI (above).
- **B2** — the A-11b amendment claimed all four positional-rebuild sites were fixed and its probe "cannot be fooled". Both overstated: `ReductionPlan` was still rebuilt positionally *inside the method that had been fixed*, and the probe matched `ReductionLever(...)` but not `pipeline.ReductionLever(...)`. The probe now enumerates every frozen presentation record — and caught five fresh violations on the very next merge.
- **B3** — `tests/test_import_graph.py` skipped relative imports on the stated premise that "a relative import cannot reach another package", which is false. `from ..ingest import dating` inside `gui/` passed all 14 tests. That check is cited by both `gui/pipeline.py` and `adapter.py` as the reason the adapter lives where it does.
- **B4** — `ocr_available()` checks presence, never capability. The Sprint-2 burn (packaged build silently producing no OCR) happened *inside* inference, where a bare `except Exception` makes a dead engine indistinguishable from a few bad pages. Instance had been fixed; the class had not.
- **C1** — one package told its operator *"Fits directly in a Claude Project"* while telling the recipient, in the README they actually read, *"About 181–197% of direct-context capacity — retrieval (RAG) mode."* Two capacity literals, two verdicts. Now one.

Five **tests that could not fail** were also found and repaired, each proven vacuous by mutation rather than by argument — including one that claimed to prove atomic swap recovery and **passed with recovery disabled**, and one that claimed the GUI writes no files while inspecting an unrelated, untouched directory.

---

## What we do NOT claim

- **"Accepted by a Claude Project" was never observed.** Uploading is a network action; nothing was uploaded. File types, sizes, count, structure and README are proven. That half needs a person with a browser. The 30 MB per-file limit is an assumption; the file-count limit is not enforced and says so.
- **Criterion 4 is not met**, ruled shipped as such (D-29). On a scanned production an expert gets a locator on roughly a third of OCR'd pages — and never a wrong one. D-19's Tesseract benchmark was offered to Alex and declined twice; that liability now carries a measured page-level cost.
- **Nobody has driven the GUI with a mouse.** All GUI assertions are offscreen.
- **The 103-minute run is an upper bound** — another OCR job held the machine throughout. A clean idle-machine OCR-on figure does not exist. §10's 60-minute target is missed and deliberately not restated.
- **The Bates after-number with OCR *on* was never measured** — two attempts killed at ~48 min each. The before-number is 0.000% with OCR on and off.
- **One unresolved flake, disclosed rather than tuned away.** `test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN` failed **2 times in 24 concurrent jobs**. First attribution looked conclusive (baseline 6/6 green, branch 2/6 red, branch-deselected 6/6 green); two further trials then came back 12/12 green with everything selected. **Attribution is not established, and neither is the opposite.** Not tuned, not skipped, not xfailed. The offline guard was separately exonerated by direct probe: 12 guarded runs at concurrency 4, zero spawn attempts.
- **Criterion 7's real-corpus evidence** remains a 45-file stratified sample from Sprint 1 (full pairs ruled too slow at ~3 h), and the acceptance run is **one** run — no determinism claim rests on it.
- **`subprocess` is now guarded** (`ProcessSpawnAttempted`, 16 entry points) — a change made because the disclosure list did not name it, not because an attempt was observed.

---

## Open, deliberately unruled

- The shipped **3,600 s per-file timeout** fired on **six** documents in the acceptance run, not the two previously recorded; all six were recovered in full by the serial re-read. That makes it **load-dependent** and points at the extraction pool rather than the files. The default was not raised: root cause unidentified, right value unknown, and it is a hashed run-identity input under A-04, so it is Alex's ruling rather than a side effect of an acceptance note.
- **D-24's standard templates are designed but not built.** `docs/design/section_taxonomy.md` §6 lists four open sub-questions, including the approver problem: `SectionRule.validate()` refuses a DROP without a "who approved", and a template is by definition not approved by the expert on the matter.

---

## The question we would most like answered

Two amendments were **raised correctly and never adopted** this sprint (A-12 by two tracks, A-14 by one), and in both cases the product silently did nothing while every individual agent behaved correctly — refusing to work around a frozen seam, documenting the gap, moving on. The rehearsal review caught both only because it was told to look. **Is there a structural check that would have caught the gap between "amendment raised" and "amendment wired end-to-end" without depending on someone remembering?** That is the failure mode we most expect to repeat.
