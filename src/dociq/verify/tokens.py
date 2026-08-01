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
produced by :data:`PRETOKEN_RE`, which is DocIQ's own approximation of the
pre-tokenization step of a modern byte-level BPE tokenizer.

## Exactly one of the two bounds is tokenizer-independent

* ``tokens <= utf8_bytes`` — **SOUND, and asserted.** A byte-level vocabulary
  contains every single byte, so a byte-level BPE tokenizer can always fall
  back to one token per byte and never needs more than that. This holds for any
  such tokenizer, whatever its vocabulary or its pre-tokenizer.

* ``tokens >= pretokens`` — **NOT SOUND, and no longer asserted.** Codex review
  #1 finding B-6 is correct on this point and it is worth stating precisely why,
  because the old argument sounds right. A byte-level BPE cannot merge across
  *its own* pre-token boundaries. It can merge freely across boundaries that
  **this module invented**. :data:`PRETOKEN_RE` splits digit runs every three
  digits (``\\d{1,3}``) and cuts letter runs from punctuation runs; a real
  tokenizer whose pre-tokenization is coarser — longer digit runs, punctuation
  glued to an adjacent word — emits *fewer* tokens than this module counts
  pre-tokens.

  This is not a theoretical caveat on this corpus. The measured MPR text is
  **13% digits**, which is precisely where the invented boundaries are densest,
  so the inflation lands exactly where the material is.

So the pre-token count is a **characterization of the text's structure under
stated assumptions**, not a floor. It is still the most informative thing DocIQ
can measure offline, and it is still what makes the D-03 band checkable — it
just may not be quoted as a bound.

## The assumptions, stated once, and travelling with every figure

:data:`TOKENS_PER_PRETOKEN_LOW_X100` and :data:`TOKENS_PER_PRETOKEN_HIGH_X100`
are the two assumed constants of the whole estimator. Both are assumptions, not
measurements; :data:`ASSUMPTIONS` states them in words and every
:class:`TokenEstimate` carries them into ``provenance``, which reaches the
processing log, the run summary PDF, the upload package README and the GUI.

## What the measurement found, and what it does and does not establish

Measured by ``tools/calibrate_tokens.py`` over 40 randomly sampled PDFs of the
real MPR corpus (1,201 pages, 2,221,486 characters, no client text retained):
**3.03 characters per pre-token**, 13% of characters digits, 23% whitespace.
The first full pipeline run measured **2.91** chars/pre-token over its own
emitted page text, and ``tools/calibrate_tokens.py`` over all 298 PDFs measured
**2.53**.

Under the assumptions above, 2.53 chars/pre-token implies roughly 1.58–3.61
chars/token — which **overlaps D-03's ruled 3.30–3.60 band**. At the coarse end
of the assumed pre-tokenization allowance the corpus lands at about 3.5
chars/token, inside the ruled band.

**D-03 is therefore NOT refuted.** The earlier claim in this module — that the
band was "unreachable" and had been refuted by the corpus's own structure — was
built on the unsound floor argument and has been withdrawn. What survives is
weaker and still useful: the corpus is denser than ordinary prose, the ruled
band sits at the coarse end of what the structure allows rather than in the
middle of it, and the true figure cannot be pinned down without a tokenizer.

:attr:`TokenEstimate.ratio_refuted` is kept (contract v1.2.0, amendment A-03)
but is now set only when the ruled band lies entirely *below* the range the
measured structure allows under the stated assumptions — a conditional
inconsistency, disclosed as such in ``provenance``, never a universal
refutation.

The remaining honest recommendation is unchanged in direction and weaker in
force: **someone with network access should count real Claude tokens on a
sample of this material.** Until then no number here is a count.

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
    "ASSUMPTIONS",
    "SOUND_BOUND",
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
    "TOKENS_PER_PRETOKEN_LOW_X100",
    "TOKENS_PER_PRETOKEN_HIGH_X100",
]

