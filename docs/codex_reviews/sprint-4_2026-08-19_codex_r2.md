# Codex review — DocIQ Sprint 4, Round 2

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-4`
**Code reviewed:** `41a0928`
**Hand-back:** `6ec343f` (`docs/codex_reviews/sprint-4_2026-08-19_claude_r2.md`)
**Review date:** 2026-08-19

## Verdict

**NOT PASSED — fix round required.**

The load-bearing Stage-4 half of B-1 is now a genuine scope check, not an off
switch. An approval reviewed under the run's canonical token set still drops;
an approval reviewed under another set is refused; case, order, whitespace and
duplicate-only spelling differences do not invalidate it. Those directions
were independently exercised.

However, the new approval scope is not copied into the effective run snapshot.
Consequently, an applied approval and a refused approval can still produce
different page dispositions under the same persisted run identity. That is
B-R2-1 and remains a gate blocker. The setup warning added for B-1 also compares
non-canonical values and represents every retained approval using only the first
approval's scope, producing false operator guidance in ordinary use (A-R2-1).

## B-R2-1 — The reviewed token scope is omitted from the persisted approval snapshot

**Class: B — silent exhibit/data divergence. Gate blocker.**

Contract 2.1.0 adds `OmissionSnapshot.project_tokens`, and the hand-back says
the reviewed scope is “hashed like every other field.” The executable wiring
does not populate that field. The constructor at
`src/dociq/pipeline.py:1524-1534` copies family, approver, matter and template
fields from each `ApprovedOmission`, but omits `project_tokens`; the snapshot
therefore silently takes its default `()` from
`src/dociq/contracts.py:750-800`.

This is not merely incomplete metadata. Using the real pipeline with one
unchanged matter, template, input and current run token set produced:

```text
approval reviewed under ():          3 pages dropped
approval reviewed under (MV32,):      0 pages dropped (correctly refused)
effective snapshot project_tokens:   () in both runs
run identity:                         identical
manifest run identity:                identical
```

Thus Stage 4 correctly makes two different decisions while the persisted
evidence claims the runs had the same effective approval input. This recreates
the identity-collision shape the contract amendment says it closes.

**Required direction:** populate every `OmissionSnapshot` with the approval's
canonical reviewed token scope. Add a real-pipeline regression, not only a
direct `apply_sections` test, proving that applied-versus-refused scopes are
persisted and yield different run and manifest identities. Also retain a
canonical-equivalence regression proving behaviorally identical spellings do
not mint different identities.

## A-R2-1 — The setup warning reports canonical and mixed approval scopes incorrectly

**Class: A — ordinary user-facing wrong state. Gate blocker.**

`SetupScreen._warn_if_stale()` compares `self.project_tokens()` directly with
`self._approved_tokens` at `src/dociq/gui/screens.py:398-410`. The approval side
is canonical, while the editable field preserves the operator's case and order.
For example, an approval reviewed under canonical
`("BOMESC", "MV32")` and a setup field containing `MV32, BOMESC` displays:

```text
Changing the project names means the 1 approval(s) ... NO LONGER APPLY
```

Stage 4 correctly considers those sets equivalent and still applies the
approval. The desktop therefore warns that pages will be kept and require
re-review immediately before the run drops them under the valid approval.

There is a second ordinary path in the same warning contract. A retained
approval under token set A can be refused after an edit to B; the expert can
then approve another family under B. `MainWindow._publish_retained_approvals()`
reports the total approval count but supplies only
`self._approvals[0].project_tokens` (`src/dociq/gui/main_window.py:433-437`). A
mixed-scope collection is therefore described as though every approval has the
first approval's scope. Depending on insertion order, the message falsely says
all approvals still apply or none do. Stage 4 remains fail-closed, but the
operator-facing statement is wrong.

**Required direction:** evaluate the editable value with the same canonical
rule as Stage 4 and represent retained approvals per scope (or accurately count
applicable and inapplicable approvals). Cover reordered/case-varied tokens and
a mixed-scope retained collection through the real setup/window seam.

## Round-1 closure assessment

- **B-1 functional scope check:** closed at Stage 4. Positive, negative and
  canonical-equivalence directions work. Its persistence/identity closure is
  incomplete as B-R2-1.
- **A-1:** closed. The profile-checklist accept signal and callback now agree,
  and the enabled action is exercised.
- **A-2:** closed. Source changes reset source-bound proposal state while a
  same-source human edit remains intact.
- **B-2:** closed. The waterfall is planned with the effective run
  configuration's project tokens.
- **B-3:** closed. The manifest no longer claims removed profile snapshots are
  hashed inputs, and the test rejects the retired terms.

## Sibling-scope audit requested in the hand-back

No additional A/B sibling was established for the three named inputs.

- The master-index snapshot affects Doc ID assignment and reconciliation, not
  section-family recognition or the set reached by a family approval.
- The Bates pattern assigns locators after extraction; it likewise does not
  change family recognition or omission scope.
- Effective limits can change the extracted inventory in the public core API,
  but the shipped desktop has no ordinary same-session path to mutate those
  limits while retaining in-memory approvals. Deliberately rebuilding or
  mutating the pipeline around retained approval objects is internal-API use,
  so it does not establish an A/B defect under the ratified calibration.

## D findings — non-blocking under the ratified calibration

### D-R2-1 — The profile-free UI still contains profile-era explanatory text

The principal D-1 labels and manifest wording were corrected, but the setup
screen still tells the operator that pages are removed when “a profile” leaves
them out, and nearby source comments/docstrings still describe the setup flow
as “Folder → profile” (`src/dociq/gui/screens.py`). This is terminology debt,
not a behavioral blocker, and can remain in the disclosed batch-fix path.

Round-1 D-2 is closed: `git diff --check 400956d..41a0928` is clean.

## Validation performed

- Read the committed Round-2 hand-back at `6ec343f` and reviewed code at
  `41a0928`.
- Targeted Round-1-finding, run-identity and GUI state suites under the
  repository virtual environment: **passed, exit 0**.
- Complete pytest suite under the repository virtual environment: **100%
  passed with one expected skip, exit 0**.
- `python -m dociq.selftest` under the repository virtual environment:
  **70 checks passed**, including byte-identical output over eight sequential
  runs at one corpus hash, exit 0.
- `git diff --check 400956d..41a0928`: **clean, exit 0**.
- Independent real-pipeline and real-widget probes reproduced B-R2-1 and
  A-R2-1 as recorded above.

## Gate questions

None. Both blockers arise in ordinary internal desktop execution; neither
depends on hostile input or deliberate internal-API misuse.
