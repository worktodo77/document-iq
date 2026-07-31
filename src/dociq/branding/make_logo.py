"""LI Document IQ — generate the D-09 window-header lockup.

D-09 (Alex, 2026-07-30): the L1 "app badge lockup" — the D-08 icon tile at
left, beside "Document IQ" (navy, with "IQ" in light blue) set over a
letterspaced LONG INTERNATIONAL caption. One mark shared across Explorer, the
taskbar and the window header.

Composed deterministically from ``li_monogram_source.png`` plus a typeset name;
nothing hand-drawn, so a brand refresh is a re-run of this script and the icon
in Explorer and the badge in the header can never drift apart — they are the
same render at two sizes.

The lockup is written at ``SCALE``× its design size and scaled down by the
window at paint time, so it stays sharp on a 150%-DPI laptop, which is what LI
staff actually run.

Usage (from the repo root, with src/ on PYTHONPATH):
    python -m dociq.branding.make_logo
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dociq.branding.make_icon import render as render_tile
from dociq.branding.palette import BRAND_DIR, Palette, sample_palette

DESIGN_TILE_PX = 44
"""The tile's height in logical pixels in the window header. Everything else in
the lockup is a ratio of this, so the badge scales as one object."""

SCALE = 4
"""Render multiplier. 4× covers 100/125/150/200% Windows scaling without a
second asset."""

NAME = "Document IQ"
ACCENT_TAIL = "IQ"
"""The part of the name set in light blue. Split by suffix rather than by index
so renaming the product does not silently recolour the wrong letters."""

CAPTION = "LONG INTERNATIONAL"
CAPTION_TRACKING = 0.20
"""Letterspacing as a fraction of the caption's type size. The caption is a
firm signature, not running text — it is meant to be read as spaced capitals."""

NAME_FONTS = (
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
CAPTION_FONTS = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _pick(candidates: tuple[str, ...]) -> str | None:
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _font(candidates: tuple[str, ...], px: int):
    from PIL import ImageFont

    path = _pick(candidates)
    if path is None:
        raise RuntimeError(
            "no usable type face found for the lockup; tried: "
            + ", ".join(candidates)
        )
    return ImageFont.truetype(path, px)


def _text_width(draw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _draw_tracked(draw, xy, text: str, font, fill, tracking_px: float) -> float:
    """Draw ``text`` one glyph at a time with fixed tracking; return its width.

    Done by hand because Pillow has no letterspacing: the alternative is a
    pre-spaced string, which puts real space characters into the art and
    tracks punctuation differently from letters.
    """
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=font) + tracking_px
    return x - xy[0] - (tracking_px if text else 0.0)


def render_lockup(pal: Palette | None = None, scale: int = SCALE):
    """The D-09 lockup as an RGBA image on a transparent ground."""
    from PIL import Image, ImageDraw

    pal = pal or sample_palette()
    tile_px = DESIGN_TILE_PX * scale
    tile = render_tile(tile_px, simple=False, pal=pal)

    name_font = _font(NAME_FONTS, int(tile_px * 0.46))
    cap_font = _font(CAPTION_FONTS, int(tile_px * 0.175))
    tracking = cap_font.size * CAPTION_TRACKING

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    head, tail = NAME[: -len(ACCENT_TAIL)], ACCENT_TAIL
    name_w = _text_width(probe, head, name_font) + _text_width(probe, tail, name_font)
    cap_w = sum(probe.textlength(c, font=cap_font) for c in CAPTION) + tracking * (
        len(CAPTION) - 1
    )

    gap = int(tile_px * 0.30)
    pad = int(tile_px * 0.06)
    width = pad + tile_px + gap + int(max(name_w, cap_w)) + pad
    height = pad * 2 + tile_px

    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    im.alpha_composite(tile, (pad, pad))
    d = ImageDraw.Draw(im)

    text_x = pad + tile_px + gap
    # Optical centring: the name's cap-height block and the caption are treated
    # as one text column vertically centred on the tile, rather than each being
    # centred on its own, which would leave the caption looking dropped.
    name_baseline = pad + int(tile_px * 0.56)
    cap_baseline = pad + int(tile_px * 0.90)

    d.text((text_x, name_baseline), head, font=name_font, fill=pal.navy, anchor="ls")
    d.text((text_x + _text_width(probe, head, name_font), name_baseline), tail,
           font=name_font, fill=pal.accent, anchor="ls")
    _draw_tracked(d, (text_x, cap_baseline), CAPTION, cap_font, pal.ink_muted,
                  tracking)
    return im


def write(out_dir: Path | None = None) -> int:
    out_dir = out_dir or BRAND_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    pal = sample_palette()
    print(f"[logo] palette sampled from li_monogram_source.png "
          f"({pal.source_sha256[:12]}…): navy={pal.navy} accent={pal.accent}")
    print(f"[logo] type faces: name={_pick(NAME_FONTS)} caption={_pick(CAPTION_FONTS)}")
    im = render_lockup(pal)
    path = out_dir / "li_dociq_lockup.png"
    im.save(path)
    print(f"[logo] wrote {path.name} ({im.size[0]}×{im.size[1]}, "
          f"{path.stat().st_size} bytes, {SCALE}× of a "
          f"{DESIGN_TILE_PX} px tile)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_logo")
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory (default: assets/branding)")
    args = ap.parse_args(argv)
    return write(out_dir=args.out)


if __name__ == "__main__":
    sys.exit(main())
