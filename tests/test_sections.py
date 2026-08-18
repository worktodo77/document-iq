"""Section recognition: the tiers, the templates, and the approval gate.

**The first class of test in this file is the reason the file exists.** D-35's
five trigger shapes are reproduced here against the NEW engine. Each of them was
watched failing against the old one, which matched a regex against every line of
every page and carried the section forward until another rule matched, so a
single DROP rule for `PROGRESS PHOTOGRAPHS` dropped the executive summary, the
critical path narrative, the weather log and the timesheets, and attributed all
of them to `PROGRESS PHOTOGRAPHS`.

**That engine is `dociq/profiles/apply.py`, and it is not in the tree — commit
4092f76 deleted it.** Said here because the sentence above is the justification
for every test below it and a reader must be able to check it: the five shapes,
the pages each one lost and the pages the expert meant to lose are tabulated in
`docs/verification/sections_2026-08-17.md`, and the five Stage-4 guarantees that
outlived the deletion are re-pointed in `tests/test_profiles.py`.

These are written as properties of the *class*, not as five named repros: what
must hold is that **no page outside the span that caused a drop is ever
dropped**, and the last test in the group asserts exactly that over generated
documents rather than over the five shapes someone happened to think of.
"""

from __future__ import annotations

import pytest

from dociq.contracts import (
    matter_key,
    ContractViolation,
    Disposition,
    DocumentRecord,
    PageKind,
    PageRecord,
    RecognitionTier,
)
from dociq.sections import resolve_sections
from dociq.sections.apply import apply_sections
from dociq.sections.model import (
    ApprovedOmission,
    Risk,
    SectionFamily,
    SectionSpan,
    SectionTemplate,
    TemplateError,
)
from dociq.sections.normalize import family_key, strip_numbering, strip_project_tokens
from dociq.sections.resolve import overlaps, section_for_page
from dociq.sections.templates import PROGRESS_REPORT
from dociq.sections.tier1_outline import spans_from_outline
from dociq.sections.tier3_pageclass import PageSignals, spans_from_page_classes


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def page(n: int, text: str = "") -> PageRecord:
    return PageRecord(page_no=n, text=text, kind=PageKind.NATIVE)


def document(*texts: str) -> DocumentRecord:
    return DocumentRecord(
        doc_id="LI-00001",
        rel_path="mpr.pdf",
        filename="mpr.pdf",
        sha256="0" * 64,
        size_bytes=1,
        ext=".pdf",
        pages=tuple(page(i, t) for i, t in enumerate(texts, 1)),
    )


PHOTO_FAMILY = SectionFamily(
    family_id="progress-photographs",
    display_name="Progress photographs",
    patterns=(r"PROGRESS PHOTO", r"^PHOTOGRAPH"),
    risk=Risk.HIGH,
    rationale=(
        "Often the only proof of site condition on a date. Worth 0.2% of the "
        "text on the measured corpus — a page-count lever, not a token lever."
    ),
)

TEMPLATE = SectionTemplate(
    template_id="progress-report",
    version="1",
    display_name="Monthly progress report",
    families=(PHOTO_FAMILY,),
)

APPROVAL = ApprovedOmission(
    family_id="progress-photographs",
    approved_by="ABachowski",
    approved_at="2026-08-17T12:00:00Z",
    matter="MODEC-495",
    matter_root=matter_key("MODEC-495"),
    template_id="progress-report",
    template_version="1",
)


def outline_spans_for(entries, page_count):
    return spans_from_outline(entries, page_count)


# ---------------------------------------------------------------------------
# D-35 — the class the old engine could not hold
# ---------------------------------------------------------------------------


