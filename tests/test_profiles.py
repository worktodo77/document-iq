"""Format profiles: schema and detection (§6) — and the Stage-4 guarantees that
outlived the engine they were written against.

**D-35 ruled the Stage-4 engine replaced rather than repaired, and commit
4092f76 deleted `dociq/profiles/apply.py`.** A profile no longer decides a
disposition. What a profile still *is* — a schema an expert authors, a content
hash, a matter copy, a record in the run's log — is untouched by that ruling,
and the schema and detection groups below test exactly what they always did.

The KEEP/DROP guarantees were not withdrawn with the engine; they moved to
:func:`dociq.sections.apply.apply_sections`, so the third group is re-pointed
there rather than deleted. KEEP is still the default, every drop is still
attributed, the page accounting still holds, a document nothing covers still
passes through unchanged, and repeated application is still stable — the same
FIVE properties, now asserted against spans, a template and an expert's approval
instead of against header patterns and heading regexes.

*(This said "four" and listed four, omitting the pass-through. Counted, because
the sprint's own headline defect was a table of five reproductions under a
sentence that said four.)*

Two guarantees in that group **were** genuinely withdrawn, both of them about a
profile *claiming a document by its header patterns*. Neither is deleted
silently: each is re-pointed at the strictly stronger successor guarantee and
says in its own docstring what was withdrawn, by which commit, and why.
"""

from __future__ import annotations

import inspect

import pytest

from dociq.contracts import ContractViolation, Disposition, RecognitionTier
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
from dociq.sections.apply import apply_sections
from dociq.sections.model import (
    ApprovedOmission,
    Risk,
    SectionFamily,
    SectionTemplate,
)
from dociq.sections.tier1_outline import spans_from_outline
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


# The same MPR shape as `mpr_profile`, expressed the way Stage 4 expresses it
# now: an outline the document itself carries, a template that names families,
# and one approval per family an expert actually engaged. Deliberately parallel
# to `mpr_profile` so the re-pointed tests below assert the same outcome on the
# same fixture — pages 4, 5 and 6 of `MPR_PAGES` drop and nothing else does.

MPR_OUTLINE = [
    ("2 EXECUTIVE SUMMARY", 1),
    ("3 SCHEDULE STATUS", 2),
    ("4 HSE STATISTICS", 3),
    ("5 PHOTO LOG", 4),
    ("6 ORGANISATION CHART", 5),
]
"""``(title, page0)`` as PyMuPDF's ``get_toc(simple=True)`` gives it. Page 1 of
``MPR_PAGES`` is the cover and no entry claims it, so it belongs to nobody."""

MPR_TEMPLATE = SectionTemplate(
    template_id="mpr-under-test",
    version="1",
    display_name="Monthly progress report",
    families=(
        SectionFamily(
            family_id="hse-stats",
            display_name="HSE statistics",
            patterns=(r"^HSE STATISTICS",),
            risk=Risk.LOW,
            rationale=(
                "Incident counts and man-hours. Carries no delay evidence and "
                "no narrative an expert would quote."
            ),
        ),
        SectionFamily(
            family_id="photo-log",
            display_name="Photo log",
            patterns=(r"^PHOTO LOG",),
            risk=Risk.HIGH,
            rationale=(
                "Image captions only, but often the only proof of site "
                "condition on a date. A page-count saving, not a token saving."
            ),
        ),
        SectionFamily(
            family_id="org-chart",
            display_name="Organisation chart",
            patterns=(r"^ORGANISATION CHART",),
            risk=Risk.LOW,
            rationale=(
                "Static org data repeated verbatim every month; the same names "
                "survive in the correspondence the chart summarises."
            ),
        ),
    ),
)

MPR_APPROVALS = tuple(
    ApprovedOmission(
        family_id=family_id,
        approved_by="abachowski",
        approved_at="2026-08-17T12:00:00Z",
        matter="MODEC-4412",
        template_id="mpr-under-test",
        template_version="1",
    )
    for family_id in ("hse-stats", "photo-log", "org-chart")
)


