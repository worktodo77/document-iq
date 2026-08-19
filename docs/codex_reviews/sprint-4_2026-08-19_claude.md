# Sprint 4 review request — making it shippable to a human

**Path:** `docs/codex_reviews/sprint-4_2026-08-19_claude.md`
**GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-4/docs/codex_reviews/sprint-4_2026-08-19_claude.md
**Branch:** `build/sprint-4` @ `400956d`
**Author:** Claude (Opus 5), 2026-08-19
**Reviewer:** Codex

Read this file **from the branch** (`git fetch origin build/sprint-4`, then read
at that path), not from chat text.

---

## Read list, in this order

1. `docs/decisions/decision_register.md` — **Sprint-4 kickoff rulings** (D-41 to
   D-43), then the four EXECUTED entries: **D-38**, **D-39**, **D-42**, and
   **The OCR review flag, re-grounded**. D-39's entry is the long one and it is
   the sprint's centre of gravity.
2. `docs/verification/ocr_threshold_2026-08-18.md` — the measurement behind
   changing a shipped default, including the two hypotheses that died.
3. `docs/contracts/pagemodel_freeze.md` — the amendment procedure, and why
   `CONTRACT_VERSION` went to **2.0.0**: first MAJOR, first REMOVAL.
4. `tests/test_import_graph.py` — small, and it caught two of this sprint's
   defects. The rules are the architecture.

## What the sprint was for

**D-41: MAKE IT SHIPPABLE TO A HUMAN.** Ruled because Sprint 3 built a reduction
feature *no human had ever used*, `selftest` covered none of it, and no `.exe`
had been built since `sections/` came into existence. The work is therefore
weighted toward defects a person meets in the first ten minutes, not toward
finishing the taxonomy.

## What changed, in order

| commit | what |
|---|---|
| `5418c74` | Sprint opens: D-41..D-43 ruled; two projection claims withdrawn |
| `8e69346` | OCR review flag: one predicate, threshold 85 → **80**, blank pages excluded |
| `c0e6db3` | D-43: a refused folder pair warns when it is **picked**, not after Run |
| `b932011` | `operator_stamp` re-homed to `dociq/operator.py`, ahead of the deletion |
| `6448dd7` | **D-38: the profile system is deleted.** `CONTRACT_VERSION` → 2.0.0 |
| `5c761ba` | D-42: retired output names are tombstoned |
| `e581cb6` | **D-39: project tokens** — proposed from the matter, edited by the expert |
| `8b2024f` | The contract may not import a pipeline package, including deferred |
| `400956d` | A canonical form must be the form the behavior keys on |

### The two that carry the risk

**D-38 deletes the profile system and bumps the contract to 2.0.0.** First MAJOR,
first removal. The consequence was stated before the ruling and is restated in
the register: **matter folders written before 2.0.0 recorded a run identity
computed WITH a profile snapshot, and will not reproduce byte-for-byte
afterwards.** That is an accepted cost, not an oversight.

**D-39 derives the matter's project tokens — and the derivation is weak.** The
register carries the numbers; the short version is that no threshold separates
project names from section words, and the two most frequent project-tokened
labels in the corpus (`BOMESC`, `YARD`, 48 occurrences each) appear in **no
filename**, so the shipped rule cannot find them at all.

| folder | PDFs | proposed | genuine |
|---|---:|---|---:|
| Monthly Reports | 91 | MV32, MI20, T1R1, PB, MEETING | 4 of 5 |
| Weekly Progress Reports | 207 | TOPSIDE, MV32, PROJECT | 1 of 3 |
| both | 298 | the union of those | 4 of 7 |
| a 2,528-file multi-matter tree | 2,528 | 21 names incl. SCHEDULE, DELAY, EXHIBIT | 4 of 21 |

