"""Measure the three unruled items in ``docs/design/section_taxonomy.md`` §6.

The taxonomy design pass ruled out heading-text regex on measured evidence and
proposed four recognition tiers in its place. Three questions were left open,
and each one decides whether a tier is worth building:

**Q1 — Tier 1 reach.** "40% of the PDFs carry a PDF outline" is quoted from a
36-document sample. This re-measures it over every PDF, and asks the question
the sample could not: not *how many documents* carry an outline, but **how many
pages an outline actually places**. A document whose outline names three
sections in 200 pages is counted as covered by the first figure and is barely
covered at all by the second.

**Q2 — Tier 2 feasibility.** TOC parsing is proposed, unbuilt and unmeasured.
§6 names the specific doubt: printed page numbers may not match PDF page
indices where front matter is unnumbered. This measures that offset directly,
**using the document's own outline as ground truth** — where a PDF carries both
a TOC and an outline, the outline says which physical page a section starts on
and the TOC says which printed number the document gives it. The difference is
the offset, and its *consistency within a document* is what decides whether
Tier 2 can be built at all: a constant offset is correctable, a scattered one
is not.

**Q3 — coverage of the other 60%.** Documents without an outline fall to
Tier 3 page-class rules, which recognize page *kinds*, not report *sections*.
This measures what share of those pages any Tier 3 rule resolves, and — the
number that matters — what share resolves to nothing and would therefore be
kept unconditionally.

Read-only. The corpus is client data: this opens PDFs, counts, and writes
aggregate figures plus generic report vocabulary. No page text, no document
names, and no file is ever written into the corpus tree.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz  # PyMuPDF

# --------------------------------------------------------------------------
# Tier 3 — the measurable page-class rules, transcribed from taxonomy §3.
#
# Each is a structural measurement rather than a reading of what the page says.
# They are duplicated here rather than imported because nothing in the shipped
# build implements them yet — that is the finding, not an oversight.
# --------------------------------------------------------------------------

ACTIVITY_GRID_HEADERS = (
    "ACTIVITY ID",
    "TOTAL FLOAT",
    "REMAINING DURATION",
    "BL PROJECT START",
    "ORIGINAL DURATION",
    "ACTIVITY NAME",
)
DATE_RE = re.compile(r"\b\d{1,2}-[A-Z][a-z]{2}-\d{2}\b")
SCHEDULE_DATE_DENSITY = 12

TOC_RE = re.compile(r"\b(TABLE\s+OF\s+CONTENTS|CONTENTS)\b", re.IGNORECASE)
TOC_OPENING_CHARS = 400

DISTRIBUTION_RE = re.compile(
    r"\b(DISTRIBUTION\s+LIST|CIRCULATION|COPY\s+TO:)", re.IGNORECASE
)

PHOTO_MAX_CHARS = 300
PHOTO_MIN_IMAGE_AREA_SHARE = 0.25

# A TOC line: a label, then leaders or whitespace, then a printed page number.
TOC_LINE_RE = re.compile(r"^(?P<label>.+?)[\s.·—–-]{2,}(?P<page>\d{1,4})\s*$")

# An outline entry that names a POSITION rather than a section.
#
# Found while smoke-testing this probe: PowerPoint-exported PDFs carry a full
# outline whose every entry reads ``Slide Number 33``. Such a document is
# counted as "carries an outline" by the 40% figure and yields nothing a
# section rule can key to — the outline places pages, but it names no section,
# so Tier 1's claim ("authored by the document's creator, not inference")
# is true and useless for it. Measured separately for that reason.
POSITIONAL_OUTLINE_RE = re.compile(
    r"^(SLIDE(\s+NUMBER)?|PAGE|SHEET|SECTION)?[\s\d]*$", re.IGNORECASE
)


def is_positional(label_key: str) -> bool:
    """Whether a normalized outline label names a position, not a section.

    The trailing ``[\\s\\d]*`` matters and the first version of this probe did
    not have it. Normalization turns the outline entry ``5.1`` into ``5 1``,
    which an anchored ``\\d+$`` does not match — so 35 entries naming nothing
    but a section number were counted as substantive names a rule could key to.
    Measured over the corpus the correction moves the count from 150 to 185.
    """
    return bool(POSITIONAL_OUTLINE_RE.fullmatch(label_key.strip()))


def strip_all_numbering(key: str) -> str:
    """Remove every leading numbering component, not just the first.

    ``4.5.1 MARINE ENGINEERING`` normalizes to ``4 5 1 MARINE ENGINEERING`` and
    needs three passes. Counting distinct labels without this inflates the
    vocabulary with numbering variants of the same section: ``4 5 ENGINEERING``
    and ``5 4 ENGINEERING`` are one name, not two.
    """
    previous = None
    while previous != key:
        previous = key
        key = LEADING_NUMBER_RE.sub("", key)
    return key


LEADING_NUMBER_RE = re.compile(r"^(\d+(\.\d+)*|[A-Z])[.)]?\s+")
"""Section numbering at the head of a label: ``3``, ``3.1``, ``3.1.2``, ``A)``.

