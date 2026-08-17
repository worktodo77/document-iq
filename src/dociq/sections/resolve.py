"""Combining the tiers: first tier that resolves a page wins.

`section_taxonomy.md` §3: *"A page is assigned to a section by the first tier
that resolves it, and the tier is recorded per page."* Both halves matter — the
precedence is what stops a page-class guess overriding the document's own
statement about itself, and the recording is what lets an expert reading the log
tell those two apart.
"""

from __future__ import annotations

from dociq.sections.model import SectionSpan
from dociq.sections.tier1_outline import spans_from_outline
from dociq.sections.tier3_pageclass import PageSignals, spans_from_page_classes

__all__ = ["resolve_sections", "section_for_page"]


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
