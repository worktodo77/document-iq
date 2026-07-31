"""``run_summary.pdf`` — the one-page, LI-branded human summary (§7).

Deliberately outside the byte-identical claim: a PDF embeds a creation
timestamp. That is stated in the freeze document and in the log's hash scope,
and the page itself says which outputs *are* reproducible, so a reader is never
left guessing which half of the claim they are holding.

The page is assembled with reportlab primitives rather than a document template
because §7 asks for exactly one page: a flowable template would silently spill
onto a second page when a matter has forty unsupported files, and the fix would
be invisible. Lists here are explicitly truncated with a stated remainder — the
full detail is in ``processing_log.json``, and the page says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dociq.contracts import DocumentRecord, document_sort_key
from dociq.emit.paths import OutputLayout
from dociq.verify.tokens import CapacityVerdict, TokenEstimate

__all__ = ["SummaryData", "build_summary_data", "write_run_summary", "DOC_REMEDIATION_HINT"]

DOC_REMEDIATION_HINT = (
    "Legacy .doc files are listed but not read (D-02). To include one, open it "
    "in Word and Save-As DOCX or PDF, then re-run."
)
"""§7 + D-02. Shown whenever a ``.doc`` file appears on the unsupported list —
without it the operator sees a file "not processed" and no way forward."""

_NAVY = (0x0E / 255, 0x4D / 255, 0x80 / 255)
_BLUE = (0x2E / 255, 0x9F / 255, 0xD4 / 255)
_INK = (0.10, 0.11, 0.13)
_MUTED = (0.42, 0.45, 0.50)
_RULE = (0.80, 0.82, 0.85)

_MAX_LISTED = 8
"""How many unsupported files and flagged pages the page names before it says
"and N more". Stated, and the remainder is always printed."""


@dataclass(frozen=True, slots=True)
class SummaryData:
    """Everything the page prints, computed once and testable without a PDF."""

    matter_name: str
    source_root: str
    output_root: str
    generated_at: str
    operator: str
    documents: int
    unsupported: int
    pages_in: int
    pages_kept: int
    pages_dropped: int
    tokens_before: TokenEstimate
    tokens_after: TokenEstimate
    capacity: CapacityVerdict
    flagged_pages: tuple[str, ...]
    flagged_page_total: int
    unsupported_files: tuple[str, ...]
    has_legacy_doc: bool
    id_regime: str
    master_index: str | None
    bates_note: str
    warnings: tuple[str, ...]


def build_summary_data(
    *,
    matter_name: str,
    source_root: str,
    output_root: str,
    generated_at: str,
    operator: str,
    documents: Sequence[DocumentRecord],
    unsupported: Sequence[DocumentRecord],
    tokens_before: TokenEstimate,
    tokens_after: TokenEstimate,
    ocr_threshold_pct: int,
    id_regime: str,
    master_index: str | None = None,
    bates_note: str = "",
    warnings: Sequence[str] = (),
) -> SummaryData:
    docs = sorted(documents, key=document_sort_key)
    flagged: list[str] = []
    for doc in docs:
        for page in doc.pages:
            if page.ocr_conf is None:
                continue
            pct = round(page.ocr_conf * 100)
            if pct < ocr_threshold_pct:
                flagged.append(f"{doc.doc_id} p.{page.page_no} — {pct}% confidence")
    unsupported_sorted = sorted(unsupported, key=document_sort_key)
    listed = tuple(
        f"{d.rel_path} ({d.ext.lstrip('.') or 'no extension'})"
        for d in unsupported_sorted
    )
    return SummaryData(
        matter_name=matter_name,
        source_root=source_root,
        output_root=output_root,
        generated_at=generated_at,
        operator=operator,
        documents=len(docs),
        unsupported=len(unsupported_sorted),
        pages_in=sum(d.pages_in for d in docs),
        pages_kept=sum(d.pages_kept for d in docs),
        pages_dropped=sum(d.pages_dropped for d in docs),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        capacity=tokens_after.capacity(),
        flagged_pages=tuple(flagged[:_MAX_LISTED]),
        flagged_page_total=len(flagged),
        unsupported_files=listed[:_MAX_LISTED],
        has_legacy_doc=any(d.ext.lower() == ".doc" for d in unsupported_sorted),
        id_regime=id_regime,
        master_index=master_index,
        bates_note=bates_note,
        warnings=tuple(warnings),
    )


def _require_reportlab():
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise RuntimeError(
            "Writing run_summary.pdf requires reportlab, a declared dependency "
            "of DocIQ; the installation is incomplete."
        ) from exc
    return LETTER, canvas


def write_run_summary(data: SummaryData, layout: OutputLayout) -> Path:
    """Draw the one-page summary."""
    LETTER, canvas_mod = _require_reportlab()
    width, height = LETTER
    layout.root.mkdir(parents=True, exist_ok=True)
    path = layout.run_summary

    c = canvas_mod.Canvas(str(path), pagesize=LETTER)
    c.setTitle(f"LI Document IQ — run summary — {data.matter_name}".strip(" —"))
    c.setAuthor("Long International, Inc.")
    c.setSubject("Deterministic document-corpus reduction (LI Document IQ)")

    margin = 54
    x = margin
    y = height - margin

    # --- masthead ---------------------------------------------------------
    c.setFillColorRGB(*_NAVY)
    c.rect(0, height - 30, width, 30, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, height - 20, "LONG INTERNATIONAL")
    c.setFont("Helvetica", 9)
    c.drawRightString(width - margin, height - 20, "Document IQ — run summary")

    y = height - 60
    c.setFillColorRGB(*_INK)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(x, y, data.matter_name or "Untitled matter")
    y -= 14
    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(*_MUTED)
    c.drawString(x, y, f"{data.generated_at}  ·  operator {data.operator or 'unknown'}")
    y -= 11
    c.drawString(x, y, f"Source: {_ellipsize(data.source_root, 105)}")
    y -= 11
    c.drawString(x, y, f"Output: {_ellipsize(data.output_root, 105)}")
    y -= 11
    c.drawString(x, y, "Processed entirely offline — no network access at any point.")

    # --- token headline + capacity gauge (D-07) ---------------------------
    y -= 30
    c.setFillColorRGB(*_INK)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(x, y, data.tokens_after.headline)
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(*_MUTED)
    c.drawString(
        x,
        y - 13,
        f"after reduction · was {data.tokens_before.headline} before "
        f"({data.tokens_before.profile.chars:,} characters in, "
        f"{data.tokens_after.profile.chars:,} out)",
    )

    y -= 38
    gauge_w = width - 2 * margin
    gauge_h = 9
    limit = data.capacity.limit or 1
    fill_frac = min(1.0, data.tokens_after.high / limit)
    c.setFillColorRGB(0.90, 0.92, 0.94)
    c.rect(x, y, gauge_w, gauge_h, stroke=0, fill=1)
    c.setFillColorRGB(*_BLUE)
    c.rect(x, y, gauge_w * fill_frac, gauge_h, stroke=0, fill=1)
    c.setStrokeColorRGB(*_NAVY)
    c.setLineWidth(1.2)
    c.line(x + gauge_w, y - 2, x + gauge_w, y + gauge_h + 2)
    c.setFont("Courier", 7.5)
    c.setFillColorRGB(*_MUTED)
    c.drawString(
        x,
        y - 11,
        f"{data.capacity.percent_of_limit_high}% OF DIRECT-CONTEXT CAPACITY "
        f"({limit:,} TOKENS, ASSUMED)",
    )
    y -= 26
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(*_INK)
    y = _paragraph(c, x, y, width - 2 * margin, data.capacity.statement, 9, 11)

    # --- accounting -------------------------------------------------------
    y -= 14
    y = _rule(c, x, y, width - margin)
    y -= 4
    stats = (
        ("Documents processed", f"{data.documents:,}"),
        ("Pages in", f"{data.pages_in:,}"),
        ("Pages kept", f"{data.pages_kept:,}"),
        ("Pages dropped", f"{data.pages_dropped:,}"),
        ("Unsupported files", f"{data.unsupported:,}"),
        ("OCR pages flagged", f"{data.flagged_page_total:,}"),
    )
    col_w = (width - 2 * margin) / 3
    for i, (label, value) in enumerate(stats):
        col = i % 3
        row = i // 3
        cx = x + col * col_w
        cy = y - row * 30
        c.setFont("Helvetica-Bold", 13)
        c.setFillColorRGB(*_INK)
        c.drawString(cx, cy - 12, value)
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(*_MUTED)
        c.drawString(cx, cy - 21, label.upper())
    y -= 60
    y = _rule(c, x, y, width - margin)

    # --- identity ---------------------------------------------------------
    y -= 14
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColorRGB(*_NAVY)
    c.drawString(x, y, "IDENTITY")
    y -= 12
    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(*_INK)
    regime_line = (
        f"Doc ID regime: {data.id_regime}"
        + (f"  ·  master index: {data.master_index}" if data.master_index else "")
    )
    y = _paragraph(c, x, y, width - 2 * margin, regime_line, 8.5, 10)
    if data.bates_note:
        y = _paragraph(c, x, y, width - 2 * margin, data.bates_note, 8.5, 10)

    # --- review queue -----------------------------------------------------
    y -= 8
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColorRGB(*_NAVY)
    c.drawString(x, y, "REQUIRES REVIEW")
    y -= 12
    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(*_INK)
    if not data.flagged_pages and not data.unsupported_files and not data.warnings:
        y = _paragraph(c, x, y, width - 2 * margin, "Nothing flagged.", 8.5, 10)
    for line in data.flagged_pages:
        y = _paragraph(c, x, y, width - 2 * margin, f"· low-confidence OCR: {line}", 8.5, 10)
    if data.flagged_page_total > len(data.flagged_pages):
        y = _paragraph(
            c,
            x,
            y,
            width - 2 * margin,
            f"· and {data.flagged_page_total - len(data.flagged_pages)} further "
            "low-confidence page(s) — full list in processing_log.json",
            8.5,
            10,
        )
    for line in data.unsupported_files:
        y = _paragraph(c, x, y, width - 2 * margin, f"· unsupported: {line}", 8.5, 10)
    if data.unsupported > len(data.unsupported_files):
        y = _paragraph(
            c,
            x,
            y,
            width - 2 * margin,
            f"· and {data.unsupported - len(data.unsupported_files)} further "
            "unsupported file(s) — full list in processing_log.json",
            8.5,
            10,
        )
    for line in data.warnings[:4]:
        y = _paragraph(c, x, y, width - 2 * margin, f"· {line}", 8.5, 10)
    if len(data.warnings) > 4:
        y = _paragraph(
            c,
            x,
            y,
            width - 2 * margin,
            f"· and {len(data.warnings) - 4} further warning(s) — see processing_log.json",
            8.5,
            10,
        )
    if data.has_legacy_doc:
        y -= 4
        c.setFillColorRGB(*_NAVY)
        y = _paragraph(c, x, y, width - 2 * margin, DOC_REMEDIATION_HINT, 8.5, 10)
        c.setFillColorRGB(*_INK)

    # --- footer -----------------------------------------------------------
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*_MUTED)
    c.drawString(
        margin,
        36,
        "Token figures are estimates from a calibrated character ratio, not a "
        "token count. " + data.tokens_after.basis.label + ".",
    )
    c.drawString(
        margin,
        27,
        "Reproducible outputs: clean_text/, sources.json, document_index.csv and "
        "the processing log's content section.",
    )
    c.drawString(
        margin,
        18,
        "This PDF and document_index.xlsx embed a creation time and are therefore "
        "outside the byte-identical claim.",
    )
    c.showPage()
    c.save()
    return path


def _rule(c, x: float, y: float, x2: float) -> float:
    c.setStrokeColorRGB(*_RULE)
    c.setLineWidth(0.5)
    c.line(x, y, x2, y)
    return y


def _ellipsize(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _paragraph(
    c, x: float, y: float, width: float, text: str, size: float, leading: float
) -> float:
    """Wrap to the given width using the canvas's own metrics.

    Hand-wrapped rather than flowed, because §7 says one page and a flowable
    would silently spill onto a second.
    """
    c.setFont("Helvetica", size)
    words = text.split()
    line = ""
    for word in words:
        probe = f"{line} {word}".strip()
        if c.stringWidth(probe, "Helvetica", size) <= width:
            line = probe
            continue
        c.drawString(x, y, line)
        y -= leading
        line = word
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y
