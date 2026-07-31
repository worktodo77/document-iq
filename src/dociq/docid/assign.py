"""Stage 3b — assign a Doc ID to every document (D-04).

Three things have to be true at once, and they pull against each other:

* **Matched files must carry the LI File No.** That is D-04's whole point: the
  identifier an LI analyst already knows is the identifier in the deliverable.
* **The result must be deterministic.** Same folder + same index = same IDs,
  so the assignment cannot depend on filesystem order, dict iteration, or which
  file happened to be hashed first.
* **Nothing may collide.** Acceptance criterion 5.

Determinism comes from doing every pass in :func:`dociq.contracts.document_sort_key`
order and from resolving the folder/index root offset *once*, globally, rather
than per file. Collision-freedom comes from :mod:`dociq.docid.ids`.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from dociq.contracts import (
    ContractViolation,
    DocumentRecord,
    IdRegime,
    document_sort_key,
)
from dociq.docid.ids import (
    CHILD_MIN_WIDTH,
    DocId,
    DocIdMinter,
    IdNamespace,
    base_width_for,
)
from dociq.docid.masterindex import MasterIndex, MasterIndexRow

__all__ = [
    "MatchMethod",
    "IdAssignment",
    "AssignmentResult",
    "RootAlignment",
    "path_key",
    "index_row_key",
    "infer_root_alignment",
    "assign_doc_ids",
]


class MatchMethod(str):
    """How a document reached its identifier. String-valued so it serializes
    into the log without a converter."""

    PATH = "filepath+filename"
    HASH = "sha256"
    BATES = "bates-range"
    PARENT = "parent-derived"
    NONE = "unmatched"


def path_key(*parts: str) -> str:
    """Normalize path fragments into one comparable key.

    Separators unify to ``/``, case folds (Windows paths are case-insensitive
    and the index is typed by hand), Unicode folds to NFC (a filename with a
    composed vs decomposed accent must not read as two different files), and
    empty components collapse. ``.`` and ``..`` are *not* resolved: an index
    cell containing ``..`` is a data-entry error worth surfacing in
    reconciliation rather than silently normalizing away.
    """
    joined = "/".join(p for p in parts if p)
    joined = joined.replace("\\", "/")
    components = [c for c in joined.split("/") if c not in ("", ".")]
    normalized = unicodedata.normalize("NFC", "/".join(components))
    return normalized.casefold().strip()


def index_row_key(row: MasterIndexRow) -> str:
    """The index side of the primary match key.

    The audited Project 495 index stores the *directory* in ``Filepath`` and the
    leaf separately in ``Filename`` — verified: zero of 9,259 rows have a
    Filepath ending in their own Filename. Joining is therefore correct, but the
    join is defensive anyway: if a future index stores the full path, the
    duplicated leaf is dropped rather than doubled.
    """
    directory = path_key(row.filepath)
    leaf = path_key(row.filename)
    if leaf and directory.endswith("/" + leaf):
        return directory
    if leaf and directory == leaf:
        return leaf
    return path_key(row.filepath, row.filename)


@dataclass(frozen=True, slots=True)
class RootAlignment:
    """How the scanned folder sits inside the index's path space.

    The operator points DocIQ at whatever folder they have; the index records
    paths from LI's own root (``P 495 - Qatar - QDCP v. Domopan\\20260521\\...``).
    Rather than matching on filename alone — which is *not* unique in the real
    index (693 duplicate filenames across 9,259 rows) — DocIQ infers the single
    directory prefix that best aligns the two path spaces and then matches full
    paths exactly.
    """

    prefix: str
    matched: int
    candidates_considered: int
    ambiguous: bool = False
    """True when a different prefix scored equally well. The winner is still
    chosen deterministically (shortest, then lexicographically first), but the
    ambiguity is reported: two equally good alignments usually means the folder
    was scanned at a level that appears twice in the index."""

    @property
    def aligned(self) -> bool:
        return self.matched > 0


def _prefix_candidates(index_keys: Iterable[str]) -> set[str]:
    """Every directory prefix appearing in the index, plus the empty prefix.

    Bounded by (rows x path depth); on the real 9,259-row index that is roughly
    40,000 strings, which is cheap. The bound is stated rather than capped: a
    silent cap here would silently mis-align a deep corpus.
    """
    out: set[str] = {""}
    for key in index_keys:
        parts = key.split("/")
        for i in range(1, len(parts)):
            out.add("/".join(parts[:i]))
    return out


def infer_root_alignment(
    index_keys: Sequence[str], folder_keys: Sequence[str]
) -> RootAlignment:
    """Pick the prefix ``P`` maximizing ``|{f : P + '/' + f in index}|``.

    Deterministic tie-break: fewest path components, then lexicographic. Ties
    are reported rather than hidden, because a tie is diagnostic — it means the
    same subtree name occurs at two depths in the index.
    """
    index_set = set(index_keys)
    if not index_set or not folder_keys:
        return RootAlignment(prefix="", matched=0, candidates_considered=0)

    candidates = _prefix_candidates(index_set)
    best_prefix = ""
    best_score = -1
    tie = False
    for prefix in sorted(candidates, key=lambda p: (p.count("/") + (1 if p else 0), p)):
        score = 0
        for key in folder_keys:
            probe = f"{prefix}/{key}" if prefix else key
            if probe in index_set:
                score += 1
        if score > best_score:
            best_prefix, best_score, tie = prefix, score, False
        elif score == best_score and score > 0:
            tie = True
    return RootAlignment(
        prefix=best_prefix,
        matched=max(best_score, 0),
        candidates_considered=len(candidates),
        ambiguous=tie,
    )


@dataclass(frozen=True, slots=True)
class IdAssignment:
    """Why one document has the identifier it has — the per-document audit row."""

    doc_id: str
    sort_key: tuple[str, str, int]
    namespace: str
    method: str
    li_file_no: str | None = None
    index_row_number: int | None = None
    parent_doc_id: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    """Stage 3b output: enriched documents plus the side structures.

    The assignments live *beside* the documents, keyed by ``doc_id``, because
    the frozen contract has no room for them and Track B may not widen it.
    """

    documents: tuple[DocumentRecord, ...]
    regime: IdRegime
    assignments: tuple[IdAssignment, ...]
    alignment: RootAlignment | None
    matched_rows: tuple[int, ...]
    """``row_number`` of every index row claimed by a folder file."""
    unmatched_row_count: int
    warnings: tuple[str, ...] = ()

    def by_doc_id(self) -> dict[str, IdAssignment]:
        return {a.doc_id: a for a in self.assignments}


def _pre_assignment_token(doc: DocumentRecord) -> tuple[str, ...]:
    """Tokens by which a child may name its parent before IDs exist.

    The frozen contract types ``parent_doc_id`` as a Doc ID, but Doc IDs do not
    exist until this stage runs — so a container child produced by Stage 1 must
    reference its parent by *something*. Rather than guess which convention
    Track A picked, both plausible ones resolve: the parent's pre-assigned
    ``doc_id`` if it already carries one, and the parent's ``rel_path``.
    Whichever resolved is recorded in the assignment note.
    """
    tokens = [doc.rel_path]
    if doc.doc_id:
        tokens.append(doc.doc_id)
    return tuple(tokens)


def _child_width(count: int) -> int:
    return max(CHILD_MIN_WIDTH, len(str(count)))


def _bucket(sort_key: tuple[str, str, int]) -> str:
    """Hashable bucket name for a document's children.

    NUL joins the components because it cannot occur in a path, a hex digest or
    a decimal integer — so two distinct sort keys cannot produce one bucket.
    """
    return f"{sort_key[0]}\x00{sort_key[1]}\x00{sort_key[2]}"


def _break_container_cycles(
    docs: Sequence[DocumentRecord],
    children_by_parent: dict[str, list[DocumentRecord]],
    orphans: list[DocumentRecord],
    warnings: list[str],
) -> None:
    """Detach any container membership cycle before numbering recurses into it.

    A cycle cannot arise from a well-formed archive walk, but it *can* arise
    from a malformed ``parent_doc_id`` and it would turn Stage 3b into an
    infinite recursion — a hang is a far worse failure than a warning. Every
    document that is not reachable from a true top-level document is detached
    and identified as top-level, so nothing is lost.
    """
    parent_of: dict[str, str] = {}
    by_bucket: dict[str, DocumentRecord] = {_bucket(document_sort_key(d)): d for d in docs}
    for bucket, members in children_by_parent.items():
        for member in members:
            parent_of[_bucket(document_sort_key(member))] = bucket

    reachable: set[str] = set()
    for bucket in sorted(by_bucket):
        chain: list[str] = []
        cursor: str | None = bucket
        seen: set[str] = set()
        while cursor is not None and cursor not in reachable and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            cursor = parent_of.get(cursor)
        if cursor is not None and cursor not in reachable:
            # Walked back into our own chain: everything in `seen` is in a cycle
            # or hangs off one.
            for member_bucket in sorted(seen):
                doc = by_bucket[member_bucket]
                parent_bucket = parent_of.pop(member_bucket, None)
                if parent_bucket is not None:
                    children_by_parent[parent_bucket] = [
                        m
                        for m in children_by_parent[parent_bucket]
                        if _bucket(document_sort_key(m)) != member_bucket
                    ]
                warnings.append(
                    f"{doc.rel_path}: container membership forms a cycle; the "
                    "member is identified as a top-level file"
                )
                orphans.append(doc)
                reachable.add(member_bucket)
            continue
        reachable.update(chain)


def assign_doc_ids(
    documents: Sequence[DocumentRecord],
    index: MasterIndex | None = None,
    *,
    bates_ranges: Mapping[tuple[str, str, int], tuple[str | None, str | None]] | None = None,
) -> AssignmentResult:
    """Assign every document an identifier.

    Args:
        documents: every record from Stage 1, containers and children together.
        index: the master index, or ``None`` for the DIQ-native regime.
        bates_ranges: optional per-document ``(start, end)`` Bates range keyed by
            :func:`dociq.contracts.document_sort_key`, used only as the tertiary
            match key and only when the index carries Bates columns.

    Matching runs in three passes over the whole corpus rather than three
    attempts per document, so a weaker key can never steal a row that a stronger
    key would have claimed for a different file.
    """
    docs = sorted(documents, key=document_sort_key)
    warnings: list[str] = []
    minter = DocIdMinter()

    parents: list[DocumentRecord] = []
    children_by_parent: dict[str, list[DocumentRecord]] = defaultdict(list)
    orphans: list[DocumentRecord] = []

    # Nested containers are real (a zip inside a zip), so the parent lookup
    # covers every document, not only top-level ones.
    token_to_sortkey: dict[str, tuple[str, str, int]] = {}
    for doc in docs:
        for token in _pre_assignment_token(doc):
            token_to_sortkey.setdefault(token, document_sort_key(doc))

    for doc in docs:
        if doc.parent_doc_id is None:
            parents.append(doc)
            continue
        target = token_to_sortkey.get(doc.parent_doc_id)
        if target is None:
            warnings.append(
                f"{doc.rel_path}: parent {doc.parent_doc_id!r} is not among the "
                "scanned documents; the member is identified as a top-level file "
                "so it is still accounted for"
            )
            orphans.append(doc)
            continue
        if target == document_sort_key(doc):
            warnings.append(
                f"{doc.rel_path}: names itself as its own container; treated as a "
                "top-level file"
            )
            orphans.append(doc)
            continue
        children_by_parent[_bucket(target)].append(doc)

    _break_container_cycles(docs, children_by_parent, orphans, warnings)

    top_level = sorted(parents + orphans, key=document_sort_key)
    orphan_keys = {document_sort_key(d) for d in orphans}

    # ---- pass 0: align the two path spaces ---------------------------------
    alignment: RootAlignment | None = None
    row_by_key: dict[str, MasterIndexRow] = {}
    row_by_hash: dict[str, MasterIndexRow] = {}
    rows_by_bates: dict[tuple[str, str], MasterIndexRow] = {}
    if index is not None:
        for row in index.rows:
            row_by_key.setdefault(index_row_key(row), row)
        duplicate_keys = len(index.rows) - len(row_by_key)
        if duplicate_keys:
            warnings.append(
                f"master index: {duplicate_keys} row(s) repeat a filepath+filename "
                "key; the lowest Original Sort wins and the rest reconcile as "
                "index-only"
            )
        alignment = infer_root_alignment(
            list(row_by_key), [path_key(d.rel_path) for d in top_level]
        )
        if alignment.ambiguous:
            warnings.append(
                f"master index: the folder root aligns equally well at more than "
                f"one point in the index; {alignment.prefix!r} was chosen "
                "(shortest, then alphabetical). Check that the scanned folder is "
                "the one the index describes."
            )
        for row in index.rows:
            if row.sha256:
                row_by_hash.setdefault(row.sha256, row)
            if row.bates_start and row.bates_end:
                rows_by_bates.setdefault((row.bates_start, row.bates_end), row)

    # ---- passes 1-3: claim index rows --------------------------------------
    claimed_rows: dict[int, tuple[str, str, int]] = {}
    match_for: dict[tuple[str, str, int], tuple[MasterIndexRow, str]] = {}

    def claim(doc: DocumentRecord, row: MasterIndexRow, method: str) -> bool:
        key = document_sort_key(doc)
        if row.row_number in claimed_rows:
            other = claimed_rows[row.row_number]
            warnings.append(
                f"{doc.rel_path}: master-index row {row.row_number} "
                f"(Original Sort {row.original_sort}) was already claimed by "
                f"{other[0]!r} via a stronger key; this file takes a DIQ "
                "identifier instead"
            )
            return False
        claimed_rows[row.row_number] = key
        match_for[key] = (row, method)
        return True

    if index is not None:
        prefix = alignment.prefix if alignment else ""
        for doc in top_level:
            probe = path_key(prefix, doc.rel_path) if prefix else path_key(doc.rel_path)
            row = row_by_key.get(probe)
            if row is not None:
                claim(doc, row, MatchMethod.PATH)
        if row_by_hash:
            for doc in top_level:
                if document_sort_key(doc) in match_for:
                    continue
                row = row_by_hash.get((doc.sha256 or "").lower())
                if row is not None:
                    claim(doc, row, MatchMethod.HASH)
        if rows_by_bates and bates_ranges:
            for doc in top_level:
                key = document_sort_key(doc)
                if key in match_for:
                    continue
                rng = bates_ranges.get(key)
                if not rng or not rng[0] or not rng[1]:
                    continue
                row = rows_by_bates.get((rng[0], rng[1]))
                if row is not None:
                    claim(doc, row, MatchMethod.BATES)

    # ---- mint identifiers ---------------------------------------------------
    li_width = base_width_for(
        IdNamespace.LI, index.max_original_sort if index is not None else 0
    )
    unmatched = [d for d in top_level if document_sort_key(d) not in match_for]
    diq_width = base_width_for(IdNamespace.DIQ, max(len(unmatched), 1))

    assignments: list[IdAssignment] = []
    out_by_key: dict[tuple[str, str, int], DocumentRecord] = {}
    docid_by_key: dict[tuple[str, str, int], DocId] = {}
    diq_counter = 0

    for doc in top_level:
        key = document_sort_key(doc)
        matched = match_for.get(key)
        note = None
        if key in orphan_keys:
            note = "container member whose parent was not scanned"
        if matched is not None:
            row, method = matched
            did = DocId(IdNamespace.LI, row.original_sort, li_width)
            rendered = minter.mint(did)
            docid_by_key[key] = did
            out_by_key[key] = _with_id(doc, rendered, row.li_file_no)
            assignments.append(
                IdAssignment(
                    doc_id=rendered,
                    sort_key=key,
                    namespace=IdNamespace.LI.value,
                    method=method,
                    li_file_no=row.li_file_no,
                    index_row_number=row.row_number,
                    note=note,
                )
            )
        else:
            diq_counter += 1
            did = DocId(IdNamespace.DIQ, diq_counter, diq_width)
            rendered = minter.mint(did)
            docid_by_key[key] = did
            out_by_key[key] = _with_id(doc, rendered, None)
            assignments.append(
                IdAssignment(
                    doc_id=rendered,
                    sort_key=key,
                    namespace=IdNamespace.DIQ.value,
                    method=MatchMethod.NONE,
                    note=note,
                )
            )

    # ---- children inherit their parent's identifier -------------------------
    for doc in top_level:
        key = document_sort_key(doc)
        bucket = children_by_parent.get(_bucket(key))
        if not bucket:
            continue
        _assign_children(
            parent_id=docid_by_key[key],
            parent_rendered=out_by_key[key].doc_id,
            members=bucket,
            children_by_parent=children_by_parent,
            minter=minter,
            out_by_key=out_by_key,
            assignments=assignments,
            warnings=warnings,
        )

    ordered = tuple(sorted(out_by_key.values(), key=document_sort_key))
    if len(ordered) != len(docs):
        raise ContractViolation(
            f"Stage 3b lost documents: {len(docs)} in, {len(ordered)} out"
        )

    regime = IdRegime.MASTER_INDEX if index is not None else IdRegime.NATIVE
    return AssignmentResult(
        documents=ordered,
        regime=regime,
        assignments=tuple(sorted(assignments, key=lambda a: a.sort_key)),
        alignment=alignment,
        matched_rows=tuple(sorted(claimed_rows)),
        unmatched_row_count=(len(index.rows) - len(claimed_rows)) if index else 0,
        warnings=tuple(warnings),
    )


def _with_id(
    doc: DocumentRecord, doc_id: str, li_file_no: str | None
) -> DocumentRecord:
    """Build the enriched record. Never mutates — the dataclass is frozen."""
    return replace(doc, doc_id=doc_id, li_file_no=li_file_no)


def _assign_children(
    *,
    parent_id: DocId,
    parent_rendered: str,
    members: Sequence[DocumentRecord],
    children_by_parent: Mapping[str, list[DocumentRecord]],
    minter: DocIdMinter,
    out_by_key: dict[tuple[str, str, int], DocumentRecord],
    assignments: list[IdAssignment],
    warnings: list[str],
) -> None:
    """Number one container's members, then recurse into nested containers.

    Ordered by ``container_order``, which ``DocumentRecord.validate()``
    guarantees is present for any record with a parent. Two members sharing a
    ``container_order`` would make numbering ambiguous, so it is reported and
    broken by the canonical sort rather than left to dict order.
    """
    ordered = sorted(members, key=lambda d: (d.container_order or 0, document_sort_key(d)))
    seen_orders: dict[int, str] = {}
    width = _child_width(len(ordered))
    for position, member in enumerate(ordered, start=1):
        order = member.container_order or 0
        if order in seen_orders:
            warnings.append(
                f"{member.rel_path}: container_order {order} repeats "
                f"{seen_orders[order]!r} inside {parent_rendered}; members are "
                "numbered by the canonical document order instead"
            )
        else:
            seen_orders[order] = member.rel_path
        child = parent_id.child(position, width)
        rendered = minter.mint(child)
        key = document_sort_key(member)
        out_by_key[key] = _with_id(member, rendered, None)
        assignments.append(
            IdAssignment(
                doc_id=rendered,
                sort_key=key,
                namespace=child.namespace.value,
                method=MatchMethod.PARENT,
                parent_doc_id=parent_rendered,
            )
        )
        nested = children_by_parent.get(_bucket(key))
        if nested:
            _assign_children(
                parent_id=child,
                parent_rendered=rendered,
                members=nested,
                children_by_parent=children_by_parent,
                minter=minter,
                out_by_key=out_by_key,
                assignments=assignments,
                warnings=warnings,
            )
