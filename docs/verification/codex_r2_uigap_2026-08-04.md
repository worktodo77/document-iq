# Codex review #2 — A-1, A-2 and B-3 closed

**Repository:** `worktodo77/document-iq`
**Branch:** `build/s2-fix-uigap` (off `build/sprint-2` @ `e02c292`)
**Date:** 2026-08-04
**Scope:** Codex review #2 findings **A-1**, **A-2**, **B-3** only. B-1 and B-2
belong to another agent and `src/dociq/pipeline.py`, `src/dociq/emit/paths.py`,
`src/dociq/gui/pipeline.py` and `src/dociq/contracts.py` were **not edited**
here.

---

## Stop-the-line items: NONE were needed

The frozen modules did not have to change. `PackageResult.missing` already
existed on the seam — B-3 was the *population*, not the declaration — and A-1
and A-2 are rendered entirely from records the seam already carries. No
amendment was raised, so `docs/contracts/amendments.toml` needed no new entry
for the fix itself. One **existing** entry's claim is corrected below.

---

## B-3 — missing Path-A documents no longer drop at the seam

`RealPipeline.build_package` now passes `missing=package.missing`, read off the
emit layer's own `UploadPackage`. **`RealPipeline.last_package_missing` is
deleted**, along with its docstring.

The test that guarded it is replaced, not amended. It asserted the private
attribute and therefore passed for the length of the sprint while the returned
record carried `()`. It now asserts:

- the returned `PackageResult.missing` from a real run
  (`tests/test_adapter.py::test_the_seam_result_carries_the_missing_doc_ids`);
- that the holding attribute is *absent*, so it cannot come back;
- the **rendered screen text**
  (`tests/test_seam_population.py::test_every_rendered_seam_field_reaches_a_screen`,
  and `tests/test_gui_failure_states.py::test_a_short_package_names_the_documents_it_could_not_include`).

**Claim withdrawn.** `docs/verification/rehearsal_fixes_2026-08-03.md` §B5 said
the value was held "the same treatment `library_issues` already gets" and was
therefore not dropped. It was dropped, for every consumer of the declared seam.
That paragraph now carries a superseding note; the old docstring's assertion
that the value sat "where a screen can reach it" is gone from the code.

---

## A-1 — the package build is observable, both ways

`MainWindow._build_package` retains the `PackageResult` and renders it.

- **Success** — `PackageOutcomeView` (`gui/view_models.py`) states the real
  path, document count, file count and size, all read off the record the emit
  layer returned; `PackageResult.scope_statement` is the statement the package
  actually carries. Missing Doc IDs are rendered **separately** from the size
  line, named and counted, in the warn colour.
- **Failure** — the exception text is carried **verbatim** into the same panel,
  with a sentence saying nothing was written and that any package already on
  disk is from an earlier build. `print()` is gone; the shipped GUI is a
  windowed executable with no console.
- **Every** exception is caught, deliberately, not a named list.
- The panel is **cleared on a scope change and on re-entering the screen**. A
  success banner surviving a scope change would say the package on disk covers
  the set now described beneath it, which is the D-20 subset confusion in a
  reassuring colour.

Wording lives in view models, not in widget strings, so it is asserted without a
QApplication and success and failure cannot drift into each other.

---

## A-2 — a settled run is a different screen from a running one

`ProgressScreen` has explicit states.

- `settle()` — the thread has stopped, however it stopped. **Cancel is
  disabled**, not hidden. Called on success as well as failure.
- `fail(message)` — the pipeline's text verbatim and in full, plus two working
  actions: **← Back to setup** and **Try this run again**.
- `stopped(reason)` — an operator-initiated stop is settled the same way and
  offered the same recovery, but is **not worded as a failure**. `RunAborted`
  now has its own worker signal so a deliberate stop cannot be reported as a
  fault. (The real pipeline handles its own cancellation and returns a
  `CANCELLED` outcome; this path exists for a stand-in or a future stage that
  lets the exception out.)
