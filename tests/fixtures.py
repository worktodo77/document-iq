"""Stub contract records for Track B's tests.

Track A implements the contract for real; Track B builds against fixtures it
constructs itself so the two can be developed concurrently without either
waiting on the other. Everything here uses only :mod:`dociq.contracts`.

Hashes are derived from the content deterministically rather than randomly: a
fixture whose hash changes between runs would make a determinism test pass for
the wrong reason.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from dociq.contracts import (
    DocumentRecord,
    PageKind,
    PageRecord,
    ProcessingStatus,
)

__all__ = ["page", "ocr_page", "document", "corpus", "MPR_PAGES"]


def page(page_no: int, text: str = "", **kw) -> PageRecord:
    kind = kw.pop("kind", PageKind.NATIVE if text else PageKind.EMPTY)
    return PageRecord(page_no=page_no, text=text, kind=kind, **kw)


def ocr_page(page_no: int, text: str, conf: float, **kw) -> PageRecord:
    kw.setdefault("ocr_line_count", max(1, len(text.split("\n"))))
    return PageRecord(
        page_no=page_no, text=text, kind=PageKind.OCR, ocr_conf=conf, **kw
    )


def document(
    rel_path: str,
    pages: Sequence[PageRecord] = (),
    *,
    doc_id: str = "",
    sha256: str | None = None,
    size_bytes: int | None = None,
    **kw,
) -> DocumentRecord:
    filename = rel_path.rsplit("/", 1)[-1]
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    body = "\n".join(p.text for p in pages)
    digest = sha256 or hashlib.sha256(f"{rel_path}\n{body}".encode("utf-8")).hexdigest()
    doc = DocumentRecord(
        doc_id=doc_id,
        rel_path=rel_path,
        filename=filename,
        sha256=digest,
        size_bytes=size_bytes if size_bytes is not None else len(body.encode("utf-8")),
        ext=ext,
        pages=tuple(pages),
        status=kw.pop("status", ProcessingStatus.FULL),
        **kw,
    )
    doc.validate()
    return doc


MPR_PAGES = (
    "MONTHLY PROGRESS REPORT\nMODEC FPSO — Contract 4412\nPeriod ending 30 June 2019",
    "EXECUTIVE SUMMARY\nThe project completed 62 percent of planned engineering\n"
    "deliverables during the period. Fabrication remains behind plan.",
    "SCHEDULE STATUS\nActivity\tPlanned\tActual\tVariance\n"
    "Hull fabrication\t42.0\t31.5\t-10.5\nTopsides\t18.0\t18.0\t0.0",
    "HSE STATISTICS\nLost time incidents\t0\nRecordable incidents\t2\n"
    "Man-hours worked\t184,220",
    "PHOTO LOG\nFigure 1 — module M12 under assembly\nFigure 2 — quayside",
    "ORGANISATION CHART\nProject Director\nEngineering Manager\nConstruction Manager",
)
"""A synthetic MPR shaped like the real thing: a cover, prose sections, a
tab-delimited table, and the three section types §6 names as typical DROP
candidates. No client text — every word is written here."""


def corpus(count: int = 3, prefix: str = "MPR") -> tuple[DocumentRecord, ...]:
    """A small synthetic MPR set."""
    docs = []
    for i in range(1, count + 1):
        pages = tuple(page(n, text) for n, text in enumerate(MPR_PAGES, start=1))
        docs.append(document(f"reports/{prefix}-{i:02d}.pdf", pages))
    return tuple(docs)
