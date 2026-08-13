# A-6 and A-7 — the package state machine, enumerated backwards

**This file:** `docs/verification/codex_r4_pkgstate_2026-08-06.md`
**Branch:** `build/s2-r4-pkgstate` (off `build/sprint-2` @ `a4973b9`) · **Date:** 2026-08-06
**Gate:** D-10, Codex review #2, **fourth** fix round
**Verdict answered:** `docs/codex_reviews/sprint-2_2026-08-04_codex.md`, third
fix-round section
**Findings closed here:** **A-6**, **A-7**, and one sibling of A-7 found by
enumeration rather than reported. **B-8 is another agent's package and is not
touched by this branch.**

Measured, not expected. What is reasoned rather than measured is labelled as
such in §7, and what remains unproven is in §8.

---

## 1. What the two findings actually were

Both are in `src/dociq/emit/handoff.py`, and both are the *same mistake in two
directions*: the package state machine was reasoned **forward** through
assembly, so a value that describes the state of the disk was ignored on the way
in (A-6) and on the way out (A-7).

**A-6.** The package's two renames are not a transaction. Between them — and
after a rollback rename that itself failed — the matter holds no
`upload_package/` and a complete previous package under
`.dociq/package_superseded/`. That directory is then the only intact package in
the matter, and the tool's own failure message says so and offers *"build
again"* as a way out. The next build opened by classifying **both** state
directories as disposable residue and deleting them **before assembling
anything**. So the recovery DocIQ offered destroyed the only thing worth
recovering, and an ordinary assembly error after that left the operator with
nothing: none published, none staged, none set aside. Delete-before-publish,
inside the redesign whose whole point was to delete last.

**A-7.** `_publish_package()`'s post-publish cleanup called
`_remove_tree(package_superseded)` and **discarded the boolean** — the boolean
that helper exists to return, written precisely so that no caller would assume a
removal happened. A scanner holding one file open makes `rmtree` give up part
way; the function returned an ordinary success anyway. `UploadPackage` had no
field for it, so nothing crossed the seam, and A-16 did not cover it:
`emit.paths.superseded_residue()` matches matter-swap directories named
`superseded*`, and the package tree is called `package_superseded` — a blind
spot exactly the width of the package path.

---

## 2. The state enumeration — the real deliverable

Codex's class statement is *"re-enumerate the redesigned state machines from
every persistent state."* A-6 exists because the machine was read forward from
`build_upload_package()` instead of backward from what a previous invocation can
leave on disk. Below is that enumeration. `P` = `upload_package/`, `S` =
`.dociq/package_staging/`, `U` = `.dociq/package_superseded/`.

| # | P | S | U | how it is reached | what the next invocation must do | before | after |
|---|---|---|---|---|---|---|---|
| 1 | – | – | – | first ever build; a first build that failed | build; nothing to lose | correct | correct |
| 2 | – | ✓ | – | died during assembly, no prior package existed | discard S, build | correct | correct |
| 3 | – | – | ✓ | **the A-6 state** — died between the two renames, or the rollback rename also failed | **RESTORE U**, then build | **destroys the only package** | restores it |
| 4 | – | ✓ | ✓ | as row 3, with the staged package still on disk | **RESTORE U**, discard S, then build | **destroys the only package** | restores it |
| 5 | ✓ | – | – | the ordinary steady state | build | correct | correct |
| 6 | ✓ | ✓ | – | died during assembly over an existing package | discard S, build | correct | correct |
| 7 | ✓ | – | ✓ | post-publish cleanup failed — `P` is the NEW package | discard U (residue), build | correct | correct |
| 8 | ✓ | ✓ | ✓ | as row 7 with staging residue | discard both, build | correct | correct |

**What the enumeration found.** Two rows, not one. Codex reproduced row 3; row 4
is its sibling and was equally broken, and it is the row where the *wrong* safe
answer is available — a staged tree sitting there looks like a package worth
publishing, and nothing on disk says whether it was ever validated. The fix
restores the tree that is **known** complete and discards the one that is not,
and it does the restore **first**: the other order is A-6 with an extra
directory in it.

**The general rule the table produces.** *Every row with `P` present is safe to
clean, because `P` is only ever reached by renaming a fully assembled and
validated tree onto it. Every row with `P` absent and `U` present must restore
before it deletes anything.* That is `_recover_interrupted_publish()`, and it
runs **before** the residue sweep rather than inside it.

**Why the restored tree is known complete, by construction rather than by
hope.** `U` is created by exactly one operation — renaming a complete published
package — and destroyed by exactly one, `_remove_tree`. With the recovery in
front of the sweep, `_remove_tree` is never pointed at `U` while `P` is absent:
the post-publish cleanup runs only after `P` holds the new package, and the
pre-build sweep now runs only after the restore. So "`P` absent and `U` present"
implies "`U` intact". An empty `U` is not a package and is left to the sweep.

**Shipped as a probe, not only as prose.**
`tests/test_package_swap.py::test_no_reachable_start_state_loses_the_only_package`
is the table, parametrized over all eight rows, asserting one sentence about
each: *no start state that contained a complete package ends without one*,
however badly the build goes. A ninth state added later without thought fails to
appear in it.