def mpr_spans(page_count: int = len(MPR_PAGES)):
    return spans_from_outline(MPR_OUTLINE, page_count)


def applied(doc=None):
    """Stage 4 over one MPR document, fully engaged."""
    return apply_sections(
        doc if doc is not None else corpus(1)[0],
        mpr_spans(),
        template=MPR_TEMPLATE,
        approvals=MPR_APPROVALS,
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


# --- apply: the Stage-4 guarantees, at the engine that holds them now --------
#
# Every test in this group used to run through `profiles.apply.apply_profile` /
# `apply_profiles`, deleted by commit 4092f76 under D-35. The guarantees are not
# deleted with it: each one below names what it used to assert and against what
# it asserts it now.


def test_keep_is_the_default_for_unmatched_pages():
    """Re-pointed to :func:`~dociq.sections.apply.apply_sections` (D-35, 4092f76).

    The guarantee is unchanged and is the first rule of the new engine: KEEP is
    the default and needs no justification. What changed is only how many roads
    lead to it. The old engine had one — no rule matched the page. This one has
    three, and two are exercised here: no span covers the page at all, and a
    span covers it but matches no family the template knows. (The third, a
    family with no approval, is D-34's and is pinned in `test_sections.py`.)
    """
    out = applied().documents[0]

    assert out.pages[0].disposition is Disposition.KEEP  # cover page, no span
    assert out.pages[0].section is None, "no span reached it, so no section"

    assert out.pages[1].disposition is Disposition.KEEP  # no family matches it
    assert out.pages[1].section == "2 EXECUTIVE SUMMARY", (
        "recognized and kept — the two are independent, and a page the log "
        "calls a section is not thereby a page the log calls dropped"
    )
    assert out.pages[1].section_tier is RecognitionTier.OUTLINE


def test_every_drop_is_attributed():
    """Re-pointed to :func:`~dociq.sections.apply.apply_sections` (D-35, 4092f76).

    The old form asserted a rule id and its ``rule_notes`` on every drop entry.
    The attribution a drop now carries is strictly larger — D-34 requires the
    *person*, and A-18 requires the *tier* — so the successor assertion is
    stronger rather than merely different: no page drops without a family id, a
    named approver, the matter he approved it on, and the kind of evidence that
    placed the page.
    """
    result = applied()
    out = result.documents[0]

    dropped = [p for p in out.pages if p.disposition is Disposition.DROP]
    assert {p.page_no for p in dropped} == {4, 5, 6}
    assert all(p.drop_rule for p in dropped)
    assert all(p.section_tier is RecognitionTier.OUTLINE for p in dropped)

    assert {e.family_id for e in result.drops} == {
        "hse-stats", "photo-log", "org-chart"
    }
    assert all(e.drop_rule == f"mpr-under-test:{e.family_id}" for e in result.drops)
    assert all(e.approved_by == "abachowski" for e in result.drops)
    assert all(e.approved_at and e.matter == "MODEC-4412" for e in result.drops)
    assert all(e.evidence for e in result.drops), (
        "§5.4 — an expert defending the omission has to be able to say what "
        "was read to place the page"
    )


def test_accounting_holds_after_a_drop():
    """Re-pointed to :func:`~dociq.sections.apply.apply_sections` (D-35, 4092f76).
    Unchanged guarantee: the contract's own page arithmetic survives Stage 4."""
    out = applied().documents[0]
    out.validate()
    assert out.pages_in == out.pages_kept + out.pages_dropped == 6


def test_a_document_no_section_covers_passes_through_whole():
    """Re-pointed from ``test_document_no_profile_claims_passes_through_whole``
    (D-35, 4092f76).

    The antecedent changed — no profile claims anything now — but the guarantee
    did not: a document Stage 4 recognizes nothing in comes back **identical**,
    not merely equal, and contributes no drop-log entry.

    ``ProfileApplyResult.profiled_doc_ids`` has no successor field and none is
    asserted here. It recorded which documents a profile claimed, and with no
    claiming left there is nothing for it to hold; the run records
    ``section_template_id``/``section_template_version`` instead (A-19), which
    is a fact about the run rather than about which profile won a document.
    """
    doc = document("misc/letter.pdf", (page(1, "Dear Sir"), page(2, "PHOTO LOG")))
    result = apply_sections(doc, (), template=MPR_TEMPLATE, approvals=MPR_APPROVALS)
    assert result.documents[0] is doc
    assert result.drops == ()


def test_a_profile_can_no_longer_claim_a_document_at_any_page():
    """**Withdrawn guarantee, restated as the stronger one that replaced it.**

    Was ``test_header_match_is_limited_to_the_opening_pages``: a profile's
    ``header_patterns`` claimed a document, and the guarantee was that they were
    consulted over the *opening* pages only, so a ``MONTHLY PROGRESS REPORT``
    line on page 6 could not hand the whole document to the MPR profile.

    D-35 withdrew the behaviour that guarantee bounded. Commit 4092f76 deleted
    `dociq/profiles/apply.py`, which was the only caller of
    :meth:`~dociq.profiles.model.FormatProfile.applies_to`, so there is no
    claiming left anywhere to bound to any page range. The successor guarantee
    is the stronger one and is what this asserts.

    Asserted structurally as well as by example, because an example can only
    show that one document was not claimed. The function that decides
    dispositions takes spans, a template and approvals; there is no parameter
    through which a profile could reach it, which is the same
    correct-by-construction shape as ``SectionTemplate`` having no field a
    disposition could be written into.
    """
    params = set(inspect.signature(apply_sections).parameters)
    assert not params & {"profile", "profiles", "header_patterns"}
    assert {"spans", "template", "approvals"} <= params

    # And by example, from both sides of the boundary the old rule drew: the
    # header phrase on the opening page and on the deep page alike.
    for header_page in (1, 6):
        pages = tuple(
            page(n, "MONTHLY PROGRESS REPORT" if n == header_page else "filler")
            for n in range(1, 7)
        )
        doc = document(f"misc/letter-{header_page}.pdf", pages)
        result = apply_sections(
            doc, (), template=MPR_TEMPLATE, approvals=MPR_APPROVALS
        )
        assert result.drops == (), f"header on page {header_page} dropped pages"


def test_a_profile_whose_drop_rules_are_now_inert_is_reported():
    """**Withdrawn guarantee, re-pointed at its successor.**

    Was ``test_competing_profiles_are_reported_not_silently_resolved``:
    ``apply_profiles`` claimed each document with the first profile whose header
    patterns matched, and warned when more than one claimed it. Commit 4092f76
    deleted that function, so there is no claimant and no competition to report.

    The guarantee that survives is the general one that test was an instance of
    — **an input the run was given and did not act on is reported, not silently
    ignored** — and its successor is
    :func:`dociq.pipeline._inert_profile_warnings`, shipped by the same commit.
    An expert who authored DROP rules and watched a run keep every page is
    exactly the operator the old warning existed for.

    Covered here rather than in ``test_pipeline.py`` because it is a fact about
    a profile, and because 4092f76 shipped the function with no test at all.
    """
    from dociq.pipeline import _inert_profile_warnings

    warnings = _inert_profile_warnings((mpr_profile(),))
    assert len(warnings) == 1
    assert "modec-mpr" in warnings[0]
    for rule_id in ("hse-stats", "photo-log", "org-chart"):
        assert rule_id in warnings[0], f"{rule_id} is not named in the warning"
    assert "removed" in warnings[0], (
        "the operator has to be told the engine went away, not merely that "
        "nothing dropped"
    )

    keep_only = FormatProfile(
        profile_id="keep-only", version="1", display_name="keep only",
        section_rules=(SectionRule("sched", r"^SCHEDULE STATUS"),),
    )
    assert _inert_profile_warnings((keep_only,)) == [], (
        "a profile with no DROP rules lost nothing and must not be warned "
        "about — a warning nobody needs is how a real one gets ignored"
    )


def test_nothing_in_the_pipeline_consults_a_profile_to_decide_a_disposition():
    """The withdrawal of D-35's behaviour, made hard to undo by accident.

    :meth:`~dociq.profiles.model.FormatProfile.applies_to` survived commit
    4092f76 with **zero callers** — the module that called it was the one the
    commit deleted. Dead code shaped exactly like the deleted engine's entry
    point is a trap: the next person who needs "which profile looks like this
    document" has a method sitting there that answers it, and wiring its answer
    into a disposition re-creates the defect rather than a convenience.

    So the guarantee is asserted where it can actually be violated — over the
    source tree — rather than over one call. Scanned with :mod:`ast` rather than
    by substring, so a mention in a docstring or a comment does not fire it.

    **Two corrections from the adversarial review, and the first is the reason
    this docstring is longer than the test.** The check matched only an
    ``ast.Call`` on an ``ast.Attribute``, while claiming a call reached through
    an alias still fired it. It did not: ``claims = profile.applies_to`` on one
    line and ``claims(sample)`` on the next was measured GREEN, and so was
    ``getattr(profile, "applies_to")(sample)`` — which is the exact re-wiring
    this exists to forbid, written across two lines instead of one. It now
    matches any ATTRIBUTE ACCESS of the name plus the ``getattr`` spelling,
    which cannot be split that way.

    **And it passed over an empty scan.** Nothing asserted the walk visited any
    file, so moving ``src/dociq`` — or renaming the tests directory — reported
    success while guarding nothing. Measured at ``files_scanned=0, offenders=[],
    assert passes: True``. A probe that cannot tell "nothing is wrong" from "I
    looked nowhere" is the vacuous-probe failure this project keeps finding in
    its own tests.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "dociq"
    offenders = []
    scanned = 0
    for path in sorted(src.rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            hit = (
                # `profile.applies_to`, whether called here or bound and called
                # later. Attribute ACCESS, not Call: binding then calling is the
                # split this check used to be blind to.
                (isinstance(node, ast.Attribute) and node.attr == "applies_to")
                # getattr(profile, "applies_to") — reaches the same method
                # without ever writing it as an attribute.
                or (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "applies_to"
                )
            )
            if hit:
                offenders.append(f"{path.relative_to(src).as_posix()}:{node.lineno}")
    assert scanned > 20, (
        f"the scan visited {scanned} file(s) under {src} — it is reporting "
        "success without having looked, which is the one result this probe "
        "must never be able to give"
    )
    assert offenders == [], (
        "a profile's header patterns are being consulted at "
        + ", ".join(offenders)
        + " — D-35 removed profile-based document claiming, and a disposition "
        "decided this way is the defect that dropped four HIGH-risk sections "
        "and attributed them to a fifth"
    )


def test_a_drop_without_a_rule_cannot_be_constructed():
    """The contract enforces attribution independently of any engine.

    Unchanged by D-35, and that is the point of it: it was written to hold
    whether or not the Stage-4 engine of the day was correct, and the engine of
    the day has since been replaced entirely. A-18 added a second rule of the
    same kind (a DROP must also carry a ``section_tier``); this one still fires
    first, so the assertion below is the same one it always was.
    """
    doc = corpus(1)[0]
    with pytest.raises(ContractViolation, match="DROP without a drop_rule"):
        doc.pages[0].evolve(disposition=Disposition.DROP).validate()


def test_apply_is_stable_over_repeated_runs():
    """Re-pointed to :func:`~dociq.sections.apply.apply_sections` (D-35, 4092f76).
    Unchanged guarantee: Stage 4 is a pure function of its inputs."""
    docs = corpus(3)
    first = [applied(doc) for doc in docs]
    for _ in range(8):
        assert [applied(doc) for doc in docs] == first
