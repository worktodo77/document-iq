"""Brand colors, SAMPLED from the brand art — never transcribed from prose.

D-07 names the colors as families (``#0E4D80`` navy, ``#2E9FD4`` light blue)
and the branding README is explicit that the exact values must come from
``li_monogram_source.png`` at build time. A hex literal copied out of a document
is a second source of truth that drifts the first time the art is refreshed, so
this module reads the art instead.

The sampling rule is deliberately blunt and therefore stable: over the fully
opaque pixels of the art, the two most frequent chromatic colors ARE the brand
pair — the monogram bars and the globe disc together cover ~82% of the opaque
area, and nothing else in the file comes close. Ties break on the RGB triple so
the result cannot depend on dict or set iteration order.

Every other color the interface uses is derived from that pair by a fixed
blend, so a brand refresh moves the whole system at once.
"""

from __future__ import annotations

import functools
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

def _brand_dir() -> Path:
    """Where the shipped brand art lives, source tree or frozen build.

    From the source tree it is ``<repo>/assets/branding``, four parents up.
    That arithmetic is wrong in a PyInstaller build, where this file sits at
    ``<bundle>/dociq/branding/palette.py`` and the same four parents land
    outside the bundle entirely — so the icon, the header lockup and the
    monogram the palette is SAMPLED from all resolve to nothing, and the
    packaged app either ships an unbranded window or fails at import when the
    palette cannot find its source art.

    ``sys._MEIPASS`` is PyInstaller's own answer to "where did my data go", and
    it is the only reliable one: the bundle directory is not derivable from
    ``__file__`` because the launcher may run from anywhere. Checked first, so
    the frozen path never depends on the source-tree arithmetic being right.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "branding"
    return Path(__file__).resolve().parents[3] / "assets" / "branding"


BRAND_DIR = _brand_dir()
MONOGRAM_SOURCE = BRAND_DIR / "li_monogram_source.png"
WORDMARK_SOURCE = BRAND_DIR / "li_logo.png"

_NEAR_WHITE = 236
"""A pixel whose darkest channel is at or above this is paper, not brand ink."""

_NEAR_BLACK = 24
"""A pixel whose brightest channel is at or below this is a shadow, not ink."""

_MIN_PAIR_DISTANCE = 60
"""Squared-free Euclidean RGB distance the accent must clear from the structure
color. Anti-aliased fringes of the navy bars are frequent enough to place in
the top few counts; they sit well inside this radius and the globe sits well
outside it, so the rule separates a fringe from a second brand color without
tuning against a specific image."""


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def _blend(fg: tuple[int, int, int], bg: tuple[int, int, int], t: float) -> str:
    """``fg`` over ``bg`` at opacity ``t``, rounded half-up per channel."""
    return _hex(tuple(int(f * t + b * (1.0 - t) + 0.5) for f, b in zip(fg, bg)))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Palette:
    """The interface's whole color system, derived from two sampled values."""

    navy: str
    """Structure: the monogram tile, headings, rules that carry weight."""

    accent: str
    """Emphasis: gauge fill, the "IQ" in the lockup, selected state."""

    source_sha256: str
    """Hash of the art the pair was sampled from — so a render can be traced to
    the exact brand file, and a refresh is visible in the log."""

    # -- derived, so a brand refresh moves the entire system ----------------

    ground: str = "#FFFFFF"
    """The white ground D-07 asks for. Not derived: it is the paper."""

    @property
    def navy_rgb(self) -> tuple[int, int, int]:
        return _rgb(self.navy)

    @property
    def accent_rgb(self) -> tuple[int, int, int]:
        return _rgb(self.accent)

    @property
    def ink(self) -> str:
        """Body text: navy pushed almost to black, so text reads as text and
        the navy stays reserved for structure."""
        return _blend(self.navy_rgb, (0, 0, 0), 0.42)

    @property
    def ink_muted(self) -> str:
        """Secondary text — captions, units, column labels.

        Derived from :attr:`ink` rather than from navy: a 50% navy on white is
        a pleasant rule color and an unreadable type color (~2.5:1 against
        the ground). This lands near 4.6:1, which small caption type needs."""
        return _blend(_rgb(self.ink), (255, 255, 255), 0.62)

    @property
    def hairline(self) -> str:
        """The editorial grid. One weight, one color, everywhere."""
        return _blend(self.navy_rgb, (255, 255, 255), 0.16)

    @property
    def hairline_strong(self) -> str:
        """Rules that close a block rather than divide one."""
        return _blend(self.navy_rgb, (255, 255, 255), 0.34)

    @property
    def tint(self) -> str:
        """Barely-there fill for chips and inset panels."""
        return _blend(self.accent_rgb, (255, 255, 255), 0.10)

    @property
    def tint_strong(self) -> str:
        return _blend(self.accent_rgb, (255, 255, 255), 0.22)

    @property
    def gauge_track(self) -> str:
        return _blend(self.navy_rgb, (255, 255, 255), 0.10)

    @property
    def warn(self) -> str:
        """Attention, not alarm: DocIQ flags for review, it never fails a page
        silently and it never scolds. Amber, fixed — it is a status color, not
        a brand color, and must not shift when the brand art is refreshed."""
        return "#B26A00"

    @property
    def warn_tint(self) -> str:
        return _blend(_rgb(self.warn), (255, 255, 255), 0.12)

    def as_dict(self) -> dict[str, str]:
        """Flat name → hex map, for the shipped palette record and for tests."""
        return {
            "navy": self.navy,
            "accent": self.accent,
            "ground": self.ground,
            "ink": self.ink,
            "ink_muted": self.ink_muted,
            "hairline": self.hairline,
            "hairline_strong": self.hairline_strong,
            "tint": self.tint,
            "tint_strong": self.tint_strong,
            "gauge_track": self.gauge_track,
            "warn": self.warn,
            "warn_tint": self.warn_tint,
            "source_sha256": self.source_sha256,
        }


