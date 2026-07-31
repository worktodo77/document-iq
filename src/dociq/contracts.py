"""FROZEN pipeline contract for LI Document IQ.

This module is the cross-track contract frozen on day one of Sprint 1 per the
D-10 contract-first rule. Tracks A (ingestion spine), B (identity and
deliverables) and C (GUI shell) all build against these types in separate
worktrees; only Track A implements them for real.

**Any change to a type in this module after the freeze is a stop-the-line
event across all three tracks, not a local edit.** Additive changes with a
safe default are permitted only via the amendment procedure in
``docs/contracts/pagemodel_freeze.md``.

Nothing here imports a third-party library, a GUI toolkit, or an OCR engine.
The contract must stay importable in a bare interpreter so every track can
depend on it without inheriting another track's dependency set.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

CONTRACT_VERSION = "1.3.0"
"""Frozen 2026-07-30 at 1.0.0. Bumped only by the amendment procedure.

1.1.0 — amendments A-01 and A-02, raised by Track C under the stop-the-line
rule and applied centrally: :class:`RunResult` gained ``tokens_before``,
``tokens_after`` and ``reconciliation``, all defaulted to ``None``. Additive
with safe defaults, so no existing construction site changes.

1.2.0 — amendment A-03: :class:`TokenEstimate` gained ``ratio_refuted``,
defaulted to ``False``. Raised once the corpus measurement showed D-03's ratio
band to be unreachable rather than merely optimistic, leaving consumers with no
sanctioned way to know the band was not used. *(Codex review #1 finding B-6
later showed that refutation is not established — the flag stays, but what
sets it must be re-grounded.)*

1.3.0 — amendment A-04, from Codex review #1 finding B-2: :class:`RunConfig`
gained ``limits: EffectiveLimits | None``. Environment-controlled caps,
timeouts, retry bounds and the OCR model identity could change output evidence
while sitting outside the hashed configuration — a determinism-identity gap by
:class:`RunConfig`'s own stated contract.
"""


# ---------------------------------------------------------------------------
# Enumerations
#
# Values are the strings that reach disk (processing_log.json, the index CSV).
# They are part of the determinism contract: renaming one changes output bytes.
# ---------------------------------------------------------------------------


class PageKind(str, enum.Enum):
    """How a page's text was obtained.

    Recorded per page because §3 requires mixed native/scanned PDFs to be
    handled page-by-page, and because the run summary reports OCR exposure.
    """

    NATIVE = "native"
    """Extracted from the document's own text layer."""

    OCR = "ocr"
    """Rasterized and read by the local OCR engine."""

    EMPTY = "empty"
    """The page exists and carries no recoverable text. It is still a page:
    Principle 1 requires it to be accounted for, and the page marker is still
    emitted so downstream page numbers stay aligned with the physical
    document."""

    PHOTO = "photo"
    """An image-based page carrying a deterministic ``[PHOTO]`` block (EXIF
    date/GPS) rather than read text. Never AI-captioned in DocIQ."""

    SYNTHETIC = "synthetic"
    """The source format has no physical pagination (DOCX, EML, MSG, XLSX,
    CSV, TXT). ``page_no`` is an approximation and MUST be reported as such in
    the log, per §3's "page approximation noted in log"."""


class Disposition(str, enum.Enum):
    """Stage-4 section classification outcome for a page.

    Default is KEEP everywhere, unconditionally. Principle 1: unclassified
    content is kept; only an expert-approved profile rule may set DROP.
    """

    KEEP = "keep"
    DROP = "drop"


class ProcessingStatus(str, enum.Enum):
    """Per-document outcome, surfaced in the index deliverable (§5)."""

    FULL = "full"
    PARTIAL_OCR_FLAGGED = "partial-ocr-flagged"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class IdRegime(str, enum.Enum):
    """Which Doc ID scheme a run used (D-04). Recorded in processing_log."""

    MASTER_INDEX = "master-index"
    """A master index was supplied; matched files carry ``LI-`` IDs."""

    NATIVE = "native"
    """No master index; every ID is ``DIQ-`` synthetic."""


