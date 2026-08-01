"""Text normalization and page-record construction.

Every ``PageRecord.text`` in the pipeline passes through :func:`normalize`
before the record is built, so ``content_hash`` is computed over normalized
text and a rerun cannot drift on encoding alone
(``docs/contracts/pagemodel_freeze.md`` §Normalization).

Construction goes through :func:`make_page` rather than ``PageRecord(...)``
directly. The contract has invariants that couple fields — an OCR page must
carry ``ocr_conf``, a non-OCR page must not — and a constructor that derives
the kind from the text it was given cannot violate them by omission. That is
correct-by-construction rather than a validate-and-hope.
"""

from __future__ import annotations

import re
import unicodedata

from ..contracts import PageKind, PageRecord

# U+200B..U+200D (ZWSP/ZWNJ/ZWJ) and U+FEFF (BOM as it appears mid-stream).
# Removed BEFORE NFC: removing a joiner can bring two characters into contact
# that NFC would then compose, so removing after normalizing would leave text
# that a second normalize() pass changes again — i.e. not idempotent.
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")

_NBSP = "\u00a0"

# Three or more consecutive newlines (i.e. two or more blank lines) → two.
_BLANK_RUN = re.compile(r"\n{3,}")


def normalize(text: str) -> str:
    """Apply the frozen normalization table. Idempotent.

    Order is load-bearing: character removal and substitution first, then NFC,
    then line handling. Any other order has a case where a second application
    changes the result.
    """
    if not text:
        return ""
    s = _ZERO_WIDTH.sub("", text)
    s = s.replace(_NBSP, " ")
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = _BLANK_RUN.sub("\n\n", s)
    return s.strip("\n")


def ocr_stats(confidences: list[float], threshold: float) -> tuple[float, int, int]:
    """Aggregate per-line OCR confidences into the contract's three fields.

    Returns ``(mean rounded to 4dp, line_count, low_confidence_line_count)``.
    The rounding happens here and only here: ``ocr_conf`` reaches disk, and an
    unrounded float would make the byte-identical claim hostage to the last
    bits of an ONNX softmax.
    """
    n = len(confidences)
    if n == 0:
        return 0.0, 0, 0
    mean = round(sum(confidences) / n, 4)
    # Clamp: a mean of 1.00000000000002 from float addition would fail the
    # contract's [0,1] check for a reason that has nothing to do with OCR.
    mean = min(1.0, max(0.0, mean))
    low = sum(1 for c in confidences if c < threshold)
    return mean, n, low


def make_page(
    page_no: int,
    text: str,
    kind: PageKind,
    *,
    confidences: list[float] | None = None,
    conf_threshold: float = 0.85,
    notes: tuple[str, ...] = (),
) -> PageRecord:
    """Build one validated page record.

    ``kind`` is the *route* the page took; the returned record's kind may be
    :attr:`PageKind.EMPTY` instead. A page whose normalized text is empty
    carries no recoverable text whatever route produced it, and EMPTY is the
    only kind the contract permits to have no ``ocr_conf`` — so deriving it
    here is what keeps an OCR page that recovered nothing from becoming an
    unvalidatable record.
    """
    clean = normalize(text)
    extra: tuple[str, ...] = ()

    if kind is PageKind.OCR:
        conf, n_lines, n_low = ocr_stats(confidences or [], conf_threshold)
        if not clean:
            # Routed to OCR, recovered nothing. Disclosure, never silence.
            extra = ("ocr: no text recovered",)
            record_kind, record_conf = PageKind.EMPTY, None
        else:
            record_kind, record_conf = PageKind.OCR, conf
    else:
        n_lines = n_low = 0
        record_conf = None
        record_kind = kind if clean else PageKind.EMPTY

    page = PageRecord(
        page_no=page_no,
        text=clean,
        kind=record_kind,
        ocr_conf=record_conf,
        ocr_line_count=n_lines,
        ocr_low_conf_lines=n_low,
        notes=notes + extra,
    )
    page.validate()
    return page


def synthetic_pages(
    blocks: list[str], *, notes: tuple[str, ...] = ()
) -> list[PageRecord]:
    """Build SYNTHETIC pages from a format with no physical pagination.

    ``blocks`` is one entry per natural division the source *does* have — a
    worksheet, a slide, or the whole body when it has none. An empty block is
    still a page: Principle 1 accounts for it rather than dropping it, which is
    also what keeps the ordinals aligned with the sheet/slide numbering.

    A body with no divisions becomes ONE page, never a character-budget split.
    Principle 2 requires a locator to reference the original document, and a
    DOCX has no page 3 anyone can turn to; inventing one would put a number in
    a marker that the source cannot corroborate.
    """
    if not blocks:
        blocks = [""]
    return [
        make_page(i, b, PageKind.SYNTHETIC, notes=notes)
        for i, b in enumerate(blocks, 1)
    ]
