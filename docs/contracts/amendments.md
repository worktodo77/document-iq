# Contract amendment register

Cases where `src/dociq/contracts.py` v1.0.0 cannot express something a track
must produce or display, raised under the stop-the-line rule in
`pagemodel_freeze.md`. Each entry states the concrete case and why a local
workaround would be wrong.

---

## A-01 — `RunResult` cannot carry the token estimate

**Raised by:** Track C (GUI shell), 2026-07-30
**Affects:** `RunResult`; Stage 6 (verify), the emit layer, the summary screen
**Proposed severity:** MINOR (additive field with a safe default)

### The case

The token estimate is the product's headline number. §7 requires the emit layer
to write "token estimate before/after" into `run_summary.pdf`; §9 requires the
summary screen to show it as the oversized headline plus the D-07 capacity
gauge; §4 Stage 6 lists "token count estimate" as part of the post-run
self-check. D-03 fixes how it is computed: characters ÷ a ratio calibrated
against the real Claude tokenizer, displayed as a conservative range.

`RunResult` carries `config`, `documents`, `unsupported` and `warnings`. There
is nowhere to put the estimate, the ratios it used, or the character counts it
was derived from.

### Why a local workaround would be wrong

The GUI *could* sum `len(page.text)` and divide. That would put a second
estimator in the product, in a widget, disagreeing with the pipeline's estimator
the first time either changes — and the two numbers would then appear in the
same matter folder, one on screen and one in `run_summary.pdf`. It also breaks
the freeze's Track-C rule: the GUI holds no pipeline logic.

`warnings: tuple[str, ...]` could carry the number as prose. Parsing a number
back out of a display string is a worse coupling than the field it avoids.

### Proposed shape (for the central amendment, not applied here)

```python
@dataclass(frozen=True, slots=True)
class TokenEstimate:
    chars: int
    ratio_low: float      # reporting-only, like ocr_conf: excluded from identity
    ratio_high: float

# on RunResult, both defaulted to None so the change is additive:
tokens_before: TokenEstimate | None = None
tokens_after: TokenEstimate | None = None
```

`ratio_low`/`ratio_high` are floats, so they must join `_IDENTITY_EXCLUDED` —
`to_jsonable(for_identity=True)` raises on any float it meets, which is exactly
the guard that would otherwise catch this late. `chars` is an int and can stay
in identity.

### What Track C did in the meantime

Nothing was added to the contract and nothing is computed in a widget. The GUI's
pipeline seam (`src/dociq/gui/pipeline.py`) defines a presentation record
`TokenEstimate` that the *adapter* supplies alongside the `RunResult`; the mock
fills it, and Sprint 2's real adapter must source it from Stage 6 rather than
recompute it. When this amendment lands, the seam's record becomes a thin read
of the contract field and the GUI does not change.

---

## A-02 — `RunResult` cannot carry the master-index reconciliation

**Raised by:** Track C (GUI shell), 2026-07-30
**Affects:** `RunResult`; §5 reconciliation, the index workbook's reconciliation
tab, the summary screen's flag chips
**Proposed severity:** MINOR (additive field with a safe default)

### The case

§5 makes the reconciliation a first-class deliverable with three categories —
in both (with field discrepancies flagged), folder-not-index, index-not-folder —
written as a tab of the index workbook. §9 requires "reconciliation mismatches"
to be one of the summary screen's flagged items, with click-through to detail.

`RunResult` has no field for it. `DocumentRecord.li_file_no` records the *result*
of a successful match on one document, which is not the same information: it
cannot express an index row with no file at all, and it cannot express a field
disagreement between a matched pair.

### Why a local workaround would be wrong

Reconstructing the categories in the GUI would require the GUI to hold the
master index and re-implement the §5 match rules (filepath+filename primary,
SHA-256 secondary, Bates when present) — pipeline logic, in a widget, in a track
that is forbidden from importing `identify/` or `docid/`. Two implementations of
the match rule would eventually disagree, and the one the client sees in the
workbook and the one on screen would be the two that disagree.

### Proposed shape

```python
@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    category: str      # "folder-only" | "index-only" | "field-mismatch"
    doc_id: str
    filename: str
    detail: str

@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    matched: int
    rows: tuple[ReconciliationRow, ...] = ()

# on RunResult:
reconciliation: ReconciliationReport | None = None   # None = no index supplied
```

All-string/int fields, so identity hashing is unaffected.

### What Track C did in the meantime

Same as A-01: the shape above lives as a presentation record in the GUI's
pipeline seam and is supplied by the adapter, not computed by the GUI. The mock
fills it; Sprint 2's adapter must read it from the pipeline.

---

**Status of A-01 and A-02:** ACCEPTED by the coordinator 2026-07-30, being
applied centrally as contract **v1.1.0** (additive, safe defaults), with two
additions to the proposed shape — `TokenEstimate.floor_tokens` and
`TokenEstimate.provenance`. Track C's seam records have been reshaped to match,
so the rebase turns them into a thin read of `RunResult.tokens_before` /
`tokens_after` / `reconciliation`. Track C did not modify `contracts.py`.

---

## A-03 — nowhere to carry "the configured ratio was refuted"

**Raised by:** Track C (GUI shell), 2026-07-30, on the v1.1.0 shape
**Affects:** `TokenEstimate`
**Proposed severity:** MINOR (additive field with a safe default)

### The case

The coordinator's instruction is explicit: *"when `ratio_refuted` is set (Track
B's `verify/tokens.py` sets it when the text's own structure contradicts the
ruled band), the screen must say so plainly."* The announced v1.1.0
`TokenEstimate` carries `floor_tokens` and `provenance` but no such flag.