# ---------------------------------------------------------------------------
# The per-page record — the core of the freeze
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageRecord:
    """One page of one source document.

    Frozen and slotted: pages are produced in bulk and must not be mutated in
    place by a later stage. Stages 3/3b/4 enrich pages by building new records
    (see :meth:`evolve`), which keeps the pipeline's data flow inspectable and
    makes an accounting discrepancy attributable to a stage.

    Field ordering here is also the canonical serialization order — see
    :func:`canonical_json`.
    """

    page_no: int
    """1-based page number **of the original native document**, never of the
    reduced output (Principle 2). For SYNTHETIC pages this is the approximated
    ordinal within the source."""

    text: str
    """The page's extracted text, already normalized (see
    ``docs/contracts/pagemodel_freeze.md`` §Normalization). Never contains a
    page marker: markers are rendered at the emit layer only."""

    kind: PageKind

    ocr_conf: float | None = None
    """Mean per-line OCR confidence in ``[0.0, 1.0]``, rounded to 4 decimal
    places, or ``None`` when :attr:`kind` is not OCR.

    Rounded because it reaches disk and floats must not destabilize the
    byte-identical contract. It is a *reporting* field only and must never be
    used as an identity/hash input (Principle 5)."""

    ocr_line_count: int = 0
    """Number of text lines the OCR engine returned for this page."""

    ocr_low_conf_lines: int = 0
    """Lines below the run's confidence threshold. Drives the §4 Stage-2
    review flag together with :attr:`ocr_conf`."""

    bates: str | None = None
    """Bates number detected on this page (Stage 3), e.g. ``"MNFV 000391"``.
    ``None`` means not detected. Absence is normal, not an error (§4 Stage 3);
    an un-Bates-stamped matter yields ``None`` on every page."""

    section: str | None = None
    """Section header this page falls under, as matched by the active profile
    (Stage 4). ``None`` when no profile matched — which means KEEP."""

    disposition: Disposition = Disposition.KEEP
    """KEEP unless an expert-approved profile rule dropped it. Defaulted so
    that any code path that forgets to classify still keeps the page."""

    drop_rule: str | None = None
    """Identifier of the profile rule that set DROP, for the per-drop log
    entry. MUST be non-None whenever :attr:`disposition` is DROP — enforced by
    :meth:`validate`. Principle 1 forbids an unattributable drop."""

    notes: tuple[str, ...] = ()
    """Disclosed degradation markers for this page (truncation, undecodable
    region, OCR failure). Disclosure, never silence."""

    def evolve(self, **changes: object) -> "PageRecord":
        """Return a copy with fields replaced. The only sanctioned way for a
        later stage to enrich a page."""
        return replace(self, **changes)  # type: ignore[arg-type]

    def validate(self) -> None:
        """Raise :class:`ContractViolation` if this record is internally
        inconsistent. Cheap; called at every stage boundary."""
        if self.page_no < 1:
            raise ContractViolation(f"page_no must be 1-based, got {self.page_no}")
        if self.kind is PageKind.OCR:
            if self.ocr_conf is None:
                raise ContractViolation(
                    f"page {self.page_no}: OCR page must carry ocr_conf"
                )
        elif self.ocr_conf is not None:
            raise ContractViolation(
                f"page {self.page_no}: ocr_conf set on non-OCR page ({self.kind.value})"
            )
        if self.ocr_conf is not None and not (0.0 <= self.ocr_conf <= 1.0):
            raise ContractViolation(
                f"page {self.page_no}: ocr_conf {self.ocr_conf} outside [0,1]"
            )
        if self.ocr_low_conf_lines > self.ocr_line_count:
            raise ContractViolation(
                f"page {self.page_no}: more low-confidence lines than lines"
            )
        if self.disposition is Disposition.DROP and not self.drop_rule:
            raise ContractViolation(
                f"page {self.page_no}: DROP without a drop_rule — "
                "Principle 1 forbids an unattributable drop"
            )
        if self.disposition is Disposition.KEEP and self.drop_rule:
            raise ContractViolation(
                f"page {self.page_no}: drop_rule set on a KEEP page"
            )


