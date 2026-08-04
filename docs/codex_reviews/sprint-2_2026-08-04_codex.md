# Codex review — DocIQ Sprint 2 (merge gate)

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
