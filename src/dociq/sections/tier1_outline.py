"""Tier 1 — the section map the document's creator wrote into the file.

The strongest tier and the one that does the work: measured over all 298 PDFs of
the real corpus it places **11,173 pages, 63.01%**, from outlines present in only
29.9% of documents. Outlined documents are the large ones, which is why the
document percentage understates the tier by half.

This is a lookup, not an inference. Everything here is arithmetic over
destinations the PDF itself declares; nothing reads a page's words.
"""

from __future__ import annotations

from dociq.contracts import RecognitionTier
from dociq.sections.model import SectionSpan
from dociq.sections.normalize import family_key

__all__ = ["spans_from_outline"]


def spans_from_outline(
    entries: list[tuple[str, int]],
    page_count: int,
    *,
    project_tokens: tuple[str, ...] = (),
) -> tuple[SectionSpan, ...]:
    """Turn ``(title, page0)`` outline entries into page spans.

    ``entries`` are as PyMuPDF's ``get_toc(simple=True)`` gives them, converted
    to 0-based pages, in document order. ``page_count`` bounds the last span.

    Four properties, each measured into existence rather than assumed:

    **A section runs to the next entry that starts LATER, not to the next entry.**
    Outlines nest: `4 ENGINEERING` at page 10 is followed by `4.1 MARINE` also at
    page 10. Ending the parent where the child begins would produce a zero-length
    span and hand the parent's pages to the child. Entries sharing a start page
    are one boundary.

    **Pages before the first entry belong to nobody.** Front matter an outline
    does not claim is not part of the first section — 228 pages of the corpus,
    and under §1 they keep.

    **A positional entry places nothing.** `Slide Number 33` and a bare `5.1`
    name a position, not a section; :func:`family_key` returns ``None`` for them
    and they are skipped. 226 of the corpus's 3,538 entries are of this kind, and
    a template keyed to `5 1` would match a different section every month. The
    pages they govern fall through to Tier 3 exactly as unoutlined pages do.

    **An out-of-order destination is kept as a STOP and never as a section.**
    Four documents in the corpus have a non-monotonic outline. Sorting would
    invent a boundary the document does not assert, so the entry's label is not
    trusted — but the entry is still the document saying "something else begins
    here", and that much is safe to believe.

    **This paragraph used to say the offending entry was ignored and "leaves its
    pages unresolved, which keeps them". That was false, and Codex reproduced
    it.** Discarding the entry removed it from the boundary set, so the span
    before it was ended by the NEXT surviving boundary and ran straight through
    it. On the outline `PROGRESS PHOTOGRAPHS -> 1`, `PROCUREMENT -> 11`,
    `EXECUTIVE SUMMARY -> 5`, the resolver returned `PROGRESS PHOTOGRAPHS` for
    pages 1-10: the executive summary's pages inherited the photograph family,
    and an expert who approved the photographs omission would have dropped them.

    That is the D-35 failure class in span form. A span did not run past its
    stated `end_page` — the end was simply computed from a boundary set that had
    already thrown away the contrary boundary. Kept as a stop, the preceding
    span ends at page 4 and pages 5-10 are resolved by nothing, which is what
    this paragraph always claimed and now does.

    **Where entries share a start page, the DEEPEST one governs.** Outlines are
    depth-first, so the last entry at a given page is the most specific thing the
    document says about it: `4.1 MARINE ENGINEERING` rather than `4 ENGINEERING`.
    Chosen deliberately and not merely to break a tie — the finer label gives the
    expert a finer lever, and an omission of "marine engineering" is a smaller
    and more defensible act than an omission of "engineering". Left overlapping,
    the two spans would have been resolved by whichever happened to be first in
    the list, which is how a span design quietly acquires a carried-state bug.
    """
    # A positional entry is kept as a BOUNDARY and never becomes a span. It is
    # a place the document divides itself without saying what the division is,
    # so the preceding section must stop there: letting it run on would label
    # pages the document never placed in it, which is the "recognizer invents a
    # section" failure §1 forbids and the exact defect D-35 removed. Its own
    # pages fall through to Tier 3 like any unplaced page.
    boundaries: list[tuple[str, str | None, int]] = []
    highest = -1
    for title, page0 in entries:
        if not (0 <= page0 < page_count):
            continue
        if page0 < highest:
            # Non-monotonic: the document contradicts itself about order. The
            # LABEL is not trusted — `None` makes it a boundary that no rule can
            # key to and that becomes no span — but the DESTINATION is kept, so
            # the preceding section stops here instead of running through it.
            #
            # `highest` is deliberately NOT advanced. A second backward entry is
            # judged against the furthest point the outline has genuinely
            # reached, so it is also a stop rather than being silently promoted
            # to a trusted section by the first one's presence.
            boundaries.append((title, None, page0))
            continue
        highest = page0
        boundaries.append((title, family_key(title, project_tokens), page0))

    if not any(key is not None for _t, key, _p in boundaries):
        return ()

    # Collapse entries sharing a start page to the deepest (last) NAMED one, so
    # no two spans can claim the same page. A positional entry never displaces a
    # named one at the same page: it adds no information and would erase some.
    by_start: dict[int, tuple[str, str | None, int]] = {}
    for entry in boundaries:
        existing = by_start.get(entry[2])
        if existing is not None and existing[1] is not None and entry[1] is None:
            continue
        by_start[entry[2]] = entry
    boundaries = [by_start[page0] for page0 in sorted(by_start)]

    spans: list[SectionSpan] = []
    for index, (title, key, page0) in enumerate(boundaries):
        if key is None:
            continue
        end0 = page_count - 1
        for _later_title, _later_key, later_page0 in boundaries[index + 1:]:
            if later_page0 > page0:
                end0 = later_page0 - 1
                break
        if end0 < page0:
            continue
        span = SectionSpan(
            section=title.strip(),
            family=key,
            tier=RecognitionTier.OUTLINE,
            start_page=page0 + 1,
            end_page=end0 + 1,
            evidence=f"the document's own outline entry {title.strip()!r}",
        )
        span.validate()
        spans.append(span)
    return tuple(spans)
