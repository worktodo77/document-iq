# Rehearsal-review findings A3, B1–B6 — verification note

Branch `build/s2-defects`, off `build/sprint-2` @ `3a44f2e`.
Seven confirmed rehearsal findings, closed in severity order.

Frozen and off-limits for this package: `src/dociq/gui/pipeline.py`,
`src/dociq/contracts.py`, `src/dociq/identify/bates.py`, the Bates decision path
in `src/dociq/pipeline.py`, `src/dociq/gui/screens.py`,
`src/dociq/gui/main_window.py`, `docs/decisions/`. Two findings needed something
inside those files; both are STOP-THE-LINE items below rather than edits.

---

## A3 (severe) — a subset package silently shipped the whole matter's manifest

**Status: fixed.** Reproduced by the reviewer, and re-reproduced here through
five distinct triggers before the change.

`_filtered_sources` returned `None` on `OSError`, on invalid JSON and on any
payload that was not a dict; `_filtered_index_csv` returned `None` on `OSError`
and on an empty file. The caller then fell through to
`shutil.copyfile(src, dst)` and put the **whole matter's** `sources.json` /
`document_index.csv` inside a two-document package, with no exception raised.
`assert_only_sanctioned` structurally cannot catch it: `sources.json` is a
sanctioned *name*, and the file that shipped was correctly named and wrong
inside. The realistic trigger on this machine is antivirus holding a lock —
an `OSError`.

Fixed by removing the fallback entirely. Both filter functions raise
`PackageContentError` with a message naming the file, the reason, and what to do;
the caller writes filtered content or the build refuses.

### Enumeration — every other silent-fallback-to-whole-file path

Walked `build_upload_package` end to end. The two manifest filters were the
only substitution paths in it:

| path | verdict |
|---|---|
| `sources.json` filter | **was** a fallback-to-whole — fixed |
| `document_index.csv` filter | **was** a fallback-to-whole — fixed |
| per-document `clean_text` copy | copies exactly the selected files; no whole-set path |
| README | generated from arguments; never copied |
| `shutil.rmtree` + `mkdir` | rebuilds from empty each time; no stale-content path |
| `if not src.is_file(): continue` | omits a manifest that does not exist; omission, not substitution — and the class guard below covers what it leaves |

### Class fix, not repro fix

`_assert_manifest_matches_folder` now runs on **every** package, before it is
returned. It asserts the property that makes a package citable — what the
manifest names, the folder holds — without caring how a wrong manifest got
there. A future writer that reintroduces whole-matter content by some other
route fails on the property.

Asymmetry, deliberate and stated: `sources.json` is checked always (§7 makes it
the thing Expert Assist reads to *find* text, so a name with no file is a
citation that resolves to nothing). `document_index.csv` is checked only for a
subset. **This was found by the new guard itself, not reasoned in advance:** it
fired on a whole-record run because a §5 *unsupported* file legitimately carries
a Doc ID and an index row and has no `clean_text` by definition. Asserting over
the whole-record index would have flagged the §5 completeness rule as a defect.

### Fail-before, watched RED

`tests/test_emit.py`:

- `test_an_unfilterable_manifest_refuses_it_never_copies_the_whole_one` ×3
  (invalid JSON, non-dict payload, empty CSV)
- `test_a_locked_manifest_refuses_rather_than_copying` ×2 (`PermissionError`
  from `read_text` — the antivirus shape, simulated at the read because a real
  lock is not portable; the code path is the same)
- `test_no_manifest_may_name_a_document_the_package_does_not_hold`
- `test_a_subset_package_never_copies_a_manifest_at_all` — the mechanism rather
  than a trigger: while a package is scoped, `shutil.copyfile` is never called
  with a manifest, however the code is rearranged. Watched red: it reported
  `copied ['DIQ-000001.txt', 'sources.json', 'document_index.csv']`.

---

## B1 — the §6 approval sentence reported a projected zero as a flat zero

**Status: fixed.** Reproduced.

`ChecklistRow.scale()` appends "(projected, not counted)"; the aggregate
`ProfileChecklistView.drop_summary()` did not. `RealPipeline.profile_rules`
returns `tokens=0, pages=0, estimated=True` for every row, so the sentence
**gating approval** read:

> 3 section types left out on your approval: 0 pages, about 0 tokens.

