# Codex review #2, findings B-1 and B-2 — what was fixed and what was measured

**Branch:** `build/s2-fix-gate` (off `build/sprint-2` @ `e02c292`)
**Date:** 2026-08-04
**Scope:** B-1 and B-2 only. A-1, A-2 and B-3 are another agent's, on the GUI
and adapter files this round did not touch.

Codex's verdict is `docs/codex_reviews/sprint-2_2026-08-04_codex.md`. Its text
governs; this note records what was done and, more importantly, what was
watched.

---

## 1. The two findings are one sentence

**State was computed and then not acted on.**

B-1: §4 Stage 6 computed `accounting.check(result)` and built the manifest, and
then called `mark_ready()` and `commit_staging()` unconditionally.
`PipelineOutcome.ok` reported the red state *after* publication.

B-2: `commit_staging()` caught `OSError` **and `ValueError`** on the readiness
marker and substituted `superseded = ()` — a permissive default for state it
could not read, driving an irreversible act.

Everything below is organised around those two classes rather than the two
locations.

---

## 2. B-1 — publication is refused

### What changed

`src/dociq/pipeline.py`. Stage 6 now ends in a gate, and the swap is reached
only through it:

| gate | red when |
|---|---|
| `accounting` | `report_acc.ok` is false |
| `unclassified-output` | the manifest carries an output the claim does not classify |
| `log-content-hash` | `manifest.log_content_sha256` is `None` (§4 below) |
| `corpus-order` | `corpus_sort_check(result)` is false (§4 below) |

A refusal calls `_refuse_publication()`, which:

1. **discards staging** — nothing was marked ready, so `commit_staging()` has
   nothing to act on and the destination is never opened;
2. records the terminal status, so the log, the GUI failure state,
   `run_status.json` and the summary banner all see it without new vocabulary;
3. returns through `_abort()` — the ONE unpublishing path — so `published=False`,
   `incomplete_dir`, the quarantined log and the failing `<run>` discrepancy are
   written in one place and cannot drift between the abort case and this one.

`_abort()` grew optional overrides (`result`, `assignment`, `reconciliation`,
`manifest`, `accounting_report`) rather than the refusal growing its own
unpublishing path. A Stage-6 refusal knows things a Stage-1 abort cannot, and
blanking them would have made the quarantined record say *"no identifier was
issued"* about a run that issued one per document.

### The status value, and the amendment it owes

Recorded as `TerminalStatus.BLOCKED`. Its contract prose — *"the run never
established a corpus it could publish"* — is true here, and its operator
sentence is exactly right. But the docstring enumerates **three ways in** and
this is a fourth, so that enumeration is now short by one.
`src/dociq/contracts.py` is frozen and was concurrently edited, so the
correction is **raised as A-15**, not made: `docs/contracts/amendments.md` and
`amendments.toml` (`status = "raised"`).

The alternative considered and rejected: leave the termination `COMPLETED` — the
walk *did* complete — and carry the refusal only in `published=False`. That
would print *"Run status: completed — the walk covered every file found."* at the
top of a refused run's `run_status.json`, and write `"published": true` into the
log of a run that published nothing.

---

## 3. B-2 — an unreadable marker fails closed

`src/dociq/emit/paths.py`.

* **`replace_text_deterministic()`** — temp file, `flush`, `fsync`, `os.replace`,
  temp removed on any `BaseException` (including `KeyboardInterrupt`).
  `mark_ready()` uses it, so the marker is never observed half-written.
* **`_read_marker()`** — strict. Unreadable, non-JSON, non-object, missing
  `superseded`, non-list `superseded`, non-string entry, or an entry that is
  absolute or escapes the matter folder: all raise `PendingSwapUnreadable` and
  **nothing moves and nothing is deleted**. The message names the staging
  directory holding the complete set and the two ways forward.
* **`_validate_superseded_entry()`** — applied on the way IN as well, so a
  marker cannot be written naming a path outside the folder.
* `dociq.pipeline.run` catches `PendingSwapUnreadable` as its first statement —
  before `discard_staging()`, which would otherwise throw away the very set the
  operator is being told to go and look at — and returns a blocked run with the
  ordinary `incomplete_run/` record. A windowed executable turns a traceback
  into nothing at all.

