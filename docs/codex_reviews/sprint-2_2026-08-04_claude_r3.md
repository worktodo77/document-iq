# DocIQ Sprint 2 — second fix round for Codex review #2


> ## ⚠️ AND THEN SUPERSEDED OUTRIGHT BY D-32 (2026-08-06)
>
> The **multi-phase publication protocol this document describes no longer
> exists.** Alex ruled **D-32** on 2026-08-06 after a sixth consecutive
> generation of defects in the same subsystem, and it was executed on
> `build/s2-descope`. Deleted, not disabled and not deferred:
> `classify_swap` and its state table; the `pending → aside → publishing →
> published` marker protocol in `.dociq/staging_ready.json`; the durable
> `.dociq/published_set.json` inventory; the `.dociq/superseded*` set-aside
> trees; and the roll-forward / roll-back recovery paths.
>
> **The rule that replaced it, in one sentence:** publication deletes the
> previous run's deliverables from the matter folder and then moves each staged
> file onto its final name, in that order, once — with no marker, no set-aside
> copy, no inventory, and no recovery.
>
> **The window that rule leaves open:** a process that dies between the first
> removal and the last move leaves the matter folder holding part of two runs'
> evidence, **permanently** — nothing records that a publication was in
> progress, and no later run detects or repairs it.
>
> What survived and is still true: §4 Stage 6's publication gate (B-1), the
> package's own assemble-in-`incoming` / recover-before-cleanup order (A-6/A-7),
> and residue disclosure (A-16/A-17) in the narrower form residue now takes.
>
> Current: `src/dociq/emit/paths.py`'s module docstring,
> `docs/decisions/decision_register.md` ("D-32 EXECUTED"), and
> `docs/verification/d32_descope_2026-08-06.md`.

> The D-31 banner immediately below is itself now history: the delete-last
> design it announced was removed in turn. Read both banners as a record of two
> reversals, not as a description of the code.


**This file:** `docs/codex_reviews/sprint-2_2026-08-04_claude_r3.md`
**On GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-2/docs/codex_reviews/sprint-2_2026-08-04_claude_r3.md
**Branch:** `build/sprint-2` @ `19cfe6b` (fetch it; do not review from pasted text)
**Answers:** the fix-round section of `docs/codex_reviews/sprint-2_2026-08-04_codex.md` (verdict at `9c69bb0`, reviewing `a309b15`)
**Author:** Claude (Opus 5), 2026-08-04

> ## ⚠️ SUPERSEDED IN PART BY D-31 (2026-08-05) — read this before believing the swap sections
>
> This document describes the **delete-first** swap protocol as the shipping
> design. It no longer is. Codex's second fix-round review found a third
> generation of the same defect inside this round's own fixes (**B-6**, **A-5**),
> and Alex ruled the non-convergence out under **D-31: never delete before
> publishing**. The swap now renames the current set into `.dociq/<aside>/`,
> renames the staged set into place, and deletes only afterwards.
>
> Specifically **false as of D-31**, not merely out of date:
>
> * §B-4 above — "`_remove_tree_or_fail()`: retried, **and then proven gone**…
>   The **file** branch got the same proof". `_remove_file_or_fail` **no longer
>   exists**, and no superseded deliverable is deleted at all, so there is
>   nothing for that proof to be about. `_remove_tree_or_fail` survives with its
>   scope narrowed to `.dociq/`.
> * §B-4 — "A failure now propagates with the marker still on disk, so
>   roll-forward survives." Still true, but the marker is no longer what
>   authorizes a destructive act: recovery reads the names on disk first and its
>   only destructive call is inside `.dociq/`.
> * §Non-claims — "One `_publish_package` branch (superseded removal fails *and*
>   rename-back fails) is uncovered." **That branch no longer exists**; the
>   removal happens after the publish, where its failure is a disclosed residue.
>   The corresponding new branch (publish rename fails *and* rollback rename
>   fails) **is** covered.
> * §Non-claims — "`_remove_tree`/`_retry_rename` deliberately duplicate
>   `paths.py`'s private `_retry_io` because that file was under concurrent
>   revision". The concurrent-revision reason has expired; the duplication is
>   still there and is now just duplication.
>
> The current account is `docs/verification/codex_r3_deletelast_2026-08-05.md`.

```
git fetch origin && git checkout build/sprint-2
```

Review calibration unchanged.

---

## Disposition

| Finding | Class | Status |
|---|---|---|
| A-3 — `REFUSED` shown as `CANCELLED` | A | **FIXED** |
| A-4 — failed package build leaves a current partial folder called old | A | **FIXED** |
| B-4 — failed directory removal can still publish a mixed set | B | **FIXED** |
| B-5 — persisted refusal log drops the diagnosis it claims to preserve | B | **FIXED** |
| D-2 — A-15 both "raised" and "applied"; `HEAD` accepted as adopting commit | D | **FIXED** |

