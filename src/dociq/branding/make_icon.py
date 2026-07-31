"""LI Document IQ — generate the Explorer/taskbar icon from the brand art.

D-08 (Alex, 2026-07-30): concept 2 "fanned corpus stack" — the LI monogram
tile (white monogram + light-blue globe on navy) with a fanned three-page stack
overlapping the lower right, the front page carrying a light-blue "IQ" tab.

WHY THIS IS A SCRIPT AND NOT A HAND-DRAWN FILE
The tile is DERIVED from ``assets/branding/li_monogram_source.png`` — the LI
bars are recoloured and the globe is carried through from the real brand art,
never redrawn. A brand refresh is a re-run of this script, and the provenance of
every shipped pixel is this file. Adapted from the LI PDF Cleaner's
``make_brand_icon.py`` so the two products' icons stay one family.

TWO LEVELS OF DETAIL, ON PURPOSE
A .ico carries independent artwork per size, and the thing that kills app icons
is the 16 px taskbar entry.

  >= 64 px  FULL  — monogram + three fanned pages + the "IQ" tab
  <  64 px  SIMPLE — monogram + one plain page silhouette, no fan, no lettering

At 32 px the three fan edges land as one grey smear and "IQ" as two smudges, so
below 64 px the overlay says less on purpose. Both levels share the silhouette
and the colour blocking, so it reads as one icon at every size. The threshold is
judged on the ladder ``--preview`` writes, not asserted.

Usage (from the repo root, with src/ on PYTHONPATH):
    python -m dociq.branding.make_icon
    python -m dociq.branding.make_icon --preview
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys
from pathlib import Path

from dociq.branding.palette import BRAND_DIR, MONOGRAM_SOURCE, Palette, sample_palette

ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
LOCKUP_TILE_PX = 512
"""Extra high-res render, for the D-09 window-header lockup. Same artwork."""

SIMPLE_BELOW = 64
SS = 4
"""Supersample factor. Everything is drawn at ``size * SS`` and resampled down
once, so the fan's diagonal edges land as antialiased ink rather than stairs."""

GLOBE_CX, GLOBE_CY, GLOBE_R = 834, 774, 435
"""The light-blue globe disc inside the source art, measured from it. Shared
with the PDF Cleaner generator — the two products crop the same brand file."""

WHITE = (255, 255, 255)
PAPER = (252, 253, 254)
PAPER_BACK = (232, 238, 244)
"""The two pages behind the front sheet, tinted so the fan reads as depth
rather than as one thick outline."""

FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def font_path() -> str | None:
    """The first available bold sans. Returned rather than hidden so callers can
    LOG which face a render used — a different face is a different output, and a
    silent substitution would break the byte-identical claim invisibly."""
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _font(px: int):
    from PIL import ImageFont

    path = font_path()
    if path is None:
        return None
    return ImageFont.truetype(path, px)


# ------------------------------------------------------------------ brand art
def _white_mark(src, width: int, navy: tuple[int, int, int]):
    """The LI monogram with its navy bars recoloured to white.

    Tolerance is generous because the art is antialiased: the bar edges are navy
    blended toward transparent, and leaving them navy would ring the white
    monogram in a dark halo against the navy tile.
    """
    import numpy as np
    from PIL import Image

    im = src.crop(src.getchannel("A").getbbox())
    w, h = im.size
    im = im.resize((width, max(1, round(h * width / w))), _lanczos())

    # Vectorised: the per-pixel Python loop this replaces took ~9 s per full
    # generator run at 512 px, which made a 30-run determinism proof a
    # five-minute wait. Same predicate, same result — asserted byte-for-byte in
    # tests/test_branding.py.
    arr = np.asarray(im, dtype=np.int16).copy()
    nr, ng, nb = navy
    hit = (
        (arr[:, :, 3] > 8)
        & (np.abs(arr[:, :, 0] - nr) < 46)
        & (np.abs(arr[:, :, 1] - ng) < 50)
        & (np.abs(arr[:, :, 2] - nb) < 50)
    )
    arr[hit, 0:3] = 255
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def _lanczos():
    from PIL import Image

    return Image.LANCZOS


# ---------------------------------------------------------------- the artwork
def _sheet(w: int, h: int, fill, outline, radius_frac: float, edge: int):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle(
        (0, 0, w - 1, h - 1),
        radius=max(2, int(w * radius_frac)),
        fill=fill,
        outline=outline,
        width=edge,
    )
    return im