def test_a_toc_line_naming_the_section_does_not_drop_the_report():
    """Shape 1. The old engine matched `PROGRESS PHOTOGRAPHS ..... 6` on the
    contents page and dropped from there to the end of the document."""
    doc = document(
        "MONTHLY PROGRESS REPORT",
        "TABLE OF CONTENTS\nEXECUTIVE SUMMARY ... 3\nPROGRESS PHOTOGRAPHS ... 6",
        "EXECUTIVE SUMMARY\nThe project is 14 weeks behind.",
        "CRITICAL PATH NARRATIVE\nDelay driven by late vendor data.",
        "Continued narrative.",
        "PROGRESS PHOTOGRAPHS",
        "[image]",
    )
    spans = outline_spans_for(
        [("1 EXECUTIVE SUMMARY", 2), ("2 CRITICAL PATH NARRATIVE", 3),
         ("3 PROGRESS PHOTOGRAPHS", 5)],
        page_count=7,
    )
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    dropped = [p.page_no for p in result.documents[0].pages
               if p.disposition is Disposition.DROP]
    assert dropped == [6, 7], (
        "only the photograph pages may drop; the contents page mentioning them "
        "is not a photograph page"
    )


def test_a_body_text_mention_does_not_drop_anything():
    """Shape 2. `...evidenced in the PROGRESS PHOTOGRAPHS appended...`"""
    doc = document(
        "EXECUTIVE SUMMARY\nThe project is 14 weeks behind.",
        "Site conditions are evidenced in the PROGRESS PHOTOGRAPHS appended.",
        "CRITICAL PATH NARRATIVE\nDelay driven by late vendor data.",
    )
    spans = outline_spans_for([("1 EXECUTIVE SUMMARY", 0)], page_count=3)
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    assert not [p for p in result.documents[0].pages
                if p.disposition is Disposition.DROP]


def test_a_transmittal_listing_enclosures_does_not_drop_anything():
    """Shape 3."""
    doc = document(
        "TRANSMITTAL\nEnclosed: MPR Rev 3; PROGRESS PHOTOGRAPHS; NCR log.",
        "EXECUTIVE SUMMARY\nThe project is 14 weeks behind.",
    )
    result = apply_sections(doc, (), template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    assert not [p for p in result.documents[0].pages
                if p.disposition is Disposition.DROP]


def test_an_appendix_cover_sheet_does_not_drop_what_follows():
    """Shape 4."""
    doc = document(
        "QUALITY / NCR LOG\nNCR-114 open.",
        "APPENDIX C\nContaining: PROGRESS PHOTOGRAPHS",
        "TIMESHEETS\nWeek 14 labour tickets.",
    )
    spans = outline_spans_for(
        [("1 QUALITY NCR LOG", 0), ("2 APPENDIX C", 1), ("3 TIMESHEETS", 2)],
        page_count=3,
    )
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    assert not [p for p in result.documents[0].pages
                if p.disposition is Disposition.DROP]


def test_a_correct_match_stops_at_the_end_of_its_own_section():
    """Shape 5, and the worst of them — it needs no confusing text at all.

    The old engine's carried state had no end, so a CORRECT first match ran to
    the end of the document, taking the weather log and the timesheets with it.
    """
    doc = document(
        "PROGRESS PHOTOGRAPHS",
        "[image]",
        "WEATHER LOG\n12 March: 40kt winds, crane stood down.",
        "TIMESHEETS\nWeek 14 labour tickets.",
    )
    spans = outline_spans_for(
        [("1 PROGRESS PHOTOGRAPHS", 0), ("2 WEATHER LOG", 2), ("3 TIMESHEETS", 3)],
        page_count=4,
    )
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    dropped = [p.page_no for p in result.documents[0].pages
               if p.disposition is Disposition.DROP]
    assert dropped == [1, 2]


@pytest.mark.parametrize("mention_page", range(0, 8))
def test_no_page_outside_the_causing_span_is_ever_dropped(mention_page):
    """The CLASS, asserted directly rather than through five named shapes.

    A document of eight pages where the phrase appears on an arbitrary page, and
    the section itself is a two-page span. Whatever the mention does, the drop
    must be exactly the span.
    """
    texts = ["narrative"] * 8
    texts[mention_page] = "see the PROGRESS PHOTOGRAPHS appended to this report"
    doc = document(*texts)
    spans = outline_spans_for(
        [("1 NARRATIVE", 0), ("2 PROGRESS PHOTOGRAPHS", 4), ("3 TIMESHEETS", 6)],
        page_count=8,
    )
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    dropped = [p.page_no for p in result.documents[0].pages
               if p.disposition is Disposition.DROP]
    assert dropped == [5, 6], f"mention on page {mention_page + 1} moved the drop"


# ---------------------------------------------------------------------------
# D-34 — the approval gate
# ---------------------------------------------------------------------------


def test_a_template_alone_drops_nothing():
    """The whole of D-34's first property. An unengaged template recognizes."""
    doc = document("PROGRESS PHOTOGRAPHS", "[image]")
    spans = outline_spans_for([("1 PROGRESS PHOTOGRAPHS", 0)], page_count=2)
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=())
    assert result.pages_dropped == 0
    assert all(p.disposition is Disposition.KEEP for p in result.documents[0].pages)
    assert all(p.section == "1 PROGRESS PHOTOGRAPHS"
               for p in result.documents[0].pages), "recognition still happens"


