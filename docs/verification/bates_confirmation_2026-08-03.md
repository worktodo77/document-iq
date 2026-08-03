# §4 Stage 3's operator confirmation — closing rehearsal finding A4

**Branch:** `build/s2-bates-ui` (off `build/sprint-2` @ `3a44f2e`) · **Date:** 2026-08-03

What was measured, not what was expected. Section 7 names what nothing here
supports, and section 6 names the two claims elsewhere in the repo that this
work makes false and that I was not permitted to edit.

---

## 1. The finding, restated in one paragraph

§4 Stage 3 requires a detected Bates format to be confirmed **with the
operator** on first detection. That confirmation was never built. The seam had
no callback, so `dociq/adapter.py` passed `auto_confirm_bates=False`,
`_bates_decision` returned `PENDING`, and `apply_bates_reported` returned every
document unchanged. **Through the shipped GUI a Bates-stamped production
received zero locators.** Acceptance criterion 4's 597/648 = 92.130% was
obtained by `tools/bates_acceptance.py`, which constructs
`BatesDecision(status=CONFIRMED, …)` in Python — a state no button in the
product could produce. Bates is a headline feature and it did nothing for any
user of the application.

## 2. What was built

| # | item | file |
|---|---|---|
| 1 | `PipelineOptions.confirm_bates` — Stage 3 asks | `src/dociq/pipeline.py` |
| 2 | CONFIRMED / REJECTED / abort, each recorded distinctly | `src/dociq/pipeline.py` |
| 3 | `RunAborted` — the way out of a callback the pipeline is blocked in | `src/dociq/runstate.py` |
| 4 | Stage 3 abort takes the *ordinary* cancellation path (`_abort`) | `src/dociq/pipeline.py` |
| 5 | `RealPipeline.run(..., confirm_bates=None)` + proposal translation | `src/dociq/adapter.py` |
| 6 | `stored_bates_pattern()` — §4's "confirmed once per document set" | `src/dociq/adapter.py` |
| 7 | `BatesConfirmScreen` — the §4 Stage-3 screen | `src/dociq/gui/screens.py` |
| 8 | worker↔GUI confirmation round trip | `src/dociq/gui/main_window.py` |
| 9 | `MockPipeline.run` carries the parameter | `src/dociq/gui/mock_pipeline.py` |
| 10 | D-28's prefix census reaches the prompt, computed once per run | `src/dociq/pipeline.py`, `src/dociq/adapter.py` |

Nothing in `src/dociq/gui/pipeline.py` or `src/dociq/contracts.py` was changed.
A-14 was applied as written and was sufficient; **no seam amendment is
requested**.

### 2.1 Three outcomes, not two

The seam's `BatesConfirm` is `Callable[[BatesProposal], bool]` (unchanged, and
what the GUI implements). The pipeline-side option is
`Callable[[BatesProposal, tuple[str, ...]], bool]` — the second argument is
D-28's prefix census; see §5.4. Either way the two bools are not the whole
answer space:

* **`True`** — the OPERATOR confirmed. Warning: *"Bates format … was CONFIRMED
  BY THE OPERATOR (`username`) at §4 Stage 3 of this run, on N% page
  coverage …"*. `decided_by = "operator (username)"`.
* **`False`** — the operator DECLINED. Status `REJECTED`, not `PENDING` and not
  `None`. The warning says in terms *"This matter is NOT unstamped — the stamps
  are on the pages and were read; they were ruled not to be this production's
  format."* An unstamped production and a stamped one whose format was declined
  are different facts about the record, and the log can now tell them apart.
  `_bates_note` — which reaches `run_summary.pdf` — carries a separate sentence
  for it rather than reusing PENDING's "the operator has not confirmed it".
* **`confirm_bates=None`** — *nobody was asked*. Unchanged behaviour: the run
  falls through to `auto_confirm_bates`, and a headless confirmation is recorded
  as a MACHINE confirmation exactly as before. A machine-confirmed pattern and
  an expert-confirmed one are not the same evidentiary object and the warnings
  keep them apart.

A rejection does **not** persist a pattern (`pipeline.py`'s existing `effective`
block already handled this; `test_a_declined_format_is_not_stored_for_the_next_run`
now holds it there).

### 2.2 The abort is not a ruling

`bool` has no value meaning "the operator walked away", and inventing one would
write a decision into the log that nobody made. `RunAborted` (new, in
`dociq/runstate.py` — the module that owns how a run ENDS, and one the GUI is
already permitted to import) is raised out of the callback.
`dociq.pipeline.run` catches it at the Stage-3 call site and routes to the
**existing** `_abort` path: nothing published, the previous run's deliverables
untouched, `incomplete_run/` written, `TerminalStatus.CANCELLED`. A second
publication rule would have been a second chance to get publication wrong
(Codex B-1).

