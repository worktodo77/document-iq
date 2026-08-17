# Wiring the section taxonomy into the pipeline

**Date:** 2026-08-17
**Branch:** `build/sprint-3`
**Commits:** `4092f76` (spine), `d3cee24` (picker + approver capture)
**Follows:** `docs/verification/sections_2026-08-17.md` (the measurement) and
`docs/verification/sprint3_red_tree_2026-08-17.md` (the corrected baseline)

D-35 ruled the shipped Stage-4 engine replaced rather than repaired. D-34 ruled
that a template ships unengaged and approval is captured when the expert engages
a lever. This is the record of wiring both, and of what the wiring cost.

---

## The baseline, counted the way the last correction says it must be

`743446b` withdrew `0f71aef`'s "11 items fail" and put the real figure at 79.
That number was re-measured here before anything was changed, from a run watched
to completion and counted with `grep -cE "^(FAILED|ERROR) "` rather than from a
`tail`:

**79 items — 74 FAILED, 5 ERROR**, distributed exactly as
`sprint3_red_tree_2026-08-17.md` records them, file for file. The correction
reproduces.

## Where recognition happens, and why it is not where disposition happens

Tier 1 reads the PDF's own outline; Tier 3 measures the geometry of its pages.
Both need the file open, and by Stage 4 the pipeline holds records rather than
bytes. So recognition runs at extraction and disposition stays at Stage 4 —
which cannot move, because a drop-log entry is written against a Doc ID and Doc
IDs are issued at 3b.

The spans cross that gap **on the pages**: `walker._record` stamps `section` and
`section_tier`, and `sections.resolve.spans_from_pages` rebuilds the spans at
Stage 4.

**The carrier is the design decision, and resume is what settles it.** A side
channel from the walk to Stage 4 — a dict, an out-parameter — would be empty for
every document replayed from the resume cache. A resumed run would then drop
nothing where a fresh run dropped pages: two corpora from one folder, which is
the Principle-5 break the determinism contract exists to forbid. Pages are
already serialized, and A-18 already round-trips `section_tier` through
`_page_from_jsonable`, so the page record was the one carrier that could not have
this failure mode.

### What that costs, stated rather than discovered later

Rebuilding a span from stamped pages recovers the section, the tier and the page
range exactly. It does **not** recover Tier 3's instance-level evidence. The
original says `17 dd-Mmm-yy dates on one page`; the rebuilt one says
`a page-class rule placed this page in 'Schedule / activity table'`.

§5.4 requires the KIND of evidence to survive per page and it does — the tier is
on the record. The measurement that fired does not, and no log line should be
read as though it did. Tier 1 loses nothing: its evidence sentence is a function
of the outline title, and the title is the section label.

Two adjacent spans with the same label and tier rebuild as one. Both carry the
same family, so both reach the same disposition, and a merged span cannot reach
a page neither original covered.

## Measured end to end, on a real PDF

An 8-page PDF whose own outline names `EXECUTIVE SUMMARY` (p1), `PROGRESS
PHOTOGRAPHS` (p3) and `CRITICAL PATH NARRATIVE` (p5), run through the real
pipeline:

| run | template | approvals | pages dropped | in `processing_log.json` |
|---|---|---|---:|---|
| 1 | loaded | none | **0** | 3 sections, each `t1_outline` |
| 2 | loaded | `progress-photographs` | **2** | pages 3-4 only, attributed |

**Run 1 is D-34's shipped state and it is now observable.** A template loaded and
nothing engaged produces recognition and no omission — and the tier reaches the
log anyway, which is what makes A-18 true of an ordinary run rather than only of
a run somebody has ruled on.

**Run 2 is D-35 closed.** The drop stops at page 4. The old engine, given one
correct rule and no later rule marking the section's end, ran the drop to the end
of the document; that shape is the fifth and worst of the five the register
reproduces, and it needs no confusing text at all. A span names its last page, so
it cannot.

The drop entry reads, in full: tier `t1_outline`, evidence *"the document's own
outline entry 'PROGRESS PHOTOGRAPHS'"*, approved by a real Windows account, at a
real time, on a named matter, against `progress-report` v1.

## A-19, and why it is A-08 again

A-08 put profiles into the run identity because profiles decided which pages
dropped, and proved it with two measured counterexamples rather than an argument.
D-35 deletes that engine; D-34 moves the decision to an approval a person gives.
So the deciding input changed, and the identity did not follow it.

Measured on the run above:

| change | run identity |
|---|---|
| no approvals to one approval | **moves** |
| approver `ABachowski` to `JLong`, same approval | **moves** |

The second row narrows the determinism claim and the narrowing is correct: the
drop log names the approver, so the bytes genuinely differ. An identity that
reported those two runs as the same would be claiming sameness about two records
that say different things about who is answerable.

