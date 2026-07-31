"""Candidate section-header detection across a sample (§6 profiling run, step 2).

This module *proposes*; it never disposes. Everything it returns is a candidate
for a human checklist, and the checklist's default is KEEP (§6 step 3). Nothing
here writes a disposition, and nothing here is used at run time — Stage 4
consumes a ruled profile, not a detection.

The heuristics are deliberately shallow and mechanical (line shape, position,
recurrence across documents). A smarter detector would start inferring what a
section *means*, which is the boundary §1 draws around this tool.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from dociq.contracts import DocumentRecord, document_sort_key

__all__ = [
    "DetectionLimits",
    "SectionCandidate",
    "DetectionResult",
    "looks_like_header",
    "normalize_label",
    "detect_candidate_sections",
]


@dataclass(frozen=True, slots=True)
class DetectionLimits:
    """Every bound the detector applies, in one place and reported in the result.

    They are surfaced rather than buried because a bound that silently discards
    a real section header is indistinguishable, from the operator's chair, from
    a format that has no sections.
    """

    max_header_chars: int = 90
    """A section header is a label, not a sentence. Longer lines are body text."""

    min_header_chars: int = 3
    max_header_words: int = 12
    scan_lines_per_page: int = 0
    """0 = every line of every page. Raised above 0 only to bound a very large
    sample, and reported when it is."""

    min_documents: int = 1
    """A candidate must recur in at least this many sample documents. Left at 1
    so a single-document sample still profiles."""

    max_candidates: int = 400
    """Upper bound on the returned checklist. Hitting it is reported as
    ``truncated`` — never silently."""


@dataclass(frozen=True, slots=True)
class SectionCandidate:
    """One recurring section header, with the evidence §6 step 2 asks for."""

    label: str
    """Normalized display label — the checklist row."""

    raw_examples: tuple[str, ...]
    occurrences: int
    document_count: int
    """§6 step 2's "frequency across the sample"."""

    avg_pages: int
    """Mean span in pages, rounded to a whole page. §6 asks for average page
    count; an integer keeps floats out of anything that reaches disk."""

    first_seen: tuple[str, int]
    """``(rel_path, page_no)`` of the first instance — §6 step 2's one-click
    preview target."""

    suggested_pattern: str
    """A ready-to-edit regex for the profile's ``section_rules``. Suggested,
    with disposition left at KEEP: the expert decides."""


@dataclass(frozen=True, slots=True)
class DetectionResult:
    candidates: tuple[SectionCandidate, ...]
    limits: DetectionLimits
    documents_sampled: int
    pages_sampled: int
    truncated: bool = False
    notes: tuple[str, ...] = ()


# Outline numbering. The roman-numeral and single-letter forms REQUIRE trailing
# punctuation: without it, "I need" and "A summary" would have their first word
# stripped as if it were a list marker, and two unrelated sections would collapse
# into one checklist row.
_NUMBERING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[.)\]]?|[IVXLC]+[.)\]]|[A-Z][.)\]])\s+"
)
_SENTENCE_END_RE = re.compile(r"[.!?,;:]$")
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def normalize_label(line: str) -> str:
    """Collapse a header line to its comparable label.

    Leading numbering is stripped (``4.2 Safety`` and ``5.1 Safety`` are one
    section), whitespace collapses, trailing punctuation goes, and the result
    folds to a canonical case for grouping. NFC first, so a composed and a
    decomposed accent group together.
    """
    text = unicodedata.normalize("NFC", line).strip()
    text = _NUMBERING_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .:-_–—")
    return text


