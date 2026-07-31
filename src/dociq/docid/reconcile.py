"""§5 master-index reconciliation, and the D-04 renumbering warning.

Two separate jobs share this module because they answer the same question from
different directions:

* **Reconciliation** compares this run's folder against this run's index — a
  production completeness check ("what did the client not send us?").
* **Renumbering detection** compares this run's identifiers against the ones a
  *previous* run issued — D-04 mitigation (b). The LI index is a living
  document; if the document manager inserts rows and re-sorts, ``LI-06881``
  can silently become a different file, and every citation written against the
  old numbering quietly rots. That is the single biggest risk D-04 accepted, so
  it gets a loud, explicit check rather than a footnote.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dociq.contracts import (
    DocumentRecord,
    MasterIndexSnapshot,
    canonical_json,
    content_hash,
    document_sort_key,
)
from dociq.docid.assign import AssignmentResult
from dociq.docid.masterindex import MasterIndex, MasterIndexRow
from dociq.identify.bates import BatesRange

__all__ = [
    "FieldDiscrepancy",
    "MatchedPair",
    "FolderOnlyEntry",
    "IndexOnlyEntry",
    "ReconciliationReport",
    "reconcile",
    "IssuedIdLedger",
    "RenumberWarning",
    "detect_renumbering",
]

_SIZE_TOLERANCE_KB = 1
"""The index stores whole kilobytes, so a byte count and a KB figure can never
agree exactly. One KB of slack absorbs the rounding and nothing else; a real
size change is orders of magnitude larger."""


@dataclass(frozen=True, slots=True)
class FieldDiscrepancy:
    field: str
    folder_value: str
    index_value: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MatchedPair:
    doc_id: str
    rel_path: str
    li_file_no: str
    index_row_number: int
    match_method: str
    discrepancies: tuple[FieldDiscrepancy, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.discrepancies


@dataclass(frozen=True, slots=True)
class FolderOnlyEntry:
    doc_id: str
    rel_path: str
    filename: str
    ext: str
    size_bytes: int
    sha256: str
    reason: str


@dataclass(frozen=True, slots=True)
class IndexOnlyEntry:
    """An index row with no folder file behind it.

    Two populations share this type, and :attr:`quarantined` tells them apart:

    * an ordinary row that was numbered but never matched — it has a real
      ``li_file_no``;
    * a row the loader could not admit to the LI number space at all (D-1) —
      ``li_file_no`` is ``""`` because there is none, and :attr:`reason` says
      why. Nothing downstream may print an invented identifier for it.
    """

    li_file_no: str
    index_row_number: int
    filename: str
    filepath: str
    ext: str | None
    size_kb: int | None
    quarantined: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """The §5 report, written as its own tab of the index workbook."""

    matched: tuple[MatchedPair, ...]
    folder_only: tuple[FolderOnlyEntry, ...]
    index_only: tuple[IndexOnlyEntry, ...]
    snapshot: MasterIndexSnapshot | None
    root_prefix: str | None
    warnings: tuple[str, ...] = ()

    @property
    def matched_count(self) -> int:
        return len(self.matched)

    @property
    def discrepancy_count(self) -> int:
        return sum(len(m.discrepancies) for m in self.matched)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "matched": len(self.matched),
            "matched_with_discrepancies": sum(1 for m in self.matched if m.discrepancies),
            "folder_only": len(self.folder_only),
            "index_only": len(self.index_only),
            # A subset of index_only, not an addition to it: rows the loader
            # could not number at all. Broken out because "the client never
            # sent us this file" and "your spreadsheet cell is malformed" are
            # different jobs for different people.
            "index_only_unnumbered": sum(1 for e in self.index_only if e.quarantined),
        }


def _size_discrepancy(doc: DocumentRecord, row: MasterIndexRow) -> FieldDiscrepancy | None:
    if row.size_kb is None:
        return None
    folder_kb = round(doc.size_bytes / 1024)
    if abs(folder_kb - row.size_kb) <= _SIZE_TOLERANCE_KB:
        return None
    return FieldDiscrepancy(
        field="size",
        folder_value=f"{folder_kb} KB ({doc.size_bytes} bytes)",
        index_value=f"{row.size_kb} KB",
        detail="the folder copy and the indexed copy are not the same size",
    )


def _ext_discrepancy(doc: DocumentRecord, row: MasterIndexRow) -> FieldDiscrepancy | None:
    if not row.ext:
        return None
    indexed = row.ext.strip().lstrip(".").casefold()
    actual = doc.ext.strip().lstrip(".").casefold()
    if indexed == actual:
        return None
    return FieldDiscrepancy(
        field="extension", folder_value=doc.ext, index_value=row.ext
    )


def _name_discrepancy(doc: DocumentRecord, row: MasterIndexRow) -> FieldDiscrepancy | None:
    if doc.filename == row.filename:
        return None
    if doc.filename.casefold() == row.filename.casefold():
        return FieldDiscrepancy(
            field="filename",
            folder_value=doc.filename,
            index_value=row.filename,
            detail="differs only in letter case",
        )
    return FieldDiscrepancy(
        field="filename", folder_value=doc.filename, index_value=row.filename
    )


def _bates_discrepancy(
    rng: BatesRange | None, row: MasterIndexRow
) -> FieldDiscrepancy | None:
    if row.bates_start is None and row.bates_end is None:
        return None
    detected = (rng.start if rng else None, rng.end if rng else None)
    indexed = (row.bates_start, row.bates_end)
    if detected == indexed:
        return None
    return FieldDiscrepancy(
        field="bates_range",
        folder_value=f"{detected[0] or '-'} .. {detected[1] or '-'}",
        index_value=f"{indexed[0] or '-'} .. {indexed[1] or '-'}",
        detail="detected stamps do not match the range the index records",
    )


def reconcile(
    result: AssignmentResult,
    index: MasterIndex | None,
    *,
    bates_ranges: Mapping[tuple[str, str, int], BatesRange] | None = None,
) -> ReconciliationReport:
    """Build the §5 three-way report from a completed Stage 3b assignment.

    Container children are excluded from the folder-only list: they have no
    index row *by design* (D-04 says so explicitly), so listing them as missing
    would drown the real gaps in noise. They are still counted, and the count is
    reported as a warning line so the omission is visible rather than assumed.
    """
    if index is None:
        return ReconciliationReport(
            matched=(),
            folder_only=(),
            index_only=(),
            snapshot=None,
            root_prefix=None,
            warnings=("no master index was supplied; reconciliation was not run",),
        )

    by_doc_id = result.by_doc_id()
    rows_by_number = {r.row_number: r for r in index.rows}
    ranges = bates_ranges or {}

    matched: list[MatchedPair] = []
    folder_only: list[FolderOnlyEntry] = []
    unassigned: list[str] = []
    child_count = 0

    for doc in sorted(result.documents, key=document_sort_key):
        assignment = by_doc_id.get(doc.doc_id)
        if assignment is None:  # pragma: no cover - assigner covers every doc
            # Unreachable while the assigner assigns every document, which is
            # exactly why it must not be a bare ``continue``: if that invariant
            # ever breaks, a file would vanish from the completeness check that
            # exists to prove nothing vanished.
            unassigned.append(doc.rel_path)
            continue
        if doc.parent_doc_id is not None:
            child_count += 1
            continue
        if assignment.index_row_number is None:
            folder_only.append(
                FolderOnlyEntry(
                    doc_id=doc.doc_id,
                    rel_path=doc.rel_path,
                    filename=doc.filename,
                    ext=doc.ext,
                    size_bytes=doc.size_bytes,
                    sha256=doc.sha256,
                    reason="no master-index row matched this file",
                )
            )
            continue
        row = rows_by_number[assignment.index_row_number]
        rng = ranges.get(document_sort_key(doc))
        found = [
            d
            for d in (
                _name_discrepancy(doc, row),
                _ext_discrepancy(doc, row),
                _size_discrepancy(doc, row),
                _bates_discrepancy(rng, row),
            )
            if d is not None
        ]
        matched.append(
            MatchedPair(
                doc_id=doc.doc_id,
                rel_path=doc.rel_path,
                li_file_no=assignment.li_file_no or row.li_file_no,
                index_row_number=row.row_number,
                match_method=assignment.method,
                discrepancies=tuple(found),
            )
        )

    claimed = set(result.matched_rows)
    # Ordinary unmatched rows first, ordered by the identifier they own; then
    # the quarantined rows, ordered by their position in the sheet because they
    # own no identifier to order by. Both keys are total and derived only from
    # loaded data, so the sequence is byte-stable across runs.
    index_only = tuple(
        IndexOnlyEntry(
            li_file_no=row.li_file_no,
            index_row_number=row.row_number,
            filename=row.filename,
            filepath=row.filepath,
            ext=row.ext,
            size_kb=row.size_kb,
        )
        for row in sorted(index.rows, key=lambda r: (r.original_sort, r.row_number))
        if row.row_number not in claimed
    ) + tuple(
        # D-1: the loader's warning promises these reconcile as index-only.
        # This is where that promise is kept. They carry no LI File No because
        # they never received one.
        IndexOnlyEntry(
            li_file_no="",
            index_row_number=row.row_number,
            filename=row.filename,
            filepath=row.filepath,
            ext=row.ext,
            size_kb=row.size_kb,
            quarantined=True,
            reason=row.detail,
        )
        for row in sorted(index.quarantined, key=lambda r: r.row_number)
    )

    warnings = list(index.warnings) + list(result.warnings)
    if unassigned:  # pragma: no cover - assigner covers every doc
        warnings.append(
            f"{len(unassigned)} folder file(s) carried no identifier assignment "
            "and could not be reconciled at all: "
            + ", ".join(sorted(unassigned)[:5])
        )
    if child_count:
        warnings.append(
            f"{child_count} container member(s) (archive entries, email "
            "attachments) were excluded from the folder-only list: D-04 gives "
            "them parent-derived identifiers and they have no index row by design"
        )
    if result.alignment is not None and not result.alignment.aligned:
        warnings.append(
            "no folder file matched any index path. The scanned folder and the "
            "index may describe different document sets, or the index's Filepath "
            "column may use a naming scheme the folder does not."
        )

    return ReconciliationReport(
        matched=tuple(matched),
        folder_only=tuple(folder_only),
        index_only=index_only,
        snapshot=index.snapshot,
        root_prefix=result.alignment.prefix if result.alignment else None,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# D-04 mitigation (b): warn when a later index snapshot renumbers issued IDs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One identifier, tied to the file it named by content, not by position."""

    doc_id: str
    sha256: str
    rel_path: str
    li_file_no: str | None