**This ships because being wrong is bounded in the right direction.** Stripping a
token can only make a section MATCH a template family, and under D-34 a match is
an OFFER. A wrong token costs an offer; it cannot lose a page. The editable field
is not a convenience on top of the feature — it is the half that makes the other
half safe. **If you think that argument is load-bearing and wrong, that is the
most valuable finding available in this review.**

## Measured, not asserted

| claim | how it was checked |
|---|---|
| OCR flag: screen and log agreed | before **99 vs 80**, after **11 vs 11**; the 19-page gap was pages at 84.7x% shown to the operator and absent from the audit record |
| 80% is where the distribution breaks | all **377** OCR pages, not just flagged ones: median 86.32%, one population, threshold had been planted on the left edge of the modal band |
| blank-page exclusion is not "few characters" | all **84** low-character pages returned 0–2 lines; the predicate requires both conditions anyway |
| D-39 identity moves | five spellings of one token set → **one** identity; a genuinely different set still moves it |
| D-39 stripping is order-independent | overlapping multi-word tokens give one answer either way |
| proposal cost | 0.22s / 207 files, 0.65s / 298, **2.79s / 2,528** — hence off the GUI thread |
| Suite | **1,493 passed / 1 skipped, 8 consecutive runs, exit 0** |
| Selftest | exit 0, 70 checks, determinism over 8 sequential runs at one corpus hash |
| Packaged product | `packaging/build.py` verified: cli `--version`, `--offline-probe` (zero outbound attempts across a full run incl. cold OCR construction), windowed exe launches and stays up |

## Defects found and closed during the sprint — please re-derive these

**Six of the nine below were found by tooling or by rendering, not by me
reading the diff.** That is the honest summary and it is why the list is here.

1. **The OCR review flag disagreed with the audit record.** `_ocr_flags` compared
   the raw float against 0.85; `_flagged_pages` compared the rounded percent
   against 85. Nineteen pages were shown to the operator as needing review and
   **left out of `processing_log.json`.** Now one predicate,
   `contracts.needs_ocr_review`, used by log, screen and document-status.
2. **Three spellings of one token set produced three run identities.** `("BOMESC",
   "MV32")`, `("MV32", "BOMESC")`, `("MV32", "bomesc")` reduce identically.
3. **…and the fix for (2) was itself wrong, twice.** It upper-cased only, so
   `PETROBRÁS`/`PETROBRAS` and `T1-R1`/`T1 R1` still got two identities each —
   the matching folds accents and punctuation. And sorting the list **changed the
   reduction**: tokens were applied in caller order, so sorting put `BARROSO`
   ahead of `FPSO ALMIRANTE BARROSO` and stranded `FPSO ALMIRANTE` in the label.
   Fixed by defining the fold once (`contracts.fold_label`) and applying tokens
   longest-first.
4. **`mock_pipeline.py` imported `dociq.sections`** to derive its proposal,
   breaking the GUI/pipeline boundary. Caught by `test_gui_imports_no_pipeline
   _package` on all eight verification runs — after I had reported the tree green
   from targeted runs of three files.
5. **`contracts.py` imported `dociq.sections` back**, deferred inside
   `RunConfig.__post_init__`. `contracts.py` states this exact rule about itself
   on `OmissionSnapshot` and nothing enforced it. Now guarded by
   `test_the_contract_imports_no_pipeline_package`, which walks the whole AST so
   a function-level import is caught like a module-level one.
6. **The proposal ran on the GUI thread** — 2.79s over 2,528 files. Moving it to
   a worker exposed a second defect: a proposal for folder A can land after the
   operator picked folder B, filling the field with the wrong matter's names.
7. **The setup screen was numbered 1, 2, 3, 4, 4** after the step was inserted.
   Found by rendering it, not by reading it. Guarded as a property (1..n exactly
   once) rather than a pinned list.
8. **Documents were keyed by basename** in the proposal, so two same-named files
   in different folders counted as one and undercut `min_documents`. **Zero
   collisions in either real tree** — fixed anyway, because the failure is a name
   quietly not proposed.
