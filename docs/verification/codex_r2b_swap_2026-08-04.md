# Codex review #2, fix round 2 — B-4 and B-5

**Branch:** `build/s2-r2-swap` (off `build/sprint-2` @ `5ab2a79`) · **Date:** 2026-08-04
**Findings closed:** B-4 (swap: a failed directory removal can still publish a
mixed set) and B-5 (the persisted refusal log drops the diagnosis it claims to
preserve)
**Not in this package:** A-3 and A-4, owned by the seam agent
(`runstate.py`, `gui/*`, `emit/summary.py`, `emit/handoff.py` were not touched).
D-2 was already closed by `5ab2a79`.

Measured, not expected. Every claim below names the check that supports it, and
§7 names what nothing here supports.

> ⚠️ **SUPERSEDED IN PART BY D-31 (2026-08-05).** The measurements here stand as
> a record of what was true at `build/s2-r2-swap`. The *design* they describe
> does not: Codex's next review found B-6 inside this very fix, and Alex ruled
> the class out with **delete-last** — the swap renames the current set into
> `.dociq/<aside>/`, renames the staged set into place, and deletes only
> afterwards. The two rows of §1.2's table naming `_remove_file_or_fail` and the
> "proven gone" discipline for a *superseded deliverable* are false as of D-31:
> `_remove_file_or_fail` no longer exists, and no deliverable is deleted at all.
> Current account: `docs/verification/codex_r3_deletelast_2026-08-05.md`.

---

## 1. B-4 — reproduced first, then fixed

### 1.1 The reproduction

Codex's reproduction was repeated on the reviewed tip before anything was
changed, with a **real** failure rather than a mock: one file inside the
superseded `upload_package/` directory was held open, which is the Windows case
this project's environment notes already document (an antivirus or backup agent
holds a transient deny-write on a file the swap is about to delete).

```
returned: ('upload_package',)
marker still there: False
stale survived: True
new README moved in: True
```

That is Codex's finding exactly: `commit_staging()` reported the directory as
removed, moved the new set in beside the survivor, deleted the readiness marker,
and returned success. The folder then held two builds with nothing on disk
saying so and no marker left to disclose or repair it.

The precondition was also verified independently — `shutil.rmtree` on a
directory containing an open file raises `PermissionError [WinError 32]` on this
host and leaves the tree in place. The failure the test injects is the real one.

### 1.2 What changed

`src/dociq/emit/paths.py`:

| | before | after |
|---|---|---|
| superseded **directory** | `shutil.rmtree(path, ignore_errors=True)` | `_remove_tree_or_fail(path)` — retried by `_retry_io`, then **proven gone** |
| superseded **file** | `_retry_io(path.unlink)` | `_remove_file_or_fail(path)` — retried, then **proven gone** |
| staging cleanup | `shutil.rmtree(staging, ignore_errors=True)` | retried; a residual **empty** tree tolerated, a residual **file** raises |

Two things were needed rather than one. `rmtree` without `ignore_errors` raises
on the first failure, which the retry handles — but a removal can also return
without the name disappearing (a file with an open handle on Windows is marked
delete-on-close and its directory entry survives until the last handle is
released). So `exists()` is checked after the fact as well: the question the
caller needs answered is not "did the call return" but "is it gone". The file
branch got the same treatment even though it was already retried, because the
class is *unproven removal*, not *unretried removal*.

A failure now propagates out of `commit_staging` **with the readiness marker
still on disk**, so the folder stays declared mid-swap and the next run rolls it
forward. The marker is deleted last, and only after everything it authorized
actually happened.

### 1.3 The one tolerated residue, stated

An **empty** staging tree left behind after every staged file has been moved is
tolerated. `.dociq/staging/` is DocIQ's own state, not a deliverable — the
manifest excludes the whole prefix, and `staging_layout()` removes it *without*
`ignore_errors` before the next run reuses the name, so a genuinely stuck
directory surfaces as a raised error at the start of that run. Converting a lock
on a directory nobody reads into a permanently blocked matter folder buys
nothing. A residual **file** is a different fact and raises.

`discard_staging()` also keeps `ignore_errors`, for the same test applied
honestly: no readiness marker exists on that path, so a surviving staging
directory cannot be published by anything.

### 1.4 The claim that was withdrawn

The fix-round relay said *"every destructive swap step is retried and
roll-forward remains possible"*. That was false for the directory branch. The
sentence is now in `commit_staging`'s docstring in a form that is true, and it
names the step that used to make it false rather than quietly dropping it.

### 1.5 The tests