### Why fail-closed rather than reconstruct

Codex offered either. Reconstructing the supersede set means recomputing
`_STALE_PATTERNS` at recovery time, and those patterns are pipeline policy that
`dociq.emit` may not import; more to the point, the marker exists *precisely* so
that a roll-forward removes what the interrupted run intended to remove, whatever
the folder looks like now. The asymmetry decides it: the permissive branch's cost
is a wrong answer that looks right, and this one's cost is a run that stops and
says why. Only one of those is discoverable by the person holding the folder.

---

## 4. The class enumeration

### Class A — a check computed and then not acted on

| site | disposition |
|---|---|
| `pipeline` Stage 6: `accounting.check`, `manifest.build` | **the finding — FIXED** |
| `pipeline.corpus_sort_check` | **FIXED.** Written as a Stage-6 check and reachable from **no code path in the product** — `grep` found one definition and zero calls outside `.venv`. A check that never runs cannot fail differently from a check whose result is ignored. Now a gate. |
| `manifest.log_content_sha256` | **FIXED.** `_log_content_hash()` returns `None` for a log it cannot read, and `corpus_sha256` folds it in as `self.log_content_sha256 or ""`. Measured: without the new gate that run publishes **and reports `ok=True`**, because `PipelineOutcome.ok` does not look at the field either — strictly worse than B-1. |
| `verify.determinism.prove` | gated: `selftest` returns 1 on `not det.ok`. |
| `DocumentRecord.validate()` | gated: raises. |
| `_stale_deliverables` termination guard | gated: raises `ContractViolation`. |
| `handoff.assert_only_sanctioned` | gated: raises `PackageContentError`. |
| `IssuedIdLedger.is_stale()` | **not a defect.** Feeds a `ledger-unusable` D-04 warning; non-destructive by design. |
| `dociq/gui/*`, `dociq/adapter.py` | **not inspected — off limits this round** (A-1/A-2/B-3 belong to the parallel agent). |

Held by `test_the_swap_is_unreachable_without_passing_the_gate` (a source check
that the swap is downstream of the gate) and
`test_a_run_that_is_not_ok_never_published` (the runtime invariant, over all six
unpublishable routes).

### Class B — an `except` substituting a permissive default for unreadable state

| site | disposition |
|---|---|
| `emit/paths.commit_staging` marker read | **the finding — FIXED** |
| `emit/paths` supersede entries (unvalidated, joined to the root, `unlink`/`rmtree`) | **FIXED.** Not filed by Codex; the same defect one layer down. A corrupt or hand-edited marker could delete outside the output root. |
| `ingest/walker._load_resume` | **fail-safe.** Unreadable journal → `{}` → re-extract everything. Costs time, loses nothing. |
| `adapter._stored_bates_pattern` | **fail-safe.** Unreadable log → `None` → the operator is asked afresh. (Also an off-limits file.) |
| `docid/masterindex` `ValueError` handlers | date-parse fallbacks; non-destructive. |
| `docid/reconcile.IssuedIdLedger.read` | **fail-closed already** — it does not catch, so an unreadable ledger raises. **Noted, not fixed:** `pipeline.py` does not wrap it, so on a windowed build that is a traceback rather than a blocked run with a reason. It is closed (nothing is published), which is why it is a note and not a defect in this round. |
| `tools/check_amendments._commit_exists` | `except → return True` when git is unavailable. Deliberate, argued in the code, and its cost is a CI check that under-reports rather than a deletion. **Noted, not changed.** |

---

## 5. Fail-before — every gate watched RED

The standing rule is that a green result proves nothing. Each fix was reverted
to its pre-fix shape and the tests were watched going red, then restored.

