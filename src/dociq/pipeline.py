"""Stages 1 to 6, end to end — the seam between Track A's spine and Track B's
deliverables.

Before this module the two halves only met in a test. Track A's spine stopped at
:class:`~dociq.contracts.RunResult` and proved determinism against a provisional
stand-in emitter; Track B's emit layer was exercised from stub records. Both
halves were green and the join between them was not covered by anything.

There is exactly one orchestration here, and everything runs through it: the
self-test, the determinism harness and the GUI adapter. A second orchestration
would be a second definition of what a run *is*, and the two would drift — which
is the same argument the contract makes for having one serializer.

Stage order is §4's, and it is not negotiable:

1-2  walk, extract, normalize            :mod:`dociq.ingest.walker`
3    Bates detection                     :mod:`dociq.identify.bates`
3b   Doc ID assignment + reconciliation  :mod:`dociq.docid`
4    section KEEP/DROP                   :mod:`dociq.sections.apply`
5    emit                                :mod:`dociq.emit`
6    verify: accounting, manifest, tokens :mod:`dociq.verify`

Stage 3 runs before 3b because a Bates range is one of Stage 3b's match keys,
and Stage 4 runs after 3b because a drop-log entry is written against a Doc ID.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Mapping, Sequence

from dociq.contracts import (
    ContractViolation,
    Disposition,
    DocumentRecord,
    IdRegime,
    OmissionSnapshot,
    PageKind,
    ProcessingStatus,
    ReconciliationRow,
    RunConfig,
    RunResult,
    TokenEstimate,
    document_sort_key,
    run_identity,
)
from dociq.contracts import ReconciliationReport as ContractReconciliation
from dociq.docid.assign import AssignmentResult, assign_doc_ids
from dociq.docid.masterindex import MasterIndex, load_master_index
from dociq.docid.reconcile import (
    IssuedIdLedger,
    ReconciliationReport,
    RenumberWarning,
    detect_renumbering,
    reconcile,
)
from dociq.emit.cleantext import write_clean_text, write_sources_json
from dociq.emit.handoff import build_upload_package
from dociq.emit.indexbook import (
    build_index_rows,
    write_index_csv,
    write_index_xlsx,
    write_reconciliation_csv,
)
from dociq.emit.log import LogBundle, build_log, write_processing_log
from dociq.emit import paths as emit_paths
from dociq.emit.paths import OutputLayout
from dociq.emit.summary import build_summary_data, write_run_summary
from dociq.identify.bates import (
    BatesDecision,
    BatesPatternError,
    BatesProposal,
    BatesRange,
    DecisionStatus,
    apply_bates_reported,
    document_ranges,
    matter_prefixes,
    parse_pattern,
    propose_format,
    ranges_by_sort_key,
)
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.operator import OperatorStamp, operator_stamp
from dociq.sections.apply import SectionApplyResult, SectionDropEntry, apply_sections
from dociq.sections.model import ApprovedOmission, SectionTemplate
from dociq.sections.resolve import spans_from_pages
from dociq.runstate import (
    COMPLETED,
    INCOMPLETE_DIR,
    STATUS_FILENAME,
    RunAborted,
    RunTermination,
    TerminalStatus,
)
from dociq.verify import accounting, manifest as mf
from dociq.verify.tokens import TokenEstimate as MeasuredEstimate
from dociq.verify.tokens import estimate_for_texts

__all__ = [
    "OCR_DISABLED",
    "PipelineOptions",
    "PipelineOutcome",
    "StageProgress",
    "STAGES",
    "run",
]

STAGES: tuple[tuple[int, str], ...] = (
    (1, "Reading the folder"),
    (2, "Extracting text"),
    (3, "Bates numbers and document IDs"),
    (4, "Deciding what to leave out"),
    (5, "Writing the deliverables"),
    (6, "Checking the results"),
)
"""§4's six stages, in plain language, numbered as §4 numbers them.

Named here rather than in a screen because the stage a run is in is the
pipeline's fact, not the GUI's — and because the measured reality makes the
numbering load-bearing rather than decorative. The acceptance run of 2026-08-02
— from scratch, OCR on, through the shipped adapter — measured Stages 1-2 at
**99.70% of run time** and everything after them at **18.5 seconds combined**
(0.30% of the run); a progress bar driven only by the walk therefore sits at
99.7% for the whole of the rest of the run, which reads as a hang. Stages 3-6 are
short, and they still have to say they are happening.

The register's earlier §10 restatement (2026-07-31) put the same two figures at
99.1% and 25.7 s, from a run that resumed 62 documents from an interrupted
attempt. That pair is superseded, not reconciled: **why the post-extraction work
took 25.7 s once and 18.5 s once over the same corpus is not established** — the
two runs differ in resumption, in machine load and in 35 pages, and no
measurement separates them. What both runs agree on, which is all this docstring
needs, is that the tail is under 1% of the wall clock. Optimizing anything but
extraction is optimizing under 1% of the run.
"""


@dataclass(frozen=True, slots=True)
class StageProgress:
    """Which of §4's stages the run is in. Reporting only — it never reaches
    disk, never enters hashed content, and no output depends on it."""

    stage: int
    name: str
    detail: str = ""
    total: int = len(STAGES)

    @property
    def headline(self) -> str:
        return f"Step {self.stage} of {self.total} — {self.name}"

OCR_DISABLED = "disabled"
"""``RunConfig.ocr_engine`` for a run that read no image page.

A sentinel in an existing field rather than a new one: the contract is frozen,
and it already has the field whose job is to say which engine produced the text.
"None of them" is an answer that field can give."""


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    """Everything a run needs that is not already part of the run identity.

    :class:`~dociq.contracts.RunConfig` carries what can change output bytes.
    This carries what cannot: which walker settings to use, who is running it,
    and which of the human-facing artifacts to produce.
    """

    walk: walker.WalkOptions | None = None
    matter_name: str = ""
    master_index: MasterIndex | None = None
    master_index_path: str | None = None

    template: SectionTemplate | None = None
    """The section template this run recognizes against
    (:mod:`dociq.sections.templates`).

    ``None`` is a real state and not a degraded one: pages are still placed in
    sections by Tiers 1 and 3, and the template is only what maps a section to a
    named family an expert can rule on. Without one, every page keeps."""

    approvals: tuple[ApprovedOmission, ...] = ()
    """The omissions an expert actually engaged (D-34).

    The only thing in the product that can drop a page. Empty is the state of a
    freshly-installed DocIQ and of every run nobody has ruled on, and it is the
    state D-34 requires a shipped template to arrive in — recognition happens,
    nothing is omitted, and the approver field holds no fiction because there is
    no approval."""

    bates_decision: BatesDecision | None = None
    confirm_bates: Callable[[BatesProposal, tuple[str, ...]], bool] | None = None
    """ASK the operator to confirm a detected Bates format (§4 Stage 3, A-14).

    Called as ``confirm_bates(proposal, other_prefixes)``. The second argument
    is **D-28's own census** — every OTHER prefix in this matter that clears the
    same two bars a proposal has to clear — and it is a separate argument rather
    than a field of ``proposal`` because ``BatesProposal.alternatives`` is not
    it. Those are the runner-up *shapes*, ``ranked[1:4]``, with no threshold
    applied at all: on the MNFV production they come back as ``Check 0001`` and
    ``retained 90095 49 00001``, two stray lines. Telling an operator that the
    production is multi-series — and that D-28 therefore refuses prefix repair —
    on the strength of a stray line would be a false statement about the record
    made in the one place the operator is being asked to rule.

    Sprint 2 shipped without this and the cost was total: the GUI had no way to
    ask, so every GUI run left :attr:`auto_confirm_bates` False, the decision
    stayed PENDING, and ``apply_bates_reported`` returned every document
    unchanged. **A Bates-stamped production came out of the product with no
    locators at all** while the acceptance harness PROJECTED 92.130% coverage
    through a code path — a hand-built CONFIRMED decision — the product could not
    reach. Rehearsal finding A4. *("measured" until 2026-08-18; D-29 rules it a
    projection and forbids quoting it flat. Measured end to end: 91.512%.)*

    Three outcomes, and they are three, not two:

    * returns ``True``  — the OPERATOR confirmed. Recorded as theirs.
    * returns ``False`` — the operator DECLINED. A ruling, recorded as one; an
      unstamped production and a stamped one whose format was declined are
      different facts about the record.
    * ``None`` (this field unset) — *nobody was asked*. The run falls through to
      :attr:`auto_confirm_bates`, exactly as before.

    Raising :class:`~dociq.runstate.RunAborted` from it abandons the run: the
    prompt is the one place the pipeline blocks on a human, so it is the one
    place that needs a way out that is not a ruling.

    It takes precedence over :attr:`auto_confirm_bates`. A caller that supplies
    both has an operator, and an operator's answer is never overridden by a
    machine's.
    """

    auto_confirm_bates: bool = False
    """Accept the detected Bates format without asking.

    §4 Stage 3 says the format is confirmed with the operator on first
    detection, and :attr:`confirm_bates` is how the GUI does that. This remains
    for the genuinely headless paths — the acceptance harness and the self-test
    — which have no operator to ask. It is recorded in the run's warnings
    whenever it fires, because a machine-confirmed pattern and an expert-
    confirmed one are not the same evidentiary object."""

    stamp: OperatorStamp | None = None
    previous_ledger: str | Path | None = None
    """Ledger of a previous run, for the D-04 renumbering check. Defaults to
    whatever ``doc_ids_issued.json`` is already sitting in the output root —
    which is the re-run case D-04 (b) is actually about."""

    on_stage: Callable[["StageProgress"], None] | None = None
    """Called once as each of §4's stages begins, and once when the run ends.

    :class:`~dociq.ingest.walker.WalkOptions` already carries a within-stage
    progress hook, and it covers Stages 1-2 — which are **99.70% of the wall
    clock** (acceptance run, 2026-08-02) and 0% of the remaining four stages.
    The figure here read "Stage 1 … 99.1%" and was wrong twice over: 99.1% is the
    superseded 2026-07-31 pair, and it was the combined Stages **1-2** figure in
    the register even then, not Stage 1's. Without this, every GUI progress bar stops
    at the walk's last tick and stays there while Bates detection, identifier
    assignment, reconciliation, classification, all of §7's emit and the
    accounting and manifest gates run. Reporting only: nothing here reaches
    disk, and an exception raised by the callback is not caught, because a
    consumer that cannot render progress is a bug in the consumer.
    """

    write_workbook: bool = True
    write_summary_pdf: bool = True
    write_package: bool = True
    """The three artifacts that need a third-party writer (openpyxl, reportlab)
    or a file copy. Off for harness runs that only care about the claim's four
    artifacts; on for anything an operator will see."""


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """One completed run: the contract result, the deliverables, the proofs."""

    result: RunResult
    """The §4 Stage-6 result, with Doc IDs assigned, dispositions applied, and
    ``tokens_before`` / ``tokens_after`` / ``reconciliation`` populated."""

    layout: OutputLayout
    accounting: accounting.AccountingReport
    manifest: mf.Manifest
    assignment: AssignmentResult
    reconciliation: ReconciliationReport
    log: LogBundle
    renumbering: tuple[RenumberWarning, ...] = ()
    walk_notes: walker.RunNotes = field(default_factory=walker.RunNotes)
    """Stage 1's invocation notes — serial retries, resume, cancellation.
    Recorded in the log's ``run`` section, never in its hashed content."""
    stale_removed: tuple[str, ...] = ()
    """Deliverables of a previous run that this one replaced. Recorded in the
    log's ``run`` section, never in its hashed content."""
    superseded_residue: tuple[str, ...] = ()
    """``.dociq/`` trees this run left behind (amendment A-16, after D-32).

    **The name is older than what it now carries** and is kept because it is a
    seam field that amendment A-16 wired end to end; renaming it is a contract
    change, and a misleading name explained in place is cheaper than a
    contract amendment nobody asked for. It named the set-aside trees of the
    swap protocol D-32 removed. It now names what
    :func:`~dociq.emit.paths.state_residue` finds: on this field, populated on
    the success path, that is a **drained staging tree the publication could not
    remove** — the matter folder holds one complete, correct set and
    ``.dociq/staging/`` holds empty directories. A residue, not a failed run,
    surfaced because nobody opens ``.dociq/``.

    Measured AFTER publication, which is why the log cannot carry it: the log is
    written into staging and sealed before publication happens. What the log
    carries is ``run.state_residue_before_run`` — what an EARLIER run left,
    measured at the top of this one. Stated rather than left to be
    discovered."""
    bates_ranges: dict[tuple[str, str, int], BatesRange] = field(default_factory=dict)
    timings_s: tuple[tuple[str, float], ...] = ()
    """Per-stage wall clock, in stage order. Reporting only — it never reaches
    a hashed artifact, because a run that took longer is not a different run."""

    termination: RunTermination = COMPLETED
    """How the run ENDED (Codex B-1). ``COMPLETED`` is the only value under
    which anything was written."""

    published: bool = True
    """Whether this run wrote the §7 deliverables.

    False for every non-complete termination, and the reason
    :attr:`incomplete_dir` exists. Recorded rather than re-derived so a
    consumer never has to know the publication rule."""

    incomplete_dir: Path | None = None
    """Where an aborted run recorded itself — ``<output>/incomplete_run/``.
    ``None`` for a completed run."""

    @property
    def ok(self) -> bool:
        """The run produced a complete, self-consistent, published output set.

        ``termination.complete`` is FIRST and is not redundant with the two
        gates after it (Codex B-1). Accounting is an identity over whatever set
        it is handed, and zero equals zero: an empty blocked run and a partial
        cancelled run both satisfy it. So does an empty manifest's
        ``unclassified``. Without this term, an aborted run reported ``ok``.
        """
        return (
            self.termination.complete
            and self.accounting.ok
            and not self.manifest.unclassified
        )

    def timing(self, stage: str) -> float:
        return dict(self.timings_s).get(stage, 0.0)


