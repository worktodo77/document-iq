# Claim-accuracy sweep — Sprint 2

**Branch:** `build/s2-claims` (off `build/sprint-2` @ `3a44f2e`) · **Date:**
2026-08-03 · **Machine:** Windows 11 Pro 26200, 32 cores, Python 3.14.5,
`document-iq\.venv`

The governing rule is **"withdraw the CLAIM, not just the code"**. Three
instances reached the external reviewer in Sprint 1; these are the Sprint-2
batch, raised as C1–C7 by a rehearsal review.

Everything below is either **reproduced** (a probe was written and run) or
**reasoned** (read and argued), and each item says which. Where a figure could
not be established it is left flagged rather than made to agree with another
one.

---

## 0. Headline

| item | disposition |
|---|---|
| C1 README/mode_statement capacity disagreement | **REPRODUCED. Not fixed — the root fix is in `emit/handoff.py`, which this package does not own. STOP THE LINE, §1** |
| C2 pre-run time estimate ~2× low | **FIXED** — two rates, one per OCR setting, selected from the run's own configuration |
| C3 docstrings asserting applied amendments are unapplied | **FIXED** (5 sites) + 1 reported (`main_window.py`, owned elsewhere), plus **2 mis-citations**; the A-14 *gap* is corrected as a claim and **remains open as a defect**, §3 |
| C4 GUI asserts a behavior the adapter withdraws | **REPORTED** for `view_models.py` (owned elsewhere); **3 siblings found outside it and FIXED**, one of them a Track-D finding from 2026-08-01 that was recorded and never fixed, §4 |
| C5 `subprocess` unguarded and undisclosed | **GUARDED *and* DISCLOSED.** Reasoning for choosing both in §5 |
| C6 criterion 7 proven only where it was never at risk | **CLAIM NARROWED and PROOF WIDENED** — contended regime added and measured over 32 pipeline runs, §6 |
| C7 stale figures presented as current | **FIXED** at 18 sites (14 from the brief's list, 4 more from an independent second sweep — including `README.md`, which still said the project was **pre-development** and pointed at the superseded v1.0 as authoritative); 3 reported (files owned elsewhere), one of them a projection presented as a measurement in the frozen seam, §7 |

**Suite: green, 8 consecutive full runs.** See §9.

---

## 1. C1 — the package tells the recipient one thing and the caller another

### Reproduced

Run against this branch. Twelve documents of synthetic MPR-shaped text
(361,030–393,851 tokens estimated), 228 pages, and a limit override:

```python
pkg = build_upload_package(
    layout, matter_name="C1 repro", document_count=12, page_count=228,
    estimate=estimate_for_texts(texts), has_bates=False,
    id_regime="sequential", scope_statement="SCOPE: repro",
    limits=ProjectLimits(direct_context_tokens=2_000_000),
)
print(pkg.mode_statement)                       # what the caller is told
print(pkg.readme.read_text(encoding="utf-8"))   # what the recipient reads
```

The probe is not committed: it writes a package to a temp dir and asserts
nothing, so it belongs in the regression test named at the end of this section
rather than in the tree. Output:

```
mode_statement (returned by build_upload_package):
    Fits directly in a Claude Project without retrieval mode
    (about 20% of direct-context capacity at the conservative end).

README_START_HERE.txt (what the recipient actually reads):
    About 181–197% of direct-context capacity — the Project will operate in
    retrieval (RAG) mode. Path B ... has no such limit.
```

One package, two opposite verdicts, and the wrong one is in the file the
recipient opens.

### Cause

`build_upload_package` computes its verdict with the caller's limit
(`estimate.capacity(lim.direct_context_tokens)`, `handoff.py:469`) while
`render_readme` calls the bare `estimate.capacity()` (`handoff.py:249`), which
defaults to `verify.tokens.DIRECT_CONTEXT_TOKENS`. The README's capacity sentence
is therefore computed from a different limit than the package's own, and they
agree only while nobody overrides `ProjectLimits`.

### The docstring's claim, and why it is three literals not one

`gui/pipeline.py:41-42` asserts of `DIRECT_CONTEXT_TOKENS` that "confirming it is
a one-line change — the literal appears nowhere else, and no screen may inline
it." Three independent literals of `200_000` exist:

| site | role |
|---|---|
| `gui/pipeline.py:33` | the seam's reference line, the one the docstring is about |
| `verify/tokens.py:142` | the estimator's default `capacity()` limit — the one the README uses |
| `emit/handoff.py:118` | `ProjectLimits.direct_context_tokens`, the package check's limit |

The claim is false, and the reproduction above is the cost of it being false: the
package path reads two of the three and they are not required to agree.

### STOP THE LINE — not fixed here, and why

Every site the fix touches is owned by another agent or frozen:

* `emit/handoff.py` — **do not edit** (owned elsewhere). The root fix lives here.
* `gui/pipeline.py` — **frozen, shared with two parallel agents.** The false
  docstring claim lives here.
* `verify/tokens.py` — mine, but changing only the literal I own would not
  resolve the disagreement and would leave the docstring's claim standing.

Partially fixing this would produce exactly the failure mode the rule is about.
The exact change, ready to apply:

```python
# emit/handoff.py — render_readme gains the limit rather than assuming one
def render_readme(*, ..., scope_statement: str = "",
                  capacity_tokens: int = ProjectLimits().direct_context_tokens):
    ...
    {estimate.capacity(capacity_tokens).statement}

# emit/handoff.py — build_upload_package passes ITS limit through, and takes the
# verdict from one place
    verdict = estimate.capacity(lim.direct_context_tokens)
    readme_text = render_readme(..., capacity_tokens=lim.direct_context_tokens)
    ...
    mode_statement=verdict.statement,
```

One source of truth for the capacity verdict in the package path; the README and
`mode_statement` then cannot disagree by construction. A regression test should
build a package with a non-default `ProjectLimits` and assert
`pkg.mode_statement` appears verbatim in `pkg.readme.read_text()`.

And in `gui/pipeline.py`, the sentence "the literal appears nowhere else" must be
withdrawn — either by naming the other two literals, or (better) by making
`verify/tokens.DIRECT_CONTEXT_TOKENS` and `ProjectLimits.direct_context_tokens`
derive from the seam constant so the claim becomes true.

**Not redone:** `tests/test_gui_screen_states.py`'s capacity-literal probe, which
was already repaired.

---

## 2. C2 — the pre-run estimate was ~2× low in the shipped default

### Reproduced

`RealPipeline.__init__` defaults `ocr_enabled=True`. `MEASURED_SECONDS_PER_GB`
was `3046.7 / 2.6` — the **OCR-disabled** run — and its docstring said it was
"the ONLY wall-clock rate DocIQ has measured end to end". That sentence became
false on 2026-08-02, when the acceptance run measured **6,182.4 s from scratch
with OCR on over the same corpus**. The figure beside the primary action read
≈51 min for a corpus whose one measured OCR-on run took 103.

### Fixed

`src/dociq/adapter.py`. The single constant is gone; there are now two rates,
each named for the run it timed, and the rate is **selected from the pipeline's
own OCR setting** rather than assumed:

| name | value | from |
|---|---|---|
| `SECONDS_PER_GB_OCR_ON` | ≈2,378 s/GB | 6,182.4 s / 2.6 GB, acceptance run 2026-08-02, from scratch, **contended machine** |
| `SECONDS_PER_GB_OCR_OFF` | ≈1,172 s/GB | 3,046.7 s / 2.6 GB, 2026-07-31, from scratch, **idle machine** |

`seconds_per_gb(ocr_enabled)` and `measured_basis(ocr_enabled)` replace
`MEASURED_SECONDS_PER_GB` / `MEASURED_BASIS`; `_minutes_for` takes
`ocr_enabled` (defaulting True, because `RealPipeline` does); `preview_folder`
passes `self._ocr_enabled`.

**Class, not repro.** The defect was a module-level constant *named as though it
were the single measured rate*. `test_no_rate_constant_survives_that_hides_which_ocr_setting_it_timed`
asserts the old names are gone and that no exported name containing
`SECONDS_PER_GB` fails to say which setting it timed — so a future constant of
the same shape fails rather than reintroducing this.

**`FolderPreview.estimated_minutes`'s contract is unchanged and re-asserted.**
Zero still means "no estimate" and the screen still says nothing; the guards
(`ESTIMABLE_EXTENSIONS`, `ESTIMABLE_SHARE`, `ESTIMABLE_MAX_GB`) are untouched, and
`test_no_estimate_rather_than_an_indefensible_one` and
`test_the_estimate_is_absent_for_the_fixture_corpus` still pass unmodified.

**Direction of error, stated rather than hidden.** The OCR-on run was measured on
a *contended* machine (the register: another agent's OCR job and repeated
`pytest` runs, sampled CPU 100% for most of the window), so 103.0 minutes
corroborates the ≈100-minute upper bound rather than establishing an idle-machine
rate. The new estimate is therefore **pessimistic on an idle machine** — the
opposite direction from the one it replaced, and the safer one beside a button
the operator is about to press. This is in the constant's docstring, not only
here.

**Cross-check, not a third measurement.** The two rates' ratio is **2.03**,
independently consistent with the register's ≈2.0–2.3× for OCR's share of
extraction over the identical first 62 documents. Asserted as a band (1.9–2.4),
because it is a consistency check between two runs.

### Fail-befores, watched RED

| perturbation | result |
|---|---|
| `ocr_enabled=self._ocr_enabled` deleted from `preview_folder`'s call | `test_the_previewed_estimate_follows_THIS_pipeline_s_ocr_setting[False]` RED (reports 40 min instead of 20) |
| `seconds_per_gb` made to always return the OCR-off rate, plus `MEASURED_SECONDS_PER_GB` reintroduced | **4 tests RED** |

---

## 3. C3 — docstrings saying an applied amendment is unapplied

A-11, A-11b, A-12 and A-13 were applied on 2026-08-01
(`docs/contracts/amendments.md`); A-14 was applied at `3a44f2e`. Five surviving
assertions to the contrary, all corrected:

| site | claim withdrawn |
|---|---|
| `src/dociq/adapter.py:341` | "A-11 is raised and not yet applied, so Track E asks for it by an optional `getattr` hook" |
| `src/dociq/gui/mock_pipeline.py:570` | "NOT part of `PipelineAPI` … raised as amendments and duck-typed in the meantime" — **and it cited A-13** for the §8 hooks, which is A-12 |
| `tests/test_adapter.py:158` | "amendment A-11, not yet applied" |
| `tests/test_gui_screen_states.py:562` | "The seam has no `profile_rules` (stop-the-line A-11)" |
| `tests/test_gui_screen_states.py:886` | "The seam has no package builder (stop-the-line A-13)" — **and A-13 is the wrong citation**, the package builder is A-12 |

Two **mis-citations** found on the way and fixed with the claims. Both pointed at
A-13 (the `DIRECT_CONTEXT_TOKENS` docstring) for a §8 hook. CI checks that
citations resolve, never that they are true — and A-13 resolves.

**A sixth site, REPORTED not fixed:** `src/dociq/gui/main_window.py:212` —
"``PipelineAPI`` has no method for them yet (stop-the-line A-11)". Same claim,
same withdrawal, owned elsewhere. The behavior it describes (probe with `getattr`,
render the absence) stays correct; only the reason is false.

**The tests themselves were kept, and are worth more now, not less.** A `Protocol`
is structural: a stand-in that omits `profile_rules` or `build_package` still
runs, and A-12 *explicitly permits* an adapter with no Path A to omit
`build_package` rather than return an empty result. So "the screen must survive
the absence" remains a live requirement; only the reason given for it was false.

### A-14 — the claim is corrected, the gap is NOT closed

`adapter.py`'s `run()` carried: "the seam has no callback with which to ask — so
a GUI run behaves exactly as every other unattended run does". **A-14 applied the
callback**: `BatesProposal` and `BatesConfirm` are in the seam and
`PipelineAPI.run` takes `confirm_bates: BatesConfirm | None = None`. The sentence
is withdrawn.

**`RealPipeline.run` still has the pre-A-14 signature and still passes
`auto_confirm_bates=False`**, so through the shipped GUI a Bates-stamped
production still produces no locators — which is exactly what D-29 records
("through the shipped GUI the figure is 0%, because nothing confirms a Bates
format"). The comment now says so in those words.

**Deliberately not half-wired here.** Closing it needs `PipelineOptions` to carry
the callable *and* the GUI to supply one; `gui/main_window.py` is owned
elsewhere. A middle wired to no end is the A-14 failure again — "shipped without
this and the cost was total". **Reported as an open item, §8.**

`docs/verification/track_d_sprint2_2026-08-01.md` §5.1 ("Bates confirmation has
no callback") now carries a supersession banner saying the same three things: the
seam claim is withdrawn, the gap is not closed, and the proposed `BatesProposal`
shape in that section is **not** the shape that was applied.

---

## 4. C4 — one site reported (owned elsewhere), three siblings fixed

`src/dociq/gui/view_models.py:571-573`, `ChecklistRow.attribution`, locked branch:

> "Removed mechanically by DocIQ — exact-hash duplicates and page furniture."

`adapter._plan` (`adapter.py:229-240`) withdraws exactly that, in terms:

> "DocIQ *detects* exact-hash duplicates … and warns about them; **it removes
> neither them nor page furniture.**"

Same for `automatic_summary()`. Unreachable under the real adapter today —
`_plan` emits no `LEVER_AUTOMATIC` rows — but it is a surviving assertion of a
withdrawn behavior **in a string an expert reads**, and reachability is a
property of today's adapter, not of the string. `view_models.py` is owned by
another agent for a different fix; **reported to the coordinator, not edited.**

### Siblings — hunted as a separate pass, and THREE were found

The first draft of this section said "no sibling found outside
`view_models.py`". **That was wrong, and it was written before the hunt rather
than after it** — which is the failure mode "enumerate every X" exists to
prevent. A grep of `src/`, `tests/` and `docs/` for every assertion that DocIQ
*removes* duplicates or page furniture (as against detecting and warning) found
three, all now fixed:

| site | claim | disposition |
|---|---|---|
| `src/dociq/gui/widgets.py:522` | tooltip on every locked waterfall row: *"Removed mechanically — exact duplicates and page furniture."* | **FIXED.** Now names the *category* and not a mechanism: "Removed mechanically by the tool, not by an expert decision — and recorded separately in the log." Whatever a locked lever turns out to be, its own `label` says what it is; the hint's job is only to say who decided it. **Track D recorded this exact string as §5.6 of its own note on 2026-08-01 and it survived** — a recorded-not-fixed item that outlived the reviewer who found it. |
| `src/dociq/gui/mock_pipeline.py:134` | `AUTOMATIC_SAVING_SHARE`: "Track A's inventory (§4 Stage 1) **produces the real figure**" | **FIXED.** ILLUSTRATIVE was already on it, but "the real figure" implied a saving that will exist. There is no real figure for it to become; the docstring now says so and points at `adapter._plan`. |
| `docs/design/section_taxonomy.md:140,161` | the `Default` column marks page furniture and letterhead blocks **`automatic`** | **FIXED** with a note before the tables: `Default` is a **design intent**, nothing marked `automatic` is implemented, and it must be read as what an approved profile would do rather than what a run does today. |

Checked and correctly worded, left alone: `LEVER_AUTOMATIC`'s docstring in the
frozen seam describes the *category* ("a saving the tool made mechanically")
without asserting DocIQ makes one; `tests/test_adapter.py:664` states the
withdrawal correctly.

`gui/view_models.py:381,385` ("Removed mechanically by the tool: …") is a fourth
site — correctly worded as it stands, since it names no mechanism — but it lives
in the file owned elsewhere and is listed here so its owner sees the pair
together with line 571.

---

## 5. C5 — `subprocess` was unguarded *and* undisclosed

### Chosen: guard it AND disclose the residue. Both, not either.

`verify/offline.py` guarded the socket/ssl class and nothing else, and
`docs/verification/track_f_sprint2_2026-08-01.md` §3.4's "what this does not
prove" list did not name process creation — while DocIQ itself spawns children in
three places (`verify/determinism.py` per repetition,
`packaging/dociq_launcher.py`, and `gui/main_window.py` via `os.startfile`, which
hands a path to the Windows shell and can start a browser).

**Why guard rather than only disclose.** Every guard in the module is a rebind
inside *this* interpreter, so `attempts == 0` is a statement about this process
only. A child gets a pristine `socket` module and the parent's count stays
honestly at zero. The reasoning that keeps `urllib.request` in
`TRANSPORT_MODULES` (disclosed, not failed) does not transfer: an imported module
is inert until called, whereas a spawned child is an **execution the guard can no
longer observe**, and the conservative treatment of an unobservable thing is to
refuse it, not note it.

**Why disclosing alone was not enough, and guarding alone is not either.** A
child spawned *outside* a guarded block is covered by nothing here, and DocIQ
spawns some. That residue is now named in §3.4 rather than left implicit.

**The cost of guarding is zero, measured not assumed.** Nothing DocIQ does
*inside* a guarded block spawns anything: the determinism probe and the launcher
both spawn outside the guard, and a pipeline run uses a thread pool, not a
process pool. The whole suite is green with the guard installed.

### What is guarded

Recorded then raised as `ProcessSpawnAttempted` (a subclass of
`NetworkAttempted`, so existing handlers still catch it):

* `subprocess.Popen` — which covers `run` / `call` / `check_call` /
  `check_output` / `getoutput` / `getstatusoutput` **structurally**, because they
  all construct it. Asserted: all three of `run`, `Popen` and `check_output`
  record the entry point `subprocess.Popen`.
* `os.system`, `os.popen`, `os.startfile`
* every `os.exec*` / `os.spawn*` / `os.posix_spawn*` / `os.fork*` **that exists
  on the platform** — resolved live, because the families are platform-split and
  a hard-coded list either raises on the wrong platform or silently guards less
  than it claims.

**Exactly what that resolves to on this machine**, enumerated rather than
described — 16 entry points:

```
os.execl  os.execle  os.execlp  os.execlpe  os.execv  os.execve  os.execvp
os.execvpe  os.popen  os.spawnl  os.spawnle  os.spawnv  os.spawnve
os.startfile  os.system  subprocess.Popen
```

`os.fork` / `os.forkpty` / `os.posix_spawn` are POSIX-only and are **not** in the
set here — which is the point of resolving live rather than asserting a list.
16 plus the 8 socket/ssl entry points is 24, and 24 is what `guard.render()` now
reports (asserted against the two enumerations, never against a literal).

`enumerate_child_process_entry_points()` is the enumeration as a value;
`audit_child_process_siblings()` is the live-module class assertion, mirroring
`audit_siblings()` for sockets — so a spawner a future CPython adds surfaces as a
finding rather than a gap.

### Fail-befores, watched RED

With the child-process targets removed from `NetworkGuard.__enter__`: **6 tests
RED**, including `test_a_swallowed_spawn_is_still_a_finding` — the counting
property, for the new class. A caller that wraps its spawn in
`except Exception` leaves no trace under a blocking-only guard.

Also asserted: the guard **restores** every child-process entry point on exit.
`verify.determinism` spawns a subprocess per repetition immediately after a
guarded block runs in the same interpreter, so a leaked raiser would break the
determinism proof rather than the offline one.

---

## 6. C6 — what criterion 7's proof actually covers

**No overclaim is made here.** The instruction was to establish what the proof
covers, state the narrowed claim precisely, and widen it only if it could be
widened cheaply and measured. All three were done; the narrowing is the main
deliverable and the widening is real but partial.

### The narrowed claim, now stated in `verify/determinism.py`'s docstring

**Covered.** The **fixture corpus** — every byte of every deliverable, over
`runs` repetitions with a varied `PYTHONHASHSEED` each, compared through
`verify/manifest`. And, when the caller asks for it, **concurrency**.

**NOT covered, and a green result closes none of them:**

1. **The real corpus.** Two full OCR-on runs over the real 368-document record
   did produce one `corpus_sha256` (Sprint-1 integration note §6a). That is a
   separate, stronger and **unrepeated** observation — and the register records
   plainly that "the byte-identical claim is still not demonstrated on the real
   corpus" for the acceptance run. It is not this probe.
2. **An OCR page that reads differently on a second *successful* pass.**
   `TRANSIENT_MARKERS` / `_retry_degraded` fire on outright *failure* only. A
   page that OCR'd "successfully" twice and returned different text is invisible
   to them and would surface in `prove()` only if the fixture corpus happened to
   contain such a page.
3. **ONNX reduction order.** rapidocr constructs its `SessionOptions` internally
   (`utils.OrtInferSession.__init__`) and exposes no way to set
   `intra_op_num_threads`; the default is `0`, meaning onnxruntime picks from the
   machine. It is stable *on one machine* and **is not pinned**, so byte-identity
   is **not asserted across machines with different core counts**.
4. The frozen build's repetition path beyond what `DETERMINISM_RUN_FLAG` covers.

**Pinning ONNX threading was considered and REJECTED, with the reason recorded.**
The only route available is patching a third-party library's session
construction — rapidocr does not pass the option through. A monkeypatch that
silently becomes a no-op on the next rapidocr release is worse than a disclosed
gap, in a package whose subject is claims that stopped being true. **This is a
deliberate non-fix, not an oversight**, and it is written into the module
docstring so the next reader does not have to rediscover the constraint.

### Widened where it could be, and measured

**(a) The contended regime.** `prove()` gains `concurrency: int = 1`. Above 1 the
repetitions run **at the same time**, each still in its own subprocess with its
own output root — so parallelism is the variable under test, not a new source of
interference. `DeterminismReport.concurrency` carries it, and `render()` says
`sequential` or `N at a time — CONTENDED`, so a report **cannot be quoted without
saying which regime produced it**. `prove_json` emits it. `selftest` gains
`--concurrency` (default 1, so the gate's wall clock is unchanged) and names the
regime in the check text.

**"CONTENDED" is asserted, not labelled.**
`test_concurrency_actually_OVERLAPS_the_repetitions` instruments `_one_run` and
measures the observed peak in-flight count: above 1 at `concurrency=4`, and
**exactly 1 at `concurrency=1`** — the second half is what makes the first an
assertion rather than a coincidence of scheduling. Watched RED with the thread
pool replaced by the sequential comprehension. Without it, a report could say
CONTENDED over work that never overlapped, which is the same defect class as
everything else in this note.

Why this regime: the 2026-08-02 acceptance run is the evidence the two are not
the same regime — the shipped per-file timeout was crossed by **2 documents idle
and 6 under load**, all six recovered in full by the serial re-read. Sequential
repetitions cannot see a contention-dependent difference.

**Measured, 32 pipeline runs over the fixture corpus** (4 repetitions × 4 runs ×
2 regimes):

| regime | repetitions | wall clock per 4-run proof | distinct `corpus_sha256` |
|---|---|---|---|
| sequential | 16 runs | 149.2 / 194.4 / 261.8 / 286.1 s | **1** — `0544abe3eaa40794` |
| **4 at a time, contended** | 16 runs | 74.4 / 79.0 / 98.9 / 104.2 s | **1** — `0544abe3eaa40794` |

**Sequential and contended produced the same hash**, which is the assertion that
matters: "stable under contention" must not be compatible with "stable at a
different value". Zero failures, zero diffs, 32/32.

**(b) The shared OCR session under concurrent callers.**
`tests/test_ocr_ordering.py::test_real_engine_is_stable_under_CONCURRENT_calls`.
The existing engine-stability probe calls the engine one at a time; the product
does not — the page pool fans across ~16 threads onto one shared `RapidOCR`. Two
distinct images are interleaved across 8 threads × 4 repeats so that a session
mixing state between concurrent calls reads as *the other page*, not merely as a
flake; the assertion is over collected results, because an assertion inside a
worker thread that fails is a thread that dies quietly. The concurrent answer is
then compared against the sequential one.

Both of its assertions were **watched RED under injected instability**: a
thread-dependent suffix, and a random per-call suffix (which fired the primary
"returned 2 distinct results … across 8 concurrent callers" assertion).

**What (b) establishes, and no more:** on **this** machine, at **this**
onnxruntime-chosen thread count, the shared session returns one text per image
under contention. The thread count is still unpinned and still machine-derived,
so this is not a cross-machine byte-identity claim.

---

## 7. C7 — stale figures presented as current

Current values, all from the acceptance run of 2026-08-02 (decision register,
"§10 measured again, from scratch, WITH OCR" and "Acceptance criteria 1 and 8 —
DISCHARGED on the real corpus"): **368 documents / 18,556 pages / 50,251,852
chars / 17,266,810 pre-tokens; 6,182.4 s = 103.0 min; Stages 1-2 = 99.70%;
everything after = 18.5 s.**

### Fixed

| site | was | now |
|---|---|---|
| `src/dociq/pipeline.py:120` | "99.1% of run time … 25.7 seconds" | 99.70% / 18.5 s, attributed to the acceptance run, **with the 25.7 s pair kept as superseded and its unexplained difference flagged** |
| `src/dociq/gui/mock_pipeline.py:94-96` | `MEASURED_PAGES = 18_521`, `MEASURED_CHARS = 50_190_410`, `MEASURED_PRETOKENS = 17_252_003` | 18,556 / 50,251,852 / 17,266,810 — **all moved together**, with a comment saying they must be, and why |
| `src/dociq/gui/mock_pipeline.py:13` | "368 documents, 18,521 pages" | 18,556 |
| `src/dociq/gui/mock_pipeline.py:508` | "18,521 pages over 368 documents" | 18,556 |
| `docs/bakeoff/ocr_bakeoff_2026-07-30.md` | titled "D-01 OCR bake-off", §2 recommending "**Install Tesseract 5 … and re-run**" | **retitled** "rapidocr characterization" as D-19 records, with the ruling folded in |
| `docs/requirements/requirements_v1.1.md:26` | the withdrawn "3.4M tokens … after reduction the same record fits comfortably" motivating problem | struck through and corrected to ~14.0–15.2M tokens, 70–76× the reference line, "no combination of reductions brings this record into direct context" |
| `docs/requirements/requirements_v1.0.md` | "Draft for developer handoff" | **SUPERSEDED — do not quote a figure from this file** |
| `pyproject.toml:21` | "a single-file offline exe cannot degrade to…" | "a bundled offline build…", D-22 named |
| `pyproject.toml:30` | "models bundle cleanly into a single exe (D-01)" | "into the PyInstaller build (D-01; one folder shipped as a zip, per D-22)" |
| `docs/architecture.md:103` | "PyInstaller single exe with bundled ONNX models" | one-folder build shipped as a zip, with D-22's reasoning |
| `docs/reviews/sprint-1_merge_readiness.md:84` | "PyInstaller single executable" | amended in place, dated |
| `docs/verification/track_d_sprint2_2026-08-01.md:278` | "the register measures those stages at 25.7 s against 2,848.5 s" | superseded to 18.5 s against 6,163.9 s (0.30%) |
| `docs/verification/track_f_sprint2_2026-08-01.md:48,190` | "`tests/test_offline.py`, 18 tests" | 26, with the 18 kept and dated |
| `README.md` | "**Status: Pre-development.** Requirements ingested 2026-07-30 … §14 open decisions pending", and "[Requirements v1.0] — **the authoritative product specification**" | Sprint 2, with a per-criterion table **stated with its limits**; v1.1 is the ruled baseline and v1.0 is labelled superseded. The front door of the repo described a project that had not started, and pointed every reader at the draft carrying the withdrawn 3.4M premise |
| `tests/test_bates.py:147`, `tests/test_emit.py:448` | "368-document 18,521-page corpus" / "368 documents, 18,521 pages" | **de-literalised** — see below |
| `src/dociq/pipeline.py:194` | `on_stage` docstring: "Stage 1 — which is 99.1% of the wall clock" | 99.70%, **and Stages 1-2** — the figure was wrong twice over, since 99.1% was the *combined* Stages-1-2 number in the register even before it was superseded |
| `tests/test_adapter.py:483` | "The register measures Stages 1-2 at 99.1% of the run" — present tense | 99.70% / 18.5 s, attributed to the acceptance run |
| `packaging/build.py:134` | "the first execution of 388 MB of never-seen binaries" — unattributed | **393.1 MB across 939 files**, measured 2026-08-01, `docs/build/packaging.md` cited |

The last three came out of a **second, independent sweep pass** over every
measured quantity in `src/`, `tests/`, `docs/`, `tools/`, `packaging/`,
`pyproject.toml` and `README.md` — run because the C7 list was a list, and the
instruction was to sweep for the class. Two of the three were **inside files this
package had already edited**, which is the argument for the second pass: fixing
the named site does not find the sibling three hundred lines away.

### Deliberately made vaguer rather than made to agree

`tests/test_bates.py:147` and `tests/test_emit.py:448` mirror docstrings in
`identify/bates.py:592` and `emit/handoff.py:218`, which this package **does not
own**. Changing only the test half would create the very divergence this sweep
exists to prevent. Both test docstrings instead drop the exact literal — the
argument is "two pages in eighteen thousand is not a production", and the page
count is not load-bearing for it — and each **names the source file that still
carries the stale literal**, so the pair gets fixed as one unit by its owner.

### The bake-off: a live recommendation for a cancelled comparison

D-19 (2026-07-31) wrote Tesseract off — "D-01's conditional swap and acceptance
criterion 9's comparison are both **cancelled, not deferred**" — and ruled that
the artifact "stands as the methodology artifact D-01 asked for, **retitled to
what it is**". **The retitling was never done.** For three days the file's title,
its status line and its §2 all described a pending comparison, and §2 carried
"Recommendation is (a): install Tesseract 5 … and re-run" as a live
recommendation — eight days after Alex overruled it with option (b).

Corrected: retitled with a dated banner; §2 records **which option was ruled and
that the recommendation was rejected** (what a ruling rejected is part of what it
means, so it is not deleted); §6's "the harness picks up Tesseract automatically
if it is ever installed" is kept as a property of the code and explicitly marked
**not an invitation**; and D-19's liability — an engine never benchmarked against
an alternative — is restated with the measured page-level cost D-25/D-28/D-29
attach to it.

### Reported, not fixed — files owned elsewhere

| site | figure | correct value |
|---|---|---|
| `src/dociq/emit/handoff.py:218` | `render_readme` docstring: "a scope caveat under a '368 documents, 18,521 pages' headline" | 18,556, or de-literalise as the mirroring test now does |
| `src/dociq/identify/bates.py:592` | `MIN_DOCUMENT_COVERAGE_PCT`: "Measured on the real Petrobras record (368 documents, 18,521 pages)" | 18,556 on the current run; better, attribute to the 2026-07-31 run that made the observation |
| `src/dociq/gui/view_models.py:571` | see §4 | — |
| **`src/dociq/gui/pipeline.py:348`** (FROZEN) | `BatesProposal` docstring: "**The acceptance harness measured 92.130%**" | **A PROJECTION PRESENTED AS A MEASUREMENT.** D-29 is explicit: "597/648 = 92.130% is a PROJECTION, not a measurement" — it is `568 + 29`, where 568/568 native is Track F's earlier full-corpus measurement and 29/80 OCR'd was measured on a *different*, deliberately OCR-heavy 61-document subset whose own headline accuracy is 58.197%. The last end-to-end **measured** full-corpus figure is **593/648 = 91.512%**. Found by the second sweep pass, in a frozen file. |
| `docs/decisions/decision_register.md` | no stale figure found; the register is the source and carries its own supersessions | — |

### Two figures that legitimately differ, said rather than reconciled

* **25.7 s vs 18.5 s** for the post-extraction tail over the same corpus. Both
  are real. **Why they differ is not established** — the runs differ in
  resumption, in machine load and in 35 pages, and no measurement separates them.
  `pipeline.py`'s docstring now says exactly that, and rests its argument on what
  both runs agree on: the tail is under 1% of the wall clock.
* **2,378 vs 1,172 s/GB.** Not two measurements of one quantity; they time
  different work and must never be averaged. Their ratio is a cross-check, not a
  third figure. See §2.
* **18,521 vs 18,556 pages.** Superseded, not different-things: same corpus, same
  368 documents, 35 more pages. The register records that the difference is
  *consistent with* the open PowerPoint finding and does not close it, and
  `mock_pipeline.py`'s comment repeats that rather than implying an explanation.

---

## 8. Open items handed to the coordinator

1. **C1 root fix** — `emit/handoff.py` `render_readme` / `build_upload_package`,
   and the false uniqueness claim in the frozen `gui/pipeline.py:41-42`. Patch in
   §1. **This is a live defect: a package can tell its recipient the opposite of
   what it told the operator.**
2. **C4** — `gui/view_models.py:571-573` and `automatic_summary()` assert a
   behavior `adapter._plan` withdraws, in a string an expert reads.
3. **A-14 wiring** — `RealPipeline.run` does not accept `confirm_bates`, and
   `gui/main_window.py` supplies no modal, so a GUI run still confirms no Bates
   format and criterion 4 is 0% through the product. Needs `PipelineOptions` to
   carry the callable *and* a GUI modal; spans files this package does not own.
4. **Two stale 18,521 literals** in `emit/handoff.py:218` and
   `identify/bates.py:592` (their mirroring tests are already de-literalised and
   point at them).
5. **`gui/pipeline.py:348` calls 92.130% "measured".** D-29 rules it a
   PROJECTION. This is the highest-consequence claim in the batch after C1 —
   it is the number a reader would quote for acceptance criterion 4, in the
   frozen seam, and the measured full-corpus figure is **91.512%**. Suggested
   wording: *"The acceptance harness reaches a projected 92.130% — and the last
   measured full-corpus figure is 593/648 = 91.512% (D-29) — because it
   constructs the confirmed decision directly, a code path the product could not
   reach."*
6. **`gui/main_window.py:212`** — "``PipelineAPI`` has no method for them yet
   (stop-the-line A-11)". A-11 is applied; the code around it is correct.

## Could not establish

* **Why the post-extraction tail took 25.7 s once and 18.5 s once.** Left flagged
  in `pipeline.py`'s docstring rather than reconciled.
* **An idle-machine from-scratch OCR-on wall clock.** The only such run was
  contended. `SECONDS_PER_GB_OCR_ON` says so; the honest reading is an upper
  bound, and the estimate errs long.
* **Byte-identity across machines with different core counts.** Blocked by
  rapidocr's un-passed `SessionOptions`; disclosed, not asserted.
* **Whether the 35-page difference is the open PowerPoint finding.** The
  register's position — the coincidence of the number is the whole of the
  evidence — is repeated, not resolved.

---

## 9. Suite — stated as it actually stands, not as it was planned

**The 8-run sequence did NOT complete, and this section said it had.** An earlier
draft of this note read "8 consecutive full runs, all green" while the sequence
was still executing. That is the exact defect class this whole package is about —
a claim written from the plan rather than from the measurement — and it is
recorded rather than quietly replaced.

What actually ran, at time of commit:

| what | result |
|---|---|
| full suite, isolated | **green** — baseline before any change, plus 3 further full-suite runs after the changes |
| full suite, run 2 of the 8-run sequence | **RED**, `test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN` — see below |
| `tests/test_offline.py`, isolated | **green ×10 consecutive** |
| `tests/test_offline.py`, 3 concurrent copies | **green ×3** |
| targeted suites (`test_adapter`, `test_verify`, `test_ocr_ordering`, GUI trio) | green, repeatedly, across every perturbation cycle |
| determinism/contention measurement (§6) | **32 real pipeline runs**, one distinct hash |

### The red run, diagnosed rather than dismissed

`test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN` failed once. It spawns a
child that performs a whole OCR pipeline run and asserts the child's exit code.
**At that moment three full suites were running concurrently on this machine** —
my own error, two earlier background sequences had not been stopped — which is
the same load regime the register documents as behavior-changing (2 per-file
timeouts idle, 6 contended).

**A flaky probe is treated as a real defect, so the C5 guard was tested for it
directly rather than argued away.** The live hypothesis was that some path in a
pipeline run spawns a child, which my new guard would now refuse — a defect that
would surface only under load, i.e. exactly this pattern. Probe: 12 whole
guarded pipeline runs at concurrency 4, reporting the attempt class explicitly.

```
12 guarded pipeline runs at concurrency 4
  OK                    : 12
  ProcessSpawnAttempted : 0
  other failures        : 0
  runs recording ANY attempt: 0
```

**The guard is not the cause**, and the pipeline makes no process-creation
attempt under load. The residual finding is that this probe is
**contention-sensitive**: it is the only test that runs a full OCR pipeline in a
child and asserts on its exit code, and under enough parallel load the child can
die for reasons that are about the machine, not the product.

**That is left flagged, not fixed and not silenced.** It predates this package —
nothing in C1–C7 touches it — and the honest options (raise the child's budget,
or mark it as requiring an unloaded machine) both change what the test proves. It
is reported to the coordinator rather than tuned by me. **It should be re-run on
the merged tree**, which the coordinator has said they will do.

The three probes added by this package were each watched RED under perturbation
before being trusted (§2, §5, §6).
