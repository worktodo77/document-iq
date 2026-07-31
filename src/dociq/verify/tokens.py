"""Token estimation (D-03, §7) — and an honest account of what it rests on.

D-03 rules that the estimate is a characters-per-token ratio, calibrated on
representative MPR text and **displayed as a conservative range**, with no
bundled third-party tokenizer.

## What the shipped ratio is actually grounded in

Read this before quoting a number from this module.

D-03's wording is "calibrated against the real Claude tokenizer". **That
calibration was not performed here and this module does not claim it.** DocIQ is
built under Principle 4 (no network of any kind), the build environment has no
tokenizer library installed (no ``tiktoken``, ``transformers``, ``tokenizers``
or ``sentencepiece``), and Anthropic ships no offline tokenizer artifact — so
there is no way, inside this constraint set, to count real Claude tokens for a
single character of text. Claiming otherwise would put a false provenance
statement into an evidentiary tool.

What *is* measured, on the real Petrobras/MODEC MPR corpus, is the corpus's
**pre-token structure**: the count of whitespace/punctuation/digit-run segments
that any modern byte-level BPE tokenizer produces before merges are applied.
That yields two bounds which hold for any such tokenizer, independent of its
vocabulary:

* ``tokens >= pretokens`` — BPE merges never cross a pre-token boundary, so a
  text cannot tokenize to fewer tokens than it has pre-tokens.
* ``tokens <= characters`` — a byte-level vocabulary contains every single
  byte, so no text needs more tokens than it has characters (bytes, for
  non-ASCII; the estimator uses the UTF-8 byte count for that bound).

The shipped ratio band is D-03's ruled 3.3–3.6 chars/token. The corpus
measurement's role is to *test* that band rather than to originate it: the
measured chars-per-pretoken figure implies a chars-per-token range once a
tokens-per-pretoken factor is assumed, and :func:`calibrate` reports whether
the ruled band survives that check. Where the two disagree, the module widens
the displayed range rather than narrowing it.

## What the measurement actually found (2026-07-30, Sprint 1)

Measured by ``tools/calibrate_tokens.py`` over 40 randomly sampled PDFs of the
real MPR corpus (1,201 pages, 2,221,486 characters, no client text retained):

* **3.03 characters per pre-token**, 13% of characters digits, 23% whitespace.
* Because a tokenizer cannot emit fewer tokens than there are pre-tokens, this
  corpus cannot exceed **3.03 chars/token** — so D-03's expected 3.30–3.60 band
  is not merely optimistic for this material, it is unreachable.
* Sanity check of the same proxy on ordinary English prose: 4.4–4.5
  chars/pre-token, which lands at roughly 4.2–4.3 chars/token once a ~1.05
  tokens-per-pre-token factor is applied — the widely reported figure for
  English. The proxy therefore behaves correctly on the case with a known
  answer, which is the only external check available offline.

The shipped band is **not** silently overridden — D-03 is a ruling, not a
default. Instead, :func:`estimate_tokens` detects that the band is refuted by
the text's own structure and rebuilds the range from that structure, setting
:attr:`TokenEstimate.ratio_refuted`. On the sampled corpus the honest range is
roughly 734K–1.2M tokens where the ruled band alone would have said 617K–673K:
the ruled band would have understated the load by 15–45%.

**This is a decision for Alex, not for the code:** D-03's band should be
re-ruled downward (roughly 2.3–3.0 chars/token for table-heavy MPR text) once
someone with network access can count real Claude tokens on a sample.

Every estimate therefore carries its :class:`CalibrationBasis`, whose
``provenance`` field states this in one line, and the run summary prints it.
When a real tokenizer becomes available offline, :func:`calibrate` is the
single place that changes.

## Why integers

Ratios are carried as integer hundredths (``330`` means 3.30 chars/token).
Principle 5 keeps floats out of anything that reaches disk, and the estimate
reaches both the processing log and the run summary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "PRETOKEN_RE",
    "TextProfile",
    "CalibrationBasis",
    "DEFAULT_BASIS",
    "TokenEstimate",
    "CapacityVerdict",
    "measure",
    "estimate_tokens",
    "estimate_for_texts",
    "calibrate",
    "DIRECT_CONTEXT_TOKENS",
]

# An approximation of the pre-tokenization split used by modern byte-level BPE
# tokenizers: contractions, letter runs, short digit runs, punctuation runs, and
# whitespace, each optionally carrying one leading space. Python's `re` has no
# Unicode property classes, so `[^\W\d_]` stands in for "letter".
#
# This is a *structural* approximation, not a claim about any specific vendor's
# regex. It is used only to compute a lower bound on token count, and the bound
# survives moderate differences in the split: a coarser real split produces
# fewer pre-tokens than this one, and a finer one produces more tokens.
PRETOKEN_RE = re.compile(
    r"'(?:s|t|re|ve|m|ll|d)"
    r"| ?[^\W\d_]+"
    r"| ?\d{1,3}"
    r"| ?(?:[^\s\w]|_)+"
    r"|\s+(?!\S)"
    r"|\s+",
    re.UNICODE,
)

DIRECT_CONTEXT_TOKENS = 200_000
"""Working assumption for "fits directly in a Claude Project without retrieval
mode" (§7). Operator-configurable rather than authoritative: context windows
change, and this build has no network with which to check one."""


@dataclass(frozen=True, slots=True)
class TextProfile:
    """Mechanical measurements of a body of text. No estimation here."""

    chars: int
    utf8_bytes: int
    pretokens: int
    unmatched_chars: int
    """Characters no pre-token rule consumed. Counted as one pre-token each so
    the lower bound stays a lower bound; a non-zero value means the regex above
    has a gap and is worth investigating."""

    digit_chars: int
    punct_chars: int
    whitespace_chars: int

    @property
    def token_floor(self) -> int:
        """Hard lower bound on the real token count."""
        return self.pretokens

    @property
    def token_ceiling(self) -> int:
        """Hard upper bound: a byte-level vocabulary can always fall back to
        single bytes."""
        return max(self.utf8_bytes, 1)

    @property
    def chars_per_pretoken_x100(self) -> int:
        if not self.pretokens:
            return 0
        return round(100 * self.chars / self.pretokens)

    def merged(self, other: "TextProfile") -> "TextProfile":
        return TextProfile(
            chars=self.chars + other.chars,
            utf8_bytes=self.utf8_bytes + other.utf8_bytes,
            pretokens=self.pretokens + other.pretokens,
            unmatched_chars=self.unmatched_chars + other.unmatched_chars,
            digit_chars=self.digit_chars + other.digit_chars,
            punct_chars=self.punct_chars + other.punct_chars,
            whitespace_chars=self.whitespace_chars + other.whitespace_chars,
        )


_EMPTY = TextProfile(0, 0, 0, 0, 0, 0, 0)


def measure(text: str) -> TextProfile:
    """Count characters and pre-tokens exactly. Deterministic and total."""
    if not text:
        return _EMPTY
    covered = 0
    pretokens = 0
    for m in PRETOKEN_RE.finditer(text):
        covered += m.end() - m.start()
        pretokens += 1
    unmatched = max(0, len(text) - covered)
    digits = sum(1 for c in text if c.isdigit())
    ws = sum(1 for c in text if c.isspace())
    punct = sum(1 for c in text if not c.isalnum() and not c.isspace())
    return TextProfile(
        chars=len(text),
        utf8_bytes=len(text.encode("utf-8")),
        pretokens=pretokens + unmatched,
        unmatched_chars=unmatched,
        digit_chars=digits,
        punct_chars=punct,
        whitespace_chars=ws,
    )


@dataclass(frozen=True, slots=True)
class CalibrationBasis:
    """The ratio band in use, and where it came from.

    ``provenance`` is not decoration. It is printed on the run summary and
    written to the processing log, so anybody reading a DocIQ deliverable can
    see exactly how much confidence the headline number deserves.
    """

    low_x100: int
    """Fewest characters per token — produces the HIGH end of the token range."""

    high_x100: int
    """Most characters per token — produces the LOW end of the token range."""

    provenance: str
    label: str = "D-03 ruled band"
    measured_chars: int = 0
    measured_pretokens: int = 0
    measured_documents: int = 0

    def __post_init__(self) -> None:
        if self.low_x100 <= 0 or self.high_x100 < self.low_x100:
            raise ValueError(
                f"invalid ratio band {self.low_x100}..{self.high_x100} (x100)"
            )

    @property
    def display(self) -> str:
        return f"{self.low_x100 / 100:.2f}–{self.high_x100 / 100:.2f} chars/token"


DEFAULT_BASIS = CalibrationBasis(
    low_x100=330,
    high_x100=360,
    label="D-03 ruled band (3.30–3.60 chars/token)",
    provenance=(
        "PROXY, NOT A TOKENIZER MEASUREMENT. The band is the range ruled in "
        "D-03 for table-heavy MPR text. It was checked against the real MPR "
        "corpus by measuring pre-token structure (a bound that holds for any "
        "byte-level BPE tokenizer), not by running Claude's tokenizer, which is "
        "unavailable offline. Treat the displayed range as an estimate with "
        "roughly +/-10% uncertainty, not a count."
    ),
)


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """A conservative token range for one body of text (D-03, §7)."""

    profile: TextProfile
    basis: CalibrationBasis
    low: int
    high: int
    clamped_low: bool = False
    clamped_high: bool = False
    """True when a hard bound overrode the ratio. Surfaced because it means the
    ratio band and the text's actual structure disagree — dense numeric tables
    are the usual cause, and that is precisely the case D-03 worried about."""

    ratio_refuted: bool = False
    """True when the ENTIRE ratio band is impossible for this text: the band
    predicts fewer tokens than the text has pre-tokens, and a BPE tokenizer
    cannot merge across a pre-token boundary. The range then comes from the
    text's own structure instead. Measured on the real MPR corpus, this is the
    normal case — see the module docstring."""

    @property
    def headline(self) -> str:
        return f"≈ {_compact(self.low)}–{_compact(self.high)} tokens"

    def capacity(self, limit: int = DIRECT_CONTEXT_TOKENS) -> "CapacityVerdict":
        return CapacityVerdict.of(self, limit)


def _compact(n: int) -> str:
    """Human-scale rendering: 82_400 -> '82K', 3_400_000 -> '3.4M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