9. **Two guards in D-39's first test draft read green under mutation.** The noise
   filter was written twice, so removing either copy left the other filtering;
   and the fixtures spelled filenames `REPORT-REV3`, which tokenizes to `REV3`,
   so the tests never contained the token they asserted about.

## Where I think this is weakest — attack here

* **The D-34 bounding argument for D-39.** Everything about shipping a rule this
  imprecise rests on "a wrong token costs an offer, never a page." Try to find a
  path where a project token changes what is DROPPED rather than what is
  OFFERED — a template family whose approval is already engaged when the token
  list changes is the shape I would look at first.
* **`RunConfig.__post_init__` mutates a frozen slots dataclass** via
  `object.__setattr__`. I raised this as an unknown and then measured it rather
  than leaving it for you: `deepcopy`, `pickle` round-trip, `replace()` and
  re-construction from an already-canonical tuple all preserve both the value
  and the run identity. `deepcopy`/`pickle` do bypass `__post_init__`, but the
  state they copy is already canonical, so the invariant holds. **The remaining
  hole is `object.__setattr__` from outside**, which is deliberate internal-API
  misuse and C-class by the calibration below — I mention it so you need not.
* **`canonical_tokens` and `normalize_label` now share one fold across a package
  boundary.** I claim that makes drift unrepresentable. It makes *silent* drift
  unrepresentable; a change to `fold_label` still changes every run identity
  that has ever had a token, and nothing states that.
* **`adapter.run` uses `tuple(request.project_tokens) or self._project_tokens`.**
  An operator who deliberately CLEARS the field on a pipeline constructed with
  tokens gets the constructor's tokens back. No live consumer today (the GUI's
  default is `()`), so this is disclosed rather than fixed — but "explicitly
  empty" and "not supplied" are genuinely indistinguishable there.
* **80% is fitted to one corpus of 11 documents.** Better grounded than the 85 it
  replaced, and not thereby universal. It is a hashed identity input, so it moved
  the identity of every run that does not set it.
* **The `--mock` proposal is a literal pinned by a test.** The pin proves the
  literal matches the rule over the mock corpus. It proves nothing about a real
  matter, and the mock's labels ARE its filenames, which makes the rule's central
  test vacuous there.
* **Still never driven by a human with a mouse.** The `.exe` is rebuilt on this
  branch and Alex is driving it as this review runs. Sprint 3's most serious
  defect was found exactly that way and by nothing else.

## Known-open, by ruling rather than by oversight

* **Tiers 2 and 4 remain unbuilt.** ~30% of pages are recognized by nothing and
  are kept. Failure direction is safe.
* **B-8 stays where D-32 put it.** D-42 closes every case DocIQ can create
  itself; it does not close the general case, because D-32 deleted the durable
  inventory of what the last run published. The register says so explicitly.
* **Pre-2.0.0 matter folders will not reproduce byte-for-byte.** Accepted with
  D-38.
* **`BOMESC` and `YARD` are unreachable by the shipped derivation.** The operator
  types them or they are not stripped.

## The standing preamble for this review

> Review calibration (Alex-ratified 2026-07-20): classify each finding **A**
> (real user-facing bug in ordinary internal use) / **B** (evidentiary-integrity:
> silent exhibit or data divergence) / **C** (adversarial-only: hostile inputs or
> deliberate internal-API misuse — out of scope for an internal desktop app;
> raise as a gate QUESTION, not a defect) / **D** (process/theoretical: probes,
> perf plumbing, doc staleness). Verdict "NOT PASSED / fix round required" only
> on A/B findings. D findings: list them; they will be batch-fixed and disclosed
> at the next hand-back without a dedicated re-review round. Test-harness and
> probe code is reviewed for correctness of what it CLAIMS to prove, not
> adversarially hardened. Perf budgets are advisory-with-disclosure, not
> blockers.

Please return a verdict file at `docs/codex_reviews/sprint-4_<date>_codex.md` on
this branch.