### 2.3 What the screen shows, and why

An operator cannot confirm a regex. `BatesConfirmScreen` leads with **a real
locator read off a real page** (`proposal.samples[0]`, never a rendering of the
pattern) at 20 pt in the mono face, then documents matched, pages matched and
page coverage as three figures, then the multi-series disclosure, then the
actions. A proposal that arrives with no example **disables the forward
action** — confirming a pattern sight unseen is the state this screen exists to
end.

When `alternatives` is non-empty the screen says, in the warn colour, directly
above the buttons, that the matter carries more than one stamp series, lists
them, and states that **D-28 therefore refuses prefix repair anywhere in this
matter** and that every mismatch is flagged instead. The operator sees the
condition rather than having it decided for them. That sentence is stated as
fact, which is precisely why the field behind it had to be D-28's own census and
not the detector's runner-up shapes — §5.4, and the defect a real production
caught.

One forward action, named for its outcome (D-16): **"Use this Bates format"**.
"Do not use it" is a ruling, not a variant of it; "Stop this run" is not a
ruling at all.

### 2.4 The thread crossing — the hard part

The run is on a worker thread and the pipeline **blocks inside the callback**.

* `_RunWorker.confirm_bates` runs on the WORKER thread. It emits
  `bates_asked`, an automatic (therefore queued) cross-thread connection, so
  every widget touch happens on the GUI thread. The worker touches no widget.
* It then waits on a `threading.Event`, polled at 20 ms.
* `answer()` (GUI thread) sets the event. `cancel()` (GUI thread) sets only the
  cancelled flag, and the **poll** is what sees it.

Each exit has exactly one wake mechanism and both are exercised by tests. An
earlier draft had `cancel()` set the event too; that made the poll's check
unreachable, and I caught it because the fail-before for the cancel path
**did not go red** when I broke that branch. An untested branch in a
confirmation round-trip is where the deadlock lives, so the second mechanism was
removed rather than documented.

**A screen swap, not a modal.** `QDialog.exec()` spins a nested event loop, so
the window's close event, the worker's `finished` and the prompt would be
interleaved inside one another. The stack already existed; no event loop is
re-entered anywhere in this change.

`bates_settled` always fires (a `finally`), including on the abort path, so the
prompt is never left standing over a run that is unwinding — and it is guarded
on the current index so a late settle cannot pull an operator off the summary.

### 2.5 §4's "confirmed once per document set"

`config_from()` (frozen) builds a `RunConfig` from the setup screen alone, which
carries no `bates_pattern`. Without more, **every re-run of a matter would
re-ask the same operator the same question** — and a tool that re-asks a ruling
it was already given teaches the operator to click past it. That is a second
half of the same §4 sentence, so it is fixed here rather than deferred:
`adapter.stored_bates_pattern()` reads the previous complete run's
`processing_log.json` from the output root (the same folder and the same
precedent as `PipelineOptions.previous_ledger`). Every read failure returns
`None` and re-asks; a pattern that is present but unreadable still stops the run
in `_bates_decision`, unchanged.

---

## 3. Measured

### 3.1 Suite

`PYTHONPATH=src python -m pytest -q` — **1,131 tests**, green on every run.
**Ten full-suite runs on the final source tree**, byte-identical output on
every one, plus four on each of the two earlier commits as they landed.

The threading round trip, the cancel-during-prompt path and the multi-series
disclosure were then run **30 times** on their own (`-k "thread or cancel or
stopping or multi_series"`) — 30/30, byte-identical output lines, no hang, no
flake. An earlier 30× of the same subset was run **while a full-corpus client
run was competing for the same cores**, which is the condition a scheduling
race would show up under; also 30/30.

### 3.2 Fail-before — every one watched go RED

| break | tests that went red |
|---|---|
| `confirm_bates=None` in the adapter (the shipped state) | `..._operator_confirming_writes_locators`, `..._proposal_carries_what_an_operator_can_judge`, `..._declining_is_a_decision_not_an_absence`, `..._confirmed_format_is_stored_for_the_next_run` |
| `stored_bates_pattern` removed | `..._a_stored_confirmation_does_not_ask_again` |
| abort collapsed into `return False` | `..._cancelling_while_the_prompt_is_open_aborts_rather_than_declining`, `..._stopping_from_the_prompt_screen_aborts` |
| `bates_asked` left unconnected | both `..._confirmation_crosses_the_thread_boundary` cases, both cancel cases, `..._prompt_names_a_multi_series_production` |
| `except RunAborted` stubbed to an unraisable class | `..._aborting_at_the_confirmation_publishes_nothing` |
| `_bates_note`'s REJECTED branch disabled | `..._summary_distinguishes_declined_from_not_yet_confirmed` |