def test_a_template_cannot_express_a_disposition():
    """D-34 made structural. A shipped template file has no field in which a
    drop could be written, so a future editor cannot add one by accident."""
    fields = set(SectionTemplate.__dataclass_fields__)
    assert not fields & {"disposition", "approved_by", "approver", "drop"}
    family_fields = set(SectionFamily.__dataclass_fields__)
    assert not family_fields & {"disposition", "approved_by", "approver", "drop"}


def test_an_omission_naming_nobody_is_refused():
    with pytest.raises(TemplateError, match="approved_by is empty"):
        ApprovedOmission(
            family_id="progress-photographs",
            approved_by="   ",
            approved_at="2026-08-17T12:00:00Z",
            matter="MODEC-495",
            matter_root=matter_key("MODEC-495"),
            template_id="progress-report",
            template_version="1",
        ).validate()


def test_an_omission_naming_no_matter_is_refused():
    with pytest.raises(TemplateError, match="matter is empty"):
        ApprovedOmission(
            family_id="progress-photographs",
            approved_by="ABachowski",
            approved_at="2026-08-17T12:00:00Z",
            matter="",
            matter_root=matter_key(""),
            template_id="progress-report",
            template_version="1",
        ).validate()


def test_an_approval_for_an_unknown_family_warns_and_drops_nothing():
    doc = document("PROGRESS PHOTOGRAPHS")
    spans = outline_spans_for([("1 PROGRESS PHOTOGRAPHS", 0)], page_count=1)
    stray = ApprovedOmission(
        family_id="weather-logs",
        approved_by="ABachowski",
        approved_at="2026-08-17T12:00:00Z",
        matter="MODEC-495",
        matter_root=matter_key("MODEC-495"),
        template_id="progress-report",
        template_version="1",
    )
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(stray,),
                            matter_root="MODEC-495")
    assert result.pages_dropped == 0
    assert any("does not define" in w for w in result.warnings)


def test_a_drop_carries_its_approver_and_its_tier():
    doc = document("PROGRESS PHOTOGRAPHS", "[image]")
    spans = outline_spans_for([("1 PROGRESS PHOTOGRAPHS", 0)], page_count=2)
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    entry = result.drops[0]
    assert entry.approved_by == "ABachowski"
    assert entry.matter == "MODEC-495"
    assert entry.tier is RecognitionTier.OUTLINE
    assert "outline" in entry.evidence
    assert entry.drop_rule == "progress-report:progress-photographs"


def test_a_family_marked_not_offered_never_drops():
    never = SectionFamily(
        family_id="executive-summary",
        display_name="Executive summary",
        patterns=(r"EXECUTIVE SUMMARY",),
        risk=Risk.HIGH,
        rationale="The densest narrative evidence in the record.",
        offer=False,
    )
    template = SectionTemplate(
        template_id="t", version="1", display_name="t", families=(never,)
    )
    approval = ApprovedOmission(
        family_id="executive-summary",
        approved_by="ABachowski",
        approved_at="2026-08-17T12:00:00Z",
        matter="M",
        matter_root=matter_key("M"),
        template_id="t",
        template_version="1",
    )
    doc = document("EXECUTIVE SUMMARY")
    spans = outline_spans_for([("1 EXECUTIVE SUMMARY", 0)], page_count=1)
    result = apply_sections(doc, spans, template=template, approvals=(approval,),
                            matter_root="M")
    assert result.pages_dropped == 0


# ---------------------------------------------------------------------------
# Tier 1
# ---------------------------------------------------------------------------


