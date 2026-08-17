"""Folding an outline label to the key a template keys to.

Every rule in this module was written against a measurement, and the measurement
is `docs/verification/sections_2026-08-17.md` Q1 over all 298 PDFs. The corpus
supplied three facts that the template design has to survive, and each one is a
step here:

**547 distinct section names.** Too many to enumerate in a shipped template, so a
template matches a *family* rather than a literal. The count is 547 only after
numbering is stripped; counted raw it is 750, and the difference is entirely
``4.5 ENGINEERING`` vs ``5.4 ENGINEERING`` — the same section under a different
number in a different month's report.

**116 of those 547 labels carry project-identifying text** — ``MV32 APPENDICES``,
``STATUS OF PETROBRAS TQ``, ``BOMESC YARD``. That is 21% of the vocabulary, and
D-24 forbids a shipped template attributable to a corpus project. A template that
matched these verbatim would have ``MV32`` compiled into a Long International
deliverable. So project tokens are stripped before matching, and the token list
is **supplied per matter, never shipped in this file**.

**The third most frequent label in the corpus is Portuguese** — ``PAGINA EM
BRANCO`` (blank page), 61 occurrences. An English-only template does not see it.
Matching is therefore accent-folded and templates may carry non-English patterns;
nothing here assumes the record is in English.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "family_key",
    "normalize_label",
    "strip_numbering",
    "strip_project_tokens",
]

_NUMBERING_RE = re.compile(r"^(\d+(\.\d+)*|[A-Z])[.)]?\s+")
"""One leading numbering component: ``3``, ``3.1``, ``3.1.2``, ``A)``.

Applied repeatedly rather than once — normalization turns ``4.5.1 MARINE
ENGINEERING`` into ``4 5 1 MARINE ENGINEERING``, which needs three passes.
"""

_BARE_POSITION_RE = re.compile(
    r"^(SLIDE(\s+NUMBER)?|PAGE|SHEET|SECTION)?[\s\d]*$", re.IGNORECASE
)
"""A label that names a position rather than a section.

``Slide Number 33`` from a PowerPoint export, and — the case the first version
of the measurement probe missed — a bare ``5.1``, which normalizes to ``5 1``
and is not matched by an anchored ``\\d+$``. Such a label places a page and
names nothing, so it must not become a family key: a template keyed to ``5 1``
would match a different section every month.
"""


def normalize_label(text: str) -> str:
    """Fold a label for comparison: accents removed, non-alphanumerics collapsed
    to single spaces, upper-cased.

    Accent folding is load-bearing rather than tidy — this corpus is Brazilian
    and its outlines carry ``PÁGINA EM BRANCO`` with and without the accent in
    the same production run.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^A-Za-z0-9]+", " ", folded)
    return folded.strip().upper()


def strip_numbering(key: str) -> str:
    """Remove every leading numbering component, not just the first."""
    previous = None
    while previous != key:
        previous = key
        key = _NUMBERING_RE.sub("", key)
    return key.strip()


def strip_project_tokens(key: str, project_tokens: tuple[str, ...] = ()) -> str:
    """Remove matter-specific tokens (vessel, client, yard) from a label.

    ``project_tokens`` is supplied by the caller from the matter's own
    configuration and is **never defaulted to a list of real project names**.
    A file in this package naming ``MV32`` would be the first step to the
    matter-attributable template D-24 forbids, and it would be wrong for the
    next matter regardless.

    Tokens are removed as whole words only. Substring removal would turn
    ``EBR`` into a rule that mangles ``EBRD FINANCING`` — and ``EBR`` is a real
    yard name in this corpus, so that is not a hypothetical.
    """
    if not project_tokens:
        return key
    for token in project_tokens:
        folded = normalize_label(token)
        if not folded:
            continue
        key = re.sub(rf"\b{re.escape(folded)}\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def family_key(label: str, project_tokens: tuple[str, ...] = ()) -> str | None:
    """The key a template matches against, or ``None`` when the label names no
    section at all.

    ``None`` is returned for a label that is purely positional, and for one that
    is empty once numbering and project tokens are removed — ``MV32`` on its own
    is a document's project, not a section of it. Returning ``None`` rather than
    an empty string forces every caller to handle "this label cannot be keyed",
    which under the §1 asymmetry means the page keeps.
    """
    key = normalize_label(label)
    if not key or _BARE_POSITION_RE.fullmatch(key):
        return None
    key = strip_numbering(key)
    if not key or _BARE_POSITION_RE.fullmatch(key):
        return None
    key = strip_project_tokens(key, project_tokens)
    if not key or _BARE_POSITION_RE.fullmatch(key):
        return None
    return key