@dataclass(frozen=True, slots=True)
class IssuedIdLedger:
    """Every identifier a run issued, persisted so the next run can compare.

    Keyed by SHA-256 first: a renamed or moved file is still the same evidence,
    and if its identifier changed that is exactly the event D-04 wants
    surfaced. The ledger is a durable fingerprinted artifact — it carries the
    hash of its own content, written through the contract's single serializer.
    """

    snapshot: MasterIndexSnapshot | None
    entries: tuple[LedgerEntry, ...]
    contract_version: str
    content_sha256: str = ""

    @staticmethod
    def from_assignment(
        result: AssignmentResult, snapshot: MasterIndexSnapshot | None
    ) -> "IssuedIdLedger":
        from dociq.contracts import CONTRACT_VERSION

        entries = tuple(
            LedgerEntry(
                doc_id=doc.doc_id,
                sha256=doc.sha256,
                rel_path=doc.rel_path,
                li_file_no=doc.li_file_no,
            )
            for doc in sorted(result.documents, key=document_sort_key)
        )
        draft = IssuedIdLedger(
            snapshot=snapshot, entries=entries, contract_version=CONTRACT_VERSION
        )
        return IssuedIdLedger(
            snapshot=snapshot,
            entries=entries,
            contract_version=CONTRACT_VERSION,
            content_sha256=content_hash(draft),
        )

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(canonical_json(self) + "\n", encoding="utf-8", newline="\n")
        return p

    @staticmethod
    def read(path: str | Path) -> "IssuedIdLedger":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        snap = raw.get("snapshot")
        ledger = IssuedIdLedger(
            snapshot=MasterIndexSnapshot(**snap) if snap else None,
            entries=tuple(LedgerEntry(**e) for e in raw.get("entries", ())),
            contract_version=raw.get("contract_version", ""),
            content_sha256=raw.get("content_sha256", ""),
        )
        return ledger

    def is_stale(self) -> bool:
        """True when the retained artifact no longer hashes to its own claim.

        A ledger that has been hand-edited, truncated, or written by an older
        serializer must not be used to reason about renumbering — it would
        produce confident nonsense.
        """
        if not self.content_sha256:
            return True
        draft = IssuedIdLedger(
            snapshot=self.snapshot,
            entries=self.entries,
            contract_version=self.contract_version,
        )
        return content_hash(draft) != self.content_sha256