An expert reads "these drops cost nothing", approves, and the run drops real
pages.

One shared `_projection_note(levers)` now produces the marker, in the row's own
vocabulary, and names the dangerous case explicitly: *"A zero here is the
absence of a measurement, not a saving of nothing."* Mixed sets say how many of
how many are projected. A fully counted set gets no marker — an always-on marker
is decoration.

### Class fix

Every aggregate over levers, not the one reported. All four are asserted by
`test_every_aggregate_over_levers_marks_a_projection`, which builds an
all-projected set and requires each sentence to say so, so a fifth aggregate
written later fails by omission:

| aggregate | before |
|---|---|
| `ProfileChecklistView.drop_summary` | unmarked — the reported defect |
| `ProfileChecklistView.automatic_summary` | unmarked — same defect, unreported |
| `SummaryView.split_line` (both halves) | unmarked — dormant today (`_plan` sets `estimated=False`), correct now |
| `SummaryView.drops_line` | already carried a marker |

### Fail-before, watched RED

Four tests in `tests/test_view_models.py`, all red with `view_models.py`
reverted, including the literal reproduction
`'1 section type left out on your approval: 0 pages, about 0 tokens.'`

---

## B2 — "fix the class" was claimed and only one class was fixed

**Status: fixed in everything editable; one site is STOP-THE-LINE.** Reproduced.

The A-11b probe policed exactly one record, `ReductionLever`. The class is *"a
frozen presentation record rebuilt by listing its fields"*, and it was never
only that record.

`test_no_seam_record_is_rebuilt_with_optional_fields_positionally` now
enumerates the frozen presentation records from `dociq.gui.pipeline` itself —
every record the seam **defines**, so one added tomorrow is policed the moment
it exists. Records the seam only re-exports (`RunConfig`, `RunResult` from
contracts; `RunTermination` from runstate) are excluded: they belong to the
contract layer and its own freeze.

**The line drawn is "positional arguments beyond a record's required fields",
not "any positional argument".** Required fields must be supplied at every call
site, so a new *required* field breaks all of them loudly and can never vanish
silently. Every field added to a shipped frozen record carries a default — and a
default is what a rebuild that stopped listing fields silently falls back to. So
the optional arguments are the ones that can go stale, and passing those by
position is the defect. This also leaves `TokenEstimate(chars, low, high)` alone
as the plain three-argument construction it is.

### Sites found and fixed

| site | record | |
|---|---|---|
| `src/dociq/adapter.py:73` | `ProfileInfo` | fixed |
| `src/dociq/gui/mock_pipeline.py:134,135,136` | `ProfileInfo` | fixed |
| `tests/test_adapter.py:82` | `RunRequest` | fixed |
| `tests/test_gui_screen_states.py:92` | `RunRequest` | fixed |
| `tests/test_gui_screen_states.py:750` | `TokenBasis` | fixed |
| `tests/test_gui_states.py:46,162` | `RunRequest` | fixed |
| `tests/test_view_models.py:228` | `RunOutcome` (6 of 8 positional) | fixed — the reported fixture |
| `src/dociq/gui/pipeline.py:256` | `ReductionPlan` | **STOP THE LINE** |

The old probe also exempted `tests/test_view_models.py` from its own scan
("this file constructs one deliberately"). The exemption was unnecessary —
keyword construction is not an offence — and it is exactly what hid the
`RunOutcome` rebuild. Removed.

### STOP THE LINE — `src/dociq/gui/pipeline.py:256`

`ReductionPlan.with_toggled` rebuilds **`ReductionPlan`** positionally inside
the very method that was fixed to stop rebuilding `ReductionLever`
positionally. Lossless today at 4 of 4 fields, silently lossy on the next one.
The one-line fix is `replace(self, levers=levers)`.

Not edited: the module is frozen and shared with parallel work. It is covered by
`test_the_frozen_seam_module_has_no_positional_rebuild`, marked
`xfail(strict=True)` — so the day the seam owner fixes it the test turns RED and
the marker must be removed. The defect cannot go quiet.

### Claim withdrawn, not just code changed

`docs/contracts/amendments.md` A-11b said all four rebuild sites were fixed and
that the probe "cannot be fooled". Both overstated. A dated correction section
now withdraws each, in the amendment itself, naming what was missed.

### Fail-before, watched RED

