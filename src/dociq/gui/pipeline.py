"""THE SEAM between the GUI and the processing pipeline.

Sprint 1 builds the shell against a mock; Sprint 2 wires the real pipeline. The
whole of that swap is :func:`get_pipeline` — one function, one line to change.
Nothing above this module knows whether a run was real, and nothing in this
module knows what a widget is.

Two rules make the seam hold:

1. **Everything crossing it is either a frozen contract object or a small
   presentation record defined here.** The GUI never sees a pipeline internal.
2. **The GUI computes nothing the pipeline is responsible for.** Page accounting
   is read from the contract's derived properties. The token estimate and the
   master-index reconciliation are *supplied by the adapter* — see
   ``docs/contracts/amendments.md``: ``RunResult`` cannot express either, and
   inventing them in a widget would put a second, disagreeing estimator in the
   product (§7 requires the emit layer to write the token estimate into
   ``run_summary.pdf``, so the pipeline's number is the only true one).

Track C may not import ``ingest``, ``identify``, ``docid``, ``profiles``,
``emit`` or ``verify``. ``tests/test_import_graph.py`` asserts it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from dociq.contracts import RunConfig, RunResult
from dociq.runstate import COMPLETED, RunTermination, TerminalStatus

DIRECT_CONTEXT_TOKENS = 200_000
"""How much text a Claude Project holds in direct context before it falls back
to retrieval mode — the reference row at the foot of the reduction waterfall.

**UNCONFIRMED.** Alex has not ruled this threshold and it is not measured; 200K
is a working placeholder. It is a single named constant precisely so that
confirming it is a one-line change — the literal appears nowhere else, and no
screen may inline it.
"""


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """A format profile as the picker shows it (§6)."""

    profile_id: str
    version: str
    label: str
    """Plain-language name — "MODEC monthly progress report", not an id."""
    section_rules: int = 0
    """How many KEEP/DROP rules the profile carries. Shown so the operator can
    see at a glance whether a profile will actually remove anything."""


@dataclass(frozen=True, slots=True)
class FolderPreview:
    """What a folder looks like before a run — shown next to the picker so the
    operator can confirm they chose the right folder without opening Explorer."""

    file_count: int
    total_bytes: int
    by_extension: tuple[tuple[str, int], ...] = ()
    estimated_minutes: int = 0
    """Rough wall-clock estimate, shown beside the action so the operator knows
    what they are starting. Zero means "no estimate" and the screen says nothing
    rather than inventing a number."""


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """A token figure and — inseparably — how it was obtained.

    Shaped to the announced contract v1.1.0 so the seam becomes a thin read of
    ``RunResult.tokens_before`` / ``tokens_after`` on rebase.

    **The provenance is not decoration.** DocIQ is an evidentiary tool: a number
    on screen that cannot say where it came from is a claim the expert would
    have to defend without support. Track B measured 2.53 chars per pre-token
    across the full 298-PDF record — dense material, well below ordinary prose.
    So the ratio is configuration, the provenance travels beside every figure
    derived from it, and the GUI renders neither ratio nor band as fact.

    **What this type no longer claims.** It used to carry ``floor_tokens``, a
    "hard lower bound" taken from the pre-token count, and the screen rendered
    it as "tokens at least". Codex review #1 finding B-6 established that the
    pre-token count is not a bound for any tokenizer but DocIQ's own regex, so
    the field is replaced by :attr:`structural_tokens` — the same measurement,
    named for what it is, and rendered as an estimate rather than a floor.
    """

    chars: int
    ratio_low: float
    ratio_high: float
    structural_tokens: int = 0
    """Tokens implied by the text's measured pre-token structure under the
    assumptions stated in :mod:`dociq.verify.tokens`, or 0 when not measured.

    **Not a bound in either direction.** A tokenizer whose pre-tokenization is
    coarser than DocIQ's approximation emits fewer tokens than this. It is
    preferred for display over the ratio range because it is derived from THIS
    text rather than from a ruled constant — but it is displayed as an estimate,
    with its assumptions one click away, never as a floor."""

    provenance: str = ""
    """How the figure was obtained, in the pipeline's own words. The GUI shows
    this verbatim and never substitutes a literal of its own."""

    ratio_refuted: bool = False
    """Set when the ruled ratio band lies entirely below the range the measured
    structure allows under those assumptions, so the band alone would understate
    the load. The screen must say so rather than quietly printing a number.

    A conditional inconsistency, not a proof the band is wrong — the wording the
    screen uses must not promote it into one (finding B-6)."""

    @property
    def high(self) -> int:
        """Upper end of the ratio range: the *smaller* chars-per-token ratio."""
        return round(self.chars / self.ratio_low)

    @property
    def low(self) -> int:
        return round(self.chars / self.ratio_high)

    @property
    def is_structural(self) -> bool:
        """True when the figure comes from this text's own measured structure
        rather than from the ruled ratio band."""
        return self.structural_tokens > 0

    @property
    def tokens(self) -> int:
        """The figure to display: the structural estimate where one was
        measured, the conservative end of the ratio range otherwise."""
        return self.structural_tokens if self.is_structural else self.high


@dataclass(frozen=True, slots=True)
class TokenBasis:
    """Provenance carried alongside a whole set of token figures.

    The waterfall shows a dozen numbers that all share one basis; repeating a
    :class:`TokenEstimate` on each lever would let them drift apart, and a lever
    whose provenance disagreed with the headline's would be worse than no
    provenance at all.
    """

    provenance: str = ""
    is_structural: bool = False
    ratio_refuted: bool = False

    @classmethod
    def of(cls, estimate: "TokenEstimate") -> "TokenBasis":
        return cls(estimate.provenance, estimate.is_structural,
                   estimate.ratio_refuted)


LEVER_EXPERT = "expert"
"""A section an expert chose to drop (§6). Interactive, accent-colored."""

LEVER_AUTOMATIC = "automatic"
"""A saving the tool made mechanically — exact-hash duplicates, page furniture.