`tests/test_emit_atomicity.py`:

* `test_a_directory_removal_that_fails_cannot_publish_a_mixed_set` — **FAIL-BEFORE
  watched red**: `Failed: DID NOT RAISE OSError` against the stashed original
  `paths.py`. Asserts the marker survives, the stale file survives, and the new
  set is still **whole in staging** (`_fingerprint(staging)` unchanged) rather
  than half-moved into the folder; then releases the lock and proves the
  roll-forward completes and removes the stale file.
* `test_a_file_removal_that_fails_cannot_publish_a_mixed_set` — the file sibling.
  This one **passed** on the old code, and its docstring says so: it is here
  because "the retried step and the unretried step behave the same way under a
  real lock" is the property, and a class fix proven only on the branch the
  reviewer named has not been shown to be a class fix.

---

## 2. B-5 — reproduced, then fixed as a class

### 2.1 The reproduction

Codex measured 19 assignments and three accounting discrepancies on the
in-memory outcome against `doc_ids.assignments == []` and `reconciliation ==
null` in the quarantined `processing_log.json`. Reproduced here as the
fail-before state of five tests (§2.4): against the stashed original
`pipeline.py` and `log.py`, all five go red, and the class probe's first missing
lookup is `KeyError: 'accounting_gate'`.

### 2.2 The class question, answered by enumeration

The finding is not "assignments were missing". It is **a value that exists in
memory and never reaches the durable artifact**. `_abort()` and `build_log()`
were enumerated for every instance:

| held by the refused run | before | now |
|---|---|---|
| `assignment` | outcome only | `content.doc_ids.assignments` |
| `reconciliation` | outcome only | `content.reconciliation` |
| accounting discrepancies | outcome only (no `build_log` input existed) | `run.accounting_gate.discrepancies` |
| `manifest` of the built set | outcome only — and staging is **discarded**, so nowhere else on disk | `run.output_manifest` |
| `drops` (Stage-4 drop log) | never passed to `_refuse_publication` | `content.drops` |
| `profiles` | not passed to `build_log` | `content.profiles` |
| `bates_decision` / `bates_ranges` | never passed to `_refuse_publication` | `content.bates` |
| `renumbering` | never passed to `_refuse_publication` | `run.renumbering_warnings` |
| `timings_s` | outcome only, on **both** paths | `run.stage_ms` (integer ms) |
| `stale_removed` | — | **no value exists**: a refused run replaces nothing (`_stale_deliverables` raises for a non-publishable termination). Declared not-durable with that reason. |
| `walk_notes`, `termination`, `published`, `layout`, `result`, `log`, `incomplete_dir` | already durable | unchanged |

Four of those ten gaps were not named in the verdict.

### 2.3 Criterion 7 — where each thing was put, and why

A refusal's diagnosis is a fact about the **invocation**, not about the
evidence. A run that was refused and one that was not differ in what happened to
them, not in what they read. So:

* **hashed `content`** received only input-derived facts: the assignment, the
  reconciliation, the drops, the profiles, the Bates section. A refused run
  established these, and blanking them is what made the record lie.
* **`run`** received the gate outcome, the manifest of the discarded staging set
  and the wall clock. Hashing any of them would make the byte-identical claim
  false on its face — the same mistake as `output_root` and the
  `"abandoned after 3604s"` string, both of which had to be unpicked here
  before.

`timings_s` is written as integer milliseconds rather than floats, so the rule
that keeps floats out of identity is not quietly relaxed one section over.

**Proven, not asserted** — `test_the_refusal_log_keeps_criterion_7`:

* two refused runs into two destinations produce the same `content_sha256` and
  byte-equal `content`;
* `"refused"`, `"accounting_gate"`, `"output_manifest"`, `"stage_ms"` and both
  destination paths (three spellings each) appear nowhere in `content`;
* a refused run's `content.doc_ids`, `content.documents`, `content.drops` and
  `content.bates` **equal a published run's** over the same corpus.

### 2.4 The tests

`tests/test_publication_gate.py`, all five **watched red** against the stashed
original `pipeline.py` + `log.py`:

* `test_the_refusal_log_on_disk_carries_the_assignment_and_reconciliation`
* `test_the_refusal_log_on_disk_names_the_discrepancies_that_refused_it`
* `test_the_refusal_log_on_disk_carries_the_manifest_of_the_discarded_set`
* `test_the_refusal_log_keeps_criterion_7`
* `test_every_outcome_field_of_a_refused_run_has_a_durable_home`

Every one reads `incomplete_run/processing_log.json` **from disk** and compares
it against the in-memory outcome, which is the assertion the fix round did not
make.