- `reset()` restores the in-flight state, so a retry does not show the previous
  attempt's error.
- `MainWindow` retains the last `RunRequest` and `retry_run()` replays **the
  same request**. `start_run` refuses to start over a live thread and joins a
  quitting one first.

---

## The class, not the repro — seam-field enumeration

Codex is right that the question is not "is `missing` propagated". B-3 is the
third instance in one sprint of a seam field added and never wired
(A-12, A-14, B-3). `tests/test_seam_population.py` enumerates **every field of
every frozen presentation record on the seam**, found by reflection, and holds
each to one of three rulings:

1. **Source probe** — every construction of a seam record in
   `src/dociq/adapter.py` must name every field, or the omission must appear in
   `UNPASSED` with a stated reason. Correct-by-construction: a *new* field fails
   the probe until someone decides where it comes from. This is the probe that
   catches B-3 before anything runs.
2. **Runtime probe** — a real end-to-end run plus a real Path A build that is
   deliberately one document short must produce a **non-default** value for
   every field declared measurable. An explicit keyword passing a constant
   default satisfies probe 1 and fails this one.
3. **Render probe** — fields declared as reaching the operator must have their
   value appear in rendered screen text.

### What the enumeration found

**A fourth instance, one amendment older than B-3.**
`ReductionLever.rule` and `ReductionLever.note` — A-11b's verbatim matching
pattern and the expert's own stated reason for an omission — were declared on
the seam, documented at length, preserved across `with_toggled`, covered by two
probes, and **populated by no adapter and rendered by no screen**. All three
construction sites in `adapter.py` left both at `""`, including
`profile_rules`, which is the §6 checklist path the amendment was written for.
The checklist could show that a DROP rule existed and never what it catches or
who approved it.

Fixed in the same package, per *fix, don't defer*:

- `profile_rules` passes `rule=rule.pattern`, `note=rule.notes`;
- the post-run waterfall threads the run's own profiles into `_plan`;
  `_section_rules` / `_rule_text` attribute a section to its rule and **return
  nothing when the section name is ambiguous** (two rules can share a label —
  the documented partial case) rather than putting one expert's words against
  another rule's pages;
- `ChecklistRow.matched_by()` / `expert_note()` and
  `ProfileChecklistScreen._row_widget` render both. An absent pattern renders as
  a stated absence, never a blank.

`docs/contracts/amendments.md`'s "A-11b — APPLIED … Both fields are carried
verbatim" is corrected in place: it was true of the seam and false of the
product.

### Declared exemptions (the enumeration's other half)

Each is a ruling that the default is the right value **at that site**, not a
note that it has not been done:

| Field | Site | Why |
|---|---|---|
| `ReductionPlan.capacity` | `_plan` | D-21's ruled reference line; the adapter has no run-specific capacity, and passing the constant would put the number in a fourth place. |
| `TokenEstimate.ratio_refuted` | `_estimate(None)` | The no-figure branch, for a run that published nothing. The measured branch passes it. |
| `TokenBasis.*` | `profile_rules` | `TokenBasis()` is the "nothing was measured" sentinel; its emptiness is its meaning. |
| `FolderPreview.by_extension`, `.estimated_minutes` | not-a-directory branch | There is no folder to describe. |
| `RunRequest` (whole record) | — | Travels GUI → pipeline. "Populated by RealPipeline" is not a property it can have. |

`test_the_enumeration_covers_every_record_on_the_seam` fails if a ruling
outlives the field it ruled on, and
`test_no_private_holding_attribute_stands_in_for_a_seam_field` refuses the B-3
anti-pattern by name.

---

## A defect the work found on its own

Adding `failed` and `stopped` to the screen-state grid immediately reported
**`progress/failed` scrolling sideways at the product's minimum window**. The
progress row's status label was unwrapped, so its width set the scrolled
widget's minimum — and the failure row's status is the pipeline's exception
text, the longest string that list can hold. The filename beside it had been
wrapped for exactly this reason and the status had not. Fixed
(`screens.py`, `ProgressScreen.append`).

