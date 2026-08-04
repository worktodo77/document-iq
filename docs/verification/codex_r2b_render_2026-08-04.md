# Codex review #2 fix round — A-3 and A-4 closed

**Repository:** `worktodo77/document-iq`
**Branch:** `build/s2-r2-render` (off `build/sprint-2` @ `5ab2a79`)
**Date:** 2026-08-04
**Scope:** fix-round findings **A-3** and **A-4** only. **B-4**, **B-5** and
**D-2** belong to another agent; `src/dociq/emit/paths.py`,
`src/dociq/pipeline.py`, `src/dociq/emit/log.py`, `src/dociq/contracts.py`,
`src/dociq/gui/pipeline.py` and `docs/contracts/amendments.*` were **not
edited** here.

---

## Stop-the-line items: NONE

Neither frozen module changed. A-3 needed no new `TerminalStatus` member — the
member existed and its *renderer* did not know about it — and A-4 is entirely
inside `dociq.emit.handoff`, which is not frozen. No amendment was raised, so
`docs/contracts/amendments.toml` needed no entry and `tests/test_amendments.py`
is untouched and green.

---

## A-3 — every terminal status is rendered as itself

`RunTermination.headline()` recognized `COMPLETED` and `BLOCKED` and let every
other member fall through a ternary to the word `CANCELLED`. Amendment A-15 added
`REFUSED` and did not touch that renderer.

**Reproduced, not reasoned.** Watched red before the fix:

```
REFUSED -> "RUN CANCELLED — NO DELIVERABLES WERE WRITTEN …"
```

and, through the real pipeline with a real red Stage-6 accounting gate,
`incomplete_run/run_status.json` on disk carrying `"terminal_status": "refused"`
two lines from `"headline": "RUN CANCELLED …"`.

### What changed

`dociq.runstate` now carries `STATUS_PROSE`, a `TerminalStatus -> StatusProse`
map with an **import-time tripwire** asserting it covers every member, and two
total lookups over it:

- `RunTermination.headline()` — `STATUS_PROSE[self.status].headline(reason)`.
  `REFUSED` reads *"PUBLICATION REFUSED — DocIQ read the COMPLETE set and then
  refused to publish it at its own §4 Stage 6 integrity gate. Nobody stopped
  this run. …"*
- `RunTermination.coverage_note()` — **new**, and it is the second half of the
  finding. `SummaryView.status_banner()` used to author one sentence and append
  it to every non-complete status: *"The figures below describe only what was
  read before the run stopped."* True of a cancelled run, false of a refused
  one. The banner now reads both sentences from the same per-member table, and
  the refused wording says the figures describe the **complete** corpus that was
  rejected rather than cut short.

There is **no fallback branch**. An unmapped member raises at import; if the
tripwire were deleted, `headline()` raises `KeyError` rather than guessing.

### The three consumers, asserted

`tests/test_terminal_status_rendering.py`:

- `run_status.json` **on disk**, from a real refused run driven through
  `dociq.pipeline.run` with a real red gate — the machine field and the headline
  must agree.
- The summary **PDF**: text extracted from the rendered `run_summary.pdf`
  contains `PUBLICATION REFUSED` and does not contain `CANCELLED`; the
  `SummaryData` behind it is asserted separately, so a renderer change cannot
  quietly pass by dropping the line.
- The **GUI banner**, per member, offscreen.

Plus: no two members render the same headline; each headline carries its own
word and no other member's.

---

## The CLASS behind A-3 — the enumeration, and what it found

The finding is the fifth instance of one class (A-12, A-14, B-3, A-11b were the
others), so the question answered here is not "does `headline()` handle
`REFUSED`". A dedicated sweep enumerated **every** place a contract enum becomes
operator-facing text.

**Seven enumerations exist** in the product: `TerminalStatus`,
`ProcessingStatus`, `IdRegime`, `Disposition`, `PageKind` (`contracts.py`),
`IdNamespace` (`docid/ids.py`), `DecisionStatus` (`identify/bates.py`).

| Renderer | Verdict |
|---|---|
| `runstate.RunTermination.headline` | **WRONG TODAY.** Fixed above. |
| `adapter` §6-checklist `engaged=` flag | **Latent wrong word.** Fixed. |
| `view_models.build_summary` ID-regime sentence | **Latent wrong word.** Fixed. |
| `emit/indexbook._status_label` (`ProcessingStatus`) | Already total (dict subscript). |
| `view_models.status_word` (`ProcessingStatus`) | Already total. |
| `view_models.disposition_word(Disposition)` | Already total. |
| `handoff.render_readme` `id_regime` prose | Free-form string, not an enum render. Default corrected. |
| `PageKind`, `IdNamespace`, `DecisionStatus` | **No prose renderer exists.** No exposure. |

