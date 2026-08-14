# Fresh-session kickoff prompt — DocIQ Sprint 2 merge gate

Paste everything below the line into a new session.

---

start remote-control (/remote-control)

You are picking up **LI Document IQ (DocIQ)** — an offline Windows document-corpus reducer for forensic construction-claims work — at the **Sprint 2 merge gate**. Repo: `C:\Users\Alex\document-iq`, branch **`build/sprint-2`** @ `31b4176`, in sync with origin, working tree clean, full suite green (**4m22s on a quiet machine**).

## Read these first, in order

1. `docs/codex_reviews/sprint-2_2026-08-14_claude_r5.md` — the current hand-back. Explains a **removal**, not a fix.
2. `docs/decisions/decision_register.md` — **D-29** (criterion 4 shipped not-met), **D-30** (spawn exemption), **D-31** (never delete before publishing — now itself superseded), **D-32** (the descope, and why the bound was drawn before the evidence arrived), and the "MEASURED: the ~20 minutes claim was wrong by 4x" section.
3. `docs/verification/d32_descope_2026-08-06.md` — what the descope removed, what it kept, and §9.3/§9.4 (two honest process failures).
4. `docs/contracts/amendments.md` + `amendments.toml` — 19 amendments, both halves checked mechanically by `tests/test_amendments.py`.

**Do not review from pasted chat text.** Every verdict and relay is a tracked file; read it from the branch.

## Where things stand

Sprint 2 is complete except the gate. Criteria **1, 2, 3, 5, 6, 7, 8 pass**; **4 is not met** and ruled shipped as such (D-29); **9 is cancelled** (D-19).

The merge gate has run **four Codex rounds plus two internal adversarial reviews**, all in one subsystem — the output swap. Each round found a defect inside the previous round's fix. On the sixth generation **D-32 fired** and the publication protocol was **deleted**: `emit/paths.py` 1,827 → 575 lines, ~2,700 lines of tests removed with it.

The rule that replaced it:

> Publication deletes the previous run's deliverables from the matter folder and then moves each staged file onto its final name, in that order, once — no marker, no set-aside copy, no inventory, no recovery.

That window is **wider** than what it replaced and the relay says so in those words. A process dying mid-publication leaves a mixed folder permanently. Two tests assert it, so it cannot be closed in documentation only.

## Your immediate next action

**Await Codex's verdict on the r5 relay.** When it arrives:

1. `git fetch` and read the verdict **from the branch** — and `git log --oneline -- <verdict path>` first, then diff any prior versions. Two materially different verdicts have been circulated on this project before.
2. If it passes: Sprint 2 merges to `main` **only on Alex's explicit authorization** — that is a standing rule, never inferred.
3. If it does not: **D-32's stopping rule still binds.** The swap has spent its generations. A new finding in the swap is a descope-or-disclose decision for Alex, not another fix round.

## Open items — none blocking the gate

| Item | State |
|---|---|
| **B-8 reopened** | Its fix depended on the deleted inventory. A retired-name deliverable now stays in the matter folder permanently — disclosed every run, pinned by a test asserting the file *survives*. Codex's finding, un-fixed on purpose. |
| **Offline-probe failures** | **Unreproduced and unattributed.** Contention was the convenient answer; a six-way concurrent probe passed 6/6, so it does not hold. Underwrites the offline claim made to clients. §9.4. |
| **3,600 s per-file timeout** | Fired on six documents, not the register's two. Load-dependent. A hashed run-identity input (A-04) — **Alex's ruling, not a side effect.** |
| **`RunOutcome.superseded_residue`** | Name no longer fits its narrower contents. Field exists, wired, carries real information. Renaming needs a new amendment — Alex's call. |
| **No inter-process lock** | Two concurrent `run()` calls on one matter folder are undefined. Recorded, not fixed. |
| **Criterion 4** | 92.130% is a **projection** (568 native measured earlier + 29 OCR'd measured on a subset). Last end-to-end measured full-corpus figure: **91.512%**. Never quote 92.130% flat. |

## Standing rules that bind you

- **A green result proves nothing.** Watch every fail-before go RED. ≥8 full-suite runs; **30** for filesystem/timing/subprocess-sensitive work.
- **Timing and flake claims measured while agents are running are worthless.** We told Codex its six-minute cap was too small; it was adequate, and our own fan-out was starving its review. Verify the machine is quiet and say so.
- **Fix the CLASS, not the repro.** Every fix ships a sibling enumeration and a probe over the class.
- **Withdraw the CLAIM, not just the code.** Grep for every *assertion* of a changed behaviour — docs, register, docstrings, test names, error strings.
- **Stop-the-line on `src/dociq/contracts.py` and `src/dociq/gui/pipeline.py`.** Frozen. Any amendment needs an `amendments.toml` entry with an **immutable commit id** (symbolic refs are rejected) and a prose status that agrees. **A seam-only commit cannot be green** — the seam-population probe refuses a declared-but-unwired field, so wire it in the same commit.
- **Client data never enters the repo.** Corpora live on the Desktop; read-only, never copied, never pasted into a doc.
- **`git commit`/`push` on a feature branch are pre-authorized. `main` is not, ever.**

## Environment

- Venv: `C:\Users\Alex\document-iq\.venv\Scripts\python.exe` (Python 3.14.5). **No `pip install`** without authorization.
- Tests: `PYTHONPATH=src <venv python> -m pytest -q`
- `python tools/check_amendments.py` — must exit 0.
- Stopping a background task kills the wrapper, **not the process tree**. Verify the machine is idle before trusting any measurement.