The distinction is not cosmetic. Three states have to be told apart on screen:

| state | what the screen must say |
|---|---|
| estimated from a ratio that holds | "estimated — <provenance>" |
| counted, a hard floor | "a floor, not an estimate — <provenance>" |
| counted, **and the configured ratio is impossible on this text** | "…; the configured ratio did not fit this text, so it was not used" |

The third is the one that matters most, because it is the case the real corpus
is in: 2.53 chars per pre-token against a ruled band of 3.3–3.6.

### Why a local workaround would be wrong

The GUI could infer it — compare `chars / floor_tokens` against `ratio_low` and
decide for itself. That puts the refutation *test* in a widget, where it would
sit alongside Track B's implementation of the same test in `verify/tokens.py`
and eventually disagree with it. Which value the screen shows and which the
`processing_log` records would then depend on which code ran.

Parsing it back out of the `provenance` string is worse: it makes a prose field
load-bearing, so rewording a sentence changes what the screen asserts.

### Proposed shape

```python
ratio_refuted: bool = False   # on TokenEstimate, defaulted False
```

Boolean, so identity hashing is unaffected.

### What Track C did in the meantime

The seam's `TokenEstimate` carries the flag explicitly and the mock sets it from
a measurement of its own text. **Nothing in the GUI decides refutation.** If the
field is declined, the GUI needs a defined provenance vocabulary instead — but
it must not be left to a widget either way.

---

**Standing note:** the real adapter cannot be written honestly until A-01 and
A-02 land, because it would have nowhere to read these values from.

---

> **Numbering note.** Two fix packages ran concurrently and both claimed
> A-05. The token-proxy/limits pair keeps A-05; the run-status amendment
> is renumbered A-06. Both are APPLIED — see `contracts.py` 1.4.0 and
> 1.5.0. A-05(b) is dispositioned NOT NEEDED, with the reasoning recorded
> in the 1.4.0 version note.

## A-05 — two gaps found while closing Codex review #1 findings B-2 and B-6

**Raised by:** the B-2/B-6 fix package, 2026-07-31, against contract **v1.3.0**
**Affects:** `TokenEstimate.floor_tokens` (documentation), `EffectiveLimits`
**Proposed severity:** MINOR for the first, DISPOSITION-ONLY for the second
**Status:** RAISED, not applied. `contracts.py` was not modified by this package.

### (a) `TokenEstimate.floor_tokens` documents a claim that has been withdrawn

The field's docstring says:

> Hard lower bound on the true token count, from the text's pre-token count: a
> byte-level BPE tokenizer cannot merge across a pre-token boundary, so it can
> never emit fewer tokens than this.

Codex review #1 finding B-6 established that this is not true, and the finding
is accepted. A byte-level BPE cannot merge across **its own** pre-token
boundaries; DocIQ's `PRETOKEN_RE` invents boundaries of its own — it splits
digit runs every three digits — and a real tokenizer with coarser
pre-tokenization merges straight across them and emits *fewer* tokens than DocIQ
counts pre-tokens. On material that is 13% digits the effect is material, not
nominal.

**What this package did instead of editing the frozen file.** The pipeline now
sets `floor_tokens=0` — its "not measured" value — because the pre-token count
cannot honestly be shipped in a field the contract defines as a hard bound. The
measurement itself is not lost: it travels in `TokenEstimate.provenance`, and
the processing log's `token_estimate` block records `pretokens`, `utf8_bytes`,
`token_ceiling`, the method used, and the three assumptions, each labeled for
what it is.

**Proposed contract-side repair (v1.4.0, additive):**

```python
structural_tokens: int = 0   # tokens implied by measured pre-token structure
                             # under stated assumptions; NOT a bound
token_ceiling: int = 0       # tokens <= UTF-8 bytes; sound for any byte-level
                             # tokenizer, and the only bound DocIQ asserts
```

and a docstring correction on `floor_tokens` marking it reserved/unused until
a real tokenizer measurement exists to fill it. Until then a consumer reading
`floor_tokens == 0` correctly learns "no bound was established", which is the
true state of the world.

### (b) `EffectiveLimits` has no field for the disk-headroom multiplier

A-04 captured the environment-controlled settings that can change output
evidence. `DOCIQ_DISK_HEADROOM` (`walker._DISK_HEADROOM`, default 1.15) is
environment-controlled and has no field.

**Disposition taken, and the reasoning, so a reviewer can overrule it.** It is
not recorded in the identity, on two grounds:

1. It gates whether the run *starts* — the preflight either blocks the run or
   lets it proceed — rather than what a completed run emits. Two completed runs
   with different headroom multipliers over the same inputs produce the same
   bytes. (The blocked case is Codex finding B-1's subject, not B-2's: a blocked
   run must acquire a terminal status, not a different content hash.)
2. It is a float, and Principle 5 bars floats from identity fields. Encoding it
   as hundredths would be possible, but see (1).

It **is** recorded, as `run.pool.disk_headroom_x100` in the processing log's
unhashed `run` section, alongside the two pool widths (`workers`,
`ocr_page_workers`) which are excluded from identity on the separate ground that
pool width must not change output — and `tests/test_run_identity.py`
`test_pool_width_does_not_change_the_output_bytes` measures that claim rather
than assuming it.

If a reviewer judges (1) wrong, the repair is a `disk_headroom_x100: int` field
on `EffectiveLimits` and a one-line change in `walker.effective_limits`.

---

## A-06 — `RunResult` cannot say whether the run COMPLETED

