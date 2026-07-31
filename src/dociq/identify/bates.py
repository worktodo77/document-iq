"""Bates stamp detection and the operator confirmation flow (§4 Stage 3).

**Absence is normal.** A footer-zone probe over all 298 PDFs of the Petrobras
corpus found no Bates stamps at all (D-13), and the audited Project 495 index
has no Bates column. An unstamped matter must therefore produce ``None`` on
every page, no warning, no flag, and no degraded status. Every entry point here
returns cleanly on an unstamped corpus.

**The confirmation flow is data, not UI.** §4 Stage 3 requires the detected
format to be confirmed with the user on first detection per document set and
then applied automatically. That is modelled as :class:`BatesProposal` (what
DocIQ believes, with the evidence for it) and :class:`BatesDecision` (what the
operator ruled). A GUI drives the transition; a test drives it just as well;
neither is privileged, and nothing is applied to a page until a decision exists.

**Zone limitation, disclosed.** §4 says "page corners/footers". The frozen
contract carries page *text*, not glyph geometry, so DocIQ cannot ask where on
the page a candidate sat. The zone is therefore approximated by position in the
text stream — the first and last few lines — which is where a footer or header
stamp lands in every extractor's reading order. A stamp rendered mid-stream by
an unusual extractor would be missed; that is a real limit of the text-only
zone and is reported rather than papered over.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Sequence

from dociq.contracts import DocumentRecord, PageRecord, document_sort_key

__all__ = [
    "BatesZone",
    "BatesFormat",
    "BatesCandidate",
    "BatesProposal",
    "BatesDecision",
    "DecisionStatus",
    "BatesRange",
    "detect_candidates",
    "propose_format",
    "apply_bates",
    "document_ranges",
    "ranges_by_sort_key",
]


@dataclass(frozen=True, slots=True)
class BatesZone:
    """How much of a page's text stream counts as "corner or footer".

    Both bounds are explicit and reported (no silent caps): a stamp outside the
    zone is a miss, and an operator is entitled to know how wide DocIQ looked.
    """

    head_lines: int = 3
    tail_lines: int = 4

    def slice_lines(self, text: str) -> tuple[tuple[int, str], ...]:
        """Zone lines as ``(line index, text)``, head first then tail, without
        repeating a line when the page is shorter than the zone."""
        lines = [ln.strip() for ln in text.split("\n")]
        picked: dict[int, str] = {}
        for i in range(min(self.head_lines, len(lines))):
            picked[i] = lines[i]
        for i in range(max(0, len(lines) - self.tail_lines), len(lines)):
            picked[i] = lines[i]
        return tuple((i, picked[i]) for i in sorted(picked) if picked[i])


# A Bates stamp is a production mark: an optional alphanumeric prefix, an
# optional separator, and a run of digits — optionally followed by a short
# alphanumeric suffix (confidentiality designations such as "-CONF"). Anchored
# at both ends of the zone line, because an unanchored match would happily read
# a date, a dollar figure, or a paragraph number as a Bates number.
#
# The prefix has two alternatives, and the split is load-bearing rather than
# stylistic. A prefix that may contain digits ("VOL2", "P 495") is ambiguous
# against the number itself: a greedy match on "MNFV 000391" reads the prefix as
# "MNFV 000" and the number as "391", which silently corrupts every stamp in the
# production. So a digit-bearing prefix REQUIRES a separator before the number,
# and a separator-less prefix must end in a letter. Between them, no input has
# two readings.
_CANDIDATE_RE = re.compile(
    r"""^
    (?:
        (?P<prefix_sep>[A-Z][A-Z0-9]*(?:[ _.-][A-Z0-9]+)*)(?P<sep>[ _.-])
      | (?P<prefix_bare>[A-Z](?:[A-Z0-9]*[A-Z])?)
    )?
    (?P<digits>\d{3,10})
    (?:(?P<suffix_sep>[ _-])(?P<suffix>[A-Z]{1,12}))?
    $""",
    re.VERBOSE,
)

_MIN_DIGITS = 3
"""Below three digits a run is far more likely to be a page number than a Bates
sequence. Stated, not tuned: it is the narrowest production numbering LI has
seen, and widening it would make every page footer a candidate."""


@dataclass(frozen=True, slots=True)
class BatesFormat:
    """The shape of one production's stamps.

    ``digit_widths`` is a *set*, not a single width, because real productions
    mix them: the MNFV disclosure (D-13) contains both ``MNFV 0391`` and
    ``MNFV 02684``. Pinning a single width would reject half a production.
    """

    prefix: str
    separator: str
    digit_widths: tuple[int, ...]
    suffix: str | None = None

    @property
    def pattern(self) -> str:
        """A regex that matches exactly this format, for ``RunConfig.bates_pattern``."""
        widths = sorted(set(self.digit_widths))
        digits = (
            f"\\d{{{widths[0]}}}"
            if len(widths) == 1
            else f"\\d{{{widths[0]},{widths[-1]}}}"
        )
        parts = [re.escape(self.prefix), re.escape(self.separator), digits]
        if self.suffix:
            parts.append(re.escape(self.suffix))
        return "^" + "".join(parts) + "$"

    @property
    def label(self) -> str:
        widths = sorted(set(self.digit_widths))
        sample = "0" * (widths[0] - 1) + "1"
        return f"{self.prefix}{self.separator}{sample}"

    def key(self) -> tuple[str, str, str]:
        """Identity of the *shape*, ignoring digit width — the grouping key for
        candidate aggregation."""
        return (self.prefix, self.separator, self.suffix or "")


@dataclass(frozen=True, slots=True)
class BatesCandidate:
    """One zone line that parsed as a stamp."""

    sort_key: tuple[str, str, int]
    page_no: int
    raw: str
    prefix: str
    separator: str
    number: int
    digit_width: int
    suffix: str | None
    line_index: int

    @property
    def format_key(self) -> tuple[str, str, str]:
        return (self.prefix, self.separator, self.suffix or "")


def _parse_line(line: str) -> tuple[str, str, int, int, str | None] | None:
    m = _CANDIDATE_RE.match(line)
    if not m:
        return None
    digits = m.group("digits")
    if len(digits) < _MIN_DIGITS:
        return None
    prefix = m.group("prefix_sep") or m.group("prefix_bare") or ""
    sep = m.group("sep") or ""
    if not prefix:
        # A bare number in a footer is a page number far more often than a
        # Bates stamp. Only accept it when it is long enough that a page number
        # is implausible.
        if len(digits) < 6:
            return None
        sep = ""
    return prefix, sep, int(digits), len(digits), m.group("suffix")


def detect_candidates(
    documents: Sequence[DocumentRecord], zone: BatesZone | None = None
) -> tuple[BatesCandidate, ...]:
    """Scan every page's zone for stamp-shaped lines.

    Returns candidates in canonical document order then page order, so the
    proposal derived from them is identical run to run.
    """
    z = zone or BatesZone()
    out: list[BatesCandidate] = []
    for doc in sorted(documents, key=document_sort_key):
        key = document_sort_key(doc)
        for page in doc.pages:
            for line_index, line in z.slice_lines(page.text):
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                prefix, sep, number, width, suffix = parsed
                out.append(
                    BatesCandidate(
                        sort_key=key,
                        page_no=page.page_no,
                        raw=line,
                        prefix=prefix,
                        separator=sep,
                        number=number,
                        digit_width=width,
                        suffix=suffix,
                        line_index=line_index,
                    )
                )
                break  # one stamp per page; the first zone line wins
    return tuple(out)


class DecisionStatus(str, Enum):
    """State of the §4 Stage-3 confirmation."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class BatesProposal:
    """What DocIQ believes the production's stamp format is, and why.

    The evidence fields exist so the confirmation prompt can be *specific* —
    "1,842 of 1,910 pages carry MNFV 000001-style stamps, e.g. MNFV 0391 on
    page 12 of X" — rather than asking an operator to approve an abstraction.
    """

    format: BatesFormat
    pages_matched: int
    pages_scanned: int
    documents_matched: int
    samples: tuple[str, ...]
    alternatives: tuple[tuple[str, int], ...] = ()
    """Runner-up formats as ``(label, page count)``, so a wrong first guess is
    visible and correctable instead of invisible."""

    @property
    def coverage_pct(self) -> int:
        """Integer percent — a float here would reach the log and the summary,
        and Principle 5 keeps floats out of anything hashed."""
        if not self.pages_scanned:
            return 0
        return round(100 * self.pages_matched / self.pages_scanned)