Probe run against the unfixed tree: five offenders reported by file and line
across `mock_pipeline.py` and `test_gui_states.py`.

---

## B3 — the import-graph check could not see a relative import

**Status: fixed.** Reproduced, end to end.

`tests/test_import_graph.py:37` — `if node.level: continue  # a relative import
cannot reach another package`. The premise is false: `from ..ingest import
dating` inside `dociq/gui/` reaches `dociq.ingest`, and that is precisely the
rule the freeze is about. The runtime subprocess half could not cover it either
— it only *imports* modules and never calls their functions, so a deferred
import is invisible to it.

`_resolve_relative` now resolves every relative import to the absolute module it
actually reaches, and `_module_names` feeds the resolved names into the same
assertion.

Also fixed: the runtime check's hard-coded eight-module list, which had gone
stale — `dociq.gui.pipeline` and `dociq.gui.theme` were both absent, so the
runtime half silently did not cover the seam module. The list is now derived
from `GUI.rglob`, with `test_the_runtime_check_covers_every_gui_module`
asserting the two that were missing are in it.

The subprocess check's docstring now states plainly what it cannot do, because
the static scan is what carries the rule.

### Fail-before, watched RED

- Resolver reverted → 3 tests red, including the resolution table
  (level/module → module actually reached) and the nested-in-a-function case.
- **End-to-end**: `def _probe_dates(): from ..ingest import dating` appended to
  `src/dociq/gui/widgets.py` →
  `test_gui_imports_no_pipeline_package[widgets.py]` FAILED with
  `'dociq.ingest'.startswith(...)`. The same append against the old check left
  all fourteen tests passing. Reverted; suite green again.

---

## B4 — `ocr_available()` checks presence, never capability

**Status: fixed at the class level.** Reasoned by the reviewer; the run-level
gap was measured here.

`ocr_available()` imports two modules and stats three `.onnx` files. It never
constructs the engine and never runs inference, so an engine that imports
cleanly, finds its models and then produces nothing on every page passes it.
The Sprint-2 burn was *inside* inference, under `_ocr_pdf_pages`'s per-page
`except Exception`, where a dead engine and a few bad pages are indistinguishable
one page at a time. The real capability probe exists only in `dociq.selftest`,
which `build.py --skip-verify` bypasses.

Added: `ocr_yield(documents)` and `ocr_yield_warning(documents)`, computed over
the **final corpus** in `walker.run` and appended to `RunResult.warnings`. It
fires when a run made OCR attempts and **every one** recovered nothing — the
shape of a dead engine, and of nothing else. The warning names the count and
tells the operator to run `dociq selftest`, which does construct the engine.

`ocr_available()`'s behaviour is unchanged (a presence check is all it can be
cheaply) but its docstring now withdraws the capability the name implies and
points at both the selftest probe and `ocr_yield`.

### A measured correction to the first draft

The first version counted attempts by `PageKind`. Run against a real dead-engine
walk it reported **zero attempts and no alarm** — because a page routed to OCR
that recovers nothing is relabelled `EMPTY` by `make_page` (the only kind the
contract lets carry no `ocr_conf`), and page 1 of a photo-only document is
relabelled `PHOTO` before that. Attempts are now counted on the **disclosures**,
which survive both relabellings:

- `M_OCR_BLANK` — routed to OCR, recovered nothing (named, was an inline literal)
- `M_OCR_PAGE` — could not be rasterized or read
- `PageKind.OCR` with text — the attempts that worked

The `PHOTO` relabelling dropped the disclosure entirely, so `extract.py` now
attaches `M_OCR_BLANK` there before the kind changes. Without it a corpus of
photo-only PDFs against a dead engine reports no attempts and no alarm.

`M_OCR_BLANK` is classified `FINAL`, not transient: the same bytes through the
same engine reach the same wall, and a corpus of blank scans would otherwise
spend the whole serial-retry budget proving it. **The existing class assertion
`test_every_degradation_marker_is_classified_exactly_once` caught the
unclassified constant** — the guard worked.

### Fail-before, watched RED

