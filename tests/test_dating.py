"""Date detection — deterministic, order-preserving, no AI."""

from __future__ import annotations

from dociq.ingest.dating import detect_dates, document_date


def test_first_appearance_order_is_preserved_not_sorted():
    text = "Issued 2024-09-01, superseding the notice of 2024-03-15."
    assert detect_dates(text) == ("2024-09-01", "2024-03-15")


def test_every_supported_written_form():
    assert detect_dates("March 1, 2024") == ("2024-03-01",)
    assert detect_dates("1 March 2024") == ("2024-03-01",)
    assert detect_dates("01-Mar-24") == ("2024-03-01",)
    assert detect_dates("Sept 4, 2024") == ("2024-09-04",)
    assert detect_dates("3/1/2024") == ("2024-03-01",)
    assert detect_dates("18.06.2025") == ("2025-06-18",)


def test_ambiguous_numeric_follows_the_convention_parameter():
    assert detect_dates("05/03/2024") == ("2024-05-03",)
    assert detect_dates("05/03/2024", convention="eu") == ("2024-03-05",)


def test_unambiguous_numeric_ignores_the_convention():
    assert detect_dates("16/07/2024", convention="us") == ("2024-07-16",)


def test_invalid_calendar_dates_are_dropped():
    assert detect_dates("2024-02-30") == ()
    assert detect_dates("13/45/2024") == ()


def test_non_dates_are_not_dates():
    assert detect_dates("Call 555-1234 about job 12-34-56789") == ()
    assert detect_dates("version 1.2.2024") == ("2024-02-01",)  # dot form, valid


def test_duplicates_collapse_to_first_appearance():
    assert detect_dates("2024-07-16 and again 2024-07-16") == ("2024-07-16",)


def test_limit_is_honoured():
    text = " ".join(f"2024-01-{d:02d}" for d in range(1, 20))
    assert len(detect_dates(text, limit=5)) == 5


def test_detection_is_stable_across_repeated_calls():
    text = "2024-07-16 / 7/16/2024 / 16 July 2024 / 2023-01-02"
    first = detect_dates(text)
    assert all(detect_dates(text) == first for _ in range(30))


def test_document_date_prefers_the_filename():
    assert document_date("2024-07-16 Daily Report.pdf",
                         "Letterhead dated 1999-01-01") == "2024-07-16"


def test_document_date_falls_back_to_the_body():
    assert document_date("report.pdf", "Period ending 2024-07-31") == "2024-07-31"


def test_document_date_may_be_none():
    assert document_date("report.pdf", "no dates here") is None
