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


LEVER_EXPERT = "expert"
"""A section an expert chose to drop (§6). Interactive, accent-coloured."""

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

    def with_toggled(self, key: str) -> "ReductionPlan":
        """A copy with one expert lever flipped. Locked levers ignore the call
        rather than raising: a click on a locked row is a question, not a bug."""
        levers = tuple(
            lever if (lever.key != key or lever.locked)
            else ReductionLever(lever.key, lever.label, lever.tokens,
                                lever.pages, lever.kind, not lever.engaged)
            for lever in self.levers
        )
        return ReductionPlan(self.full_tokens, levers, self.capacity)

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
class TokenEstimate:
    """A conservative token range, per D-03 (chars ÷ a calibrated ratio).

    Produced by the pipeline, never by the GUI. Carries the character count and
    the ratios so the summary can state its own basis rather than presenting a
    number with no provenance.
    """

    chars: int
    ratio_low: float
    ratio_high: float

    @property
    def high(self) -> int:
        """Upper end of the range: the *smaller* chars-per-token ratio."""
        return round(self.chars / self.ratio_low)

    @property
    def low(self) -> int:
        return round(self.chars / self.ratio_high)


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
    "ReductionLever",
    "ReductionPlan",
    "FolderPreview",
    "TokenEstimate",
    "ReconciliationRow",
    "Reconciliation",
    "RunOutcome",
    "RunRequest",
    "ProgressEvent",
    "PipelineAPI",
    "get_pipeline",
    "set_pipeline",
    "config_from",
]
