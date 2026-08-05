# Codex second fix-round review — DocIQ Sprint 2 (merge gate)

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-2`
**Reviewed commit:** `ec19900` (implementation tip `19cfe6b`)
**Fix-round handoff:** `docs/codex_reviews/sprint-2_2026-08-04_claude_r3.md`
**Review date:** 2026-08-05
**Gate:** D-10, Codex review #2, second fix round

## Second fix-round verdict

**NOT PASSED — another fix round is required. One A finding and two B
findings.**

A-3, A-4, B-4, B-5, and D-2 are fixed at the boundaries named in the prior
verdict. The terminal-status prose is total, package assembly is staged, named
refusal facts now reach the durable record, superseded files and directories
are removed with proof, and the amendment registry records and checks immutable
adopting commits.

The merge remains held by three adjacent failure paths. A readiness marker
whose name survives `unlink()` can make the next recovery delete the newly
published set. An ordinary refused run without a master index creates a durable
log whose own content hash disagrees with the hash embedded in its manifest.
And the package rollback can restore a partly deleted prior package under the
published name while assuring the operator that it is intact.

## B-6 — A surviving readiness marker deletes the newly published set on recovery

**Severity class:** B — evidentiary-integrity; a successful current set can be
silently removed by the next recovery.

**Locations:** `src/dociq/emit/paths.py:601-615`,
`src/dociq/emit/paths.py:649-655`

The B-4 fix now proves every named superseded file and directory is gone, but
the final marker removal deliberately uses `_retry_io(marker.unlink)` rather
than the same proof. The comment calls a lingering marker benign because the
next roll-forward supposedly has nothing left to do. That marker still contains
the superseded list, however. After the staged files have taken those same
names, the next recovery interprets the stale authorization again and removes
the new files before discovering that staging is empty.

**Failure scenario:** Windows delete-on-close semantics, an on-access scanner,
or a filesystem shim lets `unlink()` return while `staging_ready.json` remains
visible. The first `commit_staging()` reports success and the new
`sources.json` is visible. On the next run, `recover_pending()` reads the stale
marker, removes that new `sources.json` as superseded, finds no staged
replacement, deletes the marker, and returns. The direct reproduction produced
exactly `after_first: new, marker=True` followed by
`after_recovery: sources_exists=False, marker=False`.

Prove marker removal before reporting success, or atomically replace it with a
completed/harmless recovery record before attempting deletion. Add a
fail-before test that leaves the marker name visible after the first commit and
runs recovery a second time.

## B-7 — A no-index refusal log contradicts its embedded manifest hash

**Severity class:** B — evidentiary-integrity; one durable audit file gives two
different identities for its own hashed content.

**Locations:** `src/dociq/pipeline.py:778-794`,
`src/dociq/pipeline.py:1419-1425`,
`tests/test_publication_gate.py:810-816`

The published path passes `reconciliation=None` when no master index was
supplied. `_abort()` instead passes the always-created no-index
`ReconciliationReport`. The manifest is built from the staged, published-style
log before refusal; `_abort()` then rebuilds the quarantined log with a
different hashed `content.reconciliation`. The R3 comparison test checks two
refused logs and selected fields, so it does not exercise the promised full
published-versus-refused content equality in this ordinary configuration.

**Failure scenario:** a matter without a master index reaches Stage 6 and is
refused. Its quarantined `processing_log.json` says
`content.reconciliation.warnings == ["no master index was supplied; reconciliation was not run"]`,
while the staged log used to build the embedded manifest had
`reconciliation: null`. In the direct reproduction, refused and published
`content` and `content_sha256` differed; worse,
`run.output_manifest.log_content_sha256` did not equal the same file's top-level
`content_sha256`. A verifier must choose which of the record's two hashes to
believe.

Make `_abort()` use the same no-index projection as the published path. Add an
on-disk assertion that the full refused and published `content` values and
hashes agree over the same no-index corpus, and that the refused log's embedded
manifest hash equals its own top-level content hash.

## A-5 — Package rollback can call a partially deleted build “intact”

**Severity class:** A — real user-facing bug in ordinary internal use.

**Locations:** `src/dociq/emit/handoff.py:105-126`,
`src/dociq/emit/handoff.py:640-668`, `tests/test_package_swap.py:240-273`

`_remove_tree()` repeatedly invokes `shutil.rmtree(..., ignore_errors=True)` and
returns whether the path ultimately vanished. That is sufficient to stop a new
publish, but not to roll back deletion: a failed attempt may already have
removed most of the old package. `_publish_package()` renames whatever remains
back to `upload_package/` and, if the rename succeeds, tells the GUI that the
earlier build is “back in place and intact.” The test double returns `False`
without deleting anything, so it assumes the property the production helper
cannot guarantee.

**Failure scenario:** an antivirus process holds one file in the superseded
package while `rmtree` removes the other files. Exhausting retries returns
`False`; rename-back succeeds. The screen reports that nothing was published
and the earlier build is intact, and the ordinary published package name exists,
so the operator can upload it. The direct reproduction started with six files,
removed one before returning `False`, restored a five-file package, and emitted
the exact “back in place and intact” assurance.

Do not expose a destructively modified backup as an intact published package.
Preserve an immutable prior package until the replacement can claim the name,
or validate the restored tree against a pre-removal inventory/hash and report
the damaged state accurately. The fail-before must partially remove the
superseded tree before returning failure and assert both bytes on disk and GUI
wording.

## Second fix-round verification performed

- Fetched and checked out `build/sprint-2`; reviewed the clean remote tip at
  `ec19900` and read the R3 handoff from the branch.
- **536 targeted tests passed** across publication/refusal, atomic emit,
  package swap, terminal rendering, amendments, offline enforcement, adapter,
  emitter, view models, GUI screen/failure states, and incomplete-run handling.
- Direct reproductions confirmed B-6, B-7, and A-5 against the reviewed tip.
- `git diff --check 9c69bb0...HEAD` passed.
- No fresh full-suite run was attempted. The handoff's eight reported green
  1,374-test passes are not counted as this review's independent verification.
- The R3 correction is accepted: the disclosed probe recorded **zero network
  attempts across 75 runs**. Its 84 events were the exact CPython
  `platform._syscmd_ver` process spawn permitted by D-30. Criterion 6 is not
  filed as open, and this review withdraws the prior uncertainty.
- The handoff's non-claims remain non-claims: criterion 4 is not met, no
  mouse-driven GUI acceptance was performed, the package double-failure branch
  remains uncovered, and the 3,600-second per-file timeout ruling remains
  Alex's open decision.

## Second fix-round merge condition

Fix A-5, B-6, and B-7 with fail-before coverage at the disk and durable-record
boundaries described above, refresh the verification evidence, and request the
next fix-round review.

---

# Codex fix-round review — DocIQ Sprint 2 (merge gate)

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-2`
**Reviewed commit:** `a309b15`
**Fix-round handoff:** `docs/codex_reviews/sprint-2_2026-08-04_claude_r2.md`
**Review date:** 2026-08-04
**Gate:** D-10, Codex review #2, fix round