The first row is the finding itself, reproduced on demand.

One fail-before **refused to go red**, and that was the most useful result in
the package: breaking the in-loop cancellation check changed nothing, because
`cancel()` was also setting the event. The branch was dead. See §2.4 — the fix
was to remove the second wake mechanism, not to write a test for an unreachable
line.

### 3.3 A real Bates-stamped production, through the GUI path

**Corpus:** the client MNFV production at
`…\20240529\20240529\Supplemental` — 10 files, 165 MB, 10 documents, **369
pages**. A deliberately scoped subset of the matter, chosen because it is a
single self-contained production volume. Read-only; nothing was copied, and no
document text appears in this note or anywhere in the repository.

**Driven through `dociq.adapter.RealPipeline.run` — the same call the window's
worker thread makes.** The only difference from a mouse-driven run is that the
`confirm_bates` callback is a lambda instead of three buttons.

| run | `confirm_bates` | OCR | pages | pages with a locator |
|---|---|---|---|---|
| 1 | `None` (**the shipped state**) | on | 369 | **0 — 0.000%** |
| 2 | `None` (**the shipped state**) | off | 369 | **0 — 0.000%** |
| 3 | operator confirms | off | 369 | **328 — 88.889%** |

Runs 2 and 3 are the matched pair: identical corpus, identical configuration,
one difference. **0.000% → 88.889%.**

Run 1 is the same result with OCR on, so the finding is not an artefact of
turning OCR off. Runs 1 and 2 both said, in the warnings, that a format had been
detected and was *not applied* — on 50% of pages with OCR on and 88% with it
off. **DocIQ could see the production's Bates stamps the whole time and had no
way to be told to use them.**

What the operator would have been shown in run 3, verbatim from the
`BatesProposal` that crossed the seam:

* example locator **`MNFV 02636`** — read off a page, not rendered from a
  pattern
* **9 documents · 326 pages · 88% of pages sampled**
* asked **exactly once**

and the run recorded

> Bates format MNFV 00001 was CONFIRMED BY THE OPERATOR (Alex) at §4 Stage 3 of
> this run, on 88% page coverage (326 of 369 pages across 9 document(s)).

with `bates_pattern` persisted for the next run of the matter.

**This corpus is what caught the `alternatives` defect** (§5.4). Run 3's first
version handed the screen `('retained 90095 49 00001', 'Check 0001')` — the
detector's runner-up shapes — and the screen would have told the operator, in
the warn colour, that this single-series production carried three stamp series
and that D-28 therefore refused prefix repair on it. Both halves false. No
fixture would have produced that pair; a real production did.

Run 3 was **re-run on the corrected code, over the same corpus**: same 369
pages, same 328 locators (88.889%), same example, and `alternatives=()` — the
census correctly reports the MNFV production as single-series. That is the
number this note stands behind.

---

## 4. The class, not the repro

`auto_confirm_bates` was one member of a class: **a `PipelineOptions` field that
stands in for a ruling the operator was supposed to make.**
`test_no_pipeline_option_silently_decides_for_the_operator` enumerates **every**
field of `PipelineOptions` by name with the reason it is safe to default, and
fails when the dataclass grows or loses one. Adding a field that stands in for
an operator ruling now breaks a test instead of shipping another silent default.

The enumeration's answer to the brief's question — *is there any OTHER
Stage-3-style decision the GUI silently defaults instead of asking?* — is **no,
with one qualification**: `master_index_path`, `profiles` and the three
`write_*` switches are operator-facing but are chosen on the setup screen or are
deliberate constants, and `previous_ledger` defaults to the output root's own
ledger, which is the case D-04(b) is about. The one field that stood in for a
ruling was `auto_confirm_bates`, and it now yields to `confirm_bates`.

`test_every_pipeline_implementation_forwards_the_confirmation` asserts by
reflection that `RealPipeline`, `MockPipeline` and the `PipelineAPI` Protocol
all carry `confirm_bates` with a `None` default — a stand-in that dropped it
would reinstate the finding in a different file, and would surface only as "run
failed".

---

## 5. Defects found on the way

1. **`_bates_note` conflated PENDING with a refusal.** It reached
   `run_summary.pdf`. Fixed in the same package.