def _moat(stack, navy: tuple[int, int, int], grow: int):
    """Set the stack on a navy silhouette of itself, ``grow`` px larger.

    Without this the white sheets sit directly against the white monogram bars
    and the two shapes fuse — badly at 256 px, fatally at 16 px, where the icon
    became a navy square with an indistinct pale smear. The moat is the tile's
    own colour, so it reads as the page floating above the mark rather than as
    an added outline.
    """
    import numpy as np
    from PIL import Image

    if grow < 1:
        return stack
    alpha = dilate_alpha(np.asarray(stack.getchannel("A"), dtype=np.uint8), grow)
    base = Image.new("RGBA", stack.size, navy + (0,))
    base.putalpha(Image.fromarray(alpha, "L"))
    base.alpha_composite(stack)
    return base


def dilate_alpha(alpha, grow: int):
    """Square dilation by ``grow``, as ``ImageFilter.MaxFilter(2*grow+1)`` does.

    Built from shifts of doubling length instead of the filter: at the 512 px
    rung the kernel is 103 px wide and the naive filter dominated the whole
    generator (~40 s a run, which put a 30-run determinism proof out of reach).
    Dilation composes additively, so shifts of 1, 2, 4 … summing to ``grow``
    give exactly the same structuring element — asserted against the filter in
    ``tests/test_branding.py``.

    Edges are treated as empty rather than replicated. The art never reaches the
    canvas edge (the stack canvas is padded), so the two conventions cannot
    differ on real input.
    """
    import numpy as np

    for axis in (0, 1):
        remaining, step = grow, 1
        while remaining > 0:
            d = min(step, remaining)
            alpha = np.maximum(
                alpha,
                np.maximum(_pad_edge(alpha, d, axis), _pad_edge(alpha, -d, axis)),
            )
            remaining -= d
            step *= 2
    return alpha


def _pad_edge(arr, shift: int, axis: int):
    """``arr`` shifted by ``shift`` along ``axis``, vacated edge filled with 0.

    Zero rather than wrapped: the stack's alpha must not bleed from one side of
    the canvas to the other, which ``np.roll`` alone would do.
    """
    import numpy as np

    out = np.zeros_like(arr)
    if shift == 0:
        return arr
    src = [slice(None)] * arr.ndim
    dst = [slice(None)] * arr.ndim
    if shift > 0:
        src[axis] = slice(0, arr.shape[axis] - shift)
        dst[axis] = slice(shift, arr.shape[axis])
    else:
        src[axis] = slice(-shift, arr.shape[axis])
        dst[axis] = slice(0, arr.shape[axis] + shift)
    out[tuple(dst)] = arr[tuple(src)]
    return out


def _stack(w: int, h: int, simple: bool, pal: Palette):
    """The corpus stack: one silhouette when simple, three fanned sheets when
    not. Returns an RGBA image larger than ``w x h`` — the rotated back sheets
    need room — with the front sheet's top-left at a known offset."""
    from PIL import Image, ImageDraw

    navy = pal.navy_rgb
    accent = pal.accent_rgb
    edge = max(2, int(w * 0.055))
    pad = int(w * 0.30)
    canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))

    if simple:
        front = _sheet(w, h, PAPER, navy, 0.08, edge)
        # One accent band where the "IQ" tab sits on the full artwork: at 24 px
        # this block of colour is the only thing that survives resampling, and
        # it is what makes the small icon and the large icon the same icon.
        ImageDraw.Draw(front).rectangle(
            (int(w * 0.17), int(h * 0.58), int(w * 0.83), int(h * 0.80)),
            fill=accent,
        )
        canvas.alpha_composite(front, (pad, pad))
        return _moat(canvas, navy, max(1, int(w * 0.09))), pad

    for angle, dx, dy, fill in ((10.0, -0.20, -0.10, PAPER_BACK),
                                (5.0, -0.10, -0.05, PAPER_BACK)):
        back = _sheet(w, h, fill, navy, 0.08, edge)
        rot = back.rotate(angle, resample=Image.BICUBIC, expand=True)
        canvas.alpha_composite(
            rot,
            (pad + int(w * dx) - (rot.width - w) // 2,
             pad + int(h * dy) - (rot.height - h) // 2),
        )

    front = _sheet(w, h, PAPER, navy, 0.08, edge)
    d = ImageDraw.Draw(front)
    for i in range(2):  # a hint of ruled content above the tab
        y = int(h * (0.20 + 0.15 * i))
        d.rectangle(
            (int(w * 0.16), y, int(w * (0.72 - 0.14 * i)), y + max(2, int(h * 0.05))),
            fill=pal.hairline_strong,
        )
    tw, th = int(w * 0.70), int(h * 0.26)
    tx, ty = int(w * 0.15), int(h * 0.60)
    d.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=int(th * 0.28), fill=accent)
    fnt = _font(int(th * 0.72))
    if fnt is not None:
        d.text((tx + tw / 2, ty + th / 2 - th * 0.06), "IQ", font=fnt,
               fill=WHITE, anchor="mm")
    canvas.alpha_composite(front, (pad, pad))
    return _moat(canvas, navy, max(1, int(w * 0.06))), pad


