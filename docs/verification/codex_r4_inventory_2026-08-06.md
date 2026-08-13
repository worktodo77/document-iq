# B-8 — the set-aside plan is the complete previous set, and it leaves first

**Repository:** `worktodo77/document-iq`
**Branch:** `build/s2-r4-inventory` (off `build/sprint-2` @ `a4973b9`)
**Gate:** D-10, Codex review #2, **fourth** fix round
**Branches:** `build/s2-r4-inventory` (B-8, merged to `build/sprint-2`), then `build/s2-r4-inventory2` (F-1..F-6, this note's §2 rewrite)
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

**Plan normalisation, and what was removed from it (F-3).** `covering_plan()`
drops entries an ancestor already covers — pure set arithmetic over the plan,
because the plan is a union of a pattern expansion (which names directories) and
a file inventory (which names their contents).

It briefly also **collapsed** a directory all of whose on-disk entries the plan
covered into the directory's own name, so a retired directory left no empty
shell. The containment guard for that read the disk **at plan time**, at the top
of Stage 5; the rename it authorised happened after the whole of Stage 5 and
Stage 6, which on a real matter is minutes. An analyst who saved a note into
`clean_text/` inside that window had it renamed aside under the directory's name
and deleted with the set-aside tree, having never been in the plan. It also
silently coarsened `stale_outputs_replaced` from the files to `["clean_text"]`,
so the durable record of what a re-run replaced stopped naming any of it.

**Removed rather than narrowed.** Re-verifying containment immediately before the
rename narrows the window without closing it, and what the collapse bought was
cosmetic: an empty `retired_dir/` at the matter root holds no evidence, mixes no
generations, and is what the pre-B-8 build already left behind for `clean_text/`.
A cosmetic residue is a better trade than any window in which an analyst's file
can be deleted. The plan no longer reads the disk at all beyond the single
existence filter, and that property is stated in the docstring so a later
refactor cannot reintroduce it without meeting the argument.

---

## 2. The state enumeration — the AXES, not the rows

> *"Re-enumerate the redesigned state machines from every persistent state."*
> — Codex, third fix-round merge condition.

The third round's table was checked row by row and **every row it expressed was
sound.** An adversarial re-read then found three more data-loss states, and all
three lived in rows the table's *axes could not express*. That is the finding
worth carrying forward, because it is the fourth consecutive round in which this
subsystem produced a new generation of defects inside the previous round's fix:

* `staging` was **binary** — holds files / empty. It could not say **which**
  files, so a staging directory 30 files short of what the marker recorded read
  simply as "holds files" (F-2).
* the set-aside axis was **one column** covering both "residue from a completed
  swap" (always expendable) and "**this** marker's partially-completed step 1"
  (may hold the only copy of half the previous set). The marker is rewritten only
  *after* the whole step-1 loop, so a crash inside it leaves `pending` beside a
  half-filled aside tree — a state the table had no column for (F-1).
* nothing expressed **"has any staged file entered the matter root yet?"**. The
  only way to ask was to test whether a published NAME was at the root, and a
  name is not an identity: `sources.json` is at the matter root before any swap
  begins, because the previous run put it there (F-4).

### 2.1 The widened axes

| axis | third round | fourth round |
|---|---|---|
| `plan.phase` | `pending` / `aside` / `published` | + **`publishing`**, written after step 2a and before step 2b. At `pending` and `aside` **no staged file has been published**, full stop; at `publishing` and beyond a claimed name at the root is provably this run's, because reaching that phase required 2a to have vacated every one of those names. Identity comes from the phase, never from a name. |
| staging | holds files / empty | the staged **names**, compared against `plan.published`: equal / short / holding names the marker never claimed |
| set-aside | a `superseded*` tree exists / does not | **this plan's** tree holds files / does not — separately from residue trees, which are by definition already-replaced sets |
| matter root | prose | `landed` = which of `plan.published` are at the root, **supplied only at `publishing`/`published`** where the question has an answer |
| inventory | absent / fresh / stale / corrupt | unchanged (§2.4) |

### 2.2 The table is the code

`emit.paths.classify_swap(plan, staged, aside_holds, landed) -> SwapState` is
pure: no disk, no I/O, one `action` and a `why` that is carried into both the
refusal message and the durable recovery note. It is called once, at the top of
`commit_staging`, before anything moves.

Writing it as a returned value rather than as branches is what makes the table
testable directly — `test_the_state_table_is_the_code` walks every row without
constructing a filesystem — and the rows that matter most here are precisely the
ones the code cannot itself produce. Three rounds of "the dispatch tests each
axis where it happens to need it" is what put the missing rows out of sight.

**The rules, in the order the classifier applies them.**

| # | condition | action | why |
|---|---|---|---|
| 1 | staging holds a name `plan.published` does not | **REFUSE** | publishing it would put a deliverable in the folder that the durable inventory would not record — B-8 recreated one swap later |
| 2 | phase `published` and staging non-empty | **REFUSE** | the publish raises rather than recording `published` over an unpublished file, so this was assembled by a restore or a hand edit; the old code deleted `.dociq/staging` as drained scratch |
| 3 | phase `published` and staging empty | **FINISH** | cleanup under `.dociq/` only |
| 4 | every claimed name is staged or already published | **ROLL FORWARD** | the set can still be published as a set |
| 5 | nothing has been published (`landed` empty) and the set cannot be completed, and this plan's aside tree holds files | **ROLL BACK** | the aside tree is the only copy of what it holds; nothing of the new set is in the folder, so undoing costs nothing **(F-1, and S-09 folded into it)** |
| 6 | same, but the aside tree is empty | **ABANDONED** | the marker can authorize nothing; nothing has moved. An empty `plan.published` lands here **and must** — a marker that publishes nothing may not set anything aside, because that is a delete before a publish |
| 7 | otherwise (partly published **and** a claimed name is nowhere) | **REFUSE** | finishing publishes an incomplete set over a previous set already out of the folder and records it as complete; undoing destroys the part already published **(F-2)** |

Rule 5 is the one that did not exist. Rules 1, 2 and 7 are the ones the coarse
staging axis could not state.

### 2.3 The rows, and what each one does now

Row IDs continue the third round's. Probes are in `tests/test_emit_atomicity.py`
§6 and §7.

| ID | marker / phase | staging vs `plan.published` | this plan's aside tree | action | correct? |
|---|---|---|---|---|---|
| S-01 | absent | — | — | no-op | ✔ |
| S-02 | absent | orphan present | — | no-op; `run()`'s `discard_staging` throws it away — no marker, so nothing can publish it | ✔ |
| S-03 | `pending` | equal | empty | ROLL FORWARD | ✔ probe |
| S-04 | `pending` | equal | partial | ROLL FORWARD (step 1 is idempotent) | ✔ probe |
| **S-05** | `pending` | empty | **empty** | ABANDONED | ✔ probe |
| **S-06b** | `pending` | empty or short | **partial** | **ROLL BACK** | **was WRONG — F-1.** `abandoned` tested staging alone; the half of the previous set that had moved was deleted, and the note said "nothing was set aside" ✔ probe |
| S-07 | `aside` | equal | full | ROLL FORWARD | ✔ probe |
| S-08 | `pending`→ | equal, and a name the plan did not cover is occupied at the root | — | step 2a clears it **before** any publish | ✔ probe, real open handle (B-8 repro 1) |
| **S-09** | `aside` / `publishing` | empty | full | **ROLL BACK** | ✔ probe (third round; now rule 5) |
| S-09b | as S-09, after 2a moved an unplanned occupant into the same tree | | | rollback restores the **whole tree**, not `plan.superseded` | ✔ probe |
| **S-11a** | `pending` / `aside` | **short** | full | **ROLL BACK** | **was WRONG — F-2.** The short set was published over the previous one and the previous one deleted ✔ probe |
| **S-11b** | `publishing` | **short**, and some landed | full | **REFUSE** | **was WRONG — F-2** ✔ probe |
| S-10 | `publishing` | empty, all landed | full | ROLL FORWARD → cleanup | ✔ probe |
| S-12 | `published` | empty | full | FINISH | ✔ probe (B-6's state) |
| S-13 | `published` | non-empty | full | **REFUSE** | ✔ probe (third round) |
| **S-14b** | any | **holds names the marker never claimed** | any | **REFUSE** | **new (rule 1)** ✔ probe |
| S-15 | unreadable | any | any | fail closed, untouched | ✔ probe (B-2) |
| **S-15b** | readable, `superseded` names `.dociq/…` | any | any | **refused at parse** | **was WRONG — F-4.** `covering_plan` had the guard; the parser did not ✔ probe |
| S-16 | absent | — | residue only | no-op; disclosed; the next swap takes `superseded.1` | ✔ probe |

### 2.4 The inventory axis

Unchanged from the third round (I-1 absent → patterns + disclosure; I-2 fresh;
I-3 behind a surviving marker → the marker wins; I-4 corrupt → BLOCKED; I-5
escaping path → refused at parse), with one correction: `published_inventory`
read the marker at `aside` as well as `published`, which meant it could report
the set a **rollback is about to undo** as the set in the folder. It is now
`published` only, which is the single state the branch exists for — the swap
finished and the inventory write was blocked.

### 2.5 The disclosure follows the states

`commit_staging` reports which of its outcomes happened (`ROLLED FORWARD` /
`ROLLED BACK` / `NOTHING TO DO` / `CLEANED UP`) with the classifier's `why`
attached, because the return value is `()` for three of them and cannot
discriminate. F-1's note asserted that nothing had been set aside on a state
where half the previous set had been — a durable record saying the wrong thing
about which run's evidence is in the folder is the failure this subsystem exists
to prevent.

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
| `test_F1_a_pending_marker_beside_a_part_moved_previous_set_rolls_back` | `abandoned` tests staging alone | the half of the previous set that had moved was deleted |
| `test_F1_the_note_does_not_say_nothing_was_set_aside` | same | the durable note read *"nothing had been set aside"* over a half-moved set |
| `test_F2_a_short_staging_directory_never_publishes_over_the_previous_set` | "holds files" is the whole staging axis | a short set published over the previous one, which was then deleted |
| `test_F2_a_short_staging_directory_mid_publish_refuses` | same | finished an incomplete set and recorded it complete |
| `test_F3_a_file_saved_after_the_plan_is_not_swept_up_by_it` | the directory collapse restored | an analyst's `clean_text/analyst_note.md` renamed aside and deleted |
| `test_F3_the_replaced_record_names_the_files_not_the_directory` | same | `stale_outputs_replaced` read `clean_text`, naming none of the files |
| `test_F4_a_marker_cannot_name_dociqs_own_state_as_superseded` | parser guard removed | `.dociq/staging` accepted as a supersede entry |

**The test whose reach Codex named.**
`test_a_blocked_publish_leaves_an_incomplete_set_never_a_mixed_one` defined
`superseded = tuple(before)` — assuming exactly the completeness the production
enumerator did not provide, so it never entered the branch that handles a missed
name. It now withholds one deliverable from the plan deliberately, with the
mixture assertions unchanged: they have to be satisfied by 2a rather than by the
fixture. The fixture asserts its own premise (the withheld name *does* have a
staged replacement), so the test cannot silently stop covering the branch.

## 7. Recorded, NOT fixed — concurrent runs on one matter folder

**There is no inter-process lock on the matter folder, and nothing in the code
excludes two `run()` calls against the same one.** A second run reaching
`staging_layout()` deletes `.dociq/staging/` — including a first run's complete
staged set, between its `mark_ready()` and its `commit_staging()`. That is an
in-process route into F-1's premise: the marker exists, the previous set is
being set aside, and the staged set vanishes underneath it.

The fixes above make that **survivable** rather than silent — the classifier
sees a staging directory that cannot satisfy the marker and either rolls the
previous set back or refuses, and it says which in the durable note. It does not
make it *correct*: the second run then proceeds to build its own set in a folder
the first run believes it owns, and the two runs' `stale_outputs_replaced`
records describe a folder neither of them saw.

Closing it needs a decision rather than a patch — an exclusive lock file under
`.dociq/` that fails the second run closed, versus letting the second run wait,
versus declaring one-run-per-folder an operating condition and detecting
violations after the fact. Each has a different answer for a crashed run that
left a lock behind, which is the case that decides whether operators end up
deleting lock files by hand. **That is Alex's ruling, not this fix round's**, and
it is recorded here rather than closed quietly.

## 8. Not proven / not claimed

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

## 9. Files changed

```
src/dociq/emit/paths.py        M1 (PUBLISHED_NAME, published_inventory,
                               _write_inventory, SwapPlan.published),
                               M2 (commit_staging step 2a),
                               the S-09/S-09b/S-11/S-13 dispatch,
                               covering_plan, the outcome notes sink
src/dociq/pipeline.py          plan union, inventory read at the top of run()
                               inside the fail-closed block,
                               run.published_set_inventory disclosure,
                               the recovery-outcome invocation note
tests/test_emit_atomicity.py   §5 B-8 (6 tests), §6 the state enumeration
tests/test_incomplete_runs.py  one assertion widened for plan normalisation
docs/verification/codex_r3_deletelast_2026-08-05.md   withdrawal
docs/codex_reviews/sprint-2_2026-08-06_claude_r4.md   withdrawal
docs/decisions/decision_register.md   D-31 implementation note
```

Commits: `4a1cb5e` (B-8 + the state dispatch), `5fe998d` (the BLOCKED message
names the right file), `0c95901` (the recovery says which outcome it reached).

**Untouched, and checked:** `src/dociq/contracts.py`, `src/dociq/gui/pipeline.py`
and `src/dociq/emit/handoff.py` — frozen or owned by the agent doing A-6/A-7. No
`amendments.toml` entry is required because no frozen surface changed;
`tools/check_amendments.py` passes (19 entries, all applied ones wired).