## Fix-round verdict

**NOT PASSED — another fix round is required. Two A findings and two B
findings.**

The five original A/B findings are fixed at the sites named in the first
verdict: Stage 6 now refuses publication on every enumerated red gate, the
ready-marker reader fails closed, `PackageResult.missing` crosses the seam and
renders, package success/failure is visible, and a failed run offers working
back/retry actions. D-1 is closed.

The merge remains held because the swap still has an ordinary path that can
silently publish stale files, and the persistent log of a Stage-6 refusal drops
the assignment, reconciliation, and failing accounting detail that the
in-memory outcome preserves. In addition, the new `REFUSED` status is rendered
to the operator as `CANCELLED`, and a failed Path-A build can leave a current
partial package on disk while the GUI assures the operator that any package
there is from an earlier build.

## B-4 — A failed directory removal can still publish a mixed set

**Severity class:** B — evidentiary-integrity; stale package exhibits can remain
beside current ones after the recovery marker is deleted.

**Locations:** `src/dociq/emit/paths.py:501-520`

The new retry discipline is applied to file `unlink()` and staged-file moves,
but a superseded directory is still removed with
`shutil.rmtree(path, ignore_errors=True)`. A failure is therefore silently
absorbed. The function records the directory as removed, moves the new files
into the surviving directory, deletes the readiness marker, and returns
success. This contradicts the adjacent guarantee that every destructive swap
step is retried and roll-forward remains possible.