# ---------------------------------------------------------------------------
# The per-document record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """One source file and every page recovered from it."""

    doc_id: str
    """Assigned at Stage 3b (``LI-06881``, ``LI-06881.01``, ``DIQ-000123``).
    Empty string before Stage 3b has run."""

    rel_path: str
    """Path relative to the scanned root, with ``/`` separators, NFC-normalized.
    The primary master-index match key (§5) and the primary sort key."""

    filename: str
    sha256: str
    size_bytes: int
    ext: str
    """Lowercased, including the dot (``".pdf"``)."""

    pages: tuple[PageRecord, ...] = ()
    status: ProcessingStatus = ProcessingStatus.FULL

    parent_doc_id: str | None = None
    """Set for archive members and email attachments (§5). Container children
    have no master-index row and take parent-derived IDs (D-04)."""

    container_order: int | None = None
    """0-based position within the parent container, in archive member order.
    Makes child ID assignment deterministic (D-04)."""

    detected_dates: tuple[str, ...] = ()
    """ISO-8601 dates found in the document, in first-appearance order."""

    doc_type: str | None = None
    """From the active profile or a filename pattern. Never inferred by AI."""

    profile_id: str | None = None
    profile_version: str | None = None

    li_file_no: str | None = None
    """The master index's "Original Sort" value when matched (§5), else None."""

    notes: tuple[str, ...] = ()
    """Document-level disclosed degradation (content-sniff recovery, member
    cap hit, extractor fallback)."""

    error: str | None = None
    """Actionable message when :attr:`status` is FAILED or UNSUPPORTED."""

    # -- derived accounting -------------------------------------------------

    @property
    def pages_in(self) -> int:
        return len(self.pages)

    @property
    def pages_kept(self) -> int:
        return sum(1 for p in self.pages if p.disposition is Disposition.KEEP)

    @property
    def pages_dropped(self) -> int:
        return sum(1 for p in self.pages if p.disposition is Disposition.DROP)

    def validate(self) -> None:
        """Structural + accounting check for this document.

        This is the per-document half of the §4 Stage-6 zero-discrepancy gate;
        :mod:`dociq.verify.accounting` runs the corpus-wide half.
        """
        for p in self.pages:
            p.validate()
        expected = list(range(1, len(self.pages) + 1))
        actual = [p.page_no for p in self.pages]
        if actual != expected:
            raise ContractViolation(
                f"{self.rel_path}: page numbers must be a gapless 1..N sequence "
                f"in order; got {actual[:8]}{'...' if len(actual) > 8 else ''}"
            )
        if self.pages_kept + self.pages_dropped != self.pages_in:
            raise ContractViolation(
                f"{self.rel_path}: page accounting broken — "
                f"{self.pages_in} in != {self.pages_kept} kept + "
                f"{self.pages_dropped} dropped"
            )
        if self.parent_doc_id is not None and self.container_order is None:
            raise ContractViolation(
                f"{self.rel_path}: container child without a container_order — "
                "child ID assignment would be nondeterministic"
            )


# ---------------------------------------------------------------------------
# Run-level types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MasterIndexSnapshot:
    """Identity of the master index used, per D-04 mitigation (b)."""

    filename: str
    sha256: str
    row_count: int


