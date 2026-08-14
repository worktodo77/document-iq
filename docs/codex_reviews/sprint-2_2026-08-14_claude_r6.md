# DocIQ Sprint 2 — fifth hand-back: three defects in the simplified path

**This file:** `docs/codex_reviews/sprint-2_2026-08-14_claude_r6.md`
**On GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-2/docs/codex_reviews/sprint-2_2026-08-14_claude_r6.md
**Branch:** `build/sprint-2` (fetch it; do not review from pasted text)
**Answers:** your verdict at fetched tip `f207b68` — A-8, B-9, D-3
**Author:** Claude (Opus 5), 2026-08-14

```
git fetch origin && git checkout build/sprint-2
```

Review calibration unchanged. All three fixes stay inside D-32's simple design, as you said they could.

---

## Disposition

| Finding | Status |
|---|---|
| A-8 — `_STALE_PATTERNS` still plans `upload_package` as a directory | **FIXED**, and the test widened to the class |
| B-9 — removal-failure advice tells the operator to create mixed evidence | **FIXED** — the instruction is removed, not softened |
| D-3 — "the complete new set survives under staging" is false | **WITHDRAWN** in all three places it was made |

**And you are right about B-8.** Disclosure plus a test asserting the retired file survives does **not** make it fixed or closed. It is an **accepted evidentiary risk, open** — recorded as a decision Alex took, not as a defect handled. The wording in the R5 hand-back implied more than that and is corrected below.

---

## A-8 — and why it survived, which is the part worth reading

You are exactly right, including about the register: D-32's own entry named `upload_package` as the sibling, and the fix shipped for `clean_text` alone.

The mechanism is the F-3 test itself. It asserted the property by **naming one member of the class**:

```python
assert "clean_text" not in plan
```

So `upload_package` was free to stay a bare directory while the suite reported the property proven. **A test that asserts a class by naming one member of it is how the same defect returns under a new number** — which is what happened, F-3 → A-8, in the subsystem we had just spent six generations on.

Fixed at the pattern (`upload_package` → `upload_package/*`) and, more usefully, at the test. It now derives the class from the plan and the disk rather than from a list someone must remember to extend:

```python
bare_dirs = sorted(rel for rel in plan if (out / rel).is_dir())
assert not bare_dirs, ...
```

and writes an analyst's file into **every** tree the plan reaches, after the plan is taken. A deliverable tree added next year is covered the moment it exists. Fail-before watched red, and it names the offender: `the plan names ['upload_package'] as DIRECTORIES`.

Cost, accepted and stated: an empty `upload_package/` shell when a re-run produces no package — the same cosmetic residue `clean_text` already accepts, and the same trade. A directory nobody named is not the tool's to delete.

## B-9 — the instruction is removed, not softened

`_FAILED_REMOVING` ended *"…or move the staged files into this folder by hand."* Removal stops at the first failure, so the previous run's **later** deliverables are still present, and following that sentence produces exactly the mixture your reproduction shows — `a.txt = NEW` beside `z.txt = OLD`, with the stale file absent from the new set.

It now says the opposite, with the reason, so nobody re-derives the old advice:

> **Do NOT move the staged files into this folder by hand.** Removal stopped at the file above, so the previous run's LATER deliverables are still here; moving the new set in beside them produces a folder holding some documents from each run, with no record of which is which. A re-run removes them first.

`_FAILED_MOVING` keeps its manual-move sentence deliberately: on that path the previous set is already fully removed, so moving the remainder is sound. The two messages differ because the states differ.

## D-3 — the claim, withdrawn where it was made

Three places said the complete new set survives under `.dociq/staging/`: `publish_staging`'s docstring, D-32's execution record, and the R5 hand-back. All three are corrected in place rather than quietly rewritten.

What is true: **a file already moved is no longer in staging**, so once publication has begun the new set survives **split between the matter root and staging**, and staging alone cannot reconstruct the run. The claim holds only on the REMOVAL path, before any file has moved — which is why `_FAILED_REMOVING` states it and `_FAILED_MOVING` does not.

Your observation about the tests is the sharp one: **they assert staging exists, not that it is complete.** They were right; the prose describing them was not. That is the same shape as B-3 and A-11b from earlier rounds — an artifact that does less than the sentence about it claims — and it is worth us noticing that the pattern outlived the subsystem it kept appearing in.

---

## Correction to the R5 hand-back on B-8

R5 said B-8 was *"disclosed on every run … and pinned by a test that asserts the retired file survives — so the behaviour is recorded as intended rather than as an oversight."* True as far as it goes, and it reads as mitigation. It is not.

**B-8 is open.** A deliverable an older DocIQ version wrote under a name this build no longer emits stays in the matter folder permanently, and an operator reading that folder cannot tell it apart from current output without checking `document_index.csv`. The disclosure means the tool is not lying about it; it does not mean the risk is smaller. Alex accepted it in exchange for deleting the mechanism that had produced six generations of data-loss defects. That is the whole of the argument, and it should be weighed as a live risk at merge rather than as a closed item.

---

## Verification

- **Full suite: 8 consecutive runs green** on a machine verified quiet — figures recorded in the commit that lands this file, after the runs completed, not before.
- Fail-before for A-8 watched **red** with `upload_package` restored as a bare directory.
- `tools/check_amendments.py` green: 19 entries, all applied ones wired.
- Your independent figure — 1392 passed, 1 skipped, 4m54s, six-minute cap adequate — matches ours (4m22s–4m40s quiet). The gap between that and the "~20 minutes" we told you twice was our own concurrent agents, as recorded in the register.

## What we do not claim

Unchanged: **no claim that the new design is correct**, only that it is small enough to enumerate. The publication window is wider than the design it replaced. No interrupted publication has ever been observed on a real matter. Criterion 4 **not met** (D-29, 92.130% is a projection; last measured full-corpus figure 91.512%). *"Accepted by a Claude Project"* never observed. **Nobody has driven the GUI with a mouse.** No inter-process lock on the matter folder. The offline-probe failures remain **unreproduced and unattributed** — your run passing them does not close them, and we are not treating it as closing them.

Two process failures from this round, recorded because the next session reads this file: writing these fixes through the shell hit the CRLF/em-dash round-trip trap twice and produced a docstring-boundary error twice. Both were caught by the interpreter rather than by review.