The last is the **class probe**: it enumerates `dataclasses.fields(
PipelineOutcome)` and fails when a field is added or removed without a decision
about where the durable record keeps it. That is what stops the class returning,
as opposed to the two fields the reviewer happened to measure.

---

## 3. One asymmetry, disclosed

On the **published** path `build_log` receives
`reconciliation=report if index is not None else None`; on the **refusal** path
it now receives the report unconditionally. So a refused run with no master
index serializes a reconciliation object where a published run in the same
situation serializes `null`.

This was deliberate and is the conservative direction — the refusal record
carries more of what the run established, never less. Making the two identical
would mean changing the published path, which would change `content_sha256` for
every no-index run; that is not a change to make inside a fix round for a
different finding.

---

## 4. Full-suite evidence

**8 of 8 green**, sequential, one process at a time, on the tree at `36c3a8d`.
1,317 tests collected; 1 skipped in every run.

| run | exit | seconds |
|---|---|---|
| 1 | 0 | 248 |
| 2 | 0 | 254 |
| 3 | 0 | 257 |
| 4 | 0 | 268 |
| 5 | 0 | 280 |
| 6 | 0 | 282 |
| 7 | 0 | 274 |
| 8 | 0 | 260 |

No `FAILED` or `ERROR` line appears in any of the eight logs.

**The one deselection, stated plainly.** Every run carried
`--deselect tests/test_offline.py::test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`.
That test is red on this host roughly one run in six **and eight runs in twelve
at the branch base without this package** — see §5a, which diagnoses it. The
deselection is disclosed rather than hidden, and it is one named test: **8/8 is
a claim about 1,316 tests, not 1,317.** A full-suite green run including it was
also observed (the first clean run of this session), but a single observation is
not evidence and is not counted.

**Two earlier "failures" are withdrawn as harness artifacts.** During the first
attempts at this evidence, a backgrounded launcher started the same script
twice, so two full suites raced each other on one host. One run reported
`test_gui_states.py::test_the_chrome_is_us_english` failed; it did not reproduce
in 40 targeted runs, in a full suite run concurrently with another, or in any of
the eight runs above, and the en-GB regex it asserts matches nothing in the
committed tree or the working tree (checked directly against both). The other
was the offline probe. Both were observed under a condition that was my
harness's fault, and neither is offered as evidence about the code.

---

## 5. Repeat-run evidence

The swap protocol is timing-, lock- and temp-directory-sensitive, so the
standing rule is 30 runs rather than 8 for the slices that exercise it.

**30 of 30 green**, sequential:
`test_publication_gate.py` + `test_emit_atomicity.py` + `test_incomplete_runs.py`.
No `FAILED` or `ERROR` line in any of the thirty logs. A separate 30-run
sequence of the same three files was also green earlier in the session, against
the same code modulo two comments.

Every fail-before in §1.5 and §2.4 was **watched red** against the stashed
original source before the fix was reinstated — seven tests, one of which
(`..._file_removal_...`) is documented as passing before and is present for the
class rather than the repro.

---

## 5a. STOP THE LINE — the offline probe was diagnosed, and it was not what it said

Not part of B-4 or B-5. Found while establishing the repeat-run evidence, and it
changes what the branch can claim, so it is reported here rather than parked.

`tests/test_offline.py::test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN`
has failed intermittently for three rounds and was disclosed to Codex as
"cause remains open". Codex's fix-round verdict accepted that disclosure.

**The probe printed a count and threw away the evidence.** `NetworkGuard`
records the entry point, the argument and the Python stack of every attempt.
The probe printed `ATTEMPTS=<n>` and nothing else, and the assertion called that
number "outbound attempt(s)" and "A CRITERION 6 FINDING". That is why three
rounds produced a rate estimate rather than a diagnosis: the artifact that would
have explained it was discarded at the moment it was created — the same shape as
B-5, one directory over.

**With the stacks printed, it reproduced on demand.** 75 probe runs across three
concurrent loops: **12 tripped**, and all **84** recorded attempts across those
12 were one thing:

```
subprocess.Popen('ver', stdout=-1, stdin=-3, stderr=-3, text=True, shell=True)
  pipeline.run  ->  walker.effective_limits(...)
  ->  extract.ocr_model_id()  ->  extract.ocr_model_dir()
  ->  import rapidocr_onnxruntime  ->  import onnxruntime
  ->  platform.system() -> platform.uname() -> platform.win32_ver()
  -> platform._syscmd_ver()
```

