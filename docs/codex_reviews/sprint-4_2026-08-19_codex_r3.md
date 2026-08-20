# Codex review — DocIQ Sprint 4, Round 3

**Repository:** `worktodo77/document-iq`
**Branch:** `build/sprint-4`
**Code reviewed:** `e8a9a7c`
**Hand-back / A-23 flip:** `d8ef75c`
**Completed verification follow-up:** `3c536a1`
**Review date:** 2026-08-19

## Verdict

**NOT PASSED — fix round required.**

B-R2-1 is closed: the real pipeline now copies the approval's canonical
`project_tokens` and recognition fingerprint into `OmissionSnapshot`, and
applied versus refused approvals move both the disposition and persisted run
identity. A-R2-1's two reported directions are also corrected: reordered and
recased tokens remain applicable, and mixed retained scopes are counted per
approval rather than represented by the first member.

Two A findings remain. The new fingerprint wiring crashes the documented
optional-options pipeline path, and withdrawing the final retained approval
leaves a false approval warning on the setup screen. Both reproduce in ordinary
internal use and therefore require another fix round under the ratified
calibration.

## A-R3-1 — The default `run(config)` path crashes at Stage 4

**Class: A — ordinary user-facing/runtime failure. Gate blocker.**

`PipelineOptions.walk` is optional, and `run(config, options=None)` explicitly
constructs `PipelineOptions()` at `src/dociq/pipeline.py:1215-1218`. Earlier in
the same run the implementation correctly derives the effective OCR state with:

```python
ocr_ran = (opts.walk or walker.WalkOptions()).ocr_enabled
```

The A-23 Stage-4 call does not use that effective value. It unconditionally
reads `opts.walk.ocr_enabled` at `src/dociq/pipeline.py:1472-1476`. A real run
over the repository fixtures with only a `RunConfig` reaches Stage 4 and fails:

```text
run(RunConfig(source_root=fixtures, output_root=out))
AttributeError: 'NoneType' object has no attribute 'ocr_enabled'
```

This does not require a hostile option object or internal mutation; it is the
public function's declared default invocation. The full suite stays green
because its completed-run cases supply explicit walk options.

**Required direction:** compute the recognition fingerprint from the already
derived effective `ocr_ran` value (or an equivalently normalized walk option),
and add a completed real-pipeline regression that calls `run(config)` without
`PipelineOptions`.

## A-R3-2 — Withdrawing the last approval leaves a false retained-approval message

**Class: A — ordinary user-facing wrong state. Gate blocker.**

The corrected seam passes one token scope per retained approval. When the last
approval is withdrawn, `MainWindow._publish_retained_approvals()` correctly
calls `set_retained_scopes(())`. But `_warn_if_stale()` returns immediately for
an empty collection at `src/dociq/gui/screens.py:416-417` without clearing the
message it previously wrote.

Reproduced through a real `MainWindow` and setup widget:

```text
one retained approval:  1 approval(s) carried ... still apply.
withdraw last approval: 1 approval(s) carried ... still apply.
```

The setup screen therefore tells the operator an approval remains after the
collection is empty. The same stale result occurs whether the prior message
said the approval still applied or no longer applied.

**Required direction:** make the zero-scope transition remove the retained-
approval status without erasing unrelated token-proposal guidance. Add a
real-window regression that publishes one scope, then publishes none and
asserts no retained-approval claim remains.

## A-23 assessment

The substantive fingerprint directions requested in the hand-back work:

- a matching non-empty fingerprint still applies an approval;
- an OCR mismatch changes the fingerprint and refuses the approval;
- token case, order, whitespace and duplicates are canonicalized before
  fingerprinting;
- an approval with the pre-2.2.0 empty fingerprint falls back to its named
  matter/template/token scope rather than being automatically voided;
- the fingerprint survives through a real pipeline run into the hashed
  omission snapshot; and
- applied and stale fingerprints produce different run identities.

This does not recreate A-R2-1 as a second invalidation rule on equivalent
recognition inputs.

## D findings — non-blocking under the ratified calibration

### D-R3-1 — The undefined-name AST guard has a broad scope-blind false negative

The new test builds one module-wide `defined` set. Every function argument,
local assignment, comprehension target, `with` target and `except` target from
every nested scope is added to that one set (`tests/test_import_graph.py:300-347`).
It therefore misses a bare global lookup when the same spelling is merely local
somewhere else in the module. Minimal shape:

```python
def unrelated():
    missing_import = 1

def broken():
    return missing_import()
```

`broken()` raises `NameError`, but the guard sees the store inside `unrelated`
and treats every load of `missing_import` as defined. A function parameter,
comprehension target, `except ... as` name, or function-local import elsewhere
has the same effect.

The guard remains useful for the exact missing-import incidents when no local
shares the spelling, but it does not prove the stated general property. This is
test-harness correctness/process work (D), not a product blocker.

D-R2-1 is closed: the reviewed operator-facing profile-era strings and flow
docstrings are corrected. The reviewed range is also clean under
`git diff --check`.

## Validation performed

- Read the committed Round-3 hand-back at `d8ef75c`, its completed validation
  follow-up at `3c536a1`, and reviewed code at `e8a9a7c`.
- Targeted Round-1/Round-2 finding, fingerprint, import-graph, contract and
  run-identity tests: **passed, exit 0**.
- Complete pytest suite: **100% passed with one expected skip, exit 0**.
- `python -m dociq.selftest`: **70 checks passed**, including one corpus hash
  over eight sequential determinism runs, exit 0.
- `tools/check_amendments.py`: **25 entries, all applied entries wired**, exit
  0.
- `git diff --check 41a0928..e8a9a7c`: **clean, exit 0**.
- Independent real-pipeline and real-widget probes reproduced A-R3-1 and
  A-R3-2 as recorded above.

## Gate question

**C — asymmetric empty-fingerprint fallback:** Stage 4 compares fingerprints
only when both `approval.recognition` and the run's `recognition` are non-empty
(`src/dociq/sections/apply.py:180-181`). The intended compatibility case is an
old approval whose fingerprint is empty. The same condition also accepts a
modern, fingerprinted approval when a direct `apply_sections` caller omits the
run fingerprint, falling back to named fields and losing OCR scoping. The
shipped pipeline always supplies the run fingerprint, so reproducing this
requires deliberate public-core/API use rather than the desktop workflow and
is not classified as a defect. Please confirm whether an empty *run*
fingerprint should instead fail closed whenever the approval carries one.