def _to_contract_estimate(est: MeasuredEstimate, label: str) -> TokenEstimate:
    """Project the measured estimate onto the frozen contract type.

    ``provenance`` comes from :meth:`MeasuredEstimate.provenance_text`, which is
    the one place a token figure's account of itself is written. It used to be
    assembled here as well, and the two accounts drifted — Codex review #1
    finding B-6 caught the run summary asserting a calibrated ratio for runs
    that used no such thing.

    ``floor_tokens`` is deliberately left at 0, its "not measured" value. The
    contract defines it as a *hard lower bound* on the true token count, and
    DocIQ has no such bound to offer: the pre-token count that used to be put
    here is a characterization under stated assumptions, not a floor (finding
    B-6, and the ``verify.tokens`` module docstring). Shipping the pre-token
    count in a field the contract calls a hard bound would put the withdrawn
    claim straight back into the machine-readable result. The measured pre-token
    count is not lost — it travels in ``provenance`` and in the processing log's
    ``token_estimate`` block, where it is labeled for what it is. See
    ``docs/contracts/amendments.md`` A-05 for the proposed contract-side repair.

    ``ratio_refuted`` is copied from the estimator's own test result. The
    contract says a consumer must never infer it, and the only way to keep that
    true is for the producer to be the one place it is decided."""
    return TokenEstimate(
        chars=est.profile.chars,
        ratio_low=est.basis.low_x100 / 100,
        ratio_high=est.basis.high_x100 / 100,
        floor_tokens=0,
        # A-05(a)'s replacement fields, POPULATED (round-2 F-5). 1.4.0 added
        # them and the projection never set them, so both stayed at their "not
        # measured" defaults while the very same run wrote both numbers into
        # the processing log and the summary PDF. The machine contract read
        # zero for text that had been measured — a consumer holding the
        # contract and a consumer reading the log were told different things
        # about one run, and the contract was the one that was wrong.
        #
        # These are the two the withdrawn ``floor_tokens`` is replaced BY, and
        # each says exactly what it is: ``structural_tokens`` is the measured
        # pre-token count, a characterization under the stated assumptions and
        # a bound in neither direction; ``token_ceiling`` is the one
        # tokenizer-independent bound DocIQ asserts.
        structural_tokens=est.profile.pretokens,
        token_ceiling=est.profile.token_ceiling,
        ratio_refuted=est.ratio_refuted,
        provenance=est.provenance_text(label),
    )


def _to_contract_reconciliation(
    report: ReconciliationReport,
) -> ContractReconciliation:
    """Project the §5 report onto the contract's A-02 type.

    The two are not redundant. :mod:`dociq.docid.reconcile`'s report is the
    working structure the workbook is built from; the contract's is the narrow
    projection a consumer across the seam is allowed to see, and it is what
    ``RunResult.reconciliation`` carries so the GUI never has to import
    ``docid``.
    """
    rows: list[ReconciliationRow] = []
    for pair in report.matched:
        for d in pair.discrepancies:
            rows.append(
                ReconciliationRow(
                    category="field-mismatch",
                    doc_id=pair.doc_id,
                    filename=pair.rel_path.rsplit("/", 1)[-1],
                    detail=(
                        f"{d.field}: folder {d.folder_value!r} vs index "
                        f"{d.index_value!r}"
                        + (f" — {d.detail}" if d.detail else "")
                    ),
                )
            )
    for entry in report.folder_only:
        rows.append(
            ReconciliationRow(
                category="folder-only",
                doc_id=entry.doc_id,
                filename=entry.filename,
                detail=entry.reason,
            )
        )
    for entry in report.index_only:
        # A quarantined row (Codex review #1, D-1) has no Original Sort, so it
        # has no LI File No to render. The old f-string produced "LI File No
        # (index row 2) at dir" — an empty identifier laid out exactly like a
        # real one, which is worse than saying nothing. Its own reason sentence
        # already names the row and why it carries no number.
        rows.append(
            ReconciliationRow(
                category="index-only",
                doc_id="",
                filename=entry.filename or f"(index row {entry.index_row_number})",
                detail=(
                    entry.reason
                    if entry.quarantined
                    else f"LI File No {entry.li_file_no} (index row "
                         f"{entry.index_row_number}) at {entry.filepath}"
                ),
            )
        )
    return ContractReconciliation(matched=len(report.matched), rows=tuple(rows))


def _stored_pattern(
    config: RunConfig, options: PipelineOptions, warnings: list[str]
) -> tuple[str, str] | None:
    """The confirmed pattern this matter already carries, as ``(pattern,
    source)``, or ``None`` when the matter carries none.

    Two places can hold one: the run's own configuration and a format profile.
    **One place holds it now.** Until D-38 a format profile could also carry a
    confirmed Bates pattern, so this function existed to reconcile two sources
    and to name the loser in the warnings rather than resolve them silently. The
    profile system is deleted; the run configuration is the only source left, and
    a reconciliation with nothing to reconcile is a branch that can only rot.
    """
    from_profiles: list[tuple[str, str]] = []
    if config.bates_pattern:
        for pattern, source in from_profiles:
            if pattern != config.bates_pattern:
                warnings.append(
                    f"Two different confirmed Bates formats are stored for this "
                    f"matter: the run configuration's, which was used, and "
                    f"{source}'s, which was NOT. Re-confirm the format if the "
                    "profile is the current one."
                )
        return config.bates_pattern, "the run configuration"
    if from_profiles:
        return from_profiles[0][0], from_profiles[0][1]
    return None


