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

from dociq.contracts import (
    DocumentRecord,
    PageKind,
    PageRecord,
    document_sort_key,
)

__all__ = [
    "BatesZone",
    "FOOTER_BLOCK_MAX_LINES",
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
    "zone_has_candidate",
    "stamp_tokens",
    "propose_format",
    "apply_bates",
    "apply_bates_reported",
    "BatesApplication",
    "NormalizedStamp",
    "matter_prefixes",
    "near_miss_rule",
    "document_ranges",
    "ranges_by_sort_key",
]


FOOTER_BLOCK_MAX_LINES = 4
"""How many recovered stamp tokens the targeted footer re-OCR (D-25) may append
to a page's text.

The extractor appends the tokens it recovers from a high-resolution crop of the
page's footer/header band to the END of the page text, so that the ordinary
zone sees them. Appending to the tail is what makes the recovered stamp
reachable, and it is also what could push the *existing* tail out of the zone —
which would turn a page that ``apply_bates`` currently gets right, via the
confirmed-token fallback, into a miss.

So the bound is not decorative: :attr:`BatesZone.tail_lines` is
``_TAIL_LINES_BASE + FOOTER_BLOCK_MAX_LINES``, which makes eviction impossible
by construction rather than unlikely in practice. ``tests/test_bates.py``
asserts the relation, so raising one without the other is a test failure.
"""

_TAIL_LINES_BASE = 4
"""The tail zone the ordinary text stream gets, before the footer re-OCR block
is allowed for. Four lines is what the detector looked at before D-25 and it is
still the whole of what a page's own text contributes."""


@dataclass(frozen=True, slots=True)
class BatesZone:
    """How much of a page's text stream counts as "corner or footer".

    Both bounds are explicit and reported (no silent caps): a stamp outside the
    zone is a miss, and an operator is entitled to know how wide DocIQ looked.

    ``tail_lines`` carries a deliberate margin — see
    :data:`FOOTER_BLOCK_MAX_LINES`.
    """

    head_lines: int = 3
    tail_lines: int = _TAIL_LINES_BASE + FOOTER_BLOCK_MAX_LINES

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


def zone_has_candidate(text: str, zone: BatesZone | None = None) -> bool:
    """Whether the ordinary text of one page already offers a stamp-shaped line.

    This is the trigger the extractor uses to decide whether a page is worth a
    targeted footer re-OCR (D-25), and it lives here rather than in the
    extractor so that "stamp-shaped" has exactly one definition. A page that
    already carries a candidate is left alone; a page that carries none is the
    only page the expensive second pass can help.

    It is deliberately **format-agnostic**: extraction happens at Stage 1 and
    the production's format is not confirmed until Stage 3, so the trigger
    cannot ask "does this match the confirmed format?" — only "did the ordinary
    pass produce anything stamp-shaped at all?".
    """
    z = zone or BatesZone()
    return any(_parse_line(line) is not None for _, line in z.slice_lines(text))


def stamp_tokens(text: str, *, limit: int = FOOTER_BLOCK_MAX_LINES) -> tuple[str, ...]:
    """The stamp-shaped tokens inside ``text``, in reading order, deduplicated.

    Used to reduce a re-OCR'd footer strip to the part that could be a Bates
    stamp. The whole strip is *not* appended to the page: the page already has
    its own reading of that footer from the ordinary pass, and appending a
    second full reading would double-count the footer in every downstream token
    count, dedup and summary for the sake of one locator.

    **Two acceptances, and the split is what keeps this from widening
    detection.**

    1. A whole line that parses is taken whole. That is byte-for-byte what
       :func:`detect_candidates` would have accepted had the ordinary pass read
       that line, so it adds no shape the detector did not already admit — and
       it is the only way a *separated* stamp (``MNFV 000391``) survives,
       because its two halves are two words.
    2. Otherwise, a single WORD inside the line that parses **and carries a
       prefix**. That recovers the shape the acceptance run actually hit — a
       stamp folded into a longer line, ``... Sierra Madre Street in
       iiCON003961`` — without reading prose as a production mark.

    Both restrictions were put there by a failing test rather than by taste:

    * a multi-word scan inside a line reads ``30 June 2019`` as the stamp
      ``June 2019``, which is a date in every document ever produced;
    * a bare-number word is not admitted inside a line, because
      ``Page 3 of 12 MNFV 000391`` would otherwise yield ``000391`` — the same
      page, a *different* locator, and a wrong one. The whole-line rule already
      keeps bare numbers to the case detection admits them in.

    A separated stamp folded into a longer line is therefore **missed** rather
    than guessed at. That is the failure direction §4 asks for everywhere else
    in this module, and the band crop usually isolates the stamp on a line of
    its own in any case.

    ``limit`` is a stated bound, not a silent cap; the caller reports it.
    """
    out: list[str] = []
    seen: set[str] = set()

    def emit(tok: str) -> bool:
        """True when the caller should stop — the bound is reached."""
        if tok in seen:
            return False
        seen.add(tok)
        out.append(tok)
        return len(out) >= limit

    for raw_line in text.split("\n"):
        line = " ".join(raw_line.split())
        if not line:
            continue
        if _parse_line(line) is not None:
            if emit(line):
                return tuple(out)
            continue
        for word in line.split():
            parsed = _parse_line(word)
            if parsed is None or not parsed.prefix:
                continue
            if emit(word):
                return tuple(out)
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