**Failure scenario:** an ordinary rerun replaces `upload_package/` while an
antivirus or backup agent holds one old package file open. `rmtree(...,
ignore_errors=True)` leaves that stale file or subtree behind. The swap then
moves the new package over it and removes `staging_ready.json`. The visible
package is now a mixture of two builds, with no pending marker left to disclose
or repair it. A direct reproduction in this review forced the directory
removal to fail: the stale file and current file were both visible,
`commit_staging()` returned `("upload_package",)`, and the marker was gone.

Directory removal must fail closed or be retried with errors propagated; the
marker must survive unless the named superseded directory is actually gone.
Add a fail-before test that denies removal of one file inside a superseded
directory and proves no mixed directory can be committed.

## B-5 — The persisted refusal log drops the diagnosis it claims to preserve

**Severity class:** B — evidentiary-integrity; the durable audit record silently
diverges from the complete attempted corpus and its Stage-6 diagnosis.

**Locations:** `src/dociq/pipeline.py:733-775`,
`src/dociq/emit/log.py:158-175`, `src/dociq/emit/log.py:240-268`,
`src/dociq/emit/log.py:382-389`

`_abort()` now accepts and returns the real Stage-6 `assignment`,
`reconciliation`, manifest, and accounting report. But its `build_log()` call
does not pass the available assignment or reconciliation, and `build_log()` has
no input for the failing accounting discrepancies. The fix-round test asserts
only the in-memory `PipelineOutcome`, not the quarantined
`processing_log.json` that remains after the process exits.

**Failure scenario:** a complete run assigns all identifiers and reconciles the
matter, then an accounting regression makes Stage 6 refuse publication. The
operator later opens `incomplete_run/processing_log.json`, the durable record
the handoff says preserves the diagnosis. In the direct reproduction here, the
outcome carried 19 assignments and three accounting discrepancies, while the
log serialized `doc_ids.assignments` as `[]` and `reconciliation` as `null`;
the individual gate discrepancies existed only in memory. The record therefore
silently omits facts the refused run actually established.

Pass the real assignment and reconciliation into `build_log()` and give the
quarantined log a serialized representation of the accounting discrepancies
that refused publication. Add an on-disk assertion, not only an outcome
assertion.

## A-3 — `REFUSED` is shown as `CANCELLED`

**Severity class:** A — real user-facing bug in ordinary internal use.

**Locations:** `src/dociq/runstate.py:120-128`,
`src/dociq/gui/view_models.py:294-300`, `src/dociq/emit/summary.py:202-205`,
`src/dociq/pipeline.py:778-795`

`RunTermination.headline()` recognizes only `COMPLETED` and `BLOCKED`; every
other noncomplete status falls through to `CANCELLED`. A-15 added `REFUSED` but
did not update that renderer. The GUI then adds a second false statement for
all noncomplete statuses: that its figures cover only what was read before the
run stopped. A refused run did not stop part-way; it read and identified the
complete corpus and rejected publication at its gate.

**Failure scenario:** a new emitter creates an unclassified output, so Stage 6
correctly refuses the set. `run_status.json` is machine-readable as
`terminal_status: "refused"`, but its headline says `RUN CANCELLED`; the
quarantined summary PDF repeats that label, and the GUI says the figures cover
only a partial read. The operator is told that somebody stopped the run rather
than that DocIQ rejected a complete set for an integrity failure. The direct
status-object reproduction printed exactly `RUN CANCELLED` for
`TerminalStatus.REFUSED`.

Render every terminal status explicitly, and give the summary view wording for
a complete-but-refused corpus rather than reusing the partial-run explanation.
Add assertions for the JSON headline, PDF/summary data, and GUI banner.

## A-4 — A failed package build can leave a current partial folder while the GUI says it is old

**Severity class:** A — real user-facing bug in ordinary internal use.

**Locations:** `src/dociq/emit/handoff.py:480-483`,
`src/dociq/emit/handoff.py:513-578`,
`src/dociq/gui/view_models.py:981-999`

`build_upload_package()` deletes the prior package, creates the final
`upload_package/` directory, and writes files into it one by one. Any later
copy, filtering, README, or validation exception leaves that current partial
directory in place. The new GUI failure state nevertheless assures the operator
that any package already on disk is from an earlier build.