@dataclass(frozen=True, slots=True)
class RenumberWarning:
    kind: str
    """``id-moved`` (a file's identifier changed) or ``id-reused`` (an
    identifier now names different content)."""

    doc_id: str
    previous_doc_id: str | None
    sha256: str
    rel_path: str
    message: str


def detect_renumbering(
    previous: IssuedIdLedger | None, current: IssuedIdLedger
) -> tuple[RenumberWarning, ...]:
    """Compare two runs' issued identifiers and report every drift (D-04 (b)).

    Both directions are reported. "This file's ID changed" matters because past
    work cites the old one; "this ID now points somewhere else" matters more,
    because a citation to it silently resolves to the wrong document.
    """
    if previous is None:
        return ()
    if previous.is_stale():
        return (
            RenumberWarning(
                kind="ledger-unusable",
                doc_id="",
                previous_doc_id=None,
                sha256="",
                rel_path="",
                message=(
                    "the previous run's issued-ID ledger does not match its own "
                    "content hash and was ignored; renumbering could not be "
                    "checked for this run"
                ),
            ),
        )

    # Matched on (sha256, rel_path) FIRST, and on sha256 alone only where that
    # hash names exactly one previous file.
    #
    # Hash alone is not an identity on a real matter record. Duplicate content
    # is ordinary — the walker detects and reports it, and a file that also
    # appears inside an archive is the same bytes at two paths. Keying a dict by
    # sha256 lets the last twin win, so every *other* twin reads as "this file's
    # identifier changed" on a re-run where nothing changed at all. Measured on
    # the fixture corpus: three phantom id-moved warnings from two consecutive
    # identical runs. A mitigation that cries wolf on every re-run is not a
    # mitigation, and D-04 accepted renumbering as its single biggest risk.
    prev_by_pair = {(e.sha256, e.rel_path): e for e in previous.entries if e.sha256}
    by_hash: dict[str, list[LedgerEntry]] = {}
    for e in previous.entries:
        if e.sha256:
            by_hash.setdefault(e.sha256, []).append(e)
    prev_by_id = {e.doc_id: e for e in previous.entries}
    out: list[RenumberWarning] = []
    for entry in current.entries:
        old = prev_by_pair.get((entry.sha256, entry.rel_path))
        if old is None:
            # Not at the same path any more. A move or a rename is still the
            # same evidence — but only when the hash names one previous file.
            # With twins there is no way to say which one moved, and guessing
            # would manufacture the warning this pass exists to avoid.
            twins = by_hash.get(entry.sha256, ())
            old = twins[0] if len(twins) == 1 else None
        if old is not None and old.doc_id != entry.doc_id:
            out.append(
                RenumberWarning(
                    kind="id-moved",
                    doc_id=entry.doc_id,
                    previous_doc_id=old.doc_id,
                    sha256=entry.sha256,
                    rel_path=entry.rel_path,
                    message=(
                        f"{entry.rel_path} was issued {old.doc_id} against index "
                        f"snapshot {previous.snapshot.filename if previous.snapshot else 'none'} "
                        f"and is {entry.doc_id} now — citations to {old.doc_id} "
                        "refer to this file under the old numbering"
                    ),
                )
            )
        clash = prev_by_id.get(entry.doc_id)
        if clash is not None and clash.sha256 and clash.sha256 != entry.sha256:
            out.append(
                RenumberWarning(
                    kind="id-reused",
                    doc_id=entry.doc_id,
                    previous_doc_id=entry.doc_id,
                    sha256=entry.sha256,
                    rel_path=entry.rel_path,
                    message=(
                        f"{entry.doc_id} named {clash.rel_path} in the previous "
                        f"run and names {entry.rel_path} now — any citation to "
                        f"{entry.doc_id} resolves to a different document"
                    ),
                )
            )
    return tuple(sorted(out, key=lambda w: (w.kind, w.doc_id, w.sha256)))