# ---------------------------------------------------------------------------
# D-28 — prefix normalization, and the two gates that make it safe
# ---------------------------------------------------------------------------
#
# Measured under D-25: on the MNFV production rapidocr reads a footer stamp's
# DIGITS correctly and cannot resolve its PREFIX. `iiCON004926` comes back as
# `iCON004926`, `jiCON004926`, `liCON004926`, `TiCON005000`. More resolution
# makes it worse; detector tuning does not touch it; it is the recognition
# model. So criterion 4 stalls on a part of the stamp that carries no
# information: EVERY page of a confirmed production has the SAME prefix.
#
# D-28 (Alex, 2026-08-02) permits repairing it, under three conditions, and
# adds one more that follows from where the damage comes from:
#
#   1. digits, digit width, separator, suffix separator and suffix match the
#      confirmed format EXACTLY. Only the prefix may differ.
#   2. the prefix differs by a near-miss under :func:`near_miss_rule`, which is
#      written out below in terms an expert can read and apply by hand.
#   3. the MATTER carries exactly ONE proposable prefix. This is the ruling.
#      It does not make a wrong-series locator unlikely; it makes it
#      structurally impossible, because there is no second series to file a
#      page under.
#   4. the page is one DocIQ had to OCR. A native text layer is exact, and a
#      prefix that differs there is a real difference in the document, not a
#      misreading of it.
#
# **Repair is DISCLOSED, never silent.** §4 requires misses to be flagged and
# never quietly corrected, and this is a narrow ruled exception to that, not a
# hole in it. :func:`apply_bates_reported` returns every repaired locator with
# what the page actually reads and which rule fired, and the pipeline puts the
# count in the run's warnings and on the summary. Nothing about the repair
# enters hashed content beyond the locator itself: the page record carries the
# locator and nothing else, so a run's identity is the corpus, not the story of
# how it was read.


_CONFUSABLE_GROUPS: tuple[str, ...] = (
    "iIjJlL1|!tTfr",   # thin verticals with and without a dot or a bar
    "oO0QDC",          # closed round forms
    "cCeE",
    "sS5",
    "bB6",
    "gGq9",
    "zZ2",
    "uUvV",
    "nNhH",
    "aA",
    "xX",
    "yY",
    "mM",
)
"""Glyph groups a recognizer may confuse WITHIN, and the only substitutions
:func:`near_miss_rule` will accept.

The first group is the one this corpus produced: `iiCON` came back as `jiCON`,
`liCON` and `TiCON`, all thin verticals. The rest are the ordinary confusions of
any OCR engine and are listed so the rule is a stated class rather than a fix
for the four strings that happened to be measured. A substitution ACROSS groups
— `iiCON` against `xxCON` — is not a near miss and is refused.

Deliberately NOT a similarity score. A threshold on a distance metric is a knob,
and an expert asked "why was this page repaired?" is owed a rule, not a number.
"""

_CONFUSABLE_OF: dict[str, frozenset[str]] = {}
for _group in _CONFUSABLE_GROUPS:
    for _ch in _group:
        _CONFUSABLE_OF.setdefault(_ch, frozenset(_group))