---

## 3. The fixes

| finding | change | file |
|---|---|---|
| A-6 | `_recover_interrupted_publish()` — restores `U` onto `P` before any cleanup; carries the table above; raises rather than falling through if the restore fails | `emit/handoff.py` |
| A-6 | the failure message's *"or build again"* is no longer a claim the code contradicts — it now states that the next build restores the package **before** it assembles anything | `emit/handoff.py` |
| A-7 | `UploadPackage.residue` — the discarded boolean becomes a named path | `emit/handoff.py` |
| A-7 | `residue=package.residue` on the adapter's `build_package` | `adapter.py` |
| A-7 | `PackageOutcomeView.residue` + `residue_note()` — success first, then the residue, then the path | `gui/view_models.py` |
| A-7 | a dedicated label below the facts and the missing-document note, cleared with the panel | `gui/screens.py` |
| **sibling** | `_staging_residue_note()` — the same discarded boolean at the **three sites that raise** | `emit/handoff.py` |

### 3.1 The sibling, found by enumerating the callers rather than by report

`_remove_tree` has seven call sites in this module. A-7 names one. Enumerating
the rest found three more that discarded the answer: `_remove_tree(staging)` on
every failure path out of `_publish_package()`. What survives there is *worse*
than what A-7 describes — a **complete, validated, package-shaped** tree under
`.dociq/`, which is not the package the operator should upload — and the message
they read said nothing about it. Those sites are already raising, and the
operator reads one message, so the fix appends a sentence to it rather than
adding a second field. Covered by
`test_a_staged_package_that_survives_a_failed_publish_is_named`, parametrized
over both reachable raising sites.

The remaining call sites (`build_upload_package`'s startup sweep and the
assembly-failure path) already branched on the boolean and already named the
directory. All seven now do.

---

## 4. Fail-before, watched RED

Nothing below is a green test asserted to have once been red. Each was run with
the fix mechanically disabled and the failure output read.

| test | disabled | red for |
|---|---|---|
| `test_building_again_after_an_interrupted_publish_does_not_destroy_it` | the `_recover_interrupted_publish` call | no package at all after "build again" |
| `test_a_process_death_between_the_two_renames_is_recovered` | same | the package that survived a kill did not survive the next startup sweep |
| `test_a_staged_tree_beside_the_only_package_is_not_preferred_to_it` | same | row 4 destroyed |
| `test_an_unrecoverable_restore_deletes_nothing` | same | fell through into the sweep |
| `test_no_reachable_start_state_loses_the_only_package[aside-only]` | same | row 3 |
| `test_no_reachable_start_state_loses_the_only_package[aside-and-staging]` | same | row 4 |
| `test_an_undeletable_old_package_reaches_the_result_and_the_screen` | the residue capture in `_publish_package` | `residue == ()` over a `package_superseded` still on disk |
| `test_a_surviving_old_package_is_on_the_screen_and_…_a_success` | the screen's `residue_note()` render | the surviving path is nowhere on the screen |
| `test_a_staged_package_that_survives_a_failed_publish_is_named[aside-rename]` | `_staging_residue_note` | the staged tree is not in the message |
| `test_a_staged_package_that_survives_a_failed_publish_is_named[publish-rename]` | same | same |

Six of the ten went red on the A-6 disable in one run, which is the enumeration
paying for itself: Codex reported one state and the fix is load-bearing for two,
reached four ways.

**The A-7 reproduction uses a REAL Windows handle**, where Codex used one. The
test monkeypatches `_retry_rename` so that the moment the staging tree takes the
published name, a file inside `.dociq/package_superseded/` is opened `rb` — a
scanner's handle, at exactly the point Codex named, between the publish rename
and the cleanup. `shutil.rmtree` then genuinely gives up part way across all
eight retry attempts. The red run confirms the premise held: `superseded.is_dir()`
passed and only the `residue` assertion failed.

---

## 5. Test runs

* **Full suite: 8 consecutive runs, all green.** `1,418` collected,
  `1,417` passed, `1` skipped, every run. Sequential, not parallel — concurrent
  pytest invocations share `tmp_path` numbering and this branch's tests are
  about what is on disk. Runs 1-8 completed 09:27-10:25 on 2026-08-13.
* **`tests/test_package_swap.py`: 30 consecutive runs, all green.** Thirty
  rather than eight because this file is filesystem- and timing-sensitive by
  construction — real open handles, `rmtree` retry loops with sleeps, a rename
  racing a live handle. *A green result proves nothing*, and one green run of a
  file like this proves less than nothing.
* Every fail-before disable was applied and reverted mechanically, and the
  affected slice was re-run green after each revert.

### 5.1 Counts

`1,395` at the R4 handoff → `1,418` here. The 23 new tests are the eight-row
state probe, five two-invocation A-6 tests, the two-site staging-residue
enumeration, the A-7 real-handle test and its clean-build counterpart, the
handoff screen's residue state (grid + assertion), and the amendment register's
missing direction.

---

## 6. Claims withdrawn, not just code changed

