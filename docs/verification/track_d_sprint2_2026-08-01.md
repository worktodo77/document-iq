# Track D — the real pipeline under the GUI

**Branch:** `build/s2-track-d` · **Contract:** 1.6.0, unamended · **Date:** 2026-08-01

What was measured, not what was expected. Every claim below names the check that
supports it, and the last section names the ones nothing supports.

---

## 1. What was built

| item | where | state |
|---|---|---|
| D-1 `profiles()` | `src/dociq/adapter.py` | real §6 library (D-05), real rule counts, never empty |
| D-2 `preview_folder()` | `src/dociq/adapter.py` | real counts via the run's own traversal; estimate from the one measured rate, or none |
| D-3 `run()` | `src/dociq/adapter.py` | real six-stage run, merged progress, honest cancellation |
| D-4 `ReductionPlan` | `src/dociq/adapter.py` | counted per-section savings, expert levers only |
| D-5 Bates confirmation | — | **STOP THE LINE**, see §5.1 |
| D-6 emit atomicity | `src/dociq/emit/paths.py`, `src/dociq/pipeline.py` | staged, gated, swapped, roll-forward |

`get_pipeline()` returns `dociq.adapter.RealPipeline`. `set_pipeline()` still
installs the mock, and `tests/test_gui_states.py` / `tests/test_view_models.py`
still run against it — a seam with one implementation is an interface nobody has
tested.

**The adapter is not in `dociq/gui/`.** It cannot be. `tests/test_import_graph.py`
scans every file under `src/dociq/gui/` for an import of `ingest`, `identify`,
`docid`, `profiles`, `emit` or `verify`, and the adapter needs all six. Putting it
at `gui/real_pipeline.py` would have meant exempting a file from the check that
keeps the GUI honest. It sits on the pipeline side instead and imports the seam's
presentation records, which is the direction the freeze permits; `get_pipeline()`
reaches it through a function-local import, so importing the whole GUI still pulls
in no pipeline module (that test still passes, unchanged).

---

## 2. What was measured

### Test runs

| check | result |
|---|---|
| full suite | **8 consecutive runs, 0 failures** (see §2.4 for counts) |
| `tests/test_adapter.py` + `tests/test_emit_atomicity.py` | **30 consecutive runs, 0 failures** — the 30 is because these touch temp directories, hashing, ordering and a thread pool |
| `python -m dociq.selftest` | **3 runs, exit 0, 66 checks** each, including its own 8-run × 30-seed determinism harness |
| `tests/test_import_graph.py` | passing, unchanged |

### Fail-before, watched red

Nothing below is asserted from a green run alone.

| what | how it was watched | caught by |
|---|---|---|
| emit atomicity | Stage 5 reverted to the Sprint-1 write path (purge, then write into the destination) | 4 of 4 crash points + the staging-discard test went red |
| stage progress removed | `on_stage` unwired | `test_the_last_four_stages_report_themselves` |
| `NO_PROFILE` leaks into `RunConfig` | sentinel passed straight to `config_from` | `test_the_no_profile_sentinel_never_reaches_the_run_identity` |
| library returned verbatim | `NO_PROFILE` not appended | 5 profile tests |
| invented automatic lever | mock's 14% row re-added | 3 plan tests |
| token figures recomputed | `structural_tokens + 1` | `test_the_gui_is_handed_the_pipelines_own_figures` |
| progress without page counts | status reduced to "reading" | `test_progress_speaks_plain_language_about_pages` |
| preview estimating outside its envelope | the shape gate removed | 2 of the 4 envelope cases |
| figures handed over by an unpublished run | all four `if outcome.published` guards removed | `test_the_adapter_refuses_figures_from_an_unpublished_run` |

The last one is worth a sentence. Removing those guards passed the whole suite at
first, because a cancelled walk returns before Stage 6 and therefore carries no
token estimate and no reconciliation to withhold — the guards were real but
unobservable, which is the shape of defence that rots. The test now constructs an
outcome that claims to have published nothing while carrying a full result, and
that mutation goes red.

### Criterion 7 under staging

The staging directory is inside the output root, so it differs between two
destinations exactly as `output_root` does — and `output_root` is
identity-excluded precisely because the destination must not change the evidence.
Re-proven rather than assumed:

* two runs, two destinations → one `corpus_sha256`, one `run_identity_sha256`,
  one `log_content_sha256`, identical `deterministic` **and** `adjacent` maps,
  `manifest.compare` empty, byte-identical `clean_text/`, `sources.json`,
  `document_index.csv`, `upload_package/`, `doc_ids_issued.json`;
* a separate scan asserts no shipped artifact contains the strings `.dociq` or
  `staging` at all — which catches a leak into `run_summary.pdf` or the workbook,
  neither of which a two-destination hash comparison can see;
* the self-test's own 8-run × 30-seed determinism harness is green after the
  change.

### Timing

The swap is a sequence of same-volume `os.replace` calls. On the fixture corpus
(17 documents, 25 pages, 19 deterministic + 21 adjacent files) the emit stage's
recorded wall clock is unchanged within run-to-run noise; the swap itself is
metadata operations on ~40 files. **Not measured on the real corpus** — see §6.

---

## 3. Defects found and fixed in this package

1. **The roll-forward disclosure was silent in the commonest case.** The "a
   previous run's swap was completed for you" note keyed on the list of files the
   recovery *removed*, which is empty for a first run into an empty folder — the
   most likely interrupted swap there is. Found by a test that asserted the
   disclosure, not by review. Now keyed on whether a swap was pending.
2. **`upload_package` was exempt from the stale-deliverable list** on the
   reasoning that the emitter rebuilds it from scratch. True of the old write
   path; false the moment the rebuild happens in staging. A re-run that produced
   fewer documents would have left the previous run's `.txt` files in the folder
   an operator uploads whole. Fixed, and the withdrawn reasoning is recorded on
   `_STALE_PATTERNS` rather than deleted.
3. **The walker's progress hook could not support plain-language status.** It
   reported files and failures, never pages, so the only honest status line was
   "reading — 41 of 60 files" and the docstring's "read 148 pages" would have been
   invented. Added `pages` / `ocr_pages`, counted from the records as they
   complete, and recomputed from the final records at the closing tick — the
   serial retry replaces a document's records wholesale, so the running totals can
   otherwise describe pages that no longer exist.
4. **A folder preview would have been a second traversal.** `_iter_files` was
   private, so the obvious preview implementation is its own `rglob` — which
   disagrees with the run exactly where it matters (junction loops, unlistable
   directories, unstattable entries). Added `walker.list_files`, so there is one
   definition of what is in a folder.
5. **The preview counted DocIQ's own run state as documents.** `scan()` skips
   anything under `.dociq/`; the new `list_files` did not. An operator who puts
   the output folder inside the matter folder — which the setup screen permits,
   and which is an obvious thing to do — would have seen a previous run's resume
   journal and staged text files counted in "N files". Found by reading `scan`
   against the preview rather than by a failing test. Fixed by extracting one
   shared predicate, `walker.is_run_state`, so the two cannot drift; fail-before
   watched red (`4 == 2`, with `.jsonl` and `.txt` in the extension breakdown).
6. **`_purge_stale_deliverables` had to be split, and the B-1 guard kept.** The
   enumeration now happens before the log is written and the deletion at the swap.
   The required, validated `termination` argument Codex credited moved onto the
   enumerator, and the readiness marker is a third independent defence: the swap
   deletes nothing without one, and only a run that reached the end of Stage 6
   writes one. `tests/test_incomplete_runs.py` asserts all three.

---

## 4. Judgment calls made against the brief, with reasoning

**No built-in format profiles are seeded into the library.** The brief asked for
built-ins seeded as a default. What ships is one built-in — the no-profile choice
— and `profiles()` never returns an empty tuple. The reason is not caution: §6
step 4 requires a profile to be saved with the expert's Windows username and
timestamp, and `SectionRule.validate()` *refuses* a DROP rule that carries no note
recording why the section is omitted and who approved it. A shipped "MODEC monthly
progress report" profile with photo logs pre-marked DROP either fails validation
or ships an omission decision attributed to nobody — inside a product whose entire
Principle-3 argument is that the expert's omissions are separable from the tool's.
Writing such a file into `%APPDATA%` on first launch would also be a silent side
effect on the operator's machine. Section headers could have been mined from the
real corpus instead, but that puts client-derived content in the repo. **Reversible
in one commit if Alex rules otherwise**, and the honest path is the §6 profiling
workflow producing a real, attributed profile.

