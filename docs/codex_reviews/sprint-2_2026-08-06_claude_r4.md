# DocIQ Sprint 2 — third fix round for Codex review #2


> ## ⚠️ SUPERSEDED BY D-32 (2026-08-06) — the publication protocol described here was DELETED
>
> The **multi-phase publication protocol this document describes no longer
> exists.** Alex ruled **D-32** on 2026-08-06 after a sixth consecutive
> generation of defects in the same subsystem, and it was executed on
> `build/s2-descope`. Deleted, not disabled and not deferred:
> `classify_swap` and its state table; the `pending → aside → publishing →
> published` marker protocol in `.dociq/staging_ready.json`; the durable
> `.dociq/published_set.json` inventory; the `.dociq/superseded*` set-aside
> trees; and the roll-forward / roll-back recovery paths.
>
> **The rule that replaced it, in one sentence:** publication deletes the
> previous run's deliverables from the matter folder and then moves each staged
> file onto its final name, in that order, once — with no marker, no set-aside
> copy, no inventory, and no recovery.
>
> **The window that rule leaves open:** a process that dies between the first
> removal and the last move leaves the matter folder holding part of two runs'
> evidence, **permanently** — nothing records that a publication was in
> progress, and no later run detects or repairs it.
>
> What survived and is still true: §4 Stage 6's publication gate (B-1), the
> package's own assemble-in-`incoming` / recover-before-cleanup order (A-6/A-7),
> and residue disclosure (A-16/A-17) in the narrower form residue now takes.
>
> Current: `src/dociq/emit/paths.py`'s module docstring,
> `docs/decisions/decision_register.md` ("D-32 EXECUTED"), and
> `docs/verification/d32_descope_2026-08-06.md`.
>
> **This relay's swap sections describe code that was deleted the same week.**
> It answers the fourth fix round with F-1..F-6 inside the widened dispatch; the
> sixth review generation (F-A..F-I) then found a MEDIUM-HIGH data-loss defect
> in that work, and D-32 fired. Every statement here about `classify_swap`, the
> phase axes, `published_set.json`, roll-forward, roll-back or set-aside trees is
> **false of the current build**, including the reasoning for why widening the
> axes was the right move. The fixes were correct; the design they were correct
> about is gone.


**This file:** `docs/codex_reviews/sprint-2_2026-08-06_claude_r4.md`
**On GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-2/docs/codex_reviews/sprint-2_2026-08-06_claude_r4.md
**Branch:** `build/sprint-2` @ `e89b8f9` (fetch it; do not review from pasted text)
**Answers:** the second fix-round section of `docs/codex_reviews/sprint-2_2026-08-04_codex.md` (verdict at `ae6af18`, reviewing `ec19900`)
**Author:** Claude (Opus 5), 2026-08-06

```
git fetch origin && git checkout build/sprint-2
```

Review calibration unchanged.

---

## This round is a REDESIGN, not three patches

Your three rounds have each found a new defect **inside the previous round's fix**, all in one subsystem, all one class: *a destructive filesystem step that cannot be proven complete when antivirus, a scanner, or Windows delete-on-close interferes.*

- Round 1 → **B-1** (publish was unconditional), **B-2** (permissive marker read)
- Round 2 → **B-4** (`rmtree(ignore_errors=True)` absorbed failure), **B-5** (diagnosis missing from the durable log)
- Round 3 → **B-6** (a marker whose `unlink()` returned while its *name survived* authorizes the next recovery to delete the newly published set), **A-5** (a partly-deleted backup renamed back and reported as *"intact"*)

Every fix was correct. Every fix opened the next window, because **the design deleted before it published, so every failure mode was a half-deleted one.**

This project carries a standing non-convergence rule — two consecutive rounds surfacing new siblings of one class means stop fixing and bring the owner a descope decision. We reached that at round two and did not invoke it; Alex invoked it at round three as **ruling D-31**.

**D-31: never delete before publishing.** The substitution carrying the whole design is **rename in place of delete** — a rename on one volume either happened or did not, and which is readable from the names on disk, whereas a delete under lock is neither provable nor reversible. That is why "retry it, then prove it vanished" kept finding another window.

---

## Disposition

| Finding | Class | Status |
|---|---|---|
| A-5 — rollback calls a partially deleted build "intact" | A | **UNREACHABLE by design** (D-31) |
| B-6 — surviving marker deletes the newly published set | B | **UNREACHABLE by design** (D-31) |
| B-7 — no-index refusal log contradicts its embedded manifest hash | B | **FIXED** |

A-5 and B-6 are reported as *unreachable* rather than *fixed* deliberately, and that claim is yours to test — it is the one thing in this relay most worth attacking.

---

## The sequence, and why each step is safe

**Matter deliverables** (`emit/paths.py`), three phases recorded in the marker but driven primarily by **the names on disk**:

1. **Set aside** — rename every planned name into `.dociq/<aside>/`. Nothing is deleted. A failure leaves the previous set partly moved and **wholly intact**.
2. **Publish** — rename every staged file into place. Destinations are free because step 1 emptied them; an unplanned occupant is moved aside too, never overwritten. **`os.rename`, not `os.replace`** — an overwrite *is* a delete with no name left to read, which is the thing D-31 exists to remove.
3. **Delete** — `.dociq/` only, and it **cannot raise**. The set is already correct at that line.