Never merged into the expert's total. The profile system exists to keep "the
expert approved this omission" separate from "the tool did this mechanically",
and Principle 3 is what makes that distinction load-bearing rather than
cosmetic: only the first kind is the expert's to defend.
"""


@dataclass(frozen=True, slots=True)
class ReductionLever:
    """One row of the reduction waterfall."""

    key: str
    label: str
    """Plain language, as the profile names the section: "Photo logs"."""
    tokens: int
    """Tokens this lever removes when engaged."""
    pages: int
    kind: str = LEVER_EXPERT
    engaged: bool = True
    """Whether it is currently dropping. Expert levers start where the profile
    left them; automatic levers are always engaged."""

    estimated: bool = False
    """True when this lever's saving is projected rather than counted.

    Shown on the row. A projected figure standing next to counted ones, in the
    same column, in the same type, is a claim the run cannot support — and the
    reader has no way to tell which is which unless the row says so."""

    @property
    def locked(self) -> bool:
        return self.kind == LEVER_AUTOMATIC


@dataclass(frozen=True, slots=True)
class ReductionPlan:
    """The waterfall's model: the full record, the levers, and the capacity line.

    Supplied by the pipeline — the GUI toggles levers and re-adds the numbers,
    it does not decide what a lever saves. See ``docs/contracts/amendments.md``
    A-01: the contract cannot yet carry per-section token savings either.
    """

    full_tokens: int
    levers: tuple[ReductionLever, ...] = ()
    capacity: int = DIRECT_CONTEXT_TOKENS
    basis: TokenBasis = TokenBasis()
    """Where every figure in this plan came from. Travels with the numbers."""

    def with_toggled(self, key: str) -> "ReductionPlan":
        """A copy with one expert lever flipped. Locked levers ignore the call
        rather than raising: a click on a locked row is a question, not a bug."""
        levers = tuple(
            lever if (lever.key != key or lever.locked)
            else ReductionLever(lever.key, lever.label, lever.tokens,
                                lever.pages, lever.kind, not lever.engaged,
                                lever.estimated)
            for lever in self.levers
        )
        return ReductionPlan(self.full_tokens, levers, self.capacity, self.basis)

    @property
    def engaged(self) -> tuple[ReductionLever, ...]:
        return tuple(lever for lever in self.levers if lever.engaged)

    @property
    def remaining_tokens(self) -> int:
        return self.full_tokens - sum(lever.tokens for lever in self.engaged)

    @property
    def expert_tokens(self) -> int:
        return sum(lever.tokens for lever in self.engaged
                   if lever.kind == LEVER_EXPERT)

    @property
    def automatic_tokens(self) -> int:
        return sum(lever.tokens for lever in self.engaged
                   if lever.kind == LEVER_AUTOMATIC)

    @property
    def pages_dropped(self) -> int:
        return sum(lever.pages for lever in self.engaged)

    @property
    def over_capacity_factor(self) -> float:
        return self.remaining_tokens / self.capacity if self.capacity else 0.0

    @property
    def fits(self) -> bool:
        return self.remaining_tokens <= self.capacity


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    """One line of the §5 master-index reconciliation, for the detail view."""

    category: str
    """One of ``folder-only``, ``index-only``, ``field-mismatch``."""
    doc_id: str
    filename: str
    detail: str


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The §5 reconciliation summary. ``None`` when no master index was given."""

    matched: int
    rows: tuple[ReconciliationRow, ...] = ()

    @property
    def mismatches(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Everything the summary screen needs, in one object."""

    result: RunResult
    tokens_before: TokenEstimate
    tokens_after: TokenEstimate
    reconciliation: Reconciliation | None = None
    output_root: str = ""
    plan: ReductionPlan | None = None
    """The waterfall's levers. ``None`` when the run had no profile and nothing
    mechanical to report — the screen then shows the record at full size."""

    termination: RunTermination = COMPLETED
    """How the run ENDED (Codex review #1, finding B-1).

    A frozen non-contract record, so it crosses the seam under rule 1. The GUI
    must never present a blocked or cancelled run as an ordinary result: the
    numbers on the summary screen would describe part of a corpus, and the
    output folder the screen offers to open holds the PREVIOUS run's
    deliverables, not this one's."""

    published: bool = True
    """Whether this run wrote the §7 deliverables. False for every non-complete
    termination — see :attr:`termination`."""


@dataclass(frozen=True, slots=True)
class RunRequest:
    """What the setup screen collected. Turned into a :class:`RunConfig` by the
    adapter — building the config is pipeline work, not GUI work, because the
    config's fields are part of the determinism contract."""

    source_root: str
    output_root: str
    profile: ProfileInfo | None = None
    master_index_path: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One step of a run, as the progress screen shows it."""

    done: int
    total: int
    filename: str
    status: str
    """Plain language, shown verbatim: "read 148 pages", "OCR — 12 pages"."""
    flagged: bool = False


ProgressCallback = Callable[[ProgressEvent], None]
CancelCheck = Callable[[], bool]


class PipelineAPI(Protocol):
    """What the GUI is allowed to ask of the pipeline. Nothing more."""

    def profiles(self) -> tuple[ProfileInfo, ...]:
        ...

    def preview_folder(self, path: str) -> FolderPreview:
        ...

    def run(
        self,
        request: RunRequest,
        on_progress: ProgressCallback,
        should_cancel: CancelCheck,
    ) -> RunOutcome:
        ...

    def disclosure(self) -> str:
        """A standing notice the window shows above every screen, or "".

        Exists so a stand-in pipeline cannot be mistaken for a real one. The
        real adapter returns ""; the Sprint-1 mock returns what its figures are
        and how they differ from the measured record. A shell that looks exactly
        like the finished product while showing invented numbers is the single
        most expensive misunderstanding this project could ship.
        """
        ...


_OVERRIDE: PipelineAPI | None = None


def set_pipeline(pipeline: PipelineAPI | None) -> None:
    """Install a pipeline implementation. Used by tests and by Sprint 2's entry
    point; there is deliberately no auto-discovery."""
    global _OVERRIDE
    _OVERRIDE = pipeline


def get_pipeline() -> PipelineAPI:
    """THE SWAP POINT.

    Sprint 1 returns the mock. Sprint 2 returns the real adapter, and that is
    the entire integration change on the GUI side.
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    from dociq.gui.mock_pipeline import MockPipeline

    return MockPipeline()


def config_from(request: RunRequest) -> RunConfig:
    """The one place a :class:`RunRequest` becomes a :class:`RunConfig`.

    Lives beside the seam rather than in a screen so that when Sprint 2's real
    adapter needs to add a field to the config, exactly one call site changes.
    """
    return RunConfig(
        source_root=request.source_root,
        output_root=request.output_root,
        profile_id=request.profile.profile_id if request.profile else None,
        profile_version=request.profile.version if request.profile else None,
    )


__all__ = [
    "DIRECT_CONTEXT_TOKENS",
    "LEVER_AUTOMATIC",
    "LEVER_EXPERT",
    "ProfileInfo",
    "TokenBasis",
    "ReductionLever",
    "ReductionPlan",
    "FolderPreview",
    "TokenEstimate",
    "ReconciliationRow",
    "Reconciliation",
    "RunOutcome",
    "RunRequest",
    "ProgressEvent",
    "RunTermination",
    "TerminalStatus",
    "PipelineAPI",
    "get_pipeline",
    "set_pipeline",
    "config_from",
]