**Raised by:** the B-1 fix branch (`fix/codex-r1-a`), 2026-07-31, from Codex
review #1 finding B-1
**Affects:** `RunResult`; Stage 1 (walk), the pipeline's publication decision,
the log, the run summary, the GUI seam
**Proposed severity:** MINOR (additive field with a safe default)
**Status:** **APPLIED — contract v1.5.0**, then substantially revised by A-07 at
v1.6.0.

> **Register correction (D-R2-1, Codex review #1 round 2).** This entry read
> "RAISED — stop-the-line, not applied", said `contracts.py` was not modified,
> and described the `runstate.TerminalStatus` re-export as future work. All
> three statements were stale: the amendment landed centrally as 1.5.0 while
> this text still described the branch that raised it. Codex correctly read the
> staleness as editorial evidence of the unfinished integration underneath it —
> the fields existed and nothing populated them, and the predicted re-export
> never happened. Both are now done; see A-07.
>
> The lesson is procedural, not cosmetic: an amendment's status here is the
> only place a reader can check whether a contract change was *wired through*
> rather than merely declared, so leaving it stale hides exactly the failure it
> would have caught.

### The case

Codex finding B-1 requires "a typed terminal status that prevents normal
publication, or [that] make[s] the correctness gates fail while preserving the
last complete deliverables", and states that the status "must also be visible in
the machine-readable run result, manifest, log, PDF, and GUI."

`RunResult` carries `config`, `documents`, `unsupported`, `warnings`,
`tokens_before`, `tokens_after` and `reconciliation`. None of them can express
*how the run ended*. The two failure modes are indistinguishable from a good
run by inspection of the type:

- a blocked run returns `RunResult(config=..., warnings=(message,))` — an empty
  document set, which is what an empty folder also produces;
- a cancelled run returns the partial document set, which is what a small
  matter also produces.

### Why a local workaround would be wrong

`warnings: tuple[str, ...]` can carry the sentence, and on this branch it does —
the disclosure goes in first. But a consumer deciding whether to trust a run
would then have to match a prefix against a display string, which is exactly the
coupling A-01 and A-03 were raised to avoid, and rewording a sentence would
change what a consumer asserts.

Inferring it is worse: "zero documents means blocked" is false for an empty
folder, and "fewer documents than files scanned" is false for a corpus of
unreadable files.

### Proposed shape

```python
# in contracts.py, mirroring src/dociq/runstate.py:
class TerminalStatus(str, enum.Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

# on RunResult, defaulted so the change is additive:
terminal_status: TerminalStatus = TerminalStatus.COMPLETED
terminal_status_reason: str = ""
```

An enum with a string value and a string, so identity hashing is unaffected in
kind — but see the note below: it must join `_IDENTITY_EXCLUDED`. Two runs over
byte-identical inputs can legitimately differ in it (the operator cancelled one
of them), so hashing it would make the byte-identical claim false for a reason
that has nothing to do with the evidence. That is the same argument
`walker.RunNotes` already makes for the retry and resume disclosures.

### What this branch did in the meantime

`src/dociq/runstate.py` — a small non-contract module, importable by the GUI
under the freeze's Track-C rule — defines `TerminalStatus` and
`RunTermination`. `walker.RunNotes` carries it (it is a fact about the
invocation, which is precisely what `RunNotes` is for), `PipelineOutcome`
exposes it as `termination` / `published`, and it is rendered in
`processing_log.json`'s `run` section, in `run_summary.pdf`, in the GUI seam's
`RunOutcome` and in `SummaryView.status_banner()`.

**`RunResult` itself still cannot say it.** In practice a consumer holding only
a `RunResult` from an aborted run holds one that was never written to disk —
the pipeline publishes nothing — and the first entry of `RunResult.warnings` is
the disclosure. That is a mitigation, not the field Codex asked for. When this
amendment lands, `runstate.TerminalStatus` becomes a re-export and the change is
a one-line read in each of the sites above.

---

## A-07 — one `TerminalStatus`, actually populated, and NOT in the corpus identity

**Raised by:** the round-2 fix package, 2026-08-01, from Codex review #1 round 2
finding **B-R2-1**, against contract **v1.5.0**
**Affects:** `TerminalStatus`, `RunResult.terminal_status`,
`RunResult.terminal_status_reason`, `_IDENTITY_EXCLUDED`; `runstate`, the walk,
`pipeline._abort`, the GUI mock, the manifest's identity note
**Severity:** MINOR
**Status:** **APPLIED — contract v1.6.0.**

### The case

A-06 landed the typed status in the contract and nothing connected it to the
shipping path. `runstate.py` still declared a second, value-identical
`TerminalStatus`; the walk carried that one while `RunResult` declared the
contract's, so an `is` comparison across the seam answered `False` about two
statuses that were the same status. And because A-06 defaulted the field to
`COMPLETED` to keep the change additive, **every abort path took the default**.
Codex's probe on a missing source root:

```text
RunNotes termination      = blocked
RunResult terminal_status = completed
RunResult terminal_status_reason = ''
```

The machine-readable result contained two answers to one question and the
amended contract's answer was the wrong one on every abort path.

The regression test could not see it: it asserted `PipelineOutcome.termination`
and the log — both correct — and never `PipelineOutcome.result.terminal_status`.
`tests/test_contracts.py` proved only that two *hand-constructed* `RunResult`
objects hash differently, never that the pipeline constructs the non-complete
one.

**Sibling enumeration.** Six `RunResult` construction sites. Three were wrong
(the walk's two blocked preflights and `pipeline._abort`); a fourth — the walk's
normal return — was wrong on the **cancelled** path, which Codex's blocked probe
does not reach and which is the path that actually carries documents; a fifth,
`gui/mock_pipeline`, was wrong on its own cancel path and is what Track C
develops against. Only `pipeline.run`'s final result was right, and only because
it is unreachable except when complete.

### What was applied

`contracts.TerminalStatus` is the only definition; `runstate` re-exports it —
which is precisely the change A-06's own register entry predicted and which
never happened (see D-R2-1). Every returned `RunResult` is stamped by
`RunTermination.stamp()`, which sets both fields from one value, so no site can
set half of them or set them from a termination other than the one the pipeline
is about to act on.

### The hashing decision is REVERSED

1.5.0 hashed the terminal status against the raising package's advice. **Codex's
second opinion is adopted and 1.5.0's reasoning is withdrawn.** Both fields move
into `_IDENTITY_EXCLUDED`.

The 1.5.0 argument was that a cancelled partial set and a complete set must not
hash identically. That assumes the two can be compared, and they cannot: an
incomplete run publishes no corpus and no corpus manifest, so there is no
cancelled corpus hash for a completed one to collide with — the previous
completed manifest simply survives, which is the entire point of the publication
guard that B-1 produced. Calling invocation termination part of a *corpus*
identity for a corpus that was never published makes the byte-identical claim
describe something other than the bytes it covers, and makes rewording an
operator sentence change the identity of runs already produced.

If a failed attempt ever needs a verifiable identity of its own, that is a
separate **attempt** identity over the `incomplete_run/` audit record. It is not
a term in this one.

---

## A-08 — the run identity omits the ordered profile set, and four things claim to be the identity

**Raised by:** the round-2 fix package, 2026-08-01, from Codex review #1 round 2
finding **B-R2-2**, against contract **v1.5.0**
**Affects:** new `ProfileSnapshot`; `RunConfig.profiles`; `output_root` moved to
`_IDENTITY_EXCLUDED`; new `run_identity()`; `emit.log` hashed content;
`verify.manifest` `IDENTITY_NOTE` and the persisted `run_identity_sha256`
**Severity:** MINOR (additive field plus one exclusion)
**Status:** **APPLIED — contract v1.6.0.** This is the largest of the three
round-2 findings.

### The case, measured

`PipelineOptions.profiles` is an **ordered** sequence and
`apply_profiles` claims each document with the FIRST profile whose header
patterns match. So every profile's content and their precedence order decide
which pages drop — and therefore decide `clean_text`, the index, the sources
map, the log's hashed content and the corpus hash.

The effective configuration recorded only:

```text
profile_id      = opts.profiles[0].profile_id
profile_version = opts.profiles[0].version
```

Not the ordered set, not any profile's content, not the precedence that resolves
multiple claimants. Two counterexamples, both reproduced on the fixture corpus
before the fix, neither needing an attacker model:

| change | run identity | corpus hash |
|---|---|---|
| profile 2's DROP rule edited, version NOT bumped | unchanged | **moved** (2 pages KEEP → DROP) |
| profiles 2 and 3 swapped, no content change at all | unchanged | **moved** |

Nothing enforces version immutability, so the first of those is simply how a
profile library drifts between runs.

`FormatProfile.profile_hash` already existed and `emit.log.build_log` already
wrote each profile and its hash into hashed log content — profile content was
already treated as evidence-affecting. It was just missing from the projection
the manifest calls the run identity.

### The second half: four identities, none persisted

`content_hash(RunConfig)` hashed `output_root`; the manifest's `claim_identity`
said the output folder was part of the run identity; `emit.log` deliberately
excluded it from hashed content; and the criterion-7 harness runs the same
corpus to **two different destinations** and requires one identity. Those cannot
all be true, and no durable value was persisted to say which projection was
authoritative.

### What was applied

```python
@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    profile_id: str
    version: str
    profile_hash: str          # FormatProfile.profile_hash — the content

# on RunConfig, defaulted to () so the change is additive:
profiles: tuple[ProfileSnapshot, ...] = ()
```

Snapshots rather than the profiles themselves, for the same reason
`MasterIndexSnapshot` is a snapshot: the identity needs an immutable fingerprint
of the input, not a live object that can be edited afterwards. Hashing the
content also removes the reliance on a version bump nobody enforces.

`output_root` joins `_IDENTITY_EXCLUDED`: the destination is where evidence is
written, not an input that changes it. `run_identity(config)` is now the single
authoritative projection, it is written into `output_manifest.json` as
`run_identity_sha256` **and** into the log's hashed content, and `IDENTITY_NOTE`
is rewritten to describe exactly what it hashes and exactly what it excludes,
each with its reason.

The profile snapshots are built **before** the walk, with the rest of the
effective configuration, so the resume journal is keyed on them too — a journal
written under one profile library is not replayed under another.

---

## A-09 — `EffectiveLimits` recorded float deadlines at whole-second resolution

**Raised by:** the round-2 fix package, 2026-08-01. **Ours, not Codex's** — the
authoritative round-2 verdict does not raise it; we found it while closing A-08
and fixed it under the standing fix-don't-defer rule.
**Affects:** `EffectiveLimits.file_timeout_s` → `file_timeout_ms`,
`retry_budget_s` → `retry_budget_ms`
**Severity:** MINOR, but **renaming rather than additive** — see below
**Status:** **APPLIED — contract v1.6.0.**

### The case

A-04 added `EffectiveLimits` precisely so a setting able to change output
evidence could not sit outside the hashed configuration. Two of its fields then
reintroduced the same defect at finer grain. `WalkOptions.file_timeout_s` and
`DOCIQ_RETRY_BUDGET_S` are floats used as float-valued deadlines, and
`effective_limits` recorded `round(seconds)`:

```text
file_timeout_s = 1.1 → recorded 1
file_timeout_s = 1.4 → recorded 1
effective limits equal = True
```

Those two deadlines abandon different files under the watchdog while presenting
the same identity — a determinism-identity collision inside the field added to
close one.

### Why milliseconds rather than rejecting fractional values

Rejecting fractions would have removed a capability that is used: a sub-second
per-file limit is legitimate and the acceptance harness sets one. Milliseconds
keep it and remove the collision, and the recorded value is still an exact
integer, so Principle 5's bar on floats in identity fields is honored.

### Why the fields were renamed rather than reinterpreted

The semantics changed, so the name had to. A consumer reading
`limits.file_timeout_s` now breaks loudly instead of silently reading a number
in the wrong unit — 3,600 versus 3,600,000 is a thousand-fold error no type
checker catches. Under the freeze procedure a loud break is the correct outcome
for a semantic change.

---

## A-10 — 1.4.0's replacement token fields were never populated

**Raised by:** the round-2 fix package, 2026-08-01, from Codex review #1 round 2
finding **B-R2-3**, against contract **v1.5.0**
**Affects:** no type change — `TokenEstimate.structural_tokens` and
`token_ceiling`, and `pipeline._to_contract_estimate`
**Severity:** DOCUMENTATION + implementation
**Status:** **APPLIED — recorded at contract v1.6.0.**

### The case

A-05(a) withdrew `floor_tokens` and added `structural_tokens` and
`token_ceiling` to replace it. The projection correctly set the withdrawn field
to 0 and never set the two replacements, so both stayed at their "not measured"
default while the same run wrote both numbers into the processing log and the
summary PDF:

```text
MeasuredEstimate:       pretokens=5, utf8_bytes=20
contract TokenEstimate: structural_tokens=0, token_ceiling=0
```

This is the same central-amendment wiring failure as A-07 — a field declared in
the contract and never connected to the pipeline.

**The consequence is not cosmetic**, and Codex names it precisely. When
`ratio_refuted` is true the contract says the ruled band was *not* the method
used; a consumer holding only `RunResult.tokens_before` then receives no
structural figure and no ceiling, and a GUI adapter can fall back to the ruled
ratio and display a number the pipeline expressly disclaims. The log and the PDF
stayed honest; the machine-readable projection did not.

### What was applied

`_to_contract_estimate` populates both from `est.profile`. Recorded here rather
than left as an implementation fix because the machine contract's agreement with
the log is a property of the contract: the next projection added has to satisfy
it too, and the self-test now asserts the two artifacts report one set of
figures.

---

## Round-2 findings fixed rather than descoped, and two that are ours

The authoritative round-2 verdict prescribes **descope** for all three of its
findings under the no-round-3 rule. We fixed all three instead, which is
strictly stronger — the descope conditions would have removed `RunResult`'s
terminal status, the profile-driven determinism claim and the contract's token
fields from Sprint 1's supported scope. Nothing in this register relies on those
reductions.

Three further defects in this package were **not raised by the authoritative
verdict**. They were found by the fix package and fixed under fix-don't-defer;
they should not be attributed to Codex:

* **A-09**, above — float deadlines rounded to whole seconds.
* **The inventory-enumeration and non-recursive-walk defects** (no contract
  change, so no amendment number). An `iterdir()` failure on the root or a
  subtree left the run COMPLETED with a partial or empty inventory, and the
  `recursive=False` branch dropped entries that could not be `stat`'d because
  `Path.is_file()` answers `False` rather than raising. Both are silent-evidence
  loss of the class B-1 and B-3 are about. Their only contract-visible
  consequence is the widened `TerminalStatus.BLOCKED` docstring: a folder DocIQ
  could not list is now a blocked run, while a folder that was **successfully
  enumerated** and holds no files remains a legitimate empty completed run that
  may replace prior deliverables. That boundary is Codex's, it is load-bearing,
  and `tests/test_incomplete_runs.py` asserts both sides of it.
* **The resume key** omitted `RunConfig.limits`, so a record cached under
  different caps, a different OCR model or with OCR disabled could be replayed
  into a run whose manifest then hashed the new settings. It now uses the same
  identity projection that validates the evidence it replays.

---

## A-11 — the seam cannot deliver a profile's section rules before a run

**Raised by:** Track E (GUI, Sprint 2), 2026-08-01
**Affects:** `dociq/gui/pipeline.py` — `PipelineAPI`, `ProfileInfo`
**Proposed severity:** MINOR (additive protocol method, no existing field changes)

### The case

§6 requires a profiling checklist: an expert reviews each recurring section, its
frequency, its page count, and its KEEP/DROP disposition, and *approves the
omissions* before a run commits. Principle 3 is what makes that load-bearing —
an omission the expert never saw is, downstream, indistinguishable from a
document that went missing.

The seam offers `PipelineAPI.profiles() -> tuple[ProfileInfo, ...]`, and
`ProfileInfo` carries only `section_rules: int` — *how many* rules a profile
holds, never *which*. `ReductionLever` is the right shape for a rule row and
already crosses the seam, but only inside a `ReductionPlan` on a `RunOutcome`,
i.e. **after** the run the checklist is supposed to gate.

### Why a local workaround would be wrong

The GUI could read the profile YAML itself. That would import `dociq.profiles`,
which `tests/test_import_graph.py` forbids, and would put a second profile
parser in the product — disagreeing with the pipeline's the first time either
changed, in the one screen whose entire value is that it agrees.

### Proposed shape

```python
class PipelineAPI(Protocol):
    def profile_rules(
        self, profile: ProfileInfo
    ) -> tuple[tuple[ReductionLever, ...], TokenBasis, str]:
        """The profile's KEEP/DROP rules, what each is worth, and — in the
        pipeline's own words — where the rules and figures came from.

        Rows for a profile that has not been run against this matter carry
        ``estimated=True``: they are projections, not counts.
        """
```

Returning existing types keeps the amendment additive. A default in the protocol
(`return (), TokenBasis(), ""`) would let Track D adopt it lazily.

### What Track E did in the meantime

The checklist screen consumes exactly the tuple above, obtained by duck-typed
`getattr(pipeline, "profile_rules", None)`. A pipeline that does not offer it
renders `CHECKLIST_NO_RULES` — a loud empty state that **disables approval** —
rather than an empty list that reads as "nothing is dropped". The mock supplies
the hook so the state grid exercises both branches.

### Related gap, same screen: rules carry no stated reason

§6 gives a profile a free-text notes field ("why sections were dropped, who
approved") and D-05 puts a copy of the profile in the matter folder as the
record of that decision. Neither reaches the GUI. `ReductionLever` has no field
for the rule's own text or the profile's note, so the checklist attributes each
drop by rule *identity* (`<profile_id> v<version> → section "<key>" → DROP`)
and cannot show the pattern that matched or the note the expert wrote.

Proposed, additive, both defaulted to `""`:

```python
@dataclass(frozen=True, slots=True)
class ReductionLever:
    ...
    rule: str = ""   # the profile's own matching rule, verbatim
    note: str = ""   # the profile's notes field for this section
```

Not applied here. Rule identity is a true attribution and is enough to ship;
the note is what makes the omission *defensible in the expert's own words*, and
that is worth an amendment rather than a GUI-authored paraphrase.

---

## A-12 — the seam cannot carry the §8 handoff

**Raised by:** Track E (GUI, Sprint 2), 2026-08-01
**Affects:** `dociq/gui/pipeline.py` — `PipelineAPI`, `RunOutcome`
**Proposed severity:** MINOR (two additive protocol methods)

### The case

§9 acceptance criterion 8 requires "Analyze in Claude" to be a real action, and
§8 defines the two sanctioned routes. Both need something the seam has no way to
express:

* **Path B** needs the pipeline's statement of what is in the matter folder.
  `emit/handoff.py::expert_assist_layout` already produces exactly this and
  *checks the folder* rather than describing it from memory — `present`,
  `missing`, `instructions`. None of it crosses the seam; `RunOutcome` carries
  only `output_root: str`.
* **Path A** needs the package to be *built*. That is emit-layer work
  (`build_upload_package`), and D-20 adds a requirement the existing function
  does not have: the package is a **deliberately scoped subset**, and the scope
  must be stated **inside the package**, because downstream nobody can tell a
  subset from a whole record unless the package says so.

### Why a local workaround would be wrong

Writing `upload_package/` from a widget would import `dociq.emit` (forbidden),
and would put a second copy of §8's "only these files are uploaded" rule in the
product — the rule whose failure mode is DocIQ's own audit trail being uploaded
into the evidence corpus.

Describing the matter folder from the GUI's own knowledge of the layout would be
a claim about disk made by something that did not look at disk. Path B's whole
argument is that DocIQ writes where Expert Assist already reads; asserting that
without checking is exactly the assertion that would be worth checking.

### Proposed shape

```python
class PipelineAPI(Protocol):
    def matter_layout_note(self, outcome: RunOutcome) -> str:
        """What is in the matter folder and what to point Claude at, in the
        pipeline's words, having checked. "" when it did not look."""

    def build_package(
        self, outcome: RunOutcome, doc_ids: tuple[str, ...],
        scope_statement: str,
    ) -> "PackageResult":
        """Assemble §8 Path A's upload_package/ for exactly ``doc_ids``, with
        ``scope_statement`` written into README_START_HERE.txt ahead of
        everything else (D-20)."""
```

`build_upload_package` would gain a `doc_ids` filter and a `scope_statement`
that `render_readme` emits first. `PackageResult` can be a presentation record
in the seam (root path, file count, total bytes, unenforced limits) — the same
treatment `Reconciliation` already gets.

### What Track E did in the meantime

Both are duck-typed hooks. Absent `matter_layout_note`, Path B says the pipeline
confirmed nothing and the operator should check the folder. Absent
`build_package`, the Path A button is **disabled with the reason on screen**
(`PATH_A_UNAVAILABLE`) rather than greyed out silently. The scope selection, the
subset arithmetic and the scope statement are all built and rendered verbatim
now, so adopting the amendment is a wiring change, not a design change.

---

## A-13 — `DIRECT_CONTEXT_TOKENS`' docstring asserts the figure is unruled; D-21 ruled it

**Raised by:** Track E (GUI, Sprint 2), 2026-08-01
**Affects:** `dociq/gui/pipeline.py` — `DIRECT_CONTEXT_TOKENS` docstring only
**Proposed severity:** TRIVIAL (documentation; no code or field changes)

### The case

The docstring reads: "**UNCONFIRMED.** Alex has not ruled this threshold and it
is not measured; 200K is a working placeholder."

D-21 (2026-08-01) rules it: **keep 200,000**, and render it as a named, sourced
reference line called "Claude Project direct context", never as a budget or a
target. So the first sentence is now false — the threshold *is* ruled. What
remains true, and must not be lost in the correction, is that it is **not
measured and not confirmed against Anthropic's published limits**.

Raised rather than edited because the file is frozen and shared with Track D,
and because it is a *claim*, not an identifier: withdrawing it means correcting
the sentence, not deleting the constant.

### Proposed wording

> **RULED D-21 (2026-08-01), NOT MEASURED.** 200,000 is the working figure Alex
> ruled to keep, rendered as a named, sourced reference line — "Claude Project
> direct context" — and never as a budget or a target (D-15: over-capacity is
> the expected state). It has not been confirmed against Anthropic's published
> limits. It is a single named constant precisely so that confirming it is a
> one-line change — the literal appears nowhere else, and no screen may inline
> it.

### What Track E did in the meantime

The GUI names the line `CAPACITY_LABEL` and sources it `CAPACITY_SOURCE` in
`view_models.py`, both stating D-21 and both stating that it is unconfirmed. The
waterfall row previously read "unconfirmed", which was right under the old
docstring and understates the ruling under the new one; it now reads
"reference, not a target" with the source in its tooltip and in a line under the
headline. No literal `200_000` appears anywhere in `dociq/gui/`, and
`tests/test_gui_screen_states.py` asserts it.

---

## A-11, A-12, A-13 — APPLIED (2026-08-01)

Raised by Track E; applied centrally to `src/dociq/gui/pipeline.py` on
`build/sprint-2` so Tracks D, E and F receive one seam rather than three. The
cases are recorded above as Track E argued them; what follows is what was
actually applied, and the one defect applying it uncovered.

**A-11 — APPLIED as proposed.** `PipelineAPI.profile_rules(profile)` returns
`(levers, basis, source)`. Track E already consumes exactly this tuple through a
duck-typed hook whose absence *renders* as a loud empty state that disables
approval, so adoption is wiring.

**A-11b — APPLIED, and NOT as Track E left it.** Track E proposed `rule` and
`note` on `ReductionLever` but declined to apply them, on the reasoning that
rule identity is a true attribution and enough to ship. It is enough to ship;
it is not enough to *defend*. §6 gives a profile a notes field for why a
section was dropped and who approved it, and D-05 puts a copy of the profile in
the matter folder precisely so that sentence survives. An omission an expert
cannot explain in their own words is an omission they cannot defend, and the
alternative — a widget paraphrasing the rationale — is the tool putting words
in the expert's mouth about evidence. Both fields are carried verbatim.

> **CORRECTION, 2026-08-04 — "APPLIED" was true of the seam and false of the
> product.** `rule` and `note` were added to `ReductionLever`, documented,
> preserved across `with_toggled`, and covered by two probes — and **no adapter
> ever populated them and no screen ever rendered them.** All three
> construction sites in `adapter.py` left both at `""`, including
> `profile_rules`, which is the §6 checklist path this amendment exists for. So
> the checklist could show that a DROP rule existed and never what it catches or
> who approved it — the exact gap the paragraph above says is the difference
> between shipping and defending.
>
> Found by the seam-population probe built for Codex review #2's B-3
> (`tests/test_seam_population.py`), which enumerates every field of every seam
> presentation record and requires each to be named at every construction site
> in the adapter or ruled exempt. **This is the same failure as A-12, A-14 and
> B-3 — the fourth instance in one sprint** — and it is the reason the probe
> enumerates rather than checking the field that happened to be reported.
>
> Now populated (`rule=rule.pattern`, `note=rule.notes`, and from the run's own
> profiles for the post-run waterfall) and rendered by
> `ProfileChecklistScreen._row_widget` through `ChecklistRow.matched_by()` and
> `ChecklistRow.expert_note()`. Both watched RED before the fix.

**A-12 — APPLIED as proposed**, plus `PackageResult` as a presentation record
in the seam (`root`, `file_count`, `total_bytes`, `scope_statement`,
`doc_count`), the same treatment `Reconciliation` already gets.
`scope_statement` is the same sentence written INTO the package, not a second
one rendered beside it: under D-20 every Path A package is a subset unless it
says otherwise, and two sentences that can drift is how a subset comes to look
like a whole record.

`build_package` may be OMITTED by an adapter that does not offer Path A, rather
than returning an empty result. The GUI probes for it and disables the action
with the reason on screen; a stand-in that silently returned nothing would
leave the operator pressing a button that appears to work.

**A-13 — APPLIED with Track E's wording.** The docstring asserted the threshold
was unruled, which D-21 made false. Corrected, not deleted: what remains true —
that 200,000 is unmeasured and unconfirmed against Anthropic's published limits
— is the part a reader needs.

### The defect applying A-11b uncovered

`ReductionPlan.with_toggled` rebuilt `ReductionLever` by listing its fields
positionally. That was correct on the day it was written and silently lossy for
every field added afterwards — so the moment `rule` and `note` existed, an
expert's stated reason for an omission would have been on screen before a click
and gone after it. Nothing would have raised.

**Class, not repro.** Four sites rebuilt the record from its parts — the seam,
the mock's measured-scale rescale, and two screen-state tests. A lossy rebuild
inside a *fixture* is worse than one in the product: it produces a passing test
of the wrong record. All four now use `dataclasses.replace`.

Two probes, both watched RED under perturbation before being trusted:

- `test_toggling_preserves_every_lever_field_except_engaged` — generated from
  `dataclasses.fields(ReductionLever)`, so a field added next year is covered
  the moment it exists. Asserting only `rule` and `note` would have been a test
  of this amendment rather than of the defect.
- `test_no_lever_rebuild_site_lists_fields_positionally` — walks the **AST** of
  every module in `src/` and `tests/`. Its first version used a regex and
  **missed the very rebuild that motivated it**: the call was
  `ReductionLever(lever.key, ...)` and the pattern stopped at the dot. Recorded
  because it is the general lesson — a regex over source is a guess about
  syntax.

#### Correction, 2026-08-03 — both claims above were overstated

Withdrawn rather than quietly patched, because the wrong part is the *claim*,
not only the code.

1. **"All four now use `dataclasses.replace`" understated the scope of the
   defect.** Four `ReductionLever` sites were fixed. The class was
   "a frozen presentation record rebuilt by listing its fields", and it was
   never only `ReductionLever` — `ReductionPlan.with_toggled` rebuilds
   **`ReductionPlan`** positionally in the very method that was fixed, and
   `tests/test_view_models.py` rebuilt **`RunOutcome`** from six of its eight
   fields, a lossy rebuild inside a fixture of exactly the kind the paragraph
   above calls worse than one in the product. Neither was seen.
2. **"cannot be fooled by one [a regex]" claimed too much.** The AST probe
   named a single record. A probe that polices one of thirteen frozen records
   in the seam is not un-foolable; it is narrow, and its narrowness is invisible
   from its name.

Both are now closed by construction. `test_no_seam_record_is_rebuilt_with_
optional_fields_positionally` enumerates the frozen presentation records from
`dociq.gui.pipeline` itself — every record the seam *defines*, so one added
tomorrow is policed the moment it exists — and flags any positional argument
**beyond a record's required fields**. That is the precise line: required fields
must be supplied at every call site, so a new required field breaks them loudly;
every field added to a shipped frozen record carries a default, and a default is
what a rebuild that stopped listing fields silently falls back to.

`ReductionPlan.with_toggled` is **not fixed here**. `src/dociq/gui/pipeline.py`
is frozen and shared with parallel work, so the site is REPORTED: it is covered
by `test_the_frozen_seam_module_has_no_positional_rebuild`, marked
`xfail(strict=True)` so that the day the seam owner fixes it the test turns red
and the marker must be removed. It is lossless today at 4 of 4 fields and
silently lossy on the next one.

---

## A-14 — the seam cannot carry §4 Stage 3's Bates confirmation

**Raised by:** Track D (Sprint 2), 2026-08-01, as a stop-the-line
**Applied by:** the seam owner, 2026-08-03, at `3a44f2e`
**Affects:** `dociq/gui/pipeline.py` — `PipelineAPI.run`, plus two new records
**Severity:** MINOR (one additive record, one callable alias, one optional parameter)

> **This entry was written on 2026-08-04, a day after the amendment was applied
> and shipped.** For that day the register was the only place A-14 did not
> exist: it was in the seam, in three commit messages, in the decision register,
> and in the Codex relay — which pointed reviewers *here* for it. Codex found
> the gap immediately. Recorded rather than backdated, because this is the
> second-order instance of the exact failure A-14 itself is the first-order
> instance of, and the pair is the argument for the registry in
> `tools/check_amendments.py`.

### The case

§4 Stage 3 requires a detected Bates format to be confirmed **with the
operator** on first detection. `PipelineOptions.auto_confirm_bates` existed for
headless callers and recorded a warning when it fired, but nothing on the seam
could ask a human. So `RealPipeline` passed `auto_confirm_bates=False`, no
screen asked, `_bates_decision` returned `PENDING`, and `apply_bates_reported`
returned every document unchanged.

**Measured cost, on real MNFV production (10 documents / 369 pages) through
`RealPipeline`:** `confirm_bates=None` — the shipped state — produced **0 of 369
pages with a locator, with OCR on and with OCR off**. With an operator
confirmation: **328 pages, 88.889%**. Both `None` runs warned that a format *was
detected and not applied*. The pipeline could see the stamps the entire time.

Acceptance criterion 4's 92.130% was measured through
`tools/bates_acceptance.py`, which constructs `BatesDecision(status=CONFIRMED,
…)` directly — a code path the product could not reach.

### Applied shape

```python
@dataclass(frozen=True, slots=True)
class BatesProposal:
    pattern: str
    example: str = ""          # a real locator off a real page
    documents: int = 0
    pages: int = 0
    coverage_pct: float = 0.0
    alternatives: tuple[str, ...] = ()

BatesConfirm = Callable[[BatesProposal], bool]

class PipelineAPI(Protocol):
    def run(self, request, on_progress, should_cancel,
            confirm_bates: "BatesConfirm | None" = None) -> RunOutcome: ...
```

Deliberately more than the pattern: **an operator cannot confirm a regex.** They
can confirm *"MNFV 02636, on 15 of 33 pages across 20 documents"*.
`alternatives` is present because a multi-series production is exactly the
condition D-28 refuses prefix repair on, and the operator must see that rather
than have it decided for them.

`confirm_bates` is **optional with a `None` default** so every existing caller —
the tests, the headless harnesses, the self-test — kept working; a required
parameter would have broken the only implementations that can demonstrate the
seam holds.

### Three outcomes, kept apart

`None` means **nobody was asked**, which is not a refusal. An operator
confirmation records `decided_by="operator (username)"`; a refusal records
status `REJECTED` with a warning stating *"This matter is NOT unstamped — the
stamps are on the pages and were read"*; nobody-asked records the machine
confirmation it always did. A machine-confirmed pattern and an expert-confirmed
one are not the same evidentiary object, and an unstamped production and a
stamped one whose format was declined are different facts about the record.

An operator who walks away raises `runstate.RunAborted` rather than returning a
`bool` — routed to the existing abort path, so nothing is published and the
previous run's deliverables are untouched.

### The defect the wiring uncovered

`alternatives` was first fed the detector's runner-up **shapes** (`ranked[1:4]`,
no threshold). On the client corpus that was `('retained 90095 49 00001',
'Check 0001')` — so the screen would have told the operator, as fact, that their
**single-series production was multi-series** and that D-28 therefore refused
prefix repair on it. A confident, specific, wrong statement about their
evidence. It now uses `identify.bates.matter_prefixes`, computed once per run
and shared with `apply_bates_reported`'s own gate. **Found by running against
the real production, not by reasoning about the code** — and the seam docstring
for that field already explained why it mattered while the wiring behind it was
wrong.
