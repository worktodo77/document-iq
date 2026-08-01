"""Format profiles: schema, detection, and the KEEP/DROP engine (§6, §4 Stage 4)."""

from __future__ import annotations

import pytest

from dociq.contracts import ContractViolation, Disposition
from dociq.profiles.apply import apply_profile, apply_profiles
from dociq.profiles.detect import (
    DetectionLimits,
    detect_candidate_sections,
    looks_like_header,
    normalize_label,
)
from dociq.profiles.model import (
    FormatProfile,
    OperatorStamp,
    ProfileError,
    SectionRule,
    dump_profile,
    load_profile,
    loads_profile,
    operator_stamp,
    profile_library_dir,
    save_to_library,
    write_matter_copy,
)
from tests.fixtures import MPR_PAGES, corpus, document, page


def mpr_profile(**kw) -> FormatProfile:
    return FormatProfile(
        profile_id="modec-mpr",
        version="1",
        display_name="MODEC Monthly Progress Report",
        header_patterns=("MONTHLY PROGRESS REPORT",),
        section_rules=(
            SectionRule("hse-stats", r"^HSE STATISTICS", Disposition.DROP,
                        notes="Safety statistics tables carry no delay evidence. Approved by A. Bachowski."),
            SectionRule("photo-log", r"^PHOTO LOG", Disposition.DROP,
                        notes="Image captions only; no readable text. Approved by A. Bachowski."),
            SectionRule("org-chart", r"^ORGANISATION CHART", Disposition.DROP,
                        notes="Static org data, repeated monthly. Approved by A. Bachowski."),
            SectionRule("exec-summary", r"^EXECUTIVE SUMMARY", Disposition.KEEP),
            SectionRule("schedule", r"^SCHEDULE STATUS", Disposition.KEEP),
        ),
        **kw,
    )


# --- schema ----------------------------------------------------------------


def test_drop_rule_without_notes_is_refused():
    with pytest.raises(ProfileError) as exc:
        FormatProfile(
            profile_id="p", version="1", display_name="p",
            section_rules=(SectionRule("r", "x", Disposition.DROP),),
        ).validate()
    assert "notes" in str(exc.value)


def test_keep_rule_needs_no_justification():
    FormatProfile(
        profile_id="p", version="1", display_name="p",
        section_rules=(SectionRule("r", "x"),),
    ).validate()


def test_duplicate_rule_ids_are_refused():
    with pytest.raises(ProfileError):
        FormatProfile(
            profile_id="p", version="1", display_name="p",
            section_rules=(SectionRule("r", "x"), SectionRule("r", "y")),
        ).validate()


def test_bad_regex_is_reported_against_the_rule():
    with pytest.raises(ProfileError) as exc:
        SectionRule("r", "([", Disposition.KEEP).validate()
    assert "'r'" in str(exc.value)


def test_yaml_round_trip_is_exact():
    profile = mpr_profile().stamped(OperatorStamp("abachowski", "2026-07-30T12:00:00Z", "LI-PC"))
    assert loads_profile(dump_profile(profile)) == profile


def test_yaml_dump_is_byte_stable():
    profile = mpr_profile().stamped(OperatorStamp("a", "2026-07-30T12:00:00Z"))
    first = dump_profile(profile)
    for _ in range(8):
        assert dump_profile(profile) == first


def test_unknown_key_is_refused_rather_than_ignored():
    text = dump_profile(mpr_profile()).replace("notes:", "notez:", 1)
    with pytest.raises(ProfileError) as exc:
        loads_profile(text)
    assert "unknown key" in str(exc.value)


def test_profile_hash_changes_with_the_operator():
    a = mpr_profile().stamped(OperatorStamp("alice", "2026-07-30T12:00:00Z"))
    b = mpr_profile().stamped(OperatorStamp("bob", "2026-07-30T12:00:00Z"))
    assert a.profile_hash != b.profile_hash


def test_operator_stamp_uses_the_windows_username(monkeypatch):
    monkeypatch.setenv("USERNAME", "abachowski")
    monkeypatch.setenv("COMPUTERNAME", "LI-LAPTOP")
    stamp = operator_stamp()
    assert stamp.username == "abachowski"
    assert stamp.host == "LI-LAPTOP"
    assert stamp.saved_at.endswith("Z")


def test_library_default_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.delenv("DOCIQ_PROFILE_LIBRARY", raising=False)
    assert profile_library_dir().parts[-2:] == ("LI DocIQ", "profiles")
    monkeypatch.setenv("DOCIQ_PROFILE_LIBRARY", str(tmp_path / "shared"))
    assert profile_library_dir() == tmp_path / "shared"
    assert profile_library_dir(tmp_path / "explicit") == tmp_path / "explicit"