NEAR_MISS_SUBSTITUTION = "one glyph read as a confusable one"
NEAR_MISS_DOUBLED = "a doubled character read as single"
NEAR_MISS_SINGLE_DOUBLED = "a single character read as doubled"


def near_miss_rule(read: str, confirmed: str) -> str | None:
    """Which near-miss rule turns ``read`` into ``confirmed``, or ``None``.

    Written so it can be applied by hand. ``read`` is a near miss of
    ``confirmed`` when EXACTLY ONE of the following holds:

    * **substitution** — the two are the same length and differ at exactly one
      position, and the two characters there are in the same group of
      :data:`_CONFUSABLE_GROUPS`. (``jiCON`` for ``iiCON``.)
    * **a doubled character read as single** — ``read`` is ``confirmed`` with
      one character deleted, and the deleted character is identical to the one
      beside it. (``iCON`` for ``iiCON``.)
    * **a single character read as doubled** — the same in the other direction.
      (``iiiCON`` for ``iiCON``.)

    Everything else is refused, including:

    * any difference of two or more characters, at any length;
    * any edit that touches a DIGIT. On the doubling rules this is
      load-bearing and was watched to fail without it: with a separator-less
      format the prefix and the number abut, so a rule that would collapse
      ``iiCON0`` to ``iiCON`` silently moves a digit out of a seven-digit
      number and produces a locator for a page that does not exist. On the
      substitution rule it is defence in depth rather than a demonstrated
      need — the confusable groups do not currently pair a digit with a letter
      that could shift the boundary — and it is kept anyway so the rule an
      expert reads is the uniform "no edit ever touches a digit" rather than a
      rule with an exception nobody can hold in their head;
    * an insertion or deletion that is not a doubling, because that is not a
      misreading of a glyph, it is a different string.

    ``None`` when ``read == confirmed`` — an exact read is not a repair and must
    never be reported as one.
    """
    if read == confirmed or not read or not confirmed:
        return None
    if len(read) == len(confirmed):
        diff = [i for i, (a, b) in enumerate(zip(read, confirmed)) if a != b]
        if len(diff) != 1:
            return None
        i = diff[0]
        if read[i].isdigit() or confirmed[i].isdigit():
            return None
        if read[i].lower() == confirmed[i].lower():
            # A pure CASE difference is not a near miss, and this is the one
            # exclusion that is about law rather than about optics. This module
            # holds, deliberately, that a production stamping ``iiCON`` and one
            # stamping ``IICON`` are two formats and neither is applied to the
            # other's pages. Accepting a case-only repair would fold them back
            # together through the side door, on OCR pages, silently. Nothing
            # on the measured corpus needs it: every misreading there is
            # ``j``, ``l`` or ``T`` for ``i``.
            return None
        if confirmed[i] in _CONFUSABLE_OF.get(read[i], frozenset()):
            return NEAR_MISS_SUBSTITUTION
        return None
    if len(read) == len(confirmed) - 1:
        for i, ch in enumerate(confirmed):
            if ch.isdigit():
                continue
            if confirmed[:i] + confirmed[i + 1:] != read:
                continue
            # the deleted character must have been one of a doubled pair
            if (i > 0 and confirmed[i - 1] == ch) or \
                    (i + 1 < len(confirmed) and confirmed[i + 1] == ch):
                return NEAR_MISS_DOUBLED
        return None
    if len(read) == len(confirmed) + 1:
        for i, ch in enumerate(read):
            if ch.isdigit():
                continue
            if read[:i] + read[i + 1:] != confirmed:
                continue
            if (i > 0 and read[i - 1] == ch) or \
                    (i + 1 < len(read) and read[i + 1] == ch):
                return NEAR_MISS_SINGLE_DOUBLED
        return None
    return None


