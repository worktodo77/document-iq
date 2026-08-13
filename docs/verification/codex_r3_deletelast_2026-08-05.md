# D-31 — delete-last, and B-7

**This file:** `docs/verification/codex_r3_deletelast_2026-08-05.md`
**Branch:** `build/s2-r3-deletelast` (off `build/sprint-2` @ `29cda9a`) · **Date:** 2026-08-05
**Ruling implemented:** D-31 (decision register, Sprint-2 section)
**Findings closed:** **A-5**, **B-6** (by redesign, not by patch) and **B-7**
(a small adjacent fix, done as a fix)

Measured, not expected. §8 names what nothing here supports.

---

## 1. Why this is a redesign

Codex review #2 ran three fix rounds, and **each round found a new defect inside
the previous round's fix** — all in one subsystem, all one class: *a destructive
filesystem step that cannot be proven complete when antivirus, a scanner, or
Windows delete-on-close interferes.*

| round | findings |
|---|---|
| 1 | B-1 (publish was unconditional), B-2 (permissive marker read) |
| 2 | B-4 (`rmtree(ignore_errors=True)` absorbed failure), B-5 (diagnosis missing from the durable log) |
| 3 | **B-6** (a marker whose `unlink()` returned while its NAME survived authorized the next recovery to delete the newly published set), **A-5** (a partly-deleted backup renamed back and reported as "intact"), B-7 |

Every fix was correct. Every fix opened the next window, because **the design
deleted before it published, so every failure mode was "half-deleted"**.

D-31's substitution is **rename in place of delete**. A rename on one volume
either happened or it did not, and which of those is true is readable from the
names on disk. A delete under lock is neither provable nor reversible.

---

## 2. The sequence, and why each step is safe

### 2.1 The matter-deliverable swap (`src/dociq/emit/paths.py`)

`mark_ready()` writes a marker recording three things: the `superseded` names,
the **`aside`** directory the swap will rename them into (a free
`.dociq/superseded[.N]` name, chosen once so a swap and its roll-forward agree),
and the **`phase`**.

`commit_staging()` then:

| step | operation | if it fails |
|---|---|---|
| 0 | read the names on disk: what is left in staging, what is already set aside | — |
| 1 **pending → aside** | RENAME each planned name into `.dociq/<aside>/` | raise; the matter folder holds the previous set minus what already moved, all of it intact under `.dociq/`; marker stays at `pending` |
| — | atomically rewrite the marker to `aside` | raise; step 1 re-runs and is idempotent (a name already moved is skipped because it is not at the root) |
| 2 **aside → published** | (a) RENAME every occupied destination into `.dociq/<aside>/`, as a COMPLETE pass, then (b) RENAME each staged file into place — *corrected 2026-08-06, see the withdrawal below; this row used to describe (a) as happening lazily, inside (b)* | raise; the matter folder holds *part of the new set and none of the old* — incomplete, never mixed; the whole previous set is intact under `.dociq/<aside>/` and the rest of the new set is intact in staging; marker stays at `aside` |
| — | atomically rewrite the marker to `published` | as above |
| 3 | DELETE `.dociq/superseded*` and the drained staging tree | **does not raise.** The matter folder is complete and correct; a surviving tree is a disclosed residue |
| 4 | `unlink` the marker | a surviving name is now provably harmless — see §3.1 |

**Why step 1 before step 2 is the whole point.** The previous set leaves the
folder *entirely* before any of the new set arrives, so at every instant the
matter root holds files from one run only. That is the difference between
"incomplete" and "mixed", and it is what §7's fixed deliverable paths (D-20,
Path B) otherwise make impossible to guarantee.

> **WITHDRAWN AS WRITTEN, 2026-08-06 — Codex review #2, third fix round, B-8.**
> The sentence above was the load-bearing claim of this note and **it was false
> when it was written.** Step 1 did not take the previous set out; it took out
> the names `pipeline._STALE_PATTERNS` enumerated — *this build's* output
> names. Two things followed, and Codex reproduced both on Windows:
>
> * a deliverable an older build wrote at a name this build no longer names, but
>   still writes a replacement for, was recognised only when the publish loop
>   REACHED it, and moved aside at that late point. Sorted publication landed
>   `a_new.txt` first; a real open handle on `z_legacy.txt` then froze the swap
>   at phase `aside` with `a_new.txt = NEW` beside `z_legacy.txt = OLD` — the
>   mixed set this note says is unreachable;
> * a deliverable an older build wrote and this one RETIRED had no staged
>   successor, so the lazy branch was never entered. The swap **completed**,
>   removed the marker, and left the old file beside the new set permanently.
>   §4 Stage 6 cannot see it: the manifest is built over staging, not over the
>   destination root.
>
> The claim is now true, and it is true for two independent reasons rather than
> asserted: the plan is built from a **durable inventory of what the last run
> actually published** (`.dociq/published_set.json`, written by the swap that
> published it, so it survives a version change), and the publish phase begins
> with a **complete pass that clears every destination** before any staged file
> is renamed in. The first makes the plan complete; the second makes an
> incomplete plan harmless. See `docs/verification/codex_r4_inventory_2026-08-06.md`.
>
> Two further states are corrected there, found by enumerating the swap's
> persistent states backwards rather than by following a run forwards — both
> destroyed a complete set of deliverables and neither was reachable from the
> code, which is why this note never mentioned them.

