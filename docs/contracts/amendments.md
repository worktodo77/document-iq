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