2. **§4's "confirmed once per document set" was false through the GUI** —
   §2.5. Not part of A4 as written; fixed rather than recorded.
3. **A dead cancellation branch in my own first draft** — §2.4. Found because a
   fail-before refused to go red, not because a test failed.
4. **§5.4 below — the one that would have reached an operator.**

### 5.4 `alternatives` was the wrong census, and the screen asserts it as fact

The seam says `BatesProposal.alternatives` means "other prefixes seen in the
same matter", and that a non-empty value means the production is multi-series —
the condition D-28 refuses prefix repair on. The screen states that consequence
**as fact**, in the warn colour, immediately above the button.

The adapter's first version filled it from `identify.bates.BatesProposal.
alternatives`, which is `ranked[1:4]` **with no threshold applied at all**. On
the client production that is `('retained 90095 49 00001', 'Check 0001')` — two
stray lines. The screen would have made two false statements about the record at
the moment the operator was asked to rule on it.

The only function that answers D-28's question is
`identify.bates.matter_prefixes`, which applies the same two bars a proposal must
clear. Stage 3 now passes that census to `confirm_bates` as a second argument
and shares the **same** computed census with `apply_bates_reported`'s repair
gate — two independent censuses would be two answers to one question about the
record, and nothing would make them agree. An unstamped matter computes none of
it. Both directions are held by tests: a stray line must not reach the screen,
and two real series must.

I did not reason my way to this. **The client corpus printed it.**

---

## 6. Claims elsewhere that this work makes FALSE — I could not edit them

I was instructed not to touch documentation under `docs/` other than this note.
Both of the following now assert a gap that no longer exists and **must be
corrected before the next hand-off**:

1. `docs/decisions/decision_register.md`, row **D-29**, final sentence:
   > ⚠️ **And see A4 below: through the shipped GUI the figure is 0%, because
   > nothing confirms a Bates format.**

   This is now false. Suggested replacement: *"A4 is closed on
   `build/s2-bates-ui`: §4 Stage 3's confirmation is built, and a stamped
   production confirmed through the GUI writes locators (see
   `docs/verification/bates_confirmation_2026-08-03.md`). The 92.130% figure
   remains a projection and remains OCR-limited — A4's closure does not change
   it."*

2. `docs/verification/track_d_sprint2_2026-08-01.md` §5.1 ("Bates confirmation
   has no callback"), which describes the stop-the-line in the present tense and
   proposes the shape A-14 subsequently adopted. It needs a closure line
   pointing here; its body is now history, not state.

I made **no** claim-withdrawal edits in `src/`: the frozen seam's `BatesProposal`
docstring already narrates the gap in the past tense as the amendment's
rationale, which remains true.

---

## 7. What is NOT proven

* **Nobody has driven this screen with a mouse.** Every GUI assertion here is
  from `QT_QPA_PLATFORM=offscreen`. Layout under a real window manager, at the
  minimum window size, with a long multi-series list, is unverified.
* **Acceptance criterion 4's headline is untouched.** A4 was that the number was
  unreachable, not that it was wrong. The OCR-page limitation D-29 records is
  exactly where it was.
* **One matter, one operator answer per run.** The pipeline asks at most once,
  because `propose_format` returns one proposal. A production whose *second*
  series the operator would rather confirm cannot be told so — the screen names
  the alternatives, and offers no way to pick one. That is a genuine limitation
  of the seam's `BatesProposal`, not of this implementation, and it is not a
  regression: before this change no series could be confirmed at all.
* **The 20 ms poll is not a measured figure.** It is chosen below the threshold
  at which a click reads as delayed. Nothing timed it.
* **`stored_bates_pattern` is not proven against a log written by an older
  build.** It reads defensively and returns `None` on anything it does not
  recognise, which re-asks; that direction is safe but untested against a real
  older artifact.
* **The client measurement was made with OCR off.** Runs 2 and 3 are a matched
  pair and the 0.000% → 88.889% difference is therefore attributable to the one
  variable — but 88.889% is *not* an accuracy figure comparable to D-29's, and
  must not be quoted as one. It is the share of pages that came out of a GUI-path
  run carrying a locator, on this subset, with OCR off. Run 1 shows the *before*
  number is 0.000% with OCR on as well; the *after* number with OCR on was
  started twice and killed by a wall-clock limit both times, at ~48 minutes a
  run. **Nobody has measured the after-number on this corpus with OCR on.**
* **The declined and aborted paths were never exercised on client data.** Both
  are covered end-to-end on fixtures through the real pipeline; neither has been
  run against a real production.