**Why step 3 is last and non-fatal.** By that line the published set is in
place. A lock on one file in an already-superseded tree must not turn a
published run into a failed one, and it cannot make the evidence wrong. What
survives is named by `superseded_residue()` rather than absorbed.

### 2.2 The package swap (`src/dociq/emit/handoff.py`)

The order is **reversed** from the previous round's:

1. rename `upload_package/` → `.dociq/package_superseded/`
2. rename `.dociq/package_staging/` → `upload_package/`; if this fails, rename
   the superseded tree **back** — and that restore is trustworthy, because it
   was moved and never modified
3. only now, delete `.dociq/package_superseded/`

Both working directories moved from the matter root into `.dociq/`. That is
what answers the stray-folder worry the old order was built around: a set-aside
package is no longer a package-shaped folder sitting beside the deliverables in
the space §7 reserves for what Expert Assist reads. §7's layout is unchanged and
`emit/handoff.py::expert_assist_layout` still proves it.

---

## 3. The three findings, and what makes each unreachable

### 3.1 B-6 — a stale marker can no longer destroy a published set

**Three independent guards, and the disk-readable one is primary:**

1. **The plan can only select paths to RENAME INTO `.dociq/`.** There is no code
   path in the swap or in recovery that deletes a file at the matter root.
   `_remove_file_or_fail` is deleted.
2. **The names on disk.** An empty staging directory means there is nothing to
   publish, so nothing may be set aside — which is exactly the state a marker
   that outlived a successful swap sits beside.
3. **The recorded phase.** `published` says the publish is done.

`test_a_marker_that_outlives_its_swap_deletes_nothing` reproduces Codex's
scenario verbatim (the `unlink()` returns, the name survives) and asserts the
published bytes are still there.
`test_a_stale_pending_marker_beside_an_empty_staging_deletes_nothing` proves
guard 2 alone, without relying on the phase field.

### 3.2 A-5 — there is no partly-deleted backup to call intact

The prior package is *renamed* aside. Nothing deletes it until the new package
holds the published name, so no rollback ever restores a destructively modified
tree. `test_a_partly_deleted_superseded_package_is_never_the_published_one`
uses a `_remove_tree` double that **actually removes part of the tree before
failing** — the old test double returned `False` without deleting anything, which
is why it could assume the property the production helper cannot guarantee.

The rollback branch keeps its own tests, and they assert the **bytes**, not the
wording: `test_a_publish_that_cannot_take_the_name_restores_an_unmodified_backup`
and `..._and_cannot_roll_back_says_where`.

### 3.3 B-7 — one identity for a refused log's own hashed content

`_log_reconciliation()` is now the one projection both the published path and
`_abort()` call, and the refusal path also threads the published `warnings` list
and the staged `output_hashes`, which were two further divergences found by
enumerating `content`'s keys rather than reported. `_abort()` compares its
rebuild against the staged bundle and, on drift, records the staged projection
(the one the manifest hashed) and **discloses** the differing keys as a `<run>`
discrepancy plus a run note — by rebuilding the log, because `build_log` copies
the notes and the accounting report at call time and an append afterwards would
reach the outcome and never the file. That is the B-5 class, and it is not being
reintroduced by its own fix.

**The test's reach was the defect, not just the code.** The R3 comparison test
compared four *named sections* of two logs. It now compares the whole `content`
section and both hashes, and asserts the embedded-manifest-hash property over
**both** the no-index and with-index branches. Its no-index fixture was itself
asserting the defective projection, so the fixture gained a synthetic master
index rather than the assertion being weakened.

---

## 4. Every fail-before, and how it was watched red

The fixes are in the source, so each fail-before was watched red by restoring
the **pre-fix body** at the same commit and re-running.

### 4.1 B-7 — `git stash push src/dociq/pipeline.py`

