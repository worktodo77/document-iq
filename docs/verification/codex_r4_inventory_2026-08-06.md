# B-8 — the set-aside plan is the complete previous set, and it leaves first

**Repository:** `worktodo77/document-iq`
**Branch:** `build/s2-r4-inventory` (off `build/sprint-2` @ `a4973b9`)
**Gate:** D-10, Codex review #2, **fourth** fix round
**Finding:** B-8 (Codex third fix-round verdict,
`docs/codex_reviews/sprint-2_2026-08-04_codex.md`)
**Date:** 2026-08-06

Codex reproduced B-8 directly, with a real Windows open handle. Its text
governs, and nothing below contests it.

---

## 1. What was wrong, and what the fix is

D-31's load-bearing claim is that **the whole previous set leaves the matter
root before any new file enters it.** `_stale_deliverables()` did not inventory
that set — it expanded `pipeline._STALE_PATTERNS`, *this build's* output names.
Two failures followed, and the second cannot be caught by any gate:

1. **Locked unplanned replacement.** An older build left `z_legacy.txt` at a
   name this build no longer enumerates but still writes a replacement for.
   `commit_staging()` noticed the occupant only when the publish loop *reached*
   the corresponding staged file. Sorted publication landed `a_new.txt` first; a
   real handle on `z_legacy.txt` then made its late move-aside fail, leaving the
   marker at phase `aside` and the matter root holding `a_new.txt = NEW` beside
   `z_legacy.txt = OLD`. **The mixed set D-31 says is unreachable.**
2. **Retired output, success path.** An older build emitted
   `legacy_report.json`; this build retired it, so no staged path ever entered
   the lazy branch. The swap **completed**, removed the marker, and left the old
   file beside the new `sources.json` permanently. §4 Stage 6 cannot see it: the
   manifest is built over **staging**, not over the destination root.

**Two mechanisms, deliberately independent.** One makes the plan complete; the
other makes an incomplete plan harmless. Either alone closes one reproduction;
both are shipped because the class is "the plan does not know what is in the
folder", and a folder can always hold something no plan knows about.

| | mechanism | closes | where |
|---|---|---|---|
| **M1** | **A durable inventory of the complete previously published set.** `.dociq/published_set.json` records the matter-root-relative names a swap published, *written by the swap that published them*. `_stale_deliverables()` unions it with the pattern expansion. Because it records what was written rather than what this build knows how to write, **it survives a version change** — the whole point of B-8. | repro 2, and repro 1's plan | `emit/paths.py` `PUBLISHED_NAME`, `published_inventory`, `_write_inventory`; `pipeline._stale_deliverables` |
| **M2** | **The publish phase clears every destination in a complete pass (2a) before renaming any staged file in (2b).** The lazy occupant branch is gone. No new file enters the matter root until every name it will take is free. | repro 1, and any future missed name | `emit/paths.py` `commit_staging` step 2a |

**Durability of M1 across a lock.** The inventory is written below the publish,
where nothing may raise — so a transient lock on it would silently leave the
*previous* run's inventory in place, which is B-8 again. The readiness marker
carries the same list (`SwapPlan.published`, validated at parse like every other
marker field) and **is not deleted while the inventory is behind it**;
`published_inventory()` reads the marker in preference to a file behind it.
Probe: `test_a_lock_on_the_inventory_keeps_the_marker_as_the_record`.

**The bootstrap case is disclosed, not assumed away.** A folder last published by
a build predating the inventory has none to read. The plan then falls back to the
patterns — B-8's incomplete plan — and the run says so in the processing log's
`run.published_set_inventory`. An **absent** inventory is that case; a
**corrupt** one fails closed (`PublishedSetUnreadable`) and takes the same
disclosed BLOCKED path an unreadable marker takes, with the folder untouched.