**No automatic lever in the waterfall.** `LEVER_AUTOMATIC` is for savings the tool
makes mechanically. DocIQ detects exact-hash duplicates (§4 Stage 1,
`walker.duplicate_groups`) and warns; it removes neither duplicates nor page
furniture. Every page of every duplicate copy is extracted, written to
`clean_text/` under its own Doc ID and counted in the accounting identity. A lever
claiming that saving would claim a reduction the deliverable does not contain, and
it would be *subtracted* from the figure the operator reads as the load. So the
row is absent, not zeroed with a footnote. The two ways out are a decision, not a
build choice: (a) implement exact-hash suppression — which changes page
accounting, Doc ID assignment and the §7 outputs, and is a Stage-1 design question;
or (b) withdraw the claim from the UI (see §5.5).

---

## 5. STOP THE LINE — seam and coordination items

Each is reported with the exact shape proposed. None has been applied locally.

### 5.1 Bates confirmation has no callback (D-5, the item the brief flagged)

§4 Stage 3 requires the detected Bates format to be confirmed **with the operator**
on first detection. `PipelineOptions.auto_confirm_bates` exists for headless paths
and records a warning when it fires. The seam has no way to ask, so the adapter
sets it `False` and a GUI run behaves as every unattended run does: the format is
detected, **not applied**, and the run says so in its warnings. That is the least
wrong interim — setting it `True` would have the machine confirm on the expert's
behalf and then record that it had done so.

Proposed addition to `dociq/gui/pipeline.py`:

```python
@dataclass(frozen=True, slots=True)
class BatesProposal:
    """A detected Bates format, offered for the operator's confirmation (§4
    Stage 3). Plain language only — the pattern itself never crosses the seam."""

    label: str            # "MNFV 000123" — the format as a human reads it
    coverage_pct: int     # share of pages it was found on
    sample_pages: tuple[str, ...] = ()   # a few rendered examples, verbatim

BatesConfirm = Callable[[BatesProposal], bool]
"""Asked ONCE per document set, on the GUI thread, before Stage 3b. True applies
the format; False leaves the production unstamped for this run. There is no
'ask me later': the identifier regime depends on the answer."""
```

and `PipelineAPI.run` gains `confirm_bates: BatesConfirm | None = None`. Track E
would need a modal; the run blocks until it returns, which is correct — the answer
changes the Doc IDs. If the callback is absent the current behaviour stands.
**Sprint-2 acceptance criterion 4 (Bates ≥99% on the MNFV production) cannot be
discharged through the GUI until this lands.**

### 5.2 `ProgressEvent` cannot express a stage

`ProgressEvent` counts files and the progress screen renders "N of M files", so
once the walk finishes the bar is legitimately full while Stages 3-6 run and only
the status line moves. The register measures those stages at 25.7 s against
2,848.5 s, so this is a small window on the real corpus and a visible one on a
small matter. Proposed:

```python
    stage: int = 0        # §4's stage number, 0 when not stage-aware
    stage_total: int = 0  # 6
```

Both default to 0, so nothing existing changes; a screen can then render "Step 5
of 6" as its own progress dimension rather than reading it out of `status`. The
pipeline side is already there — `dociq.pipeline.StageProgress`.

### 5.3 `FolderPreview.estimated_minutes` is a bare integer

Everywhere else in this seam a figure carries its provenance — `TokenEstimate`
does, and its docstring argues that a number that cannot say where it came from is
a claim the expert would have to defend without support. `estimated_minutes` is a
naked `int` whose only honest values are "the one measured rate applied to a
folder shaped like the one corpus we timed" and 0. Proposed:

```python
    estimate_basis: str = ""
    """How the estimate was obtained, in the pipeline's words. Empty when
    estimated_minutes is 0. Shown verbatim beside the figure."""
```

`dociq.adapter.MEASURED_BASIS` is the string that would go in it today.

### 5.4 No channel for "the profile library has a broken file"

A profile whose YAML will not parse is skipped by `profiles()` and recorded on
`RealPipeline.library_issues`, which nothing renders. It must not go into
`RunResult.warnings` — that is hashed content, and junk in `%APPDATA%` would then
change the corpus hash. `disclosure()` is reserved for saying a pipeline is not
real. An expert's saved ruling silently missing from the picker is a bad failure;
please route a channel for it.

### 5.5 A UI claim the build does not support (Track E's file — not edited)

