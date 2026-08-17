"""``processing_log.json`` — the complete audit (§7).

The file has two top-level sections and the split is the whole point:

* ``run`` — timestamp, operator, host, tool version. These change every run by
  definition, so they sit **outside** the hashed content. Without the split, a
  determinism claim about the log would be false on its face and the whole
  byte-identical claim would look like hand-waving.
* ``content`` — everything derived from the inputs: per-document page
  accounting, every drop with its rule, OCR flags, unsupported files, hashes,
  profile version, Doc ID regime and master-index snapshot. Two runs over the
  same folder, profile and index produce identical ``content`` bytes.

``content_sha256`` is the SHA-256 of ``content``'s canonical form. Because
``content`` contains no floats and no field named ``ocr_conf``, the identity
projection and the persisted projection are the *same bytes* — so the claim is
verifiable by re-hashing the file, not merely asserted. OCR confidence is
therefore carried as an integer percent; Principle 5 keeps floats out of
identity, and a log the operator cannot re-hash is not an audit trail.
"""

from __future__ import annotations

import hashlib
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from dociq.contracts import (
    CONTRACT_VERSION,
    ContractViolation,
    Disposition,
    DocumentRecord,
    PageKind,
    RunConfig,
    canonical_json,
    content_hash,
    document_sort_key,
    run_identity,
    to_jsonable,
)
from dociq.docid.assign import AssignmentResult
from dociq.docid.reconcile import ReconciliationReport, RenumberWarning
from dociq.emit.paths import OutputLayout, write_text_deterministic
from dociq.identify.bates import BatesDecision, BatesRange
from dociq.profiles.model import FormatProfile, OperatorStamp, operator_stamp
from dociq.sections.apply import SectionDropEntry
from dociq.verify import tokens as tokens_mod
from dociq.verify.accounting import AccountingReport
from dociq.verify.manifest import Manifest
from dociq.verify.tokens import TokenEstimate

__all__ = ["build_log", "write_processing_log", "assert_float_free", "LogBundle"]


