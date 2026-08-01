"""Document date detection — deterministic, no AI (§4 Stage 1, §12).

Adapted from MIP 3.9's ``api/doc_census.doc_date`` and the date regex battery
in ``api/rag._extract_dates``. Two changes from the original:

* **First-appearance order, not sorted.** ``DocumentRecord.detected_dates`` is
  documented as "in first-appearance order"; MIP 3.9 returned a sorted set,
  which silently reorders. The order is a locator of sorts — the first date in
  a monthly report is nearly always the report's own period — so it carries
  information that sorting destroys.
* **No dependency on the project date convention.** MIP 3.9 read a mutable
  module-level ``_DATE_CONVENTION``. Global mutable state that changes output
  bytes is a determinism bug by construction; the convention is a parameter
  here, defaulting to US month-first as it did there.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# "March 1, 2024" / "Mar 1 2024" / "March 1st, 2024" / "Mar. 1, 2024"
_RE_MDY = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE)
# "1 March 2024" / "1st March, 2024" / "01-Mar-2024" / "01-Mar-24"
_RE_DMY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?[\s-](" + _MONTH_ALT + r")\.?[,]?[\s-](\d{2,4})\b",
    re.IGNORECASE)
# Numeric "7/16/2024", "07/16/24", "3-1-2024".
_RE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b|\b(\d{1,2})-(\d{1,2})-(\d{2,4})\b")
# European dot format "18.06.2025" (DAY-first). The dot collides with version
# numbers and decimals, so a 4-digit year AND a valid calendar date are both
# required before it counts.
_RE_DOT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

_ISO_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

DEFAULT_CONVENTION = "us"
"""Month-first for ambiguous numeric dates. 'eu'/'uk'/'au'/'ca' are day-first;
unambiguous values (16/07) are read correctly under either."""


def _full_year(y: int) -> int:
    """Expand a 2-digit year (00-69 → 20xx, 70-99 → 19xx)."""
    return y if y >= 100 else (2000 + y if y < 70 else 1900 + y)


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def detect_dates(text: str, *, convention: str = DEFAULT_CONVENTION,
                 limit: int | None = None) -> tuple[str, ...]:
    """Every valid date in ``text``, ISO-formatted, in first-appearance order.

    Each candidate is validated as a real calendar date, which is what keeps
    phone numbers, job numbers and part numbers out. ``limit`` caps the result
    for the document-level summary; the cap is the caller's to disclose.
    """
    day_first = (convention or DEFAULT_CONVENTION).lower() not in ("us",)
    hits: list[tuple[int, str]] = []

    for m in _RE_ISO.finditer(text):
        iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            hits.append((m.start(), iso))
    for m in _RE_MDY.finditer(text):
        mi = _MONTHS.get(m.group(1).lower())
        if mi:
            iso = _iso(_full_year(int(m.group(3))), mi, int(m.group(2)))
            if iso:
                hits.append((m.start(), iso))
    for m in _RE_DMY.finditer(text):
        mi = _MONTHS.get(m.group(2).lower())
        if mi:
            iso = _iso(_full_year(int(m.group(3))), mi, int(m.group(1)))
            if iso:
                hits.append((m.start(), iso))
    for m in _RE_NUM.finditer(text):
        g = m.groups()
        a, b, c = (g[0], g[1], g[2]) if g[0] else (g[3], g[4], g[5])
        if not a:
            continue
        ai, bi = int(a), int(b)
        if ai > 12 and bi <= 12:      # unambiguous: force D/M
            mm, dd = bi, ai
        elif bi > 12 and ai <= 12:
            mm, dd = ai, bi
        else:
            mm, dd = (bi, ai) if day_first else (ai, bi)
        iso = _iso(_full_year(int(c)), mm, dd)
        if iso:
            hits.append((m.start(), iso))
    for m in _RE_DOT.finditer(text):
        ai, bi = int(m.group(1)), int(m.group(2))
        dd, mm = (ai, bi) if bi <= 12 else (bi, ai)
        iso = _iso(int(m.group(3)), mm, dd)
        if iso:
            hits.append((m.start(), iso))

    # Sort by position, then by the ISO string: two patterns can match at the
    # same offset (an ISO date is also a valid numeric date to the dot rule),
    # and the tiebreak has to be a value, not the order the loops happened to
    # run in — otherwise reordering the loops changes output bytes.
    out: list[str] = []
    seen: set[str] = set()
    for _, iso in sorted(hits, key=lambda h: (h[0], h[1])):
        if iso not in seen:
            seen.add(iso)
            out.append(iso)
            if limit is not None and len(out) >= limit:
                break
    return tuple(out)


def document_date(filename: str, text: str,
                  *, convention: str = DEFAULT_CONVENTION) -> str | None:
    """The document's own date, or ``None``.

    Filename first: a production names its files ``2024-07-16 Daily Report.pdf``
    far more reliably than its body text dates itself, and a body-first rule
    picks up whatever date the letterhead happens to mention. Falls back to the
    first date found in the leading text.
    """
    m = _ISO_IN_NAME.search(filename or "")
    if m:
        iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso
    found = detect_dates((text or "")[:4000], convention=convention, limit=1)
    return found[0] if found else None