`src/dociq/gui/widgets.py:511` renders the hint *"Removed mechanically — exact
duplicates and page furniture"*. DocIQ removes neither. With no automatic lever
the string is currently unreachable, which makes it latent rather than false — but
per "withdraw the claim, not just the code" it should go, or come back attached to
a real mechanism. §4 Stage 1's "duplicate detection (by hash)" **is** implemented;
duplicate *removal* was never specified and is listed in §11 non-goals only for
near-duplicates.

---

## 6. What could NOT be proven — read this part

1. **Nothing here ran against the real corpus.** Every measurement above is the
   fixture corpus (17 documents, 25 pages). The staging swap moves ~40 files here
   and would move ~19,000 on the D-12 corpus (`clean_text/` alone is one file per
   document, plus `upload_package/` copies of all of them). Same-volume moves are
   metadata operations, but **19,000 of them on a network-mounted matter folder is
   not something this package measured**, and the swap window scales with it.
2. **The swap is not atomic against a concurrent reader.** What changed is the
   size of the exposure window — from the whole of Stage 5, minutes and every OCR
   page of it, to a sequence of moves — plus a marker on disk and a roll-forward.
   A reader that opens the folder *during* the moves, or between a crash and the
   next run, can still see a mixture. Closing it entirely needs a published-set
   indirection that §8 Path B's fixed paths (`clean_text/`, `sources.json` at the
   matter root) currently forbid. Stated on `commit_staging` rather than implied.
3. **The crash tests inject exceptions; they do not kill the process.** A `SIGKILL`
   or a power loss mid-`os.replace` is not exercised. The roll-forward is designed
   for it and is idempotent, but "designed for it" is not "watched surviving it".
4. **`estimated_minutes` rests on one run, on one machine, with OCR off.** The
   register's only clean from-scratch full-corpus figure is 3,046.7 s for 2.6 GB
   with OCR disabled. OCR is ≈2.0–2.3× extraction on a corpus that was 2.6%
   scanned, and nothing knowable before the walk says how scanned a folder is — so
   the estimate is **optimistic by construction on scanned material**. The
   denominator is "2.6 GB" to two significant figures with binary/decimal
   unstated (≈7% before anything else). The envelope gate (≥80% of bytes in
   pdf/docx/pptx/doc, ≤26 GB) returns 0 outside it, which is the seam's documented
   "no estimate" — but *inside* it the number is still one measurement
   extrapolated linearly, and linear is assumed, not observed.
5. **The fixture corpus is too small to produce any estimate at all**, so the
   estimate path is proven by unit tests on `_minutes_for` and by nothing
   end-to-end.
6. **Cancellation is proven at file granularity, not mid-file.** The walker
   checks `cancelled()` between completion batches; a cancel pressed during a
   3,600-second OCR of one PDF waits for that document. Pre-existing, unchanged,
   and worth an operator-facing sentence Track E does not currently have.
7. **`RealPipeline` always enables OCR** (`ocr_enabled=True`). The GUI has no
   control for it and the seam has no field, so an operator cannot turn OCR off
   for a fast pass. Not raised as a stop-the-line item because nobody has asked
   for it; recorded because it is a decision made by default rather than ruled.
8. **The reduction plan's levers are per *section label*, and a section label is
   whatever the profile's rule chose to call it.** Two profiles that label the
   same section differently produce two levers over one corpus; one profile that
   reuses a label across sections merges them. Correct for the single-profile case
   the GUI supports today, unproven for the multi-profile library Stage 4 allows.
9. **`ProgressEvent.flagged` fires on the tick where a failure appears**, which
   means a failure that appears in the same batch as another is one flag, not two.
   Deliberate — a flag that stays on for 300 files marks nothing — but it is a
   choice, not a measurement.
10. **Two runs into one output folder are not safe, and the swap does not make
    them safer.** Run B's roll-forward would commit run A's staging, and B's
    `discard_staging` would delete A's staging while A was still writing into it.
    This was already broken before the change — two runs sharing a destination
    fought over the deliverables directly — so it is not a regression, but the
    staging directory is now a *shared* piece of run state and that is new. There
    is no lock. If concurrent runs are a real scenario, the marker file is the
    natural place to put one.
11. **A crash while an aborted run is recording itself still leaves a partial
    `incomplete_run/`.** The quarantine directory is written directly, not staged.
    It is a diagnostic rather than a deliverable and a later complete run purges
    it, so this was left alone — but the guarantee in §5's heading covers the §7
    deliverables, not this folder.