def matter_prefixes(
    documents: Sequence[DocumentRecord],
    zone: BatesZone | None = None,
    *,
    min_pages: int = 2,
    min_document_coverage_pct: int = MIN_DOCUMENT_COVERAGE_PCT,
) -> tuple[str, ...]:
    """Every prefix in this matter that could be a production's, sorted.

    D-28's third condition asks whether the matter carries one prefix or more
    than one, and this is the answer it is asked of. A prefix counts when some
    shape carrying it clears **the same two bars** :func:`propose_format` uses
    to put a format in front of an operator at all — enough pages overall, and
    enough of one document's pages. Anything less is a stray line, and the
    Petrobras corpus is the standing proof that a matter with no production in
    it still produces stray lines.

    **A near-miss prefix counts.** ``jiCON`` on twenty one-page documents clears
    both bars and is counted as a second prefix even though it is, in fact,
    twenty misreadings of ``iiCON``. That is not an oversight — it is the
    ruling. DocIQ cannot distinguish a genuine ``iCON`` production from a
    misread ``iiCON`` one; that indistinguishability is the entire hazard D-28
    closes, and the ruled response to it is to refuse, not to guess which it is.
    A matter where the misreadings are numerous enough to look like a series is
    exactly a matter where repair must not happen.
    """
    candidates = detect_candidates(documents, zone)
    if not candidates:
        return ()
    by_shape: dict[tuple[str, str, str, str], list[BatesCandidate]] = {}
    for c in candidates:
        by_shape.setdefault(c.format_key, []).append(c)
    pages_by_key = {document_sort_key(d): len(d.pages) for d in documents}
    out: set[str] = set()
    for key, group in by_shape.items():
        if len(group) < min_pages:
            continue
        matched = Counter(c.sort_key for c in group)
        best = max(
            (round(100 * n / pages_by_key[k])
             for k, n in matched.items() if pages_by_key.get(k)),
            default=0,
        )
        if best >= min_document_coverage_pct:
            out.add(key[0])
    return tuple(sorted(out))


@dataclass(frozen=True, slots=True)
class NormalizedStamp:
    """One locator repaired under D-28 — the disclosure record.

    Carries what the page actually reads as well as what was written, because
    "this page's Bates number was repaired" is not a useful disclosure unless
    an expert can see what it was repaired FROM and by which rule.
    """

    sort_key: tuple[str, str, int]
    page_no: int
    read: str
    applied: str
    rule: str


@dataclass(frozen=True, slots=True)
class BatesApplication:
    """What :func:`apply_bates_reported` did, documents and disclosure together."""

    documents: tuple[DocumentRecord, ...]
    normalized: tuple[NormalizedStamp, ...] = ()
    normalization_available: bool = False
    matter_prefixes: tuple[str, ...] = ()

    @property
    def refused_reason(self) -> str | None:
        """Why prefix repair was not available, in one sentence, or ``None``."""
        if self.normalization_available:
            return None
        if len(self.matter_prefixes) > 1:
            return (
                f"prefix repair (D-28) is REFUSED on this matter: it carries "
                f"{len(self.matter_prefixes)} proposable Bates prefixes "
                f"({', '.join(repr(p) for p in self.matter_prefixes)}), and a "
                f"page repaired into the wrong series is a locator an expert "
                f"cannot defend"
            )
        return "prefix repair (D-28) did not apply: no confirmed format"


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


def _confirmed_token_re(fmt: BatesFormat) -> re.Pattern[str]:
    """A regex matching EXACTLY the confirmed format as a standalone token.

    Built from the same fields as :attr:`BatesFormat.pattern` — one grammar,
    two renderings — so the token search can never accept something the
    confirmed pattern would reject. The delimiters are "not an alphanumeric",
    which is what stops ``iiCON001483`` matching inside ``XiiCON0014837``.
    """
    widths = fmt.widths
    digits = (
        f"\\d{{{widths[0]}}}"
        if len(widths) == 1
        else "(?:" + "|".join(f"\\d{{{w}}}" for w in widths) + ")"
    )
    parts = [re.escape(fmt.prefix), re.escape(fmt.separator), digits]
    if fmt.suffix:
        parts.append(re.escape(fmt.suffix_sep))
        parts.append(re.escape(fmt.suffix))
    return re.compile(r"(?<![A-Za-z0-9])(" + "".join(parts) + r")(?![A-Za-z0-9])")


def _near_miss_token_re(fmt: BatesFormat) -> re.Pattern[str]:
    """The confirmed format with an OPEN prefix — everything else exact.

    The same construction as :func:`_confirmed_token_re` with one hole in it, so
    D-28's first condition ("digits, digit width, separator, suffix separator
    and suffix match EXACTLY; only the prefix may differ") is enforced by the
    shape of the pattern rather than by a check that could be forgotten. What
    lands in the ``prefix`` group is then put to :func:`near_miss_rule`, which
    is the only thing that may accept it.
    """
    widths = fmt.widths
    digits = (
        f"\\d{{{widths[0]}}}"
        if len(widths) == 1
        else "(?:" + "|".join(f"\\d{{{w}}}" for w in widths) + ")"
    )
    parts = [r"(?P<prefix>[A-Za-z][A-Za-z0-9]*)", re.escape(fmt.separator),
             f"(?P<digits>{digits})"]
    if fmt.suffix:
        parts.append(re.escape(fmt.suffix_sep))
        parts.append(re.escape(fmt.suffix))
    return re.compile(
        r"(?<![A-Za-z0-9])(?P<token>" + "".join(parts) + r")(?![A-Za-z0-9])")