---

## Fail-before evidence — every one WATCHED RED

Each was produced by reverting the specific behaviour in place and running the
suite, then restoring from a byte-identical copy.

**A-1 / A-2** — `_build_package` restored to discarding the result and
`print()`ing the exception, `_run_failed` restored to appending one flagged row,
`settle()` / `stopped()` removed. **11 of 12 tests in
`tests/test_gui_failure_states.py` went red** (the twelfth is the second-run
guard, which the mutation did not touch):

```
FAILED test_a_failed_run_reaches_an_explicit_failed_state
FAILED test_the_failed_screen_returns_to_setup
FAILED test_retry_reruns_the_same_request_and_can_succeed
FAILED test_cancel_is_dead_after_a_successful_run_too
FAILED test_an_aborted_run_settles_without_being_called_a_failure
FAILED test_a_successful_build_states_the_path_counts_and_size
FAILED test_a_short_package_names_the_documents_it_could_not_include
FAILED test_a_failed_build_is_an_error_on_the_same_screen
FAILED test_changing_the_scope_clears_the_last_build
FAILED test_revisiting_the_handoff_clears_the_last_build
FAILED test_the_build_never_lets_an_exception_reach_the_event_loop
```

**B-3** — with `missing=package.missing` removed,
`test_the_seam_result_carries_the_missing_doc_ids` read
`assert () == ('LI-99999',)`. With the holding attribute restored, the same test
failed on `hasattr`.

**B-3 + A-11b, through the probe** — with `missing=`, `rule=` and `note=`
removed from all four construction sites, six probe tests went red, including
the source probe for **both** `PackageResult` and `ReductionLever`, the runtime
probe for `PackageResult.missing`, and **both** render probes.

---

## Repetition

- `test_the_failed_state_is_reached_every_time` is parametrized **30 times**.
  It crosses the worker-thread boundary, where `failed` and `QThread.quit` are
  ordered only by Qt's queued delivery, and the standing rule is 30 runs for
  threading-sensitive work.
- The changed suites were run repeatedly; see the run log at the foot of this
  note.

---

## Disclosed limitations — these stay disclosed

- **Nobody has ever driven this GUI with a mouse.** Every assertion here runs
  under the offscreen Qt platform plugin and reads widget state and label text.
  It proves a value reaches a widget with the right text; it does **not** prove
  a human sees it on a monitor, that the panel is not clipped on a real display,
  or that the buttons are reachable by pointer. Unchanged by this package.
- Widget *visibility* is not asserted for the package panel. Under the offscreen
  plugin no top-level window is shown, so `isVisible()` is false for every
  widget on every screen; the panel is **cleared** when there is no outcome and
  the accessors read text, which is a weaker but honest signal. The progress
  screen's recovery buttons use `isHidden()`, which does report explicit hiding.
- The GUI tests for A-1 and A-2 run against stand-in pipelines. That is
  deliberate — the findings are properties of the window and the screen — but it
  means these particular tests do not exercise the real adapter.
  `tests/test_adapter.py` and `tests/test_seam_population.py` hold the real
  implementation to the same records, including one render assertion driven from
  a **real** run and a **real** package build.
- The seam-population source probe scans `src/dociq/adapter.py` only. The
  Sprint-1 mock (`gui/mock_pipeline.py`) constructs the same records and is not
  covered; it is a stand-in by design, but this is a gap in the probe's reach
  and is not claimed otherwise.
- The runtime probe's `MEASURED` list is what the fixture corpus exercises, not
  every field. `RunOutcome.reconciliation` (no master index) and
  `BatesProposal.alternatives` (single-series) are legitimately empty there and
  are not asserted non-default.
- B-1 and B-2 are **not** addressed here.
