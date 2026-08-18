"""Combining the tiers: first tier that resolves a page wins.

`section_taxonomy.md` §3: *"A page is assigned to a section by the first tier
that resolves it, and the tier is recorded per page."* Both halves matter — the
precedence is what stops a page-class guess overriding the document's own
statement about itself, and the recording is what lets an expert reading the log
tell those two apart.
"""

from __future__ import annotations

from typing import Sequence

from dociq.contracts import PageRecord, RecognitionTier
from dociq.sections.model import SectionSpan
from dociq.sections.normalize import family_key
from dociq.sections.tier1_outline import spans_from_outline
from dociq.sections.tier3_pageclass import PageSignals, spans_from_page_classes

__all__ = [
    "overlaps",
    "resolve_sections",
    "section_for_page",
    "spans_from_pages",
]


def resolve_sections(
    *,
    outline: list[tuple[str, int]] | None = None,
    page_count: int = 0,
    signals: list[PageSignals] | None = None,
    project_tokens: tuple[str, ...] = (),
) -> tuple[SectionSpan, ...]:
    """Every span for one document, strongest tier first.

    Returns spans sorted by start page. A page covered by no span has no
    section, which under §1 means it keeps — on the real corpus that is 29.6% of
    pages, and it is a correct outcome rather than a coverage failure.
    """
    tier1 = spans_from_outline(
        outline or [], page_count, project_tokens=project_tokens
    )
    covered = frozenset(
        page for span in tier1 for page in range(span.start_page, span.end_page + 1)
    )
    tier3 = spans_from_page_classes(
        signals or [], project_tokens=project_tokens, covered=covered
    )
    return tuple(sorted(tier1 + tier3, key=lambda s: (s.start_page, s.end_page)))


def section_for_page(spans: tuple[SectionSpan, ...], page_no: int) -> SectionSpan | None:
    """The span governing one page, or ``None``.

    Spans from :func:`resolve_sections` never overlap — Tier 1 collapses entries
    sharing a start page and Tier 3 is handed Tier 1's coverage to skip — so the
    first match is the only match. Asserted rather than assumed: an overlap here
    would mean a page had two sections and the log recorded one of them, which is
    the shape of defect this whole redesign exists to remove.
    """
    for span in spans:
        if span.covers(page_no):
            return span
    return None


def overlaps(spans: tuple[SectionSpan, ...]) -> tuple[tuple[SectionSpan, SectionSpan], ...]:
    """Every pair of spans claiming a page in common.

    Exists so a test can assert emptiness over real documents rather than over a
    fixture someone wrote to pass.
    """
    found = []
    ordered = sorted(spans, key=lambda s: (s.start_page, s.end_page))
    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            if second.start_page > first.end_page:
                break
            found.append((first, second))
    return tuple(found)


def spans_from_pages(
    pages: Sequence[PageRecord],
    *,
    project_tokens: tuple[str, ...] = (),
) -> tuple[SectionSpan, ...]:
    """Rebuild the spans that stamped a document's pages.

    Recognition happens at extraction, where the PDF is open and its outline and
    image geometry are readable; disposition happens at Stage 4, which cannot run
    until Stage 3b has issued the Doc ID a drop-log entry is written against. The
    spans have to cross that gap, and the pages themselves are what carries them:
    :func:`dociq.ingest.walker._record` stamps ``section`` and ``section_tier``
    onto every page a span covered.

    **The page record is the carrier deliberately, and the reason is resume.** A
    side channel from the walk to Stage 4 would be empty for every document
    replayed from the resume cache, so a resumed run would drop nothing where a
    fresh run dropped pages — two corpora from one folder, which is the exact
    Principle-5 break the whole determinism contract exists to forbid. Pages are
    already serialized, and A-18 already round-trips ``section_tier`` through
    :func:`dociq.ingest.walker._page_from_jsonable`.

    A span is a maximal run of consecutive pages sharing a section AND a tier.
    That is the inverse of the stamping, with one bounded loss and one bounded
    merge, both stated here rather than discovered later:

    **``evidence`` is regenerated per tier, not preserved.** For Tier 1 the
    regenerated sentence is identical to the one
    :func:`~dociq.sections.tier1_outline.spans_from_outline` wrote, because that
    sentence is a function of the outline title and the title is the section
    label. For Tier 3 it is not: the original names the measurement that fired
    ("17 dd-Mmm-yy dates on one page") and the rebuilt one names the class that
    matched. §5.4 requires the KIND of evidence to survive per page, and it does
    — the tier is on the record. The instance-level measurement does not, and no
    log line should be read as though it did.

    **Two adjacent spans with the same label and tier rebuild as one.** Two
    outline entries both titled ``APPENDIX``, back to back, were two spans and
    become one. Harmless where it happens: both carry the same family, so both
    reach the same disposition, and a span that merges cannot reach a page
    neither original covered.
    """
    spans: list[SectionSpan] = []
    label: str | None = None
    tier: RecognitionTier | None = None
    start = end = 0

    def flush() -> None:
        nonlocal label
        if label is None or tier is None:
            return
        key = family_key(label, project_tokens)
        if key is not None:
            span = SectionSpan(
                section=label,
                family=key,
                tier=tier,
                start_page=start,
                end_page=end,
                evidence=_rebuilt_evidence(tier, label),
            )
            span.validate()
            spans.append(span)
        label = None

    for page in sorted(pages, key=lambda p: p.page_no):
        if page.section is None or page.section_tier is None:
            flush()
            continue
        if label == page.section and tier is page.section_tier and page.page_no == end + 1:
            end = page.page_no
            continue
        flush()
        label, tier = page.section, page.section_tier
        start = end = page.page_no

    flush()
    return tuple(spans)


_REBUILT_EVIDENCE = {
    RecognitionTier.OUTLINE: "the document's own outline entry {label!r}",
    RecognitionTier.TOC: "the document's own table of contents, naming {label!r}",
    RecognitionTier.PAGE_CLASS: "a page-class rule placed this page in {label!r}",
    RecognitionTier.EXPLICIT: "a page range entered by the expert for {label!r}",
}
"""One sentence per tier, for a span rebuilt from the pages it stamped.

Every tier is spelled out, including the two Sprint 3 does not build. A mapping
that covered only the built tiers would raise — or worse, fall back to a generic
sentence — on the first run after Tier 2 lands, and the failure would surface in
a drop log an expert is reading rather than in a test.
"""


def _rebuilt_evidence(tier: RecognitionTier, label: str) -> str:
    return _REBUILT_EVIDENCE[tier].format(label=label)
