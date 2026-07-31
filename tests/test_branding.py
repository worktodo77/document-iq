"""The brand generators: sampled colours, .ico structure, and determinism.

The determinism claim matters because the icon ships inside the exe: a generator
whose output moved between runs would make the build unreproducible, and the
byte-identical claim in `pagemodel_freeze.md` is about the whole artefact set,
not only the text outputs.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dociq.branding.make_icon import ICO_SIZES, ico_report, render, write  # noqa: E402
from dociq.branding.make_logo import render_lockup  # noqa: E402
from dociq.branding.palette import MONOGRAM_SOURCE, sample_palette  # noqa: E402

RUNS = 8
"""Determinism is claimed over this many independent interpreter runs, each with
a different PYTHONHASHSEED. One pass proves nothing."""


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_hex_to_rgb(a), _hex_to_rgb(b))) ** 0.5


# --------------------------------------------------------------------- palette

def test_palette_is_sampled_from_the_art_not_transcribed() -> None:
    pal = sample_palette()
    digest = hashlib.sha256(MONOGRAM_SOURCE.read_bytes()).hexdigest()
    assert pal.source_sha256 == digest


def test_sampled_pair_is_in_the_ruled_brand_families() -> None:
    """D-07 names the families ``#0E4D80`` navy and ``#2E9FD4`` light blue. The
    sampled values must land in those families — if they do not, either the art
    changed or the sampler is picking the wrong two colours."""
    pal = sample_palette()
    assert _distance(pal.navy, "#0E4D80") < 40, pal.navy
    assert _distance(pal.accent, "#2E9FD4") < 40, pal.accent
    assert sum(_hex_to_rgb(pal.navy)) < sum(_hex_to_rgb(pal.accent))


def test_caption_text_has_usable_contrast_on_the_ground() -> None:
    """``ink_muted`` sets the smallest type in the product. WCAG AA for body
    text is 4.5:1; anything less and the captions under the gauge are decoration."""
    pal = sample_palette()

    def lum(hexv: str) -> float:
        def ch(c: int) -> float:
            s = c / 255.0
            return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
        r, g, b = (ch(c) for c in _hex_to_rgb(hexv))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    ratio = (lum(pal.ground) + 0.05) / (lum(pal.ink_muted) + 0.05)
    assert ratio >= 4.5, f"ink_muted contrast {ratio:.2f}:1"


def test_sampling_is_stable_across_hash_seeds() -> None:
    code = (
        "from dociq.branding.palette import sample_palette;"
        "p=sample_palette();print(p.navy,p.accent)"
    )
    seen = set()
    for seed in range(RUNS):
        env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONHASHSEED": str(seed)}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, cwd=str(ROOT))
        assert out.returncode == 0, out.stderr
        seen.add(out.stdout.strip())
    assert len(seen) == 1, seen


# ------------------------------------------------------------------------ icon

def test_ico_carries_every_declared_size(tmp_path: Path) -> None:
    assert write(preview=False, out_dir=tmp_path) == 0
    frames = ico_report(tmp_path / "li_dociq_icon.ico")
    assert sorted(f[0] for f in frames) == sorted(ICO_SIZES)
    assert len(frames) == 7


def test_small_frames_are_dib_so_explorer_renders_them(tmp_path: Path) -> None:
    """Explorer silently falls back to the default application icon when a
    sub-256 frame is PNG-compressed — the LI PDF Cleaner shipped that twice."""
    write(preview=False, out_dir=tmp_path)
    for size, kind, planes, bpp in ico_report(tmp_path / "li_dociq_icon.ico"):
        assert kind == ("PNG" if size >= 256 else "DIB"), (size, kind)
        assert (planes, bpp) == (1, 32)


def test_fast_dilation_matches_the_filter_it_replaced() -> None:
    """The moat's dilation was hand-rolled for speed (40 s → 2 s a run). The
    claim that it is the SAME operation has to be checked, not asserted in a
    comment."""
    import numpy as np
    from PIL import Image, ImageFilter

    from dociq.branding.make_icon import dilate_alpha

    art = np.zeros((97, 131), dtype=np.uint8)
    art[20:60, 30:90] = 255
    art[68:80, 45:70] = 180  # a partial alpha, well clear of the canvas edge
    for grow in (1, 3, 11):
        got = dilate_alpha(art.copy(), grow)
        want = np.asarray(
            Image.fromarray(art, "L").filter(ImageFilter.MaxFilter(2 * grow + 1))
        )
        assert np.array_equal(got, want), grow


def test_artwork_simplifies_below_the_threshold() -> None:
    """D-08's small-size rule: one page silhouette and no lettering under 64 px.
    Asserted as "the two draws differ", which is what "simplifies" means — the
    judgement of whether it simplifies *well* is the ladder's job."""
    full = render(48, simple=False).tobytes()
    simple = render(48, simple=True).tobytes()
    assert full != simple


@pytest.mark.parametrize("module", ["make_icon", "make_logo"])
def test_generator_output_is_byte_identical_across_runs(module: str,
                                                        tmp_path: Path) -> None:
    digests = set()
    for seed in range(RUNS):
        out = tmp_path / f"{module}_{seed}"
        env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONHASHSEED": str(seed)}
        proc = subprocess.run(
            [sys.executable, "-m", f"dociq.branding.{module}", "--out", str(out)],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        assert proc.returncode == 0, proc.stderr
        digests.add(tuple(
            (p.name, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(out.iterdir())
        ))
    assert len(digests) == 1, "generator output differs between runs"


def test_lockup_names_the_product_in_two_colours() -> None:
    """The D-09 rule that is easiest to lose in a refactor: "IQ" is the accent
    colour, the rest of the name is navy. Asserted on pixels, since that is
    where the requirement lives."""
    pal = sample_palette()
    im = render_lockup(scale=2).convert("RGB")
    counts = {}
    for x in range(im.width):
        for y in range(im.height):
            counts[im.getpixel((x, y))] = counts.get(im.getpixel((x, y)), 0) + 1
    assert counts.get(_hex_to_rgb(pal.navy), 0) > 200
    assert counts.get(_hex_to_rgb(pal.accent), 0) > 200