A TOC prints ``3.1 EXECUTIVE SUMMARY`` where the outline entry is often just
``EXECUTIVE SUMMARY``. Comparing the two verbatim scores a near-total mismatch
that says nothing about the corpus and everything about the comparison — the
first version of this probe paired 3 lines out of 3,484 for exactly that
reason. Stripped, the same corpus pairs 103.
"""


def normalize_label(text: str) -> str:
    """Fold a heading to a comparable key: case, accents and runs of
    whitespace/punctuation removed. Used only to pair a TOC line with the
    outline entry naming the same section."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^A-Za-z0-9]+", " ", folded)
    return folded.strip().upper()


def pairing_key(text: str) -> str:
    """Normalized label with leading section numbering removed.

    Deliberately stops here. A substring match would pair 1,292 of the same
    3,484 lines, and ``PROGRESS`` is a substring of six distinct section names
    in this corpus's vocabulary — a pairing that loose would manufacture
    offsets rather than measure them.
    """
    return LEADING_NUMBER_RE.sub("", normalize_label(text))


@dataclass
class DocMeasure:
    """One document's figures. Deliberately carries no identifying text."""

    pages: int = 0
    has_outline: bool = False
    outline_entries: int = 0
    outline_resolved: int = 0
    """Outline entries whose destination resolves to a real page."""
    outline_pages_placed: int = 0
    """Pages falling inside some outline entry's span."""
    outline_monotonic: bool = True
    outline_positional_entries: int = 0
    """Entries naming a position (``Slide Number 33``) rather than a section."""
    outline_is_substantive: bool = False
    """True when the outline carries at least one entry that names something
    other than a position. The distinction decides whether Tier 1 can key a
    section rule to this document at all."""

    toc_pages: int = 0
    toc_lines_parsed: int = 0
    toc_lines_paired: int = 0
    """TOC lines matched to an outline entry by normalized label."""
    toc_offsets: list[int] = field(default_factory=list)
    """printed number minus physical 1-based page, one per paired line."""

    tier3_classified: Counter = field(default_factory=Counter)
    tier3_unresolved: int = 0


def outline_spans(doc: fitz.Document) -> tuple[list[tuple[str, int]], int, bool]:
    """Return ``(entries, resolved, monotonic)``.

    ``entries`` is ``(title, page0)`` for every outline entry whose destination
    resolves. PyMuPDF reports an unresolved destination as page ``-1``; those
    are counted and dropped rather than coerced, because a destination that
    does not resolve is exactly the case where a lookup would silently place a
    section on the wrong page.
    """
    try:
        raw = doc.get_toc(simple=True)
    except Exception:
        return [], 0, True
    entries: list[tuple[str, int]] = []
    for item in raw:
        if len(item) < 3:
            continue
        _level, title, page1 = item[0], item[1], item[2]
        if not isinstance(page1, int) or page1 < 1:
            continue
        entries.append((str(title), page1 - 1))
    monotonic = all(
        entries[i][1] <= entries[i + 1][1] for i in range(len(entries) - 1)
    )
    return entries, len(entries), monotonic


