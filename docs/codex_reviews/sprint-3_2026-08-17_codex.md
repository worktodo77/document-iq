# Codex review — DocIQ Sprint 3 (merge gate)

**Repository:** `worktodo77/document-iq`  
**Branch:** `build/sprint-3`  
**Reviewed commit:** `444b4c5` (implementation tip `ff29d9c`)  
**Base:** `main` at `d69459d`  
**Review request:** `docs/codex_reviews/sprint-3_2026-08-17_claude.md`  
**Review date:** 2026-08-17  
**Gate:** D-33, Sprint 3 omission taxonomy

## Verdict

**NOT PASSED — a fix round is required. Two B findings and one D finding.**

The central replacement is materially better than the deleted carried-regex
engine. Recognition is transported through resume-safe page records, a bounded
span cannot run past its stated end, an unengaged template drops nothing, and
the approver and template inputs move the run identity. I also attacked
`spans_from_pages` directly and found no third production-path departure beyond
the two the handoff discloses: Tier-3 evidence is regenerated per tier, and
adjacent equal-label/equal-tier spans merge.

The merge is nevertheless held at two adjacent trust boundaries. First, the
Tier-1 resolver discards a backward outline entry as a section but also discards
it as a boundary, allowing an earlier offerable section to run across pages the
document assigned elsewhere. Second, approval scope is recorded but never
enforced: the ordinary **New run** GUI path retains the previous matter's
approvals, and Stage 4 applies them to the new matter by `family_id` alone. Both
paths can remove pages under a ruling that did not authorize their removal.

## B-1 — A skipped non-monotonic outline entry lets the previous section overrun it

**Severity class:** B — evidentiary integrity; pages can be dropped under a
section the document did not place them in.

**Locations:** `src/dociq/sections/tier1_outline.py:70-80`,
`src/dociq/sections/tier1_outline.py:96-104`,
`tests/test_sections.py:366-373`

`spans_from_outline()` says an out-of-order destination is skipped so its pages
remain unresolved. The code removes that entry from `boundaries` entirely.
Span ends are then calculated only from the surviving boundaries, so the
previous accepted section continues through the skipped destination. The
existing test proves only that the backward label is absent and that spans do
not overlap; it never asks what governs the pages beginning at that destination.

The direct counterexample used this outline over twelve pages:

```text
PROGRESS PHOTOGRAPHS -> page 1
PROCUREMENT          -> page 11
EXECUTIVE SUMMARY    -> page 5   (backward in outline order)
```

The reviewed code returned `PROGRESS PHOTOGRAPHS` for pages **1-10** and
`PROCUREMENT` for pages 11-12. The backward entry itself was absent, but its
pages were not unresolved; pages 5-10 inherited the photograph family. If the
expert approved the offered `progress-photographs` omission, those executive
pages would drop. This is the D-35 failure class in span form: a section is not
carried beyond an explicit `end_page`, but the resolver calculated that end
from a boundary set that had already thrown away the contrary boundary.

This is not hypothetical corpus shape: the required measurement records four
documents with non-monotonic outlines. Preserve a backward destination as a
safe stop boundary without treating its label as a trusted section, or leave
the conflicting interval unresolved by another fail-closed construction. Add a
fail-before with an approved, offerable preceding family and assert that every
page from the backward destination to the next trustworthy boundary remains
KEEP.

## B-2 — An approval is transferable across matters and template versions

**Severity class:** B — evidentiary integrity; a prior matter's ruling can drop
pages from a later matter while the log attributes the act to the prior matter.

**Locations:** `src/dociq/gui/main_window.py:183-190`,
`src/dociq/gui/main_window.py:240-241`,
`src/dociq/gui/main_window.py:322-337`,
`src/dociq/adapter.py:841-891`,
`src/dociq/sections/apply.py:109-124`,
`src/dociq/sections/apply.py:139-172`,
`src/dociq/pipeline.py:1517-1525`

`ApprovedOmission` carries `matter`, `template_id`, and `template_version`, and
its contract says an approval is not transferable between matters. None of
those three fields participates in authorization. `apply_sections()` validates
only that the record is nonempty and that its `family_id` exists in the current
template; it then keys the approval by `family_id`. It does not compare the
approval's template id/version with the loaded template, and it has no current
matter against which to compare `approval.matter`.

