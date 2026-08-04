# Ruling D-30 — the one permitted spawn, the corrected record, and the class

**Branch:** `build/s2-r2-spawn` (off `build/s2-r2-swap`) · **Date:** 2026-08-04
**Ruling:** D-30 (Alex, 2026-08-04)
**Supersedes, in part:** `docs/codex_reviews/sprint-2_2026-08-04_claude_r2.md`
§"One thing still open, and it is the criterion-6 probe" — corrected in place,
original retained beneath the correction.

Measured, not expected. §6 names what nothing here supports.

---

## 0. What this is, in one paragraph

`test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN` failed intermittently
for three review rounds and was reported to Codex as an unexplained
**criterion-6 outbound-network risk**. It was not one. The probe printed a
COUNT of guard attempts and discarded the stack recorded on every one, so the
socket guard and the child-process guard arrived as a single number that the
assertion then called "outbound". The thing actually happening was
`platform.uname()` running `ver` through the shell during a dependency's import.
Alex ruled D-30: permit that one call by identity, keep everything else raising,
and correct the record. This note is the evidence for all three.

---

## 1. The exemption's exact shape

`src/dociq/verify/offline.py`. `PERMITTED_SPAWNS` is a tuple with **one**
`SpawnExemption`, and `enumerate_permitted_spawns()` renders it so a reviewer
can diff the set rather than read an `if`:

```
subprocess.Popen(ver | command /c ver | cmd /c ver, shell=True)
  from _syscmd_ver in c:\python314\lib\platform.py   [D-30 (Alex, 2026-08-04)]
```

**Six components, every one checked, every one load-bearing:**

| # | component | value | what it refuses |
|---|---|---|---|
| 1 | `entry_point` | `subprocess.Popen` | `os.system`, `os.startfile`, every `os.spawn*`/`exec*`/`fork*` |
| 2 | `commands` | exactly `ver`, `command /c ver`, `cmd /c ver` | any other command, and a call with no command at all |
| 3 | `shell` | `True` | the same string run without the shell |
| 4 | `caller_function` | `_syscmd_ver` | any other function, including others inside `platform.py` |
| 5 | `caller_file` | the stdlib's own `platform.py`, by absolute normcased path | code that defines its own `_syscmd_ver` |
| 6 | `caller_within_frames` | 4 | a caller that merely has `_syscmd_ver` somewhere far up its stack |

The command strings are **literals copied from CPython, not read from it at run
time**. A future CPython that changes them must fail this match and be looked
at, rather than being permitted automatically because the exemption follows
whatever the library now does.

**Refused shapes, named in the code and in D-30** — "allow spawns during
import", "allow short commands", "allow anything from site-packages". Each is a
CATEGORY, and a category readmits the whole class the child-process guard was
added for: the first permits any dependency to run anything at import time, the
second permits `curl x`, the third permits every dependency.

**The guard's founding argument is untouched.** A spawned child is an execution
the guard can no longer observe, and the conservative treatment of an
unobservable thing is to refuse it — that paragraph is still in the module
docstring, and every spawn but this one still raises for exactly that reason.
`_permitted_spawn` is consulted **only** for the child-process class; no
exemption exists, or may exist, for a socket, and
`test_no_exemption_may_be_a_socket` asserts it.

**It is recorded, not waved through.** A permitted spawn appends an
`ExemptedSpawn` — the exemption, the call detail, and the full stack — to
`guard.exempted`, and `NetworkGuard.render()` names it **even on a clean
report**. `clean` stays a function of `attempts` alone, because the ruling
permits the call; what may not happen is a report saying "clean" without saying
what it let through.

**The call actually runs.** Blocking it would not be a stricter guard, it would
be a different product: DocIQ would report `models-unavailable` for its OCR
engine identity whenever the guard happened to be the first thing to touch
`platform.uname()`.

### 1.1 Fail-before, watched

**The exemption itself.** With `offline.py` stashed,
`test_the_platform_version_probe_runs_and_is_recorded` goes red —
`ProcessSpawnAttempted`.

**Each narrowing.** Six perturbations, applied one at a time to
`_permitted_spawn`, each removing exactly one component:

| widened | rows that went RED |
|---|---|
| command check dropped | "a different command", "no command at all" |
| shell check dropped | "not through the shell" |
| entry-point check dropped | "a different entry point" |
| caller-name check dropped | "a different function in platform.py" |
| caller-file check dropped | "the right name, the wrong file", **and** the end-to-end impostor test |
| frame window unbounded | "the permitted caller too far up the stack" |

Restored: all green, and the restored file was `diff`ed **byte-identical** to
the original. `test_the_exact_permitted_call_still_matches` is present so the
matrix cannot be satisfied by a function that always refuses.

### 1.2 Rate, before and after

Isolated `pytest tests/test_offline.py`, this host:

| tree | red |
|---|---|
| branch base `5ab2a79` | **8 of 12** |
| `build/s2-r2-swap` tip (diagnosis, no exemption) | **2 of 12** |
| this tip (D-30 applied) | **0 of 12** |

---

## 2. The corrected record

### 2.1 The relay to Codex

`docs/codex_reviews/sprint-2_2026-08-04_claude_r2.md` carries a correction block
**above** the original text, which is retained rather than deleted — Codex was
told the original and is entitled to see exactly what it was told. Corrected:

* **"a non-zero attempt count … means criterion 6 is not met" is false and is
  withdrawn.** A non-zero *socket* count would mean that. A non-zero *spawn*
  count means something else, and the probe could not tell which it had.
* **The measurement**: 75 probe runs across three concurrent loops, 12 tripped,
  **84 attempts, every one `subprocess.Popen('ver', shell=True)`**, and
  `ATTEMPTS_NET` **0 in every one of the 75 runs**.
* **The rate claim, corrected without reconciling it in our favour.** The relay
  offered "15 consecutive full-suite runs green" as characterizing the flake.
  That was a real observation — sequential runs on an idle machine — but
  isolated repeats measured **8 red of 12 at `5ab2a79`, the very commit the
  relay was written against**. Both numbers are real and they measure different
  things; the one published was the flattering one, and the correction says so
  in those words rather than picking between them.

### 2.2 Criterion 6, restated

The claim now lives as **one value**, `offline.CRITERION_6_CLAIM`, so the code
and the documents that assert it cannot drift:

> A DocIQ run makes **NO outbound network attempt** — no socket, no resolver, no
> TLS handshake — and creates **NO child process, with exactly one named
> exception**: the Windows version probe `ver` that the standard library's
> `platform._syscmd_ver` runs when `platform.uname()` is first called, which a
> dependency's import triggers. That exception is permitted by identity (ruling
> D-30), is recorded with its stack every time it occurs, and reaches no
> network.

**More specific, not weaker.** The network half is unchanged and now has 75
further runs behind it. What was incomplete was the *scope*: the old sentence
would have been read as covering process creation and did not.

### 2.3 `track_f_sprint2_2026-08-01.md`

* §3.4(5)'s **"The cost is zero: nothing DocIQ does inside a guarded block
  spawns anything"** is struck through and corrected in place. It was asserted
  on reasoning ("a thread pool, not a process pool") and measured false. What
  the reasoning missed is that the spawn is not DocIQ's own call — it is the
  standard library's, three imports down.
* **New §3.5** states the claim as it now stands, what keeps the exemption from
  widening, and that D-30 adds no residue of its own.
* The §1 summary row points at §3.5.

Residues (1) and (2) — no adapter-disabled run, and a C extension calling
Winsock or Win32 directly — are unchanged and still open.

### 2.4 The decision register

`docs/decisions/decision_register.md` gains the **D-30** row after D-29, with
the measurement, the rejected alternatives, and the fail-before requirement.

**No `amendments.toml` entry is required**: D-30 is a ruling in the decision
register, not a contract amendment. `src/dociq/contracts.py` and
`src/dociq/gui/pipeline.py` are untouched, and `tests/test_amendments.py`
passes.

---

## 3. The class — a probe that counts without retaining what it counted

The finding was not "the offline probe was wrong". It was **a check that reports
a tally, a boolean or a status and discards the evidence behind it** — B-5's
shape one directory over. Every probe and guard in the repo was enumerated
against that question.

| site | reports | evidence retained? | disposition |
|---|---|---|---|
| `offline` probe in `tests/test_offline.py` | `ATTEMPTS=<n>` | **no** — stacks discarded | **FIXED** (previous package): prints `render()`, splits `ATTEMPTS_NET` / `ATTEMPTS_SPAWN`, now also `EXEMPTED` / `EXEMPTIONS` |
| `pipeline.corpus_sort_check` | bare `bool`, **and it gates publication** | **no** | **FIXED**: `corpus_sort_disagreements` names position, found Doc ID and expected Doc ID; the refusal quotes it |
| `selftest._check_no_network` | `render().splitlines()[0]` | pass path drops the permitted-spawn disclosure | **FIXED**: names what was permitted, on pass and on failure |
| `selftest` accounting / manifest / determinism checks (3 sites) | `render().splitlines()[0]` | **yes** — each prints the full discrepancy list, unclassified names, or full report adjacently on failure | left as is, judged compensated; recorded here so the judgement is reviewable |
| `verify.accounting.AccountingReport.ok` | `bool` | yes — `discrepancies` carry path, kind and detail | fine |
| `verify.determinism.DeterminismReport.ok` | `bool` | yes — `diffs` and `failures` retained | fine |
| `verify.manifest` `unclassified` / `compare` | lists of names | yes | fine |
| `offline.audit_siblings` / `audit_child_process_siblings` / `audit_*_imports` | tuples of names | yes — the names ARE the evidence | fine |
| `masterindex.quarantined_count` | `int` | yes — `quarantined` rows retained beside it | fine |
| `extract.ocr_available()` | `bool` | its one caller immediately fetches `ocr_models_present()[1]` for the reason | fine |
| `emit.handoff` `ok` / `ready` | `bool` | **not assessed** — file owned by the A-3/A-4 agent | §6 |
| predicates (`is_tier1`, `looks_like_header`, `has_transient_marker`, …) | `bool` | n/a — a predicate's evidence is its argument | not in the class |