def _bates_decision(
    documents: Sequence[DocumentRecord],
    options: PipelineOptions,
    config: RunConfig,
    stamp: OperatorStamp,
    warnings: list[str],
    prefix_census: Callable[[], tuple[str, ...]] | None = None,
) -> BatesDecision | None:
    """Stage 3's decision, or ``None`` for an unstamped set.

    An unstamped set is the ordinary case (§4 Stage 3, D-13) and produces no
    warning at all: no stored pattern, no proposal, no decision, nothing
    written, nothing said.

    With no new operator decision, a *stored* confirmation is loaded and
    applied — that is what "confirmed once per document set, then applied
    automatically" means, and recording the old pattern in the run
    configuration without deserializing it was not that. A stored pattern that
    cannot be reconstructed into a complete format stops the run
    (:class:`BatesPatternError`) rather than falling through to detection or to
    the unstamped path: silently re-detecting would discard the operator's
    ruling, and silently ignoring it would leave locators unenforced.
    """
    if options.bates_decision is not None:
        return options.bates_decision
    stored = _stored_pattern(config, options, warnings)
    if stored is not None:
        pattern, source = stored
        fmt = parse_pattern(pattern)
        if fmt is None:
            raise BatesPatternError(
                f"The Bates format confirmed for this matter cannot be read "
                f"back. {source} carries bates_pattern={pattern!r}, which is "
                "not a complete DocIQ Bates pattern — it does not carry the "
                "'(?#dociq-bates:1;...)' token that records the prefix, "
                "separator, allowed digit widths, suffix separator and suffix. "
                "DocIQ will not run on a confirmed format it cannot enforce, "
                "because a partly enforced format writes locators that are not "
                "in the production. Re-confirm the Bates format for this "
                "document set (Stage 3), or clear bates_pattern to run the "
                "matter as unstamped."
            )
        warnings.append(
            f"Bates format {fmt.label} was applied from the confirmation "
            f"already stored in {source}; §4 Stage 3's confirmation happened in "
            "an earlier run, not this one."
        )
        return BatesDecision(
            DecisionStatus.CONFIRMED,
            fmt,
            f"stored confirmation ({source})",
            None,
        )
    proposal = propose_format(documents)
    if proposal is None:
        return None
    if options.confirm_bates is not None:
        # D-28's census, and only when there is something to ask about — it is
        # a second full candidate sweep of the corpus and an unstamped matter
        # must not pay for it. `others` is what makes the screen's multi-series
        # sentence TRUE rather than plausible; see the field's docstring.
        census = prefix_census() if prefix_census is not None \
            else matter_prefixes(documents)
        others = tuple(p for p in census if p != proposal.format.prefix)
        # §4 Stage 3, as written: the format is confirmed WITH THE OPERATOR on
        # first detection. The call may raise RunAborted; it is deliberately not
        # caught here, because "the operator walked away" is not a Bates
        # decision and this function's job is to return one. `run` catches it.
        if options.confirm_bates(proposal, others):
            warnings.append(
                f"Bates format {proposal.format.label} was CONFIRMED BY THE "
                f"OPERATOR ({stamp.username}) at §4 Stage 3 of this run, on "
                f"{proposal.coverage_pct}% page coverage "
                f"({proposal.pages_matched} of {proposal.pages_scanned} pages "
                f"across {proposal.documents_matched} document(s))."
            )
            return BatesDecision(
                DecisionStatus.CONFIRMED,
                proposal.format,
                f"operator ({stamp.username})",
                stamp.saved_at,
            )
        # A refusal is a DECISION, and the run has to be able to say so. It is
        # not "no Bates present": DocIQ read the stamps, put them to the
        # operator, and was told not to use them. Recording that as an unstamped
        # matter would erase the ruling and the evidence both.
        warnings.append(
            f"Bates format {proposal.format.label} was DETECTED on "
            f"{proposal.coverage_pct}% of pages and DECLINED by the operator "
            f"({stamp.username}) at §4 Stage 3. No locators were written. This "
            "matter is NOT unstamped — the stamps are on the pages and were "
            "read; they were ruled not to be this production's format."
        )
        return BatesDecision(
            DecisionStatus.REJECTED,
            proposal.format,
            f"operator ({stamp.username})",
            stamp.saved_at,
            note="declined at §4 Stage 3",
        )
    if not options.auto_confirm_bates:
        warnings.append(
            f"Bates format {proposal.format.label} was detected on "
            f"{proposal.coverage_pct}% of pages and is NOT applied: §4 Stage 3 "
            "requires the operator to confirm the format on first detection, and "
            "this run had no operator to ask."
        )
        return BatesDecision(DecisionStatus.PENDING, proposal.format, "", "")
    warnings.append(
        f"Bates format {proposal.format.label} was confirmed AUTOMATICALLY "
        f"({proposal.coverage_pct}% page coverage), not by an expert: this run "
        "was headless. §4 Stage 3's confirmation step did not happen."
    )
    return BatesDecision(
        DecisionStatus.CONFIRMED, proposal.format, "auto-confirmed", stamp.saved_at
    )


def _hashes_of(written: Sequence[Path], layout: OutputLayout) -> dict[str, str]:
    """Hashes of the artifacts THIS run wrote, before the log.

    Written into the log's hashed content so the audit trail carries the
    fingerprint of the deliverables it describes and can be checked against
    ``output_manifest.json`` without trusting either one alone. The log itself is
    absent by construction — it cannot contain its own hash.

    Hashed from an explicit list rather than by globbing the output root. A glob
    would pick up anything the *previous* run left behind, so re-running a matter
    into its own folder would produce a different hashed content section from a
    first run over the same inputs — a determinism break with no bad input
    anywhere, which is the hardest kind to diagnose.
    """
    root = layout.root
    return {
        p.relative_to(root).as_posix(): mf.sha256_file(p)
        for p in sorted(set(written))
        if p.is_file()
    }


_EMITTED_PATTERNS = (
    "clean_text/*.txt",
    "sources.json",
    "document_index.csv",
    "document_index.xlsx",
    "reconciliation.csv",
    "processing_log.json",
    "run_summary.pdf",
    mf.MANIFEST_NAME,
    f"{INCOMPLETE_DIR}/*",
    "upload_package/*",
)
"""What THIS BUILD writes into a matter folder."""

_RETIRED_PATTERNS = (
    # Written by every build up to CONTRACT_VERSION 2.0.0; D-38 deleted the
    # profile system, so nothing emits it now. It stays here because a name this
    # build stopped writing is exactly the name B-8 is about.
    "profile/*.yaml",
)
"""Names DocIQ NO LONGER WRITES and still knows how to remove — tombstones
(D-42).

**This list only ever grows.** Removing an entry re-opens B-8 for that name: the
file goes on sitting in an expert's matter folder, unaccounted for by any
manifest, because the manifest is built over STAGING and has never seen the
destination. `tests/test_emit_atomicity.py` asserts the growth-only property, so
a future deletion here fails rather than quietly stranding a file.

**Why this closes the case that matters and not the general one.** D-32 deleted
the durable inventory of what the last run actually published, so DocIQ cannot
know about a file some *other* tool left, or one written by a build older than
this list. What it can know is what IT used to write — and until D-38 that set
was empty, which is the only reason B-8 stayed theoretical for three sprints.
The tombstone covers every case DocIQ can itself create. Nothing here recovers
the general case, and nothing here pretends to.
"""

_STALE_PATTERNS = _EMITTED_PATTERNS + _RETIRED_PATTERNS
"""Deliverables a re-run replaces.

``doc_ids_issued.json`` is absent because it is this run's *input* to the D-04
renumbering check — deleting it would silence the one warning the re-run case
exists to produce. (It is still replaced: the run stages a new one, and
publication moves it over the old.)

``upload_package/*`` is listed **as its FILES, never as the directory** — and
that distinction is the whole of Codex finding A-8. A plan entry naming a
directory is decided at the top of Stage 5 and acted on after Stage 6, minutes
later on a real corpus, and `publish_staging` then removes whatever the
directory contains — including a file the plan never named. An analyst who saves
`upload_package/cover_letter.docx` while the run is working loses it, and
`stale_outputs_replaced` never says so.

This is exactly the defect that was fixed for ``clean_text`` (finding F-3) and
left standing for its sibling, which D-32's own register entry named and which
the F-3 regression test did not cover because it asserted only on
``clean_text``. Costs an empty ``upload_package/`` shell when a re-run produces
no package — the same cosmetic residue ``clean_text`` already accepts, and the
same trade: a directory nobody named is not the tool's to delete.

The package IS still replaced. :func:`~dociq.emit.handoff.build_upload_package` rebuilds
the package from scratch — but since deliverables are built in staging it
rebuilds it *in staging*, so the destination's copy is no longer touched by the
emit at all. Left unlisted, a re-run that produced fewer documents would leave the previous run's
extra ``upload_package/LI-06999.txt`` sitting in a folder an operator uploads
whole. The claim that the rebuild covers it was true of the old write path and is
withdrawn with it.

``incomplete_run/*`` IS listed: once a complete run has written this folder, the
record of an earlier aborted attempt describes a state the folder is no longer
in, and leaving it would put a "RUN BLOCKED" artifact beside a good output set.

**These names are DELETED, and this list is the only knowledge publication has**
(D-32). Every entry is a name :func:`~dociq.emit.paths.publish_staging` removes
from the matter folder before the staged set is moved in. The set-aside rename
that used to stand between "stale" and "gone" is removed, and so is the durable
inventory of what the last run actually published — so a deliverable an OLDER
DocIQ wrote under a name this list does not match is **left in the matter
folder**, permanently and undetected (Codex review #2, finding B-8, knowingly
reopened). A name added to this list must therefore be treated as permanent:
retiring an output means leaving its pattern here forever, not deleting the
line."""