The GUI makes this reachable through ordinary use. **New run** returns to setup
without clearing `_approvals`; `start_run()` injects the retained tuple into
whatever new `RunRequest` the setup screen emits. The adapter computes the new
matter name at line 850 but does not use it to validate the approvals, and
copies their old scope unchanged into the core run.

A direct public-API reproduction applied an approval stamped
`Matter-A / old-template v0` to `Matter-B / current v1`. The page dropped, and
the resulting drop entry still said `Matter-A / old-template v0`. Thus the log
is complete but proves the opposite of authorization: it records on its face
that the ruling belonged elsewhere.

Enforce approval scope at the pipeline boundary before Stage 4: every approval
must match the current matter and the exact loaded template id/version, in
addition to naming an existing offerable family. A mismatch must fail closed
with no page dropped. Also clear or deliberately re-establish approvals when
the operator starts a new matter. Add fail-befores for the real **summary -> New
run -> different matter** path and for direct core calls carrying stale matter,
template-id, and template-version values.

## D-1 — A-18 is simultaneously applied and “RAISED, NOT APPLIED”

**Severity class:** D — process/documentation; nonblocking by itself.

**Location:** `docs/contracts/amendments.md:1305-1306`

A-18's header correctly says **APPLIED** and cites the unengaged real-PDF run
that writes tiers into `processing_log.json`. Its final paragraph still says
“while this entry says RAISED it means it” and “no run writes it to disk yet.”
Those statements describe the pre-wiring state and directly contradict both
the status and the measured application condition earlier in the same entry.
Delete or rewrite the stale paragraph so the required read list gives one
answer about amendment state.

## What this review accepts

- The page-record carrier is the correct resume boundary. A fresh extraction
  and a resume replay both reach Stage 4 with the same `section` and
  `section_tier`; a transient side channel would not.
- `spans_from_pages()` preserves coverage, label, and tier on the real stamping
  path. Its generic Tier-3 evidence and adjacent-span merge are exactly the two
  bounded losses stated in D-40 and the wiring note.
- With no approvals, recognition remains observable and every page remains
  KEEP. With a correctly scoped approval on a monotonic outline, the drop ends
  at the span boundary.
- The eight `offer=False` families are refused at both the reduction model and
  the real adapter. The checklist describes all nineteen template families and
  the real approve path is no longer permanently disabled.
- A-19's new inputs participate in the canonical run identity, including the
  approver. The identity changes when an approval is added and when only the
  approver changes.
- D-38 and D-39 are Sprint-4 rulings, not omissions in this review. Tiers 2 and
  4 remain explicitly unbuilt; the measured roughly-70% recognition ceiling and
  the broader shipped Tier-3 reach are stated as limits rather than equality
  claims.

## Verification performed

- Followed the handoff's required read order through D-33 to D-40, the corrected
  79-item baseline, the corpus measurement, the wiring artifact, and A-18 to
  A-20, all from `build/sprint-3` at `444b4c5`.
- The focused section/adapter/GUI/end-to-end/identity/emit/contract slice passed
  with exit 0.
- One complete `python -m pytest -q` run collected **1,494 tests** and exited 0:
  **1,493 passed and one intentional parameterized skip** (`RunRequest travels
  GUI -> pipeline; see INBOUND`). No failure or error appeared.
- `python -m dociq.selftest` exited 0 with **70 checks**; its determinism check
  completed eight sequential runs with one corpus hash.
- Direct counterexamples reproduced both B findings against the reviewed tip:
  the backward outline expanded photographs from pages 1-4 to pages 1-10, and a
  stale matter/template approval dropped a page while preserving its stale
  scope in the drop entry.
- `git diff --check d69459d...444b4c5` passed, and the worktree was clean before
  this verdict file was added.
- No human mouse-driven GUI acceptance or full-corpus timing run was performed;
  the handoff's corresponding non-claims remain non-claims.

## Merge condition

Fix B-1 and B-2 with fail-before coverage at the actual page-disposition and
real GUI boundaries described above, correct D-1 in the same round, refresh the
verification evidence, and request a fix-round review.