### The two latent ones, fixed rather than recorded

**`adapter.py` — a `Disposition` flattened to a bool that is later rendered as a
word.** `engaged=rule.disposition is Disposition.DROP` fed
`LeverRow.disposition_word()`, which is `"DROP" if self.dropped else "KEEP"`. A
third `Disposition` member would arrive as `False` and be printed to the expert
as **KEEP** — on §6's approval checklist, the screen where an expert signs for
an omission. Now `_LEVER_ENGAGED[rule.disposition]`, a total map with its own
tripwire.

**`view_models.build_summary` — an `IdRegime` sentence chosen by a proxy.** The
note branched on `master_index is None` rather than on the regime the run
recorded. Now `_ID_REGIME_NOTE[result.config.id_regime](index)`, total. A test
substitutes both entries and asserts the screen reads them, so the map is proven
to be on the path rather than merely to exist.

**`handoff.build_upload_package`'s `id_regime` default was `"DIQ-native"`, a
string no `IdRegime` member has ever produced.** Corrected to
`IdRegime.NATIVE.value`. Drift, not a live bug.

### The standing probe

`tests/test_terminal_status_rendering.py` ships three tripwires that fail on a
member added next year rather than printing the wrong word:

1. **`test_no_operator_facing_string_is_chosen_by_an_enum_ternary`** — an AST
   scan of every file under `src/dociq` for `IfExp` nodes whose test compares
   against an enum member and whose branches are both string literals.
   `ALLOWED_TERNARIES` is **empty**. Watched red against the pre-fix code, where
   it named `runstate.py:224` unprompted.
2. **`test_no_enum_is_flattened_to_a_bool_that_is_later_rendered_as_a_word`** —
   the blind spot in (1), because the §6 case threw the enum away one layer
   earlier. Scans keyword-argument and assignment values for enum comparisons.
   `ALLOWED_FLATTENINGS` has **one** entry, `gui/mock_pipeline.py:699`, with its
   justification written beside it: that bool becomes a row COLOR, and
   "not `FULL`" is the correct answer for any member that is not `FULL`. Watched
   red against the pre-fix adapter.
3. **`test_the_enum_list_this_probe_scans_for_is_complete`** — the scan's own
   enum list is derived from source and asserted against the hard-coded one, so
   a new enumeration cannot be silently unscanned.

**What probe (2) does not catch, stated rather than implied:** a flattening
performed inside a helper function and returned, rather than written at a call
site or an assignment. That shape is not present in `src/dociq` today; the probe
would not see it if it appeared.

---

## A-4 — a failed package build never leaves a current partial folder

`build_upload_package()` deleted the prior package, created the final
`upload_package/`, and wrote into it one file at a time. Any later copy, filter,
README or validation exception left that current, partial, unvalidated directory
under the name an operator drags into a Claude Project — while the GUI's failure
state said *"Any package already on disk is from an EARLIER build."*

### What changed

Assembly moved into `_assemble_package()`, which writes into whatever directory
it is given and knows nothing about `upload_package/`. `build_upload_package()`
now orchestrates:

1. Remove any `upload_package.incoming` / `.superseded` left by an earlier
   crashed attempt — **and check they are gone**. If not, the build does not
   start and the published package is not touched.
2. Assemble and validate into `upload_package.incoming/`.
3. On **any** `BaseException` (including `KeyboardInterrupt`): discard the
   sibling and re-raise the original untouched. If the discard itself fails, a
   `PackageSwapError` carrying the original text plus the residue's location is
   raised instead — the failure is never absorbed.
4. Publish: rename the earlier package aside, **remove it and check it is
   gone**, then rename staging onto the published name.

### Why the removal is step 4b and not last

The obvious order — publish, then tidy up — has one outcome this one does not: a
correct published package beside a stray folder of the previous one. Reporting
that makes the GUI say *"The upload package was NOT built"* about a package that
**was** built, validated and published, which is a false statement of exactly
the class A-4 is about; absorbing it leaves the stray folder. Removing first
means the only reachable states are *the earlier build is intact and nothing was
published* and *the new package is published and is the only one* — both of
which the screen already describes truthfully. An intermediate version of this
fix had the tidy-up-last order and its test asserted the reporting; **that
claim and that test are withdrawn**, not reworded.

### Fail-before coverage — `tests/test_package_swap.py`

Every one watched red against the restored in-place build (9 of 13 red; the
other four assert behavior the old code also had, and are regression guards):