def _stale_deliverables(
    layout: OutputLayout,
    termination: RunTermination,
) -> tuple[str, ...]:
    """ENUMERATE the previous run's deliverables this run supersedes.

    Split from the removal (which is
    :func:`~dociq.emit.paths.publish_staging`) because the two happen at
    different times: the list has to be in the processing log, and the log is
    written *before* publication. It carries the same required, validated
    ``termination`` argument as the removal always has, so the guard sits on the
    function that decides what gets deleted as well as on the one that deletes
    it.

    **One source, and its limit is the point** (D-32). :data:`_STALE_PATTERNS`
    is what this build knows how to write (:data:`_EMITTED_PATTERNS`) PLUS what
    it used to write and still removes (:data:`_RETIRED_PATTERNS`, D-42).

    The durable inventory of what the last run actually published was part of
    the publication protocol D-32 removed, and went with it. So B-8's general
    case stands: a file some other tool left, or one written by a build older
    than this list, is still not accounted for. What D-42 closes is the case
    DocIQ creates itself — and until D-38 retired ``profile/*.yaml`` that set
    was empty, which is the only reason B-8 stayed theoretical.

    Entries are filtered to what is ON DISK once, here. This function runs at the
    TOP of Stage 5 and the removals it plans happen after Stage 6, which on a
    real matter is minutes — so a name that has since disappeared is skipped by
    publication, and a name that has since appeared is not removed at all. The
    second is the direction that matters: a file an analyst saves into the matter
    folder mid-run at a name no pattern matched is not in this plan and is not
    touched.
    """
    if not termination.publishable:
        raise ContractViolation(
            "refusing to list a previous run's deliverables for replacement for "
            f"a run that ended {termination.status.value}: {termination.reason}"
        )
    # FILES ONLY, recursing into any directory a pattern matches. A plan entry
    # that names a directory is decided here, at the top of Stage 5, and acted
    # on after Stage 6 — minutes later on a real matter — and publication then
    # removes whatever the directory holds AT THAT MOMENT, including a file the
    # plan never named.
    #
    # This is the third generation of one defect. F-3 fixed it for `clean_text`
    # by making that pattern a glob; A-8 found `upload_package` still bare;
    # A-8's second round found that `upload_package/*` matches DIRECTORIES too,
    # so a pre-existing `upload_package/analyst_notes/` was planned whole and
    # recursively deleted. Each fix corrected the pattern list, which is the
    # wrong layer — the patterns are a list someone must keep right, and this
    # loop is the place the property can be made true for every pattern at once.
    #
    # So the invariant is established HERE and cannot be reintroduced by editing
    # `_STALE_PATTERNS`: whatever a pattern matches, the plan names files.
    found: list[str] = []
    for pattern in _STALE_PATTERNS:
        for path in sorted(layout.root.glob(pattern)):
            if path.is_file():
                found.append(path.relative_to(layout.root).as_posix())
            elif path.is_dir():
                found.extend(
                    child.relative_to(layout.root).as_posix()
                    for child in sorted(path.rglob("*")) if child.is_file()
                )
    return tuple(sorted(set(found)))


def _log_reconciliation(
    report: ReconciliationReport | None, *, index_supplied: bool
) -> ReconciliationReport | None:
    """The reconciliation projection the log's hashed ``content`` records.

    **One projection, two callers** (Codex review #2, second fix round, B-7).
    The published path passed ``report if index is not None else None`` and
    :func:`_abort` passed the always-created no-index report, so an ordinary
    matter *without* a master index produced a refused log whose
    ``content.reconciliation`` was an object where the published log's was
    ``null`` — two different hashed identities for the same evidence, and the
    refused log's own ``content_sha256`` therefore disagreed with the
    ``log_content_sha256`` embedded in the manifest it carries. A verifier had
    to choose which of one file's two hashes to believe.

    The rule itself is unchanged and is stated here rather than at two call
    sites: **reconciliation is a fact about the master index**, so a run given
    no index records ``null`` — not an empty report, which would say
    "reconciliation ran and matched nothing".
    """
    return report if index_supplied else None


