# The red tree on `build/sprint-3`, counted properly

**Date:** 2026-08-17
**Branch:** `build/sprint-3`
**Corrects:** commit `0f71aef`'s message, which says *"11 items fail — 6 in
tests/test_profiles.py and 5 in tests/test_adapter.py"*. **That figure is wrong
and is withdrawn.** The real figure is **79 failing items across 8 files.**

## How the wrong number was produced

Three attempts to count the full suite in a background shell wrote **empty**
output files. The shell had lost its working directory and every one of them
died on `./.venv/Scripts/python.exe: No such file or directory` — which never
reached me, because I was reading the redirected output rather than the exit
path. Seeing nothing, I fell back on a number I *had* measured cleanly:

```
pytest tests/test_profiles.py tests/test_adapter.py -q | grep -cE "^(FAILED|ERROR)"
```

That command is correct and returns 11. It is also **scoped to two files**,
chosen because they were the two visible in an earlier `tail`-truncated summary.
I reported its answer as the blast radius of the whole suite.

**This is the "a green result proves nothing" failure wearing different
clothes.** Empty output was treated as *inconclusive but survivable* instead of
as *the measurement did not run*, and a narrower measurement was promoted to
answer a broader question without re-deriving it. The rule that would have
caught it is the one already on the books: a figure quoted to anyone must come
from a run that was watched producing it.

Two further truncation traps sat underneath, and both flattered the number:
`pytest -q | tail -N` puts the pass/fail summary line ABOVE the FAILED/ERROR
list, so every `tail` I used cut the summary off entirely; and a `tail -30` that
returns exactly 30 lines is a floor, not a total.

## The real figure

**79 items: 74 FAILED, 5 ERROR.**

| file | items |
|---|---:|
| `tests/test_package_swap.py` | 29 |
| `tests/test_emit.py` | 24 |
| `tests/test_end_to_end.py` | 7 |
| `tests/test_adapter.py` | 6 |
| `tests/test_profiles.py` | 5 |
| `tests/test_run_identity.py` | 3 |
| `tests/test_contracts.py` | 3 |
| `tests/test_terminal_status_rendering.py` | 2 |

## What they are, which is not 79 separate problems

Three distinct causes, and the diagnosis is what makes the number actionable.

**1. The A-18 tightening, hit through the old engine — the large majority.**
`ContractViolation: page N: DROP without a section_tier`. These pages are built
by `profiles/apply.py` during real emit and end-to-end runs, not by test
fixtures: extending `tests/fixtures.py`'s `page()` helper to default a tier on
DROP changed the count by **zero**, which is the measurement that establishes
it. The engine swap is the fix, exactly as `0f71aef` argued — that part of the
commit message stands.

**2. A version-string assertion.** `assert CONTRACT_VERSION == "1.6.0"` in
`tests/test_contracts.py`, now 1.7.0. One line.

**3. An existing guard doing its job.**
`tests/test_terminal_status_rendering.py` enumerates every enum in the contract
and refuses one it has never scanned:

> `enumeration(s) this probe has never scanned: ['RecognitionTier', 'Risk'] —
> an enum nobody enumerated is finding A-3 waiting to happen.`

That is a probe written in an earlier sprint catching a new enum on the first
run after it was added. It needs both enums added to `ENUM_NAMES` **and their
renderers checked**, which is the part the message asks for and the part that is
easy to skip.

## What is unchanged by this correction

The 50 tests in `tests/test_sections.py` pass, both fail-befores were watched
red, and the coupling argument in `0f71aef` — that A-18 and the engine swap
cannot land separately — is confirmed rather than weakened by cause 1. What
changed is only the size of the remaining rewiring, and the honesty of the
number attached to it.