def assert_float_free(node: Any, path: str = "content") -> None:
    """Refuse to write a float into the hashed content.

    A float in the log would make the hash hostage to platform rounding, and
    the failure would appear as an intermittent determinism break long after
    the field was added. Checked at write time so the failure lands on the
    person who added the field.
    """
    if isinstance(node, float):
        raise ContractViolation(
            f"{path}: floats must not appear in the hashed log content "
            "(carry the value as an integer percent or a string)"
        )
    if isinstance(node, Mapping):
        for k, v in node.items():
            assert_float_free(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            assert_float_free(v, f"{path}[{i}]")


def _conf_pct(value: float | None) -> int | None:
    return None if value is None else round(value * 100)


def _sections_of(doc: DocumentRecord) -> list[dict[str, Any]]:
    """What was recognized in one document, and by which tier (§5.4, A-18).

    **Recognition is logged whether or not anything was dropped**, and that is
    the half it would have been easy to leave out. Under D-34 a shipped template
    arrives unengaged, so the ordinary run — the one a freshly-installed DocIQ
    performs — drops nothing at all; if the tier reached the log only through a
    drop entry, then §5.4's "recognition tier belongs in the log, per page"
    would be satisfied by exactly the runs that need it least, and A-18 would be
    a field that no real run populates. That is the A-12 shape: declared, wired
    to something, and absent from the product's actual behavior.

    It is also what makes the index's "sections dropped" column readable: a
    count of omissions means nothing without the sections that were recognized
    and kept beside it.

    One row per contiguous run of pages under one section and one tier, in page
    order — the same grouping :func:`dociq.sections.resolve.spans_from_pages`
    uses, so the log and the drop decision describe the same spans.
    """
    rows: list[dict[str, Any]] = []
    for page in doc.pages:
        if page.section is None or page.section_tier is None:
            continue
        dropped = 1 if page.disposition is Disposition.DROP else 0
        if (rows and rows[-1]["section"] == page.section
                and rows[-1]["tier"] == page.section_tier.value
                and rows[-1]["last_page"] == page.page_no - 1):
            rows[-1]["last_page"] = page.page_no
            rows[-1]["pages"] += 1
            rows[-1]["pages_dropped"] += dropped
            continue
        rows.append({
            "section": page.section,
            "tier": page.section_tier.value,
            "first_page": page.page_no,
            "last_page": page.page_no,
            "pages": 1,
            "pages_dropped": dropped,
        })
    return rows


def _document_entry(doc: DocumentRecord) -> dict[str, Any]:
    ocr_pages = [p for p in doc.pages if p.kind is PageKind.OCR]
    confs = [p.ocr_conf for p in ocr_pages if p.ocr_conf is not None]
    kinds: dict[str, int] = {}
    for p in doc.pages:
        kinds[p.kind.value] = kinds.get(p.kind.value, 0) + 1
    return {
        "doc_id": doc.doc_id,
        "li_file_no": doc.li_file_no,
        "rel_path": doc.rel_path,
        "filename": doc.filename,
        "ext": doc.ext,
        "sha256": doc.sha256,
        "size_bytes": doc.size_bytes,
        "status": doc.status.value,
        "parent_doc_id": doc.parent_doc_id,
        "container_order": doc.container_order,
        "doc_type": doc.doc_type,
        "profile_id": doc.profile_id,
        "profile_version": doc.profile_version,
        "detected_dates": list(doc.detected_dates),
        "pages_in": doc.pages_in,
        "pages_kept": doc.pages_kept,
        "pages_dropped": doc.pages_dropped,
        "sections": _sections_of(doc),
        "page_kinds": dict(sorted(kinds.items())),
        "ocr_pages": len(ocr_pages),
        "ocr_mean_conf_pct": _conf_pct(sum(confs) / len(confs)) if confs else None,
        "ocr_low_conf_lines": sum(p.ocr_low_conf_lines for p in doc.pages),
        "notes": list(doc.notes),
        "error": doc.error,
    }


def _flagged_pages(doc: DocumentRecord, threshold_pct: int) -> list[dict[str, Any]]:
    out = []
    for page in doc.pages:
        if page.ocr_conf is None:
            continue
        pct = _conf_pct(page.ocr_conf)
        if pct is not None and pct < threshold_pct:
            out.append(
                {
                    "doc_id": doc.doc_id,
                    "page_no": page.page_no,
                    "ocr_conf_pct": pct,
                    "ocr_low_conf_lines": page.ocr_low_conf_lines,
                    "ocr_line_count": page.ocr_line_count,
                }
            )
    return out


@dataclass(frozen=True, slots=True)
class LogBundle:
    """The log as a value, so it can be asserted on without touching disk."""

    run: dict[str, Any]
    content: dict[str, Any]
    content_sha256: str

    def as_document(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "hash_scope": (
                "content_sha256 is the SHA-256 of the canonical JSON form of the "
                "'content' section only. The 'run' section (timestamp, operator, "
                "host) is excluded by design so that a rerun at a different time "
                "still proves byte-identical content. document_index.xlsx and "
                "run_summary.pdf are outside the byte-identical claim entirely; "
                "clean_text/*.txt, sources.json and document_index.csv are inside it."
            ),
        }

    def render(self) -> str:
        return canonical_json(self.as_document()) + "\n"


def build_log(
    config: RunConfig,
    documents: Sequence[DocumentRecord],
    *,
    unsupported: Sequence[DocumentRecord] = (),
    assignment: AssignmentResult | None = None,
    reconciliation: ReconciliationReport | None = None,
    renumbering: Sequence[RenumberWarning] = (),
    drops: Sequence[SectionDropEntry] = (),
    profiles: Sequence[FormatProfile] = (),
    bates_decision: BatesDecision | None = None,
    bates_ranges: Mapping[tuple[str, str, int], BatesRange] | None = None,
    token_estimate: TokenEstimate | None = None,
    warnings: Sequence[str] = (),
    stamp: OperatorStamp | None = None,
    tool_version: str = "",
    output_hashes: Mapping[str, str] | None = None,
    accounting_report: AccountingReport | None = None,
    manifest: Manifest | None = None,
    timings_s: Sequence[tuple[str, float]] = (),
    run_notes: Mapping[str, Any] | None = None,
) -> LogBundle:
    """Assemble the log. Pure — nothing here touches the filesystem.

    **On the three arguments that land in ``run`` rather than in ``content``**
    (``accounting_report``, ``manifest``, ``timings_s`` — Codex review #2 fix
    round, B-5). They exist because a run that was REFUSED publication at §4
    Stage 6 has a diagnosis, and until this the diagnosis lived only in the
    in-memory :class:`~dociq.pipeline.PipelineOutcome`. The quarantined
    ``processing_log.json`` is what remains after the process exits; a value the
    operator can only see by holding the return value of the function that
    already returned is not a durable audit record.

    They go in ``run``, and that placement is not a filing convenience — it is
    criterion 7. A run that was refused and a run that was not differ in their
    INVOCATION, not in their evidence: the same corpus, the same profile and the
    same index must produce the same ``content`` bytes whether or not this
    particular attempt tripped a gate. Hashing a gate outcome, a manifest of a
    discarded staging set or a wall clock would make the byte-identical claim
    false on its face, which is exactly what ``output_root`` and an elapsed-time
    string each did here before and had to be unpicked. ``content`` keeps only
    the input-derived facts — the assignment, the reconciliation, the drops, the
    profiles, the Bates section — and those ARE passed on the refusal path now,
    because a refused run established them and blanking them makes the record
    lie about what the run did.
    """
    docs = sorted(documents, key=document_sort_key)
    ranges = bates_ranges or {}
    s = stamp or operator_stamp()

    run = {
        "timestamp": s.saved_at,
        "operator": s.username,
        "host": s.host or platform.node(),
        "tool_version": tool_version,
        "contract_version": CONTRACT_VERSION,
        "python": platform.python_version(),
        "output_root": config.output_root,
        # The destination is deliberately OUTSIDE the hashed content. The
        # determinism contract is "same folder + same profile + same master
        # index = byte-identical"; where the operator chose to put the results
        # is not one of those inputs, and hashing it would make the same corpus
        # reduced into two different directories fail its own determinism proof.
    }
    # Facts about THIS invocation rather than about the inputs — what the run
    # cleared out of the destination, for instance. They belong beside the
    # timestamp for the same reason: a first run and a re-run over identical
    # inputs differ in exactly these fields, so recording them in `content`
    # would make the byte-identical claim false on its face for the re-run case
    # D-04 mitigation (b) exists to support.
    # `run_notes` is also where the caller records settings that are recorded
    # but NOT hashed: pool widths (which must not change output — if one ever
    # does, that is a determinism defect to fix, not an input to hash) and the
    # disk-headroom multiplier (which gates whether the run starts rather than
    # what it emits, and is a float, which Principle 5 bars from identity
    # fields). They are supplied by the pipeline rather than read here, because
    # `emit` does not depend on `ingest`.
    # The Stage-6 gate outcome, serialized. `AccountingReport.ok` is a property
    # over `discrepancies`, so the list is what is recorded and the boolean is
    # restated beside it for a reader who wants one field: a log that says only
    # "not ok" reproduces the unactionable-at-9,000-documents problem
    # `AccountingReport` was written to avoid. The `<run>` entries the pipeline
    # inserts — the terminal status and one line per refusing gate — are part of
    # this list and are therefore the thing that names WHICH gate refused.
    if accounting_report is not None:
        run["accounting_gate"] = {
            "ok": accounting_report.ok,
            "documents": accounting_report.documents,
            "unsupported": accounting_report.unsupported,
            "failed": accounting_report.failed,
            "pages_in": accounting_report.pages_in,
            "pages_kept": accounting_report.pages_kept,
            "pages_dropped": accounting_report.pages_dropped,
            "documents_degraded": accounting_report.documents_degraded,
            "documents_evidence_lost": accounting_report.documents_evidence_lost,
            "discrepancies": [
                {"rel_path": d.rel_path, "kind": d.kind, "detail": d.detail}
                for d in accounting_report.discrepancies
            ],
        }
    # The manifest of the set this run BUILT. On a published run it is also a
    # deliverable of its own (`output_manifest.json`); on a refused one the
    # staging directory that held it is discarded, so without this the only
    # record of what the refused set contained — including the unclassified
    # outputs that may be the reason it was refused — would be the in-memory
    # outcome.
    if manifest is not None:
        run["output_manifest"] = manifest.to_jsonable()
    # Wall clock per stage. Reporting only, and in `run` for the plainest of the
    # reasons on this list: a run that took longer is not a different run. Ints
    # of milliseconds rather than floats, so the same rule that keeps floats out
    # of identity is not quietly relaxed one section over.
    if timings_s:
        run["stage_ms"] = {stage: round(seconds * 1000) for stage, seconds in timings_s}
    run.update(dict(run_notes or {}))

    bates_section: dict[str, Any] = {
        "status": bates_decision.status.value if bates_decision else "not-run",
        "pattern": bates_decision.pattern() if bates_decision else None,
        "decided_by": bates_decision.decided_by if bates_decision else None,
        "decided_at": bates_decision.decided_at if bates_decision else None,
        "documents_with_bates": sum(
            1 for r in ranges.values() if r.pages_with_bates > 0
        ),
        "pages_with_bates": sum(r.pages_with_bates for r in ranges.values()),
        "note": (
            "No Bates stamps were detected. Absence is normal (§4 Stage 3) and is "
            "not an error."
            if not any(r.pages_with_bates for r in ranges.values())
            else None
        ),
        "ranges": [
            {
                "doc_id": doc.doc_id,
                "start": ranges[document_sort_key(doc)].start,
                "end": ranges[document_sort_key(doc)].end,
                "pages_with_bates": ranges[document_sort_key(doc)].pages_with_bates,
                "pages_without_bates": ranges[document_sort_key(doc)].pages_without_bates,
            }
            for doc in docs
            if document_sort_key(doc) in ranges
            and ranges[document_sort_key(doc)].pages_with_bates
        ],
    }

    id_section: dict[str, Any] = {
        "regime": config.id_regime.value,
        "master_index": (
            {
                "filename": config.master_index.filename,
                "sha256": config.master_index.sha256,
                "row_count": config.master_index.row_count,
            }
            if config.master_index
            else None
        ),
        "root_prefix": (
            assignment.alignment.prefix if assignment and assignment.alignment else None
        ),
        "root_alignment_matches": (
            assignment.alignment.matched if assignment and assignment.alignment else 0
        ),
        "assignments": [
            {
                "doc_id": a.doc_id,
                "namespace": a.namespace,
                "method": a.method,
                "li_file_no": a.li_file_no,
                "index_row_number": a.index_row_number,
                "parent_doc_id": a.parent_doc_id,
                "note": a.note,
            }
            for a in (assignment.assignments if assignment else ())
        ],
    }

    # D-04 (b)'s renumbering warnings live in the `run` section, NOT in the
    # hashed content. They are a comparison against a ledger the *destination*
    # folder happens to hold, and the destination is not one of the determinism
    # contract's inputs — the same reason `output_root` sits in `run`. Hashing
    # them would mean a first run and a re-run over identical inputs produced
    # different content, and a corrupt leftover ledger could break the
    # byte-identical claim with no input change anywhere. They are still written
    # to the log, still in the run summary, and still in RunResult.warnings.
    renumbering_section = [
        {
            "kind": w.kind,
            "doc_id": w.doc_id,
            "previous_doc_id": w.previous_doc_id,
            "rel_path": w.rel_path,
            "message": w.message,
        }
        for w in renumbering
    ]
    run["renumbering_warnings"] = renumbering_section

    flagged = [e for doc in docs for e in _flagged_pages(doc, config.ocr_conf_threshold_pct)]

    content: dict[str, Any] = {
        # The ONE run identity, in the hashed section (amendment A-08, from
        # Codex review #1 round 2 finding B-R2-2). Four things used to claim to
        # be the run identity and disagree: RunConfig's hash included the
        # output folder, the manifest's claim_identity said it counted, this
        # section deliberately left it out, and the acceptance harness ran to
        # two destinations and demanded one identity. Nothing was persisted, so
        # there was no value to point at.
        #
        # It is hashed content rather than a `run` note because it is a fact
        # about the INPUTS: two runs over the same inputs must agree on it, and
        # a run whose profile set or caps changed must not.
        "run_identity_sha256": run_identity(config),
        "config": {
            "source_root": config.source_root,
            "profile_id": config.profile_id,
            "profile_version": config.profile_version,
            "ocr_conf_threshold_pct": config.ocr_conf_threshold_pct,
            "ocr_engine": config.ocr_engine,
            "ocr_engine_version": config.ocr_engine_version,
            "bates_pattern": config.bates_pattern,
            # A-04 / Codex review #1 finding B-2. Serialized through the
            # contract's identity projection, which is the same projection the
            # run hash uses — so what the log shows and what the hash covers
            # cannot drift apart. `workers` drops out here by design and is
            # recorded in the `run` section below: pool width is a performance
            # setting, and if it ever changed output that would be a
            # determinism defect to fix rather than an input to hash.
            "limits": (
                to_jsonable(config.limits, for_identity=True)
                if config.limits is not None
                else None
            ),
        },
        "accounting": {
            "documents": len(docs),
            "unsupported": len(unsupported),
            "pages_in": sum(d.pages_in for d in docs),
            "pages_kept": sum(d.pages_kept for d in docs),
            "pages_dropped": sum(d.pages_dropped for d in docs),
        },
        "documents": [_document_entry(d) for d in docs],
        "unsupported_files": [
            {
                "doc_id": d.doc_id,
                "rel_path": d.rel_path,
                "filename": d.filename,
                "ext": d.ext,
                "sha256": d.sha256,
                "size_bytes": d.size_bytes,
                "status": d.status.value,
                "error": d.error,
                "notes": list(d.notes),
            }
            for d in sorted(unsupported, key=document_sort_key)
        ],
        # §7's per-drop record, and §5.4's requirement inside it. Each entry
        # now answers three questions an expert is asked in cross-examination
        # and could previously answer only the first of: WHICH rule omitted this
        # page, WHAT KIND OF EVIDENCE placed it in that section (`tier`, A-18 —
        # the document's own outline is not the same claim as a page-class
        # rule), and WHO approved the omission and when (D-34 — an approver
        # that is a real person who acted, never a template's default).
        "drops": [
            {
                "doc_id": e.doc_id,
                "rel_path": e.rel_path,
                "page_no": e.page_no,
                "section": e.section,
                "family": e.family,
                "tier": e.tier.value,
                "evidence": e.evidence,
                "family_id": e.family_id,
                "drop_rule": e.drop_rule,
                "approved_by": e.approved_by,
                "approved_at": e.approved_at,
                "matter": e.matter,
                "template_id": e.template_id,
                "template_version": e.template_version,
            }
            for e in drops
        ],
        "ocr_flagged_pages": flagged,
        "bates": bates_section,
        "doc_ids": id_section,
        "profiles": [
            {
                "profile_id": p.profile_id,
                "version": p.version,
                "display_name": p.display_name,
                "created_by": p.created_by,
                "created_at": p.created_at,
                "profile_hash": p.profile_hash,
                "drop_rules": [
                    {"rule_id": r.rule_id, "pattern": r.pattern, "notes": r.notes}
                    for r in p.drop_rules
                ],
            }
            for p in profiles
        ],
        "reconciliation": (
            {
                "totals": reconciliation.totals,
                "root_prefix": reconciliation.root_prefix,
                "warnings": list(reconciliation.warnings),
            }
            if reconciliation is not None
            else None
        ),
        "token_estimate": (
            {
                "chars": token_estimate.profile.chars,
                "utf8_bytes": token_estimate.profile.utf8_bytes,
                "pretokens": token_estimate.profile.pretokens,
                "pretokens_note": (
                    "DocIQ's own approximate pre-tokenization. NOT a lower "
                    "bound on token count — see assumption A1."
                ),
                "tokens_low": token_estimate.low,
                "tokens_high": token_estimate.high,
                "token_ceiling": token_estimate.profile.token_ceiling,
                "ratio_low_x100": token_estimate.basis.low_x100,
                "ratio_high_x100": token_estimate.basis.high_x100,
                "basis": token_estimate.basis.label,
                "method": token_estimate.method,
                "provenance": token_estimate.provenance_text(),
                "assumptions": list(tokens_mod.ASSUMPTIONS),
                "sound_bound": tokens_mod.SOUND_BOUND,
                "ratio_refuted": token_estimate.ratio_refuted,
                "widened": token_estimate.widened,
                "clamped_high": token_estimate.clamped_high,
            }
            if token_estimate is not None
            else None
        ),
        "output_hashes": dict(sorted((output_hashes or {}).items())),
        "warnings": list(warnings),
    }

    assert_float_free(content)
    return LogBundle(run=run, content=content, content_sha256=content_hash(content))


def write_processing_log(bundle: LogBundle, layout: OutputLayout) -> Path:
    """Write the log, then prove the hash it claims.

    Re-hashing the rendered bytes is the check that makes the audit trail
    self-verifying: if the persisted form and the hashed form ever diverge, the
    run stops here rather than shipping a log whose hash means nothing.
    """
    text = bundle.render()
    path = write_text_deterministic(layout.processing_log, text)
    replayed = hashlib.sha256(
        canonical_json(bundle.content, for_identity=True).encode("utf-8")
    ).hexdigest()
    if replayed != bundle.content_sha256:  # pragma: no cover - guards a refactor
        raise ContractViolation(
            "processing_log content hash does not replay; the persisted and "
            "hashed projections have diverged"
        )
    return path
