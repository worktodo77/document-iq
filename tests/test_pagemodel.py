"""Normalization and page-record construction."""

from __future__ import annotations

import unicodedata

import pytest

from dociq.contracts import ContractViolation, PageKind, PageRecord
from dociq.ingest.pagemodel import make_page, normalize, ocr_stats, synthetic_pages

ADVERSARIAL = [
    "a\r\nb\rc\n",
    "line   \n\ttab kept\t\n",
    "\u200b\u200b\u200cx\u200d\ufeffy",
    "a\u00a0\u00a0\u00a0b",
    "\n\n\n\ntop\n\n\n\n\n\nmiddle\n\n\n\nbottom\n\n\n",
    "e\u0301\u200bcole",          # combining acute + a joiner between
    "e\u200b\u0301cole",   # joiner BETWEEN base and combining mark
    "\ufeff\r\n\u00a0\u200b\r",
    "",
    "   ",
    "café",                        # already NFC
    unicodedata.normalize("NFD", "café"),
    "mixed\r\n\r\n\r\n\r\nblanks\u00a0 \t\r",
]


@pytest.mark.parametrize("raw", ADVERSARIAL)
def test_normalize_is_idempotent(raw):
    once = normalize(raw)
    assert normalize(once) == once


@pytest.mark.parametrize("raw", ADVERSARIAL)
def test_normalize_output_obeys_every_rule(raw):
    s = normalize(raw)
    assert "\r" not in s
    assert "\u00a0" not in s
    assert not any(c in s for c in "\u200b\u200c\u200d\ufeff")
    assert "\n\n\n" not in s
    assert s == unicodedata.normalize("NFC", s)
    assert all(line == line.rstrip() for line in s.split("\n"))
    assert s == s.strip("\n")


def test_normalize_preserves_interior_tabs():
    assert normalize("a\tb\tc") == "a\tb\tc"


def test_normalize_collapses_to_exactly_two_blank_lines():
    assert normalize("a\n\n\n\n\n\nb") == "a\n\nb"


def test_normalize_joiner_removal_precedes_nfc():
    # If NFC ran first the joiner would still separate base and mark, and a
    # SECOND normalize() would compose them — the non-idempotent case.
    assert normalize("e\u200b\u0301cole") == "école"


def test_ocr_stats_rounds_and_counts():
    mean, n, low = ocr_stats([0.9, 0.8, 0.700001], 0.85)
    assert (mean, n, low) == (0.8, 3, 2)


def test_ocr_stats_empty():
    assert ocr_stats([], 0.85) == (0.0, 0, 0)


def test_ocr_page_that_recovered_nothing_becomes_empty():
    p = make_page(1, "   ", PageKind.OCR, confidences=[0.4])
    assert p.kind is PageKind.EMPTY
    assert p.ocr_conf is None
    assert "ocr: no text recovered" in p.notes
    p.validate()


def test_ocr_page_with_text_keeps_confidence():
    p = make_page(3, "READ", PageKind.OCR, confidences=[0.9, 0.6],
                  conf_threshold=0.85)
    assert p.kind is PageKind.OCR
    assert p.ocr_conf == 0.75
    assert (p.ocr_line_count, p.ocr_low_conf_lines) == (2, 1)


def test_native_page_with_no_text_becomes_empty():
    assert make_page(1, "", PageKind.NATIVE).kind is PageKind.EMPTY


def test_make_page_normalizes_before_building():
    p = make_page(1, "a\r\nb\u00a0", PageKind.NATIVE)
    assert p.text == "a\nb"


def test_synthetic_pages_keep_empty_blocks_as_pages():
    pages = synthetic_pages(["one", "", "three"])
    assert [p.page_no for p in pages] == [1, 2, 3]
    assert pages[1].kind is PageKind.EMPTY


def test_synthetic_pages_of_nothing_is_one_page():
    assert len(synthetic_pages([])) == 1


def test_contract_still_rejects_a_hand_built_bad_record():
    # The fail-before for make_page's whole reason to exist.
    with pytest.raises(ContractViolation):
        PageRecord(page_no=1, text="x", kind=PageKind.OCR).validate()
    with pytest.raises(ContractViolation):
        PageRecord(page_no=1, text="x", kind=PageKind.NATIVE,
                   ocr_conf=0.9).validate()
