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
from dataclasses import dataclass, field, replace
from typing import Protocol

from dociq.contracts import RunConfig, RunResult
from dociq.runstate import COMPLETED, RunTermination, TerminalStatus

DIRECT_CONTEXT_TOKENS = 200_000
"""How much text a Claude Project holds in direct context before it falls back
to retrieval mode — the reference row at the foot of the reduction waterfall.

**RULED D-21 (2026-08-01), NOT MEASURED.** 200,000 is the working figure Alex
ruled to keep, rendered as a named, sourced reference line — "Claude Project
direct context" — and never as a budget or a target (D-15: over-capacity is the
expected state). It has NOT been confirmed against Anthropic's published limits.
It is a single named constant so that a screen never inlines it, and
``tests/test_gui_screen_states.py`` asserts no ``200_000`` appears anywhere
under ``dociq/gui/``.

**Confirming the figure is NOT a one-line change, and an earlier version of
this docstring said it was.** Two further literals hold the same number for
different jobs: ``verify.tokens.DIRECT_CONTEXT_TOKENS``, the default limit
``TokenEstimate.capacity()`` falls back to, and
``emit.handoff.ProjectLimits.direct_context_tokens``, the limit an upload
package is checked against. They are deliberately separate — the seam's is a
*display reference line*, the emit layer's is an *operator-configurable package
constraint*, and collapsing them would let a caller's override of one silently
move the other. Unifying them by importing this constant into ``verify`` and
``emit`` would invert the dependency direction the pagemodel freeze protects,
so the duplication is kept and disclosed rather than removed.

But it means confirming 200,000 against Anthropic's published limits means
changing three — and it once meant a package telling its operator *"Fits
directly in a Claude Project"* while telling the recipient, in the README they
actually read, *"About 181–197% of direct-context capacity — the Project will
operate in retrieval (RAG) mode."* That defect is fixed in ``emit/handoff.py``
(one verdict, one limit, regression-tested); this note exists so the next
person to touch the figure knows how many places it lives in.

Amendment A-13. The previous docstring said the threshold was unruled, which
D-21 made false; the sentence was corrected rather than deleted, because what
remains true — that it is unmeasured — is the part a reader needs.
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


LEVER_RECOGNIZED = "recognized"
"""A section the tool RECOGNIZED and will never offer to drop (amendment A-20).

`section_taxonomy.md` §4 grades some categories HIGH risk and the shipped
template marks them ``offer=False``: the executive summary, the critical path
narrative, the weather log, the timesheets, the manpower histograms, the change
log, the quality/NCR log, the action register. They are recognized so an expert
can find and cite them, and they are **not levers**.

A third kind rather than an ``offer`` flag on the row, because the distinction
has to survive :meth:`ReductionPlan.with_toggled`. A boolean that a widget is
trusted to respect is a boolean some future widget does not; a kind that is not
``LEVER_EXPERT`` is locked by :attr:`ReductionLever.locked`, and the toggle
ignores locked rows at the model layer where no screen can reach past it.