def test_matter_copy_is_written_regardless_of_the_library(tmp_path):
    profile = mpr_profile()
    matter = write_matter_copy(profile, tmp_path / "matter")
    assert matter.exists()
    assert load_profile(matter) == profile
    lib = save_to_library(profile, tmp_path / "lib")
    assert lib.exists() and lib != matter


# --- detection -------------------------------------------------------------


def test_headers_are_proposed_never_dropped():
    result = detect_candidate_sections(corpus(3))
    labels = {c.label for c in result.candidates}
    assert "HSE STATISTICS" in labels
    assert "PHOTO LOG" in labels
    # Detection returns candidates only; nothing carries a disposition.
    assert not hasattr(result.candidates[0], "disposition")


def test_detection_reports_frequency_and_first_instance():
    result = detect_candidate_sections(corpus(3))
    hse = next(c for c in result.candidates if c.label == "HSE STATISTICS")
    assert hse.document_count == 3
    assert hse.occurrences == 3
    assert hse.first_seen[1] == 4


def test_detection_order_is_deterministic():
    docs = corpus(3)
    first = detect_candidate_sections(docs).candidates
    for _ in range(8):
        assert detect_candidate_sections(docs).candidates == first


def test_body_text_is_not_a_header():
    lim = DetectionLimits()
    assert not looks_like_header(
        "The project completed 62 percent of planned engineering deliverables.", lim
    )
    assert not looks_like_header("Activity\tPlanned\tActual", lim)
    assert looks_like_header("HSE STATISTICS", lim)
    assert looks_like_header("4.2 Schedule Status", lim)


def test_numbering_is_stripped_but_words_are_not():
    assert normalize_label("4.2 Schedule Status") == "Schedule Status"
    assert normalize_label("A. Introduction") == "Introduction"
    assert normalize_label("A Summary Of Events") == "A Summary Of Events"
    assert normalize_label("I need more time") == "I need more time"


def test_truncation_is_reported_not_silent():
    pages = tuple(page(i, f"SECTION {i:03d}") for i in range(1, 40))
    result = detect_candidate_sections(
        (document("a.pdf", pages),), DetectionLimits(max_candidates=5)
    )
    assert result.truncated
    assert len(result.candidates) == 5
    assert any("max_candidates" in n for n in result.notes)


# --- apply -----------------------------------------------------------------


def test_keep_is_the_default_for_unmatched_pages():
    doc = corpus(1)[0]
    result = apply_profile(doc, mpr_profile())
    out = result.documents[0]
    assert out.pages[0].disposition is Disposition.KEEP  # cover page, no section
    assert out.pages[1].disposition is Disposition.KEEP  # explicit KEEP rule


def test_every_drop_is_attributed():
    doc = corpus(1)[0]
    result = apply_profile(doc, mpr_profile())
    out = result.documents[0]
    dropped = [p for p in out.pages if p.disposition is Disposition.DROP]
    assert {p.page_no for p in dropped} == {4, 5, 6}
    assert all(p.drop_rule for p in dropped)
    assert {e.rule_id for e in result.drops} == {"hse-stats", "photo-log", "org-chart"}
    assert all(e.rule_notes for e in result.drops)


def test_accounting_holds_after_a_drop():
    out = apply_profile(corpus(1)[0], mpr_profile()).documents[0]
    out.validate()
    assert out.pages_in == out.pages_kept + out.pages_dropped == 6


def test_document_no_profile_claims_passes_through_whole():
    doc = document("misc/letter.pdf", (page(1, "Dear Sir"), page(2, "PHOTO LOG")))
    result = apply_profile(doc, mpr_profile())
    assert result.documents[0] is doc
    assert result.drops == ()
    assert result.profiled_doc_ids == ()


def test_header_match_is_limited_to_the_opening_pages():
    pages = (page(1, "Dear Sir"),) * 0 + tuple(
        page(i, "filler") for i in range(1, 6)
    ) + (page(6, "MONTHLY PROGRESS REPORT"),)
    doc = document("misc/letter.pdf", pages)
    assert apply_profile(doc, mpr_profile()).drops == ()


def test_competing_profiles_are_reported_not_silently_resolved():
    a = mpr_profile()
    b = FormatProfile(
        profile_id="other", version="1", display_name="other",
        header_patterns=("MONTHLY PROGRESS REPORT",),
    )
    result = apply_profiles(corpus(1), (a, b))
    assert any("all claim this document" in w for w in result.warnings)


def test_a_drop_without_a_rule_cannot_be_constructed():
    """The contract enforces attribution independently of this module."""
    doc = corpus(1)[0]
    with pytest.raises(ContractViolation):
        doc.pages[0].evolve(disposition=Disposition.DROP).validate()


def test_apply_is_stable_over_repeated_runs():
    docs = corpus(3)
    profile = mpr_profile()
    first = apply_profiles(docs, (profile,))
    for _ in range(8):
        assert apply_profiles(docs, (profile,)) == first
