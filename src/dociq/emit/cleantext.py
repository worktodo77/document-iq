"""``clean_text/<doc_id>.txt`` and ``sources.json`` (§7).

**This module is the only place a page marker is rendered.** The contract freeze
states it explicitly, and the reason is not tidiness: if an extractor embedded
``===== PAGE 4 =====`` in page text, the marker would be hashed as content, and
a Stage-4 drop of page 4 would leave behind a marker asserting a page that is no
longer there. Markers are presentation, produced once, at the end.

Page numbers in the markers are the ORIGINAL document's (Principle 2). Dropped
pages are omitted from the output, and their numbers are simply absent — a
reader seeing ``PAGE 11`` follow ``PAGE 8`` is being told the truth about what
was removed, which is exactly what the processing log then attributes to a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dociq.contracts import (
    ContractViolation,
    Disposition,
    DocumentRecord,
    canonical_json,
    document_sort_key,
)
from dociq.emit.paths import OutputLayout, write_text_deterministic

__all__ = [
    "page_marker",
    "render_document",
    "CleanTextResult",
    "write_clean_text",
    "write_sources_json",
]

MARKER_PREFIX = "===== PAGE "
MARKER_SUFFIX = " ====="


def page_marker(page_no: int, bates: str | None = None) -> str:
    """``===== PAGE 12 [BATES: MNFV 000391] =====`` — Bates segment only when
    detected (§7)."""
    if page_no < 1:
        raise ContractViolation(f"page marker requires a 1-based page, got {page_no}")
    stamp = f" [BATES: {bates.strip()}]" if bates and bates.strip() else ""
    return f"{MARKER_PREFIX}{page_no}{stamp}{MARKER_SUFFIX}"


def render_document(doc: DocumentRecord) -> str:
    """Render one document's kept pages to its clean-text body.

    A kept page with no text still gets its marker: ``PageKind.EMPTY`` pages are
    pages (Principle 1), and dropping the marker would silently renumber the
    reader's mental model of the document.
    """
    blocks: list[str] = []
    for page in doc.pages:
        if page.disposition is Disposition.DROP:
            continue
        body = page.text.strip("\n")
        blocks.append(page_marker(page.page_no, page.bates) + ("\n" + body if body else ""))
    return "\n\n".join(blocks) + "\n" if blocks else ""


@dataclass(frozen=True, slots=True)
class CleanTextResult:
    sources: tuple[tuple[str, str], ...]
    """``(doc_id, path relative to the matter root)``, in canonical document
    order. A tuple of pairs rather than a dict so the order is part of the
    value and cannot be lost."""

    documents_written: int
    documents_empty: int
    """Documents whose every page was dropped, or which had no pages. They are
    still listed in ``sources.json`` — an empty file is evidence that the
    document existed and was reduced to nothing, which is not the same as the
    document being absent."""

    total_chars: int

    def as_mapping(self) -> dict[str, str]:
        return dict(self.sources)


def write_clean_text(
    documents: Sequence[DocumentRecord], layout: OutputLayout
) -> CleanTextResult:
    """Write one text file per document, in canonical order.

    A repeated ``doc_id`` is a hard failure, not a last-writer-wins overwrite:
    losing a document to a silent overwrite is precisely the class of failure
    Principle 1 and acceptance criterion 5 exist to exclude.
    """
    layout.ensure()
    seen: dict[str, str] = {}
    sources: list[tuple[str, str]] = []
    empty = 0
    total_chars = 0

    for doc in sorted(documents, key=document_sort_key):
        if not doc.doc_id:
            raise ContractViolation(
                f"{doc.rel_path}: clean text cannot be written before Stage 3b "
                "has assigned a Doc ID"
            )
        prior = seen.get(doc.doc_id)
        if prior is not None:
            raise ContractViolation(
                f"Doc ID {doc.doc_id!r} is used by both {prior!r} and "
                f"{doc.rel_path!r}; writing both would lose one document"
            )
        seen[doc.doc_id] = doc.rel_path

        body = render_document(doc)
        if not body:
            empty += 1
        total_chars += len(body)
        path = layout.clean_text_file(doc.doc_id)
        write_text_deterministic(path, body)
        sources.append((doc.doc_id, path.relative_to(layout.root).as_posix()))

    return CleanTextResult(
        sources=tuple(sources),
        documents_written=len(sources),
        documents_empty=empty,
        total_chars=total_chars,
    )


def write_sources_json(result: CleanTextResult, layout: OutputLayout) -> Path:
    """``{doc_id: clean_text_path}`` — read directly by Expert Assist (§7).

    Written through the contract's canonical serializer so it lands inside the
    byte-identical claim alongside the text files it points at.
    """
    payload = {doc_id: path for doc_id, path in result.sources}
    return write_text_deterministic(
        layout.sources_json, canonical_json(payload) + "\n"
    )
