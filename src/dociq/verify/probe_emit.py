"""A PROVISIONAL emitter, built only so determinism can be proven end to end.

**This is not the deliverable emit layer.** ``emit/cleantext.py``,
``emit/indexbook.py`` and ``emit/log.py`` belong to Track B, and at integration
this module is deleted, not merged: two writers of ``clean_text/*.txt`` is
exactly the two-serializers-eventually-disagree failure the contract exists to
prevent.

It exists because the Sprint-1 determinism proof names four artifacts —
``clean_text/*``, ``sources.json``, ``document_index.csv`` and the log's
``content`` section — and a proof that skipped them because their writer lives
in another worktree would prove nothing about the thing the claim is actually
about. So Track A writes the smallest possible stand-ins, with the same shape
and the same inputs, and proves byte-identity over them.

Doc IDs here are positional ``DIQ-`` values assigned in
:func:`~dociq.contracts.document_sort_key` order. Track B's Stage 3b assigner
replaces them wholesale; nothing downstream of this module reads them.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ..contracts import (
    CONTRACT_VERSION,
    DocumentRecord,
    RunResult,
    content_hash,
    document_sort_key,
    to_jsonable,
)

PROVISIONAL = True
"""Read by the selftest, which fails if this module is still present once
``dociq.emit`` exists. The stand-in must not outlive the thing it stands in for.
"""


def _doc_ids(result: RunResult) -> dict[str, str]:
    """``{rel_path: DIQ-nnnnnn}`` in contract order."""
    ordered = sorted(result.documents, key=document_sort_key)
    return {d.rel_path: f"DIQ-{i:06d}" for i, d in enumerate(ordered, 1)}


def _page_marker(page_no: int, bates: str | None) -> str:
    return (f"===== PAGE {page_no} [BATES: {bates}] ====="
            if bates else f"===== PAGE {page_no} =====")


def _clean_text(doc: DocumentRecord) -> str:
    """Markers around normalized page text, LF, no trailing whitespace.

    Dropped pages still get their marker: Principle 2 keeps output page numbers
    aligned to the original document, so removing a page's marker would shift
    every locator after it.
    """
    out: list[str] = []
    for p in doc.pages:
        out.append(_page_marker(p.page_no, p.bates))
        if p.text:
            out.append(p.text)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def write(result: RunResult) -> Path:
    """Write the four artifacts under ``result.config.output_root``."""
    root = Path(result.config.output_root)
    (root / "clean_text").mkdir(parents=True, exist_ok=True)
    ids = _doc_ids(result)

    sources: dict[str, str] = {}
    for doc in sorted(result.documents, key=document_sort_key):
        doc_id = ids[doc.rel_path]
        rel = f"clean_text/{doc_id}.txt"
        (root / rel).write_text(_clean_text(doc), encoding="utf-8", newline="\n")
        sources[doc_id] = rel

    (root / "sources.json").write_text(
        json.dumps(sources, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")

    _write_index(root / "document_index.csv", result, ids)
    _write_log(root / "processing_log.json", result, ids)
    return root


_INDEX_COLUMNS = ("doc_id", "rel_path", "filename", "ext", "date", "pages_in",
                  "pages_kept", "pages_dropped", "status", "sha256",
                  "parent_doc_id", "container_order", "notes")


def _write_index(path: Path, result: RunResult, ids: dict[str, str]) -> None:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_INDEX_COLUMNS)
    for doc in sorted(result.documents, key=document_sort_key):
        w.writerow([
            ids[doc.rel_path], doc.rel_path, doc.filename, doc.ext,
            doc.detected_dates[0] if doc.detected_dates else "",
            doc.pages_in, doc.pages_kept, doc.pages_dropped, doc.status.value,
            doc.sha256, doc.parent_doc_id or "",
            "" if doc.container_order is None else doc.container_order,
            " | ".join(doc.notes)])
    for doc in sorted(result.unsupported, key=document_sort_key):
        w.writerow(["", doc.rel_path, doc.filename, doc.ext, "", 0, 0, 0,
                    doc.status.value, doc.sha256, "", "", doc.error or ""])
    path.write_text(buf.getvalue(), encoding="utf-8", newline="\n")


def _write_log(path: Path, result: RunResult, ids: dict[str, str]) -> None:
    """``content`` is hashed and byte-identical; ``run`` is neither.

    The two sections are written from one structure so the split cannot drift
    apart, and ``run`` deliberately carries the values that make a rerun differ.
    """
    # The config's RUN-IDENTITY projection only. ``output_root`` is where the
    # operator chose to put the deliverables, not an input to what they
    # contain — the determinism contract is "same folder + same profile + same
    # master index". Hashing it made two runs of identical inputs into two
    # different corpora, which is exactly the false negative that trains people
    # to stop trusting the gate. It lives in the ``run`` section instead.
    cfg = to_jsonable(result.config)
    run_only = {"output_root": cfg.pop("output_root")}
    content = {
        "contract_version": CONTRACT_VERSION,
        "config": cfg,
        "documents": [
            {"doc_id": ids[d.rel_path],
             "document": to_jsonable(d),
             "identity_hash": content_hash(d)}
            for d in sorted(result.documents, key=document_sort_key)],
        "unsupported": [to_jsonable(d)
                        for d in sorted(result.unsupported, key=document_sort_key)],
        "warnings": list(result.warnings),
        "totals": {"pages_in": result.pages_in, "pages_kept": result.pages_kept,
                   "pages_dropped": result.pages_dropped},
    }
    payload = {
        "content": content,
        "run": dict(run_only,
                    note="timestamp, operator and host are written here by "
                         "Track B's emit/log.py; excluded from the "
                         "byte-identical claim and from the content hash"),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