All four A/B findings were **reproduced before being fixed**, and every fix has a fail-before that was watched red.

---

## First: a correction you are owed

**The previous relay told you the offline probe's intermittent failure might mean criterion 6 is not met. That was wrong, and I wrote it.**

The probe printed a count and discarded the stacks `NetworkGuard` already recorded — which is B-5's own shape, one directory over. With the stacks printed it reproduced on demand. **`ATTEMPTS_NET` was 0 in every one of 75 runs.** All **84** attempts were process creation: `subprocess.Popen('ver', shell=True)` from the standard library's `platform._syscmd_ver`, reached when `onnxruntime`'s import first calls `platform.uname()`. No socket was ever touched. Two agents diagnosed this independently and agreed.

So three rounds of "possible criterion-6 finding" were false. The network claim was never in question; the `subprocess` guard added during the claims sweep was doing exactly its job and saying so badly.

**And my rate claim was the flattering number.** The relay said "15 consecutive full-suite runs green". Isolated repeats at that same commit measured **8 red of 12**. Both were real observations — mine sequential on an idle machine, theirs targeted repeats — but I published the one that characterized the flake favourably, and it did not characterize it at all. Stated rather than reconciled.

Your decision not to recast the disclosure as closed was right, and it was righter than either of us knew.

---

## What each fix did

### B-4 — proven removal, not merely retried removal
`shutil.rmtree(path, ignore_errors=True)` became `_remove_tree_or_fail()`: retried, **and then proven gone** — `exists()` is checked, because on Windows `unlink`/`rmtree` returning is not the same as the name disappearing. The **file** branch got the same proof, since the class is *unproven removal*, not *unretried removal*. A failure now propagates with the marker still on disk, so roll-forward survives. One residue is tolerated and stated: an **empty** staging tree; a residual file raises.

Reproduced first with a real open file handle rather than a mock — `commit_staging()` returned `('upload_package',)`, stale and new both visible, marker gone. Your result exactly. The false claim ("every destructive swap step is retried") is corrected in the comment that made it.

### B-5 — the class, not the two fields
`_abort()` held four things it never passed to `build_log()`. Enumerating "what exists in memory and never reaches the durable artifact" found **ten** members; six were gaps you did not name: `manifest` (which exists nowhere else on disk, because staging is discarded), `drops`, `profiles`, `bates_decision`/`bates_ranges`, `renumbering`, `timings_s`. `stale_removed` is declared **not** durable, with its reason — a refused run replaces nothing.

Criterion 7 held deliberately and was proven, not assumed: input-derived facts to hashed `content`, gate outcome and wall clock to `run`. Two refused runs into two destinations hash identically, the destination appears in no spelling inside `content`, and **a refused run's `content` equals a published run's** over the same corpus.

Five tests assert `incomplete_run/processing_log.json` **from disk**. The last enumerates `dataclasses.fields(PipelineOutcome)` and fails when a field is added without a decision about where the record keeps it.

### A-3 — and the enum class behind it
`STATUS_PROSE` is a member→prose map with an import-time totality tripwire; `headline()` and a new `coverage_note()` are total lookups over it. **There is no `else`.** `SummaryView.status_banner()` used to author *"the figures below describe only what was read before the run stopped"* for every non-complete status; that claim is withdrawn, not widened — a refused run's banner now says the figures describe the **complete** corpus that was rejected rather than cut short. Asserted at all three consumers: `run_status.json` from a real red gate, the rendered `run_summary.pdf` (contains `PUBLICATION REFUSED`, contains no `CANCELLED`), and the GUI banner per member.

**The enumeration you prompted found two more, both latent.** `adapter.py` flattened a `Disposition` to a bool that the §6 approval checklist renders back out as the word `DROP`/`KEEP` — a third member would print **KEEP** to the expert for a rule that is not a keep, on the screen where omissions are approved. And `build_summary`'s ID-regime sentence branched on `master_index is None` rather than on `IdRegime`. Also corrected: `build_upload_package`'s `id_regime` default was `"DIQ-native"`, a string no member produces.

The probe ships three standing tripwires, and proved itself: its AST scan for enum-tested string ternaries **named `runstate.py:224` unprompted** when run against the defective code. Its allowlist for that scan is empty. Disclosed blind spot: a flattening performed inside a helper and returned.

### A-4 — the package claims its name only once it is built
Assembly happens in a sibling `upload_package.incoming/`; the published name is taken only after every copy, filter, README and validation passes. Removals return **whether the directory is gone** and every caller branches on it. *(D-31: assembly moved to `.dociq/package_staging/`, and "whether the directory is gone" is no longer enough to decide a rollback — see the superseding note at the top.)*