**`corpus_sort_check` is the one that mattered.** It gates PUBLICATION. Before
this, a run could be refused with "the corpus is not in canonical order" over
nine thousand documents, and neither the log nor the status file could say which
two were the wrong way round. That is precisely the reasoning
`verify/accounting.py` was built on — "accounting failed is unactionable at
9,000 documents" — applied to every check but this one.

**No silent cap.** The evidence list stops at ten and the eleventh entry says
`... and N further position(s) (M in all)`. Fail-before watched: with the
disclosure line removed, `test_the_order_evidence_is_capped_out_loud_not_silently`
goes red; the test also asserts the fixture corpus is large enough to reach the
cap, so it cannot pass vacuously.

**A test that would have gone stale, caught.** `_force_out_of_order` stubbed
`corpus_sort_check`, the boolean the gate no longer reads. Left alone it would
have kept passing against a gate that never consulted it — the same "check whose
result is ignored" defect one level up. It now stubs the function the gate
actually reads.

---

## 4. Full-suite evidence

**8 of 8 green, with NOTHING deselected.** 1,334 tests collected, 1 skipped,
sequential, one process at a time.

| run | exit | seconds |
|---|---|---|
| 1 | 0 | 271 |
| 2 | 0 | 279 |
| 3 | 0 | 469 |
| 4 | 0 | 469 |
| 5 | 0 | 462 |
| 6 | 0 | 466 |
| 7 | 0 | 429 |
| 8 | 0 | 455 |

No `FAILED` or `ERROR` line in any of the eight logs.

**This is the claim the previous package could not make.** Its 8/8 carried
`--deselect` on `test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`,
disclosed at the time; the deselection is gone and the suite is green with the
probe running. The spread in run time (271 s to 469 s) is other work on the
host, not this branch.

---

## 5. Repeat-run evidence

The exemption is subprocess-, timing- and cache-sensitive by construction —
whether `platform.uname()`'s cache is already warm decides whether the permitted
call happens at all — so the standing rule is 30, not 8.

**30 of 30 green**: `test_offline.py` + `test_publication_gate.py` +
`test_emit_atomicity.py`, sequential. No `FAILED` or `ERROR` line in any of the
thirty logs.

Plus the rate table in §1.2: **0 red of 12** isolated `test_offline.py` runs
against 8 red of 12 at the branch base.

Every fail-before in §1.1 and §3 was **watched red**: the exemption's positive
case against stashed source, six single-axis widenings each turning exactly its
own matrix row red, the corpus-order refusal stripped of its evidence, and the
cap made silent.

---

## 6. What this does NOT establish

* **The adapter-disabled run is still not executed.** D-30 does not touch
  residue (1) of `track_f` §3.4, and criterion 6's "verified with network
  disabled" clause still needs a machine with the adapter off.
* **A C extension that spawns through the Win32 API** — rather than through
  `os`/`subprocess` — is invisible to this guard, and therefore to the
  exemption's narrowness as well. Unchanged residue (2).
* **The exemption is proven against CPython 3.14 on this host.** The caller file
  is resolved from the live `platform.__file__`, so it follows the interpreter;
  the command strings are literals, so a CPython that changes them **fails**
  rather than widening — that is the intended direction, but it has not been
  exercised against another CPython.
* **`emit/handoff.py`'s `ok` and `ready` were not assessed** for the class in §3.
  The file is owned by the A-3/A-4 agent and was not read or modified here.
* **The three compensated `selftest` truncations are a judgement, not a
  measurement.** Each prints its full evidence adjacently on failure, so the
  information reaches a captured log; nothing tests that it does.
* **`build/sprint-2` did not contain the B-4/B-5 work when this branch was cut.**
  The coordinator's instruction was to branch off `build/sprint-2` after merging
  it, but `origin/build/sprint-2` was still `5ab2a79` and `origin/build/s2-r2-swap`
  was the only branch containing `ec64ae3`. This branch is therefore cut from
  `build/s2-r2-swap`, which already has `build/sprint-2` as an ancestor, so the
  merge was a no-op. **A-3/A-4 have not landed and are not in this branch.**