**Failure scenario:** the text and manifests copy successfully, then antivirus
locks the README write. The GUI visibly reports the exception, but says any
package on disk is an earlier build. In the direct reproduction here,
`DIQ-1.txt` and `sources.json` from the failed current attempt remained in
`upload_package/`; there was no README and no completed validation. An operator
following the assurance can upload a partial current package as if it were the
last complete one.

Build Path A in a sibling staging directory and replace the visible package
only after all validation passes, or remove the partial directory on every
failure and report that cleanup result accurately. Add a fail-before test that
raises after at least one package file is written and inspects disk as well as
screen text.

## D-2 — A-15 is simultaneously “raised” and “applied,” and `HEAD` passes as an adopting commit

**Severity class:** D — process/documentation; nonblocking by itself.

**Locations:** `docs/contracts/amendments.md:1049-1056`,
`docs/contracts/amendments.toml:163-169`, `tools/check_amendments.py:171-176`

The prose register still says A-15 is `RAISED, NOT APPLIED`, while the TOML
registry says `applied`. Its `adopted_in` value is the symbolic name `HEAD`, so
the checker accepts whichever commit happens to be checked out rather than
preserving the immutable commit that adopted the amendment. The actual adopting
commit is `b1eac7e`.

**Failure scenario:** a reviewer follows the mandated read order and cannot
determine whether A-15 is adopted; meanwhile the structural check remains green
because `git cat-file -t HEAD` always names the current commit. Moving the
branch changes what the recorded “adopting commit” means without changing the
registry.

Update the prose disposition, record `b1eac7e`, and reject symbolic refs where
the registry requires a historical commit ID.

## Fix-round verification performed

- Fetched and checked out `build/sprint-2`; reviewed the clean remote tip at
  `a309b15` and read the fix-round handoff from the branch.
- **473 tests passed and 1 skipped** in focused and adjacent slices:
  publication gate, seam population, GUI failures, atomic emit, incomplete-run
  state, adapter, package emit, view models, GUI screen states, amendment checks,
  and the disclosed whole-pipeline offline probe.
- The offline probe passed in isolation. This review accepts the handoff's
  disclosure that its intermittent concurrent-load cause remains open; it does
  not recast that disclosure as closed.
- Direct reproductions confirmed all four A/B findings above against the
  reviewed tip.
- `git diff --check e02c292...HEAD` passed.
- No fresh full-suite run was attempted; the handoff reports about 20 minutes
  per pass and 15 consecutive green runs. Those reported runs are not counted as
  this review's independent verification.

## Fix-round merge condition

Fix A-3, A-4, B-4, and B-5 with fail-before coverage at the durable/user-visible
boundary described above, refresh the verification evidence, and request the
next fix-round review. D-2 should be batch-fixed and disclosed but does not
independently hold the merge.

---

