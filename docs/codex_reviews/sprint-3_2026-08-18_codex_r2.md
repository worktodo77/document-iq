# Codex fix-round review — DocIQ Sprint 3

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-3`
**Reviewed commit:** `1446bb3` (fix implementation `46bc067`)
**Fix-round handoff:** `docs/codex_reviews/sprint-3_2026-08-17_claude_r2.md`
**Prior verdict:** `docs/codex_reviews/sprint-3_2026-08-17_codex.md`
**Review date:** 2026-08-18

## Fix-round verdict

**NOT PASSED — B-1 and D-1 are closed; B-2 remains open at the matter-identity boundary.**

The non-monotonic outline fix now fails closed at the actual page disposition,
and A-18's amendment entry gives one consistent answer. The approval fix also
correctly refuses different matter strings and different template ids/versions.

The flagged divergence is acceptable as policy: approvals should survive the
rebuild that applies them to the same matter, and they should be discarded when
the operator changes matters. Clearing on every **New run** would indeed make
the summary-screen approval impossible to apply. The implementation does not
yet establish that policy, however, because both the GUI and Stage 4 define the
matter scope as `Path(source_root).name`. Two different matter folders with the
same final directory name therefore share an authorization scope.

## B-1 — CLOSED

**Prior finding:** a skipped non-monotonic outline entry was removed from the
boundary set, so an earlier approved section could overrun into its pages.

**Re-verification:** `spans_from_outline()` now retains the backward destination
as an unnamed boundary. On the original counterexample it returns photographs
for pages 1-4, leaves pages 5-10 unresolved, and resumes with procurement at
page 11. With the photographs omission approved, only pages 1-4 drop. The new
tests assert both geometry and the page-level KEEP guarantee.

**Locations:** `src/dociq/sections/tier1_outline.py:50-69`,
`src/dociq/sections/tier1_outline.py:89-104`,
`tests/test_sections.py:650-716`

No residual B-1 defect was found.

## B-2 — PARTIALLY FIXED, STILL OPEN

**Severity class:** B — evidentiary integrity; one matter's approval can still
drop pages from a different matter.

**Locations:** `src/dociq/gui/main_window.py:316-318`,
`src/dociq/gui/main_window.py:338-358`,
`src/dociq/adapter.py:841-891`,
`src/dociq/sections/apply.py:129-165`,
`src/dociq/sections/model.py:279-284`

The Stage-4 fix is correct for the values it compares. An approval with a
different `matter`, `template_id`, or `template_version` is discarded, the page
keeps, and the reason is reported. Supplying approvals without a current matter
raises. Those checks close the exact `Matter-A` versus `Matter-B` reproduction.

The remaining issue is that `matter` is not an identity. The capture point, the
new-run filter, and the adapter all derive it from the source folder's basename:

```text
C:/Client-A/Production  -> matter "Production"
D:/Client-B/Production  -> matter "Production"
```

Those are distinct source roots and can be unrelated legal matters, but the GUI
filter sees equal strings and retains the first approval. Stage 4 receives the
same equal string and authorizes it. The approval record has no source-root key
or other unique matter identifier that either layer could compare.

This was reproduced through the real offscreen `MainWindow.start_run` path. An
approval captured for one root ending in `Matter` remained in `_approvals` when
the next request used a different root also ending in `Matter`. Passing that
retained approval to the current Stage-4 code dropped a page and recorded
`matter="Matter"`. The log is internally consistent and still cannot show that
the approval came from another folder.

The five new B-1/B-2 tests are all in `tests/test_sections.py`; none drives the
requested **summary -> New run -> different matter folder** boundary, and none
uses two distinct roots with the same basename. The distinct-string tests prove
the comparison, not the scope.

### Ruling on the divergence

**Matter-scoped retention is approved; basename-scoped retention is not.**

Retain approvals when the incoming request identifies the same matter and clear
them when it does not. The scope must be collision-resistant within ordinary
use—for example, a normalized source-root identity carried separately from the
human-readable matter name and enforced at both the GUI and Stage-4 boundaries.
If no such scope is added, the safe alternative is to discard approvals whenever
`source_root` changes, even when the two basenames match; same-source rebuilds
may still retain them. Add the real GUI fail-before with two different roots
sharing one basename, and prove the second corpus remains entirely KEEP.

## D-1 — CLOSED

A-18's stale “RAISED, NOT APPLIED” paragraph now records that it described the
one pre-wiring commit and became false at `4092f76`. The entry consistently says
APPLIED and states that recognition tiers reach `processing_log.json`, including
on an unengaged run that drops nothing.

**Location:** `docs/contracts/amendments.md:1302-1315`

## Verification performed

- Read the R2 handoff and reviewed `444b4c5..1446bb3`, including all six changed
  production/documentation files and the affected tests.
- The focused sections/adapter/end-to-end/GUI/identity/emit slice exited 0;
  `tools/check_amendments.py` reported 22 entries, all applied ones wired.
- One complete `python -m pytest -q` run collected **1,499 tests** and exited 0:
  **1,498 passed and one intentional seam-population skip**. No failure or error
  appeared.
- `python -m dociq.selftest` exited 0 with **70 checks** and eight sequential
  determinism runs at one corpus hash.
- The original B-1 counterexample now keeps pages 5-10. Distinct matter strings,
  template ids, and template versions are refused as claimed.
- A new ordinary-use counterexample reproduced B-2's remaining basename
  collision through the real offscreen window and the current Stage-4 API.
- The fix-only range passes `git diff --check`. The complete range reports six
  trailing spaces used as Markdown hard breaks in my prior Codex verdict; they
  predate and are unrelated to the fix implementation.
- No human mouse-driven GUI acceptance was performed.

## Next review condition

Give approvals a matter scope that distinguishes different source roots with
the same display name, enforce it before Stage 4, and add the real GUI
same-basename/different-root fail-before. Then request the next fix-round review.