@dataclass(frozen=True, slots=True)
class BatesDecision:
    """The operator's ruling on a proposal — the thing a GUI produces.

    A decision travels with the matter: ``RunConfig.bates_pattern`` records the
    confirmed pattern so a rerun applies it without re-prompting, which is what
    "confirmed once per document set, then applied automatically" means.
    """

    status: DecisionStatus
    format: BatesFormat | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    note: str | None = None

    @property
    def applies(self) -> bool:
        return self.status is DecisionStatus.CONFIRMED and self.format is not None

    def pattern(self) -> str | None:
        return self.format.pattern if self.format else None


def propose_format(
    documents: Sequence[DocumentRecord],
    zone: BatesZone | None = None,
    *,
    min_pages: int = 2,
) -> BatesProposal | None:
    """Propose the dominant stamp format, or ``None`` for an unstamped set.

    ``None`` is a completely ordinary outcome and carries no warning: §4 Stage 3
    and D-13 both say so, and the Petrobras corpus is the proof case.
    """
    candidates = detect_candidates(documents, zone)
    if not candidates:
        return None
    pages_scanned = sum(len(d.pages) for d in documents)
    by_shape: dict[tuple[str, str, str], list[BatesCandidate]] = {}
    for c in candidates:
        by_shape.setdefault(c.format_key, []).append(c)

    # Deterministic: most pages first, then the longest prefix (a longer prefix
    # is the more specific reading of the same stamp), then alphabetical.
    ranked = sorted(
        by_shape.items(), key=lambda kv: (-len(kv[1]), -len(kv[0][0]), kv[0])
    )
    (prefix, sep, suffix), best = ranked[0]
    if len(best) < min_pages:
        return None

    widths = tuple(sorted(Counter(c.digit_width for c in best)))
    fmt = BatesFormat(
        prefix=prefix, separator=sep, digit_widths=widths, suffix=suffix or None
    )
    samples = tuple(c.raw for c in best[:3])
    alternatives = tuple(
        (
            BatesFormat(k[0], k[1], tuple(sorted({c.digit_width for c in v})), k[2] or None).label,
            len(v),
        )
        for k, v in ranked[1:4]
    )
    return BatesProposal(
        format=fmt,
        pages_matched=len(best),
        pages_scanned=pages_scanned,
        documents_matched=len({c.sort_key for c in best}),
        samples=samples,
        alternatives=alternatives,
    )