# An approximation of the pre-tokenization split used by modern byte-level BPE
# tokenizers: contractions, letter runs, short digit runs, punctuation runs, and
# whitespace, each optionally carrying one leading space. Python's `re` has no
# Unicode property classes, so `[^\W\d_]` stands in for "letter".
#
# This is a *structural* approximation, not a claim about any specific vendor's
# regex — and the difference matters. A real tokenizer with a COARSER split
# merges across boundaries invented here and emits fewer tokens than this regex
# yields pre-tokens, which is why the pre-token count is not a bound. See the
# module docstring.
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


TOKENS_PER_PRETOKEN_LOW_X100 = 70
"""Assumed FEWEST tokens per DocIQ pre-token: 0.70.

Below 1.00 deliberately, and this is the correction Codex review #1 finding B-6
forced. A tokenizer emits fewer tokens than DocIQ counts pre-tokens whenever its
pre-tokenization is coarser than :data:`PRETOKEN_RE` — most importantly on digit
runs, which this regex cuts every three digits while a real pre-tokenizer may
keep whole. On text that is 13% digits an allowance of this size is not
generous, it is the minimum honest one.

0.70 is **assumed, not derived**. It cannot be derived without the artifact
Principle 4 forbids fetching. It is stated here rather than buried in an
expression so a future calibration run has exactly one number to replace at the
low end."""

TOKENS_PER_PRETOKEN_HIGH_X100 = 160
"""Assumed MOST tokens per DocIQ pre-token: 1.60. Reflects long words,
identifiers and digit runs splitting further than this regex does. Also
assumed, for the same reason and with the same remedy."""

ASSUMPTIONS: tuple[str, ...] = (
    "ASSUMPTION A1 (pre-tokenization). DocIQ's pre-token regex is an "
    "approximation of a byte-level BPE pre-tokenizer, not any vendor's. A real "
    "tokenizer with a coarser split — notably one that keeps digit runs longer "
    "than three digits together — merges across boundaries DocIQ invented and "
    "emits FEWER tokens than DocIQ counts pre-tokens. The allowance for this is "
    f"{TOKENS_PER_PRETOKEN_LOW_X100 / 100:.2f} tokens per pre-token and it is "
    "assumed, not measured. This material is about 13% digits, so the allowance "
    "is material rather than nominal.",
    "ASSUMPTION A2 (merge depth). At the other end, a pre-token may split into "
    f"as many as {TOKENS_PER_PRETOKEN_HIGH_X100 / 100:.2f} tokens for long "
    "words, identifiers and numbers. Also assumed, not measured.",
    "ASSUMPTION A3 (no tokenizer was run). No token in any DocIQ figure was "
    "produced by a tokenizer. Principle 4 forbids the network and no offline "
    "Claude tokenizer artifact exists, so every figure here is derived from "
    "character and pre-token counts alone.",
)
"""The complete set of assumptions any DocIQ token figure rests on.

Kept as data rather than prose in a docstring because it travels: it is written
into ``TokenEstimate.provenance``, which reaches the processing log, the run
summary PDF, the upload package README and the GUI. A figure whose assumptions
stay in the source file is a figure whose reader cannot check it."""

SOUND_BOUND = (
    "The one tokenizer-independent bound DocIQ asserts is tokens <= UTF-8 "
    "bytes: a byte-level vocabulary always contains single-byte fallbacks, so "
    "no text needs more tokens than it has bytes. There is no corresponding "
    "lower bound — DocIQ's pre-token count is NOT one (see ASSUMPTION A1)."
)