@dataclass(frozen=True, slots=True)
class EffectiveLimits:
    """Every environment-controlled setting that can change output evidence
    (amendment A-04, from Codex review #1 finding B-2).

    :class:`RunConfig`'s contract is that anything influencing output and not
    in it is a determinism bug. These settings were outside it: caps, timeouts,
    retry bounds and the OCR model identity all live in module-level constants
    read from the environment. When a cap or timeout *bites*, the same folder,
    profile and index produce different evidence under an identical hashed
    configuration — so the determinism identity was incomplete, by the
    contract's own definition.

    Per-document truncation notes disclose the effect but do not repair the
    identity: a consumer comparing two runs' hashes would see agreement that
    the bytes do not support.

    All ints and strings, so identity hashing is unaffected.
    """

    xlsx_max_rows: int
    csv_max_rows: int
    zip_max_mb: int
    zip_max_members: int
    zip_max_depth: int
    file_timeout_s: int
    retry_max: int
    retry_budget_s: int
    recurse: bool

    ocr_model_id: str = ""
    """Stable identity of the OCR model artifact — package version plus a hash
    of the model files. Two engines that read the same page differently are
    different inputs, and a version string alone does not prove the bytes
    match."""

    workers: int = 0
    """Recorded but NOT hashed by convention — see
    :data:`_IDENTITY_EXCLUDED`. Thread-pool width must not change output; if it
    ever does, that is a determinism defect to fix rather than a value to
    absorb into the identity. Recording it keeps a performance report
    interpretable."""


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Everything that can change output bytes.

    The determinism contract is "same folder + same profile + same master
    index = byte-identical" (Principle 5 as amended by D-04). Anything that
    influences output and is NOT in this dataclass is a determinism bug.
    """

    source_root: str
    output_root: str
    profile_id: str | None = None
    profile_version: str | None = None
    master_index: MasterIndexSnapshot | None = None
    ocr_conf_threshold_pct: int = 85
    """§4 Stage 2 default. Pages whose confidence falls below this are flagged
    for human review.

    An integer percent, not a float fraction — deliberately. This value is part
    of the run identity (change it and the flagged-page set, and therefore the
    log and summary, change), and Principle 5 forbids floats in identity
    fields. Percent is also the unit §4 states the default in. Compare against
    :attr:`PageRecord.ocr_conf` via :attr:`ocr_conf_threshold`."""

    ocr_engine: str = "rapidocr"
    ocr_engine_version: str = ""
    bates_pattern: str | None = None
    """Confirmed with the user on first detection per set (§4 Stage 3)."""

    limits: EffectiveLimits | None = None
    """The effective environment-controlled settings for this run (A-04).
    ``None`` only for constructions that never reach a real run — the pipeline
    must always populate it, and the manifest names it as part of the identity
    the byte-identical claim covers."""

    @property
    def ocr_conf_threshold(self) -> float:
        """The threshold as a ``[0,1]`` fraction, for comparison against
        :attr:`PageRecord.ocr_conf`. Derived — never stored, never hashed."""
        return self.ocr_conf_threshold_pct / 100.0

    @property
    def id_regime(self) -> IdRegime:
        return IdRegime.MASTER_INDEX if self.master_index else IdRegime.NATIVE


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """The §7 token estimate for one body of text (amendment A-01).

    Carried on :class:`RunResult` because §4 Stage 6 computes it, §7 writes it
    to ``run_summary.pdf`` and §9 headlines it on the summary screen. Without
    a field here the GUI would have to recompute it, putting a second estimator
    in the product — and the two numbers would then disagree in the same matter
    folder, one on screen and one in the PDF.
    """

    chars: int

    ratio_low: float
    ratio_high: float
    """The chars-per-token band the range was built from. Floats, therefore
    reporting-only and excluded from identity — see :data:`_IDENTITY_EXCLUDED`."""

    floor_tokens: int = 0
    """Hard lower bound on the true token count, from the text's pre-token
    count: a byte-level BPE tokenizer cannot merge across a pre-token boundary,
    so it can never emit fewer tokens than this. ``0`` means not measured.

    Prefer displaying this over the estimate wherever one number must be shown.
    A bound that holds for any tokenizer is defensible; a point estimate
    derived from an assumed ratio is not."""

    ratio_refuted: bool = False
    """True when the text's own structure contradicts the configured ratio
    band, so the band was not used (amendment A-03).

    A separate field rather than something a consumer infers, for two reasons.
    Inferring it — say, by comparing ``chars / floor_tokens`` against the band —
    would put the pipeline's refutation test inside whatever code asks the
    question, and two implementations of it would eventually disagree. Parsing
    it back out of :attr:`provenance` would make a display string load-bearing.
    A boolean the producer sets is the only version of this that cannot drift.

    Consumers must render the refuted case differently rather than silently
    showing a number computed some other way."""

    provenance: str = ""
    """How the ratio was obtained, in words, travelling with the number.

    D-03 specifies calibration "against the real Claude tokenizer", which
    cannot be performed under Principle 4 (no network) with no tokenizer
    artifact available offline. Whatever a build actually did must be stated
    here and rendered beside the figure. An evidentiary tool may show an
    approximation; it may not show an approximation dressed as a measurement."""

    def __post_init__(self) -> None:
        if self.ratio_low <= 0 or self.ratio_high < self.ratio_low:
            raise ContractViolation(
                f"token ratio band must be positive and ordered, got "
                f"{self.ratio_low}–{self.ratio_high}"
            )


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    """One discrepancy between the folder and the master index (§5)."""

    category: str
    """``"folder-only"`` | ``"index-only"`` | ``"field-mismatch"``."""

    doc_id: str
    filename: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """The §5 reconciliation, a first-class deliverable (amendment A-02).

    ``DocumentRecord.li_file_no`` records the *result* of one successful match,
    which is not the same information: it cannot express an index row with no
    file at all, nor a field disagreement between a matched pair. Both are
    categories §5 requires the report to carry.
    """

    matched: int
    rows: tuple[ReconciliationRow, ...] = ()

    @property
    def folder_only(self) -> tuple[ReconciliationRow, ...]:
        return tuple(r for r in self.rows if r.category == "folder-only")

    @property
    def index_only(self) -> tuple[ReconciliationRow, ...]:
        return tuple(r for r in self.rows if r.category == "index-only")

    @property
    def field_mismatch(self) -> tuple[ReconciliationRow, ...]:
        return tuple(r for r in self.rows if r.category == "field-mismatch")


@dataclass(frozen=True, slots=True)
class RunResult:
    """The full outcome of one run — what the emit layer writes and the GUI
    displays."""

    config: RunConfig
    documents: tuple[DocumentRecord, ...] = ()
    unsupported: tuple[DocumentRecord, ...] = ()
    """Tier-2 files: inventoried and hashed, never blocking (§3)."""
    warnings: tuple[str, ...] = ()

    tokens_before: TokenEstimate | None = None
    tokens_after: TokenEstimate | None = None
    """§7 token estimate across the corpus, before and after reduction (A-01).
    ``None`` means not computed."""

    reconciliation: ReconciliationReport | None = None
    """§5 master-index reconciliation (A-02). ``None`` means no master index
    was supplied — which is not the same as a reconciliation that found
    nothing, and the emit layer must not render the two identically."""

    @property
    def pages_in(self) -> int:
        return sum(d.pages_in for d in self.documents)

    @property
    def pages_kept(self) -> int:
        return sum(d.pages_kept for d in self.documents)

    @property
    def pages_dropped(self) -> int:
        return sum(d.pages_dropped for d in self.documents)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DocIQError(Exception):
    """Base class for every DocIQ error."""


class ContractViolation(DocIQError):
    """A record violated the frozen contract. Always a bug, never user input."""


class ExtractionError(DocIQError):
    """A document's text could not be extracted. Carries an actionable message
    and is recorded against the document — it never aborts a run."""


# ---------------------------------------------------------------------------
# The canonical serializer — one function, used for BOTH hashing and
# persistence, per the durable-fingerprinted-artifact rule
# ---------------------------------------------------------------------------

_IDENTITY_EXCLUDED: frozenset[str] = frozenset(
    {"ocr_conf", "ratio_low", "ratio_high", "workers"}
)
"""Fields excluded from identity hashing.

