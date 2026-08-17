"""Tier 3 — properties of a page, never a reading of what it says.

`section_taxonomy.md` §3 permits these and only these, and the reason is §1: a
recognizer whose behavior cannot be stated as a rule cannot be approved in
advance and cannot be explained in cross-examination. Every test below is a
structural measurement — a count, an area, a repetition — and every one of them
is a sentence an expert can read before the run.

**What this tier is, measured.** Over the 209 corpus documents with no usable
outline (6,331 pages) these rules resolve **1,308 pages, 20.66%**, and leave
**79.34% resolved by nothing**. That settles what Tier 3 is: a supplement, not a
fallback that catches the corpus. Four pages in five, in exactly the documents
that need it most, get no section — and under §1 that is the safe direction,
because a page with no section keeps.

**No classifier, no model, no scoring** (§1). If a threshold below looks like it
wants tuning, the honest move is to measure it against hand-checked ground truth
and record the result, not to nudge it until a corpus looks tidier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dociq.contracts import RecognitionTier
from dociq.sections.model import SectionSpan
from dociq.sections.normalize import family_key

__all__ = ["PageSignals", "spans_from_page_classes"]

ACTIVITY_GRID_HEADERS = (
    "ACTIVITY ID",
    "ACTIVITY NAME",
    "TOTAL FLOAT",
    "REMAINING DURATION",
    "ORIGINAL DURATION",
    "BL PROJECT START",
)
"""Column headers of a P6 activity grid pasted into a report (§3).

Two must be present. One is not enough: `ACTIVITY NAME` alone appears in prose
and in a narrative's own subheadings.
"""

_DATE_RE = re.compile(r"\b\d{1,2}-[A-Z][a-z]{2}-\d{2}\b")
SCHEDULE_DATE_DENSITY = 12
"""§3's alternative signature: a grid of dates is a schedule even when its
headers were lost to a column break."""

_TOC_RE = re.compile(r"\b(TABLE\s+OF\s+CONTENTS|CONTENTS)\b", re.IGNORECASE)
TOC_OPENING_CHARS = 400
"""`CONTENTS` must appear in the page's OPENING region, not anywhere on it — a
narrative that mentions the contents of a shipment is not a contents page."""

_DISTRIBUTION_RE = re.compile(
    r"\b(DISTRIBUTION\s+LIST|CIRCULATION|COPY\s+TO:)", re.IGNORECASE
)

PHOTO_MAX_CHARS = 300
PHOTO_MIN_IMAGE_AREA_SHARE = 0.25
"""A photo page is *text below a threshold* AND *substantial image area* (§3).

Both halves are load-bearing. Image area alone matches a page of narrative under
a letterhead logo; low text alone matches a section divider.
"""


@dataclass(frozen=True, slots=True)
class PageSignals:
    """The measurements one page offers, gathered where the file is open.

    A separate record rather than fields on ``PageRecord`` because none of this
    is evidence — it is what the rules below consume to decide, and it does not
    belong in the frozen contract or on disk.
    """

    page_no: int
    text: str
    has_image: bool = False
    image_area_share: float = 0.0
    """Share of the page covered by embedded raster images. Overlaps are not
    subtracted, so this is an UPPER bound — the safe direction for a rule whose
    failure mode should be KEEP, because overstating image area can only make a
    page look more like a photo page, and photo pages are OFFERED, never dropped
    automatically."""


def classify(signals: PageSignals) -> tuple[str, str] | None:
    """``(section label, evidence)`` for the first class that matches, else
    ``None``.

    Order is §3's and is cheapest-and-least-ambiguous first. ``None`` is the
    answer for 79.34% of the pages this tier sees, and it is the answer that
    keeps a page.
    """
    text = signals.text
    stripped = text.strip()
    upper = text.upper()

    if not stripped and not signals.has_image:
        return ("Blank page", "the page has no text and no image")

    if _TOC_RE.search(text[:TOC_OPENING_CHARS]):
        return ("Table of contents", "'CONTENTS' in the page's opening region")

    if _DISTRIBUTION_RE.search(text):
        return (
            "Distribution list",
            "a distribution, circulation or copy-to heading",
        )

    header_hits = sum(1 for h in ACTIVITY_GRID_HEADERS if h in upper)
    if header_hits >= 2:
        return (
            "Schedule / activity table",
            f"{header_hits} activity-grid column headers on the page",
        )
    dates = len(_DATE_RE.findall(text))
    if dates >= SCHEDULE_DATE_DENSITY:
        return (
            "Schedule / activity table",
            f"{dates} dd-Mmm-yy dates on one page",
        )

    if (
        len(stripped) < PHOTO_MAX_CHARS
        and signals.has_image
        and signals.image_area_share >= PHOTO_MIN_IMAGE_AREA_SHARE
    ):
        return (
            "Photograph / figure page",
            f"{len(stripped)} characters of text with "
            f"{signals.image_area_share:.0%} of the page covered by an image",
        )

    if not stripped:
        return ("Image-only page", "the page has an image and no extractable text")

    return None


def spans_from_page_classes(
    signals: list[PageSignals],
    *,
    project_tokens: tuple[str, ...] = (),
    covered: frozenset[int] = frozenset(),
) -> tuple[SectionSpan, ...]:
    """Page classes for every page a stronger tier did not resolve.

    ``covered`` is the set of 1-based page numbers Tier 1 already placed. §3's
    rule is that a page is assigned by **the first tier that resolves it**, so
    this tier never re-labels a page the document's own outline placed — the
    outline is the stronger evidence and re-labelling it would be a downgrade
    the log could not show.

    Adjacent pages of the same class merge into one span, because a run of
    fourteen photograph pages is one section of the report rather than fourteen.
    A merge never crosses a page a stronger tier placed: the gap breaks the run.
    """
    spans: list[SectionSpan] = []
    run_label: str | None = None
    run_evidence = ""
    run_start = 0
    run_end = 0

    def flush() -> None:
        nonlocal run_label
        if run_label is None:
            return
        key = family_key(run_label, project_tokens)
        if key is not None:
            span = SectionSpan(
                section=run_label,
                family=key,
                tier=RecognitionTier.PAGE_CLASS,
                start_page=run_start,
                end_page=run_end,
                evidence=run_evidence,
            )
            span.validate()
            spans.append(span)
        run_label = None

    for signal in sorted(signals, key=lambda s: s.page_no):
        if signal.page_no in covered:
            flush()
            continue
        hit = classify(signal)
        if hit is None:
            flush()
            continue
        label, evidence = hit
        if run_label == label and signal.page_no == run_end + 1:
            run_end = signal.page_no
            continue
        flush()
        run_label, run_evidence = label, evidence
        run_start = run_end = signal.page_no

    flush()
    return tuple(spans)