It is drawn and never merged into a total: an unengaged row contributes nothing
to :attr:`ReductionPlan.remaining_tokens` either way, and showing it is the
point — a waterfall that silently omitted the sections it will not drop would
let a reader believe the record contains less than it does.
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

    rule: str = ""
    """The profile's own matching rule for this section, verbatim (A-11b).

    Attribution by rule *identity* — "profile X v1 → section 'photo_logs' →
    DROP" — is true but tells the expert nothing about what actually matched.
    This is the pattern itself, so the checklist can show what a DROP catches
    rather than only that one exists."""

    note: str = ""
    """The profile's §6 notes field for this section: why it is dropped and who
    approved it (A-11b).

    This is what makes an omission defensible **in the expert's own words**
    rather than in the tool's. It is carried verbatim and never paraphrased by
    a widget — a GUI-authored rationale for an evidentiary omission would be the
    tool putting words in the expert's mouth."""

    family_id: str = ""
    """The template family this row belongs to (amendment A-20).

    The identifier an :class:`OmissionApproval` is given against, so the row the
    expert clicked and the approval the pipeline records name the same thing.
    Empty for a recognized section the loaded template defines no family for —
    which is a row that can be seen and cannot be engaged."""

    risk: str = ""
    """``low`` / ``medium`` / ``high`` — what dropping this section costs
    (§4, A-20).

    On the row rather than in a tooltip, and required for the same reason §5.3
    gives: risk is **deliberately not correlated with size**. The most dangerous
    categories in the taxonomy are among the smallest — a weather log is trivial
    in tokens and decisive in a weather-delay claim. A waterfall sorted by
    saving puts a HIGH-risk row next to a large number and an easy click, and
    the only thing standing between those two facts is this field being
    rendered."""

    tier: str = ""
    """How the section was recognized: ``t1_outline``, ``t3_page_class``
    (A-18, A-20).

    "The document's own outline placed these pages here" and "a page-class rule
    matched them" are different claims with different strengths, and §5.4 puts
    the difference in front of the person deciding — not only in the log after
    they have decided."""

    approved_by: str = ""
    """Who approved this omission, once someone has (D-34).

    Empty until a human engages the row. It is never defaulted and never
    inferred from the template: a template approved nothing, and the approver
    field holding a fiction is the exact failure D-34 was ruled to prevent."""

    @property
    def locked(self) -> bool:
        """True for every row that is not the expert's to toggle.

        **Widened from ``kind == LEVER_AUTOMATIC``** when A-20 added
        :data:`LEVER_RECOGNIZED`. Written as "not expert" rather than as a list
        of locked kinds on purpose: the next kind added is locked unless someone
        deliberately unlocks it, which is the safe direction for a predicate
        that decides whether a page can be dropped."""
        return self.kind != LEVER_EXPERT


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
        rather than raising: a click on a locked row is a question, not a bug.

        Rebuilt with :func:`dataclasses.replace`, NOT by re-listing the fields
        positionally. The positional form was correct until A-11b added ``rule``
        and ``note``, at which point every toggle would have silently dropped an
        expert's stated reason for an omission — visible on screen before the
        click and gone after it. Field-by-field reconstruction of a frozen
        record is a defect waiting for the next field; ``replace`` cannot have
        that failure mode. ``tests/test_view_models.py`` asserts the property
        over every field rather than over the two this amendment happened to
        add.

        **And the fix missed its own method.** The amendment claimed all four
        rebuild sites were corrected; the return statement below rebuilt
        ``ReductionPlan`` positionally, inside the very method whose lesson was
        that positional reconstruction of a frozen record is a defect waiting
        for the next field. Lossless at 4 of 4 fields and silently lossy on the
        fifth. Found by the rehearsal review, not by the probe written to catch
        exactly this — which is why the probe now enumerates every frozen
        presentation record in this module rather than the one record that
        happened to fail first."""
        levers = tuple(
            lever if (lever.key != key or lever.locked)
            else replace(lever, engaged=not lever.engaged)
            for lever in self.levers
        )
        return replace(self, levers=levers)

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

    superseded_residue: tuple[str, ...] = ()
    """``.dociq/`` trees a completed run could not delete (amendment A-16).

    **The name predates what it carries.** It was written for the set-aside
    trees of the publication protocol D-32 removed, and it is kept unchanged
    because renaming a wired seam field is a contract amendment, which this
    descope has no business making. What it now carries is
    :func:`dociq.emit.paths.state_residue`'s answer on the success path: a
    drained ``.dociq/staging/`` that could not be removed after every file had
    been moved out of it.

    **This is a success with a residue, not a failure**, and the screen must say
    so in that order. The matter folder holds one complete, correct set; what
    remains under ``.dociq/`` is empty directories. The run published. The
    evidence is right.

    It crosses the seam because **nobody opens ``.dociq/``**. Left unsurfaced it
    is disk that fills for reasons no operator can see, and — worse for this
    tool — a stale copy of a previous run's deliverables sitting on the matter
    machine with nothing on screen having mentioned it.

    Deliberately NOT routed through ``RunResult.warnings``: those become hashed
    ``content``, and a run that hit a transient lock and one that did not would
    then produce different bytes for the same evidence. That is criterion 7's
    whole boundary, and this project has twice had to unpick something from
    hashed content that belonged in the run record instead."""


@dataclass(frozen=True, slots=True)
class BatesProposal:
    """A detected Bates format, put to the operator for confirmation (A-14).

    §4 Stage 3 requires the format to be confirmed **with the operator** on
    first detection. Sprint 2 shipped without this and the cost was total:
    ``auto_confirm_bates`` is False for a GUI run, so the format never reached
    CONFIRMED, so ``apply_bates_reported`` returned every document unchanged and
    **a Bates-stamped production produced no locators at all**. The acceptance
    harness reached criterion 4's headline figure at all only because it
    constructs the confirmed decision directly — a code path the product could
    not reach.

    That figure is **92.130%, and it is a PROJECTION** (D-29): 568 native from
    an earlier full-corpus measurement plus 29 OCR'd measured on a subset. The
    last end-to-end **measured** full-corpus number is **91.512%**. Stated here
    because this docstring is where a reader meets the number, and an earlier
    draft of this very paragraph called it "measured" — the same defect the
    rehearsal review had just caught in the decision register, reintroduced in
    the commit that fixed it.

    The record is deliberately not just the pattern. An operator cannot confirm
    a regex; they can confirm *"iiCON000123, on 15 of 33 pages across 20
    documents"*, which is what :attr:`example` and the coverage fields are for.
    """

    pattern: str
    """The detected format, in the pipeline's own words."""

    example: str = ""
    """A real locator read off a real page — the thing the operator recognizes."""

    documents: int = 0
    pages: int = 0
    coverage_pct: float = 0.0
    """Share of sampled pages the format matched. Shown because a format that
    matched a third of pages and one that matched all of them are different
    propositions, and the operator is the only one who can say which is
    acceptable for the matter in hand."""

    alternatives: tuple[str, ...] = ()
    """Other prefixes seen in the same matter. Non-empty means the production is
    multi-series, which is exactly the condition D-28 refuses prefix repair on —
    so the operator must see it rather than have it decided for them."""