| test | red |
|---|---|
| `test_the_refusal_log_keeps_criterion_7` | ✅ |
| `test_a_refused_log_gives_one_identity_for_its_own_hashed_content` | ✅ |
| `test_a_refused_log_with_an_index_also_agrees_with_its_manifest` | ✅ (`79d46e58… != c7a9df02…`) |

### 4.2 A-5 — the pre-D-31 publish order restored in `_publish_package`

`test_a_partly_deleted_superseded_package_is_never_the_published_one` went red
with the exact sentence the finding is about:

```
PackageSwapError: The new package was built and validated but the package it
replaces could not be removed. Nothing was published. The earlier build is back
in place and intact.
```

### 4.3 D-31 / B-6 — the pre-D-31 `commit_staging` body restored

Eight tests red:

```
test_a_marker_that_outlives_its_swap_deletes_nothing
test_a_stale_pending_marker_beside_an_empty_staging_deletes_nothing
test_the_swap_destroys_nothing_under_the_matter_root
test_recovery_destroys_nothing_under_the_matter_root
test_a_blocked_set_aside_leaves_the_previous_set_and_no_mixture
test_a_blocked_publish_leaves_an_incomplete_set_never_a_mixed_one
test_a_blocked_cleanup_publishes_and_discloses_the_residue
test_dociq_state_inside_staging_is_never_published
```

### 4.4 The class probe

`_DestructiveCallAudit` wraps `shutil.rmtree`, `os.remove`, `os.unlink`,
`os.rmdir`, `os.replace`, `Path.unlink` and `Path.replace` for the duration of a
swap and of a recovery, and asserts **no recorded target under the matter root
is outside `.dociq/`**. `os.replace` is audited alongside the deletions because
an overwrite *is* a delete — it destroys the destination with no name left to
read afterwards — and that is exactly the substitution D-31 forbids.

This is the assertion that makes the fix a class fix rather than three repro
fixes: it does not name a failure mode, it names the primitives.

---

## 5. Enumeration — every destructive filesystem operation in the product

D-31's standing instruction: *enumerate every remaining destructive filesystem
operation in the product and state, for each, what happens when it fails — and
whether that outcome is readable from disk.* An independent sweep of
`src/dociq/**` found **58** call sites. They fall into six groups.

**Doing this found a defect nobody had reported, and it is fixed** — see §5.7.

### 5.1 The swap engine (`emit/paths.py`) — 10 sites

| operation | target | on failure | readable from disk? |
|---|---|---|---|
| `_rename_or_fail` (set-aside, publish, unplanned-occupant) | matter root ↔ `.dociq/<aside>/` | raises; marker stays at the phase actually reached | **yes** — the names say which side each entry is on |
| `_remove_tree_or_fail` | `.dociq/<aside>` or `.dociq/staging` **only** | raises to callers that absorb it | yes — the tree is still named |
| `_discard_aside_trees` | every `.dociq/superseded*` | collects survivors, never raises | yes — and reported by `superseded_residue()` |
| `commit_staging` staging removal | `.dociq/staging` | `except OSError: pass` | yes |
| `commit_staging` marker unlink | `.dociq/staging_ready.json` | **tolerated** (§5.7) | yes — and the surviving marker says `published` |
| `staging_layout` | `.dociq/staging` | raises at the top of Stage 5, deliberately loud | yes |
| `discard_staging` | `.dociq/staging` | `ignore_errors=True` | yes; no marker exists on that path, so a survivor can publish nothing |
| `replace_text_deterministic` (`os.replace` + tmp unlink) | `.dociq/staging_ready.json` | raises; the `.partial` is removed | yes |

**No matter-root path is deleted or overwritten anywhere in this module.**
Containment is enforced at marker-parse time by `_validate_superseded_entry`
(what may be moved) and `_validate_aside_name` (where it may be moved to, and
therefore what may later be deleted).

### 5.2 The package builder (`emit/handoff.py`) — 16 sites

Every deletion targets `.dociq/package_staging` or `.dociq/package_superseded`.
The only matter-root operations are two renames — `upload_package/` out to
`.dociq/`, and staging in — plus the rollback rename. Failures before the
publish raise `PackageSwapError` with the earlier package under its own name;
the rollback failure names where the earlier build is; the post-publish deletion
is absorbed on purpose, because a published package must not be reported as a
failed build.

### 5.3 The Stage-5 emitters — 12 sites

`cleantext`, `indexbook`, `log`, `summary`, `manifest.write`,
`IssuedIdLedger.write`, `write_matter_copy`. All are truncating writers, all
bare (no retry, no temp file), and **all of them write into
`.dociq/staging/`** — `pipeline.run` hands every Stage-5 emitter `stage_out`, not
`layout`. A failure raises out of Stage 5 with the matter folder untouched,
which is what `test_a_crash_during_emit_leaves_the_previous_run_untouched`
proves for four of them.

