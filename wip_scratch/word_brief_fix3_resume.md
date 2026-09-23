# Word fix round 3: FINISH a stopped builder's draft (2026-09-23, under D-57)

SP = `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad`.
Worktree `SP\wt-word`, branch `build/sprint-5-word` @ `2671947`. Everything else the briefs call
`...\scratchpad\<x>` now lives at `SP\recovered\<x>` (the old scratchpad was wiped by Windows and rebuilt
from transcripts; `SP\recovered\word_fix2\measure\` lacks `child_side.py` and `orchestrator.py`, whose
ancestors are `SP\recovered\review_word\measure\`; snapshot and red-base directories were not rebuilt:
regenerate them with `make_red_base.py` or `git worktree add --detach` under `SP\recovered\word_fix3\`).

**The task is `SP\recovered\word_brief_fix3.md`, in full.** Its builder was stopped mid-gate on 2026-09-15.
Its uncommitted edits were replayed exactly from its transcript into `SP\wt-word` (5 files, +1590/-222, the
same stat it recorded; the Word test files give `232 passed`, as its last run did). Treat that diff as a
careful but UNVERIFIED draft: you own it. Its narration and gate outputs are in
`SP\recovered\word_fix3\predecessor_narration.md`; its helpers (`red_run.py`, `mutants.py` with 143 mutants,
`quote_notes.py`, `gen_zapf_table.py`, census scripts) are in `SP\recovered\word_fix3\`, their paths already
pointed at SP. Where it stopped: it was running `red_run.py` (red lines on `2671947`); `mutants.py` had not
completed; the corpus re-measure, the per-file gate 2, selftest and full suite had not run.

Do this:
1. Read the brief, `SP\recovered\review_word_r3\findings.md` + `payload.json`, then the diff. Check each of the
   four items, its class deliverable, and all eleven C items against the diff. Finish anything missing or wrong.
2. Run every gate of the brief in order. Red lines by full node id on a detached `2671947` (never `-k`); the
   mutation run on COPIES; the corpus re-measure counts vs `ea4f1cb`; each gate-2 file in its own process;
   selftest (`PYTHONPATH=src`); the full suite once (addopts carry `-q`: parse the bare count line).
3. Commit on `build/sprint-5-word` with files listed explicitly. The message ends
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (this replaces the brief's trailer). Do not push.

The brief's Rules apply (no pip install, no deleting, no `checkout --`/`reset`/`rebase`/`stash`, never touch
`C:\Users\Alex\document-iq` except reading the register, never another worktree, helper scripts as files under
`SP\recovered\word_fix3\`, invented text only per D-12, US English). No GUI probes other than what the named
test files run. Report per the brief (under 1,500 words), and first say what in the draft you changed and why.