def _rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _sample(path: Path) -> Palette:
    import numpy as np
    from PIL import Image

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGBA"), dtype=np.uint8)

    flat = arr.reshape(-1, 4)
    opaque = flat[flat[:, 3] == 255][:, :3].astype(np.int32)
    keep = (opaque.min(axis=1) < _NEAR_WHITE) & (opaque.max(axis=1) > _NEAR_BLACK)
    ink = opaque[keep]
    if ink.size == 0:
        raise ValueError(f"{path}: no brand ink found — is this the right art?")

    packed = (ink[:, 0] << 16) | (ink[:, 1] << 8) | ink[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    # Descending count, ascending packed RGB on a tie: a total order, so the
    # result cannot depend on hash seeding or numpy's internal ordering.
    order = np.lexsort((values, -counts))

    ranked = [
        ((int(v) >> 16) & 0xFF, (int(v) >> 8) & 0xFF, int(v) & 0xFF)
        for v in values[order]
    ]
    structure = ranked[0]
    accent = None
    for cand in ranked[1:]:
        d = sum((a - b) ** 2 for a, b in zip(cand, structure)) ** 0.5
        if d >= _MIN_PAIR_DISTANCE:
            accent = cand
            break
    if accent is None:
        raise ValueError(
            f"{path}: only one brand color found — the art is not the LI monogram"
        )
    # The lighter of the two is the accent by construction of the family: the
    # globe disc is light blue on a navy field, never the reverse.
    if sum(structure) > sum(accent):
        structure, accent = accent, structure
    return Palette(navy=_hex(structure), accent=_hex(accent), source_sha256=digest)


@functools.lru_cache(maxsize=4)
def sample_palette(path: Path | str = MONOGRAM_SOURCE) -> Palette:
    """The brand palette, sampled from ``path``.

    Cached because the GUI asks for it per widget and the art is 2.8M pixels;
    the cache is keyed on the path, so a test can sample a different file.
    """
    return _sample(Path(path))