def test_front_matter_before_the_first_entry_belongs_to_nobody():
    spans = spans_from_outline([("1 EXECUTIVE SUMMARY", 2)], 5)
    assert section_for_page(spans, 1) is None
    assert section_for_page(spans, 2) is None
    assert section_for_page(spans, 3).family == "EXECUTIVE SUMMARY"


def test_nested_entries_at_one_page_yield_the_deepest_label():
    spans = spans_from_outline(
        [("4 ENGINEERING", 9), ("4.1 MARINE ENGINEERING", 9), ("5 PROCUREMENT", 19)],
        25,
    )
    assert section_for_page(spans, 10).family == "MARINE ENGINEERING"
    assert overlaps(spans) == ()


def test_a_positional_entry_ends_the_previous_span_and_names_nothing():
    """`Slide Number 33` is a boundary the document draws without saying what it
    is. The preceding section must stop there — running through it would label
    pages the document never placed in it."""
    spans = spans_from_outline(
        [("1 MARINE ENGINEERING", 9), ("Slide Number 33", 14), ("2 PROCUREMENT", 19)],
        25,
    )
    assert section_for_page(spans, 14).family == "MARINE ENGINEERING"
    assert section_for_page(spans, 15) is None, "the unnamed boundary must bite"
    assert section_for_page(spans, 19) is None
    assert section_for_page(spans, 20).family == "PROCUREMENT"


def test_a_bare_numeric_entry_is_positional():
    """`5.1` normalizes to `5 1`, which an anchored digit match misses. The
    measurement probe's first version had exactly this hole."""
    assert family_key("5.1") is None
    assert family_key("Slide Number 33") is None
    spans = spans_from_outline([("5.1", 0), ("6 PROCUREMENT", 4)], 10)
    assert all(s.family != "5 1" for s in spans)


def test_a_non_monotonic_entry_is_skipped_not_sorted():
    spans = spans_from_outline(
        [("1 FIRST", 0), ("2 SECOND", 10), ("3 BACKWARDS", 4), ("4 LAST", 15)], 20
    )
    families = [s.family for s in spans]
    assert "BACKWARDS" not in families
    assert families == ["FIRST", "SECOND", "LAST"]
    assert overlaps(spans) == ()


def test_an_out_of_range_destination_is_ignored():
    spans = spans_from_outline([("1 REAL", 0), ("2 BEYOND", 99)], 5)
    assert [s.family for s in spans] == ["REAL"]
    assert spans[0].end_page == 5


def test_an_outline_of_only_positional_entries_yields_nothing():
    assert spans_from_outline([("Slide Number 1", 0), ("Slide Number 2", 1)], 5) == ()


# ---------------------------------------------------------------------------
# Tier 3
# ---------------------------------------------------------------------------


def test_page_classes_recognize_the_measured_categories():
    signals = [
        PageSignals(page_no=1, text="TABLE OF CONTENTS\n1 Executive summary ... 3"),
        PageSignals(page_no=2, text="DISTRIBUTION LIST\nA. Smith\nB. Jones"),
        PageSignals(
            page_no=3,
            text="ACTIVITY ID  ACTIVITY NAME  TOTAL FLOAT\nA1000 Piling 4",
        ),
        PageSignals(page_no=4, text="", has_image=True, image_area_share=0.8),
    ]
    spans = spans_from_page_classes(signals)
    assert [s.section for s in spans] == [
        "Table of contents",
        "Distribution list",
        "Schedule / activity table",
        "Photograph / figure page",
    ]
    assert all(s.tier is RecognitionTier.PAGE_CLASS for s in spans)


def test_one_activity_header_is_not_a_schedule_table():
    """Two are required. `ACTIVITY NAME` alone appears in prose."""
    signals = [PageSignals(page_no=1, text="The ACTIVITY NAME was changed in Rev 3.")]
    assert spans_from_page_classes(signals) == ()


def test_a_date_grid_is_a_schedule_table_even_without_headers():
    text = " ".join(f"{d:02d}-Mar-24" for d in range(1, 15))
    spans = spans_from_page_classes([PageSignals(page_no=1, text=text)])
    assert spans[0].section == "Schedule / activity table"


def test_low_text_without_an_image_is_not_a_photo_page():
    spans = spans_from_page_classes(
        [PageSignals(page_no=1, text="Section 4", has_image=False)]
    )
    assert spans == ()