- **Failure after at least one file is written**, parametrized over the three
  points where that is true: the README write (Codex's own site),
  `assert_only_sanctioned`, `_assert_manifest_matches_folder`. The failing
  rebuild is deliberately a **narrower scope** than the first build — with an
  identical scope the two validation cases went green against the old code
  because the partial directory happened to hold the same files, and a
  fail-before that passes for that reason establishes nothing. That was caught
  and corrected here (commit `da2c7bf`), not discovered downstream.
- The abandoned attempt's files are **nowhere in the matter folder**, not merely
  absent from the package.
- A failed **first** build leaves no package folder at all, so the GUI's
  "EARLIER build" sentence is vacuously true rather than true of a folder the
  failed attempt made.
- A leftover staging directory that cannot be removed **stops the build**.
- The earlier package that cannot be moved aside, and the one that cannot be
  removed, both leave the earlier build byte-for-byte and publish nothing.
- The unrecoverable window — the final rename fails after the earlier package is
  deliberately gone — leaves **no** package-shaped folder behind.
- `_remove_tree` answers with the state of the disk, which is the property every
  caller branches on. `shutil.rmtree(..., ignore_errors=True)` on its own is the
  shape Codex named in B-4; the errors are still retried and swallowed here, but
  the ANSWER is `path.exists()` afterwards.

### Disk AND screen, in one test

`test_the_screen_and_the_disk_agree_after_a_failed_build` drives the real
`MainWindow` with the real `RealPipeline` and the real emit layer: it builds a
package successfully, then fails the README write, then asserts the screen says
`NOT built` / `EARLIER build` **and** that the bytes in `upload_package/` are
byte-identical to the earlier build, with no sibling directories. One test,
because the finding was that the two disagreed.

### The claim, withdrawn and re-established

`view_models.package_failed()`'s second sentence is unchanged in wording and
rewritten in status: its docstring now records that the sentence was **false**
when written, and that it is true now by construction rather than by care, with
a pointer to the test that asserts it against the disk.

---

## Verification

- **Fail-befores watched RED**, by restoring the defective code and re-running,
  then restoring the fix via `git checkout`:
  - A-3: 9 failures, including the AST probe naming `runstate.py:224`.
  - A-3 siblings: the flattening probe naming `adapter.py:667`; the ID-regime
    binding test.
  - A-4: 9 failures across the three assembly points, the screen+disk test and
    the fail-closed removals.
- **The two new modules: 30 consecutive green runs**, 40 tests each, no
  ordering, temp-directory or retry flakiness observed. 30 rather than 8 because
  every test here is temp-directory and retry-loop sensitive.
- **Full suite: 8 runs at `ee6be3a`.** Runs 1–2 fully green. Runs 3–8 each
  reported **exactly one** failure, and it is the same one every time:
  `tests/test_offline.py::test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`,
  diagnosed below and proven present on the base commit. **No test touched by
  this change failed in any of the eight runs.**

  ```
  HEAD=ee6be3a420e2d53bcd46fc2f057a8516af12e5f3
  FULL RUN 1 exit=0 :: 0 failed
  FULL RUN 2 exit=0 :: 0 failed
  FULL RUN 3 exit=1 (471s) :: 1 failed :: test_offline.py::…WHOLE_PIPELINE_RUN
  FULL RUN 4 exit=1 (463s) :: 1 failed :: test_offline.py::…WHOLE_PIPELINE_RUN
  FULL RUN 5 exit=1 (460s) :: 1 failed :: test_offline.py::…WHOLE_PIPELINE_RUN
  FULL RUN 6 exit=1 (457s) :: 1 failed :: test_offline.py::…WHOLE_PIPELINE_RUN
  FULL RUN 7 exit=1 (426s) :: 1 failed :: test_offline.py::…WHOLE_PIPELINE_RUN
  FULL RUN 8 exit=1 (426s) :: 1 failed :: test_offline.py::…WHOLE_PIPELINE_RUN
  ```

  **Runs 1–2 ran on a quiet machine; runs 3–8 ran while another agent's own
  8-full-suite-plus-30-repeat series was executing in a sibling worktree.** That
  is stated because it is the variable, not as an excuse: the diagnosis below
  explains exactly why load flips this test and nothing else.

  **`8 of 8 green` is NOT claimed.** What is claimed is: 8 of 8 green on
  everything this change touches, and one pre-existing, now-diagnosed,
  load-dependent failure that is reproducible on the base commit.

- `tests/test_amendments.py` green and untouched.

### Found in passing and DIAGNOSED, not fixed: the offline probe's intermittent

`tests/test_offline.py::test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`
has carried a disclosure that it "fails intermittently under concurrent load"
and that "its failure was not diagnosable" and "has never reproduced on demand".
It failed in both of this branch's first two full-suite runs, so it was chased
rather than waved through.

**It reproduces on demand and the cause is CPython's, not DocIQ's.** All seven
recorded attempts are `subprocess.Popen('ver', shell=True)`. On Windows
`platform.system()` shells out to `ver` the first time the OS version is asked
for; `onnxruntime/capi/_pybind_state.py` calls `platform.system()` at import;
DocIQ imports `rapidocr_onnxruntime` **lazily**, from `extract.ocr_model_dir()`
via `walker.effective_limits` — so with OCR off that import happens **inside**
the guard. `platform.uname()` memoizes, so whether the spawn lands inside or
outside the guard depends on whether anything asked first. The other six are the
OCR page pool's worker threads racing the same cold cache.

| Condition | Result |
|---|---|
| 6 concurrent probes, unmodified | **5 of 6** `ATTEMPTS=7` |
| 6 concurrent probes, unmodified (second round) | **6 of 6** `ATTEMPTS=7` |
| 12 concurrent probes, `platform.uname()` called before the guard | **12 of 12** `ATTEMPTS=0` |
| 1 probe in isolation, unmodified | `ATTEMPTS=0` |

**The guard is right**: a whole pipeline run does create child processes.

**It is present on the base commit, proven by a controlled experiment.** A
detached worktree was created at `5ab2a79` (the branch base) and the single
offline test was run there and here **simultaneously**, eight pairs at once, so
both sides saw identical machine load:

| Commit | Failures |
|---|---|
| `5ab2a79` (base, before this change) | **3 of 8**, each `ATTEMPTS=7` |
| `ee6be3a` (this branch) | **0 of 8** |

An earlier, sequentially-interleaved round of the same comparison gave base 0/8
and this branch 1/8. Pooled: base **3 of 16**, branch **1 of 16**. The defect is
on both sides; its rate tracks load, not the commit. This change touches none of
`ingest/extract.py`, `ingest/walker.py`, `verify/offline.py` or the OCR thread
pool.

**Not fixed here.** It is outside A-3/A-4, it is in files this agent was not
given, and the fix is a ruling rather than a patch — warm `platform.uname()` at
start-up so the count is about DocIQ; or attribute `platform._syscmd_ver` the
way `offline.TRANSPORT_MODULES` already attributes stdlib transport; or accept
it and restate what the shipped `--offline-probe` tells law-firm IT. All three
change a claim made to a client.

**What IS done here:** the false half of the disclosure is withdrawn. The
docstring in `tests/test_offline.py` now carries the diagnosis, the intervention
that confirms it, and the three options — and no longer says the failure could
not be diagnosed or reproduced.

### One aborted run series, disclosed

The first attempt at the 8-run series was **stopped and discarded**, not
reported. Its run 1 went red on two tests:

1. `tests/test_gui_states.py::test_the_chrome_is_us_english` — the A-3 prose in
   `runstate.py` used the en-GB spelling "recognised". A real defect in this
   change; fixed in `36eb016`, and the sweep for the other en-GB words the test
   looks for came back empty.
2. `tests/test_offline.py::test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`
   — the **already-disclosed** intermittent, whose docstring records 2 failures
   in 24 concurrent jobs and states that its cause is not diagnosed. Nothing in
   this change touches the offline guard, subprocess probe or any import path
   it inspects. The disclosure is repeated here rather than recast as closed.

The series was restarted from scratch after the spelling fix rather than
counting the surviving runs, because the tree changed underneath them.

## Limitations, disclosed and still disclosed

- **Nobody has ever driven this GUI with a mouse.** Every GUI assertion here
  runs under the offscreen platform plugin and reads view-model text or widget
  state. What that does and does not prove is stated in
  `docs/verification/codex_r2_uigap_2026-08-04.md` and is not recast here.
- The AST probes are **shape** detectors. They catch the two shapes that have
  actually produced wrong words in this product; they are not a proof that no
  enum can reach an operator as the wrong word. The named blind spot is above.
- The `_remove_tree` / `_retry_rename` helpers are **duplicated** rather than
  reused from `dociq.emit.paths`, whose `_retry_io` is private and under
  concurrent revision for B-4. If B-4's fix promotes a shared helper, these
  should collapse into it.
- Windows file-lock behavior is simulated by monkeypatching, not by taking a
  real exclusive lock. The retry loops are therefore exercised for their control
  flow, not against a real antivirus scanner.
- One branch of `_publish_package` is **not covered**: the double failure where
  the superseded package cannot be removed *and* cannot be renamed back. Its
  message is asserted only by reading the source. The single-failure branch on
  either side of it is covered.
- `tests/test_offline.py::test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`
  remains intermittent under concurrent load, as its own docstring discloses.
  This change does not touch it and does not close it.