def _abort(
    *,
    config: RunConfig,
    walked: RunResult,
    walk_notes: walker.RunNotes,
    layout: OutputLayout,
    stamp: OperatorStamp,
    opts: PipelineOptions,
    timings: list[tuple[str, float]],
    result: RunResult | None = None,
    assignment: AssignmentResult | None = None,
    reconciliation: ReconciliationReport | None = None,
    index_supplied: bool = False,
    manifest: mf.Manifest | None = None,
    accounting_report: accounting.AccountingReport | None = None,
    renumbering: Sequence[RenumberWarning] = (),
    drops: Sequence[SectionDropEntry] = (),
    bates_decision: BatesDecision | None = None,
    bates_ranges: dict[tuple[str, str, int], BatesRange] | None = None,
    content_warnings: Sequence[str] | None = None,
    output_hashes: Mapping[str, str] | None = None,
    staged_bundle: LogBundle | None = None,
) -> PipelineOutcome:
    """End a run that did not complete, WITHOUT publishing anything.

    Codex review #1, finding B-1. Everything Stage 5 would do is skipped: no
    publication, no ``clean_text/``, no index, no ``sources.json``, no
    ``processing_log.json``, no ``run_summary.pdf``, no ``output_manifest.json``
    and no issued-ID ledger. Whatever the last COMPLETE run left in this folder
    is exactly as it was, which is the point of the finding.

    The run is not silent, though — an aborted run that leaves no trace is its
    own audit failure. It records itself under ``incomplete_run/``:

    * ``run_status.json`` — the typed terminal status, machine-readable;
    * ``processing_log.json`` — the ordinary log structure over whatever was
      read, so the diagnostic tooling that reads a log can read this one;
    * ``run_summary.pdf`` — the same one-page summary an operator is used to,
      carrying the status banner.

    They live in a sub-directory rather than beside the deliverables so that no
    name an incomplete run writes can collide with a name a complete run wrote.
    A subsequent complete run replaces the directory (``_STALE_PATTERNS`` — it
    is removed with the rest of the superseded set), and the
    manifest classifies it as excluded so it can never make a later, good run
    report an unclassified output.

    **The optional arguments** (Codex review #2, finding B-1). This started as
    the Stage-1 abort and is now also the STAGE-6 REFUSAL, where a run walked
    the whole corpus, built a complete set in staging, and was refused
    publication because its own gates went red. That run knows things a Stage-1
    abort cannot — the assigned identifiers, the reconciliation, the manifest of
    the set it built, the accounting report that condemned it — and blanking
    them would make the quarantined record say "no identifier was issued" about
    a run that issued nine thousand.

    They are overrides on ONE function rather than a second unpublishing path on
    purpose: the whole of Codex review #1's B-1 was that a second publication
    rule is a second chance to get publication wrong. Everything that decides
    *not* to publish returns from here, so ``published=False``,
    ``incomplete_dir``, the quarantined log, the status file and the failing
    ``<run>`` discrepancy are written once and cannot drift between the cases.
    """
    termination = walk_notes.termination
    documents = walked.documents
    warnings = list(walk_notes.messages()) + list(walked.warnings)
    # The list that becomes hashed `content.warnings`. A Stage-1 abort has only
    # the walk's own warnings and passes nothing, so it keeps the line above. A
    # STAGE-6 REFUSAL walked the whole corpus and assigned identifiers, so it
    # hands over the same list the published log records — otherwise the refused
    # log's content is missing the assignment and drop warnings the published
    # one carries, and the two hashes for the same evidence disagree again one
    # field over from B-7's (Codex review #2, second fix round).
    hashed_warnings = (
        list(content_warnings) if content_warnings is not None else warnings
    )

    # Stamped from the SAME termination the outcome carries (round-2 F-1).
    # This construction site took the contract's COMPLETED default, so an
    # aborted run handed a consumer a machine result that contradicted the
    # wrapper around it, the log beside it and the run_status.json under it.
    result = walk_notes.termination.stamp(
        result
        if result is not None
        else RunResult(
            config=config,
            documents=documents,
            unsupported=walked.unsupported,
            warnings=tuple(warnings),
        )
    )

    # The correctness gate fails as well as publication being withheld. Codex
    # offered these as alternatives; doing both means a consumer that only reads
    # `accounting.ok` — the property `PipelineOutcome.ok` used to be derived
    # from on its own — still cannot mistake this for a good run.
    report_acc = (
        accounting_report if accounting_report is not None
        else accounting.check(result)
    )
    report_acc.discrepancies.insert(
        0,
        accounting.Discrepancy(
            "<run>",
            f"run-{termination.status.value}",
            termination.reason
            or f"the run ended {termination.status.value} and published nothing",
        ),
    )

    quarantine = OutputLayout(layout.root / INCOMPLETE_DIR)
    quarantine.root.mkdir(parents=True, exist_ok=True)

    before = estimate_for_texts(p.text for d in documents for p in d.pages)
    after = estimate_for_texts(
        p.text
        for d in documents
        for p in d.pages
        if p.disposition is Disposition.KEEP
    )

    # Everything this run actually established goes into the quarantined log
    # (Codex review #2 fix round, B-5). Before this, `_abort` accepted the
    # assignment, the reconciliation, the manifest and the accounting report,
    # returned all four on the in-memory outcome, and passed NONE of them here —
    # so `incomplete_run/processing_log.json`, the durable record the handoff
    # said preserves the diagnosis, serialized `doc_ids.assignments` as `[]` and
    # `reconciliation` as `null` for a run that had assigned an identifier to
    # every document, and carried no trace of the discrepancies that refused it.
    #
    # The split across the two sections is the determinism rule, not a
    # preference: the assignment, the reconciliation, the drops, the profiles
    # and the Bates section are facts about the INPUTS and belong in hashed
    # `content`; the gate outcome, the manifest of the discarded staging set and
    # the wall clock are facts about this INVOCATION and are handed to
    # `build_log` as `run`-section arguments. A refused run and an unrefused run
    # over the same corpus must still agree on `content_sha256`.
    #
    # B-7 (second fix round) added three arguments to this call, and all three
    # are here for one property: the quarantined log's own `content_sha256` must
    # equal the `run.output_manifest.log_content_sha256` it carries. That
    # manifest was built over the STAGED log — the published-style one this run
    # wrote into `.dociq/staging/` before Stage 6 refused it — so any field on
    # which this rebuild disagrees with that one gives a single durable audit
    # file two identities for its own hashed content. `reconciliation` went
    # through `_log_reconciliation` (the projection the published path uses),
    # `warnings` became the published list rather than the walk's subset, and
    # `output_hashes` — the hashes of the set this run actually built — stopped
    # being blanked to `{}`.
    run_notes: dict[str, object] = {
        **termination.as_jsonable(),
        "published": False,
        "deliverables_note": (
            "This run wrote NO deliverables. The files in the parent folder, "
            "if any, belong to the last run that completed."
        ),
        "load_dependent_extraction": list(walk_notes.load_dependent),
        "invocation_notes": list(walk_notes.invocation),
    }
    def _build() -> LogBundle:
        return build_log(
            config,
            documents,
            unsupported=walked.unsupported,
            assignment=assignment,
            reconciliation=_log_reconciliation(
                reconciliation, index_supplied=index_supplied),
            renumbering=renumbering,
            drops=drops,
            bates_decision=bates_decision,
            bates_ranges=bates_ranges,
            token_estimate=after,
            warnings=hashed_warnings,
            stamp=stamp,
            accounting_report=report_acc,
            manifest=manifest,
            timings_s=timings,
            output_hashes=output_hashes,
            run_notes=run_notes,
        )

    bundle = _build()
    # The property above, ENFORCED rather than asserted in prose. The rebuild is
    # a genuinely independent computation from the same inputs, so this compares
    # two answers rather than an answer with itself; and where they disagree the
    # STAGED content wins, because that is the one the embedded manifest hash was
    # taken over and an audit file must not carry two hashes for one section.
    #
    # A disagreement is a defect, and it is DISCLOSED rather than raised: this
    # code path is already handling a refused run, and turning a projection drift
    # into a traceback would replace an operator's refusal report with a crash.
    # It surfaces as a named `<run>` discrepancy and a run note listing the keys
    # — and it is disclosed by REBUILDING, because `build_log` copies both the
    # notes and the accounting report at call time, so a discrepancy appended
    # afterwards would reach the in-memory outcome and never reach the file.
    # That is the B-5 class, and it is not being reintroduced by its own fix.
    if staged_bundle is not None and bundle.content != staged_bundle.content:
        drifted = sorted(
            k for k in set(bundle.content) | set(staged_bundle.content)
            if bundle.content.get(k) != staged_bundle.content.get(k)
        )
        run_notes["log_content_projection_drift"] = drifted
        report_acc.discrepancies.insert(
            1,
            accounting.Discrepancy(
                "<run>",
                "log-content-projection-drift",
                "the quarantined log's hashed content was rebuilt from the same "
                "inputs as the staged log and disagreed on: "
                + ", ".join(drifted)
                + ". The staged projection is recorded, because the manifest's "
                "log_content_sha256 was taken over it — but the two projections "
                "must agree and this run proves they do not.",
            ),
        )
        rebuilt = _build()
        bundle = LogBundle(
            run=rebuilt.run,
            content=staged_bundle.content,
            content_sha256=staged_bundle.content_sha256,
        )
    write_processing_log(bundle, quarantine)

    (quarantine.root / STATUS_FILENAME).write_text(
        json.dumps(
            {
                **termination.as_jsonable(),
                "headline": termination.headline(),
                "generated_at": stamp.saved_at,
                "operator": stamp.username,
                "source_root": config.source_root,
                "output_root": config.output_root,
                "documents_read": len(documents),
                "unsupported_inventoried": len(walked.unsupported),
                "pages_read": result.pages_in,
                "warnings": warnings,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if opts.write_summary_pdf:
        write_run_summary(
            build_summary_data(
                matter_name=opts.matter_name,
                source_root=config.source_root,
                output_root=config.output_root,
                generated_at=stamp.saved_at,
                operator=stamp.username,
                documents=documents,
                unsupported=walked.unsupported,
                tokens_before=before,
                tokens_after=after,
                ocr_threshold_pct=config.ocr_conf_threshold_pct,
                id_regime=config.id_regime.value,
                bates_note="",
                warnings=tuple(warnings),
                termination=termination,
            ),
            quarantine,
        )

    return PipelineOutcome(
        result=result,
        layout=layout,
        accounting=report_acc,
        manifest=manifest if manifest is not None else mf.Manifest(),
        assignment=assignment if assignment is not None else AssignmentResult(
            documents=documents,
            regime=IdRegime.NATIVE,
            assignments=(),
            alignment=None,
            matched_rows=(),
            unmatched_row_count=0,
            warnings=(f"no identifier was issued: the run ended "
                      f"{termination.status.value}",),
        ),
        reconciliation=(
            reconciliation if reconciliation is not None else ReconciliationReport(
                matched=(),
                folder_only=(),
                index_only=(),
                snapshot=None,
                root_prefix=None,
                warnings=(f"reconciliation was not run: the run ended "
                          f"{termination.status.value}",),
            )
        ),
        log=bundle,
        walk_notes=walk_notes,
        termination=termination,
        published=False,
        incomplete_dir=quarantine.root,
        timings_s=tuple(timings),
    )


def _refuse_publication(
    *,
    effective: RunConfig,
    result: RunResult,
    walk_notes: walker.RunNotes,
    layout: OutputLayout,
    stamp: OperatorStamp,
    opts: PipelineOptions,
    timings: list[tuple[str, float]],
    assignment: AssignmentResult,
    reconciliation: ReconciliationReport,
    index_supplied: bool,
    report_acc: accounting.AccountingReport,
    manifest: mf.Manifest,
    refusals: list[tuple[str, str]],
    content_warnings: Sequence[str],
    output_hashes: Mapping[str, str],
    staged_bundle: LogBundle,
    renumbering: Sequence[RenumberWarning] = (),
    drops: Sequence[SectionDropEntry] = (),
    bates_decision: BatesDecision | None = None,
    bates_ranges: dict[tuple[str, str, int], BatesRange] | None = None,
) -> PipelineOutcome:
    """Stage 6 went red: REFUSE to publish (Codex review #2, finding B-1).

    Three things happen and their order is the guarantee, exactly as it is on
    the success side:

    1. **Staging is discarded.** It is a set that failed its own audit, and
       leaving it under a matter folder is leaving a set that failed its audit
       where a later hand could find it. Nothing in the destination is touched:
       :func:`~dociq.emit.paths.publish_staging` is a direct call that this path
       never makes, so the previous run's deliverables stay byte-for-byte as
       they were. **This is the constraint that kept staging alive through
       D-32** — the gate audits the staged set, so a red gate costs the operator
       a run and never an evidence set.
    2. **The refusal becomes the terminal status**, so every consumer that
       already knows how to read one — the log's ``run`` section, the GUI's
       failure state, ``run_status.json``, the summary banner — sees it without
       learning a new vocabulary.
    3. **The record is written under** ``incomplete_run/``, through the same
       :func:`_abort` that every other unpublished run goes through.

    **On the status value.** Recorded as ``REFUSED`` (amendment **A-15**,
    applied by the seam owner 2026-08-04). This shipped briefly as ``BLOCKED``,
    which was honest but wrong in one direction that matters: a blocked run
    never established a corpus, and a refused run established one and assigned
    an identifier to every document before failing its own gate. Reporting "the
    run never established a corpus it could publish" about a run that issued a
    Doc ID per document says something false about the folder.

    The alternative considered and rejected was leaving the termination
    ``COMPLETED`` and carrying the refusal only in ``published=False`` — which
    would have printed "Run status: completed — the walk covered every file
    found." at the top of a refused run's ``run_status.json``.
    """
    emit_paths.discard_staging(layout)

    detail = "; ".join(why for _, why in refusals)
    walk_notes.termination = RunTermination(
        TerminalStatus.REFUSED,
        f"§4 Stage 6 REFUSED to publish this run: {detail}. The set this run "
        f"built was discarded and the deliverables already in this folder were "
        f"not touched.",
    )
    walk_notes.invocation.append(
        "PUBLICATION REFUSED at §4 Stage 6 — the run completed its walk and "
        "built a full set, and that set failed the Stage-6 gates: "
        + ", ".join(kind for kind, _ in refusals)
    )
    # Each failing gate is its own `<run>` discrepancy, so a reader of the
    # accounting report alone learns which gate refused rather than only that
    # something did. `_abort` inserts the terminal-status line above these.
    for kind, why in reversed(refusals):
        report_acc.discrepancies.insert(
            0, accounting.Discrepancy("<run>", f"refused-{kind}", why))

    return _abort(
        config=effective,
        walked=result,
        walk_notes=walk_notes,
        layout=layout,
        stamp=stamp,
        opts=opts,
        timings=timings,
        result=result,
        assignment=assignment,
        reconciliation=reconciliation,
        index_supplied=index_supplied,
        manifest=manifest,
        accounting_report=report_acc,
        renumbering=renumbering,
        drops=drops,
        bates_decision=bates_decision,
        bates_ranges=bates_ranges,
        # The three that make the quarantined log's own hash agree with the
        # manifest hash it carries, and the staged bundle they are checked
        # against (B-7). See the note at the `build_log` call in `_abort`.
        content_warnings=content_warnings,
        output_hashes=output_hashes,
        staged_bundle=staged_bundle,
    )


def run(config: RunConfig, options: PipelineOptions | None = None) -> PipelineOutcome:
    """Run §4's six stages over ``config.source_root`` and write §7's outputs."""
    opts = options or PipelineOptions()
    stamp = opts.stamp or operator_stamp()
    layout = OutputLayout.at(config.output_root).ensure()
    timings: list[tuple[str, float]] = []

    def mark(stage: str, t0: float) -> None:
        timings.append((stage, round(time.monotonic() - t0, 3)))

    def stage(number: int, detail: str = "") -> None:
        if opts.on_stage is not None:
            opts.on_stage(StageProgress(number, dict(STAGES)[number], detail))

    # What an EARLIER run left under `.dociq/`, measured before this run touches
    # the folder — which is the only moment it can be measured, because the next
    # two statements create this run's own staging tree.
    #
    # A surviving `.dociq/staging/` is the on-disk signature of D-32's accepted
    # window: a run that died or was cancelled between building its set and
    # publishing it. It is disclosed in the log rather than silently discarded,
    # because the run that left it wrote no record of itself.
    #
    # There is no roll-forward, no marker to read, and no folder state that can
    # block a run. D-32 removed all three; see `emit.paths` for what that costs.
    residue_before = emit_paths.state_residue(layout)
    emit_paths.discard_staging(layout)

    index = opts.master_index
    if index is None and opts.master_index_path:
        index = load_master_index(opts.master_index_path)

    # ---- The effective configuration, built BEFORE the walk ----------------
    #
    # Codex review #1 round 2, F-4a. This used to be assembled *after* the walk
    # returned, and the walk was handed the caller's original ``config``. The
    # walk is where the resume journal is written and matched, so the journal
    # was keyed on a configuration that did not yet know its own limits, its
    # own OCR engine, or its master index — while the deliverables recorded the
    # completed one. A record cached with OCR disabled, or under different
    # caps, or read by different OCR model bytes, satisfied that key and was
    # replayed into a run whose manifest then honestly hashed the *new*
    # settings. The documents were not produced under the configuration the
    # deliverable claims they were.
    #
    # Everything knowable before Stage 1 is therefore fixed before Stage 1.
    # ``bates_pattern`` is the sole exception and cannot be otherwise — it is
    # Stage 3's output — and it is sound to leave out: Bates is applied to
    # already-extracted pages, so it cannot change what the walk produced or
    # what a replayed record would have been.
    ocr_ran = (opts.walk or walker.WalkOptions()).ocr_enabled
    walk_config = replace(
        config,
        # ``WalkOptions.ocr_enabled`` is not part of RunConfig, and RunConfig's
        # own docstring says anything influencing output and absent from it is
        # a determinism bug — which this was. Measured on the real corpus: the
        # same folder, the same (identical) RunConfig, OCR on and off, produced
        # 400 OCR pages versus 400 more EMPTY pages and a different hashed
        # content section, while the recorded configuration claimed both runs
        # used rapidocr 1.2.3. The engine fields are the contract's own place
        # to say this, so the run stamps what it actually did rather than what
        # it was configured with.
        #
        # The same argument extends to everything A-04 added (finding B-2): the
        # XLSX/CSV/ZIP caps, the ZIP depth, the per-file timeout, the retry
        # bounds, whether the walk recursed, and the bytes of the OCR model.
        # They are captured from the modules that own them, so the recorded
        # identity is what the run used rather than a restatement that can go
        # stale.
        limits=walker.effective_limits(opts.walk, ocr_enabled=ocr_ran),
        ocr_engine=config.ocr_engine if ocr_ran else OCR_DISABLED,
        ocr_engine_version=config.ocr_engine_version if ocr_ran else "",
        master_index=index.snapshot if index else config.master_index,
    )

    # ---- Stages 1-2 --------------------------------------------------------
    t = time.monotonic()
    walk_notes = walker.RunNotes()
    stage(1)
    walked = walker.run(walk_config, opts.walk, walk_notes)
    stage(2, f"{len(walked.documents)} document(s) read")
    mark("walk_extract", t)

    # Codex B-1. A blocked or cancelled walk stops HERE, before Stage 3 and
    # therefore long before Stage 5. Every later stage — assignment,
    # emission, accounting, the manifest, publication — is unreachable for a run
    # that did not complete, so none of them needs to know about the case, and
    # none of them can be the place a future edit reintroduces it.
    if not walk_notes.termination.publishable:
        return _abort(
            # The effective configuration, not the caller's — an aborted run
            # must record the settings it was going to run under, or its
            # diagnostic log describes a different run than the one that failed.
            config=walk_config,
            walked=walked,
            walk_notes=walk_notes,
            layout=layout,
            stamp=stamp,
            opts=opts,
            timings=timings,
        )

    warnings: list[str] = list(walked.warnings)
    documents: tuple[DocumentRecord, ...] = walked.documents

    # ---- Stage 3 -----------------------------------------------------------
    stage(3)
    t = time.monotonic()
    # D-28's census, computed AT MOST ONCE per run and shared by the two places
    # that need it — the confirmation prompt and the repair gate. It is a full
    # candidate sweep of the corpus; two of them on an 18,000-page record is a
    # measurable cost paid for nothing, and two INDEPENDENT ones would be two
    # answers to "is this matter multi-series?" that could disagree.
    _census: list[tuple[str, ...]] = []

    def census() -> tuple[str, ...]:
        if not _census:
            _census.append(matter_prefixes(documents))
        return _census[0]

    try:
        decision = _bates_decision(documents, opts, config, stamp, warnings,
                                   census)
    except RunAborted as aborted:
        # The ONE place the pipeline blocks on a human, and therefore the one
        # place a cancellation cannot be a poll. It takes the ordinary abort
        # path — nothing published, the previous run's deliverables untouched,
        # incomplete_run/ written — rather than a new one: a run abandoned at
        # Stage 3 is not a different kind of aborted run from one abandoned at
        # Stage 1, and a second publication rule is a second chance to get
        # publication wrong (Codex B-1).
        walk_notes.termination = RunTermination(
            TerminalStatus.CANCELLED,
            f"the run was stopped at §4 Stage 3, while the Bates format was "
            f"waiting to be confirmed: {aborted.reason}",
        )
        walk_notes.invocation.append(
            "CANCELLED at the Bates confirmation: the format was detected and "
            "no ruling was made on it."
        )
        return _abort(
            config=walk_config,
            walked=walked,
            walk_notes=walk_notes,
            layout=layout,
            stamp=stamp,
            opts=opts,
            timings=timings,
        )
    applied = apply_bates_reported(
        documents, decision,
        # The census the prompt was answered against, when there was a prompt.
        # `apply_bates_reported` computes its own when handed None, and that is
        # still the right default — but a run that already asked the operator
        # must gate the repair on the SAME census it showed them.
        matter_prefix_census=_census[0] if _census else None)
    documents = applied.documents
    # D-28's repair is disclosed, never silent. §4 requires misses to be
    # flagged and never quietly corrected; prefix repair is a narrow ruled
    # exception to that, so the run says how many locators it repaired and the
    # refusal — when the matter carries more than one prefix — is a warning in
    # its own right rather than an absence an operator has to notice.
    if applied.normalized:
        rules = Counter(n.rule for n in applied.normalized)
        warnings.append(
            f"{len(applied.normalized)} Bates locator(s) were REPAIRED under "
            f"D-28: the page's own reading differed from the confirmed format "
            f"in the prefix alone, by "
            + "; ".join(f"{r} ({n})" for r, n in sorted(rules.items()))
            + f". Examples: "
            + ", ".join(f"{n.read} -> {n.applied}"
                        for n in applied.normalized[:3])
        )
    elif applied.refused_reason and len(applied.matter_prefixes) > 1:
        warnings.append(applied.refused_reason)
    ranges = document_ranges(documents)
    mark("bates", t)

    # ---- Stage 3b ----------------------------------------------------------
    #
    # ONE INVENTORY, not two (Codex review #1, finding B-7). Stage 3b used to
    # see only the extracted documents, so a Tier-2 file kept an empty Doc ID
    # and never reached ``build_index_rows`` — yet §5 lists ``Unsupported`` as a
    # required Processing status *of the document index*, and the GUI tells the
    # operator unsupported files are recorded there. On the real corpus the
    # seven legacy ``.doc`` files were counted in the log and the summary and
    # were absent from the first-class deliverable.
    #
    # They are assigned together rather than in a second pass because that is
    # what makes acceptance criterion 5 hold by construction: one
    # :class:`~dociq.docid.ids.DocIdMinter` sees every identifier this run
    # issues, so LI and DIQ cannot collide with each other or with themselves.
    # Two passes would mean two minters and two DIQ counters, and the second
    # pass would have to be told which numbers the first had already used —
    # exactly the bookkeeping the minter exists to remove.
    #
    # A Tier-2 file with a master-index row now MATCHES it and takes its LI
    # identifier, which is also the right answer for reconciliation: the file is
    # in the folder and in the index, and reporting it as index-only was a false
    # gap. Container children that were Tier-2 (a .dwg inside a .zip) pick up a
    # parent-derived identifier for the first time.
    t = time.monotonic()
    inventory = tuple(documents) + tuple(walked.unsupported)
    assignment = assign_doc_ids(
        inventory, index, bates_ranges=ranges_by_sort_key(ranges)
    )
    # Split back on status, which is the exact discriminator: the walker has
    # already moved every UNSUPPORTED record — including Tier-2 archive members
    # — onto its unsupported list, so no record can be on the wrong side here.
    documents = tuple(
        d for d in assignment.documents
        if d.status is not ProcessingStatus.UNSUPPORTED
    )
    unsupported = tuple(
        d for d in assignment.documents
        if d.status is ProcessingStatus.UNSUPPORTED
    )
    if len(documents) + len(unsupported) != len(inventory):
        raise ContractViolation(  # pragma: no cover — guards a refactor
            f"Stage 3b lost inventory: {len(inventory)} in, "
            f"{len(documents)} documents + {len(unsupported)} unsupported out"
        )
    warnings.extend(assignment.warnings)
    report = reconcile(assignment, index, bates_ranges=ranges)
    mark("assign_reconcile", t)

    # ---- Stage 4 -----------------------------------------------------------
    #
    # Recognition already happened, at Stage 2, where the file was open: every
    # page a tier placed arrived here carrying its section and its tier. What is
    # left is the DISPOSITION, and it happens at this point in the order for one
    # reason — a drop-log entry is written against a Doc ID, and Doc IDs are
    # issued at 3b.
    #
    # The spans are rebuilt from the pages rather than carried in a side channel
    # (:func:`~dociq.sections.resolve.spans_from_pages`), which is what makes a
    # resumed run behave like a fresh one: replayed records come back stamped, so
    # they rebuild the same spans and reach the same dispositions.
    #
    # **Nothing drops without an approval that names a person (D-34).** With no
    # approvals this loop stamps nothing new and returns every document
    # unchanged — the recognition-only state a freshly-installed DocIQ is in.
    stage(4)
    t = time.monotonic()
    classified: list[DocumentRecord] = []
    section_drops: list[SectionDropEntry] = []
    for doc in documents:
        spans = spans_from_pages(
            doc.pages, project_tokens=walk_config.project_tokens
        )
        # NOT named `applied`: that name is already bound, twenty lines up, to
        # the Bates result, and two later call sites read it.
        stage4 = apply_sections(
            doc, spans, template=opts.template, approvals=opts.approvals,
            # The run's own tokens, so an approval reviewed under a different
            # set is refused rather than silently widened (Codex B-1).
            project_tokens=walk_config.project_tokens,
            # The matter this run is FOR, so Stage 4 can refuse an approval
            # given on a different one. opts.matter_name is what the adapter
            # derives from the source folder and what the drop log records.
            # The SOURCE ROOT, not the matter's display name. `matter_name`
            # is a label and two clients' `Production` folders share one
            # (Codex r2, B-2).
            matter_root=walk_config.source_root,
        )
        classified.extend(stage4.documents)
        section_drops.extend(stage4.drops)
        warnings.extend(stage4.warnings)
    if len(classified) != len(documents):
        raise ContractViolation(  # pragma: no cover — guards a refactor
            f"Stage 4 changed the document count: {len(documents)} in, "
            f"{len(classified)} out"
        )
    documents = tuple(classified)
    drops = tuple(section_drops)
    mark("classify", t)

    # The config the deliverables record is the one the walk actually ran
    # under, plus the one thing that could not be known before Stage 3: the
    # confirmed Bates pattern. Everything else was fixed before Stage 1 (F-4a,
    # above), which is what makes the resume key and this identity the same
    # configuration rather than two configurations that usually agree.
    effective = replace(
        walk_config,
        # Only a decision that was actually APPLIED is persisted. Recording the
        # pattern of a pending or rejected decision would make the next run
        # load it as a confirmation — the operator's "not yet" silently
        # promoted to "yes" by a re-run — and an explicit rejection has to be
        # able to clear a stored pattern, or it could never be undone.
        bates_pattern=(
            decision.pattern()
            if decision is not None and decision.applies
            else (None if decision is not None else config.bates_pattern)
        ),
        # The profile LIBRARY this run was driven by. Resolved here rather than
        # into `walk_config` on purpose — see the note at the pre-walk block:
        # stamping it onto records at walk time would label documents no
        # profile claimed. What the run was configured with, and what matched a
        # given document, are two different facts and only Stage 4 knows the
        # second.
        # A-19. The approvals are the input that decides which pages dropped, so
        # they are the input the identity has to cover — the same finding A-08
        # made about profiles, on the mechanism that replaced them.
        #
        # Built HERE rather than into `walk_config`, and the split is the same
        # one the Bates pattern makes: an approval cannot change what the walk
        # produced. Recognition happens at Stage 2 and is a property of the
        # document; approval happens at Stage 4 and is a property of the ruling.
        # A journal replayed under a different approval set is still a correct
        # journal, and keying the resume on approvals would throw away an
        # extraction every time an expert changed his mind about a section.
        omissions=tuple(
            OmissionSnapshot(
                family_id=a.family_id,
                approved_by=a.approved_by,
                approved_at=a.approved_at,
                matter=a.matter,
                matter_root=a.matter_root,
                template_id=a.template_id,
                template_version=a.template_version,
            )
            for a in opts.approvals
        ),
        # Recorded even when nothing was approved: "the expert engaged nothing"
        # and "no template was offered" are different facts about a run.
        section_template_id=(
            opts.template.template_id if opts.template else config.section_template_id
        ),
        section_template_version=(
            opts.template.version if opts.template
            else config.section_template_version
        ),
    )

    # D-04 (b): the renumbering check runs BEFORE the result is assembled, not
    # in the emit block where the ledger is written. Its warnings have to reach
    # `RunResult.warnings` as well as the log — an operator reading the summary
    # screen and an auditor reading the log must not be told different things
    # about whether identifiers moved.
    #
    # They are kept OUT of the list handed to the log's hashed content, and go
    # into its `run` section instead: the comparison is against a ledger the
    # destination folder happens to hold, and the destination is not one of the
    # determinism contract's inputs.
    ledger_path = Path(opts.previous_ledger) if opts.previous_ledger else layout.issued_ids
    previous = IssuedIdLedger.read(ledger_path) if ledger_path.is_file() else None
    ledger = IssuedIdLedger.from_assignment(assignment, effective.master_index)
    renumbering = detect_renumbering(previous, ledger)
    # Stage 1's invocation notes travel the same road as the renumbering
    # warnings, and for the same reason. A serial-retry disclosure, a resume
    # note and a cancellation note are all facts about THIS invocation: two
    # runs over byte-identical inputs can legitimately differ in them, so they
    # are visible to the operator (here, and in the log's `run` section, and in
    # the summary) and invisible to the hash. Putting them in `warnings` — the
    # list that becomes hashed `content` — would make a run that needed a retry
    # produce different bytes from a run that did not, which is the very defect
    # the retry exists to remove.
    #
    # They go FIRST, not last. The run summary renders the first four warnings
    # and folds the rest into a count, so appending a "this document failed
    # under load and was re-read" disclosure to the end of a list of 300 would
    # satisfy the letter of "recorded" and none of the point.
    all_warnings = (walk_notes.messages() + warnings
                    + [w.message for w in renumbering])

    # ---- Stage 6 (measure first — the log records the numbers) -------------
    t = time.monotonic()
    before = estimate_for_texts(p.text for d in documents for p in d.pages)
    after = estimate_for_texts(
        p.text
        for d in documents
        for p in d.pages
        if p.disposition is Disposition.KEEP
    )
    mark("tokens", t)

    # Stamped rather than left to the COMPLETED default, even though this line
    # is only reachable for a complete run. The default is what made the three
    # abort sites wrong silently; a construction site that states its status is
    # one a future early return cannot quietly join.
    result = walk_notes.termination.stamp(
        RunResult(
            config=effective,
            documents=documents,
            unsupported=unsupported,
            warnings=tuple(all_warnings),
            tokens_before=_to_contract_estimate(before, "before reduction"),
            tokens_after=_to_contract_estimate(after, "after reduction"),
            reconciliation=(
                _to_contract_reconciliation(report) if index is not None else None
            ),
        )
    )

    # ---- Stage 5 -----------------------------------------------------------
    #
    # EVERYTHING below is written into `stage_out`, a staging directory inside
    # the matter folder, and moved into place in one go at the end (Sprint-1
    # merge readiness, NOT PROVEN item 8). The previous shape — purge the
    # destination, then emit into it — meant that a crash anywhere in emit left
    # a folder holding some of the old run's deliverables and some of the new
    # one's, under Doc IDs that need not agree, with nothing on disk saying so.
    #
    # The staging path is NOT part of any hashed artifact and must never become
    # one. `_hashes_of` keys on paths relative to the layout root, `sources.json`
    # stores paths relative to the layout root, and the recorded configuration
    # keeps `output_root` pointing at the DESTINATION — a run staged at a
    # different path is not a different run. This is the same class of mistake as
    # the output root inside the log's hashed content in Sprint 1, and
    # `tests/test_emit_atomicity.py` proves byte-identity across two
    # destinations, which is the only check that would catch it.
    stage(5)
    t = time.monotonic()
    stale = _stale_deliverables(layout, walk_notes.termination)
    stage_out = emit_paths.staging_layout(layout)
    written: list[Path] = []
    # Clean text is for EXTRACTED documents only. An unsupported file has no
    # text to write and must not appear in ``sources.json``, which is the map
    # Expert Assist reads to find a document's content — a Doc ID pointing at a
    # file that was never read would be worse than its absence. It appears in
    # the index instead, which is the inventory (B-7).
    text_result = write_clean_text(documents, stage_out)
    written.extend(stage_out.root / rel for _, rel in text_result.sources)
    written.append(write_sources_json(text_result, stage_out))
    rows = build_index_rows(documents + unsupported, bates_ranges=ranges,
                            drops=drops)
    written.append(write_index_csv(rows, stage_out))
    if index is not None:
        written.append(write_reconciliation_csv(report, stage_out))

    # The ledger is read above, from the DESTINATION, before this line writes the
    # new one into staging: comparing a run against the ledger it just wrote
    # would report that nothing was renumbered, every time, forever.
    written.append(ledger.write(stage_out.issued_ids))

    # Captured in a name because the REFUSAL path needs the same value: a run
    # refused at Stage 6 built this same set and its quarantined log has to
    # record the same `content.output_hashes`, or its own `content_sha256`
    # disagrees with the `log_content_sha256` the manifest took over this very
    # log (B-7).
    staged_hashes = _hashes_of(written, stage_out)
    bundle = build_log(
        effective,
        documents,
        unsupported=unsupported,
        assignment=assignment,
        reconciliation=_log_reconciliation(report, index_supplied=index is not None),
        renumbering=renumbering,
        drops=drops,
        bates_decision=decision,
        bates_ranges=ranges,
        token_estimate=after,
        warnings=warnings,
        stamp=stamp,
        output_hashes=staged_hashes,
        run_notes={
            # The terminal status is recorded on EVERY run, not only on the
            # ones that end badly (Codex B-1). A consumer must be able to ask
            # "did this run complete?" of any log it is handed and get an
            # answer, rather than inferring completion from the absence of a
            # field. It sits in `run`, not in `content`: a cancellation is a
            # fact about this invocation, and hashing it would make an
            # interrupted run and a clean one differ inside the byte-identical
            # claim.
            **walk_notes.termination.as_jsonable(),
            # Recorded, never hashed. Pool widths must not change output; if one
            # ever does, that is a determinism defect to fix rather than a value
            # to absorb into the identity (A-04's note on
            # ``EffectiveLimits.workers``). The disk-headroom multiplier gates
            # whether the run starts rather than what a completed run emits, and
            # it is a float, which Principle 5 bars from identity fields — see
            # ``docs/contracts/amendments.md`` A-05(b) for the disposition.
            "pool": {
                "workers": effective.limits.workers if effective.limits else None,
                "ocr_page_workers": ex._OCR_PAGE_WORKERS,
                "disk_headroom_x100": round(walker._DISK_HEADROOM * 100),
            },
            "stale_outputs_replaced": list(stale),
            # WHAT PUBLICATION KNEW, and what it could not know (D-32). The plan
            # above is this build's own output patterns and nothing else — the
            # durable inventory of what the last run actually published went out
            # with the publication protocol. So a deliverable an older DocIQ
            # wrote at a name this build no longer writes is still in this
            # folder, and no field can name it. Recorded as a standing statement
            # about the design rather than as a per-run finding, because it is
            # true of every run of this build. `run`, not `content`: a fact
            # about this folder's history, not about the evidence.
            "stale_outputs_plan_source": (
                "this build's output patterns only; a deliverable an older "
                "build wrote under a name this build does not write remains in "
                "this folder and is not detected (D-32, reopening B-8)"
            ),
            # What an EARLIER run left under `.dociq/`, measured at the top of
            # this run and discarded immediately afterwards. A `staging` tree
            # here means a previous run died or was cancelled between building
            # its set and publishing it — which under D-32 may also mean that
            # run left this folder holding part of two runs' evidence. Nothing
            # detects that; this is the only trace of it.
            #
            # This run's OWN residue cannot appear here: the log is written into
            # staging and sealed before publication. The operator sees it on
            # screen through `PipelineOutcome.superseded_residue`, and the next
            # run records it here.
            "state_residue_before_run": list(residue_before),
            "load_dependent_extraction": list(walk_notes.load_dependent),
            "invocation_notes": list(walk_notes.invocation),
        },
    )
    write_processing_log(bundle, stage_out)

    if opts.write_workbook:
        write_index_xlsx(
            rows,
            stage_out,
            report if index is not None else None,
            matter_name=opts.matter_name,
        )
    if opts.write_summary_pdf:
        write_run_summary(
            build_summary_data(
                matter_name=opts.matter_name,
                source_root=effective.source_root,
                output_root=effective.output_root,
                generated_at=stamp.saved_at,
                operator=stamp.username,
                documents=documents,
                unsupported=unsupported,
                tokens_before=before,
                tokens_after=after,
                ocr_threshold_pct=effective.ocr_conf_threshold_pct,
                id_regime=effective.id_regime.value,
                master_index=index.snapshot.filename if index else None,
                bates_note=_bates_note(decision, ranges),
                warnings=tuple(all_warnings),
                termination=walk_notes.termination,
            ),
            stage_out,
        )
    if opts.write_package:
        build_upload_package(
            stage_out,
            matter_name=opts.matter_name,
            document_count=len(documents),
            page_count=result.pages_in,
            estimate=after,
            has_bates=any(r.pages_with_bates for r in ranges.values()),
            id_regime=effective.id_regime.value,
            # The run's own package is the whole extracted record, so it takes
            # the whole-record scope statement (D-20). It is stated rather than
            # left implicit precisely because a package with no scope line is
            # indistinguishable from a subset once it has been uploaded — and
            # the §5 listed-only files are named, because they are the one thing
            # a "complete production" claim would silently be wrong about.
            doc_ids=None,
            unsupported=len(unsupported),
        )
    mark("emit", t)

    # ---- Stage 6 (the gates) ----------------------------------------------
    #
    # Run against STAGING, before anything reaches the matter folder. The
    # manifest is a hash of the set this run produced, so building it over the
    # destination would have it hash whatever a previous run left behind that
    # this one does not replace — and running the gates before publication means a
    # set that fails them never displaces a set that passed.
    stage(6)
    t = time.monotonic()
    report_acc = accounting.check(result)
    man = mf.build(stage_out.root, config=effective)
    mf.write(stage_out.root, man)
    # The disagreements, not the boolean. A refusal that cannot name the
    # documents it refused over is the class D-30 came out of.
    disorder = corpus_sort_disagreements(result)
    mark("verify", t)

    # ---- The gate ----------------------------------------------------------
    #
    # Codex review #2, finding B-1. Everything above COMPUTED these checks; only
    # this decides anything with them. Until it existed, Stage 6 detected a
    # page-accounting discrepancy or an unclassified artifact and then marked
    # published it regardless, so a red set replaced the last good deliverables
    # and `PipelineOutcome.ok` reported the fact afterwards to an in-memory
    # caller who need never look. Observation is not a gate. The heading said
    # "the gates" and the relay said the set was "gated there, marked, then
    # swapped"; that claim was false and is withdrawn with the behavior it
    # described.
    #
    # **This gate is why staging survived D-32.** The descope removed the
    # publication protocol and kept the staging directory, because B-1's fix
    # lives here: the audit runs over the staged set, so a failure costs a run
    # rather than an evidence set.
    #
    # `corpus_sort_check` joins them here rather than staying the module-level
    # function nothing called. It was written as a Stage-6 check, was reachable
    # from no code path in the product, and is exactly the same defect one step
    # further along: a check that never runs cannot fail differently from a
    # check whose result is ignored.
    refusals: list[tuple[str, str]] = []
    if not report_acc.ok:
        refusals.append((
            "accounting",
            f"§4 Stage 6's page accounting reports "
            f"{len(report_acc.discrepancies)} discrepancy(ies); the first is "
            f"{report_acc.discrepancies[0]}",
        ))
    if man.unclassified:
        refusals.append((
            "unclassified-output",
            f"the byte-identical claim does not classify "
            f"{len(man.unclassified)} output(s): "
            f"{', '.join(sorted(man.unclassified)[:5])}",
        ))
    if man.log_content_sha256 is None:
        # The class-B sibling, found by enumerating the other places that
        # substitute a permissive default for state they could not read.
        # `manifest._log_content_hash` returns None when `processing_log.json`
        # is missing or unparseable, and `corpus_sha256` folds it in as
        # `self.log_content_sha256 or ""` — so an unreadable log produced a
        # manifest that silently omitted one of the claim's two halves and a
        # corpus hash that could not be told apart from a run whose log hashed
        # to nothing. Stage 5 always writes that file into this very staging
        # directory, so there is no legitimate None here. Measured: without this
        # gate the run publishes AND reports ok=True, because
        # `PipelineOutcome.ok` does not look at the field either.
        refusals.append((
            "log-content-hash",
            f"{mf.LOG_NAME}'s '{mf.LOG_CONTENT_KEY}' section could not be "
            f"hashed, so the byte-identical claim would be published missing "
            f"half of what it claims",
        ))
    if disorder:
        refusals.append((
            "corpus-order",
            "the corpus is not in canonical order, so the run's identity and "
            "its Doc ID assignment do not agree on the sequence of documents: "
            + "; ".join(disorder),
        ))
    if refusals:
        return _refuse_publication(
            effective=effective,
            result=result,
            walk_notes=walk_notes,
            layout=layout,
            stamp=stamp,
            opts=opts,
            timings=timings,
            assignment=assignment,
            reconciliation=report,
            index_supplied=index is not None,
            report_acc=report_acc,
            manifest=man,
            refusals=refusals,
            # The three that keep the quarantined log's hashed `content` equal
            # to the STAGED log's — the one `man.log_content_sha256` was taken
            # over — so the refused record does not give two identities for its
            # own content (B-7).
            content_warnings=warnings,
            output_hashes=staged_hashes,
            staged_bundle=bundle,
            # Everything else Stage 6 had in hand. A refused run walked the
            # whole corpus: it has the renumbering comparison, the drop log, the
            # Bates decision and its per-document ranges, and leaving them at
            # this call site would put them in the same class as the assignment
            # and the reconciliation B-5 found — established by the run, present
            # in memory, absent from the record that outlives it.
            renumbering=renumbering,
            drops=drops,
            bates_decision=decision,
            bates_ranges=ranges,
        )

    # ---- Publication -------------------------------------------------------
    #
    # ONE statement, reached only by a set that PASSED the gate above.
    #
    # `publish_staging` removes the deliverables named in `stale` and then moves
    # the staged set into place. A crash before this line leaves the matter
    # folder exactly as the last complete run left it. A crash INSIDE it leaves
    # the folder holding part of the previous run's evidence and part of this
    # one's, permanently and undetected — that is D-32's accepted window, and it
    # is stated at full width on `emit.paths.publish_staging` rather than here,
    # so that the one place it is described is the place a reader changing the
    # code will be standing.
    #
    # `PublicationFailed` is NOT caught. Its message says what state the folder
    # is in and where the complete staged set still is; converting it into a
    # refused run would file a mixed matter folder under a status that means
    # "nothing was touched".
    publication = emit_paths.publish_staging(layout, stale)
    residue = publication.residue

    return PipelineOutcome(
        result=result,
        layout=layout,
        accounting=report_acc,
        manifest=man,
        assignment=assignment,
        reconciliation=report,
        log=bundle,
        walk_notes=walk_notes,
        renumbering=renumbering,
        stale_removed=stale,
        superseded_residue=residue,
        bates_ranges=ranges,
        timings_s=tuple(timings),
        termination=walk_notes.termination,
        published=True,
    )


def _bates_note(
    decision: BatesDecision | None,
    ranges: dict[tuple[str, str, int], BatesRange],
) -> str:
    stamped = sum(r.pages_with_bates for r in ranges.values())
    if decision is None:
        return "No Bates stamps detected — absence is normal (§4 Stage 3)."
    if decision.status is DecisionStatus.REJECTED:
        # NOT the same sentence as PENDING. "Not yet confirmed" and "the
        # operator declined it" are different facts about the record, and the
        # summary an expert forwards must not blur them into one.
        label = decision.format.label if decision.format else "the format"
        return (
            f"A Bates format ({label}) was detected and DECLINED by the "
            "operator (§4 Stage 3); no locators were written."
        )
    if not decision.applies:
        return (
            "A Bates format was detected but not applied: the operator has not "
            "confirmed it (§4 Stage 3)."
        )
    return f"{decision.pattern()} — {stamped} page(s) stamped."


def ocr_page_count(result: RunResult) -> int:
    """Pages the OCR engine read. Used by the §10 restatement, which has to
    separate OCR cost from extraction cost rather than quoting one number."""
    return sum(
        1
        for d in result.documents
        for p in d.pages
        if p.kind is PageKind.OCR
    )


def corpus_sort_disagreements(result: RunResult) -> tuple[str, ...]:
    """WHERE the corpus departs from canonical order — not merely that it does.

    Found by enumerating the class D-30 came out of: a probe that reports a
    tally, a boolean or a status and discards the evidence behind it. This one
    returned a bare ``bool``, and it gates PUBLICATION — so a run could be
    refused with "the corpus is not in canonical order" over nine thousand
    documents and nothing on disk saying which two were the wrong way round.
    "Accounting failed is unactionable at 9,000 documents" is the reasoning
    :mod:`dociq.verify.accounting` was built on; this check was the same shape
    and had not had it applied.

    Each entry names the position, the Doc ID found there, and the Doc ID
    canonical order puts there. Capped at the first ten with the total stated,
    because a fully reversed corpus would otherwise produce a discrepancy per
    document — and the cap is disclosed in the text rather than being a silent
    truncation.
    """
    ordered = sorted(result.documents, key=document_sort_key)
    out: list[str] = []
    total = 0
    for i, (found, want) in enumerate(zip(result.documents, ordered)):
        if found is want:
            continue
        total += 1
        if len(out) < 10:
            out.append(
                f"position {i}: {found.doc_id or found.rel_path!r} is here, "
                f"canonical order puts {want.doc_id or want.rel_path!r}")
    if total > len(out):
        out.append(f"... and {total - len(out)} further position(s) "
                   f"({total} in all)")
    return tuple(out)


def corpus_sort_check(result: RunResult) -> bool:
    """Documents are in canonical order. Cheap, and it catches an emitter that
    sorted its own way.

    Kept as the boolean the gate reads; :func:`corpus_sort_disagreements` is
    the evidence the refusal quotes. Derived from that function rather than
    reimplementing the comparison, so the two cannot disagree about whether the
    corpus is ordered.
    """
    return not corpus_sort_disagreements(result)
