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
4    section KEEP/DROP                   :mod:`dociq.profiles.apply`
5    emit                                :mod:`dociq.emit`
6    verify: accounting, manifest, tokens :mod:`dociq.verify`

Stage 3 runs before 3b because a Bates range is one of Stage 3b's match keys,
and Stage 4 runs after 3b because a drop-log entry is written against a Doc ID.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

from dociq.contracts import (
    Disposition,
    DocumentRecord,
    PageKind,
    ReconciliationRow,
    RunConfig,
    RunResult,
    TokenEstimate,
    document_sort_key,
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
from dociq.emit.paths import OutputLayout
from dociq.emit.summary import build_summary_data, write_run_summary
from dociq.identify.bates import (
    BatesDecision,
    BatesRange,
    DecisionStatus,
    apply_bates,
    document_ranges,
    propose_format,
    ranges_by_sort_key,
)
from dociq.ingest import walker
from dociq.profiles.apply import apply_profiles
from dociq.profiles.model import FormatProfile, OperatorStamp, operator_stamp, write_matter_copy
from dociq.verify import accounting, manifest as mf
from dociq.verify.tokens import TokenEstimate as MeasuredEstimate
from dociq.verify.tokens import estimate_for_texts

__all__ = ["OCR_DISABLED", "PipelineOptions", "PipelineOutcome", "run"]

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
    profiles: tuple[FormatProfile, ...] = ()
    bates_decision: BatesDecision | None = None
    auto_confirm_bates: bool = False
    """Accept the detected Bates format without asking.

    §4 Stage 3 says the format is confirmed with the operator on first
    detection, and the GUI will do exactly that. This exists for the headless
    paths — the acceptance harness and the self-test — which have no operator to
    ask. It is recorded in the run's warnings whenever it fires, because a
    machine-confirmed pattern and an expert-confirmed one are not the same
    evidentiary object."""

    stamp: OperatorStamp | None = None
    previous_ledger: str | Path | None = None
    """Ledger of a previous run, for the D-04 renumbering check. Defaults to
    whatever ``doc_ids_issued.json`` is already sitting in the output root —
    which is the re-run case D-04 (b) is actually about."""

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
    bates_ranges: dict[tuple[str, str, int], BatesRange] = field(default_factory=dict)
    timings_s: tuple[tuple[str, float], ...] = ()
    """Per-stage wall clock, in stage order. Reporting only — it never reaches
    a hashed artifact, because a run that took longer is not a different run."""

    @property
    def ok(self) -> bool:
        return self.accounting.ok and not self.manifest.unclassified

    def timing(self, stage: str) -> float:
        return dict(self.timings_s).get(stage, 0.0)


def _measured_provenance(est: MeasuredEstimate, label: str) -> str:
    """The provenance string that travels with a token figure.

    Assembled from what this run actually did rather than quoted from a
    constant, so a figure can never claim a basis the run did not use. §7 makes
    the token estimate the headline; a headline whose basis is a stale literal is
    the failure this string exists to prevent.
    """
    profile = est.profile
    parts = [est.basis.provenance]
    if est.ratio_refuted:
        parts.append(
            f"THE RATIO BAND WAS NOT USED FOR THIS {label.upper()} FIGURE: the "
            f"text measures {profile.chars_per_pretoken_x100 / 100:.2f} "
            f"characters per pre-token, so the band {est.basis.display} predicts "
            f"fewer tokens than the text has pre-tokens ({profile.pretokens:,}) "
            "and no byte-level BPE tokenizer can emit that few. The range below "
            "is rebuilt from the measured pre-token structure instead."
        )
    elif est.clamped_low or est.clamped_high:
        parts.append(
            "A hard bound overrode the ratio at one end of the range "
            f"(low clamped: {est.clamped_low}, high clamped: {est.clamped_high})."
        )
    parts.append(
        f"Measured on this run ({label}): {profile.chars:,} characters, "
        f"{profile.pretokens:,} pre-tokens, reported range "
        f"{est.low:,}-{est.high:,} tokens."
    )
    return " ".join(parts)


def _to_contract_estimate(est: MeasuredEstimate, label: str) -> TokenEstimate:
    """Project the measured estimate onto the frozen contract type.

    ``ratio_refuted`` is copied from the estimator's own test result. The
    contract says a consumer must never infer it, and the only way to keep that
    true is for the producer to be the one place it is decided."""
    return TokenEstimate(
        chars=est.profile.chars,
        ratio_low=est.basis.low_x100 / 100,
        ratio_high=est.basis.high_x100 / 100,
        floor_tokens=est.profile.token_floor,
        ratio_refuted=est.ratio_refuted,
        provenance=_measured_provenance(est, label),
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
        rows.append(
            ReconciliationRow(
                category="index-only",
                doc_id="",
                filename=entry.filename,
                detail=(
                    f"LI File No {entry.li_file_no} (index row "
                    f"{entry.index_row_number}) at {entry.filepath}"
                ),
            )
        )
    return ContractReconciliation(matched=len(report.matched), rows=tuple(rows))


def _bates_decision(
    documents: Sequence[DocumentRecord],
    options: PipelineOptions,
    stamp: OperatorStamp,
    warnings: list[str],
) -> BatesDecision | None:
    """Stage 3's decision, or ``None`` for an unstamped set.

    An unstamped set is the ordinary case (§4 Stage 3, D-13) and produces no
    warning at all.
    """
    if options.bates_decision is not None:
        return options.bates_decision
    proposal = propose_format(documents)
    if proposal is None:
        return None
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


_STALE_PATTERNS = (
    "clean_text/*.txt",
    "sources.json",
    "document_index.csv",
    "document_index.xlsx",
    "reconciliation.csv",
    "processing_log.json",
    "run_summary.pdf",
    mf.MANIFEST_NAME,
    "profile/*.yaml",
)
"""Deliverables a re-run replaces. ``upload_package/`` is absent because
:func:`~dociq.emit.handoff.build_upload_package` already rebuilds it from
scratch, and ``doc_ids_issued.json`` is absent because it is this run's *input*
to the D-04 renumbering check — deleting it would silence the one warning the
re-run case exists to produce."""


def _purge_stale_deliverables(layout: OutputLayout) -> tuple[str, ...]:
    """Remove the previous run's deliverables from the destination.

    Re-running a matter means replacing its outputs, and a leftover
    ``clean_text/LI-06881.txt`` from a run against an older master index is
    worse than a missing one: it sits in the folder Expert Assist reads, under
    an identifier this run gave to a different document.

    Nothing is deleted silently — the list goes into the log's ``run`` section,
    which is outside the hashed content precisely because a first run and a
    re-run must not differ inside it.
    """
    removed: list[str] = []
    for pattern in _STALE_PATTERNS:
        for path in sorted(layout.root.glob(pattern)):
            if path.is_file():
                removed.append(path.relative_to(layout.root).as_posix())
                path.unlink()
    return tuple(removed)


def run(config: RunConfig, options: PipelineOptions | None = None) -> PipelineOutcome:
    """Run §4's six stages over ``config.source_root`` and write §7's outputs."""
    opts = options or PipelineOptions()
    stamp = opts.stamp or operator_stamp()
    layout = OutputLayout.at(config.output_root).ensure()
    timings: list[tuple[str, float]] = []

    def mark(stage: str, t0: float) -> None:
        timings.append((stage, round(time.monotonic() - t0, 3)))

    index = opts.master_index
    if index is None and opts.master_index_path:
        index = load_master_index(opts.master_index_path)

    # ---- Stages 1-2 --------------------------------------------------------
    t = time.monotonic()
    walk_notes = walker.RunNotes()
    walked = walker.run(config, opts.walk, walk_notes)
    mark("walk_extract", t)
    warnings: list[str] = list(walked.warnings)
    documents: tuple[DocumentRecord, ...] = walked.documents

    # ---- Stage 3 -----------------------------------------------------------
    t = time.monotonic()
    decision = _bates_decision(documents, opts, stamp, warnings)
    documents = apply_bates(documents, decision)
    ranges = document_ranges(documents)
    mark("bates", t)

    # ---- Stage 3b ----------------------------------------------------------
    t = time.monotonic()
    assignment = assign_doc_ids(
        documents, index, bates_ranges=ranges_by_sort_key(ranges)
    )
    documents = assignment.documents
    warnings.extend(assignment.warnings)
    report = reconcile(assignment, index, bates_ranges=ranges)
    mark("assign_reconcile", t)

    # ---- Stage 4 -----------------------------------------------------------
    t = time.monotonic()
    applied = apply_profiles(documents, opts.profiles)
    documents = applied.documents
    warnings.extend(applied.warnings)
    mark("classify", t)

    # The config the deliverables record is the one the run actually used, which
    # includes a Bates pattern that could not be known before Stage 3 ran.
    #
    # It also includes whether OCR ran at all. ``WalkOptions.ocr_enabled`` is not
    # part of :class:`RunConfig`, and RunConfig's own docstring says that
    # anything influencing output and absent from it is a determinism bug —
    # which this was. Measured on the real corpus: the same folder, the same
    # (identical) RunConfig, OCR on and off, produced 400 OCR pages versus 400
    # more EMPTY pages and a different hashed content section, while the
    # recorded configuration claimed both runs used rapidocr 1.2.3. The engine
    # fields are the contract's own place to say this, so the run stamps what it
    # actually did rather than what it was configured with.
    ocr_ran = (opts.walk or walker.WalkOptions()).ocr_enabled
    effective = replace(
        config,
        ocr_engine=config.ocr_engine if ocr_ran else OCR_DISABLED,
        ocr_engine_version=config.ocr_engine_version if ocr_ran else "",
        master_index=index.snapshot if index else config.master_index,
        bates_pattern=(decision.pattern() if decision else config.bates_pattern),
        profile_id=opts.profiles[0].profile_id if opts.profiles else config.profile_id,
        profile_version=(
            opts.profiles[0].version if opts.profiles else config.profile_version
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
    all_warnings = (warnings + [w.message for w in renumbering]
                    + walk_notes.messages())

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

    result = RunResult(
        config=effective,
        documents=documents,
        unsupported=walked.unsupported,
        warnings=tuple(all_warnings),
        tokens_before=_to_contract_estimate(before, "before reduction"),
        tokens_after=_to_contract_estimate(after, "after reduction"),
        reconciliation=(
            _to_contract_reconciliation(report) if index is not None else None
        ),
    )

    # ---- Stage 5 -----------------------------------------------------------
    t = time.monotonic()
    stale = _purge_stale_deliverables(layout)
    written: list[Path] = []
    text_result = write_clean_text(documents, layout)
    written.extend(layout.root / rel for _, rel in text_result.sources)
    written.append(write_sources_json(text_result, layout))
    rows = build_index_rows(documents, bates_ranges=ranges)
    written.append(write_index_csv(rows, layout))
    if index is not None:
        written.append(write_reconciliation_csv(report, layout))
    for profile in opts.profiles:
        written.append(write_matter_copy(profile, layout.root))

    # The ledger is read above, before this line overwrites it: comparing a run
    # against the ledger it just wrote would report that nothing was renumbered,
    # every time, forever.
    written.append(ledger.write(layout.issued_ids))

    bundle = build_log(
        effective,
        documents,
        unsupported=walked.unsupported,
        assignment=assignment,
        reconciliation=report if index is not None else None,
        renumbering=renumbering,
        drops=applied.drops,
        profiles=opts.profiles,
        bates_decision=decision,
        bates_ranges=ranges,
        token_estimate=after,
        warnings=warnings,
        stamp=stamp,
        output_hashes=_hashes_of(written, layout),
        run_notes={
            "stale_outputs_removed": list(stale),
            "load_dependent_extraction": list(walk_notes.load_dependent),
            "invocation_notes": list(walk_notes.invocation),
        },
    )
    write_processing_log(bundle, layout)

    if opts.write_workbook:
        write_index_xlsx(
            rows,
            layout,
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
                unsupported=walked.unsupported,
                tokens_before=before,
                tokens_after=after,
                ocr_threshold_pct=effective.ocr_conf_threshold_pct,
                id_regime=effective.id_regime.value,
                master_index=index.snapshot.filename if index else None,
                bates_note=_bates_note(decision, ranges),
                warnings=tuple(all_warnings),
            ),
            layout,
        )
    if opts.write_package:
        build_upload_package(
            layout,
            matter_name=opts.matter_name,
            document_count=len(documents),
            page_count=result.pages_in,
            estimate=after,
            has_bates=any(r.pages_with_bates for r in ranges.values()),
            id_regime=effective.id_regime.value,
        )
    mark("emit", t)

    # ---- Stage 6 (the gates) ----------------------------------------------
    t = time.monotonic()
    report_acc = accounting.check(result)
    man = mf.build(layout.root)
    mf.write(layout.root, man)
    mark("verify", t)

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
        bates_ranges=ranges,
        timings_s=tuple(timings),
    )


def _bates_note(
    decision: BatesDecision | None,
    ranges: dict[tuple[str, str, int], BatesRange],
) -> str:
    stamped = sum(r.pages_with_bates for r in ranges.values())
    if decision is None:
        return "No Bates stamps detected — absence is normal (§4 Stage 3)."
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


def corpus_sort_check(result: RunResult) -> bool:
    """Documents are in canonical order. Cheap, and it catches an emitter that
    sorted its own way."""
    ordered = sorted(result.documents, key=document_sort_key)
    return list(result.documents) == ordered