def render(size: int, simple: bool | None = None, pal: Palette | None = None):
    """The D-08 icon at ``size`` px, RGBA."""
    from PIL import Image, ImageDraw

    pal = pal or sample_palette()
    if simple is None:
        simple = size < SIMPLE_BELOW
    with Image.open(MONOGRAM_SOURCE) as raw:
        src = raw.convert("RGBA")

    d_ = size * SS
    navy = pal.navy_rgb
    tile = Image.new("RGBA", (d_, d_), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, d_ - 1, d_ - 1), radius=int(d_ * 0.20), fill=navy + (255,)
    )

    # Simple artwork carries a smaller mark and a larger page: at 16–48 px the
    # monogram's inner counters close up anyway, so the mark is doing less work
    # than the page silhouette and should occupy less of the tile.
    mark_w = int(d_ * (0.66 if simple else 0.78))
    mark = _white_mark(src, mark_w, navy)
    mark_x = int(d_ * 0.10) if simple else (d_ - mark.size[0]) // 2
    tile.alpha_composite(mark, (mark_x, int(d_ * (0.15 if simple else 0.17))))

    # Origins are chosen so the artwork plus its moat stops short of the tile's
    # rounded edge; run it to 0.98 and the corner mask shaves the page's own
    # outline off, which reads as a rendering fault rather than as a crop.
    sw = int(d_ * (0.48 if simple else 0.42))
    stack, pad = _stack(sw, int(sw * (1.18 if simple else 1.22)), simple, pal)
    origin = (0.44, 0.39) if simple else (0.48, 0.42)
    tile.alpha_composite(stack, (int(d_ * origin[0]) - pad,
                                 int(d_ * origin[1]) - pad))

    mask = Image.new("L", (d_, d_), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, d_ - 1, d_ - 1), radius=int(d_ * 0.20), fill=255
    )
    out = Image.new("RGBA", (d_, d_), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out.resize((size, size), _lanczos())


# ----------------------------------------------------------------- .ico I/O
def _dib_frame(im) -> bytes:
    """One icon frame as a BMP/DIB: BITMAPINFOHEADER + bottom-up BGRA + the
    1bpp AND mask.

    Explorer only reads PNG-compressed frames at 256 px; below that it wants a
    DIB, and given a PNG it silently falls back to the default application icon.
    PIL writes every frame as PNG, which is why this is hand-rolled — the LI PDF
    Cleaner shipped a broken icon twice before this was understood.
    """
    w, h = im.size
    px = im.load()
    xor = bytearray()
    for y in range(h - 1, -1, -1):  # bottom-up
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    row = ((w + 31) // 32) * 4  # AND mask: 1bpp, 4-byte aligned rows
    and_mask = bytearray()
    for y in range(h - 1, -1, -1):
        bits = bytearray(row)
        for x in range(w):
            if px[x, y][3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        and_mask += bits
    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                      len(xor) + len(and_mask), 0, 0, 0, 0)
    return bytes(hdr + xor + and_mask)


def write_ico(layers: dict[int, object], sizes, path: Path) -> None:
    """Write a multi-size .ico Windows will actually display."""
    frames: list[tuple[int, bytes]] = []
    for s in sorted(sizes):
        im = layers[s].convert("RGBA")  # type: ignore[union-attr]
        if s >= 256:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            frames.append((s, buf.getvalue()))
        else:
            frames.append((s, _dib_frame(im)))
    out = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    for s, blob in frames:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                           len(blob), offset)
        offset += len(blob)
    for _s, blob in frames:
        out += blob
    path.write_bytes(bytes(out))


def ico_report(path: Path) -> list[tuple[int, str, int, int]]:
    """(size, kind, planes, bpp) per frame, read back FROM THE FILE.

    Read back rather than reported from the write path: the claim "all seven
    sizes are present" is only worth making against the bytes that shipped.
    """
    d = path.read_bytes()
    _r, _t, n = struct.unpack("<HHH", d[:6])
    got: list[tuple[int, str, int, int]] = []
    off = 6
    for _i in range(n):
        w, _h, _c, _r1, planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", d[off:off + 16]
        )
        off += 16
        blob = d[offset:offset + size]
        kind = "PNG" if blob[:8] == b"\x89PNG\r\n\x1a\n" else "DIB"
        got.append((w or 256, kind, planes, bpp))
    return got