def looks_like_header(line: str, limits: DetectionLimits) -> bool:
    """Shape test for a section-header line.

    Three mechanical signals, any one of which is enough: the line is short and
    entirely upper case; it is short, title-cased and unpunctuated; or it opens
    with an outline number. Body text fails all three because it is long, ends
    in punctuation, or is sentence-cased.
    """
    stripped = line.strip()
    if not stripped or "\t" in stripped:
        return False
    if not (limits.min_header_chars <= len(stripped) <= limits.max_header_chars):
        return False
    if not _HAS_LETTER_RE.search(stripped):
        return False
    if _SENTENCE_END_RE.search(stripped):
        return False
    words = stripped.split()
    if len(words) > limits.max_header_words:
        return False

    letters = [c for c in stripped if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    if _NUMBERING_RE.match(stripped):
        rest = _NUMBERING_RE.sub("", stripped).strip()
        return bool(rest) and len(rest.split()) <= limits.max_header_words
    alpha_words = [w for w in words if w[:1].isalpha()]
    if alpha_words and all(w[:1].isupper() for w in alpha_words) and len(words) <= 8:
        return True
    return False


def _suggest_pattern(label: str) -> str:
    """A regex matching this label allowing flexible internal whitespace.

    Anchored at the start so a mention of the section name inside body text
    cannot be mistaken for the header itself.
    """
    tokens = [re.escape(t) for t in label.split()]
    return r"^\s*" + r"\s+".join(tokens) if tokens else r"^$"


def detect_candidate_sections(
    documents: Sequence[DocumentRecord], limits: DetectionLimits | None = None
) -> DetectionResult:
    """Scan a sample and return the recurring header candidates.

    Ordering is deterministic — most documents first, then most occurrences,
    then label — so the checklist an expert sees does not reshuffle between
    runs of the same sample.
    """
    lim = limits or DetectionLimits()
    docs = sorted(documents, key=document_sort_key)
    notes: list[str] = []
    if lim.scan_lines_per_page:
        notes.append(
            f"only the first {lim.scan_lines_per_page} lines of each page were "
            "scanned for headers"
        )

    hits: dict[str, dict[str, object]] = {}
    pages_sampled = 0
    for doc in docs:
        seen_here: set[str] = set()
        header_pages: dict[str, list[int]] = {}
        for page in doc.pages:
            pages_sampled += 1
            lines = page.text.split("\n")
            if lim.scan_lines_per_page:
                lines = lines[: lim.scan_lines_per_page]
            for line in lines:
                if not looks_like_header(line, lim):
                    continue
                label = normalize_label(line)
                if len(label) < lim.min_header_chars:
                    continue
                key = label.casefold()
                entry = hits.setdefault(
                    key,
                    {
                        "label": label,
                        "raw": [],
                        "occurrences": 0,
                        "docs": set(),
                        "first": (doc.rel_path, page.page_no),
                    },
                )
                entry["occurrences"] = int(entry["occurrences"]) + 1
                if len(entry["raw"]) < 3 and line.strip() not in entry["raw"]:
                    entry["raw"].append(line.strip())
                entry["docs"].add(doc.rel_path)
                seen_here.add(key)
                header_pages.setdefault(key, []).append(page.page_no)

        # Span of a section = distance to the next header start in the same
        # document, which is the only page-count meaning available without
        # rendering the document.
        starts = sorted(
            (pno, key) for key, pnos in header_pages.items() for pno in pnos
        )
        total_pages = len(doc.pages)
        for i, (pno, key) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else total_pages + 1
            span = max(1, end - pno)
            spans = hits[key].setdefault("spans", [])
            spans.append(span)  # type: ignore[union-attr]

    candidates: list[SectionCandidate] = []
    for key, entry in hits.items():
        doc_names = entry["docs"]
        assert isinstance(doc_names, set)
        if len(doc_names) < lim.min_documents:
            continue
        spans = entry.get("spans") or [1]
        assert isinstance(spans, list)
        first = entry["first"]
        assert isinstance(first, tuple)
        label = str(entry["label"])
        candidates.append(
            SectionCandidate(
                label=label,
                raw_examples=tuple(entry["raw"]),  # type: ignore[arg-type]
                occurrences=int(entry["occurrences"]),
                document_count=len(doc_names),
                avg_pages=max(1, round(sum(spans) / len(spans))),
                first_seen=first,
                suggested_pattern=_suggest_pattern(label),
            )
        )

    candidates.sort(key=lambda c: (-c.document_count, -c.occurrences, c.label))
    truncated = len(candidates) > lim.max_candidates
    if truncated:
        notes.append(
            f"{len(candidates)} candidate headers found; the checklist shows the "
            f"{lim.max_candidates} most widespread. Raise max_candidates to see "
            "the rest — none were discarded from the underlying scan."
        )
        candidates = candidates[: lim.max_candidates]

    return DetectionResult(
        candidates=tuple(candidates),
        limits=lim,
        documents_sampled=len(docs),
        pages_sampled=pages_sampled,
        truncated=truncated,
        notes=tuple(notes),
    )
