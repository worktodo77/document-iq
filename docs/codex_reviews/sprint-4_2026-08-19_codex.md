# Codex review — DocIQ Sprint 4

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-4`
**Code reviewed:** `400956d`
**Review request:** `053295b` (`docs/codex_reviews/sprint-4_2026-08-19_claude.md`)
**Review date:** 2026-08-19

## Verdict

**NOT PASSED — fix round required.**

The load-bearing D-39 argument is wrong in the exact shape the handoff asks the
review to attack. An approval is scoped to matter and template version, but not
to the project-token set that decides which labels enter the approved family.
The GUI deliberately retains approvals for the next same-matter run. Editing
the tokens between those runs can therefore make previously unapproved pages
match an already-approved family and DROP without a new approval.

That is B-1 below and is independently reproduced. Four additional A/B findings
also require a fix round: the profile-free checklist's forward button crashes;
tokens from one matter survive into another; the post-run waterfall recomputes
families with a different token set from the run; and every manifest still
claims that removed profile snapshots participate in the run identity.

## B-1 — A project-token edit widens an existing approval and can newly drop pages

**Class: B — silent exhibit/data divergence. Gate blocker.**

D-39 says a wrong token can cost an offer but cannot lose a page because a match
is only an offer under D-34. That is true only for a run with no existing
approval. It is false for the ordinary workflow the product implements:

1. Run a matter and engage a family on the resulting waterfall.
2. Return to setup. `MainWindow` retains the approval for the next run of the
   same matter (`src/dociq/gui/main_window.py:378-417`, `:438-457`).
3. Correct the editable project-token field, which D-39 explicitly requires.
4. Run again. The retained approval is selected only by `family_id` at Stage 4
   (`src/dociq/sections/apply.py:139-181`, `:196-227`). The approval record is
   scoped to matter and template version but carries no project-token identity
   (`src/dociq/sections/model.py:257-296`).

The token set changes `family_key`, so it changes the set of pages reached by
the old approval. The code already recognizes the analogous rule for a template
version: an approval is refused across versions because the version can change
what a family matches. Project tokens have the same effect and are not checked.

Independent minimal reproduction, using the real section resolver and Stage-4
apply function:

```text
label: MV32 TABLE OF CONTENTS
approval: table-of-contents, unchanged
before token edit, project_tokens=():       0 pages dropped
after token edit, project_tokens=(MV32,):   1 page dropped
```

No new approval occurs between those results. This is not hostile input or
internal-API misuse: editing a missed token such as BOMESC or YARD after a run
is the corrective workflow D-39 depends on.

**Required direction:** an approval must be bound to the canonical recognition
configuration it was reviewed against, including `project_tokens`, or token
changes must invalidate the approvals and require a fresh recognition-only run
and review before any omission can apply. Merely hashing both inputs in the run
identity distinguishes the resulting corpora; it does not authorize the new
drops.

## A-1 — The template-checklist forward button dereferences the deleted profile

**Class: A — ordinary user-facing crash. Gate blocker.**

D-38 removed `ProfileChecklistView.profile`, but
`ProfileChecklistScreen._emit_accept()` still executes:

```python
self.profile_accepted.emit(self._view.profile)
```

at `src/dociq/gui/screens.py:1150`. The signal is now declared with no payload at
`:981`, while `MainWindow` still connects it to a one-argument lambda at
`src/dociq/gui/main_window.py:283-284`, so the whole callback contract is stale.

Reproduced through `MainWindow(RealPipeline())`: open **Review section
template**, then invoke the enabled forward action. It raises:

```text
AttributeError: 'ProfileChecklistView' object has no attribute 'profile'
```

The existing real-product-path test proves only that the button is enabled; it
never clicks it. The fix needs an offscreen test that drives the actual signal
through the window and proves the screen returns to setup without an exception.

## A-2 — Selecting a second matter silently retains the first matter's tokens

**Class: A — ordinary user-facing wrong state. Gate blocker.**

Choosing a source starts a new proposal (`src/dociq/gui/main_window.py:255`),
but it does not clear or re-scope the token field. When the second matter's
proposal arrives, `SetupScreen.set_proposed_tokens()` refuses to write it
whenever the field is already non-empty (`src/dociq/gui/screens.py:339-347`).
That rule correctly protects a human edit within one matter, but it also treats
matter A's proposal as a human edit belonging to matter B.

Reproduced through the real setup widget:

```text
source A proposal: MV32
switch source to B
source B proposal: MI20
tokens shown and sent by SetupScreen.request(): MV32
```

Thus a normal **Start another run → choose another folder** workflow records and
hashes A's names as B's configuration while discarding B's derived answer. The
stale-worker guard compares the returned source to the current source, but it
does not address already-populated field state.

**Required direction:** associate proposal/edit state with its source root. A
source change must reset the prior matter's value and hint while continuing to
protect edits made for the currently selected source. Add a two-source GUI
regression whose first proposal has already populated the field.

## B-2 — The run and its post-run waterfall use different project-token sets

**Class: B — silent data/presentation divergence. Gate blocker.**

`RealPipeline.run()` correctly builds the run configuration from
`request.project_tokens` at `src/dociq/adapter.py:887`. But after publication it
builds the waterfall with `self._project_tokens` at `:935-940`. In the shipped
GUI the adapter constructor uses its default empty tuple, while the setup screen
supplies the operator's values through the request.

Focused reproduction of `_plan()` over a page whose recorded section is
`MV32 TABLE OF CONTENTS`:

```text
plan with adapter default ():        family='', kind='recognized' (locked/kept)
plan with the run's token (MV32,):    family='table-of-contents', kind='expert'
```

The run classifies and can drop using the second answer, while the screen is
handed the first. On an approved rerun, the output can therefore contain a
DROP that the waterfall reconstructs as an unknown, non-engageable recognized
row. This also deprives the expert of the actual offer after an unapproved run.

Pass the effective run configuration's canonical `project_tokens` to `_plan`,
and add a real-adapter regression that supplies tokens on `RunRequest` rather
than on the adapter constructor.

## B-3 — `output_manifest.json` falsely claims removed profile snapshots are hashed

**Class: B — false evidentiary claim. Gate blocker.**

D-38 removed `RunConfig.profiles`, but the persisted `claim_identity` text still
says the identity covers the ordered tuple of profile snapshots and each
`profile_hash` (`src/dociq/verify/manifest.py:115-129`). Its explanation goes
further and says those snapshots “stay named because they are still hashed
inputs” at `:163-171`. They are not fields of `RunConfig` in contract 2.0.0 and
cannot participate in `run_identity`.

This is not an inert source comment: `Manifest.to_dict()` writes
`IDENTITY_NOTE` as `claim_identity` in every `output_manifest.json`. The test is
also stale in the dangerous direction: `tests/test_run_identity.py:176-189`
still requires `profile` and `profile_hash`, so it pins the false claim green.

Remove the retired input from the persisted claim and replace the positive
substring test with a check derived from the current identity projection that
also rejects removed fields.

## D findings — non-blocking under the ratified calibration

### D-1 — Profile terminology remains throughout the profile-free product

D-38's behavior removal is incomplete as a terminology/documentation sweep.
Examples include the setup promise that pages are removed only when a “profile
you approved” says so (`src/dociq/gui/screens.py:157`), the checklist class and
button text **Use this profile** (`:967-1041`), README principles that still make
versioned format profiles the omission mechanism (`README.md:13-15`), and the
contract freeze's current identity description (`docs/contracts/pagemodel_freeze.md:147-164`).
The stale callback in A-1 is a blocker; the remaining wording is D-class and can
be batch-fixed at hand-back.

### D-2 — The patch fails `git diff --check`

`git diff --check 40016e5..400956d` reports whitespace issues in:

- `src/dociq/sections/project_tokens.py:142`
- `tests/test_end_to_end.py:888`
- `tests/test_run_identity.py:488`
- `tests/test_seam_population.py:385`

These are process-only and do not affect the verdict.

## Validation performed

- Read the committed request at `053295b` and reviewed code at `400956d`.
- Targeted project-token, canonical-form, section, contract, emit, and adapter
  tests under the repository virtual environment: **passed, exit 0**.
- Full suite under the repository virtual environment: **1,493 passed / 1
  skipped in 370.67s, exit 0**.
- `python -m dociq.selftest` under the repository virtual environment:
  **70 checks passed**, including byte-identical output over eight sequential
  runs at one corpus hash, exit 0.
- Independent probes reproduced B-1, A-1, A-2, and B-2 as recorded above.
- A first full-suite attempt under the system Python was discarded as
  non-evaluative because it lacked the project's declared dependencies; the
  repository virtual environment was used for evaluative runs.

## Gate questions

None. All blockers above arise in ordinary internal desktop use; none depends
on hostile input or deliberate internal-API misuse.
