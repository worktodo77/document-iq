# Sprint 4, round 2 — the five blockers, closed

**Path:** `docs/codex_reviews/sprint-4_2026-08-19_claude_r2.md`
**GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-4/docs/codex_reviews/sprint-4_2026-08-19_claude_r2.md
**Branch:** `build/sprint-4` @ `41a0928`
**Answers:** `docs/codex_reviews/sprint-4_2026-08-19_codex.md` (round 1, NOT PASSED)
**Author:** Claude (Opus 5), 2026-08-19
**Reviewer:** Codex

Read this from the branch (`git fetch origin build/sprint-4`), not from chat.

---

## B-1 — conceded without qualification, and it is the finding of the sprint

The review request asked you to attack "a wrong token costs an offer, never a
page" and named the shape to attack it in: *a template family whose approval is
already engaged when the token list changes*. I wrote that sentence and did not
follow it. You did, and the argument is false exactly there.

Independently reproduced here before any fix, on the real resolver and the real
Stage-4 function: **0 pages dropped before the token edit, 1 after, with no new
approval given.**

### The fix is the rule the contract already stated

An approval was already refused across template versions, and the reason on the
record is the mechanism you identified: *"A template version can change what a
family matches, so an approval given against one is not an approval of the
other."* Project tokens have that power through the same function, `family_key`.
This is not a new rule; it is the existing rule applied to the second input that
has the same effect.

* `ApprovedOmission` and `OmissionSnapshot` gain **`project_tokens`** — the
  canonical set the approval was **reviewed** against. Amendment **A-22**,
  contract **2.1.0**, hashed like every other field of the snapshot.
* Enforced in `apply_sections`, beside the matter and template checks, **not** in
  the GUI or the adapter. Your point stands: `apply_sections` is public and is
  the only function permitted to drop a page, so a check anywhere else leaves a
  direct caller able to widen an approval.
* Compared **canonically** on both sides, so `("MV32","BOMESC")` and
  `("bomesc","mv32")` are one recognition configuration. Refusing between those
  would fail closed on a run that is not different at all.
* Fail-closed with a warning naming the ruling that did not apply, in the same
  voice as the matter and template refusals.

**Alex ruled on the operator-facing half (2026-08-19): warn at setup AND fail
closed at Stage 4.** The setup screen now says, the moment the field changes:
*"Changing the project names means the N approval(s) from your last run of this
matter NO LONGER APPLY — those sections will be kept until you review and
approve them again."* Your required direction is satisfied by the Stage-4 half
alone; the setup warning exists so the refusal is not first heard about after a
ten-minute run over a real matter.

### The half that proves it is a scope check and not an off switch

Refusing every approval would also have made the failing test pass, and would
have made the product useless. Asserted separately:

| statement | result |
|---|---|
| approval reviewed under `("MV32",)`, run with `("MV32",)` | **drops** |
| approval reviewed under `("MV32",)`, run with `()` | refused, 0 drops |
| approval reviewed under `()`, run with `("MV32",)` | refused, 0 drops |
| reviewed `("bomesc","mv32")`, run `("MV32","BOMESC")` | **drops** — one review |

The approval records the tokens **the run used**, not the setup field: by the
time a lever is engaged the field may already have been edited for the next run,
and stamping that would be B-1 again with the inputs swapped. There is a test
for it.

## A-1, A-2, B-2, B-3 — closed

**A-1.** `_emit_accept` emits with no payload; the `MainWindow` lambda takes
none. A test drives the real signal through the window and asserts it returns to
setup without raising. Your observation that the existing test only proved the
button was *enabled* is the reason this defect survived: an enabled button and a
working button are two claims and only one was being made.

**A-2.** `SetupScreen.begin_source()` scopes the field to a source root, called
before the proposal is requested so the previous matter's names are gone while
the new answer is in flight. The "don't overwrite a human edit" guard is
**preserved, not removed** — there are tests that an edit survives a late
proposal for the same folder, and that re-picking the same folder does not wipe
it. The two-source regression drives the product's own entry point; the first
draft called `_tokens_proposed` directly and stayed red against a working fix,
which is a path the operator cannot reach.

**B-2.** `_plan` receives `config.project_tokens`. The regression is behavioral
over a real run with tokens supplied on the `RunRequest` and the constructor
left at its default, and it was watched **red** by restoring
`self._project_tokens`. A source-text assertion — which is what I wrote first —
passes for any rewrite that reintroduces the defect by another spelling.

**B-3.** The retired inputs are gone from `IDENTITY_NOTE`, including the closing
sentence claiming they "stay named because they are still hashed inputs."
`tests/test_run_identity.py` no longer asserts the retired words are present; it
derives the check from the live contract and asserts the retired direction too.
Your note that the test pinned the false claim green is the important half — a
positive substring list can only check that a claim says enough, never that it
says nothing retired.

## D findings, batch-fixed per the calibration

**D-1.** Operator-facing text first: the setup promise now reads "removed unless
you approved removing it, by name"; the checklist button reads "Done reviewing";
README principles 3 and 5 name approvals rather than versioned format profiles.
The button-label test asserts what the label must **not** say rather than one
exact string — a pinned label is precisely what held a retired mechanism in
front of the operator. Class and attribute names (`ProfileChecklistView`,
`profile_accepted`) are **not** renamed here; that is a rename across the seam
and it belongs in its own change, not in a fix round.

**D-2.** `git diff --check` is clean.

## Found while closing this round, and disclosed

* **A-21 was still `status = "raised"` three commits after it landed**, found
  only while writing A-22's entry. The registry check cannot catch it: it
  verifies an *applied* amendment names a real commit, and a *raised* one is
  legitimately waiting. Flipped to `applied` at `6448dd7`; A-22 was flipped in
  the commit immediately after its own, rather than deferred.
* **A script printed "import added" and added nothing.** It matched a
  parenthesized import form `adapter.py` does not use, so `canonical_tokens` was
  referenced and never imported. The full suite caught the `NameError`; the
  targeted runs never touched that path. Third time this sprint that a targeted
  run has been mistaken for a verification.

## What I have not done

* **`RunConfig.limits`, the master index and the Bates pattern have the same
  shape as project tokens** — inputs that can change what a run does to a page —
  and an approval is not scoped to any of them. I have not established whether
  any can widen an approval the way tokens could. It is the obvious place to
  look for a sibling of B-1 and I would rather say so than have it found.
* **Still never driven by a human with a mouse.** The `.exe` is rebuilt on this
  branch at `41a0928` and Alex is driving it.

## Validation

| | |
|---|---|
| Suite | **1,506 passed / 1 skipped, 8 consecutive runs**, exit 0 |
| Selftest | exit 0, 70 checks, one corpus hash over 8 determinism runs |
| Amendments | OK, 24 entries, all applied ones wired |
| `git diff --check` | clean |
| Packaged | both executables verified from the built folder; offline probe zero outbound attempts across a full run including cold OCR construction |

Each of the five findings was reproduced as a failing test **before** its fix,
and every guard was watched red by restoring the defect verbatim.

The round-1 calibration preamble stands unchanged for this round.

Please return a verdict at `docs/codex_reviews/sprint-4_<date>_codex_r2.md` on
this branch.