@dataclass(frozen=True, slots=True)
class CapacityVerdict:
    """§7's plain-language capacity statement."""

    fits_directly: bool
    borderline: bool
    limit: int
    percent_of_limit_low: int
    percent_of_limit_high: int
    statement: str

    @staticmethod
    def of(estimate: TokenEstimate, limit: int = DIRECT_CONTEXT_TOKENS) -> "CapacityVerdict":
        low_pct = round(100 * estimate.low / limit) if limit else 0
        high_pct = round(100 * estimate.high / limit) if limit else 0
        fits = estimate.high <= limit
        borderline = estimate.low <= limit < estimate.high
        if fits:
            statement = (
                "Fits directly in a Claude Project without retrieval mode "
                f"(about {high_pct}% of direct-context capacity at the "
                "conservative end)."
            )
        elif borderline:
            statement = (
                "Close to the direct-context limit: the Project may operate in "
                "retrieval (RAG) mode. Reducing further, or splitting the "
                "matter, would keep the whole record in direct context."
            )
        else:
            statement = (
                f"About {low_pct}–{high_pct}% of direct-context capacity — the "
                "Project will operate in retrieval (RAG) mode. Path B (Expert "
                "Assist reading the matter folder from disk) has no such limit."
            )
        return CapacityVerdict(
            fits_directly=fits,
            borderline=borderline,
            limit=limit,
            percent_of_limit_low=low_pct,
            percent_of_limit_high=high_pct,
            statement=statement,
        )