`tests/test_walker.py::test_a_run_whose_ocr_recovered_nothing_at_all_says_so` —
a real walk over the scanned fixture with `_ocr_array` stubbed to return
nothing. Red with the kind-based draft (`AssertionError: ()` — no warnings at
all), green after. Paired with
`test_a_working_engine_raises_no_alarm`, because an alarm that also fires on a
healthy run is an alarm that gets ignored on the run that matters. Four unit
tests in `tests/test_extract.py` cover the counting, including the
never-rasterized page.

---

## B5 — `UploadPackage.missing` was dropped on the floor

**Status: wired to the seam boundary. STOP THE LINE.** Reproduced.

`build_upload_package` computes `missing` — Doc IDs requested with no
`clean_text` file — with a docstring saying it is *"reported rather than
silently skipped… the operator is the only one who can say whether it matters"*.
`RealPipeline.build_package` never read it, and `PackageResult` has no field for
it.

`PackageResult` is in the frozen seam and was not edited. The value is now held
on the adapter as `RealPipeline.last_package_missing`, the same treatment
`library_issues` already gets, read off the package rather than recomputed.

### The field the seam needs

```python
# src/dociq/gui/pipeline.py, on PackageResult
missing: tuple[str, ...] = ()
"""Doc IDs the scope asked for that the matter folder had no clean_text for."""
```

Once it exists, `adapter.py` sets it directly and
`RealPipeline.last_package_missing` should be removed. A package whose scope
statement claims N documents and whose folder holds N-1 is the D-20 failure in
miniature, and it is the one the operator would never see.

---

## B6 — a partially-dropped section rendered as fully KEPT

**Status: confirmed, then fixed.** Flagged as reasoned; **constructed and
confirmed before any code changed**, as instructed.

### Confirmation

`src/dociq/adapter.py:262` set `engaged = dropped == pages` and the lever always
carried the whole section's figures. On a three-page construction with one page
of a two-page section dropped:

```
lever 'Photo logs': pages=2 tokens=328 engaged=False
full_tokens 451 | waterfall remaining 451 | actual tokens_after 287
```

The row said the section survived whole; one of its pages had not. The waterfall
overstated the published corpus by 164 tokens and disagreed with `tokens_after`.

### It is reachable — not a phantom

`FormatProfile.validate` enforces uniqueness on `rule_id` **only**; `label` is
unconstrained. Stage 4 keys a page's section on `rule.label or matched_text`. So
one DROP rule and one KEEP rule sharing a label — in one profile, or across two
profiles claiming different documents — put dropped and kept pages under the
same section name. Driven through `apply_profiles` with an ordinary profile that
`validate()` accepts:

```
page 1 None       keep
page 2 Appendices drop
page 3 Appendices keep
```

### Fix

`_section_lever` now accounts for the dropped part separately. A lever means
"what this removes when engaged", so:

- some pages dropped → engaged, carrying the **dropped** part's pages and
  tokens, with the label saying `(part — 1 of 2 pages)`;
- nothing dropped → not engaged, carrying the whole section — the projection
  "if you dropped this too".

Both readings are then consistent with `remaining_tokens`, verified as
arithmetic: 287 remaining against 287 published. The two cases that were already
right are unchanged, label included — `(part — 2 of 2)` on a full drop would be
noise that trains the reader to skip the marker.

The partial fact lives in the label because `ReductionLever` has no field for it
and the seam is frozen; the label is the string every screen already renders.

---

## Standing rules

- **A green result proves nothing.** Every fix above has a fail-before that was
  watched go red — by reverting the source, by perturbing the probe, or (B3, B4)
  by driving the real defect through the real code path. Two drafts were caught
  by their own probes coming back green when they should not have (the B4
  kind-based counter; the A3 mechanism test that passed before it was made to
  break the manifest first) and were rewritten rather than accepted.
- **Fix the class, not the repro.** A3, B1, B2 and B4 each ship a sibling
  enumeration and a probe over the class. Two of them (B2, B4) were explicitly
  previous class-fix claims that did not hold.
- **Withdraw the claim.** A-11b's two overstatements are corrected in the
  amendment; `ocr_available`'s docstring withdraws the capability its name
  implies, with a test asserting the withdrawal survives.

## Open — for the seam owner

1. `src/dociq/gui/pipeline.py:256` — `ReductionPlan.with_toggled` rebuilds
   `ReductionPlan` positionally. Strict-xfail'd, not fixed.
2. `PackageResult.missing: tuple[str, ...] = ()` — needed so B5's value reaches
   a screen.
