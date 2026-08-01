"""The latent hazard: the shared OCR pool returns pages out of order.

The contract's gapless-1..N check catches a *missing* page but not a
*permuted* one — a permutation keeps the page numbers perfect and moves the
text. So the defence has to be the assembly itself, and this is the probe that
would see it fail.

The probe deliberately makes completion order the *reverse* of page order and
then randomizes it, because an assembly bug that happens to agree with
completion order on a fast machine is exactly the bug that survives to a
client matter.
"""

from __future__ import annotations

import random
import time

import pytest

from dociq.contracts import PageKind
from dociq.ingest import extract as ex

REPEATS = 30
"""Ordering-sensitive, so the long count. One green run of a race proves
nothing about the race."""


@pytest.fixture
def fake_ocr(monkeypatch):
    """Replace the engine with a deterministic-content, chaotic-timing stub.

    Each page's array is tagged with its own index by ``_page_array``, so the
    text a page ends up with is a direct statement about which array it was
    assembled from.
    """
    def fake_page_array(page):
        return f"PAGE-{page.number}"

    def fake_ocr_array(arr, _delays={}):
        idx = int(str(arr).split("-")[1])
        # Later pages finish FIRST: the opposite of completion-equals-order.
        time.sleep(max(0.0, (8 - idx) * 0.004) + random.random() * 0.004)
        return f"TEXT FOR PAGE {idx} " * 4, [0.9, 0.95]

    monkeypatch.setattr(ex, "_page_array", fake_page_array)
    monkeypatch.setattr(ex, "_ocr_array", fake_ocr_array)


@pytest.mark.parametrize("run", range(REPEATS))
def test_ocr_pages_are_assembled_by_index_not_completion(fake_ocr, run, tmp_path):
    from tests.conftest import FIXTURES

    raw = (FIXTURES / "02_scanned_instruction.pdf").read_bytes()
    got = ex._ocr_pdf_pages(raw, [0, 1])
    for i in (0, 1):
        assert got[i].text.startswith(f"TEXT FOR PAGE {i} ")


@pytest.mark.parametrize("run", range(REPEATS))
def test_extracted_page_text_lands_on_its_own_page(fake_ocr, run):
    from tests.conftest import FIXTURES

    path = FIXTURES / "02_scanned_instruction.pdf"
    doc = ex.extract(path.name, path.read_bytes())
    for p in doc.pages:
        assert p.kind is PageKind.OCR
        assert f"TEXT FOR PAGE {p.page_no - 1}" in p.text


def test_real_engine_is_stable_over_repeated_calls():
    """rapidocr's own determinism. If the engine is not stable, no amount of
    stable plumbing makes the corpus byte-identical — so it is measured, not
    assumed. Confirmed separately over 30 repeats on a real scanned MPR page
    in the D-01 bake-off."""
    from PIL import Image, ImageDraw
    import numpy as np

    img = Image.new("L", (900, 200), 255)
    d = ImageDraw.Draw(img)
    d.text((20, 40), "NOTICE OF DELAY 2024-07-16", fill=0)
    d.text((20, 110), "ISSUED TO THE CONTRACTOR", fill=0)
    arr = np.repeat(np.array(img)[:, :, None], 3, axis=2)

    results = {ex._ocr_array(arr)[0] for _ in range(REPEATS)}
    confs = {tuple(round(c, 6) for c in ex._ocr_array(arr)[1])
             for _ in range(3)}
    assert len(results) == 1, f"engine returned {len(results)} distinct texts"
    assert len(confs) == 1
