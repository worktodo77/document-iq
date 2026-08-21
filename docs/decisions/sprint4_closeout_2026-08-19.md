# Sprint 4 close-out, and what Sprint 5 should be

**Date:** 2026-08-19
**Branch:** `build/sprint-4` @ `2e0204e` — **not merged to main**
**Status:** Codex round 4 outstanding; the packaged `.exe` has still not been
driven by a person.
**Author:** Claude (Opus 5). Every figure below was checked against the code or
the run output, not recalled.

---

## 1. What D-41 asked for, and what it got

> **D-41: MAKE IT SHIPPABLE TO A HUMAN.** … Sprint 3 built a reduction feature
> that **no human has ever used**, `python -m dociq.selftest` covers **none** of
> it, and no `.exe` has been built since `sections/` came into existence.

Three concrete gaps. Scored honestly:

| D-41's stated gap | status |
|---|---|
| No `.exe` built since `sections/` existed | **CLOSED.** Built and verified four times this sprint; current artifact `build_out/dist/DocumentIQ-win64.zip`, both executables run from the built folder, offline probe zero outbound attempts across a full pipeline run including cold OCR construction. |
| The reduction feature has never been used by a human | **OPEN.** Unchanged since the sprint opened. |
| `selftest` covers none of the reduction feature | **ESSENTIALLY OPEN**, and this is the finding of the close-out. |

### The selftest gap is narrower on paper and unchanged in substance

D-41 measured `bates`, `omission`, `waterfall`, `approv` and `template` at zero
occurrences in `selftest.py`. Today `omission` appears 4 times and `approv` 5.
That reads like progress and is not.

**Measured: the selftest drops zero pages.**

```
PASS  page accounting reconciles to zero discrepancy
      pages in 25 = kept 25 + dropped 0 across 17 document(s)
```

Its only approval-related checks are that adding an `OmissionSnapshot` moves the
run identity, and that a run nobody ruled on records an empty approval set.
Both are *identity* probes. The shipped self-diagnostic still never engages a
lever, never approves an omission, never drops a page, and never verifies that a
dropped page is attributed in the log.

So the tool a colleague would run to check their install still cannot tell them
whether the feature that removes pages from their evidence works. `bates` and
`waterfall` remain at literal zero.

This is not a defect anyone introduced — it is the third of D-41's three gaps,
and the sprint closed one of them.

## 2. What landed

| ruling | what shipped |
|---|---|
| OCR review flag | Screen said 99, log said 80, from two predicates over one run. Now one predicate; threshold 85 → **80**, fitted to the measured distribution (median 86.32%, one population); blank pages excluded from review and counted instead. **99 → 11 on both surfaces.** |
| D-43 | A refused folder pair warns when it is **picked**, not after Run. |
| D-38 | Profile system deleted. `CONTRACT_VERSION` 1.9.0 → **2.0.0** — first MAJOR, first removal. |
| D-42 | Retired output names tombstoned. B-8's first real instance, closed for every case DocIQ can create. |
| D-39 | Project tokens proposed from the matter, editable on the setup screen. |
| A-22 | An approval is scoped to the project tokens it was reviewed against. Contract **2.1.0**. |
| A-23 | An approval is bound to a **recognition fingerprint** — tokens, template id and version, and whether OCR ran. Contract **2.2.0**. |

**Verified at `2e0204e`:** 1,529 passed / 1 skipped over 8 consecutive runs;
selftest exit 0 at 70 checks with one corpus hash over 8 determinism runs;
amendment registry OK at 25 entries; `git diff --check` clean.

## 3. What the review found, and what that says about the work

Four Codex rounds, **nine blockers**, all closed. The distribution matters more
than the count:

* **Two refuted claims the work rested on.** B-1 showed D-39's load-bearing
  argument — "a wrong token costs an offer, never a page" — was false for the
  workflow the feature exists to support. B-R2-1 showed A-22 recreated the exact
  identity collision its own amendment said it closed.
* **Two rounds found defects created by the previous round's fixes.** A-R3-1
  crashed the public `run(config)` default path; A-R3-2 left a false approval
  message on screen. Both were introduced by the fixes for rounds 1 and 2.
* **I found none of the nine.** Every one came from the reviewer.

The common shape, recorded because it generalizes: **a rule stated in prose that
nothing enforced.** Seven instances this sprint, including a boundary rule
written in the docstring of the very module that broke it. The durable output is
that the guards are now *derived* rather than hand-listed — a test reads the
dataclass and the construction site; a test reads the whole import graph; a test
reads every module's symbol table.