def test_adjacent_pages_of_one_class_merge_into_one_span():
    signals = [
        PageSignals(page_no=n, text="", has_image=True, image_area_share=0.9)
        for n in (1, 2, 3, 5)
    ]
    spans = spans_from_page_classes(signals)
    assert [(s.start_page, s.end_page) for s in spans] == [(1, 3), (5, 5)]


def test_tier_3_never_relabels_a_page_tier_1_placed():
    signals = [PageSignals(page_no=n, text="TABLE OF CONTENTS") for n in (1, 2, 3)]
    spans = spans_from_page_classes(signals, covered=frozenset({2}))
    assert [(s.start_page, s.end_page) for s in spans] == [(1, 1), (3, 3)]


def test_an_unresolved_page_has_no_section_and_therefore_keeps():
    doc = document("ordinary narrative prose about the works")
    spans = resolve_sections(
        outline=[], page_count=1,
        signals=[PageSignals(page_no=1, text="ordinary narrative prose")],
    )
    assert spans == ()
    result = apply_sections(doc, spans, template=TEMPLATE, approvals=(APPROVAL,),
                            matter_root="MODEC-495")
    assert result.documents[0].pages[0].disposition is Disposition.KEEP
    assert result.documents[0].pages[0].section is None


# ---------------------------------------------------------------------------
# normalization — every rule here came from a corpus measurement
# ---------------------------------------------------------------------------


def test_numbering_is_stripped_to_every_depth():
    assert strip_numbering("4 5 1 MARINE ENGINEERING") == "MARINE ENGINEERING"
    assert family_key("4.5 ENGINEERING") == family_key("5.4 ENGINEERING")


def test_project_tokens_are_stripped_as_whole_words():
    assert strip_project_tokens("MV32 APPENDICES", ("MV32",)) == "APPENDICES"
    assert strip_project_tokens("EBRD FINANCING", ("EBR",)) == "EBRD FINANCING", (
        "substring removal would mangle a real word; EBR is a yard in the corpus"
    )


def test_a_label_that_is_only_a_project_token_keys_to_nothing():
    assert family_key("MV32", ("MV32",)) is None


def test_matching_is_accent_folded_for_a_non_english_record():
    """The corpus's third most frequent label is Portuguese."""
    assert family_key("PÁGINA EM BRANCO") == "PAGINA EM BRANCO"
    assert family_key("PAGINA EM BRANCO") == "PAGINA EM BRANCO"


# ---------------------------------------------------------------------------
# the contract half (amendment A-18)
# ---------------------------------------------------------------------------


def test_a_section_without_a_tier_is_refused():
    with pytest.raises(ContractViolation, match="without a section_tier"):
        PageRecord(page_no=1, text="", kind=PageKind.NATIVE,
                   section="EXECUTIVE SUMMARY").validate()


def test_a_tier_without_a_section_is_refused():
    with pytest.raises(ContractViolation, match="without a section"):
        PageRecord(page_no=1, text="", kind=PageKind.NATIVE,
                   section_tier=RecognitionTier.OUTLINE).validate()


def test_a_drop_without_a_tier_is_refused():
    with pytest.raises(ContractViolation, match="DROP without a section_tier"):
        PageRecord(page_no=1, text="", kind=PageKind.NATIVE,
                   disposition=Disposition.DROP, drop_rule="t:f").validate()


def test_the_tier_values_are_the_stable_audit_vocabulary():
    """Renaming one breaks the audit trail between runs, exactly as renaming a
    rule id does."""
    assert [t.value for t in RecognitionTier] == [
        "t1_outline", "t2_toc", "t3_page_class", "t4_explicit",
    ]


def test_a_span_must_name_a_real_range():
    with pytest.raises(TemplateError, match="precedes"):
        SectionSpan(
            section="X", family="X", tier=RecognitionTier.OUTLINE,
            start_page=5, end_page=2, evidence="e",
        ).validate()


# ---------------------------------------------------------------------------
# the shipped templates (D-24)
# ---------------------------------------------------------------------------


def test_every_shipped_template_validates():
    from dociq.sections.templates import BUILT_IN_TEMPLATES

    assert BUILT_IN_TEMPLATES
    for template in BUILT_IN_TEMPLATES:
        template.validate()