`project_tokens` is in the identity for the same reason and is the easier one to
miss — supply `MV32` and `MV32 APPENDICES` normalizes to `APPENDICES` and matches
an appendices rule; withhold it and the page keeps.

## Two defects found while wiring, both closed here

**1. The index would have printed a template id under "Omission approved by".**
The approver is deliberately not on `PageRecord` — a page records `drop_rule`,
which is `template_id:family_id`, while the approver lives on the
`ApprovedOmission` in the matter folder, because D-34 makes approval a
matter-scoped act by a named human rather than a property of a page. The first
draft derived the column from `drop_rule.split(":")[0]`, which is the template
id. `build_index_rows` now takes the drop entries.

**2. Widening `ReductionLever.locked` routed recognized rows into the
"automatic" branch of the waterfall.** `locked` changed from
`kind == LEVER_AUTOMATIC` to `kind != LEVER_EXPERT` so that a
recognized-never-offered section could not be toggled. The waterfall widget
splits on `locked`, and the automatic row's hint reads *"Removed mechanically by
the tool, not by an expert decision"*. Every `offer=False` family — the weather
logs, the timesheets, the manpower histograms, the critical path narrative —
would have been drawn to the operator as REMOVED, in the place they go to check
what happened to the corpus, while those pages were kept.

Fixed with a fifth row kind rather than by re-narrowing `locked`. "Not expert" is
the safe direction for a predicate deciding whether a page may be dropped: the
next kind added is locked unless somebody deliberately unlocks it.

## What the picker refuses, and at which layer

Eight of the template's nineteen families carry `offer=False`. They are drawn,
and they cannot be engaged — by the model, not by the widget:

* `ReductionPlan.with_toggled` ignores a locked row, so the summary screen cannot
  move one even if a future widget wires a click to it;
* `RealPipeline.set_omission` refuses an unknown family AND a family with
  `offer=False`, so a caller that bypasses the screen entirely is still refused.

Both were exercised: engaging `weather-logs` and engaging an unknown id are each
refused, and the refusal carries the family's own stated reason.

"The widget will not send it" is a hope about every future widget. The refusal is
at the layer a widget cannot reach past.

## Claims withdrawn, not just code deleted

* `RunConfig.profiles` and `contracts.py`'s A-08 note said Stage 4 applies the
  first profile whose header patterns claim a document. Stage 4 does not claim
  documents. Corrected where the mechanism is described; A-08's own history is
  left standing as history.
* `pipeline.py`'s note on why `profile_id` is not resolved from the library gave
  a reason that stopped being true — Stage 4 no longer overwrites the stamp on
  claimed documents, because it no longer claims. The conclusion survives on a
  plainer and *stronger* ground and now says so.
* `sections/normalize.py` quoted 547 families / 750 raw / 116 project-bearing /
  21%. Those are runs 1 and 2 of the probe, and the measurement note says in
  terms that any figure quoted from those runs elsewhere is superseded. The true
  figures are **522 / 716 / 159 / 30.5%**. Nothing in the module's behavior
  turned on them, which is exactly why they survived two readings.
* `test_import_graph.py`'s `FORBIDDEN` list named the five pipeline packages that
  existed when the freeze was written. `dociq.sections` is a sixth and holds
  `apply_sections`. A rule enumerated from what exists on the day has its blind
  spot exactly where the codebase grew — the same shape as the `A-11b` reference
  pattern that could not match the one amendment nobody had checked.

## §6's checklist now describes the template

Rendering a profile's rules on the checklist would print "DROP" beside a rule
that drops nothing, on the one screen whose entire purpose is an expert approving
omissions **before** a run commits to them. `profile_rules` returns the
template's families, every lever OFF, `estimated=True` and no figures — before a
run there are no pages to count.

This is a decision taken while wiring rather than one that was ruled, and it is
flagged as such: `profile` stays on the Protocol because a profile is still an
identity input, a matter copy and a log entry.

## What is NOT established by this note

* **No timing figure appears here.** Parallel agents were running for part of
  this work, and the register already records what timing claims made under load
  are worth.
* **Tier 3 in the shipped engine recognizes MORE than the probe measured.** The
  probe classified Tier 3 only in documents whose outline is not substantive;
  the engine classifies every page no Tier-1 span covers, including pages inside
  an outlined document. So the 1,308-page / 20.66% figure is a floor for the
  shipped behavior, not an equality. Neither difference can drop a page —
  dropping needs an approval.
* **The probe read PyMuPDF's text; the engine reads the pipeline's** (native
  layer, OCR where there was none, footer band merged). A scanned page the probe
  saw as empty is classified here on its OCR text.
* **Tiers 2 and 4 are not built**, and the corpus-wide ceiling stands where the
  measurement put it: about 70% of pages recognized, about 30% kept
  unconditionally.