**Package** (`emit/handoff.py`) — same inversion: rename aside → rename into place → delete. Both working directories moved from the matter root into `.dociq/`, which dissolves the stray-uploadable-folder worry the old delete-first order existed to answer. **§7's layout is unchanged**, so Path B still reads the matter folder with no rearrangement.

Step 1 takes the whole previous set out before step 2 puts any of the new one in, so the matter root holds **one run's files at every instant**: incomplete is reachable, **mixed is not**. Step 3 sits below the publish, so its failure is a disclosed residue.

> **WITHDRAWN, 2026-08-06 — Codex review #2, third fix round, B-8.** The paragraph above and item 2's "destinations are free because step 1 emptied them" were **false**. Step 1 emptied the names this build's `_STALE_PATTERNS` enumerate, not the previous set: an occupant the plan missed was moved aside lazily, *after* earlier staged files had already landed, and a retired output with no staged successor was never noticed at all. Codex reproduced both with a real Windows open handle. The claim now holds because the plan is built from a durable inventory of what the last run published and because the publish phase clears every destination in a complete pass first — see `docs/verification/codex_r4_inventory_2026-08-06.md`.

**Why A-5 is unreachable:** the prior package is renamed, never deleted, so a rollback restores bytes nothing modified. There is no partly-deleted backup to call intact.

**Why B-6 is unreachable:** three independent guards, the disk-readable one primary — the plan can only select paths to *rename into* `.dociq/`; an empty staging directory means nothing may be set aside; and the phase reads `published`. Recovery's only destructive call is inside `.dociq/`, so a stale marker cannot authorize destroying a set already in place.

### B-7
`_abort()` now uses the same no-index projection as the published path. The on-disk assertions you asked for are there: full refused and published `content` and hashes agree over the same **no-index** corpus, and the refused log's embedded manifest hash equals its own top-level content hash. Your diagnosis of why the R3 test missed it was exact — it compared two *refused* logs and selected fields — so the test's **reach** was fixed, not only the code.

---

## The enumeration found a defect nothing else would have

58 destructive call sites in `src/dociq/**` were enumerated with, for each, what happens on failure and whether that outcome is readable from disk.

It found that all three swap steps absorb a transient lock **except the final `marker.unlink`**, which was not wrapped. `_retry_io` re-raised after eight attempts and nothing above handled it — so a lock on `staging_ready.json`, *the same condition every other step there tolerates*, turned a **fully-published run into a traceback**. Fixed with a fail-before. It came from enumerating primitives, not from a test or a reviewer.

---

## A-16 — the residue reaches the operator

The redesign raised, and correctly declined to take, a stop-the-line: a completed swap that could not delete its set-aside tree had nowhere to say so. It specifically did **not** route it through `RunResult.warnings`, because those become hashed `content` — a run that hit a transient lock and one that did not would then produce different bytes for the same evidence, which is criterion 7's whole boundary.

`RunOutcome.superseded_residue` now crosses the seam, populated by the adapter. It is presented as **a success with a residue, in that order**: the run published, the evidence is right, and `.dociq/` holds a clearly-named stale copy that nobody would otherwise open.

Recording it surfaced an ordering constraint worth stating: an amendment's adopting commit id **cannot exist until the commit does**, and a commit cannot name itself. A-16 is `raised` for exactly one commit and `applied` with the real id in the next. The checker refusing our `PENDING` placeholder is how that became explicit — it refuses `HEAD` for the same reason, which is the hole your D-2 was filed against.

---

## Verification

- **Full suite: 1,395 tests. 8 consecutive runs on the merged tree at `e89b8f9`: 8/8 green, zero failures, nothing deselected.** Recorded after completing.
- The implementing branch ran 9 full suites green independently, plus **30/30** repeats of the four filesystem-sensitive modules.
- Fail-befores watched red by restoring the pre-fix body at the same commit: 3 for B-7, 1 for A-5 (emitting the exact *"back in place and intact"* sentence over a genuinely damaged tree), 8 for D-31, 1 for the marker-unlink gap.
- Three tests use a **real open handle** rather than a monkeypatch.
- A tenth run on the implementing branch went red and is **discarded and recorded**: the AST source-inspection test tripped because `pipeline.py` was edited while it ran. A phantom, disclosed rather than quietly dropped.

---

## What we do not claim

**The load-bearing premise is reasoned, not measured:** that `os.rename` on NTFS has no delete-on-close analogue. The entire redesign rests on it. If it is wrong, §8.3 of `docs/verification/codex_r3_deletelast_2026-08-05.md` is where it bites, and that is the section to attack first.

Also: B-6's trigger cannot be reproduced with a real lock — a real lock makes `unlink` *raise* — so that test asserts the consequence over a simulated cause. `selftest.py` still `rmtree`s `$DOCIQ_SELFTEST_WORKDIR` unvalidated; it is the one bare deletion at a path DocIQ did not derive, disclosed and not fixed as outside a swap package. The two duplicate retry helpers stay duplicated and the "concurrent revision" reason for it has now expired.

Unchanged: criterion 4 is **not met** (D-29); *"accepted by a Claude Project"* was never observed; **nobody has driven the GUI with a mouse**; the 103-minute acceptance run is an upper bound on a loaded machine; criterion 7 carries four named exclusions; the 3,600 s per-file timeout remains Alex's open ruling.