**A green suite was three times not evidence.** 1,521 tests over eight runs said
nothing about the documented default invocation, because every case in the suite
supplied explicit options.

## 4. Acceptance criteria

| # | criterion | status |
|---|---|---|
| 1 | End-to-end on the reference set, consumed by evidence-mining | **DISCHARGED** (D-20 split: Path B at full scale, 368 docs; Path A on a stated scoped subset) |
| 2 | Page accounting reconciles to zero discrepancy | **DISCHARGED** — 18,556 in = 18,556 kept + 0 dropped on the full corpus |
| 3 | Page markers resolve on a 50-marker spot-check | **DISCHARGED** |
| 4 | Bates detection ≥ 99% on stamped sets | **NOT MET.** Last measured **91.512%** (568/568 native, 25/80 OCR'd). 92.130% is a *projection* — never quote it flat. |
| 5 | Master-index reconciliation and Doc ID assignment | **DISCHARGED** on the real 9,259-row index |
| 6 | Runs fully offline | **DISCHARGED**, re-proven on the packaged build |
| 7 | Byte-identical repeat runs | **DISCHARGED** — one corpus hash over 8 sequential runs |
| 8 | Handoff: Path A package accepted, Path B consumed | **HALF-DISCHARGED.** Path B proven. Path A's *"accepted by a Claude Project"* half needs a person with a browser and has never been done. |
| 9 | OCR bake-off | **CANCELLED** by D-19 — closed as cancelled, not as met |

Two are outstanding: **4** (measured shortfall) and **8** (needs a human).

## 5. Carried forward — open, and owner-accepted

* **D-39's derivation is weak and shipped anyway.** 4 of 7 genuine names on the
  real corpus; **`BOMESC` and `YARD` cannot be found at all** — they appear in no
  filename. It is safe only because a wrong token costs an offer, and Codex
  proved even that argument had a hole. Never describe it as accurate.
* **Tiers 2 and 4 unbuilt.** ~30% of pages are recognized by nothing and kept.
* **B-8's general case.** D-42 closes every case DocIQ can create; a file left by
  another tool, or by a build older than the cleanup list, is still unaccounted
  for. D-32 deleted the durable inventory that would close it.
* **No inter-process lock anywhere in `src/`.** `staging_layout()`
  unconditionally removes `.dociq/staging/`; two runs against one matter folder
  is undefined. A correctness risk, deferred rather than closed.
* **The Bates prompt screen has never been rendered.** `SCREENS` in the GUI grid
  omits it, and the grid asserts its own completeness against that list — so
  "every screen × every state" passes over a hole.
* **`ocr_enabled` lives on `WalkOptions`, not the contract.** Every other
  recognition input is a `RunConfig` field. The fingerprint takes an effective
  value the contract cannot itself express.
* **The offline-probe failures from Sprint 2 remain unreproduced and
  unattributed.**

## 6. What Sprint 5 should be

Four candidates. My recommendation is first, with the reasoning rather than the
conclusion.

### A. Make the self-diagnostic prove the feature — and drive the product

**The unfinished half of D-41.** Two runs of one matter through the packaged
`.exe`: engage a lever, drop pages, withdraw it, re-run. Then extend `selftest`
so it does the same thing unattended — approve, drop, and assert the dropped
page is attributed in the log — so the check a colleague runs on their install
covers the feature that removes their evidence.

*Why first:* it is the objective the sprint was ruled to achieve and did not,
and it is the one verification four review rounds could not substitute for.
Sprint 3's single worst defect was found by driving a screen and by nothing
else. Cheap, and it closes criterion 8's outstanding half on the way.

### B. Criterion 4 — measure before funding

The register already says the measurement needs **no code change**: the 51
residual pages are named in a committed artifact, and the claim that most carry
a repairable token is labelled a *prediction* in its own document. Measure that
first; only then decide whether targeted footer re-OCR is worth a sprint.

*Why not first:* it is the only formally unmet criterion, but it is a known
quantity with a known method, and it does not need the product driven.

### C. Finish the taxonomy — tiers 2 and 4

~30% of pages recognized by nothing. Failure direction is safe (unmatched pages
keep), so this is capability, not correctness.

*Why not first:* it grows a feature no human has yet used.

### D. The citation-verification checker

The problem you actually came in with. Still parked on one question: what you
would hand it — a structured list, a `.docx`, or a live lookup against the
corpus. New scope, and it should not start while Sprint 4 is unmerged.

---

**Nothing merges to main without explicit authorization.** Round 4's verdict is
outstanding, and rounds 3 and 4 each found defects introduced by the previous
round's fixes — so a first PASS is not by itself evidence that a fix round was
safe.