def _zone_stamp(text: str, zone: BatesZone,
                token_re: re.Pattern[str]) -> str | None:
    """The one confirmed stamp in this page's zone, or ``None``.

    ``None`` when the zone holds no match **and** when it holds two different
    ones: a page whose footer read as two candidate locators is a page DocIQ
    cannot locate, and picking one would be a guess wearing a locator's
    clothes.

    **This is the only reading rule, for anchored and folded stamps alike**, and
    that unification is a fix, not a tidy-up. It used to be a fallback: an
    anchored zone line matching the confirmed format won outright, and only a
    page with no such line was searched for a folded one. D-25's footer re-OCR
    appends a recovered stamp as its own line, which is anchored — so a band
    pass that misread one digit would have produced an anchored ``iiCON003945``
    that beat the folded, correct ``iiCON003944`` already in the zone, and
    turned a right answer into a wrong one. Two different confirmed stamps in
    one zone is a REFUSAL wherever they came from.
    """
    found: set[str] = set()
    for _, line in zone.slice_lines(text):
        found.update(m.group(1) for m in token_re.finditer(line))
        if len(found) > 1:
            return None
    return next(iter(found)) if len(found) == 1 else None


def _zone_near_miss(text: str, zone: BatesZone, fmt: BatesFormat,
                    near_re: re.Pattern[str]) -> tuple[str, str] | None:
    """``(what the page reads, which rule)`` for a repairable stamp, or ``None``.

    Reached only when :func:`_zone_stamp` found nothing exact, and only when
    D-28's gates are open. The ambiguity rule is the same one and for the same
    reason: two readings that repair to two DIFFERENT locators is a refusal, not
    a choice. Two readings that repair to the SAME locator are not ambiguous —
    they are the same page read twice.
    """
    reads: dict[str, str] = {}
    for _, line in zone.slice_lines(text):
        for m in near_re.finditer(line):
            rule = near_miss_rule(m.group("prefix"), fmt.prefix)
            if rule is None:
                continue
            reads[m.group("token")] = rule
    if not reads:
        return None
    # Every accepted read repairs to the identical locator by construction —
    # the digits and every other part are pinned by the pattern — so more than
    # one distinct DIGIT run is the only way this can be ambiguous.
    digits = {near_re.search(tok).group("digits") for tok in reads}  # type: ignore[union-attr]
    if len(digits) > 1:
        return None
    token, rule = sorted(reads.items())[0]
    return token, rule


def apply_bates(
    documents: Sequence[DocumentRecord],
    decision: BatesDecision | None,
    zone: BatesZone | None = None,
    *,
    matter_prefix_census: Sequence[str] | None = None,
) -> tuple[DocumentRecord, ...]:
    """Write the confirmed stamps into ``PageRecord.bates``.

    The documents only. :func:`apply_bates_reported` is the same pass with
    D-28's disclosure attached, and callers that repair locators are expected to
    use it — a repair nobody can see is the thing §4 forbids.

    With no decision, or a rejected/pending one, the documents come back
    untouched — same objects, same order. That is the unstamped-matter path and
    it is deliberately a no-op rather than a pass that writes ``None`` over
    ``None``: an identity return is trivially provable.

    A line is written only if it matches the confirmed format *completely* —
    prefix, separator, digit width, suffix separator and suffix. Width is not
    cosmetic: a format confirmed for four or five digits used to accept
    ``MNFV 1234567890``, and a locator that is not in the production is worse
    than no locator, because the record it points at does not exist.

    **The zone is searched for the confirmed format as a standalone token
    (criterion-4 acceptance run, 2026-08-01).** The whole-line anchor is right
    for *detection*, where the grammar is open-ended and an unanchored match
    would read a date, a dollar figure or a paragraph number as a Bates number.
    It is too strict for *application*, where the format is already confirmed
    and known exactly. On the MNFV production, OCR'd pages fold the burned-in
    stamp into a longer line — a signature block that ends ``... in
    iiCON003961``, a page whose text came back as ``untij isfiyed
    iiCON003944`` — and the stamp is right there, correct, and rejected because
    it does not occupy the line alone. Measured on the acceptance sample:
    **every one of these was an OCR page**, and no native-text page needed it.

    The token search cannot widen what is accepted: it looks for exactly the
    string the operator confirmed, at exactly the confirmed widths, delimited so
    it cannot match inside a longer alphanumeric run. A page with two different
    stamps in its zone is left UNSTAMPED rather than guessed at — an ambiguous
    locator is the failure §4 rates worse than none. See :func:`_zone_stamp`
    for why anchored and folded stamps go through one rule and not two.
    """
    return apply_bates_reported(
        documents, decision, zone,
        matter_prefix_census=matter_prefix_census).documents


