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
**Status:** RAISED — **stop-the-line, not applied.** `contracts.py` was NOT
modified on this branch.

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

## A-07 — two `TerminalStatus` enumerations, and neither abort path filled the field in

**Raised by:** the round-2 fix package, 2026-08-01, from Codex review #1 round 2
finding **F-1**, against contract **v1.5.0**
**Affects:** `TerminalStatus`, `RunResult.terminal_status_reason`,
`_IDENTITY_EXCLUDED`; `runstate`, the walk, `pipeline._abort`, the GUI mock
**Severity:** MINOR
**Status:** **APPLIED — contract v1.6.0.**

### The case

A-06 landed `TerminalStatus` and `RunResult.terminal_status` in the contract
while `runstate.py` still declared a value-identical enumeration of its own, and
nothing reconciled them. The walk carried `runstate`'s type; `RunResult`
declared the contract's. Two enumerations spelled the same way compare `==` on
their string values and `is` never, so the identity check the typed status
exists to enable answered `False` about two statuses that are the same status.

The deeper half is that A-06 defaulted the field to `COMPLETED` so the change
would be additive — and *every* abort path then took the default. Codex's probe
on a missing source root:

```text
RunNotes termination = blocked
RunResult terminal_status = completed
RunResult terminal_status_reason = ''
```

A consumer holding the machine contract was told the opposite of the outcome
wrapper, the log beside it and the `run_status.json` under it. The round-1 test
asserted `PipelineOutcome.termination` and the log — both of which were right —
and therefore could not see it.

**Sibling enumeration.** Six `RunResult` construction sites exist. Three were
wrong (`walker.run`'s two blocked preflights and `pipeline._abort`); a fourth,
`walker.run`'s normal return, was wrong on the **cancelled** path and was not
covered by Codex's probe; a fifth, `gui/mock_pipeline`, was wrong on its own
cancel path and is what Track C develops against. Only `pipeline.run`'s final
result was right, and only because it is unreachable except when complete.

### What was applied

`contracts.TerminalStatus` is the only definition and `runstate` imports it.
Every returned `RunResult` is stamped by `RunTermination.stamp()`, which sets
both fields from one value, so no site can set half of them or set them from a
different termination than the pipeline is about to act on.

`terminal_status_reason` joins `_IDENTITY_EXCLUDED`, adopting the reviewer's
advisory. The enum still hashes, so a cancelled partial set and a complete set
still cannot share an identity — the distinction is carried by the typed status
and does not need the prose. Leaving the free-form sentence in the hash would
mean rewording an operator message changes the identity of runs already
produced, which is exactly why `ratio_low`/`ratio_high` are excluded.

`TerminalStatus.BLOCKED`'s docstring is widened to name the third way in: an
inventory that could not be enumerated (F-2, below).

---

## A-08 — `EffectiveLimits` recorded float deadlines at whole-second resolution

**Raised by:** the round-2 fix package, 2026-08-01, from Codex review #1 round 2
finding **F-4b**, against contract **v1.5.0**
**Affects:** `EffectiveLimits.file_timeout_s` → `file_timeout_ms`,
`retry_budget_s` → `retry_budget_ms`
**Severity:** MINOR, but **renaming rather than additive** — see below
**Status:** **APPLIED — contract v1.6.0.**

### The case

A-04 added `EffectiveLimits` precisely so that a setting able to change output
evidence could not sit outside the hashed configuration. Two of its fields then
reintroduced the same defect at finer grain. `WalkOptions.file_timeout_s` and
`DOCIQ_RETRY_BUDGET_S` are floats used as float-valued deadlines, and
`effective_limits` recorded `round(seconds)`. Codex's probe:

```text
recorded file_timeout_s = 1 / 1
effective limits equal = True
```

A 1.1 s limit and a 1.4 s limit abandon different files and present the same
identity. That is a determinism-identity collision inside the field added to
close one.

### Why milliseconds rather than rejecting fractional values

Rejecting fractions was the alternative and would have removed a capability the
watchdog uses — a sub-second per-file limit is legitimate, and the acceptance
harness sets one. Milliseconds keep it and remove the collision, and the
recorded value is still an exact integer, so Principle 5's bar on floats in
identity fields is honored.

### Why the fields were renamed, not reinterpreted

The semantics changed, so the name had to. A consumer reading
`limits.file_timeout_s` now breaks loudly instead of silently reading a number
in the wrong unit — 3600 seconds versus 3,600,000 milliseconds is a
thousand-fold error that no type checker would catch and no test outside this
repository would see. Under the freeze procedure a loud break is the correct
outcome for a semantic change; a silent one is not.

---

## A-09 — 1.4.0's replacement token fields were never populated

**Raised by:** the round-2 fix package, 2026-08-01, from Codex review #1 round 2
finding **F-5**, against contract **v1.5.0**
**Affects:** no type change — `TokenEstimate.structural_tokens` and
`token_ceiling`, and `pipeline._to_contract_estimate`
**Severity:** DOCUMENTATION + implementation
**Status:** **APPLIED — recorded at contract v1.6.0.**

### The case

A-05(a) withdrew `floor_tokens` and added `structural_tokens` and
`token_ceiling` to replace it. The projection correctly set the withdrawn field
to 0 and never set the two replacements, so both stayed at their "not measured"
defaults while the same run wrote both numbers into the processing log and the
summary PDF. Codex's probe, on text with five pre-tokens and 20 UTF-8 bytes:

```text
MeasuredEstimate: pretokens=5, utf8_bytes=20
contract TokenEstimate: structural_tokens=0, token_ceiling=0
```

That is worse than the fields' absence. A consumer holding the machine contract
and a consumer reading the log were told different things about one run, and the
contract — the artifact the freeze exists to make trustworthy — was the wrong
one. A GUI adapter reading `structural_tokens` would have rendered a measured
corpus as unmeasured.

### What was applied

`_to_contract_estimate` populates both from `est.profile`. It is recorded here
rather than left as an implementation fix because the machine contract's
agreement with the log is a property of the contract, not of the pipeline: the
next projection added has to satisfy it too.

---

## Round-2 finding F-2 — recorded here for the contract-side note only

F-2 (an inventory enumeration failure must make the run non-publishable) and F-3
(the non-recursive walk dropped unstattable entries) are implementation
findings and needed no new type. Their contract-visible consequence is the
widened `TerminalStatus.BLOCKED` docstring under A-07: a folder DocIQ could not
list is now a blocked run, while a folder that was **successfully enumerated**
and holds no files remains a legitimate empty completed run that may replace
prior deliverables. That boundary is the reviewer's, it is load-bearing, and
`tests/test_incomplete_runs.py` asserts both sides of it.