@dataclass(frozen=True, slots=True)
class BatesRange:
    """Per-document Bates range (§5's "Bates start" / "Bates end").

    Ordered numerically, not lexicographically: a production that mixes
    ``MNFV 0391`` and ``MNFV 02684`` sorts wrongly as text, and the §5 columns
    would then report a range that does not contain its own contents.
    """

    start: str | None
    end: str | None
    pages_with_bates: int
    pages_without_bates: int

    @property
    def complete(self) -> bool:
        return self.pages_with_bates > 0 and self.pages_without_bates == 0


def apply_bates(
    documents: Sequence[DocumentRecord],
    decision: BatesDecision | None,
    zone: BatesZone | None = None,
) -> tuple[DocumentRecord, ...]:
    """Write the confirmed stamps into ``PageRecord.bates``.

    With no decision, or a rejected/pending one, the documents come back
    untouched — same objects, same order. That is the unstamped-matter path and
    it is deliberately a no-op rather than a pass that writes ``None`` over
    ``None``: an identity return is trivially provable.
    """
    if decision is None or not decision.applies:
        return tuple(sorted(documents, key=document_sort_key))
    assert decision.format is not None
    target = decision.format.key()
    z = zone or BatesZone()

    out: list[DocumentRecord] = []
    for doc in sorted(documents, key=document_sort_key):
        pages: list[PageRecord] = []
        changed = False
        for page in doc.pages:
            stamp: str | None = None
            for _, line in z.slice_lines(page.text):
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                prefix, sep, _number, _width, suffix = parsed
                if (prefix, sep, suffix or "") == target:
                    stamp = line
                    break
            if stamp is not None and page.bates != stamp:
                pages.append(page.evolve(bates=stamp))
                changed = True
            else:
                pages.append(page)
        out.append(replace(doc, pages=tuple(pages)) if changed else doc)
    return tuple(out)


def document_ranges(
    documents: Sequence[DocumentRecord],
) -> dict[tuple[str, str, int], BatesRange]:
    """Per-document Bates range, keyed by :func:`document_sort_key`.

    Keyed by sort key rather than ``doc_id`` because Stage 3 runs *before*
    Stage 3b: at detection time no document has an identifier yet, and the
    Bates range is itself one of Stage 3b's tertiary match keys.
    """
    out: dict[tuple[str, str, int], BatesRange] = {}
    for doc in documents:
        stamped = [p for p in doc.pages if p.bates]
        if not stamped:
            out[document_sort_key(doc)] = BatesRange(
                None, None, 0, len(doc.pages)
            )
            continue
        parsed = []
        for p in stamped:
            got = _parse_line(p.bates or "")
            parsed.append(((got[2] if got else 0), p.bates or ""))
        parsed.sort()
        out[document_sort_key(doc)] = BatesRange(
            start=parsed[0][1],
            end=parsed[-1][1],
            pages_with_bates=len(stamped),
            pages_without_bates=len(doc.pages) - len(stamped),
        )
    return out


def ranges_by_sort_key(
    ranges: Mapping[tuple[str, str, int], BatesRange],
) -> dict[tuple[str, str, int], tuple[str | None, str | None]]:
    """Reduce ranges to the ``(start, end)`` pairs Stage 3b's tertiary match
    key consumes."""
    return {k: (v.start, v.end) for k, v in ranges.items()}
