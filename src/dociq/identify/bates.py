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

**The persisted confirmation is the complete grammar.** A confirmed format is
carried between runs as ``RunConfig.bates_pattern`` (and, for a recurring
production, ``FormatProfile.bates_pattern``). A bare regex cannot carry it: a
set of allowed digit widths flattens into a ``{min,max}`` span, and nothing in
the escaped text says where the prefix stops and the separator starts. So
:attr:`BatesFormat.pattern` emits a *canonical* string in two halves::

    (?#dociq-bates:1;p=MNFV;s=%20;w=4,5;xs=-;x=CONF)^MNFV\\ (?:\\d{4}|\\d{5})\\-CONF$

The leading ``(?#...)`` is a regular-expression comment — inert to matching,
so the string remains a valid regex that any consumer can compile and that
:meth:`FormatProfile.validate` accepts — and it carries the format's fields
percent-encoded (``%``-escaping keeps ``;``, ``=`` and ``)`` out of the values,
so the token can always be split back apart). The grammar of the token is:

``(?#dociq-bates:1;`` *then* ``p=`` prefix ``;s=`` separator ``;w=`` widths,
comma-separated ``;xs=`` suffix separator ``;x=`` suffix *then* ``)``.

:func:`parse_pattern` reads it back and returns the exact
:class:`BatesFormat`, or ``None`` when the string is not a DocIQ-issued
pattern. ``None`` is what makes a run fail closed rather than proceed on a
format it cannot enforce; see :func:`dociq.pipeline._bates_decision`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Sequence
from urllib.parse import quote, unquote

from dociq.contracts import DocumentRecord, PageRecord, document_sort_key

__all__ = [
    "BatesZone",
    "BatesFormat",
    "BatesCandidate",
    "BatesProposal",
    "BatesDecision",
    "DecisionStatus",
    "BatesRange",
    "MIN_DOCUMENT_COVERAGE_PCT",
    "BatesPatternError",
    "PATTERN_TOKEN_VERSION",
    "parse_pattern",
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
# alphabetic suffix (confidentiality designations such as "-CONF"). Anchored
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
#
# THE LETTER CLASS IS ``[A-Za-z]``, NOT ``[A-Z]`` — corrected 2026-08-01 by the
# criterion-4 acceptance run, which is the first time this code met a real
# production. It was uppercase-only, and the MNFV disclosure's own production
# prefix is **iiCON**. Every one of the 280 sampled pages carried its stamp,
# correctly, in the zone the detector looks at, and every one was rejected —
# 0% accuracy, no format proposed, and not a single warning anywhere in the
# run, because an unstamped set producing nothing is the ordinary case (D-13).
# A matter would have shipped with no Bates locators at all and nothing on the
# face of the run to say so.
#
# The class, not the repro: a Bates prefix is a string a producing party
# chooses, and nothing makes it uppercase. Lowercase ("iiCON"), mixed-case
# ("Def", "PltfBates") and party-initial forms are all ordinary. Case is
# PRESERVED, never folded: ``format_key`` compares the literal prefix, so a
# production stamping ``iiCON`` and one stamping ``IICON`` remain two formats
# and neither is applied to the other's pages.
_CANDIDATE_RE = re.compile(
    r"""^
    (?:
        (?P<prefix_sep>[A-Za-z][A-Za-z0-9]*(?:[ _.-][A-Za-z0-9]+)*)(?P<sep>[ _.-])
      | (?P<prefix_bare>[A-Za-z](?:[A-Za-z0-9]*[A-Za-z])?)
    )?
    (?P<digits>\d{3,10})
    (?:(?P<suffix_sep>[ _-])(?P<suffix>[A-Za-z]{1,12}))?
    $""",
    re.VERBOSE,
)

_MIN_DIGITS = 3
"""Below three digits a run is far more likely to be a page number than a Bates
sequence. Stated, not tuned: it is the narrowest production numbering LI has
seen, and widening it would make every page footer a candidate."""


class BatesPatternError(ValueError):
    """A stored Bates confirmation that cannot be reconstructed.

    Raised only when a pattern is *present* and unreadable. Absence of a Bates
    pattern is the ordinary case (D-13) and never raises anything.
    """


PATTERN_TOKEN_VERSION = "1"
"""Version of the ``(?#dociq-bates:...)`` token documented in the module
docstring. A future field is a new version, and an older reader returns ``None``
for it rather than reconstructing a format it does not fully understand — which
fails the run closed instead of enforcing half a grammar."""

_TOKEN_RE = re.compile(
    r"^\(\?\#dociq-bates:(?P<version>[0-9]+);(?P<body>[^)#]*)\)(?P<body_re>.*)$",
    re.DOTALL,
)
_ENCODE_SAFE = ""
"""``quote`` safe set: nothing. Every reserved character — including ``;``,
``=``, ``)`` and ``%`` itself — is percent-encoded, so splitting the token on
``;`` and ``=`` can never split inside a value."""


@dataclass(frozen=True, slots=True)
class BatesFormat:
    """The shape of one production's stamps.

    ``digit_widths`` is a *set*, not a single width, because real productions
    mix them: the MNFV disclosure (D-13) contains both ``MNFV 0391`` and
    ``MNFV 02684``. Pinning a single width would reject half a production.

    Every part of the grammar is a field, including ``suffix_sep`` — the
    separator that precedes a confidentiality designation. Dropping it turned a
    confirmed ``MNFV 000391-CONF`` into a pattern that only matched
    ``MNFV 000391CONF``, which no page in the production carries.
    """

    prefix: str
    separator: str
    digit_widths: tuple[int, ...]
    suffix: str | None = None
    suffix_sep: str = ""

    @property
    def widths(self) -> tuple[int, ...]:
        """Allowed digit widths, sorted and deduplicated — the canonical form
        used by the pattern, by :meth:`accepts_width` and by equality of the
        persisted string."""
        return tuple(sorted(set(self.digit_widths)))

    def accepts_width(self, width: int) -> bool:
        """Whether a run of ``width`` digits is in the confirmed format.

        The one place width is judged, so the check cannot drift between
        detection, application and the persisted pattern."""
        return width in self.widths

    @property
    def pattern(self) -> str:
        """The canonical persisted form: a matching regex prefixed by the
        lossless ``(?#dociq-bates:...)`` token (module docstring).

        The regex half enumerates the allowed widths as an alternation rather
        than a ``{min,max}`` span, so even a consumer that ignores the token
        enforces the exact confirmed widths.
        """
        widths = self.widths
        digits = (
            f"\\d{{{widths[0]}}}"
            if len(widths) == 1
            else "(?:" + "|".join(f"\\d{{{w}}}" for w in widths) + ")"
        )
        parts = [re.escape(self.prefix), re.escape(self.separator), digits]
        if self.suffix:
            parts.append(re.escape(self.suffix_sep))
            parts.append(re.escape(self.suffix))
        token = (
            f"(?#dociq-bates:{PATTERN_TOKEN_VERSION};"
            f"p={quote(self.prefix, safe=_ENCODE_SAFE)};"
            f"s={quote(self.separator, safe=_ENCODE_SAFE)};"
            f"w={','.join(str(w) for w in widths)};"
            f"xs={quote(self.suffix_sep, safe=_ENCODE_SAFE)};"
            f"x={quote(self.suffix or '', safe=_ENCODE_SAFE)})"
        )
        return token + "^" + "".join(parts) + "$"

    @property
    def label(self) -> str:
        widths = self.widths
        sample = "0" * (widths[0] - 1) + "1"
        suffix = f"{self.suffix_sep}{self.suffix}" if self.suffix else ""
        return f"{self.prefix}{self.separator}{sample}{suffix}"

    def key(self) -> tuple[str, str, str, str]:
        """Identity of the *shape*, ignoring digit width — the grouping key for
        candidate aggregation. The suffix separator is part of the shape: a
        production stamping ``-CONF`` and one stamping ``_CONF`` are two
        formats, and merging them would apply either one to both."""
        return (self.prefix, self.separator, self.suffix_sep, self.suffix or "")


def parse_pattern(pattern: str | None) -> BatesFormat | None:
    """Reconstruct the format a persisted pattern stands for.

    Returns ``None`` — never a guess and never a partial format — when the
    string is absent, is not a DocIQ-issued pattern, carries a token version
    this build does not know, or does not round-trip to itself. The caller is
    expected to treat ``None`` on a *present* pattern as a stop, because a
    format that cannot be reconstructed cannot be enforced, and applying an
    unenforceable format is how out-of-format locators get in.
    """
    if not pattern:
        return None
    m = _TOKEN_RE.match(pattern)
    if not m or m.group("version") != PATTERN_TOKEN_VERSION:
        return None
    fields: dict[str, str] = {}
    for part in m.group("body").split(";"):
        if not part or "=" not in part:
            return None
        name, _, value = part.partition("=")
        if name in fields:
            return None
        fields[name] = value
    if set(fields) != {"p", "s", "w", "xs", "x"}:
        return None
    raw_widths = fields["w"].split(",")
    if not all(w.isdigit() and int(w) > 0 for w in raw_widths):
        return None
    fmt = BatesFormat(
        prefix=unquote(fields["p"]),
        separator=unquote(fields["s"]),
        digit_widths=tuple(sorted({int(w) for w in raw_widths})),
        suffix=unquote(fields["x"]) or None,
        suffix_sep=unquote(fields["xs"]),
    )
    # The round trip is the validation. A pattern whose regex half disagrees
    # with its token — hand-edited, truncated, or written by a different
    # version — is rejected rather than reconciled, because there is no honest
    # way to choose which half the operator confirmed.
    if fmt.pattern != pattern:
        return None
    return fmt


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
    suffix_sep: str = ""

    @property
    def format_key(self) -> tuple[str, str, str, str]:
        return (self.prefix, self.separator, self.suffix_sep, self.suffix or "")


@dataclass(frozen=True, slots=True)
class _ParsedLine:
    """One zone line decomposed into every part of the grammar.

    A record rather than a tuple because the tuple was the defect: callers
    unpacked the parts they remembered and dropped the rest, which is how the
    digit width reached ``apply_bates`` as ``_width`` and the suffix separator
    never reached it at all.
    """

    prefix: str
    separator: str
    number: int
    digit_width: int
    suffix: str | None
    suffix_sep: str

    @property
    def format_key(self) -> tuple[str, str, str, str]:
        return (self.prefix, self.separator, self.suffix_sep, self.suffix or "")


def _parse_line(line: str) -> _ParsedLine | None:
    m = _CANDIDATE_RE.match(line)
    if not m:
        return None
    digits = m.group("digits")
    if len(digits) < _MIN_DIGITS:
        return None
    prefix = m.group("prefix_sep") or m.group("prefix_bare") or ""
    sep = m.group("sep") or ""
    suffix = m.group("suffix")
    suffix_sep = m.group("suffix_sep") or ""
    if not prefix:
        # A bare number in a footer is a page number far more often than a
        # Bates stamp. Only accept it when it is long enough that a page number
        # is implausible.
        if len(digits) < 6:
            return None
        sep = ""
    return _ParsedLine(
        prefix=prefix,
        separator=sep,
        number=int(digits),
        digit_width=len(digits),
        suffix=suffix,
        suffix_sep=suffix_sep if suffix else "",
    )


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
                out.append(
                    BatesCandidate(
                        sort_key=key,
                        page_no=page.page_no,
                        raw=line,
                        prefix=parsed.prefix,
                        separator=parsed.separator,
                        number=parsed.number,
                        digit_width=parsed.digit_width,
                        suffix=parsed.suffix,
                        line_index=line_index,
                        suffix_sep=parsed.suffix_sep,
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

    best_document_coverage_pct: int = 0
    """Share of the pages of the single best-covered document that carry this
    format. The corpus-wide figure cannot decide whether a production is
    stamped: a fully stamped 306-page disclosure inside an 18,000-page record
    is 1.7% of the corpus and 100% of itself."""

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


MIN_DOCUMENT_COVERAGE_PCT = 50
"""How much of one document a format must stamp before it is proposed.

An absolute page count cannot carry this decision. Measured on the real
Petrobras record (368 documents, 18,521 pages): two stray lines that parse as
``CP0001`` were enough to propose a Bates format for the whole corpus, on a set
D-13 designates as the *negative* case — detection there must come back empty.
Two pages in eighteen thousand is not a production.

The test is applied per DOCUMENT rather than per corpus, and that is the point.
A real production stamps essentially every page of the documents it covers, so a
fully stamped 306-page disclosure sitting inside an 18,000-page record — D-13's
MNFV set, exactly — is 1.7% of the corpus and 100% of itself, and must still be
proposed. A corpus-wide floor would throw it away.

Fifty percent, rather than something higher, because a production can carry
unstamped inserts and because the operator confirms the proposal anyway: the
cost of proposing wrongly is one dialog, and the cost of not proposing is a
matter that silently loses its Bates locators.
"""


def propose_format(
    documents: Sequence[DocumentRecord],
    zone: BatesZone | None = None,
    *,
    min_pages: int = 2,
    min_document_coverage_pct: int = MIN_DOCUMENT_COVERAGE_PCT,
) -> BatesProposal | None:
    """Propose the dominant stamp format, or ``None`` for an unstamped set.

    ``None`` is a completely ordinary outcome and carries no warning: §4 Stage 3
    and D-13 both say so, and the Petrobras corpus is the proof case.

    A format must clear two bars: ``min_pages`` pages overall, and
    :data:`MIN_DOCUMENT_COVERAGE_PCT` of at least one document's pages. Both
    thresholds are named and adjustable rather than buried, because suppressing
    a candidate is a decision and a silent one would be indistinguishable from
    not looking.
    """
    candidates = detect_candidates(documents, zone)
    if not candidates:
        return None
    pages_scanned = sum(len(d.pages) for d in documents)
    by_shape: dict[tuple[str, str, str, str], list[BatesCandidate]] = {}
    for c in candidates:
        by_shape.setdefault(c.format_key, []).append(c)

    # Deterministic: most pages first, then the longest prefix (a longer prefix
    # is the more specific reading of the same stamp), then alphabetical.
    ranked = sorted(
        by_shape.items(), key=lambda kv: (-len(kv[1]), -len(kv[0][0]), kv[0])
    )
    (prefix, sep, suffix_sep, suffix), best = ranked[0]
    if len(best) < min_pages:
        return None

    pages_by_key = {document_sort_key(d): len(d.pages) for d in documents}
    matched_by_key = Counter(c.sort_key for c in best)
    best_doc_pct = max(
        (round(100 * n / pages_by_key[key])
         for key, n in matched_by_key.items() if pages_by_key.get(key)),
        default=0,
    )
    if best_doc_pct < min_document_coverage_pct:
        return None

    widths = tuple(sorted(Counter(c.digit_width for c in best)))
    fmt = BatesFormat(
        prefix=prefix,
        separator=sep,
        digit_widths=widths,
        suffix=suffix or None,
        suffix_sep=suffix_sep,
    )
    samples = tuple(c.raw for c in best[:3])
    alternatives = tuple(
        (
            BatesFormat(
                prefix=k[0],
                separator=k[1],
                digit_widths=tuple(sorted({c.digit_width for c in v})),
                suffix=k[3] or None,
                suffix_sep=k[2],
            ).label,
            len(v),
        )
        for k, v in ranked[1:4]
    )
    return BatesProposal(
        format=fmt,
        pages_matched=len(best),
        pages_scanned=pages_scanned,
        documents_matched=len(matched_by_key),
        samples=samples,
        alternatives=alternatives,
        best_document_coverage_pct=best_doc_pct,
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

    A line is written only if it matches the confirmed format *completely* —
    prefix, separator, digit width, suffix separator and suffix. Width is not
    cosmetic: a format confirmed for four or five digits used to accept
    ``MNFV 1234567890``, and a locator that is not in the production is worse
    than no locator, because the record it points at does not exist.
    """
    if decision is None or not decision.applies:
        return tuple(sorted(documents, key=document_sort_key))
    assert decision.format is not None
    fmt = decision.format
    target = fmt.key()
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
                if parsed.format_key == target and fmt.accepts_width(
                    parsed.digit_width
                ):
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
            parsed.append(((got.number if got else 0), p.bates or ""))
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
