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
    DocumentRecord,
    PageKind,
    RunConfig,
    canonical_json,
    content_hash,
    document_sort_key,
)
from dociq.docid.assign import AssignmentResult
from dociq.docid.reconcile import ReconciliationReport, RenumberWarning
from dociq.emit.paths import OutputLayout, write_text_deterministic
from dociq.identify.bates import BatesDecision, BatesRange
from dociq.profiles.apply import DropLogEntry
from dociq.profiles.model import FormatProfile, OperatorStamp, operator_stamp
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
    drops: Sequence[DropLogEntry] = (),
    profiles: Sequence[FormatProfile] = (),
    bates_decision: BatesDecision | None = None,
    bates_ranges: Mapping[tuple[str, str, int], BatesRange] | None = None,
    token_estimate: TokenEstimate | None = None,
    warnings: Sequence[str] = (),
    stamp: OperatorStamp | None = None,
    tool_version: str = "",
    output_hashes: Mapping[str, str] | None = None,
) -> LogBundle:
    """Assemble the log. Pure — nothing here touches the filesystem."""
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
        "renumbering_warnings": [
            {
                "kind": w.kind,
                "doc_id": w.doc_id,
                "previous_doc_id": w.previous_doc_id,
                "rel_path": w.rel_path,
                "message": w.message,
            }
            for w in renumbering
        ],
    }

    flagged = [e for doc in docs for e in _flagged_pages(doc, config.ocr_conf_threshold_pct)]

    content: dict[str, Any] = {
        "config": {
            "source_root": config.source_root,
            "profile_id": config.profile_id,
            "profile_version": config.profile_version,
            "ocr_conf_threshold_pct": config.ocr_conf_threshold_pct,
            "ocr_engine": config.ocr_engine,
            "ocr_engine_version": config.ocr_engine_version,
            "bates_pattern": config.bates_pattern,
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
        "drops": [
            {
                "doc_id": e.doc_id,
                "rel_path": e.rel_path,
                "page_no": e.page_no,
                "section": e.section,
                "rule_id": e.rule_id,
                "pattern": e.pattern,
                "matched_text": e.matched_text,
                "profile_id": e.profile_id,
                "profile_version": e.profile_version,
                "rule_notes": e.rule_notes,
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
                "pretokens": token_estimate.profile.pretokens,
                "tokens_low": token_estimate.low,
                "tokens_high": token_estimate.high,
                "ratio_low_x100": token_estimate.basis.low_x100,
                "ratio_high_x100": token_estimate.basis.high_x100,
                "basis": token_estimate.basis.label,
                "provenance": token_estimate.basis.provenance,
                "clamped_low": token_estimate.clamped_low,
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
