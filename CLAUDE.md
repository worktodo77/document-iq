# DocIQ — project instructions

LI Document IQ: prepares faithful, traceable, token-efficient project evidence
for mining with Claude, and verifies the quotations, figures and citations that
come back against the original sources.

Alex's global rules at `C:\Users\Alex\CLAUDE.md` govern authorization (what may
be run without asking, what needs an explicit yes, how to ask a question). They
are not repeated here. This file covers only what is specific to this repository.

---

## Read order for a fresh session

Read these in order. Do not start from the README — it is a front page, not a
specification, and it has been stale before.

1. **`docs/decisions/decision_register.md`** — every ruling, and the current
   value of every measured figure. **The register wins over any other file.**
   Read the newest sections first; they are at the top.
2. **`docs/reviews/confirmed_scope_after_interview_2026-09-09.md`** — the
   current product scope, S-01..S-25, from Alex's own answers. This supersedes
   the older "fully offline deterministic reducer, no interpretation" framing as
   a *product definition*.
3. **`docs/reviews/scope_governance_architecture_audit_2026-09-09.md`** — what
   is built, what is not, and what the gaps are. F-01..F-08, G-01..G-11.
4. **`docs/requirements/requirements_v1.1.md`** — the ruled baseline, amended in
   place. Carries corrections dated at the point of the claim they correct.

**Known-stale, do not quote as current:** `README.md` status section (says
Sprint 2), `docs/architecture.md` (drafted 2026-07-30, header still says "no
build code until then"), `docs/HANDOFF.md` (opens Sprint 3),
`docs/requirements/requirements_v1.0.md` (superseded historical draft),
`docs/contracts/pagemodel_freeze.md` (header still 1.0.0; current contract is
2.2.0). These are kept as history. Fixing them is tracked as G-02/G-03/G-07.

## Running things

Python lives in `.venv`. On Windows: `.venv\Scripts\python.exe`.

```
python -m pytest                    # full suite
python -m pytest tests/test_x.py::test_y -x   # one test, by FULL node id
python -m dociq.selftest            # the diagnostic a colleague runs; exits 0
python -m compileall . -q
python tools/check_amendments.py    # requirement-to-code registry
```

Source is `src/dociq/`. Tests are `tests/`. Packaging is `docs/build/packaging.md`
— use the root-based procedure, not the `document-iq-wt/track-f` path in the
older notes; that directory does not exist here.

## Evidence claims policy

This is the rule the project exists to enforce, and the one most often broken.

- **Never promote a projection, a proxy or a ratio to a measured result.** Bates
  detection is **91.512%** measured. 92.130% is a *projection* — never quote it
  flat. Token figures are *estimates under stated assumptions*, not counts;
  `verify/tokens.py` says calibration was never performed.
- **State the denominator, the corpus and the date with every figure.**
- **A waiver is not a technical pass.** D-47 records an owner-authorized skipped
  review. That is an accepted exception, not a green result. Keep implemented,
  tested, independently reviewed, owner-accepted-limitation, deferred and
  cancelled as six distinct states.
- **Do not claim extraction fidelity that has not been measured.** Page
  accounting reconciling to zero discrepancy proves pages were *counted*. It does
  not prove the content on them survived. Those are different properties and the
  product has conflated them before.

## Verification rules this project learned by being wrong

- **A green suite is not evidence about the thing you just changed.** 1,521
  tests over 8 runs said nothing about `run(config)`'s documented default,
  because every case in the suite supplied explicit options. Codex found nine
  blockers across four rounds; the agent found none of them.
- **Watch every fail-before actually go RED, by full node id.** `pytest -k`
  matching nothing exits 0. So does a `-k` filter naming a test that was never
  written.
- **`-q` suppresses the summary line.** Grepping `"N passed"` on `-q` output
  matches nothing — a repeat-run harness can report success while measuring
  nothing.
- **A killed patch script leaves the tree MUTATED.** After a surprising result,
  the first question is what `git status` says, not what the product did.
- **Do not write Python through a bash heredoc.** Backslashes are mangled — a
  `\b` once became a literal 0x08 byte inside a test regex. Write the script to a
  file, then run the file.
- **Fix the class, not the repro,** and enumerate the siblings explicitly. Asking
  for "the class" does not by itself make anyone go looking; ask for the
  enumeration as a named deliverable, and require the clean results too.
- **Withdraw the claim, not just the code.** Removing a behaviour means grepping
  for every *assertion* of it — in requirements, README, decision entries, error
  strings and test names — not just its identifier. Nothing checks that prose
  is still true.

## Scope-change process

A ruling gets a **D-number** and its own entry in the decision register.
**Check the register for the next free number before assigning one** — D-44 and
D-45 were once assigned twice, and duplicate ids in the authoritative record
resolve to two different rulings.

Committing a document does not make it a ruling. Review artifacts under
`docs/reviews/` are assessments; they amend nothing until a D-entry says so.

Any change touching a persisted locator, source identity, transformation or
verification label needs a migration decision and a behaviour test.

`CONTRACT_VERSION` is in `src/dociq/contracts.py`, currently **2.2.0**. It is
semver over the page/run contract; a removal is a MAJOR.