def test_no_shipped_family_names_a_project():
    """D-24, asserted against the REAL corpus's own project tokens.

    Not against a list of words someone chose to check: these are the tokens
    that actually contaminate 30.5% of the measured outline vocabulary, so a
    template built by copying observed labels fails this test rather than
    shipping a client's vessel name inside a Long International deliverable.
    """
    from dociq.sections.templates import BUILT_IN_TEMPLATES

    corpus_tokens = (
        "MV32", "PETROBRAS", "FPSO", "BOMESC", "COSCO", "BRASFELS", "EBR",
        "MODEC", "ALMIRANTE", "BARROSO", "MNFV",
    )
    for template in BUILT_IN_TEMPLATES:
        haystack = " ".join(
            [template.template_id, template.display_name, template.notes]
            + [f.family_id + " " + f.display_name + " " + " ".join(f.patterns)
               for f in template.families]
        ).upper()
        for token in corpus_tokens:
            assert token not in haystack, (
                f"template {template.template_id!r} names {token!r} — D-24 "
                "forbids a template attributable to a corpus project"
            )


def test_every_shipped_family_states_what_omitting_it_costs():
    """§5.3. A lever offered without a stated cost is a lever that gets clicked."""
    from dociq.sections.templates import BUILT_IN_TEMPLATES

    for template in BUILT_IN_TEMPLATES:
        for family in template.families:
            assert len(family.rationale.strip()) > 40, (
                f"{family.family_id}: rationale is too thin to inform a ruling"
            )


def test_the_high_risk_evidentiary_sections_are_recognized_and_never_offered():
    """§4 grades these HIGH and keeps them. Recognition without offering is the
    setting that lets an expert FIND them without being invited to drop them."""
    from dociq.sections.templates import PROGRESS_REPORT

    for family_id in (
        "executive-summary", "critical-path-narrative", "change-and-variation",
        "quality-ncr", "manpower-histograms", "weather-logs", "action-items",
        "timesheets",
    ):
        family = PROGRESS_REPORT.family(family_id)
        assert family is not None, f"{family_id} is missing from the template"
        assert family.risk is Risk.HIGH
        assert not family.offer, f"{family_id} must never be offered as a drop"


def test_the_photograph_row_states_that_it_is_not_a_token_saving():
    """§5.1: stop advertising photo-dropping as a saving. The row must say so
    itself, because the row is what the expert reads."""
    from dociq.sections.templates import PROGRESS_REPORT

    rationale = PROGRESS_REPORT.family("progress-photographs").rationale
    assert "0.2%" in rationale
    assert "PAGE-COUNT" in rationale.upper()


def test_the_measured_corpus_vocabulary_routes_to_the_expected_families():
    """The template is keyed to families; these are real labels from the
    measured outline vocabulary, normalized as a run would normalize them."""
    from dociq.sections.templates import PROGRESS_REPORT

    cases = {
        "1 EXECUTIVE SUMMARY": "executive-summary",
        "3.7 CRITICAL PATH NARRATIVE": "critical-path-narrative",
        "4.1 HSE": "hse-statistics",
        "3.2 OVERALL PROGRESS S CURVE": "progress-curves",
        "PROGRESS PHOTOS": "progress-photographs",
        "PAGINA EM BRANCO": "blank-page",
        "4.3 CONTRACT ADMINISTRATION": "change-and-variation",
        "5 ONE 1 MONTH LOOK AHEAD ACTIVITIES": "schedule-activity-tables",
    }
    for label, expected in cases.items():
        key = family_key(label)
        assert key is not None, label
        family = PROGRESS_REPORT.classify(key)
        assert family is not None, f"{label!r} -> {key!r} matched no family"
        assert family.family_id == expected, (
            f"{label!r} -> {key!r} matched {family.family_id!r}, "
            f"expected {expected!r}"
        )


def test_an_unrecognized_section_matches_no_family_and_therefore_keeps():
    from dociq.sections.templates import PROGRESS_REPORT

    key = family_key("4.5.2 EICT ENGINEERING")
    assert PROGRESS_REPORT.classify(key) is None, (
        "a section the template does not know must fall through to KEEP, not "
        "to the nearest-looking family"
    )