* **`_publish_package`'s double-rename-failure message.** It offered the
  operator *"Rename that folder back, or build again."* The second half was
  false when written — building again deleted the folder it had just told them
  about. It now says the next build puts it back **before** it assembles
  anything, and `test_the_offered_recovery_is_the_one_the_message_describes`
  asserts **both** ways out actually work rather than only the one the fix
  touched.
* **`test_a_publish_that_cannot_take_the_name_and_cannot_roll_back_says_where`**
  is the test that deliberately leaves and names this state. It is unchanged and
  still passes, and it is now the *fixture* for the A-6 tests
  (`_interrupt_between_the_two_renames` drives it), so the recovery is proven
  from the product's own covered failure path rather than from a hand-built
  reconstruction of it.
* **`_publish_package`'s docstring** claimed a failed final cleanup "is reported
  as a residue rather than as a failed build". Half of that sentence was true
  (it was not reported as a failure) and half was not (it was not reported at
  all). It is true now.
* **`_remove_tree`'s docstring** says "every caller below branches on it". Three
  callers did not. They do now.

---

## 7. Reasoned, not measured

* **"`U` present with `P` absent implies `U` is intact."** Reasoned from the
  call graph — `U` is written only by one rename and read destructively only by
  `_remove_tree`, and the recovery now precedes every such call while `P` is
  absent — not from a fault-injection campaign over interleavings. The
  enumeration probe exercises the states, not the transitions between them.
* **Process death is simulated by leaving the disk in the state a kill leaves,
  not by killing a process.** `test_a_process_death_between_the_two_renames_is_recovered`
  performs the rename and stops. That is the correct fidelity for a state
  machine read backwards from disk — a real kill would prove the same disk
  state and nothing more — but it is a simulation and is named as one.
* **Windows rename semantics.** Unchanged from the R3 note's §8.3 premise, which
  Codex accepted narrowly: `os.rename` fails when the destination exists, and no
  hidden delete-on-close pending state is assumed for the operation used here.
  Nothing above depends on refuting or extending that.

---

## 8. Not claimed

* **B-8 is untouched.** The matter-swap set-aside plan, `_stale_deliverables()`
  and `commit_staging()` are another agent's package on this fix round;
  `src/dociq/emit/paths.py` and `src/dociq/pipeline.py` were not edited here.
* **No mouse has driven this GUI.** The residue label is asserted through the
  screen-state grid and through the screen's own text accessors under the
  offscreen platform plugin, which is the same signal the operator's click
  emits — and it is not a person looking at it.
* **`RunOutcome.superseded_residue` (A-16) still reaches no screen.** The field
  crosses the seam and the adapter populates it, and nothing in
  `gui/view_models.py` or `gui/screens.py` renders it. A-17's package half is
  rendered end-to-end by this branch; A-16's matter half is not, and that is a
  gap in the round that adopted A-16 rather than something this branch
  introduced or closed. **Flagged for the fifth round.**
* The real-corpus byte-identical claim, criterion 4, and the 3,600-second
  per-file timeout are all unchanged and remain open exactly as the R4 handoff
  left them.

---

## 9. Stop-the-line

* **`src/dociq/contracts.py` — not touched.** Nothing here needed a contract
  change.
* **`src/dociq/gui/pipeline.py` — not touched.** `PackageResult.residue` was
  pre-applied as amendment **A-17** at `0e31730`, recorded at `a4973b9`. This
  branch consumes it and does not edit the module.
* **The branch tip this round started from was RED.** A-17 added
  `PackageResult.residue` at `0e31730` and nothing populated it, so
  `test_seam_population.py::test_every_seam_field_is_passed_explicitly_by_the_adapter[PackageResult]`
  was failing at `a4973b9` — *"adapter.py:886 builds PackageResult without
  naming 'residue'"*. That is the B-3 probe doing exactly its job, and it means
  the seam-half commit was pushed without a suite run behind it. Fixed here by
  the adapter wiring; confirmed by removing that wiring and watching the probe
  go red again. Worth a note to whoever lands seam halves ahead of their
  implementations: the probe is designed to refuse a declared-but-unwired field,
  so a seam-only commit cannot be green by construction.
* **Register hygiene, fixed rather than deferred.** `amendments.toml` carried
  A-16 and A-17 with immutable adopting commits; `amendments.md` — *the file a
  reviewer is sent to* — stopped at A-15. Neither had a prose half. That is the
  D-2 shape with the halves swapped, and the status-agreement test could not see
  it because it only compares entries that exist in both. Prose sections for
  A-16 and A-17 are added here, and A-17's `wired_in` now lists the emit and
  view-model sites this branch wires.
* **…and the missing direction of that check is now a test.**
  `test_every_machine_readable_amendment_is_written_up_in_prose` fails on any
  adopted amendment with no prose half. Watched red on A-16 and A-17 before the
  prose was added. It carries **three named exclusions** — A-04, A-05a and A-11b
  were recorded machine-readably and never written up, they predate the check,
  and authoring their prose is not this round's work. They are named in the
  test rather than skipped by a pattern, because "and anything else we forget"
  is the omission the file exists to catch. **Open for whoever owns the
  register.**