### 5.4 `_abort` — 3 sites, and the one exception to §5.3

`_abort` writes `processing_log.json`, `run_status.json` and `run_summary.pdf`
**directly into `<matter>/incomplete_run/`**, which may already exist. These are
truncating in-place overwrites of a real matter-root directory. They are not a
D-31 concern — `incomplete_run/` is the record of a run that published nothing,
and overwriting an older failed run's record with a newer one is the intended
behaviour — but it is the one place a matter-root file is overwritten rather
than renamed into, and it is named here rather than left to be found.

### 5.5 Ingest scratch — 5 sites

`_clear_scratch`, the resume journal's truncate and unlink, and two
`NamedTemporaryFile` unlinks. All under `.dociq/scratch/` or `%TEMP%`, all
absorbed, none evidentiary.

### 5.6 Outside the matter root — 7 sites

`profiles/model.py::save_to_library` writes into `%APPDATA%\LI DocIQ\profiles`
(D-05); `branding/make_*.py` overwrite repo assets; `selftest.py` deletes its
work directory. See §8 for the one of these worth flagging.

### 5.7 The defect the enumeration found — and closed

Steps 1–3 of `commit_staging` are built so that a failure **below the publish**
is a disclosed residue rather than an error. The final `marker.unlink` was not:
`_retry_io` re-raised after eight attempts and nothing above `commit_staging`
handles it, so a transient antivirus lock on `staging_ready.json` — *the same
condition every other step in that function absorbs* — turned a run whose
deliverables were fully published into a traceback.

Fixed, with a fail-before watched red
(`test_a_locked_marker_does_not_fail_a_run_whose_set_is_published`). The
surviving marker is provably harmless: it says `published`, and the next
recovery reads that, finds an empty staging directory, and touches nothing.

This is recorded prominently because of what it says about the method: the
finding came from enumerating the primitives, not from a test failing and not
from a reviewer. Two of the last three rounds' findings were of the same shape.

---

## 6. Reproduced vs reasoned

**Reproduced on disk:**

- B-7's two divergent hashes, and their agreement after the fix (§4.1).
- A-5's "back in place and intact" over a genuinely damaged tree (§4.2).
- B-6's surviving marker, and the published set still being there after the
  second recovery (§4.3).
- Every failure state in §2.1's table, by injecting a failure at that step and
  asserting the bytes on disk afterwards.
- A **real** Windows lock for the set-aside rename: `test_a_file_removal_that_
  fails_cannot_publish_a_mixed_set` and `test_a_directory_removal_that_fails_
  cannot_publish_a_mixed_set` hold an open handle on a real file, and the rename
  genuinely fails. No mock.

**Reasoned, not reproduced:**

- That `os.rename` on NTFS has no "marked for rename on close" state analogous
  to delete-on-close. This is the load-bearing premise of the whole redesign and
  it rests on the semantics of the operation, not on a measurement here.
- That a same-volume rename cannot be a copy. Enforced structurally — staging
  and the set-aside tree are both inside the matter root — rather than checked
  at runtime.
- B-6's exact trigger (a shim or scanner that lets `unlink()` return while the
  entry survives) is simulated by monkeypatch, as §8 records. A real lock makes
  `unlink` *raise*, which is a different state and was already handled.

---

## 7. Test fidelity — which tests use a real lock and which a monkeypatch

| test | mechanism | why |
|---|---|---|
| `test_a_directory_removal_that_fails_cannot_publish_a_mixed_set` | **real open handle** | Windows refuses to rename a directory containing an open file |
| `test_a_file_removal_that_fails_cannot_publish_a_mixed_set` | **real open handle** | same, for a file |
| `test_a_marker_that_outlives_its_swap_deletes_nothing` | monkeypatch on `Path.unlink` | the state reproduced is *the call succeeded and the name is still there*, which a real lock does **not** produce |
| `test_a_blocked_set_aside_...`, `test_a_blocked_publish_...` | monkeypatch on `_rename_or_fail` | needed to fail a *specific* rename mid-sequence; a real lock cannot be aimed that precisely |
| `test_a_blocked_cleanup_...` | monkeypatch on `_remove_tree_or_fail` | as above |
| `test_a_partly_deleted_superseded_package_...` | monkeypatch double that really deletes a file | fidelity *raised*: the old double deleted nothing |
| package rename branches | monkeypatch on `_retry_rename` | to fail one specific rename and allow the rollback |

Two tests were raised to a real lock where it was cheap. The rest could not be,
and the reason is given per row rather than glossed.