BatesConfirm = Callable[[BatesProposal], bool]
"""Ask the operator to confirm a detected Bates format. ``True`` confirms.

``None`` means *nobody was asked*, which is NOT the same as a refusal and must
not be recorded as one: a headless run records a machine confirmation and says
so, because a machine-confirmed pattern and an expert-confirmed one are not the
same evidentiary object.
"""


@dataclass(frozen=True, slots=True)
class PackageResult:
    """What §8 Path A actually wrote (amendment A-12).

    A presentation record, like :class:`Reconciliation` — the GUI never touches
    ``emit`` and never writes a file, so this is how it learns what exists.

    :attr:`scope_statement` is not decoration and not a caption. **D-20 rules
    that Path A is proven on a deliberately scoped SUBSET**, so every package is
    a subset unless it says otherwise, and downstream nobody can tell a subset
    from a whole record by looking at it. The statement is written INTO the
    package ahead of everything else; this field is the same sentence, so the
    screen shows the operator exactly what the recipient will read rather than
    a second sentence that could drift from it.
    """

    root: str
    file_count: int
    total_bytes: int
    scope_statement: str
    doc_count: int = 0

    residue: tuple[str, ...] = ()
    """Old package trees under ``.dociq/`` this build could not delete
    (amendment A-17, from Codex third-fix-round finding A-7).

    The package's exact analogue of :attr:`RunOutcome.superseded_residue`, and
    it exists because A-16 did NOT cover it: ``emit.paths.state_residue()``
    recognizes DocIQ's own state trees and not ``package_superseded``. So the field that was supposed to make undeletable
    residue visible had a blind spot the exact width of the package path, and
    the GUI said only "Upload package built" while a full stale copy of a
    previous package sat on the matter machine.

    Rendered success-FIRST, in that order, exactly as A-16 requires: the
    package published, it is correct, and a named old copy remains. A partial
    old package on a machine an operator uploads from is a retention problem and
    a confusion problem, not a build failure, and calling it either of the other
    two would be wrong."""

    missing: tuple[str, ...] = ()
    """Doc IDs the operator asked for that have no ``clean_text`` file (B5).

    ``build_upload_package`` has always computed this, with a docstring saying
    it is "reported rather than silently skipped… the operator is the only one
    who can say whether it matters" — and then it reached no screen, because
    there was nowhere on the seam to put it. A package quietly missing
    documents the operator selected is the same failure class as a subset that
    does not say it is a subset: the recipient cannot tell."""


