"""The §4 Stage-6 zero-discrepancy gate, corpus-wide.

``DocumentRecord.validate()`` is the per-document half and runs at every stage
boundary. This is the other half: the totals across the whole run, plus the
structural checks that only make sense with the corpus in view — a child whose
parent is missing, a page number that skipped, a DROP with no rule.

The output is a report, not a boolean. "Accounting failed" is unactionable at
9,000 documents; the operator needs the document, the stage-visible symptom,
and the numbers that disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import (
    ContractViolation,
    Disposition,
    DocumentRecord,
    ProcessingStatus,
    RunResult,
)


@dataclass(frozen=True, slots=True)
class Discrepancy:
    rel_path: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rel_path}: [{self.kind}] {self.detail}"


@dataclass
class AccountingReport:
    pages_in: int = 0
    pages_kept: int = 0
    pages_dropped: int = 0
    documents: int = 0
    unsupported: int = 0
    failed: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    documents_degraded: int = 0
    """Documents carrying a RETRYABLE evidence gap (Codex review #1, B-3).

    A count, not a discrepancy: a disclosed gap is Principle 1 working, not
    the accounting failing. It is here because a gap recorded only inside one
    document's notes is a gap nobody reads at 9,000 documents, and B-3's
    requirement is that a failure stay auditable "in the parent record AND in
    accounting"."""

    documents_evidence_lost: int = 0
    """Documents carrying a FINAL evidence gap — content named in the record
    whose bytes are not in the corpus and will not be recovered by re-reading."""

    @property
    def ok(self) -> bool:
        return not self.discrepancies

    @property
    def evidence_line(self) -> str:
        """The B-3 tally, or ``""`` when nothing read short.

        Empty on a clean corpus deliberately: the ordinary run's accounting
        text is unchanged, so this adds a line exactly when there is something
        to say.
        """
        parts = []
        if self.documents_degraded:
            parts.append(f"{self.documents_degraded} document(s) read with a "
                         "disclosed, retryable evidence gap")
        if self.documents_evidence_lost:
            parts.append(f"{self.documents_evidence_lost} document(s) name "
                         "content whose bytes are NOT in the corpus")
        return "EVIDENCE GAPS — " + "; ".join(parts) if parts else ""

    def render(self) -> str:
        """Human-readable, and the same text the log and the summary quote."""
        head = (f"pages in {self.pages_in} = kept {self.pages_kept} + dropped "
                f"{self.pages_dropped} across {self.documents} document(s); "
                f"{self.unsupported} unsupported, {self.failed} failed")
        gaps = self.evidence_line
        if self.ok:
            return (f"PAGE ACCOUNTING OK — {head}"
                    + (f"\n{gaps}" if gaps else ""))
        lines = [f"PAGE ACCOUNTING FAILED — {head}"]
        if gaps:
            lines.append(gaps)
        lines.append(f"{len(self.discrepancies)} discrepancy(ies):")
        lines.extend(f"  - {d}" for d in self.discrepancies)
        return "\n".join(lines)


def check(result: RunResult) -> AccountingReport:
    """Reconcile a whole run. Never raises — a broken corpus must be
    reportable, and raising would hide every discrepancy after the first."""
    rep = AccountingReport(documents=len(result.documents),
                           unsupported=len(result.unsupported))
    # A container member names its parent by ``rel_path`` before Stage 3b and by
    # the parent's Doc ID after it (``docid.assign`` performs the swap). The
    # gate runs on both sides of that boundary — Track A's spine calls it on the
    # raw walk, the full pipeline calls it on the assigned corpus — so it has to
    # recognize both forms. Accepting only one would report every archive member
    # in a completed run as an orphan.
    known = {d.rel_path for d in result.documents} | {
        d.rel_path for d in result.unsupported}
    known |= {d.doc_id for d in result.documents if d.doc_id}
    known |= {d.doc_id for d in result.unsupported if d.doc_id}

    for doc in result.documents:
        rep.pages_in += doc.pages_in
        rep.pages_kept += doc.pages_kept
        rep.pages_dropped += doc.pages_dropped
        if doc.status is ProcessingStatus.FAILED:
            rep.failed += 1
        transient, final = _evidence_gaps(doc)
        rep.documents_degraded += 1 if transient else 0
        rep.documents_evidence_lost += 1 if final else 0
        _check_document(doc, known, rep)

    if rep.pages_kept + rep.pages_dropped != rep.pages_in:
        rep.discrepancies.append(Discrepancy(
            "<corpus>", "totals",
            f"{rep.pages_in} in != {rep.pages_kept} kept + "
            f"{rep.pages_dropped} dropped"))
    # The derived properties must agree with the sum computed here. They are
    # the numbers the GUI and the summary read, and two ways of counting the
    # same thing eventually disagree — this is where that would surface.
    for label, ours, theirs in (("pages_in", rep.pages_in, result.pages_in),
                                ("pages_kept", rep.pages_kept, result.pages_kept),
                                ("pages_dropped", rep.pages_dropped,
                                 result.pages_dropped)):
        if ours != theirs:
            rep.discrepancies.append(Discrepancy(
                "<corpus>", "derived-property",
                f"{label}: reconciler counted {ours}, RunResult reports {theirs}"))

    dup_paths = _duplicate_paths(result)
    for rel, n in dup_paths:
        rep.discrepancies.append(Discrepancy(
            rel, "duplicate-record",
            f"{n} records share this relative path; the emit layer would "
            "overwrite one clean_text file with another"))
    return rep


def _check_document(doc: DocumentRecord, known: set[str],
                    rep: AccountingReport) -> None:
    try:
        doc.validate()
    except ContractViolation as exc:
        rep.discrepancies.append(Discrepancy(doc.rel_path, "contract", str(exc)))

    for p in doc.pages:
        if p.disposition is Disposition.DROP and not p.drop_rule:
            rep.discrepancies.append(Discrepancy(
                doc.rel_path, "unattributable-drop",
                f"page {p.page_no} dropped with no rule — Principle 1"))

    if doc.parent_doc_id is not None and doc.parent_doc_id not in known:
        rep.discrepancies.append(Discrepancy(
            doc.rel_path, "orphan-child",
            f"parent '{doc.parent_doc_id}' is not in the run"))

    if doc.status is ProcessingStatus.FAILED and not doc.error:
        rep.discrepancies.append(Discrepancy(
            doc.rel_path, "silent-failure",
            "status FAILED with no message — the operator cannot act on it"))

    if doc.status is ProcessingStatus.FULL and doc.pages_in == 0 \
            and doc.ext != ".zip":
        rep.discrepancies.append(Discrepancy(
            doc.rel_path, "zero-page-success",
            "status FULL but no pages were produced"))


def _evidence_gaps(doc: DocumentRecord) -> tuple[bool, bool]:
    """``(has a retryable gap, has a final gap)`` for one document.

    Reads the same marker vocabulary the extractor emits and the walker's
    serial retry keys on, so there is one definition of "this read short" in
    the product rather than three. Imported lazily: ``verify`` is allowed to
    depend on ``ingest``, but only the corpus gate actually needs it.
    """
    from ..ingest.extract import has_final_marker, has_transient_marker

    texts = list(doc.notes) + [n for p in doc.pages for n in p.notes]
    if doc.error:
        texts.append(doc.error)
    return (any(has_transient_marker(t) for t in texts),
            any(has_final_marker(t) for t in texts))


def _duplicate_paths(result: RunResult) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for d in list(result.documents) + list(result.unsupported):
        counts[d.rel_path] = counts.get(d.rel_path, 0) + 1
    return sorted((rel, n) for rel, n in counts.items() if n > 1)