@dataclass(frozen=True, slots=True)
class TextProfile:
    """Mechanical measurements of a body of text. No estimation here."""

    chars: int
    utf8_bytes: int
    pretokens: int
    unmatched_chars: int
    """Characters no pre-token rule consumed. Counted as one pre-token each so
    the structural characterization stays complete; a non-zero value means the
    regex above has a gap and is worth investigating."""

    digit_chars: int
    punct_chars: int
    whitespace_chars: int

    @property
    def token_ceiling(self) -> int:
        """Hard upper bound, sound for any byte-level tokenizer: the vocabulary
        can always fall back to single bytes. This is the only tokenizer-
        independent bound in the module — see :data:`SOUND_BOUND`."""
        return max(self.utf8_bytes, 1)

    @property
    def assumed_token_low(self) -> int:
        """Fewest tokens the measured structure allows **under**
        :data:`ASSUMPTIONS`. Not a bound: a tokenizer whose pre-tokenization is
        coarser than assumption A1 allows would go below it."""
        if not self.pretokens:
            return 0
        return max(1, round(self.pretokens * TOKENS_PER_PRETOKEN_LOW_X100 / 100))

    @property
    def assumed_token_high(self) -> int:
        """Most tokens the measured structure allows under :data:`ASSUMPTIONS`."""
        if not self.pretokens:
            return 0
        return max(
            self.assumed_token_low,
            round(self.pretokens * TOKENS_PER_PRETOKEN_HIGH_X100 / 100),
        )

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
    label="D-03 ruled band (3.30–3.60 chars/token), uncalibrated",
    provenance=(
        "PROXY, NOT A TOKENIZER MEASUREMENT. The band is the range ruled in "
        "D-03 for table-heavy MPR text. No tokenizer was run: Claude's is "
        "unavailable offline and Principle 4 forbids fetching one. The band was "
        "checked for consistency against the real MPR corpus by measuring "
        "pre-token structure — a CHARACTERIZATION under stated assumptions, not "
        "a bound (see ASSUMPTION A1). Treat the displayed range as an estimate, "
        "not a count."
    ),
)