# ------------------------------------------------------------------- outputs
def ladder(layers: dict[int, object], pal: Palette, path: Path) -> Path:
    """A size-ladder contact sheet: every shipped size, on light and on dark.

    Each rung gets its own cell of a fixed pitch — the Cleaner's first version
    reused x positions and drew the small sizes on top of each other.
    """
    from PIL import Image, ImageDraw

    rungs = (128, 64, 48, 32, 24, 16)
    cell, gap, left = 130, 14, 300
    W = left + len(rungs) * (cell + gap) + 24
    H = 500
    sh = Image.new("RGB", (W, H), (245, 246, 248))
    d = ImageDraw.Draw(sh)
    d.rectangle((0, 0, W, 64), fill=pal.navy_rgb)
    f, small = _font(25), _font(13)
    if f:
        d.text((24, 19), "LI Document IQ icon — D-08 fanned corpus stack",
               font=f, fill=WHITE)

    sh.paste(layers[256], (24, 100), layers[256])  # type: ignore[arg-type]
    if small:
        d.text((24, 372), "256 px", font=small, fill=(90, 100, 112))
        for i, line in enumerate((
            "Every size the .ico ships, at 1:1 on a light desktop",
            f"and on a dark taskbar. Under {SIMPLE_BELOW} px the artwork",
            "simplifies: same silhouette and colour blocking, one",
            "page instead of three, no lettering.",
        )):
            d.text((24, 402 + i * 20), line, font=small, fill=(110, 120, 132))

    for row, bg, fg, tag in ((100, (230, 234, 240), (90, 100, 112), "light"),
                             (272, (42, 46, 54), (150, 158, 170), "dark")):
        for i, s in enumerate(rungs):
            x = left + i * (cell + gap)
            d.rounded_rectangle((x, row, x + cell, row + cell), radius=9, fill=bg)
            sh.paste(layers[s], (x + (cell - s) // 2, row + (cell - s) // 2),
                     layers[s])  # type: ignore[arg-type]
            if small:
                d.text((x + cell // 2, row + cell + 6), f"{s} px", font=small,
                       fill=fg, anchor="ma")
                if s < SIMPLE_BELOW:
                    d.text((x + cell // 2, row + cell + 24), "simple",
                           font=small, fill=fg, anchor="ma")
        if small:
            d.text((left, row - 18), tag + " background", font=small,
                   fill=(120, 130, 142))
    path.parent.mkdir(parents=True, exist_ok=True)
    sh.save(path)
    return path


def write(preview: bool = False, out_dir: Path | None = None,
          ladder_path: Path | None = None) -> int:
    """Write the shipped icon set. Returns a process exit code."""
    out_dir = out_dir or BRAND_DIR
    if not MONOGRAM_SOURCE.is_file():
        print(f"[icon] FAIL: brand source missing: {MONOGRAM_SOURCE}")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    pal = sample_palette()
    print(f"[icon] palette sampled from {MONOGRAM_SOURCE.name} "
          f"({pal.source_sha256[:12]}…): navy={pal.navy} accent={pal.accent}")
    print(f"[icon] type face: {font_path() or 'NONE — tab lettering suppressed'}")

    sizes = sorted(set(ICO_SIZES) | {LOCKUP_TILE_PX})
    layers = {s: render(s, pal=pal) for s in sizes}

    ico = out_dir / "li_dociq_icon.ico"
    write_ico(layers, ICO_SIZES, ico)
    layers[256].save(out_dir / "li_dociq_icon.png")  # type: ignore[union-attr]
    layers[LOCKUP_TILE_PX].save(out_dir / "li_dociq_tile_512.png")  # type: ignore[union-attr]
    for name in ("li_dociq_icon.ico", "li_dociq_icon.png", "li_dociq_tile_512.png"):
        print(f"[icon] wrote {name} ({(out_dir / name).stat().st_size} bytes)")

    frames = ico_report(ico)
    got = sorted(f[0] for f in frames)
    if got != sorted(ICO_SIZES):
        print(f"[icon] FAIL: .ico carries {got}, expected {sorted(ICO_SIZES)}")
        return 1
    bad = [f for f in frames
           if f[1] != ("PNG" if f[0] >= 256 else "DIB") or f[2] != 1 or f[3] != 32]
    if bad:
        print(f"[icon] FAIL: frames Windows will not render: {bad}")
        return 1
    print("[icon] .ico frames verified: "
          + ", ".join(f"{s}={k}" for s, k, _p, _b in frames))

    if preview:
        p = ladder(layers, pal, ladder_path or (out_dir / "icon_preview.png"))
        print(f"[icon] preview: {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_icon")
    ap.add_argument("--preview", action="store_true",
                    help="also write the size-ladder contact sheet")
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory (default: assets/branding)")
    ap.add_argument("--ladder", type=Path, default=None,
                    help="path for the ladder PNG")
    args = ap.parse_args(argv)
    return write(preview=args.preview, out_dir=args.out, ladder_path=args.ladder)


if __name__ == "__main__":
    sys.exit(main())