| reversion | result |
|---|---|
| `if refusals:` → `if False and refusals:` (the pre-fix "compute then proceed") | **7 red**: 3 × `test_a_red_stage_6_gate_refuses_to_publish`, 3 × `..._says_so_on_disk_and_names_the_gate`, `..._keeps_the_diagnosis_it_earned` |
| the same, gate 4 only (`log-content-hash`) | **1 red** — and it reported `published=True, ok=True` |
| the gate block MOVED below the swap | **1 red**: `test_the_swap_is_unreachable_without_passing_the_gate` (`gate at statement 93, mark_ready at 86`) |
| `_read_marker` → `except (OSError, ValueError): superseded = ()` | **9 red**: 8 × `test_an_unreadable_marker_moves_and_deletes_nothing`, plus the shrinking-rerun test |
| `mark_ready` → `write_text_deterministic`, validation removed | **2 red**: `test_a_crash_while_writing_the_marker_leaves_no_marker` (*"a half-written marker exists"*), `test_mark_ready_refuses_to_record_an_escaping_supersede_entry` |

### The pre-fix behaviour, measured rather than reasoned

**B-1, on the fixture corpus:**

```
[accounting]    published=True  ok=False  files changed/added: 2
                 processing_log.json, run_summary.pdf
[unclassified]  published=True  ok=False  files changed/added: 4
                 exhibit_bundle.idx, output_manifest.json,
                 processing_log.json, run_summary.pdf
```

The unclassified case is the sharp one: an artifact the byte-identical claim
does not recognise was **published into the matter folder** and the manifest was
replaced, on a run the caller was told was not ok.

**B-2, Codex's exact scenario at full size** — run 1 publishes the 17-document
fixture corpus; run 2 legitimately shrinks to 2 documents, stages, and dies
writing the marker; `recover_pending()` is then called:

```
run 1 clean_text files : 17
run 2 clean_text files : 2
visible after recovery : 17      <- the mixed set
manifest claims        : 4 deterministic entries
stale survivors        : 15
marker deleted         : True    <- and the only signal is gone
```

That is the finding, reproduced: a 17-file evidence set carrying a 2-file
manifest, with nothing on disk recording it. After the fix, `recover_pending()`
raises, the folder is byte-for-byte run 1's, the marker and the staged set are
both still there, and repairing the marker completes the shrink properly —
`survived == small_texts`, all 15 stale files removed.

The mixed-set assertion is made against `recover_pending()` **in isolation**, not
against a subsequent `pipeline.run()`. An end-to-end assertion cannot see it: run
3 walks the same source as run 1 and republishes the same 17 files either way, so
a folder comparison is satisfied by a mixed set and a clean one alike. That is
recorded in the test, because it is the vacuous-probe trap this project keeps
finding in its own suites.

---

## 6. Determinism (criterion 7)

Nothing about a refusal reaches hashed content, asserted by
`test_the_gate_does_not_reach_the_hashed_content`: two clean runs into two
destinations share one `corpus_sha256`, `sources.json` and
`document_index.csv` are byte-identical, and the string `refused` appears
nowhere in the log's hashed `content` section. The refusal vocabulary lives in
the `run` section and in `incomplete_run/`, both outside the claim.

`_UNREADABLE` and the `.partial` temporary are inside `.dociq/`, which
`manifest.build` skips, so neither can become an unclassified output.

---

## 7. Runs

| | |
|---|---|
| `tests/test_publication_gate.py` (27 tests) | see §8 |
| targeted slice: publication gate + emit atomicity + incomplete runs + verify + amendments + pipeline | green |
| full suite | see §8 |

## 8. What could not be proven here

* **`REFUSED` as a distinct terminal status.** Raised as A-15, not applied —
  `contracts.py` is frozen and was being edited in parallel. The shipped
  behaviour is correct; the enum's docstring is one item short until A-15 lands.
* **The GUI's rendering of a refused run.** `dociq/gui/*` and `adapter.py` are
  the parallel agent's this round (A-1, A-2, B-3). A refusal is legible from the
  folder — `run_status.json`, the quarantined log, the summary banner — and
  whether the *screen* shows it is A-2's question, not this one's.
* **An unreadable issued-ID ledger** raises rather than blocking cleanly
  (§4, class B). Closed, not pretty; noted rather than fixed because fixing it
  well means deciding how `pipeline.run` reports pre-Stage-1 exceptions
  generally, which overlaps A-2.
* **Real-corpus measurement.** Everything above is on the 14-file / 17-document
  fixture corpus. The failure modes are structural rather than scale-dependent,
  but no refused run has been observed on the 368-document record.