def pages_placed_by_outline(entries: list[tuple[str, int]], page_count: int) -> int:
    """Pages an outline places **under a name a section rule could key to**.

    A section spans from its own start page to the page before the next entry
    that starts later; the last entry runs to the end of the document. Two
    kinds of page are excluded, and both exclusions are the point of the
    measurement:

    - pages before the first entry — front matter the outline does not claim;
    - pages whose governing entry is *positional* (``Slide Number 33``), which
      the outline places but does not name.

    Counting the second kind is how "40% of PDFs carry an outline" becomes a
    coverage claim the tier cannot support: the outline is present, the page is
    placed, and there is still no section for a rule to match.
    """
    if not entries:
        return 0
    starts = sorted(
        {
            (p, not is_positional(normalize_label(t)))
            for t, p in entries
            if 0 <= p < page_count
        }
    )
    if not starts:
        return 0

    # A page is governed by the LAST entry starting at or before it. Where two
    # entries share a start page, a substantive one wins: the page does carry a
    # name, and the positional sibling does not take it away.
    governing: dict[int, bool] = {}
    for page0, substantive in starts:
        governing[page0] = governing.get(page0, False) or substantive

    placed = 0
    current: bool | None = None
    for index in range(page_count):
        if index in governing:
            current = governing[index]
        if current:
            placed += 1
    return placed


def parse_toc_page(text: str) -> list[tuple[str, int]]:
    """Parse ``label ..... 12`` lines out of one TOC page."""
    out: list[tuple[str, int]] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        m = TOC_LINE_RE.match(stripped)
        if not m:
            continue
        label = m.group("label").strip()
        if len(label) < 3:
            continue
        try:
            printed = int(m.group("page"))
        except ValueError:
            continue
        if printed < 1 or printed > 5000:
            continue
        out.append((label, printed))
    return out


def classify_tier3(
    text: str, image_area_share: float, has_image: bool
) -> str | None:
    """First matching Tier 3 class, or ``None`` when nothing resolves.

    Order matters and is the taxonomy's: the cheapest and least ambiguous
    structural tests run first. ``None`` is the answer that decides Q3 — it is
    a page no Tier 3 rule can speak about, which under the KEEP-by-default
    contract is a page that survives.
    """
    stripped = text.strip()
    if not stripped and not has_image:
        return "blank"
    upper = text.upper()

    if TOC_RE.search(text[:TOC_OPENING_CHARS]):
        return "table_of_contents"
    if DISTRIBUTION_RE.search(text):
        return "distribution_list"

    header_hits = sum(1 for h in ACTIVITY_GRID_HEADERS if h in upper)
    if header_hits >= 2 or len(DATE_RE.findall(text)) >= SCHEDULE_DATE_DENSITY:
        return "schedule_table"

    if (
        len(stripped) < PHOTO_MAX_CHARS
        and has_image
        and image_area_share >= PHOTO_MIN_IMAGE_AREA_SHARE
    ):
        return "photo_figure"

    if not stripped:
        return "image_only"
    return None


def image_area_share(page: fitz.Page) -> tuple[float, bool]:
    """Share of the page covered by embedded raster images, and whether any
    exist. Overlapping images are not de-overlapped; the share is therefore an
    upper bound, which is the safe direction for a rule whose failure mode
    should be KEEP."""
    try:
        rects = [page.get_image_bbox(info) for info in page.get_images(full=True)]
    except Exception:
        return 0.0, False
    if not rects:
        return 0.0, False
    page_area = abs(page.rect.get_area())
    if page_area <= 0:
        return 0.0, True
    covered = sum(abs(r.get_area()) for r in rects if r is not None)
    return min(covered / page_area, 1.0), True


