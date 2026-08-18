# Codex fix-round 2 review — DocIQ Sprint 3

**Repository:** `worktodo77/document-iq`  
**Branch:** `build/sprint-3`  
**Reviewed commit:** `5d60ad3` (fix implementation `e90a4cf`)  
**Fix-round handoff:** `docs/codex_reviews/sprint-3_2026-08-18_claude_r3.md`  
**Prior verdict:** `docs/codex_reviews/sprint-3_2026-08-18_codex_r2.md`  
**Review date:** 2026-08-18

## Fix-round verdict

**PASSED — B-2's residual is closed; B-1 and D-1 remain closed.**

The implementation now carries one canonical source-root key from approval
capture through the window's retention decision and into Stage 4. Distinct
roots with the same final folder name no longer share an authorization scope.
The approved divergence therefore has the intended behavior: an approval may
survive a rebuild of the same matter, but is discarded on a different matter,
including the basename-collision case from the prior review.

One non-blocking documentation finding, D-2, is recorded below. It does not
change the Sprint 3 gate result.

## B-2 — CLOSED

**Prior residual:** the approval scope was a display basename, so approvals for
`C:/Client-A/Production` and `D:/Client-B/Production` were treated as belonging
to the same matter.

**Re-verification:** `matter_key()` is now the single derivation for the scope
key. It normalizes case and converts relative paths to absolute paths without
requiring filesystem resolution. The human-readable `matter` remains separate.

The requested boundary is enforced at all three seams:

1. `MainWindow._capture_approval()` supplies the selected `source_root`, and
   `RealPipeline.set_omission()` refuses an empty root and stores its
   `matter_key()` in `OmissionApproval.matter_root`.
2. `MainWindow.start_run()` derives the incoming request's key with the same
   function and retains only approvals whose `matter_root` equals it.
3. Stage 4 derives the current key with the same function, refuses approvals if
   no current root was supplied, and keeps the page when an approval's root
   differs. The run snapshot also persists `matter_root`.

**Locations:** `src/dociq/contracts.py:650-708`,
`src/dociq/adapter.py:667-696`, `src/dociq/gui/main_window.py:286-358`,
`src/dociq/sections/apply.py:98-165`, `src/dociq/sections/model.py:279-324`,
`tests/test_sections.py:804-841`

An independent offscreen probe exercised the real capture and window paths,
then the Stage-4 API. An approval captured for `C:/Client-A/Production` was
retained for that exact root, discarded for `D:/Client-B/Production`, refused
by Stage 4 on the latter root, and applied on the former. The colliding display
name remained `Production` throughout, so this witnesses root scoping rather
than a distinct-name shortcut.

### Ruling on the flagged divergence

**Accepted and correctly implemented.** Clearing approvals on every **New run**
would discard the summary-screen decision before the same-matter rebuild could
apply it. Retaining by canonical `matter_root`, while discarding on a different
root, preserves the workflow without allowing one matter's approval to govern
another. Aliased paths that cannot be equated lexically can cause a safe
false-negative (discard and re-approval), not an unauthorized drop.

The committed regression proves both Stage-4 directions: the colliding root is
refused and the approved root still applies. The real-window boundary is not a
committed regression test, but the independent probe above verifies the current
capture and retention behavior.

## B-1 — REMAINS CLOSED

The non-monotonic outline boundary fix is unchanged. Its focused section tests
pass, including the original counterexample: pages 1-4 may be omitted under the
approved family, while pages 5-10 remain KEEP and page 11 resumes the next
recognized section. No B-1 regression was found.

## D-1 — REMAINS CLOSED

A-18's prose still consistently records that the earlier “RAISED, NOT APPLIED”
state ended when `4092f76` wired recognition tiers into the processing log.
The amendment registry and contract checks remain green.

**Location:** `docs/contracts/amendments.md:1302-1315`

## D-2 — OPEN, NON-BLOCKING

The code-level contract history correctly records contract 1.9.0 as **A-19
extended** and says that `OmissionSnapshot` gained `matter_root`. The
authoritative A-19 prose and registry were not extended with it, however:

- A-19's “What lands” field list still ends with `matter`, `template_id`, and
  `template_version`, omitting `matter_root`.
- The TOML entry still records only adoption in `4092f76` and wiring through
  `OmissionSnapshot` plus `_inert_profile_warnings`; it does not identify the
  `e90a4cf` extension or the capture/retention/Stage-4 enforcement sites.

**Locations:** `docs/contracts/amendments.md:1316-1354`,
`docs/contracts/amendments.toml:218-227`, `src/dociq/contracts.py:198-199`

Update the A-19 prose and machine-readable record so the durable amendment
record describes the field set and enforcement that contract 1.9.0 actually
ships. Per the standing review convention for class-D documentation findings,
this may be batch-fixed and does not require another Codex review.

## Verification performed

- Read the R3 handoff from `build/sprint-3` at `5d60ad3` and reviewed the full
  fix range from the prior reviewed head, `1446bb3..5d60ad3`.
- Traced `matter_root` through capture, approval conversion, GUI retention,
  pipeline snapshot construction, and Stage-4 enforcement.
- Ran the focused sections/contracts/adapter/seam/run-identity/end-to-end slice:
  exit 0, with its one documented seam-population skip.
- Ran the complete suite: **1,500 tests collected**, exit 0, with one intentional
  skip and no failures or errors.
- Ran `python -m dociq.selftest`: exit 0, **70 checks**, including eight
  sequential determinism runs with one corpus hash.
- Ran `tools/check_amendments.py`: 22 entries reported OK. D-2 is a semantic
  extension omission that the current registry checker does not detect.
- Ran `git diff --check 1446bb3..HEAD`: clean.
- No human mouse-driven GUI acceptance was performed.

## Gate result

Sprint 3 passes this fix-round review. B-2 is closed at the actual matter-root
boundary, and B-1 and D-1 remain closed. Only the non-blocking A-19 record update
in D-2 remains.