**Plan normalisation.** The plan is a union of a pattern expansion (which names
directories) and a file inventory (which names their contents), so
`covering_plan()` drops entries an ancestor already covers and collapses a
directory **all of whose current on-disk entries the plan covers** into the
directory itself. The containment guard is checked against the disk, so a file
the plan does not name — an operator's note in `clean_text/`, a deliverable from
a build older still — keeps the directory expanded and is left where it is.
Without the collapse, a retired *directory* left an empty shell at the matter
root that only a deletion could remove, and adding a deletion at the matter root
is exactly what D-31 bought the right not to do.

---

## 2. The state enumeration

> *"Re-enumerate the redesigned state machines from every persistent state."*
> — Codex, third fix-round merge condition.

Every round of this review found a defect that came from reasoning **forward**
through the happy path instead of **backward from each state that can exist on
disk**. The table is that exercise. Axes: marker present/absent × phase ×
staging holds files / is empty-or-absent × matter root × `.dociq/superseded*`
present/absent. The inventory is an orthogonal axis, table 2.2.

Probes carry the row ID (`tests/test_emit_atomicity.py` §6).

### 2.1 The matter swap (`emit/paths.py`)

| ID | marker | phase | staging | matter root | aside tree | what the next run does | correct? |
|---|---|---|---|---|---|---|---|
| S-01 | absent | — | absent | published set | absent | `recover_pending` no-ops; the run stages and swaps normally | ✔ |
| S-02 | absent | — | holds files | published set | absent | no-op; `run()`'s `discard_staging` throws the orphan away — no marker exists, so nothing can publish it | ✔ |
| S-03 | present | `pending` | holds files | previous set | absent | full swap: set aside → clear destinations → publish → delete → inventory → marker | ✔ probe |
| S-04 | present | `pending` | holds files | previous set, partly already moved | present | idempotent: a plan name not at the root is skipped, the rest move, then publish | ✔ existing probe (`..._blocked_set_aside_...`) |
| S-05 | present | `pending` | empty | previous set | absent | **abandoned marker.** Nothing staged ⇒ nothing may be set aside. No move, no delete outside `.dociq/`, marker cleared, **inventory deliberately not rewritten** (`plan.published` names a set that was never published) | ✔ probe |
| S-06 | present | `pending` | empty | previous set | present (stale) | as S-05, plus the stale tree is deleted or disclosed as residue | ✔ existing probe |
| S-07 | present | `aside` | holds files | plan names vacated | present | publish (2a finds nothing, 2b renames) → delete → inventory → marker | ✔ probe |
| S-08 | present | `aside` | holds files | vacated **plus an unplanned occupant** | present | **2a moves the occupant aside first**, then 2b publishes. This is B-8 repro 1 | ✔ probe, real open handle |
| S-09 | present | `aside` | **empty**, none of `published` at the root | previous set only in the aside tree | present | **ROLL BACK.** The aside tree holds the only copy; its entries are renamed back to the root and the marker is cleared | **was WRONG — fixed.** The old code ran the empty publish loop, recorded `published`, then *deleted the aside tree*: the folder was left with **no deliverables at all** |
| S-09b | present | `aside` | empty, after step **2a** had moved an unplanned occupant into the same tree | — | present | the rollback restores **the whole tree**, not `plan.superseded` — 2a's occupants are recorded only in the return value, so a plan-keyed rollback would restore part of the tree and hand the rest to `_discard_aside_trees` | **new sibling, found by enumeration** ✔ probe |
| S-10 | present | `aside` | empty, **all** of `published` at the root | new set | present | publish finished, only the marker update was lost: nothing moves, cleanup only | ✔ probe |
| S-11 | present | `aside` | empty, **some** of `published` at the root | mixed by a restore | present | **REFUSE** (`PendingSwapUnrecoverable`), folder untouched, marker kept. Unreachable from the code — a publish moves one file at a time, so an interrupted one leaves the rest in staging | **new** — the old code took the S-10 branch and deleted the aside tree |
| S-12 | present | `published` | empty | new set | present | cleanup only; nothing moves; marker cleared. B-6's state | ✔ existing probe |
| S-13 | present | `published` | **holds files** | new set | present | **REFUSE**, folder untouched | **was WRONG — fixed.** The old code trusted the phase and ran `_remove_tree_or_fail(staging)`, deleting a complete set of deliverables as drained scratch |
| S-14 | present, unparseable | — | any | any | any | **fail closed** (`PendingSwapUnreadable`), nothing moved; `run()` returns a disclosed BLOCKED run | ✔ existing probe (B-2) |
| S-15 | present, `aside`/`superseded` entry escapes | — | any | any | any | refused at parse; the only names the cleanup can delete are ones `_free_aside_name` could have issued | ✔ existing probe |
| S-16 | absent | — | absent | published set | present (residue) | no-op; `superseded_residue()` discloses it; the next swap takes `superseded.1` and neither tree is overwritten | ✔ probe |