# Original verdict — DocIQ Sprint 2 (merge gate)

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-2`
**Reviewed commit:** `292e7197`
**Base:** `main` at `e7fd4eb`
**Review date:** 2026-08-04
**Gate:** D-10, Codex review #2

## Verdict

**NOT PASSED — fix round required. Two A findings and three B findings.**

The submission contains substantial implementation and verification work, and
the focused suites exercised during this review passed. The merge remains held
because ordinary GUI actions can appear to do nothing or leave the operator in
an unrecoverable screen, selected documents missing from a Path-A package do
not cross the seam to the operator, an unreadable swap marker can publish a
mixed evidence set, and the checks described as Stage-6 gates do not actually
prevent a red set from replacing the last good deliverables.

Per the Alex-ratified calibration in the handoff, every A or B finding requires
a fix round and re-review. D-1 was nonblocking and was closed after the reviewed
tip by `56129c9`, before this verdict file was committed; it is retained below
as a finding against `292e7197` and marked with that disposition.

## B-1 — Stage-6 checks do not gate publication

**Severity class:** B — evidentiary-integrity; a red evidence set can silently
replace the last good deliverables.

**Locations:** `src/dociq/pipeline.py:1346-1358`

The pipeline computes `report_acc = accounting.check(result)` and builds the
manifest, but it never checks `report_acc.ok` or `man.unclassified` before
calling `mark_ready()` and `commit_staging()`. `PipelineOutcome.ok` reports the
red state only after publication has already happened. That is observation,
not gating, despite the Stage-6 heading and the handoff's claim that the set is
"gated there, marked, then swapped."

**Failure scenario:** a regression produces a document with a page-accounting
discrepancy, or a new Stage-5 emitter writes an artifact that the manifest does
not classify. Stage 6 detects the defect, then unconditionally marks the
staging set ready and replaces the prior complete output. The caller receives
`ok=False`, but the matter folder already contains the failed set and an
operator or downstream reader need not inspect that in-memory return value.

Publication must be refused when accounting is not OK or the manifest carries
an unclassified output. Add fail-before tests that force each gate red and
prove the prior output remains byte-for-byte untouched and no ready marker is
written.

## B-2 — An unreadable ready marker can publish a mixed evidence set

**Severity class:** B — evidentiary-integrity; stale exhibits can survive beside
new deliverables without a remaining recovery signal.

**Locations:** `src/dociq/emit/paths.py:126-140`,
`src/dociq/emit/paths.py:210-238`, `src/dociq/emit/paths.py:275-310`

The ready marker is written directly by `write_text_deterministic`; it is not
written to a temporary path and atomically renamed. If the process dies during
that write, the marker can exist with truncated JSON. `commit_staging()` catches
both `OSError` and `ValueError`, silently substitutes `superseded = ()`, moves
the staged files, deletes the marker, and calls the result recoverable. Nothing
then identifies the stale files it deliberately left behind.

**Failure scenario:** run 1 publishes 100 clean-text files. Run 2 legitimately
shrinks the output to 80, finishes staging, and crashes while writing the ready
marker. The next run sees the marker, cannot parse it, moves run 2's 80 files
over the destination without removing run 1's other 20, and deletes the marker.
If the newly starting run then cancels or fails, the matter folder remains a
mixed 100-file set with run 2's manifest and no pending-swap marker. Those 20
stale exhibits can be read or uploaded as current evidence.

An unreadable marker must fail closed, or recovery must reconstruct and verify
the exact superseded set before moving anything. The marker should also use a
temp-write-plus-replace protocol. Add a crash/truncated-marker test with a
shrinking rerun and prove no mixed set can become the visible folder.

## B-3 — Missing Path-A documents are still dropped at the seam

**Severity class:** B — evidentiary-integrity; a scoped package can omit a
selected document without telling the operator.

**Locations:** `src/dociq/gui/pipeline.py:427-456`,
`src/dociq/adapter.py:793-818`, `tests/test_adapter.py:1038-1067`

`PackageResult` now has the `missing` field required by rehearsal finding B5,
and `build_upload_package()` returns the missing Doc IDs. However,
`RealPipeline.build_package()` constructs `PackageResult` without
`missing=package.missing`, so the field defaults to an empty tuple. The adapter
copies the real value only into `last_package_missing`, a private attribute the
GUI never reads. The focused test asserts that private holding attribute rather
than the seam result, so it passes while the user-visible path stays wrong.

**Failure scenario:** the operator selects `LI-00001` and `LI-99999`; the first
clean-text file exists and the second is absent. Emit builds a one-document
package and correctly reports `("LI-99999",)` as missing. The adapter returns a
`PackageResult` saying `doc_count == 1` and `missing == ()`. Any consumer of the
declared seam sees no missing document, and the package's scope can still claim
the operator-selected set.

Propagate `missing=package.missing` into `PackageResult`, remove the private
holding pattern, and assert the returned seam record and rendered GUI state.

## A-1 — “Build the upload package” has no observable success or failure

**Severity class:** A — real user-facing bug in ordinary internal use.

**Locations:** `src/dociq/gui/main_window.py:382-402`,
`src/dociq/gui/screens.py:1082-1234`

`MainWindow._build_package()` discards the `PackageResult` returned by the
pipeline and repaints the same handoff view. A successful build shows no output
path, document count, file count, size, completion message, or missing-document
warning. A failed build is handled only with `print()`. The shipped GUI is a
windowed executable, so there is no operator-visible console on which to rely.

**Failure scenario:** an operator chooses a date scope and clicks “Build the
upload package.” On success, the screen is unchanged and gives no indication
that a folder was written or where it is. On an ordinary failure such as an
antivirus lock on `sources.json`, the screen is equally unchanged because the
exception text is printed off-screen. The user cannot distinguish success,
failure, or an ignored click and may upload an earlier package left on disk.

Retain the returned `PackageResult` and render an explicit success state with
the actual path/counts/size/scope and any missing Doc IDs. Render exceptions as
an actionable error on the same screen. Add GUI tests for both outcomes.

## A-2 — A failed run leaves the GUI on a dead-end progress screen

**Severity class:** A — real user-facing bug in ordinary internal use.

**Locations:** `src/dociq/gui/main_window.py:247-264`,
`src/dociq/gui/main_window.py:313-319`, `src/dociq/gui/screens.py:372-455`

When `_RunWorker.start()` catches an exception, the failure signal appends one
flagged row to `ProgressScreen` and the worker thread quits. That screen offers
only a Cancel button. Once the thread has stopped, Cancel has nothing to cancel,
and there is no back, retry, or new-run action.

**Failure scenario:** a run encounters an unreadable stored Bates pattern, an
emit error, a full disk, or another exception correctly raised by the pipeline.
The operator sees “Run failed” in the progress list, but cannot return to setup
or retry. The only recovery is closing and reopening DocIQ.

Transition to an explicit failed state that preserves the error and provides a
working return-to-setup/retry action. Disable or replace Cancel after settlement
and add a GUI test driven through a pipeline implementation that raises.

## D-1 — A-14 is referenced as an amendment but is absent from the amendment register

**Severity class:** D — process/documentation; nonblocking by itself.

**Post-review disposition:** **CLOSED** by `56129c9` — A-14 was added to the
prose register, a machine-readable amendment registry was added, and the suite
now checks prose/registry completeness and claimed wiring.

**Locations:** `docs/codex_reviews/sprint-2_2026-08-04_claude.md:36`,
`docs/contracts/amendments.md:831-949`

The handoff's required read list directs the reviewer to A-14, and code and
verification artifacts consistently refer to A-14 as the seam amendment that
carried Bates confirmation. `docs/contracts/amendments.md` contains A-11,
A-11b, A-12, A-13, and their applied section, but no A-14 entry or applied
record. This is the same raised-versus-adopted bookkeeping gap the handoff asks
how to prevent.

Record A-14 with its raised shape, actual adopted shape, applying commit, and
status. Structurally, maintain a machine-readable amendment registry containing
ID, status, adopting commit, and required seam/tests, and fail CI when source or
review documents reference an absent or unapplied amendment.

## What the review accepts

- D-29 states criterion 4 as not met and distinguishes the 92.130% projection
  from the last measured full-corpus result. This review does not refile that
  ruled limitation.
- The A-14 Bates confirmation implementation correctly distinguishes operator
  confirmation, operator decline, unattended operation, and cancellation. The
  dedicated Bates slice passed.
- The package emitter itself filters subset manifests, refuses a fallback to
  whole-matter manifests, applies the sanctioned-file check, and computes
  missing Doc IDs. B-3 is the subsequent seam projection, not the emitter.
- Staging before publication materially narrows the interrupted-emit window.
  B-2 concerns the corrupt-marker recovery branch, and B-1 concerns the absent
  red-gate publication boundary; neither withdraws the value of staging.
- The disclosed limitations about browser acceptance, mouse-driven GUI testing,
  performance, Bates OCR coverage, and determinism scope are stated plainly.

## Verification performed

- Fetched and checked out `build/sprint-2`; the final reviewed remote tip was
  `292e7197` and the worktree was clean.
- Followed the handoff's mandated read order through D-20–D-29, A-11–A-13 and
  the absent A-14, the taxonomy, verification records, core code, packaging,
  and relevant tests.
- **511 targeted tests passed**:
  - adapter, emitter, view-model, and GUI screen-state slice: 302;
  - Bates and Bates-confirmation slice: 153;
  - pipeline, emit-atomicity, and verification slice: 56.
- A complete `python -m pytest -q` run was attempted with a six-minute cap. It
  emitted no failure output but did not complete before termination, so this
  review does **not** count that fresh full-suite run as green.
- `git diff --check e7fd4eb...HEAD` passed.
- Repository-wide `compileall` traversed `.venv` and encountered the installed
  Python-2 compatibility file `olefile/olefile2.py`; that is not project source
  and is not filed as a finding.

## Merge condition

Fix A-1, A-2, and B-1 through B-3; add fail-before coverage for each corrected
path; refresh the verification evidence; and request a fix-round review. D-1
is already closed by `56129c9` and does not independently require another
round.