# Method labels. Constants rather than inline literals because the PDF, the log
# and the GUI must all render the same words for the same run — the exact defect
# B-6 recorded against the summary footer, which named a method the run had not
# used.
METHOD_BAND = (
    "characters ÷ the D-03 ruled chars-per-token band; the measured pre-token "
    "structure is consistent with that band"
)
METHOD_WIDENED = (
    "the D-03 ruled band WIDENED to its union with the measured pre-token "
    "structure, which lies entirely above the band"
)
METHOD_EMPTY = "no text measured"


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """A conservative token range for one body of text (D-03, §7)."""

    profile: TextProfile
    basis: CalibrationBasis
    low: int
    high: int
    method: str = METHOD_EMPTY
    """The method actually used for THIS estimate, in words. Rendered verbatim
    by every consumer; never re-derived by one."""

    clamped_high: bool = False
    """True when the sound ``tokens <= utf8_bytes`` ceiling overrode the range's
    upper end."""

    widened: bool = False
    """True when the reported range is the union of the ruled band and the
    structure-implied range rather than the band alone."""

    ratio_refuted: bool = False
    """True when the ruled band lies entirely BELOW the range the measured
    pre-token structure allows under :data:`ASSUMPTIONS` — so the band alone
    would understate the load.

    This is a **conditional** inconsistency, not a proof: it depends on
    assumption A1, which is not verified and cannot be offline. Before Codex
    review #1 finding B-6 this flag was set by an unsound argument (the
    pre-token count treated as a hard floor) and was therefore set on the real
    corpus. Under the corrected assumptions it is not."""

    @property
    def headline(self) -> str:
        return f"≈ {_compact(self.low)}–{_compact(self.high)} tokens"

    @property
    def method_short(self) -> str:
        """A few words naming the method, for a single-line footer.

        Derived from :attr:`method` rather than chosen independently, so the
        short form cannot say something the long form does not."""
        if self.method == METHOD_EMPTY:
            return "no text measured"
        if self.widened:
            return "ruled band widened by measured structure"
        return "ruled band, structure-checked"

    def capacity(self, limit: int = DIRECT_CONTEXT_TOKENS) -> "CapacityVerdict":
        return CapacityVerdict.of(self, limit)

    def provenance_text(self, label: str = "") -> str:
        """The full provenance string that must travel with this figure.

        Built here, once, so the processing log, the summary PDF, the upload
        package README, the GUI and the contract projection cannot render
        different accounts of the same number. Finding B-6 is precisely what a
        second, hand-written account produces.
        """
        where = f" ({label})" if label else ""
        parts = [
            self.basis.provenance,
            f"METHOD FOR THIS FIGURE{where}: {self.method}.",
        ]
        if self.ratio_refuted:
            parts.append(
                "CONDITIONAL INCONSISTENCY: the ruled band "
                f"{self.basis.display} lies entirely below the "
                f"{self.profile.assumed_token_low:,}–"
                f"{self.profile.assumed_token_high:,} token range the measured "
                f"structure allows ({self.profile.pretokens:,} pre-tokens at "
                f"{self.profile.chars_per_pretoken_x100 / 100:.2f} characters "
                "each), so the band alone would understate this text. This "
                "follows from ASSUMPTION A1 and is not a proof that the band is "
                "wrong."
            )
        if self.clamped_high:
            parts.append(
                "The upper end was reduced to the sound ceiling of "
                f"{self.profile.token_ceiling:,} tokens (UTF-8 bytes)."
            )
        parts.append(
            f"Measured{where}: {self.profile.chars:,} characters, "
            f"{self.profile.utf8_bytes:,} UTF-8 bytes, "
            f"{self.profile.pretokens:,} DocIQ pre-tokens; reported range "
            f"{self.low:,}–{self.high:,} tokens."
        )
        parts.append(SOUND_BOUND)
        parts.extend(ASSUMPTIONS)
        return " ".join(parts)


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

    Two ranges are computed and compared:

    * the **ratio range**, ``characters ÷ the ruled band``; and
    * the **structure range**, the measured pre-token count times the assumed
      tokens-per-pre-token band (:data:`ASSUMPTIONS`).

    When they overlap the ruled band governs — D-03 is a ruling, and a corpus
    check that agrees with it is not a reason to discard it. When the structure
    range lies entirely *above* the ruled band the reported range widens to the
    union, because in that direction the band would understate the load, which
    is the error that matters for a capacity decision.

    The reverse case — a structure range entirely *below* the band, which
    happens on artificial text with very long words — does **not** widen
    downward. The band is the conservative statement there, and widening toward
    one token for a 36,000-character string would trade a defensible estimate
    for a meaningless one.

    The only clamp applied is the sound ``tokens <= utf8_bytes`` ceiling. The
    pre-token count is deliberately NOT used as a floor: see the module
    docstring and Codex review #1 finding B-6.
    """
    b = basis or DEFAULT_BASIS
    profile = (
        text_or_profile
        if isinstance(text_or_profile, TextProfile)
        else measure(text_or_profile)
    )
    if profile.chars == 0:
        return TokenEstimate(
            profile=profile, basis=b, low=0, high=0, method=METHOD_EMPTY
        )

    band_low = math.floor(profile.chars * 100 / b.high_x100)
    band_high = math.ceil(profile.chars * 100 / b.low_x100)
    s_low, s_high = profile.assumed_token_low, profile.assumed_token_high

    structure_above = bool(profile.pretokens) and s_low > band_high
    if structure_above:
        low, high = min(band_low, s_low), max(band_high, s_high)
        method = METHOD_WIDENED
    else:
        low, high = band_low, band_high
        method = METHOD_BAND

    clamped_high = False
    ceiling = profile.token_ceiling
    if high > ceiling:
        high, clamped_high = ceiling, True
    low = max(1, min(low, high))
    return TokenEstimate(
        profile=profile,
        basis=b,
        low=low,
        high=high,
        method=method,
        clamped_high=clamped_high,
        widened=structure_above,
        ratio_refuted=structure_above,
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

    ``consistent`` is a statement about two ranges overlapping under
    :data:`ASSUMPTIONS`. It is not evidence about any real tokenizer, and a
    ``consistent=False`` result is not a refutation of D-03.
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
            "were counted individually; the pre-token characterization remains "
            "complete but the regex has a gap worth closing"
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
            + " Widened after a corpus pre-token check disagreed with the ruled "
            "band under ASSUMPTION A1.",
            measured_chars=total.chars,
            measured_pretokens=total.pretokens,
            measured_documents=len(texts),
        )
    notes.append(
        "This check compares two computed ranges under stated assumptions. It "
        "is not a tokenizer measurement and cannot refute D-03 on its own."
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
