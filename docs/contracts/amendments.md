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

**Status:** both raised for the central amendment. Track C did not modify
`contracts.py` and does not depend on either amendment landing before Sprint 2
integration — but the real adapter cannot be written honestly until they do,
because it would have nowhere to read these values from.