# ---------------------------------------------------------------------------
# Codex Sprint-3 review, findings B-1 and B-2
# ---------------------------------------------------------------------------


def test_a_backward_outline_entry_stops_the_previous_section():
    """B-1. A skipped non-monotonic entry let the previous section overrun it.

    `spans_from_outline` discarded an out-of-order destination and said its pages
    were "left unresolved, which keeps them". It removed the entry from the
    boundary set, so the preceding span's end was computed from the NEXT
    surviving boundary and ran straight through it.

    Codex's outline, reproduced verbatim. Pages 5-10 belong to the executive
    summary and were returned under `PROGRESS PHOTOGRAPHS` — an OFFERED family,
    so an expert who engaged photographs dropped the executive summary. The
    D-35 failure class in span form: no span ran past its stated end, the end was
    computed from a boundary set that had thrown the contrary boundary away.
    """
    spans = spans_from_outline(
        [("PROGRESS PHOTOGRAPHS", 0), ("PROCUREMENT", 10), ("EXECUTIVE SUMMARY", 4)],
        12,
    )
    photos = [s for s in spans if s.family == "PROGRESS PHOTOGRAPHS"]
    assert len(photos) == 1
    assert photos[0].end_page == 4, (
        "the photographs section runs past the backward destination at page 5 — "
        f"it ends at {photos[0].end_page}"
    )
    covered = {n for s in spans for n in range(s.start_page, s.end_page + 1)}
    assert not (set(range(5, 11)) & covered), (
        "pages the document placed elsewhere were absorbed by a neighbouring "
        "section instead of being left unresolved"
    )
    # The backward LABEL is still not trusted as a section.
    assert not any(s.family == "EXECUTIVE SUMMARY" for s in spans)


def test_pages_after_a_backward_entry_survive_an_approved_omission():
    """B-1, at the layer that matters: the pages must still be KEPT.

    Codex asked for exactly this — an approved, offerable preceding family, and
    an assertion that every page from the backward destination onward stays KEEP.
    Span geometry is the mechanism; a page surviving is the guarantee, and only
    the second is what an expert is cross-examined about.
    """
    spans = spans_from_outline(
        [("PROGRESS PHOTOGRAPHS", 0), ("PROCUREMENT", 10), ("EXECUTIVE SUMMARY", 4)],
        12,
    )
    doc = document(*[f"page {n}" for n in range(1, 13)])
    approval = ApprovedOmission(
        family_id="progress-photographs",
        approved_by="abachowski",
        approved_at="2026-08-17T12:00:00Z",
        matter="Matter-B",
        matter_root=matter_key("Matter-B"),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )
    out = apply_sections(
        doc, spans, template=PROGRESS_REPORT, approvals=(approval,),
        matter_root="Matter-B",
    )
    dropped = {p.page_no for p in out.documents[0].pages
               if p.disposition is Disposition.DROP}
    assert dropped == {1, 2, 3, 4}, (
        "an approved photographs omission removed pages the document placed "
        f"under a later section: dropped {sorted(dropped)}"
    )


def test_an_approval_does_not_carry_to_another_matter():
    """B-2. `ApprovedOmission` recorded its matter and nothing compared it.

    An approval stamped `Matter-A` dropped a page from `Matter-B`, and the drop
    log recorded `Matter-A` — a record that is complete and proves the opposite
    of authorization. Fail-closed: the approval is discarded, the page keeps, and
    the run says which ruling did not apply.
    """
    spans = (SectionSpan("PROGRESS PHOTOGRAPHS", "PROGRESS PHOTOGRAPHS",
                         RecognitionTier.OUTLINE, 1, 1, "the outline"),)
    doc = document("x")
    stale = ApprovedOmission(
        family_id="progress-photographs", approved_by="abachowski",
        approved_at="2026-01-01T00:00:00Z", matter="Matter-A",
        matter_root=matter_key("C:/Client-A/Production"),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )
    out = apply_sections(doc, spans, template=PROGRESS_REPORT,
                         approvals=(stale,),
                         matter_root="D:/Client-B/Production")
    assert out.drops == ()
    assert all(p.disposition is Disposition.KEEP for p in out.documents[0].pages)
    # Lower-cased: `matter_key` normcases, because a Windows path differs in
    # case and not in meaning.
    lowered = [w.lower() for w in out.warnings]
    assert any("client-a" in w and "client-b" in w for w in lowered), (
        "the mismatch was not reported to the operator"
    )


