# Track E — Sprint 2 verification note

**Branch:** `build/s2-track-e` · **Worktree:** `document-iq-wt/track-e`
**Date:** 2026-08-01
**Scope:** E-1 (the §6 profiling checklist), E-2 (the D-14 waterfall on real
figures, under D-21), E-3 (§8 / acceptance criterion 8 — Analyze in Claude,
Paths A and B).

Nothing in this note is a claim about the real corpus. Every figure on every
screen crosses `src/dociq/gui/pipeline.py` or does not appear, and the shell
still renders against `mock_pipeline`, which discloses itself in standing chrome
on every screen.

---

## 1. What was built

### E-1 — `ProfileChecklistScreen` (§6 steps 2–3)

Reached from the setup screen by a **link** ("See what this profile keeps and
drops…"), never a peer button: D-16 removed "Review what gets dropped" as a
button because a second button beside the primary read as a prerequisite step.
The link sits with the existing "Profile new format…" link, and the setup screen
still has exactly one `#primary` action — asserted.

Each row states, in one place: the disposition as a **word**, the section's
plain-language label, its page and token weight, and the **attribution** —
`Rule <profile_id> v<version> → section "<key>" → DROP`, plus the statement
that every page it removes is listed in `processing_log.json` against that rule.

The screen's load-bearing property is completeness, and it is enforced rather
than asserted:

| state | rendered | approvable |
|---|---|---|
| rules read, count matches the profile's declaration | full list + "All N section rules … are listed above" | yes |
| profile declares 0 rules | "This profile carries no section rules" | yes |
| rules could not be read | `CHECKLIST_NO_RULES`, in warn colour | **no** |
| declared count ≠ shown count | "declares N … but M are shown here. Do not run it until that is explained" | **no** |

"Use this profile" is disabled in the last two, because the button's entire
meaning is "I have seen what this drops".

### E-2 — the waterfall, under D-14/D-15/D-21

* The capacity row is a **named, sourced reference**: `CAPACITY_LABEL` =
  "Claude Project direct context", delta column reads `200K reference, not a
  target`, tooltip and a line under the headline carry `CAPACITY_SOURCE`
  (ruled D-21, not confirmed against published limits). It previously read
  "unconfirmed", which understates a ruling that now exists.
* **The two totals are stated apart and there is no combined figure anywhere.**
  `SummaryView.split_line()` renders "Left out on your approval: … · Removed
  mechanically by the tool: …". A test asserts the merged figure appears
  nowhere on the line.
* `SummaryView.drops_line()` names **every** engaged expert lever, so an expert
  can describe what was left out. Nothing is elided; a list that quietly stopped
  at five would be a silent cap in the one sentence whose job is completeness.
* `ReductionLever.estimated` is now marked on **expert** rows too, in the delta
  column beside the figure. It was previously rendered on automatic rows only.
* `route_line()`/`capacity_line()` reworded so the line is never a thing to
  reduce to; `CapacityReading.verdict()` and `.caption()` are **withdrawn**
  (below).
* `ratio_refuted` wording states the inconsistency **and its condition**, and
  ends "not a finding that the band is wrong."

### E-3 — `HandoffScreen`

Reached by the summary screen's single `#primary` action, "Analyze in Claude",
which is disabled — with the reason — for any run that published nothing.

**Path B leads.** It is first on the screen, described in full, and owns the
screen's `#primary` action ("Open the matter folder"). The folder path is shown
in mono with a copy button, and the description of what is in the folder is the
**pipeline's** sentence rendered verbatim; when the pipeline offers none, the
screen says DocIQ cannot confirm the folder is Expert-Assist-shaped rather than
describing a layout it did not check.

**Path A is bounded, per D-20.** The operator scopes the package (whole matter /
date range / document type). The **scope statement that will be written into the
package** is rendered verbatim on screen before the button is pressed, and a
subset says `This package is a SUBSET. It covers N of the M documents…` with
"Do not treat it as the complete record". A date scope additionally names the
count of documents with **no detected date**, which fall out of the scope on the
absence of a date rather than on their content.

The GUI writes nothing. The package build crosses the seam as
`(doc_ids, scope_statement)`.

---

## 2. Stop-the-line items raised (seam FROZEN, nothing edited)

Full entries in `docs/contracts/amendments.md`.

| # | Gap | Interim |
|---|---|---|
| **A-11** | `PipelineAPI` cannot deliver a profile's section rules *before* a run; `ProfileInfo` carries a rule **count**, never the rules. Proposed `profile_rules(profile) -> (tuple[ReductionLever,...], TokenBasis, str)`. | Duck-typed `getattr` hook; absence renders the loud empty state and blocks approval. |
| **A-11 (b)** | `ReductionLever` has no `rule`/`note`, so a drop is attributed by rule *identity*, never by the profile's own matching pattern or the §6 notes field ("why sections were dropped, who approved"). | Identity attribution only. This is the gap between "attributable" and "defensible in the expert's own words". |
| **A-12** | Neither §8 path crosses the seam. Path B needs `emit/handoff.expert_assist_layout`'s *checked* statement; Path A needs `build_upload_package` **plus** D-20's `doc_ids` filter and a `scope_statement` written into the package. | Two duck-typed hooks; both absences are rendered with the reason on screen. |
| **A-13** | `DIRECT_CONTEXT_TOKENS`' docstring still says "**UNCONFIRMED.** Alex has not ruled this threshold". D-21 ruled it. The claim, not the constant, needs withdrawing. | GUI states the ruling and the non-confirmation in `CAPACITY_SOURCE`. |

---

## 3. Defects found and fixed in this package

All found by the state grid or by reading the renders; all fixed here, none
deferred. Each has a test whose fail-before was **watched red** (§4).

1. **Three screens scrolled sideways at the product's minimum window (1040 px).**
   `setup` 21 px, `progress` 187 px, `detail` 376 px. Two causes, one class: an
   unwrapped `QLabel` inside a `QScrollArea` sets the scrolled widget's minimum
   width to its full text width (progress filenames, detail primaries and
   locators), and a `QComboBox` sizes its minimum to its longest item (the
   profile picker). Pre-existing — Sprint 1's overflow test covered the summary
   screen only. Fixed by wrapping and by capping the combo's minimum contents
   length; the grid now asserts **28 cells** rather than one screen.
2. **A projected EXPERT saving stood in the delta column in the same type as a
   counted one.** `estimated` was rendered on automatic rows only.
3. **The automatic row rendered "DROP" in the expert accent** on the checklist,
   merging the two kinds of omission at a glance — the thing `LEVER_AUTOMATIC`
   and D-14 exist to prevent. It now reads `AUTOMATIC`, muted.
4. **An unreadable profile summarised itself as "Nothing is being left out on
   your approval — every section is kept."** A claim of absence made where
   there was no knowledge, in the operator's own summary line, on the screen
   whose job is to prevent exactly that. Now: "Not known … must not be assumed
   to be nothing."
5. **The handoff screen had two sources of truth for the scope.** The statement
   came from the view; the build button re-read the controls. A scope set any
   way but by clicking produced a package under a statement describing something
   else — the precise failure D-20 is about. Controls are now synced to the
   view, an offered-value gap is added rather than silently ignored, and the
   build emits the scope whose statement was displayed.
6. **Disabled secondary and link buttons looked enabled.** Only `#primary` had a
   `:disabled` QSS rule. The refused "Build the upload package" rendered with a
   crisp border and full-strength navy label. The reason is stated beside it,
   but a control that looks pressable invites the press before the reason is
   read.
7. **The checklist's disposition column clipped "AUTOMATIC" to "AUTOMAT".** A
   hand-picked `UNIT * 8` width — a truncation the screen performed and did not
   say it had performed. Found by reading the render, not by a test. Fixed
   correct-by-construction: the column is sized from the widest word it can
   ever hold, the words are enumerated in `DISPOSITION_WORDS`, and a test
   asserts the enumeration is exhaustive against what the screens actually
   produce, so a fourth word cannot silently reintroduce the clipping.
8. **A full-scope package called itself "the complete production" while §5's
   listed-only files existed.** `build_handoff` projected
   `result.documents` and ignored `result.unsupported` — on the fixture, 5
   files inventoried and hashed but never text-extracted. A Path A package can
   never contain them, so the statement written *into* the package asserted a
   completeness the package did not have, in the one file a downstream reader
   would trust to know better. Also found by reading the render. The statement
   now names the listed-only count, says they are not in the package, and
   points at their `document_index.csv` rows.
9. **A test in this package crashed the whole suite, silently.**
   `test_every_button_kind_has_a_disabled_appearance` called `build_theme()`
   without requesting the `app` fixture. Resolving a font with no
   `QApplication` is an **access violation**, not an exception: the run died
   immediately after `test_extract`, at 27%, with no traceback, no failure
   report, and a bare exit code — and it **passed when the file was run
   alone**, which is how it survived every targeted run in this package. It was
   caught only because the ≥8-run rule forces the whole suite to run in order.
   A green targeted run really does prove nothing.
10. **Withdrawn, not reworded:** `CapacityReading.verdict()` ended "Drop more
   sections, or split the matter" — an instruction to get under the line, which
   D-15 and D-21 both rule against. It and `caption()` were dead code called
   only by their own tests. Both methods, both tests, deleted. A replacement
   test scans **string literals that are not docstrings** across `dociq/gui/`
   for target framing, and the grid asserts it over rendered text on all 28
   cells.

---

## 4. What was measured

### Fail-before, watched red

`19 of 19` guards. Each perturbation reverts exactly the behaviour a test
claims to protect; the harness then requires a non-zero exit.

```
RED (good)  layout: combo minimum uncapped
RED (good)  layout: progress filename unwrapped
RED (good)  layout: detail locator unwrapped
RED (good)  E-2: estimated expert lever not marked
RED (good)  E-2: capacity row unnamed / target-shaped
RED (good)  E-2: the two totals merged into one
RED (good)  E-2: automatic row drawn as an expert DROP
RED (good)  E-2: a target phrase reaches a screen
RED (good)  E-1: unreadable profile summarized as dropping nothing
RED (good)  E-1: an unreadable profile can be approved
RED (good)  E-1: projected checklist figure unmarked
RED (good)  E-3: a subset does not say it is a subset
RED (good)  E-3: controls not synced to the scope on screen
RED (good)  E-3: full-scope package hides the listed-only files
RED (good)  E-3: undated documents silently excluded
RED (good)  E-3: no reason given when the package cannot be built
RED (good)  checklist: disposition column silently clips a word
RED (good)  chrome: no disabled appearance for secondary buttons
RED (good)  D-21: the capacity literal inlined in the GUI
```

**An incident in the harness itself, recorded because it nearly cost a false
green.** The first version restored the file only in an in-process `finally`. A
run was interrupted between write and restore and left a perturbation
(`ChecklistRow.scale()` returning the unmarked figure) in the source tree. It
was found by luck — a later case reported its own search text missing — not by
design, and while it was in place a full-suite run was in flight. The harness
now writes its backup to disk and reclaims it at start-up; on the very next run
that recovery path fired for real and printed the file it had repaired, which
is how the mechanism is known to work rather than assumed to. **Every
measurement in this note was taken after that recovery**, on a tree confirmed
clean by `git status` and by programmatically re-checking every perturbation
site for both its original text and its perturbed text.

### Full suite

8 consecutive runs of all 775 tests, every one exit 0 — the log is in §5. `tests/test_import_graph.py` passes throughout;
the GUI imports no `ingest`/`identify`/`docid`/`profiles`/`emit`/`verify`
module, statically or at runtime.

### State grid

`tests/test_gui_screen_states.py` enumerates **28 cells** — 6 screens × their
reachable states — and walks the whole grid with four global properties:

* no horizontal scroll at 1040 × 720, the product's minimum window;
* nothing left over from the previous state of the same screen;
* no wording that makes the reference line a budget or a target (D-21);
* no wording that promises a token floor (Codex B-6).

A separate test asserts the grid still covers every screen and still has 28
cells, so a grid that quietly stopped covering something fails rather than
passing forever.

### Renders

`tools/render_screens.py --out docs/design/sprint2_track_e` — 18 PNGs
committed, including the four checklist states and four handoff states. The
"package buildable" render uses a named subclass that writes nothing; the mock
still does not offer a package builder, so the default renders show the refusal
with its reason.

---

## 5. Full-suite runs

**775 tests, 8 consecutive full-suite runs, every one exit 0.** Command:
`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider`,
each run a fresh subprocess.

```
run 1: exit=0 :: .......................................................                  [100%] (208s)
run 2: exit=0 :: .......................................................                  [100%] (215s)
run 3: exit=0 :: .......................................................                  [100%] (204s)
run 4: exit=0 :: .......................................................                  [100%] (224s)
run 5: exit=0 :: .......................................................                  [100%] (182s)
run 6: exit=0 :: .......................................................                  [100%] (153s)
run 7: exit=0 :: .......................................................                  [100%] (170s)
run 8: exit=0 :: .......................................................                  [100%] (179s)
```

The wall-clock spread (153-224 s) is host load, not a signal. The exit code is
the assertion: a crash of the kind found in defect 9 produces a non-zero exit
with no failure report at all, so a run is only counted here if the process
exited 0.


---

## 6. What could NOT be proven

Stated plainly rather than left to be discovered.

1. **Neither E-1 nor E-3 has been driven by a real pipeline.** Both consume
   duck-typed hooks that only the mock implements (A-11, A-12). The screens
   are proven against the seam types and against the absence of the hooks; they
   are **not** proven against Track D's adapter, and the shapes in A-11/A-12 are
   proposals, not agreed contracts.
2. **No package has ever been built.** `build_package` is called with
   `(doc_ids, scope_statement)` and the call is asserted; nothing verifies that
   `emit/handoff.py` can honour a `doc_ids` filter, that `render_readme` puts
   the scope statement first, or that the resulting folder is what a Claude
   Project accepts. D-20's Path A acceptance run is not discharged by this
   package.
3. **The Path B instructions are the mock's words, and the mock says it checked
   nothing.** Whether the matter folder really is Expert-Assist-shaped is
   `expert_assist_layout`'s question and it has not been asked from the GUI.
4. **Every figure is still fixture-scale.** The waterfall is reviewed at the
   measured record's magnitude via `at_measured_scale`, which is the fixture's
   *shape* at the measured *total* — it is not a measurement of what a profile
   would remove from the real corpus. No mockup number was carried in, and the
   disclosure bar states the gap on every screen.
5. **Attribution is by rule identity, not by the expert's stated reason.**
   A-11(b). The checklist can say *which rule* removed a page; it cannot yet
   show the pattern that matched or the note the approving expert wrote. That is
   the difference between an omission an expert can locate and one they can
   defend in their own words.
6. **`DIRECT_CONTEXT_TOKENS` remains unconfirmed against published limits.**
   D-21 rules the value; nothing measured it. Every screen that shows it says so.
7. **One unrelated test flaked once and is not ours.**
   `tests/test_extract.py::test_mixed_pdf_routes_page_by_page` failed a single
   time during this package with `PageKind.EMPTY` where `PageKind.OCR` was
   expected, then passed on every subsequent run, including the 8
   consecutive full suites in §5, and passes on a clean checkout of the branch point. It is in the
   ingest/OCR path, which Track E does not touch. **It is a real flake, not a
   pass** — recorded here for whoever owns that path rather than dismissed,
   because a page silently classified EMPTY instead of OCR is evidence loss of
   exactly the class §4 is written against.