@dataclass(frozen=True, slots=True)
class OmissionApproval:
    """One expert, engaging one lever, as it crosses the seam (D-34, A-20).

    A seam record rather than :class:`dociq.sections.model.ApprovedOmission`
    itself, because the freeze forbids the GUI reaching into the pipeline's
    packages — the adapter converts. The fields are the same fields, and they
    are all of them: an approval that reached a screen without its approver, or
    without the matter it was given on, would be exactly the half-record D-34
    says must not exist.

    **The GUI never constructs one.** It asks the pipeline to record an approval
    and is handed this back. That is what keeps ``approved_by`` and
    ``approved_at`` facts about the machine and the moment rather than strings a
    widget composed — a GUI-authored approver is a fiction, and the ruling is
    that this field never holds one.
    """

    family_id: str
    approved_by: str
    approved_at: str
    matter: str
    """The matter's NAME, for display."""

    matter_root: str
    """The folder the approval was given on, keyed by
    ``dociq.contracts.matter_key``. What decides whether a later run is the same
    matter — the name does not, because two clients each having a `Production`
    folder is ordinary."""

    template_id: str
    template_version: str


@dataclass(frozen=True, slots=True)
class RunRequest:
    """What the setup screen collected. Turned into a :class:`RunConfig` by the
    adapter — building the config is pipeline work, not GUI work, because the
    config's fields are part of the determinism contract."""

    source_root: str
    output_root: str
    profile: ProfileInfo | None = None
    master_index_path: str | None = None

    approvals: tuple[OmissionApproval, ...] = ()
    """The omissions an expert engaged on the waterfall, carried into the run
    that will act on them (D-34).

    On the REQUEST rather than held inside the adapter, deliberately. Engaging a
    lever changes no file — the deliverables were written by the run that has
    already finished, which is why the summary screen marks itself stale — so
    the approval has to survive as far as the next run's inputs, and an adapter
    that remembered it privately would make two runs from one visible request
    produce different corpora. It is an input; it travels with the inputs."""


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

    def check_folders(self, source: str, output: str) -> str:
        """Why this pair of folders cannot be used, or ``""``.

        Asked as the operator picks, so a refusal arrives beside the folder that
        caused it rather than after the run is started. The pipeline answers,
        because the rule is the pipeline's: the GUI may not import
        :mod:`dociq.ingest`, and a screen that re-implemented the check would be
        a second definition free to disagree with the first.

        An adapter that does not offer it returns ``""`` and the run answers
        instead, exactly as it did before — later, but never wrong.
        """
        ...

    def run(
        self,
        request: RunRequest,
        on_progress: ProgressCallback,
        should_cancel: CancelCheck,
        confirm_bates: "BatesConfirm | None" = None,
    ) -> RunOutcome:
        """Run the pipeline.

        ``confirm_bates`` carries §4 Stage 3's operator confirmation across the
        seam (A-14). It is **optional with a None default** so that every
        existing caller — the tests, the headless harnesses, the self-test —
        keeps working unchanged; adding it as a required parameter would have
        broken the only implementations that can demonstrate the seam holds.

        ``None`` means no operator is available, and the pipeline then records a
        *machine* confirmation in the run's warnings. It must never be recorded
        as an operator confirmation, and a refusal must never be silently
        treated as "no Bates present" — an unstamped production and a stamped
        one whose format was declined are different facts about the record.
        """
        ...

    def set_omission(
        self, family_id: str, engaged: bool, matter: str, source_root: str
    ) -> "OmissionApproval | None":
        """Engage or withdraw one omission, and CAPTURE THE APPROVER (D-34).

        This is the moment D-34 is about. A template ships unengaged and can
        never drop a page; the instant a human ticks a row, DocIQ writes that
        person's identity, the time and the matter into the approval and into
        every drop-log line it will produce. Returns the record on engage and
        ``None`` on withdrawal.

        It is a pipeline call rather than a GUI construction because the
        approver is the machine's answer to "who is running this", not a string
        a screen can compose. The alternative — a widget filling in a name — is
        the fiction the ruling forbids.

        ``source_root`` is the folder the run is over, and it is a separate
        argument from ``matter`` on purpose: the first decides scope, the second
        is what an expert reads. Deriving one from the other is what let a
        `Production` folder under one client authorize an omission in a
        `Production` folder under another.

        An adapter that does not offer it leaves the row un-engageable, which is
        the safe direction: no approval, no drop.
        """
        ...

    def profile_rules(
        self, profile: ProfileInfo
    ) -> tuple[tuple[ReductionLever, ...], TokenBasis, str]:
        """The profile's KEEP/DROP rules, what each is worth, and — in the
        pipeline's own words — where the rules and the figures came from
        (amendment A-11).

        §6's checklist gates a run: the expert approves the omissions BEFORE the
        pipeline commits to them, which means the rules must cross the seam
        before there is a :class:`RunOutcome` to carry them. ``ProfileInfo``
        says only HOW MANY rules a profile holds, never which.

        Rows for a profile that has not been run against this matter carry
        ``estimated=True`` — they are projections, not counts.

        An adapter that cannot supply these returns ``((), TokenBasis(), "")``.
        The screen renders that as a loud empty state that DISABLES approval,
        because an empty checklist that says it is empty is safe and one that
        looks complete is not.
        """
        ...

    def matter_layout_note(self, outcome: RunOutcome) -> str:
        """§8 Path B: what is in the matter folder and what to point Claude at,
        in the pipeline's words, HAVING CHECKED (amendment A-12).

        ``emit.handoff.expert_assist_layout`` inspects the folder rather than
        describing it from memory, and that distinction is the whole point:
        Path B's claim is that DocIQ writes where Expert Assist already reads,
        and asserting that without looking is precisely the assertion worth
        checking. Returns "" when the adapter did not look — the screen then
        says so instead of implying a verified folder.
        """
        ...

    def build_package(
        self,
        outcome: RunOutcome,
        doc_ids: tuple[str, ...],
        scope_statement: str,
    ) -> PackageResult:
        """§8 Path A: assemble ``upload_package/`` for exactly ``doc_ids``, with
        ``scope_statement`` written into the package ahead of everything else
        (amendment A-12, D-20).

        Emit-layer work, deliberately: the GUI may not import ``emit``, and a
        second copy of §8's "only these files are uploaded" rule in a widget
        would fail by uploading DocIQ's own audit trail into the evidence
        corpus.

        An adapter that does not offer Path A may omit this method entirely
        rather than returning an empty result — the GUI probes for it and
        disables the action WITH THE REASON ON SCREEN. A stand-in silently
        returning nothing would leave the operator pressing a button that
        appears to work.
        """
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
    """THE SWAP POINT — **swapped** (Sprint 2).

    Returns the real adapter, :class:`dociq.adapter.RealPipeline`. The mock is
    still installable through :func:`set_pipeline`, and that is how
    ``tests/test_gui_states.py`` and ``tests/test_view_models.py`` keep proving
    the seam holds: they are the only thing that can, because they are the only
    consumer that runs against both sides of it.

    The import is function-local and stays that way. ``dociq.adapter`` imports
    six pipeline packages, and ``tests/test_import_graph.py`` asserts that
    importing the whole GUI pulls in none of them — a module-level import here
    would make merely opening the window drag in ``rapidocr``, and would break
    the freeze's Track-C rule at its only remaining seam.
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    from dociq.adapter import RealPipeline

    return RealPipeline()


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
    "LEVER_RECOGNIZED",
    "OmissionApproval",
    "ProfileInfo",
    "TokenBasis",
    "ReductionLever",
    "ReductionPlan",
    "FolderPreview",
    "TokenEstimate",
    "ReconciliationRow",
    "Reconciliation",
    "PackageResult",
    "BatesProposal",
    "BatesConfirm",
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