**`ATTEMPTS_NET` was 0 in every single run. No socket was ever touched.** It is
the child-process half of the guard, tripped by the standard library probing the
Windows version during an OCR-model import. Every previous occurrence was
reported as an outbound-network finding, and every one of those reports was
false.

**Measured rates**, isolated `pytest tests/test_offline.py` on this host:

| tree | red |
|---|---|
| branch base `5ab2a79` (without this package) | **8 of 12** |
| this tip | **2 of 12** |

Pre-existing and not introduced here. The probe is not a low-rate flake — at the
base it is red more often than green, which is hard to reconcile with the
"15 consecutive full-suite runs green" the fix round reported, and that
discrepancy is disclosed rather than explained.

**What was changed here, and what deliberately was not.** The probe now prints
`guard.render()`, and `ATTEMPTS_NET` / `ATTEMPTS_SPAWN` are asserted separately
so each failure says what it means. **Neither assertion was weakened** — a spawn
inside the guard still fails the suite. Whether DocIQ should tolerate
`platform.uname()`'s `cmd /c ver` during an OCR-model import is a **gate
question**: it is not an outbound connection and criterion 6 is about the
network, but the child-process guard exists precisely because a child leaves the
scope of every rebind in this interpreter. Answering it by relaxing an assertion
is not a test's decision, and it is not this package's.

**Recommended ruling** (for Alex, one question): warm `platform.uname()` once at
process start, outside any guard, so the stdlib's one-time version probe cannot
occur inside a guarded run — versus declaring `platform._syscmd_ver` a
disclosed, named exception in `offline.py` with the reason recorded. The first
moves an unavoidable stdlib call; the second admits it in writing. Both keep the
network claim intact; neither is taken here.

---

## 6. Files changed

| file | why |
|---|---|
| `src/dociq/emit/paths.py` | B-4: `_remove_file_or_fail`, `_remove_tree_or_fail`, `commit_staging` fail-closed; withdrawn claim restated truthfully |
| `src/dociq/emit/log.py` | B-5: `accounting_report`, `manifest`, `timings_s` inputs, all landing in `run` |
| `src/dociq/pipeline.py` | B-5: `_abort` passes what it holds; `_refuse_publication` threads drops/profiles/Bates/renumbering |
| `tests/test_emit_atomicity.py` | two B-4 fail-before tests |
| `tests/test_publication_gate.py` | five B-5 on-disk tests, including the class probe |
| `tests/test_offline.py` | §5a: the probe prints the attempt stacks and splits socket from child-process counts |

Nothing frozen was touched: `src/dociq/contracts.py` and `src/dociq/gui/*` are
unmodified, so no `docs/contracts/amendments.toml` entry is required. The other
agent's files (`runstate.py`, `emit/summary.py`, `emit/handoff.py`, `gui/*`) are
unmodified.

---

## 7. What this package does NOT establish

* **A-3 and A-4 are not addressed here.** `REFUSED` is still rendered as
  `CANCELLED` by `RunTermination.headline()`, and a failed Path-A build can
  still leave a current partial `upload_package/`. Both sit in files this
  package was told not to touch.
* **STOP THE LINE — the quarantined `run_summary.pdf` still does not carry the
  gate discrepancies.** `incomplete_run/` holds three artifacts, and B-5 was
  fixed in two of them: `processing_log.json` (this package) and
  `run_status.json` (already carried the termination). The third is built by
  `build_summary_data()` in `src/dociq/emit/summary.py`, which this package was
  told not to edit — it is the seam agent's for A-3, whose own requirement is
  "wording for a complete-but-refused corpus". So the operator-facing PDF of a
  refused run names the status but not which gate refused it. That is the same
  class as B-5, in a file this agent may not touch, and it is handed to A-3
  rather than parked.
* **The tolerated empty-staging residue is a judgement, not a proof.** No test
  forces a lock that survives only on the empty directory; the reasoning in §1.3
  is why it is safe, and it rests on `staging_layout` raising, which is existing
  covered behaviour rather than something measured for this residue specifically.
* **The B-4 lock is injected by holding a handle from the same process.** That
  is the same mechanism the OS gives an antivirus, and it produced the real
  `PermissionError`, but it is not a test against an actual scanner.
* **`stage_ms` is new on the refusal path only.** The published path's
  `build_log` call runs before Stage 6 is timed, so adding it there would record
  a partial figure. The asymmetry is deliberate and untested.
* Determinism is proven for the log's `content` across two destinations and
  against a published run's content. It is **not** proven across machines or
  Python versions; that limitation is unchanged from the branch's existing
  disclosures.