def apply_bates_reported(
    documents: Sequence[DocumentRecord],
    decision: BatesDecision | None,
    zone: BatesZone | None = None,
    *,
    matter_prefix_census: Sequence[str] | None = None,
) -> BatesApplication:
    """:func:`apply_bates`, with D-28's prefix repair and its disclosure.

    A page whose zone yields the confirmed format exactly is stamped with it and
    is **not** a repair. Only a page that yields nothing exact is offered to
    D-28, and only when every gate is open:

    * the matter carries exactly ONE proposable prefix (:func:`matter_prefixes`);
    * that prefix is the confirmed one;
    * the page is one DocIQ had to OCR;
    * the read differs from the confirmed format in the prefix ALONE, and by a
      near miss (:func:`near_miss_rule`).

    ``matter_prefix_census`` exists because the third gate is a question about
    the MATTER and this function is sometimes handed one document at a time. A
    caller that streams documents — the acceptance harness does, to bound
    memory — MUST pass the census it computed over the whole corpus. Left
    ``None``, the census is computed from ``documents``, which is correct for a
    whole-matter call and would silently ask the gate about the wrong
    population for a partial one. Getting that wrong is how a single-document
    view would report "one prefix" for a matter that has four.
    """
    if decision is None or not decision.applies:
        return BatesApplication(tuple(sorted(documents, key=document_sort_key)))
    assert decision.format is not None
    fmt = decision.format
    z = zone or BatesZone()
    token_re = _confirmed_token_re(fmt)

    census = tuple(matter_prefix_census) if matter_prefix_census is not None \
        else matter_prefixes(documents, zone)
    repair_ok = census == (fmt.prefix,)
    near_re = _near_miss_token_re(fmt) if repair_ok else None

    out: list[DocumentRecord] = []
    repaired: list[NormalizedStamp] = []
    for doc in sorted(documents, key=document_sort_key):
        key = document_sort_key(doc)
        pages: list[PageRecord] = []
        changed = False
        for page in doc.pages:
            stamp = _zone_stamp(page.text, z, token_re)
            if stamp is None and near_re is not None and page.kind is PageKind.OCR:
                got = _zone_near_miss(page.text, z, fmt, near_re)
                if got is not None:
                    read, rule = got
                    stamp = fmt.prefix + read[len(read) - _tail_len(fmt, read):]
                    repaired.append(NormalizedStamp(
                        sort_key=key, page_no=page.page_no, read=read,
                        applied=stamp, rule=rule))
            if stamp is not None and page.bates != stamp:
                pages.append(page.evolve(bates=stamp))
                changed = True
            else:
                pages.append(page)
        out.append(replace(doc, pages=tuple(pages)) if changed else doc)
    return BatesApplication(
        documents=tuple(out),
        normalized=tuple(repaired),
        normalization_available=repair_ok,
        matter_prefixes=census,
    )


def _tail_len(fmt: BatesFormat, read: str) -> int:
    """How much of a read token is everything-but-the-prefix.

    Derived from the format rather than from the read, so the repaired locator
    is assembled from the confirmed prefix plus the part of the page's own
    reading that the pattern already pinned — never from a guess about where
    the prefix ended.
    """
    m = _near_miss_token_re(fmt).fullmatch(read)
    assert m is not None, read
    return len(read) - len(m.group("prefix"))


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