**Both corrected rows have the same shape and it is worth naming.** Neither
S-09 nor S-13 is reachable from the code, which is exactly why nothing had
looked at them — and both had a next step that destroyed the only intact copy of
a complete set. They are reachable *on disk*: a backup agent restoring a folder,
a copy that finished halfway, a cleanup script, or an operator following the
readiness marker's own "move staging aside and delete the marker" instruction
and doing only the first half. That instruction is in this codebase's own error
message, so S-09 is a state DocIQ tells operators how to create.

**The class assertion.** `only_old.txt` belongs to the previous set,
`only_new.txt` to the staged one, and
`test_no_persistent_state_lets_two_generations_share_the_matter_root` asserts
over six constructible states that the matter root never holds both — before as
well as after recovery — and that recovery never leaves it holding **neither**.
That last clause is what went red on S-09.

### 2.2 The inventory axis

| ID | inventory | next run |
|---|---|---|
| I-1 | absent | falls back to the patterns and **discloses it** in `run.published_set_inventory`; the next successful swap writes one ✔ probe |
| I-2 | present, fresh | complete plan ✔ probe |
| I-3 | present but behind a surviving marker (its write was blocked) | the marker wins; the marker is retained precisely so it can ✔ probe |
| I-4 | corrupt | `PublishedSetUnreadable` → disclosed BLOCKED run, folder untouched ✔ probe |
| I-5 | names a path outside the matter folder | refused by `_validate_superseded_entry` at parse — the same check the marker's `superseded` list gets, and for the same reason: it selects paths that get moved ✔ |

---

## 3. Determinism (criterion 7)

Nothing about the inventory, the residue or the retry reaches hashed `content`.

* `.dociq/published_set.json` is under `STATE_DIRNAME`, which
  `verify.manifest.build` excludes wholesale and which the deliverable
  fingerprint in the tests skips.
* `run.published_set_inventory` sits in the log's **`run`** block, beside
  `stale_outputs_replaced` and `superseded_residue_before_swap`, never in
  `content`. Asserted:
  `test_an_absent_inventory_falls_back_to_the_patterns_and_says_so` checks the
  key is absent from `json.dumps(payload["content"])`.
* The existing determinism probes are unchanged and still pass:
  `test_a_rerun_into_the_same_folder_is_byte_identical_to_the_first`,
  `test_the_state_of_the_destination_cannot_change_the_hashed_content`,
  `test_two_destinations_produce_one_identity_and_identical_bytes`,
  `test_no_hashed_artifact_mentions_the_staging_directory`.
* A run that hit a lock and one that did not produce identical bytes: the only
  difference a lock makes is which `.dociq/` names survive and what the *next*
  run's `run` block records.

## 4. §7's layout is unchanged

The inventory lives under `.dociq/`. No file is added at the matter root, so
Path B (D-20) still reads the matter folder with no rearrangement. The one
behavioural change visible at the root is the opposite of an addition: a
deliverable a former build published and this one retired now **leaves**.

## 5. Withdrawn claims

Per the standing rule, the assertion was withdrawn, not just the code.

* `docs/verification/codex_r3_deletelast_2026-08-05.md` §2.1 — the step table's
  row 2 and the paragraph *"Why step 1 before step 2 is the whole point"*, which
  was the note's load-bearing claim and was false when written. Corrected in
  place with the reproduction and the two mechanisms that now make it true.