def test_an_approval_does_not_carry_across_template_versions():
    """B-2, the sibling. A template version can change what a family matches, so
    an approval given against one is not an approval of the other."""
    spans = (SectionSpan("PROGRESS PHOTOGRAPHS", "PROGRESS PHOTOGRAPHS",
                         RecognitionTier.OUTLINE, 1, 1, "the outline"),)
    doc = document("x")
    root = "D:/Client-B/Production"
    for field, value in (("template_id", "some-other-template"),
                         ("template_version", "0")):
        kw = dict(
            family_id="progress-photographs", approved_by="abachowski",
            approved_at="2026-08-17T00:00:00Z", matter="Matter-B",
            matter_root=matter_key("D:/Client-B/Production"),
            template_id=PROGRESS_REPORT.template_id,
            template_version=PROGRESS_REPORT.version,
        )
        kw[field] = value
        out = apply_sections(doc, spans, template=PROGRESS_REPORT,
                             approvals=(ApprovedOmission(**kw),),
                             matter_root=root)
        assert out.drops == (), f"a mismatched {field} still dropped a page"
        assert out.warnings, f"a mismatched {field} was not reported"


def test_approvals_without_a_matter_are_refused_rather_than_defaulted():
    """B-2's bypass. A defaulted matter would silently skip the scope check, so
    supplying approvals without one raises instead of comparing against ""."""
    spans = (SectionSpan("PROGRESS PHOTOGRAPHS", "PROGRESS PHOTOGRAPHS",
                         RecognitionTier.OUTLINE, 1, 1, "the outline"),)
    doc = document("x")
    approval = ApprovedOmission(
        family_id="progress-photographs", approved_by="abachowski",
        approved_at="2026-08-17T00:00:00Z", matter="Matter-B",
        matter_root=matter_key("D:/Client-B/Production"),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )
    with pytest.raises(TemplateError) as exc:
        apply_sections(doc, spans, template=PROGRESS_REPORT, approvals=(approval,))
    assert "matter" in str(exc.value)


def test_two_matters_of_the_same_name_are_not_the_same_matter():
    """Codex r2, B-2. Scope was keyed on the folder's NAME.

    `C:/Client-A/Production` and `D:/Client-B/Production` are one string, so the
    first client's approval survived the matter change and dropped the second
    client's pages. **The error was deriving a SCOPE KEY from a DISPLAY
    STRING** — one answers "what should an expert read", the other "are these
    the same matter", and only the second is an identity.

    Both directions are asserted. A scope check that refuses everything would
    pass the first half and be useless, so the same approval is then applied on
    the folder it was actually given on and must still drop.
    """
    spans = (SectionSpan("PROGRESS PHOTOGRAPHS", "PROGRESS PHOTOGRAPHS",
                         RecognitionTier.OUTLINE, 1, 1, "the outline"),)
    doc = document("x")
    a_root, b_root = "C:/Client-A/Production", "D:/Client-B/Production"
    from pathlib import Path as _P
    assert _P(a_root).name == _P(b_root).name, "the fixture must actually collide"

    approval = ApprovedOmission(
        family_id="progress-photographs", approved_by="abachowski",
        approved_at="2026-08-17T00:00:00Z", matter=_P(a_root).name,
        matter_root=matter_key(a_root),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )
    out = apply_sections(doc, spans, template=PROGRESS_REPORT,
                         approvals=(approval,), matter_root=b_root)
    assert out.drops == (), (
        "an approval given on one client's Production folder dropped a page "
        "from another client's Production folder"
    )
    assert all(p.disposition is Disposition.KEEP for p in out.documents[0].pages)
    assert out.warnings

    same = apply_sections(doc, spans, template=PROGRESS_REPORT,
                          approvals=(approval,), matter_root=a_root)
    assert len(same.drops) == 1, (
        "the approval stopped applying on the folder it was given on — a scope "
        "check that refuses everything is not a scope check"
    )