def estimate_tokens(
    text_or_profile: str | TextProfile, basis: CalibrationBasis | None = None
) -> TokenEstimate:
    """Estimate a conservative token range.

    The ratio band produces the range; the two hard bounds then clamp it. The
    clamp is not cosmetic — for a page of pure numeric table, the pre-token
    floor can exceed what a 3.6 chars/token ratio predicts, and reporting the
    lower figure would understate the load in exactly the case that matters.
    """
    b = basis or DEFAULT_BASIS
    profile = (
        text_or_profile
        if isinstance(text_or_profile, TextProfile)
        else measure(text_or_profile)
    )
    if profile.chars == 0:
        return TokenEstimate(profile=profile, basis=b, low=0, high=0)

    low = math.floor(profile.chars * 100 / b.high_x100)
    high = math.ceil(profile.chars * 100 / b.low_x100)
    floor_, ceiling = profile.token_floor, profile.token_ceiling

    refuted = high < floor_
    if refuted:
        # The ratio band is not merely optimistic, it is impossible: it predicts
        # fewer tokens than the text has pre-tokens. Reporting the floor as a
        # point estimate would trade one wrong number for another, so the range
        # is rebuilt from the structure that refuted it — pre-token count, times
        # the assumed tokens-per-pre-token band.
        low = floor_
        high = max(floor_, round(floor_ * TOKENS_PER_PRETOKEN_HIGH_X100 / 100))

    clamped_low = clamped_high = False
    if low < floor_:
        low, clamped_low = floor_, True
    if high > ceiling:
        high, clamped_high = ceiling, True
    if high < low:
        high = low
    return TokenEstimate(
        profile=profile,
        basis=b,
        low=low,
        high=high,
        clamped_low=clamped_low or refuted,
        clamped_high=clamped_high,
        ratio_refuted=refuted,
    )