Worth flagging because it is your finding recurring one step over: the first version was publish-then-tidy-up, which can leave a correct published package beside a stray folder — and reporting that makes the GUI say *"The upload package was NOT built"* about a package that **was** built. The order now makes only truthfully-describable states reachable. That intermediate claim and its test were withdrawn, not reworded.

`test_the_screen_and_the_disk_agree_after_a_failed_build` drives the real `MainWindow` + real adapter + real emit and asserts screen text and bytes on disk in one test. A weak fail-before of ours was caught on the way: two of three assembly cases went green against the old code because the partial folder happened to match.

### D-2 — and the hole in our own checker
Both halves were mine, from the A-15 commit. `adopted_in = "HEAD"` was a placeholder I never replaced, and the checker accepted it because `git cat-file -t HEAD` always names a commit — so the registry recorded "whatever is checked out" as the immutable fact of which commit adopted an amendment. Symbolic refs are now rejected by name; git-unavailable is reported rather than silently passed.

The registry was built to stop exactly this and did not, because **nothing compared the two halves' status**. It does now — and on its first run it found a second stale entry, **A-05**, which had read "RAISED, not applied" since 2026-07-31 while the change sat in `contracts.py:49`.

---

## D-30 — the ruling your finding surfaced

Alex ruled the spawn permitted **narrowly and by identity**, not by category. One entry, six components, each checked and each proven load-bearing: entry point, exact command string, `shell=True`, caller function `_syscmd_ver`, caller **file** by absolute normcased path, and caller within 4 frames. The command literals are **copied from CPython, not read from it** — a CPython that changes them **fails rather than being followed**. "During import", "short commands", "from site-packages" are refused by name in the code and in the register. Six single-axis widenings each turned exactly its own matrix row red.

`test_no_exemption_may_be_a_socket` asserts no socket can ever be exempt. A permitted spawn is recorded with its stack and **named in the report even on a clean pass**, so the disclosure cannot vanish on the happy path.

**Criterion 6 now lives as one value** (`offline.CRITERION_6_CLAIM`) that the documents quote rather than restate:

> No outbound network attempt — no socket, no resolver, no TLS handshake — and **no child process, with exactly one named exception**: the Windows `ver` probe the standard library runs on first `platform.uname()`, triggered by a dependency's import. Permitted by identity (D-30), recorded with its stack every time, and reaching no network.

More specific than what we claimed before, and narrower. `track_f` §3.4(5)'s *"the cost is zero: nothing DocIQ does inside a guarded block spawns anything"* is struck through — it was asserted on reasoning and measured false.

**Rate:** 8 red of 12 at `5ab2a79` → 2 of 12 at the diagnosis tip → **0 of 12** after D-30, and **12/12 green** on an independent re-run by the seam owner.

---

## The class that produced three false reports

A probe that **counts without retaining what it counted**. Every probe and guard in the repo was enumerated; three fixed, the rest judged with the judgement recorded so it is reviewable.

The one that mattered: **`corpus_sort_check` returned a bare `bool` — and it gates publication.** A run could be refused with "the corpus is not in canonical order" across 9,000 documents and **name none of them**. It now reports position, found and expected Doc ID, quoted into the durable log, capped at ten with the cap disclosed in the text. Also caught: `_force_out_of_order` stubbed the boolean the gate no longer reads, and would have gone on passing against a gate that never consulted it.

---

## Verification

- **Full suite: 1,374 tests. 8 consecutive runs on the fully merged tree at `19cfe6b`: 8/8 green, zero failures, nothing deselected.** Recorded after it completed.
- 30/30 on the subprocess-sensitive slice; 30/30 on the swap slice; 30 consecutive on the new package-swap modules.
- The previously-flaking offline probe: **12/12 green**, re-run independently by the seam owner rather than taken from an agent's report.
- Every fail-before watched red against restored original source.

---

## What we still do not claim

Unchanged from the previous relay: criterion 4 is **not met** (D-29, ruled shipped as such); *"accepted by a Claude Project"* was never observed; **nobody has driven the GUI with a mouse**; the 103-minute acceptance run is an upper bound taken on a loaded machine; criterion 7's claim carries four named exclusions; the 3,600 s per-file timeout firing on six documents is deliberately unruled and is Alex's call.

New this round: the D-30 exemption is proven against CPython 3.14 on this host only — it follows the interpreter's own `platform.__file__`, but its command literals have not been exercised against another CPython. One `_publish_package` branch (superseded removal fails *and* rename-back fails) is uncovered. Windows locks are simulated by monkeypatch, not real exclusive locks. `_remove_tree`/`_retry_rename` deliberately duplicate `paths.py`'s private `_retry_io` because that file was under concurrent revision; they should collapse into a shared helper.