def measure_document(path: Path) -> DocMeasure | None:
    m = DocMeasure()
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    try:
        m.pages = doc.page_count
        entries, resolved, monotonic = outline_spans(doc)
        m.has_outline = bool(entries)
        m.outline_entries = len(entries)
        m.outline_resolved = resolved
        m.outline_monotonic = monotonic
        m.outline_pages_placed = pages_placed_by_outline(entries, doc.page_count)

        outline_by_label: dict[str, int] = {}
        substantive = 0
        for title, page0 in entries:
            key = normalize_label(title)
            if not key:
                continue
            if is_positional(key):
                m.outline_positional_entries += 1
            else:
                substantive += 1
            pkey = pairing_key(title)
            if pkey and pkey not in outline_by_label:
                outline_by_label[pkey] = page0
        m.outline_is_substantive = substantive > 0

        for index in range(doc.page_count):
            try:
                page = doc.load_page(index)
                text = page.get_text() or ""
            except Exception:
                continue

            if TOC_RE.search(text[:TOC_OPENING_CHARS]):
                m.toc_pages += 1
                for label, printed in parse_toc_page(text):
                    m.toc_lines_parsed += 1
                    key = pairing_key(label)
                    if key in outline_by_label:
                        m.toc_lines_paired += 1
                        physical = outline_by_label[key] + 1
                        m.toc_offsets.append(printed - physical)

            if not m.outline_is_substantive:
                share, has_image = image_area_share(page)
                cls = classify_tier3(text, share, has_image)
                if cls is None:
                    m.tier3_unresolved += 1
                else:
                    m.tier3_classified[cls] += 1
        return m
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--project-tokens",
        default="",
        help=(
            "Comma-separated tokens identifying THIS corpus's project (vessel "
            "name, client, yard). Supplied at run time and never compiled into "
            "the tool, because D-24 forbids a template attributable to a "
            "matter and a probe carrying a project's names in its source would "
            "be the first step to one. Used only to COUNT how much of the "
            "outline vocabulary is project-specific."
        ),
    )
    args = ap.parse_args()
    project_tokens = tuple(
        normalize_label(t) for t in args.project_tokens.split(",") if t.strip()
    )

    pdfs = sorted(p for p in args.corpus.rglob("*") if p.suffix.lower() == ".pdf")
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"no PDFs under {args.corpus}", file=sys.stderr)
        return 2

    docs_total = 0
    docs_unreadable = 0
    pages_total = 0

    docs_with_outline = 0
    docs_with_substantive_outline = 0
    pages_in_outlined_docs = 0
    pages_in_substantive_outlined_docs = 0
    outline_pages_placed = 0
    outline_entries_total = 0
    outline_positional_entries = 0
    docs_outline_nonmonotonic = 0
    outline_vocab: Counter = Counter()
    outline_vocab_stripped: Counter = Counter()
    project_labelled: Counter = Counter()

    docs_with_toc = 0
    docs_toc_and_outline = 0
    toc_lines_parsed = 0
    toc_lines_paired = 0
    docs_offset_constant = 0
    docs_offset_scattered = 0
    offset_spread: Counter = Counter()
    constant_offsets: Counter = Counter()

    tier3_pages = 0
    tier3_classified: Counter = Counter()
    tier3_unresolved = 0

    for i, path in enumerate(pdfs, 1):
        if i % 25 == 0 or i == len(pdfs):
            print(f"  {i}/{len(pdfs)}", file=sys.stderr, flush=True)
        m = measure_document(path)
        if m is None:
            docs_unreadable += 1
            continue
        docs_total += 1
        pages_total += m.pages

        if m.has_outline:
            docs_with_outline += 1
            pages_in_outlined_docs += m.pages
            outline_entries_total += m.outline_entries
            outline_positional_entries += m.outline_positional_entries
            if not m.outline_monotonic:
                docs_outline_nonmonotonic += 1
            if m.outline_is_substantive:
                docs_with_substantive_outline += 1
                pages_in_substantive_outlined_docs += m.pages
                outline_pages_placed += m.outline_pages_placed
                try:
                    doc = fitz.open(path)
                    for title, _p in outline_spans(doc)[0]:
                        key = normalize_label(title)
                        if not key or is_positional(key):
                            continue
                        outline_vocab[key] += 1
                        bare = strip_all_numbering(key)
                        if bare and not is_positional(bare):
                            outline_vocab_stripped[bare] += 1
                            if any(tok and tok in bare for tok in project_tokens):
                                project_labelled[bare] += 1
                    doc.close()
                except Exception:
                    pass

        # Anything Tier 1 cannot key a section rule to falls to Tier 3 — which
        # includes a document whose outline names only slide positions.
        if not m.outline_is_substantive:
            tier3_pages += m.pages
            tier3_classified.update(m.tier3_classified)
            tier3_unresolved += m.tier3_unresolved

        if m.toc_pages:
            docs_with_toc += 1
            toc_lines_parsed += m.toc_lines_parsed
            toc_lines_paired += m.toc_lines_paired
            if m.has_outline:
                docs_toc_and_outline += 1
            if m.toc_offsets:
                distinct = set(m.toc_offsets)
                spread = max(m.toc_offsets) - min(m.toc_offsets)
                offset_spread[spread] += 1
                if len(distinct) == 1:
                    docs_offset_constant += 1
                    constant_offsets[m.toc_offsets[0]] += 1
                else:
                    docs_offset_scattered += 1

    report = {
        "corpus_root": str(args.corpus),
        "documents_readable": docs_total,
        "documents_unreadable": docs_unreadable,
        "pages_total": pages_total,
        "Q1_tier1_outline": {
            "documents_with_any_outline": docs_with_outline,
            "documents_with_any_outline_pct": round(
                100.0 * docs_with_outline / docs_total, 3
            )
            if docs_total
            else 0.0,
            "documents_with_SUBSTANTIVE_outline": docs_with_substantive_outline,
            "documents_with_SUBSTANTIVE_outline_pct": round(
                100.0 * docs_with_substantive_outline / docs_total, 3
            )
            if docs_total
            else 0.0,
            "outline_entries_total": outline_entries_total,
            "outline_entries_positional": outline_positional_entries,
            "pages_in_any_outlined_document": pages_in_outlined_docs,
            "pages_in_substantive_outlined_document": pages_in_substantive_outlined_docs,
            "pages_placed_by_a_substantive_outline": outline_pages_placed,
            "pages_placed_pct_of_corpus": round(
                100.0 * outline_pages_placed / pages_total, 3
            )
            if pages_total
            else 0.0,
            "documents_with_nonmonotonic_outline": docs_outline_nonmonotonic,
            "vocabulary_distinct_raw": len(outline_vocab),
            "vocabulary_distinct_numbering_stripped": len(outline_vocab_stripped),
            "vocabulary_top_40_numbering_stripped": (
                outline_vocab_stripped.most_common(40)
            ),
            "vocabulary_labels_carrying_a_project_token": len(project_labelled),
            "vocabulary_project_token_examples": project_labelled.most_common(10),
            "project_tokens_supplied": list(project_tokens),
        },
        "Q2_tier2_toc": {
            "documents_with_a_toc_page": docs_with_toc,
            "documents_with_toc_and_outline": docs_toc_and_outline,
            "toc_lines_parsed": toc_lines_parsed,
            "toc_lines_paired_to_outline": toc_lines_paired,
            "documents_offset_constant": docs_offset_constant,
            "documents_offset_scattered": docs_offset_scattered,
            "constant_offset_values": constant_offsets.most_common(20),
            "offset_spread_histogram": sorted(offset_spread.items()),
        },
        "Q3_tier3_the_other_60pct": {
            "documents_without_a_substantive_outline": docs_total
            - docs_with_substantive_outline,
            "pages_without_a_substantive_outline": tier3_pages,
            "classified": dict(tier3_classified.most_common()),
            "classified_total": sum(tier3_classified.values()),
            "unresolved": tier3_unresolved,
            "unresolved_pct_of_those_pages": round(
                100.0 * tier3_unresolved / tier3_pages, 3
            )
            if tier3_pages
            else 0.0,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