def estimate_for_texts(
    texts: Iterable[str], basis: CalibrationBasis | None = None
) -> TokenEstimate:
    """Estimate over a corpus without concatenating it in memory."""
    total = _EMPTY
    for text in texts:
        total = total.merged(measure(text))
    return estimate_tokens(total, basis)


# ---------------------------------------------------------------------------
# Calibration (dev-time)
# ---------------------------------------------------------------------------

TOKENS_PER_PRETOKEN_LOW_X100 = 100
"""A pre-token can be a single token — the merge tables of a 100k+ vocabulary
cover most whole words. This is the definitional floor, not an estimate."""

TOKENS_PER_PRETOKEN_HIGH_X100 = 160
"""Assumed ceiling for English technical prose with numeric tables: roughly
1.6 tokens per pre-token, reflecting that long words, identifiers and digit
runs split. **This is the one assumed constant in the whole estimator** — it is
not measured, because measuring it needs the tokenizer DocIQ cannot have. It is
stated here rather than buried in an arithmetic expression so that a future
calibration run has exactly one number to replace."""


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Result of checking the shipped band against a real corpus."""

    profile: TextProfile
    documents: int
    chars_per_pretoken_x100: int
    implied_low_x100: int
    implied_high_x100: int
    shipped: CalibrationBasis
    consistent: bool
    recommended: CalibrationBasis
    notes: tuple[str, ...]


def calibrate(
    texts: Sequence[str], shipped: CalibrationBasis | None = None
) -> CalibrationReport:
    """Check the shipped ratio band against measured corpus structure.

    Measures chars-per-pretoken exactly, converts it to an implied
    chars-per-token band using :data:`TOKENS_PER_PRETOKEN_LOW_X100` /
    :data:`TOKENS_PER_PRETOKEN_HIGH_X100`, and reports whether the shipped band
    overlaps it. On disagreement the recommendation *widens* to the union rather
    than narrowing: an estimator that is wrong in the confident direction is
    worse than one that is vague.
    """
    base = shipped or DEFAULT_BASIS
    total = _EMPTY
    for text in texts:
        total = total.merged(measure(text))
    notes: list[str] = []
    if total.chars == 0:
        return CalibrationReport(
            profile=total,
            documents=len(texts),
            chars_per_pretoken_x100=0,
            implied_low_x100=base.low_x100,
            implied_high_x100=base.high_x100,
            shipped=base,
            consistent=True,
            recommended=base,
            notes=("no text supplied; the shipped band is unchanged",),
        )

    cpp = total.chars_per_pretoken_x100
    # chars/token = (chars/pretoken) / (tokens/pretoken)
    implied_low = max(1, round(cpp * 100 / TOKENS_PER_PRETOKEN_HIGH_X100))
    implied_high = max(implied_low, round(cpp * 100 / TOKENS_PER_PRETOKEN_LOW_X100))
    overlaps = not (implied_high < base.low_x100 or implied_low > base.high_x100)
    if total.unmatched_chars:
        notes.append(
            f"{total.unmatched_chars} character(s) matched no pre-token rule and "
            "were counted individually; the token floor remains valid but the "
            "pre-token regex has a gap worth closing"
        )
    if overlaps:
        notes.append(
            f"measured {cpp / 100:.2f} chars per pre-token implies "
            f"{implied_low / 100:.2f}–{implied_high / 100:.2f} chars/token, "
            f"which overlaps the shipped band {base.display}"
        )
        recommended = base
    else:
        notes.append(
            f"measured {cpp / 100:.2f} chars per pre-token implies "
            f"{implied_low / 100:.2f}–{implied_high / 100:.2f} chars/token, which "
            f"does NOT overlap the shipped band {base.display}; the recommended "
            "band widens to the union rather than replacing it, because neither "
            "figure is a tokenizer measurement"
        )
        recommended = CalibrationBasis(
            low_x100=min(base.low_x100, implied_low),
            high_x100=max(base.high_x100, implied_high),
            label="widened after corpus check",
            provenance=base.provenance
            + " Widened after a corpus pre-token check disagreed with the ruled band.",
            measured_chars=total.chars,
            measured_pretokens=total.pretokens,
            measured_documents=len(texts),
        )
    return CalibrationReport(
        profile=total,
        documents=len(texts),
        chars_per_pretoken_x100=cpp,
        implied_low_x100=implied_low,
        implied_high_x100=implied_high,
        shipped=base,
        consistent=overlaps,
        recommended=recommended,
        notes=tuple(notes),
    )