* `docs/codex_reviews/sprint-2_2026-08-06_claude_r4.md` — "Step 1 takes the
  whole previous set out before step 2 puts any of the new one in" and item 2's
  "destinations are free because step 1 emptied them".
* `emit/paths.py` `commit_staging` — the same sentence in the docstring, and the
  step-2 description that said an unplanned occupant "is moved aside too" without
  saying *when*, which was the whole defect.

## 6. Fail-before

Every one watched RED, on the pre-fix shape, for the reason the finding names —
not an incidental one. The reverts were surgical (the lazy-occupant loop
restored; the inventory union disabled; the state dispatch removed), so the
tests failed on behaviour rather than on a missing symbol.

| test | reverted to | observed failure |
|---|---|---|
| `test_a_locked_unplanned_replacement_publishes_nothing_before_it` | lazy occupant branch inside the publish loop | `a_new.txt` present at the matter root beside `z_legacy.txt = OLD` — **Codex's reproduction 1 verbatim** |
| `test_a_retired_output_leaves_the_folder_on_the_SUCCESS_path` | plan from patterns only | `legacy_report.json` survived a successful swap |
| `test_the_inventory_survives_a_version_that_never_heard_of_the_name` | plan from patterns only | 4 of 4 retired names survived |
| `test_a_blocked_publish_leaves_an_incomplete_set_never_a_mixed_one` (**reach widened**) | lazy occupant branch | `document_index.csv` "neither published nor set aside" |
| `test_S09_aside_with_a_lost_staging_restores_the_previous_set` | state dispatch removed | the matter root held **neither generation**: every deliverable destroyed |
| `test_S13_published_with_a_full_staging_refuses` | state dispatch removed | `.dociq/staging` — a complete set — deleted |
| `test_no_persistent_state_..._share_the_matter_root[S09]` | state dispatch removed | "recovery left the matter root holding NEITHER generation" |
| `test_S09_rollback_restores_an_UNPLANNED_occupant_too` | rollback keyed on `plan.superseded` | `unplanned.txt` destroyed by the cleanup that follows the rollback |

**The test whose reach Codex named.**
`test_a_blocked_publish_leaves_an_incomplete_set_never_a_mixed_one` defined
`superseded = tuple(before)` — assuming exactly the completeness the production
enumerator did not provide, so it never entered the branch that handles a missed
name. It now withholds one deliverable from the plan deliberately, with the
mixture assertions unchanged: they have to be satisfied by 2a rather than by the
fixture. The fixture asserts its own premise (the withheld name *does* have a
staged replacement), so the test cannot silently stop covering the branch.

## 7. Not proven / not claimed

* **The bootstrap gap is real and is disclosed, not closed.** A folder whose last
  publication predates the inventory has no record of what is in it, and no code
  can produce one — a sweep of the matter root would set aside and then delete
  files DocIQ never wrote. That run uses the patterns and says so in the log.
  From its next successful swap onward the folder is covered.
* **Criterion 4 remains unmet**, no mouse-driven GUI acceptance was performed,
  and the real-corpus byte-identical claim remains open. Unchanged from R4.
* The 3,600-second per-file timeout remains Alex's open ruling.
* A-6 and A-7 are **not** addressed here; `emit/handoff.py` and
  `gui/pipeline.py` are owned by another agent for that work, and this change
  touches neither. No `amendments.toml` entry was needed: `contracts.py`,
  `gui/pipeline.py` and `emit/handoff.py` are untouched (`git status` in §8).

## 8. Files changed

```
src/dociq/emit/paths.py        M1 + M2 + the S-09/S-11/S-13 dispatch
src/dociq/pipeline.py          plan union, inventory read at the top of run(),
                               run.published_set_inventory disclosure
tests/test_emit_atomicity.py   §5 B-8 (6 tests), §6 the state enumeration (6 + 6)
tests/test_incomplete_runs.py  one assertion widened for plan normalisation
docs/verification/codex_r3_deletelast_2026-08-05.md   withdrawal
docs/codex_reviews/sprint-2_2026-08-06_claude_r4.md   withdrawal
```
