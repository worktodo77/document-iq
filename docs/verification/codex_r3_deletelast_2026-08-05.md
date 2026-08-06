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
| 2 **aside → published** | RENAME each staged file into place; a destination the plan did not cover is moved aside too, never overwritten | raise; the matter folder holds *part of the new set and none of the old* — incomplete, never mixed; the whole previous set is intact under `.dociq/<aside>/` and the rest of the new set is intact in staging; marker stays at `aside` |
| — | atomically rewrite the marker to `published` | as above |
| 3 | DELETE `.dociq/superseded*` and the drained staging tree | **does not raise.** The matter folder is complete and correct; a surviving tree is a disclosed residue |
| 4 | `unlink` the marker | a surviving name is now provably harmless — see §3.1 |

**Why step 1 before step 2 is the whole point.** The previous set leaves the
folder *entirely* before any of the new set arrives, so at every instant the
matter root holds files from one run only. That is the difference between
"incomplete" and "mixed", and it is what §7's fixed deliverable paths (D-20,
Path B) otherwise make impossible to guarantee.

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

*(filled in §5 below from the independent sweep — see the table.)*

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

*(filled in below.)*