---

## 8. What this package does NOT claim

### 8.1 Stop-the-line items — raised, not taken

* **`src/dociq/contracts.py` and `src/dociq/gui/pipeline.py` were not touched.**
  No amendment was needed and none was written.
* **The residue does not reach the screen.** `superseded_residue` is on
  `PipelineOutcome` and in the next run's log
  (`run.superseded_residue_before_swap`), but `gui/pipeline.py::RunOutcome` — the
  frozen seam — carries no field for it, and adding one is a
  **stop-the-line amendment** that was not authorized. It was deliberately not
  routed through `RunResult.warnings` instead, because those become hashed
  `content.warnings` and a residue is an invocation fact: doing so would break
  criterion 7. Precedent: `stale_removed` does not cross the seam either.

### 8.2 Windows fidelity

Three tests use a **real** open handle (§7). The rest use monkeypatch, with a
per-test reason. B-6's trigger specifically **cannot** be reproduced with a real
lock: a real lock makes `unlink` raise, and the state the finding is about is
the call *succeeding* over a surviving name. So the B-6 test asserts the
consequence over a simulated cause. That is a genuine limit and is not
presented as anything else.

### 8.3 Reasoned premises

The redesign rests on `os.rename` having no delete-on-close analogue on NTFS
(§6). That is the semantics of the operation, not a measurement taken here. If
it is wrong, the redesign has a window and this note is where that would have
had to be said.

### 8.4 Outside this package's scope, and disclosed

`src/dociq/selftest.py` takes `$DOCIQ_SELFTEST_WORKDIR` from the environment and
`shutil.rmtree`s it whole, unvalidated, with errors silenced. It is a CI/operator
entry point rather than anything the GUI reaches, and it predates this package —
but it is in `src/dociq/`, it is the one bare unretried deletion of a whole tree
at a path DocIQ did not derive, and D-31 asked for the enumeration to be
complete rather than convenient. **Not fixed here**: changing the selftest
harness's teardown is outside a swap-redesign package and would go untested by
the suite that would have to run it.

### 8.5 Carried forward unchanged

`_remove_tree` (handoff) and `_remove_tree_or_fail` (paths) remain two
independent retry helpers with different contracts. The reason previously given
for the duplication — that `paths.py` was under concurrent revision — has
expired, so the duplication is now just duplication. Recorded rather than
collapsed, because merging them mid-redesign would put an untested shared
helper under both swaps at once.

### 8.6 Unchanged non-claims from the previous round

Criterion 4 is not met. No mouse-driven GUI acceptance has been performed. The
3,600-second per-file timeout remains Alex's open decision.

---

## 9. Suite evidence

All at `8caeca1` on `build/s2-r3-deletelast`. **No source file was edited while
any of these ran** — see §9.3 for why that sentence is here.

### 9.1 Full suite — 9 runs, nothing deselected

| batch | runs | result |
|---|---|---|
| sequential ×4 | 1–4 | 100%, no `F` and no `E` in the progress output, loop exit 0 |
| single | 5 | `1394 passed, 1 skipped in 537.74s` |
| sequential ×4 | 6–9 | `1394 passed, 1 skipped` — 554.55s, 1481.45s, 358.45s, 341.40s |

A further five runs completed exit 0 in a batch whose per-run output was
swallowed by a bad `grep` in the harness. **They are not counted**, because
"exit 0" there was the exit code of `tail`, not of `pytest`. Recorded rather
than quietly folded into the total.

The wall-clock spread (5:41 to 24:41) is contention — two suites ran
concurrently for part of it, deliberately — not variance in the tests. The
24:41 run passed under that contention, which is a small piece of evidence in
its own right for a package this filesystem-heavy.

### 9.2 The filesystem-sensitive class — 30 repeats

`test_emit_atomicity.py` + `test_package_swap.py` + `test_publication_gate.py` +
`test_incomplete_runs.py`, the four modules that touch real files, locks and
renames: **30 repeats, 30 green, 0 red.** This is the "30 for anything
filesystem/timing/subprocess-sensitive" bar; the whole of this package is that
class, and one green run of it would have proved nothing.

### 9.3 One discarded run, and what it teaches

The first full-suite run of this package went red on
`test_the_swap_is_unreachable_without_passing_the_gate` with an
`IndentationError` out of `ast.parse`. It was not a defect: that test reads
`pipeline.py` **from disk** via `inspect.getsource`, and the file was edited
while the suite was running. The run is discarded and the finding is recorded —
a source-inspection test makes concurrent editing a source of phantom failures,
and the correct response is not to touch the tree during a suite run.