``ocr_conf`` is a float and a *reporting* value; including it would make the
byte-identical claim hostage to OCR float jitter. It is still persisted — it
just does not participate in identity. Principle 5: "no floats in identity
fields."

``ratio_low``/``ratio_high`` (A-01) join it for the same reason: the token
band is an estimate about the text, not a property of it, and re-ruling D-03
must not invalidate the identity of runs already produced.

Note this is matched by field *name* across every contract dataclass, so a
field named ``ocr_conf`` on a future type is excluded automatically. That is
deliberate — the failure mode it prevents (a float silently entering the hash)
is worse than the one it risks (a field excluded that need not have been).
"""


def to_jsonable(obj: object, *, for_identity: bool = False) -> object:
    """Convert a contract object into JSON-safe primitives.

    The single conversion used by both the hash path and the persistence path.
    Two serializers would eventually disagree; this one cannot.

    Args:
        for_identity: drop :data:`_IDENTITY_EXCLUDED` fields, for hashing.
    """
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, float):
        if for_identity:
            raise ContractViolation("floats must not appear in identity fields")
        return round(obj, 4)
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v, for_identity=for_identity)
                for k, v in sorted(obj.items())}
    if hasattr(obj, "__dataclass_fields__"):
        out: dict[str, object] = {}
        for name in obj.__dataclass_fields__:  # declaration order — stable
            if for_identity and name in _IDENTITY_EXCLUDED:
                continue
            out[name] = to_jsonable(getattr(obj, name), for_identity=for_identity)
        return out
    if isinstance(obj, Sequence):
        return [to_jsonable(v, for_identity=for_identity) for v in obj]
    raise ContractViolation(f"not serializable under the contract: {type(obj)!r}")


def canonical_json(obj: object, *, for_identity: bool = False) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace, UTF-8, LF.

    Used for every hashed or persisted structure.
    """
    return json.dumps(
        to_jsonable(obj, for_identity=for_identity),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def content_hash(obj: object) -> str:
    """SHA-256 over the identity projection of ``obj``.

    Note the split the manifest must state explicitly (architecture §Determinism
    spine): run timestamp and operator live outside the hashed content, so a
    rerun at a different time still proves byte-identical *content*.
    """
    return hashlib.sha256(
        canonical_json(obj, for_identity=True).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Canonical ordering
# ---------------------------------------------------------------------------


def document_sort_key(doc: DocumentRecord) -> tuple[str, str, int]:
    """The one true document order: relative path, then SHA-256, then position
    within a container.

    Every emitter, the index, the log and the ID assigner must use this. Two
    files can share a path only across container boundaries, so the hash and
    container order break the tie deterministically.
    """
    return (doc.rel_path, doc.sha256, doc.container_order or 0)


__all__ = [
    "CONTRACT_VERSION",
    "PageKind",
    "Disposition",
    "ProcessingStatus",
    "IdRegime",
    "PageRecord",
    "DocumentRecord",
    "TokenEstimate",
    "ReconciliationRow",
    "ReconciliationReport",
    "EffectiveLimits",
    "MasterIndexSnapshot",
    "RunConfig",
    "RunResult",
    "DocIQError",
    "ContractViolation",
    "ExtractionError",
    "to_jsonable",
    "canonical_json",
    "content_hash",
    "document_sort_key",
]
