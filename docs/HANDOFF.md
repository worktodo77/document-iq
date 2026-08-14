# Fresh-session kickoff prompt — DocIQ Sprint 3

Paste everything below the line into a new session.

---

start remote-control (/remote-control)

You are opening **Sprint 3** of **LI Document IQ (DocIQ)** — an offline Windows document-corpus reducer for forensic construction-claims work. Repo: `C:\Users\Alex\document-iq`.

**Sprint 2 is merged.** `main` @ `3f5384a`, clean, full suite green (~4m50s on a quiet machine), `python -m dociq.selftest` exits 0 with 70 checks. Codex review #2 — the D-10 merge gate for the whole programme — **PASSED** at `57c7cc0` after four verdict rounds and two internal adversarial reviews.

## Read these first

1. `docs/decisions/decision_register.md` — start at **"MERGE GATE PASSED"**, then **D-32** (why the publication protocol was deleted rather than fixed), **D-29**, **D-30**, and the "MEASURED: the ~20 minutes claim was wrong by 4x" section.
2. `docs/design/section_taxonomy.md` — the omission taxonomy, measured against the real corpus. **§6 lists four things not yet ruled.** This is where the product's remaining value lives.
3. `docs/codex_reviews/sprint-2_2026-08-14_claude_r6.md` — the final hand-back and its addendum.
4. `docs/contracts/amendments.md` + `amendments.toml` — 19 amendments, both halves checked by `tests/test_amendments.py`.

## Your first action: get Sprint 3 scoped

**D-10 planned two sprints and both are done. Sprint 3 has no agreed scope.** Do not start building. Put the candidates to Alex one at a time with `AskUserQuestion`, recommendation first, each with a real-life example — that is a standing rule on this project.

The candidates, with what is actually known about each:

| # | Candidate | Why it might be first |
|---|---|---|
| **1** | **Build the omission taxonomy (D-24)** | The reduction feature is *designed and not built*. `section_taxonomy.md` has ~45 section types, a tiered recognizer, and measured evidence — and DocIQ ships **one** profile, "keep every page". Blocked on the **approver problem** (§6): `SectionRule.validate()` refuses a DROP without "who approved", and a template is by definition not approved by the expert on the matter. **That is a ruling, not a coding task.** |
| **2** | **Criterion 4 — the only unmet criterion** | Bates sits at 92.130% projected / 91.512% measured against a ≥99% bar, and the shortfall is entirely OCR'd pages (36.250%). Every avenue short of changing the engine has been built and measured. **D-19's Tesseract benchmark has been offered and declined twice**; it is the remaining move. |
| **3** | **B-8 — merged open** | A deliverable an older build wrote under a name this build no longer emits stays in the matter folder permanently. Alex accepted it under D-32. Reopening it means reintroducing an inventory, which is what produced six generations of data-loss defects. |
| **4** | **The offline-probe failures** | Unreproduced and unattributed. Contention was disproved by a six-way concurrent probe that passed 6/6. This underwrites the offline claim made to clients, so it is smaller than it looks and matters more than it looks. |
| **5** | **Ship it to a human** | Nobody has driven the GUI with a mouse. No package has ever been accepted by a real Claude Project. Both are disclosed non-claims, and both are one afternoon with a person. |

**My recommendation, if asked:** 1, and open it with the approver ruling — the taxonomy is the difference between a tool that reduces a corpus and one that only measures it, and the measured evidence is already in hand (schedule tables 33.9%, furniture 8.0%, TOC 5.4% — and **photographs 0.2%**, which is why nobody should build "drop the photos" first).

## Smaller items, none blocking

- **3,600 s per-file timeout** fired on six documents, not the register's two. Load-dependent, and a hashed run-identity input (A-04) — **Alex's ruling, not a side effect.**
- **`RunOutcome.superseded_residue`** keeps a name that no longer fits its narrower contents. Renaming it needs a new amendment.
- **No inter-process lock** on the matter folder; two concurrent runs are undefined. Recorded, not fixed.
- **Inno Setup installer** deferred at D-22 (packaging is one-folder-in-a-zip).

## Standing rules that bind you

- **A green result proves nothing.** Watch every fail-before go RED. ≥8 full-suite runs; **30** for filesystem/timing/subprocess-sensitive work.
- **Timing and flake claims measured while agents are running are worthless.** We told Codex its six-minute cap was too small when the suite takes 4m40s and our own fan-out was starving it. Verify the machine is quiet and say so.
- **Fix the CLASS, not the repro** — and *a test that asserts a class by naming one member of it is how the same defect returns under a new number.* That cost three generations this sprint (F-3 → A-8 → A-8-nested). It was only fixed when the invariant moved out of the pattern list and into the planner.
- **Withdraw the CLAIM, not just the code.** Grep for every *assertion* of a changed behaviour — docs, register, docstrings, test names, error strings.
- **Stop-the-line on `src/dociq/contracts.py` and `src/dociq/gui/pipeline.py`.** Frozen. Any amendment needs an `amendments.toml` entry with an **immutable commit id** (symbolic refs are rejected) and a prose status that agrees. **A seam-only commit cannot be green** — the seam-population probe refuses a declared-but-unwired field, so wire it in the same commit.
- **Non-convergence:** if two consecutive review rounds find new siblings of one defect class in one subsystem, **stop fixing and bring Alex a descope decision.** D-32 is the precedent and it was invoked four rounds late.
- **Client data never enters the repo.** Corpora on the Desktop; read-only, never copied, never pasted into a doc.
- **`git commit`/`push` on a feature branch are pre-authorized. `main` is not, ever.**

## Environment

- Venv: `C:\Users\Alex\document-iq\.venv\Scripts\python.exe` (Python 3.14.5). **No `pip install`** without authorization.
- Tests: `PYTHONPATH=src <venv python> -m pytest -q` · `python tools/check_amendments.py` must exit 0.
- Stopping a background task kills the wrapper, **not the process tree** — verify the machine is idle before trusting any measurement.
- Writing source through the shell hits a **CRLF / em-dash round-trip trap** and a docstring-boundary trap; both bit repeatedly last sprint. Use the Edit/Write tools for source files.
