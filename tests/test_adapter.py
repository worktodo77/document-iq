"""The real pipeline under the GUI — :mod:`dociq.adapter`.

Every assertion here is about the seam holding while the thing behind it becomes
real: the same presentation records, the same types, no new knowledge on the GUI
side, and — the part that is easy to lose — no number on screen that the run did
not produce.

The mock stays installable throughout (``set_pipeline``), and
``tests/test_gui_states.py`` still runs against it. That is deliberate: a seam
proven by only one implementation is not proven.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from dociq import adapter
from dociq.contracts import Disposition, RunConfig
from dociq.gui.pipeline import (
    DIRECT_CONTEXT_TOKENS,
    LEVER_AUTOMATIC,
    LEVER_EXPERT,
    LEVER_RECOGNIZED,
    ProfileInfo,
    ReductionPlan,
    RunRequest,
    get_pipeline,
    set_pipeline,
)
from dociq.profiles.model import Disposition as _D  # noqa: F401  (re-export check)
from dociq.profiles.model import (
    FormatProfile,
    ProfileError,
    SectionRule,
    dump_profile,
)
from dociq.runstate import TerminalStatus
from dociq.sections.model import ApprovedOmission
from dociq.sections.templates import PROGRESS_REPORT

from .conftest import FIXTURES

# The fixture corpus's native PDF opens "MONTHLY PROGRESS REPORT" and its second
# page opens "2. PROGRESS NARRATIVE" — a real header and a real section, so the
# profile below drops a real page rather than a contrived one.
MPR = FormatProfile(
    profile_id="mpr-test",
    version="1.0",
    display_name="Monthly progress report (test)",
    header_patterns=("MONTHLY PROGRESS REPORT",),
    section_rules=(
        SectionRule("mpr.narrative", r"PROGRESS NARRATIVE",
                    disposition=Disposition.DROP, label="Progress narrative",
                    notes="dropped for the test; approved by nobody, which is "
                          "why this profile lives in a test and not in the app"),
        SectionRule("mpr.cover", r"MONTHLY PROGRESS REPORT",
                    disposition=Disposition.KEEP, label="Cover"),
    ),
)


@pytest.fixture
def library(tmp_path, monkeypatch):
    """An empty profile library, pointed away from the real ``%APPDATA%`` one.

    The environment override rather than the constructor argument, because it is
    the path a shipped run takes (D-05) and because it also covers the code that
    resolves the library a second time when a chosen profile is loaded.
    """
    lib = tmp_path / "profiles"
    monkeypatch.setenv("DOCIQ_PROFILE_LIBRARY", str(lib))
    return lib


def _write(library: Path, profile: FormatProfile) -> Path:
    library.mkdir(parents=True, exist_ok=True)
    path = library / f"{profile.profile_id}.v{profile.version}.yaml"
    path.write_text(dump_profile(profile.stamped()), encoding="utf-8", newline="\n")
    return path


def _request(tmp_path, name="out", profile=None, index=None,
             approvals=()) -> RunRequest:
    return RunRequest(str(FIXTURES), str(tmp_path / name),
                      profile=profile, master_index_path=index,
                      approvals=tuple(approvals))


# The one family the fixture corpus actually exercises. `_scratch`-free and
# measured rather than assumed: a run over tests/fixtures recognizes exactly two
# sections — "Blank page" (blank-page) and "Photograph / figure page"
# (progress-photographs) — both by Tier 3, both offered. Approving this one is
# what turns 0 dropped pages into 3.
FIXTURE_FAMILY = "progress-photographs"


def _approval(family_id: str = FIXTURE_FAMILY,
              approved_by: str = "j.long") -> ApprovedOmission:
    """One expert-approved omission, built directly (D-34).

    Used where the test is about ``_plan``'s arithmetic rather than about the
    capture point. Where the capture point IS the subject, the approval comes
    from :meth:`RealPipeline.set_omission` instead, so ``approved_by`` is the
    machine's reading of who is running the tool and not a string a test wrote.
    """
    return ApprovedOmission(
        family_id=family_id,
        approved_by=approved_by,
        approved_at="2026-08-17T00:00:00Z",
        matter="the test matter",
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )


def _run(pipe, request, cancel=lambda: False):
    events = []
    outcome = pipe.run(request, events.append, cancel)
    return outcome, events


# ---------------------------------------------------------------------------
# D-1 — profiles()
# ---------------------------------------------------------------------------


def test_an_empty_library_still_offers_a_choice(library):
    """FAIL-BEFORE: returning the library verbatim gives ``()`` on a machine
    that has never profiled a format — which is every machine on first launch —
    and an empty picker is indistinguishable from a broken one."""
    assert not library.exists()
    offered = adapter.RealPipeline().profiles()
    assert offered == (adapter.NO_PROFILE,)
    assert offered[-1].section_rules == 0


def test_a_missing_library_is_not_an_error(library, tmp_path):
    """The library may be a shared LI drive that is not reachable (D-05). That
    must not stop a matter from being run without a profile."""
    pipe = adapter.RealPipeline(library_dir=tmp_path / "nowhere" / "at" / "all")
    assert pipe.profiles() == (adapter.NO_PROFILE,)


def test_the_rule_count_is_the_real_one(library):
    """``section_rules`` is what the profile carries, not a placeholder.

    **Its stated purpose is withdrawn.** This read "the operator reads it to see
    whether a profile will remove anything" — false since D-35: a profile
    removes nothing, and that number was precisely what disabled the §6 approve
    button while it was still being compared against the template's families.
    The count is still worth asserting as a faithful reading of the file, which
    is all it now claims to be.
    """
    _write(library, MPR)
    offered = adapter.RealPipeline().profiles()
    real = [p for p in offered if p.profile_id == "mpr-test"]
    assert len(real) == 1
    assert real[0].section_rules == 2, "the rule count is not the profile's"
    assert real[0].version == "1.0"
    assert real[0].label == "Monthly progress report (test)"
    assert offered[-1] is adapter.NO_PROFILE, "the no-profile choice moved"


def test_an_unreadable_profile_is_skipped_and_recorded(library):
    """A broken YAML file must not take the picker down with it — and must not
    vanish silently either."""
    _write(library, MPR)
    (library / "broken.v1.yaml").write_text("profile_id: [\n", encoding="utf-8")

    pipe = adapter.RealPipeline()
    offered = pipe.profiles()
    assert [p.profile_id for p in offered] == ["mpr-test", "none"]
    assert len(pipe.library_issues) == 1
    assert "broken.v1.yaml" in pipe.library_issues[0]


def test_the_offered_order_is_deterministic(library):
    for pid in ("zeta", "alpha", "middle"):
        _write(library, FormatProfile(profile_id=pid, version="1.0",
                                      display_name=pid.title()))
    first = adapter.RealPipeline().profiles()
    second = adapter.RealPipeline().profiles()
    assert first == second
    assert [p.profile_id for p in first] == ["alpha", "middle", "zeta", "none"]


# ---------------------------------------------------------------------------
# A-11's hook — §6's approval checklist
#
# REPOINTED AT D-35 / D-34 (commits 4092f76, d3cee24). Every test in this block
# used to assert that ``profile_rules`` rendered THE CHOSEN PROFILE'S OWN
# SECTION RULES: one row per rule, a DROP rule arriving engaged, a KEEP rule
# arriving off. That guarantee is withdrawn at its root — D-35 deleted
# ``dociq.profiles.apply``, so a profile's DROP rule now drops nothing, and
# drawing one beside the word DROP would state a falsehood on the one screen
# whose entire purpose is an expert approving omissions before a run commits.
#
# What replaced it, and what these five tests now pin:
#
#   * the rows are the loaded SECTION TEMPLATE's families, not the profile's
#     rules — ``profile`` is accepted and decides nothing;
#   * every row arrives OFF and names no approver (D-34: a template ships
#     unengaged and can never approve anything);
#   * a family the template refuses to offer is a LOCKED row, one screen
#     earlier than the summary waterfall and by the same rule;
#   * every row carries its risk, its stated cost and its own patterns, which
#     is A-11b's guarantee surviving the change of source.
# ---------------------------------------------------------------------------


def test_the_checklist_lists_every_family_the_template_carries(library):
    """REPOINTED from ``test_the_checklist_hook_lists_every_rule_the_profile_carries``.

    A-11 is APPLIED (``docs/contracts/amendments.md``, 2026-08-01) and
    ``profile_rules`` is on ``PipelineAPI``; Track E still reaches it through the
    optional ``getattr`` hook, because the loud empty state is the right
    behaviour for a stand-in that cannot supply the rows. Absent is not the
    expected case, and it is not the case here.

    The withdrawn half is the SOURCE of the rows. Where this once asserted
    ``len(levers) == len(MPR.section_rules)`` and read a DROP rule as an engaged
    lever, it now asserts the template's families, all of them, all off.
    """
    _write(library, MPR)
    pipe = adapter.RealPipeline()
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]

    levers, basis, source = pipe.profile_rules(chosen)

    assert [le.key for le in levers] == [
        f.family_id for f in PROGRESS_REPORT.families
    ], "the checklist is not the template's families, in the template's order"

    # D-34, and the reason this replaced "a DROP rule reads KEEP": there is no
    # longer any input to this call that could make a row arrive engaged.
    assert not any(le.engaged for le in levers), (
        "a row arrived ON. A template ships unengaged; an omission exists only "
        "once a person has approved it"
    )
    assert not any(le.approved_by for le in levers), (
        "the approver field holds a fiction — the exact failure D-34 was ruled "
        "to prevent"
    )

    offered = {f.family_id for f in PROGRESS_REPORT.families if f.offer}
    assert offered, "the shipped template offers nothing; the probe is vacuous"
    assert {le.key for le in levers if le.kind == LEVER_EXPERT} == offered
    assert {le.key for le in levers if le.kind == LEVER_RECOGNIZED} == (
        {f.family_id for f in PROGRESS_REPORT.families} - offered
    ), "a family the template refuses to offer is drawn as engageable"
    # NOT `all(le.locked for ... if kind == LEVER_RECOGNIZED)`, which was here
    # and is a tautology: `locked` is DEFINED as `kind != LEVER_EXPERT`, so it
    # restates the filter. What is worth asserting is that the model refuses to
    # move such a row — the property a screen depends on and a definition cannot
    # give.
    plan = ReductionPlan(full_tokens=1000, levers=tuple(levers))
    for row in (le for le in levers if le.kind == LEVER_RECOGNIZED):
        assert plan.with_toggled(row.key) == plan, (
            f"{row.key}: a recognized-never-offered row moved when toggled"
        )

    # §5.3 / A-11b: every row says what it costs and what it catches.
    assert all(le.risk for le in levers), "a row was drawn without its risk grade"
    assert all(le.note for le in levers), "a lever offered without a stated cost"
    assert all(le.rule for le in levers), "a row says a drop exists, not what it catches"

    # Before a run there is nothing counted and no tier has placed anything.
    assert all(le.estimated for le in levers), (
        "a row with no measurement behind it was presented as counted"
    )
    assert all(le.tokens == 0 and le.pages == 0 for le in levers)
    assert all(le.tier == "" for le in levers), (
        "a tier before a run — which tier places a section is a property of "
        "documents, and none have been read"
    )

    assert f"{PROGRESS_REPORT.template_id} v{PROGRESS_REPORT.version}" in source
    assert "no omission is approved" in source
    assert "No pages have been read" in source
    assert basis.provenance == ""


def test_the_no_profile_choice_gets_the_same_checklist(library):
    """REPOINTED from ``test_the_checklist_hook_says_nothing_for_the_no_profile_choice``.

    **The withdrawn claim.** Choosing :data:`adapter.NO_PROFILE` used to produce
    ``levers == ()`` and ``source == ""``, and that was correct while the rows
    were the profile's rules: NO_PROFILE has none. D-35 makes the rows the
    template's, and the template loads whatever the operator picked — so "no
    profile" no longer means "nothing is offered".

    That is not a loss, and this asserts why: what protected the expert was
    never the empty list, it was that nothing is ENGAGED and nobody is named.
    That half is unchanged and is now checked on the choice most likely to be
    read as "this run drops nothing".
    """
    levers, _basis, source = adapter.RealPipeline().profile_rules(
        adapter.NO_PROFILE)

    assert [le.key for le in levers] == [
        f.family_id for f in PROGRESS_REPORT.families
    ], "the no-profile choice gets a different checklist from every other choice"
    assert not any(le.engaged for le in levers)
    assert not any(le.approved_by for le in levers)
    assert PROGRESS_REPORT.template_id in source, (
        "the screen names no source for rows it is asking an expert to approve"
    )


def test_two_families_never_collapse_into_one_row(library):
    """REPOINTED from ``test_two_rules_with_one_label_stay_two_rows``.

    The hazard is unchanged and is worth restating: two rows an expert cannot
    tell apart are two omissions he cannot tell apart, and the checklist keys
    its toggle on the row's key. What changed is where the guarantee comes from.
    A profile could carry two rules sharing a label — ``FormatProfile.validate``
    enforces uniqueness on ``rule_id`` only — so the old test built one and
    checked the adapter kept the rows distinct. A template cannot:
    :meth:`SectionTemplate.validate` refuses a duplicate ``family_id`` outright,
    and the row key IS the ``family_id``.

    So this asserts the property on the SHIPPED template rather than on a
    profile constructed to break it, and it asserts the display names too —
    those are unenforced by ``validate`` and are the string an expert actually
    reads.
    """
    _write(library, MPR)
    pipe = adapter.RealPipeline()
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    levers, _b, _s = pipe.profile_rules(chosen)

    keys = [le.key for le in levers]
    assert len(set(keys)) == len(keys), (
        f"two rows share a key, so one toggle would move both: {keys}")
    labels = [le.label for le in levers]
    assert len(set(labels)) == len(labels), (
        f"two rows read the same words — two omissions an expert cannot tell "
        f"apart: {sorted(labels)}")

    # And the structural half, stated where it lives: a template that tried to
    # ship a duplicate family would not load at all.
    from dociq.sections.model import SectionTemplate, TemplateError

    clashing = SectionTemplate(
        template_id="clash", version="1", display_name="Clashing",
        families=(PROGRESS_REPORT.families[0], PROGRESS_REPORT.families[0]),
    )
    with pytest.raises(TemplateError):
        clashing.validate()


def test_a_vanished_profile_no_longer_empties_the_checklist(library):
    """REPOINTED from ``test_a_vanished_profile_does_not_silently_produce_an_empty_checklist``.

    **The withdrawn behaviour.** ``profile_rules`` used to re-read the profile
    file and raise :class:`ProfileError` when it was gone, so Track E's call
    site could render the loud empty state that disables approval; an empty list
    returned quietly would have read as "this profile drops nothing".

    D-35 removes the hazard rather than the guard. The rows come from a template
    compiled into the build, so a profile file vanishing between the picker and
    the checklist cannot empty it — there is no longer a state in which this
    screen is silently blank. The consequence is asserted here, and the
    remaining half of the old guarantee (a vanished profile must still not be
    RUN) is asserted by
    ``test_a_chosen_profile_that_vanished_stops_the_run``, which is unchanged
    and still passes.
    """
    _write(library, MPR)
    pipe = adapter.RealPipeline()
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    (library / "mpr-test.v1.0.yaml").unlink()

    levers, _basis, source = pipe.profile_rules(chosen)
    assert len(levers) == len(PROGRESS_REPORT.families) > 0, (
        "the checklist emptied when a profile file vanished — the state that "
        "used to read as 'this drops nothing'")
    assert PROGRESS_REPORT.template_id in source

    # The run is still refused. The two screens answer different questions and
    # only one of them depends on the file.
    with pytest.raises(ProfileError):
        pipe._profiles_for(_request(Path("."), profile=chosen))


def test_the_checklist_and_the_run_offer_one_template(library):
    """REPOINTED from ``test_the_checklist_and_the_run_read_one_file_through_one_parser``.

    The guarantee is the same sentence with a different noun: what an expert is
    offered on screen must be what the run can act on. It used to be enforced by
    both paths reading one YAML file through one parser. D-35 moves the decision
    off the profile entirely, so it is now enforced by both paths reading one
    template object — ``RealPipeline._template`` — and by the adapter refusing an
    approval for anything not on it.
    """
    _write(library, MPR)
    pipe = adapter.RealPipeline()
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    levers, _b, _s = pipe.profile_rules(chosen)

    # Same rows for the profile choice and for no choice at all: the template
    # is the source, so there is nothing for the two paths to disagree about.
    assert [le.key for le in levers] == [
        le.key for le in pipe.profile_rules(adapter.NO_PROFILE)[0]
    ]

    engageable = {le.key for le in levers if not le.locked}
    assert engageable == {f.family_id for f in PROGRESS_REPORT.families if f.offer}

    # Every row the screen says can be engaged, can be. Enumerated rather than
    # sampled: one family silently unapprovable is a lever that does nothing.
    for key in sorted(engageable):
        approval = pipe.set_omission(key, True, "the test matter")
        assert approval is not None and approval.family_id == key
        assert approval.template_id == PROGRESS_REPORT.template_id
        assert approval.template_version == PROGRESS_REPORT.version
        assert approval.approved_by, "an approval that names nobody (D-34)"

    # And every row it locks is refused at the layer a widget cannot reach past.
    for locked in sorted({le.key for le in levers} - engageable):
        with pytest.raises(ProfileError):
            pipe.set_omission(locked, True, "the test matter")
    with pytest.raises(ProfileError):
        pipe.set_omission("no-such-family", True, "the test matter")


# ---------------------------------------------------------------------------
# D-2 — preview_folder()
# ---------------------------------------------------------------------------


def test_the_preview_counts_what_the_run_will_count():
    """One traversal, not two: the count beside the action is the run's own."""
    from dociq.ingest import walker

    preview = adapter.RealPipeline().preview_folder(str(FIXTURES))
    assert preview.file_count == len(walker.list_files(Path(FIXTURES)))
    assert preview.total_bytes > 0
    exts = dict(preview.by_extension)
    assert exts[".pdf"] >= 4
    assert sum(exts.values()) == preview.file_count


def test_the_preview_ignores_docis_own_run_state(tmp_path):
    """FAIL-BEFORE: the preview walked the folder without the run's
    ``STATE_DIR`` filter, so an operator who puts the output folder inside the
    matter folder — which the setup screen permits — saw a previous run's
    journal and staging files counted as documents.

    Found by reading :func:`dociq.ingest.walker.scan` against the preview, not
    by a failing test; the predicate is now shared so the two cannot drift.
    """
    from dociq.ingest import walker

    source = tmp_path / "matter"
    (source / "sub").mkdir(parents=True)
    (source / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (source / "sub" / "b.docx").write_bytes(b"PK\x03\x04")
    state = source / "out" / walker.STATE_DIR / "staging" / "clean_text"
    state.mkdir(parents=True)
    (state / "LI-00001.txt").write_text("previous run\n", encoding="utf-8")
    (source / "out" / walker.STATE_DIR / "resume.jsonl").write_text(
        "{}\n", encoding="utf-8")

    preview = adapter.RealPipeline().preview_folder(str(source))
    assert preview.file_count == 2, dict(preview.by_extension)
    assert ".txt" not in dict(preview.by_extension)
    assert preview.file_count == len(
        [e for e in walker.scan(source)]), "the preview and the run disagree"


def test_a_folder_that_is_not_there_previews_as_nothing(tmp_path):
    preview = adapter.RealPipeline().preview_folder(str(tmp_path / "nope"))
    assert preview == type(preview)(0, 0)


def test_the_estimate_is_the_measured_rate_and_nothing_else():
    """FAIL-BEFORE: the mock's ``MINUTES_PER_GIGABYTE = 18`` is labelled
    ILLUSTRATIVE, and carrying it into the real adapter would put an invented
    number beside the operator's action.

    Each rate is arithmetic on one measured run, so this test is allowed to
    restate them — if either constant is ever changed without a new measurement,
    this is where the change has to be argued.
    """
    two_gb = 2_000_000_000
    on = adapter._minutes_for(two_gb, {".pdf": two_gb}, ocr_enabled=True)
    off = adapter._minutes_for(two_gb, {".pdf": two_gb}, ocr_enabled=False)
    assert on == round(2.0 * (6182.4 / 2.6) / 60) == 79
    assert off == round(2.0 * (3046.7 / 2.6) / 60) == 39


def test_the_default_rate_is_the_ocr_on_one_because_the_default_run_is():
    """The claim-accuracy defect this closes, stated as an assertion.

    ``MEASURED_SECONDS_PER_GB`` was derived from the OCR-DISABLED run while
    :class:`RealPipeline` constructs with ``ocr_enabled=True``, so the figure
    beside the primary action read ~51 min for a corpus whose one measured
    OCR-on run took 103. The rate and the run must be the same configuration.
    """
    two_gb = 2_000_000_000
    assert adapter._minutes_for(two_gb, {".pdf": two_gb}) == \
        adapter._minutes_for(two_gb, {".pdf": two_gb}, ocr_enabled=True)
    assert adapter.seconds_per_gb(True) > adapter.seconds_per_gb(False)
    # ≈2.0×, independently consistent with the register's ≈2.0–2.3× figure for
    # OCR's share of extraction. Asserted as a band, not a literal: this is a
    # cross-check between two runs, not a third measurement.
    ratio = adapter.seconds_per_gb(True) / adapter.seconds_per_gb(False)
    assert 1.9 <= ratio <= 2.4, ratio


@pytest.mark.parametrize("ocr", [True, False])
def test_the_previewed_estimate_follows_THIS_pipeline_s_ocr_setting(
    tmp_path, monkeypatch, ocr
):
    """Not merely that two rates exist — that ``preview_folder`` READS the one
    matching the run it is previewing.

    Asserted through ``preview_folder`` itself, on a folder whose bytes are
    faked to 1 GB, rather than by re-calling ``_minutes_for`` with the setting
    the test already knows. A test that calls the helper directly passes whether
    or not the caller is wired to it — which is the bug, not the fix.

    FAIL-BEFORE, watched RED: with ``ocr_enabled=self._ocr_enabled`` deleted from
    ``preview_folder``'s call, the ``ocr=False`` case reports 40 minutes instead
    of 20.
    """
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    one_gb = 1_000_000_000

    real_stat = Path.stat

    def fake_stat(self, *a, **k):
        st = real_stat(self, *a, **k)
        if self.suffix.lower() == ".pdf":
            class _S:
                st_size = one_gb
            return _S()
        return st

    monkeypatch.setattr(Path, "stat", fake_stat)

    preview = adapter.RealPipeline(ocr_enabled=ocr).preview_folder(str(tmp_path))
    assert preview.total_bytes == one_gb
    assert preview.estimated_minutes == round(
        1.0 * adapter.seconds_per_gb(ocr) / 60
    )
    assert preview.estimated_minutes == (40 if ocr else 20)


def test_the_shipped_default_is_ocr_on():
    """The whole premise of the fix: the figure beside the primary action has to
    describe the run the primary action starts."""
    assert adapter.RealPipeline()._ocr_enabled is True


def test_no_rate_constant_survives_that_hides_which_ocr_setting_it_timed():
    """CLASS assertion, not the repro.

    The defect was a module-level constant named as though it were the single
    measured rate. Any future constant of that shape reintroduces it, so the
    module must expose the two rates under names that say which is which, and
    must not re-export an unqualified one.
    """
    assert not hasattr(adapter, "MEASURED_SECONDS_PER_GB")
    assert not hasattr(adapter, "MEASURED_BASIS")
    assert not hasattr(adapter, "MEASURED_SECONDS")
    assert {"SECONDS_PER_GB_OCR_ON", "SECONDS_PER_GB_OCR_OFF",
            "seconds_per_gb", "measured_basis"} <= set(adapter.__all__)
    for name in adapter.__all__:
        assert "SECONDS_PER_GB" not in name or name.endswith(("_ON", "_OFF")), \
            f"{name} is a rate whose name does not say which OCR setting it timed"


def test_the_basis_sentence_names_the_run_it_came_from():
    """A rate on screen that cannot say which run produced it is a claim the
    operator would have to defend without support."""
    on, off = adapter.measured_basis(True), adapter.measured_basis(False)
    assert "6,182.4 s" in on and "OCR enabled" in on and "2026-08-02" in on
    assert "3,046.7 s" in off and "OCR disabled" in off and "2026-07-31" in off
    assert on != off


@pytest.mark.parametrize(
    "total, sized, why",
    [
        (0, {}, "an empty folder"),
        (1_000_000_000, {".msg": 1_000_000_000}, "formats no run has timed"),
        (1_000_000_000, {".pdf": 500_000_000, ".msg": 500_000_000},
         "a mix outside the measured shape"),
        (100_000_000_000, {".pdf": 100_000_000_000},
         "100 GB, forty times anything measured"),
    ],
)
def test_no_estimate_rather_than_an_indefensible_one(total, sized, why):
    """Zero is the seam's documented "no estimate" and the screen then says
    nothing. Each case is a folder the one measured run does not describe."""
    assert adapter._minutes_for(total, sized) == 0, why


def test_the_estimate_is_absent_for_the_fixture_corpus():
    """Honest consequence, asserted rather than hidden: the fixture corpus is a
    few megabytes, and a few megabytes round to no minutes. The screen says
    nothing about duration, which is better than "about 1 minute" from a rate
    measured on 2.6 GB."""
    assert adapter.RealPipeline().preview_folder(str(FIXTURES)).estimated_minutes == 0


# ---------------------------------------------------------------------------
# D-3 — run()
# ---------------------------------------------------------------------------


# ``real_run`` is now a SESSION fixture in ``tests/conftest.py``. It moved
# because the seam-population probe and the GUI's rendered-state tests must
# assert against the same real run this file does — a second, separately
# constructed run would let the two disagree about what the pipeline produced,
# which is exactly the disagreement B-3 was.


def test_a_real_run_publishes_and_says_so(real_run):
    outcome, _events, root = real_run
    assert outcome.published
    assert outcome.termination.complete
    assert outcome.result.terminal_status is TerminalStatus.COMPLETED
    assert outcome.output_root == str(root)
    assert (root / "sources.json").is_file()
    assert (root / "document_index.csv").is_file()
    assert (root / "processing_log.json").is_file()


def test_the_gui_is_handed_the_pipelines_own_figures(real_run):
    """Seam rule 2. The adapter READS ``RunResult.tokens_before`` — it does not
    estimate. A second estimator would put two numbers in one matter folder, one
    on screen and one in ``run_summary.pdf``."""
    outcome, _events, _root = real_run
    assert outcome.tokens_before.chars == outcome.result.tokens_before.chars
    assert (outcome.tokens_before.structural_tokens
            == outcome.result.tokens_before.structural_tokens)
    assert outcome.tokens_before.provenance == outcome.result.tokens_before.provenance
    assert outcome.tokens_after.structural_tokens <= \
        outcome.tokens_before.structural_tokens
    assert "ASSUMPTION A1" in outcome.tokens_before.provenance


def test_no_master_index_means_no_reconciliation(real_run):
    """``None``, not an empty report — the contract distinguishes them and so
    must the screen."""
    outcome, _events, _root = real_run
    assert outcome.reconciliation is None


def test_progress_speaks_plain_language_about_pages(real_run):
    _outcome, events, _root = real_run
    assert events, "a run reported no progress at all"
    read = [e for e in events if e.status.startswith("read ")]
    assert read, [e.status for e in events][:5]
    assert "pages" in read[-1].status
    assert all(e.total >= e.done for e in events)


def test_the_last_four_stages_report_themselves(real_run):
    """The acceptance run of 2026-08-02 measures Stages 1-2 at **99.70%** of the
    run — 18.5 s for everything after them. (This said 99.1%, the superseded
    2026-07-31 pair, in the present tense.) Without this, the progress screen
    goes quiet for everything after the walk and a long emit reads as a hang."""
    _outcome, events, _root = real_run
    statuses = [e.status for e in events]
    for step in (3, 4, 5, 6):
        assert any(s.startswith(f"Step {step} of 6") for s in statuses), (
            f"stage {step} never announced itself: {statuses[-8:]}"
        )
    assert not any(s.startswith("Step 1 of 6") or s.startswith("Step 2 of 6")
                   for s in statuses), (
        "the walk's per-file line was overwritten by a coarser stage line"
    )


def test_a_cancelled_run_publishes_nothing_and_hands_over_no_figures(tmp_path):
    """The numbers on the summary screen would otherwise describe the fraction
    of a folder that happened to be read before the operator pressed stop."""
    pipe = adapter.RealPipeline(ocr_enabled=False)
    request = _request(tmp_path, "matter")
    first, _ = _run(pipe, request)
    assert first.published
    root = tmp_path / "matter"
    before = {p.name for p in root.iterdir()}
    sources = (root / "sources.json").read_bytes()

    cancelled, events = _run(pipe, request, cancel=lambda: True)
    assert not cancelled.published
    assert not cancelled.termination.complete
    assert cancelled.termination.status is TerminalStatus.CANCELLED
    assert cancelled.plan is None
    assert cancelled.reconciliation is None
    assert cancelled.tokens_before.structural_tokens == 0
    assert cancelled.tokens_before.chars == 0
    assert "published nothing" in cancelled.tokens_before.provenance
    assert cancelled.tokens_before.high == 0, "a zero-chars figure divided badly"
    # The previous complete run is untouched, and the folder the summary screen
    # offers to open still holds a whole corpus — plus the cancelled run's own
    # record of itself, which is where an aborted run belongs.
    after = {p.name for p in root.iterdir()}
    assert after - before <= {"incomplete_run"}, after - before
    assert not before - after, "a cancelled run removed a deliverable"
    assert (root / "sources.json").read_bytes() == sources
    assert (root / "incomplete_run" / "run_status.json").is_file()


def test_the_adapter_refuses_figures_from_an_unpublished_run(tmp_path, monkeypatch):
    """The adapter's OWN guard, tested where the shipped path cannot show it.

    A cancelled walk returns before Stage 6, so its ``RunResult`` carries no
    token estimate and no reconciliation to begin with — which means the
    ``if outcome.published`` guards in :meth:`RealPipeline.run` cannot be seen to
    do anything by cancelling a real run, and a mutation that removes them passes
    the suite. That is exactly the shape of defence that rots.

    So the case is constructed: an outcome that says it published nothing while
    carrying a full result. It is not hypothetical — every stage between the
    walk and the swap is a place a future edit could return one.
    """
    from dataclasses import replace as dc_replace

    from dociq.runstate import RunTermination

    pipe = adapter.RealPipeline(ocr_enabled=False)
    real, _ = _run(pipe, _request(tmp_path, "matter"))
    assert real.tokens_before.structural_tokens > 0

    captured = {}

    def fake_run(config, options=None):
        outcome = captured["outcome"]
        return dc_replace(
            outcome,
            published=False,
            termination=RunTermination(TerminalStatus.CANCELLED, "stopped"),
        )

    captured["outcome"] = _last_core_outcome(tmp_path / "matter")
    monkeypatch.setattr(adapter.core, "run", fake_run)
    unpublished, _ = _run(pipe, _request(tmp_path, "matter2"))

    assert not unpublished.published
    assert unpublished.plan is None
    assert unpublished.reconciliation is None
    assert unpublished.tokens_before.structural_tokens == 0
    assert unpublished.tokens_after.structural_tokens == 0
    assert "published nothing" in unpublished.tokens_after.provenance


def _last_core_outcome(root):
    """A real, complete :class:`dociq.pipeline.PipelineOutcome` to mutate."""
    from dociq.ingest import extract as ex
    from dociq.ingest import walker

    return adapter.core.run(
        RunConfig(source_root=str(FIXTURES), output_root=str(root),
                  ocr_engine_version=ex.ocr_engine_version()),
        adapter.core.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False)),
    )


def test_the_no_profile_sentinel_never_reaches_the_run_identity(tmp_path, library):
    """FAIL-BEFORE: passing :data:`adapter.NO_PROFILE` straight through
    ``config_from`` stamps ``profile_id='none'`` into the hashed
    :class:`RunConfig`, so "no profile" and "a profile called none" record
    identically and every document is labelled with a profile that does not
    exist."""
    pipe = adapter.RealPipeline(ocr_enabled=False)
    outcome, _ = _run(pipe, _request(tmp_path, "matter", adapter.NO_PROFILE))
    assert outcome.result.config.profile_id is None
    assert outcome.result.config.profile_version is None
    assert not outcome.result.config.profiles
    log = json.loads((tmp_path / "matter" / "processing_log.json")
                     .read_text(encoding="utf-8"))
    assert "none" not in json.dumps(log["content"]["config"])


def test_a_chosen_profile_that_vanished_stops_the_run(tmp_path, library):
    """Running without the rules the operator chose, and publishing the result
    as a reduced corpus, is the failure this product exists to prevent."""
    _write(library, MPR)
    pipe = adapter.RealPipeline()
    offered = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    (library / "mpr-test.v1.0.yaml").unlink()

    with pytest.raises(ProfileError) as exc:
        _run(pipe, _request(tmp_path, "matter", offered))
    assert "no longer in the profile library" in str(exc.value)


def test_bates_is_not_auto_confirmed_for_an_operator_run(tmp_path, library):
    """§4 Stage 3 confirmation is the operator's. Until the seam can ask, the
    run must behave as every unattended run does — detect, decline to apply, and
    say so — rather than confirm on the expert's behalf."""
    pipe = adapter.RealPipeline(ocr_enabled=False)
    outcome, _ = _run(pipe, _request(tmp_path, "matter"))
    assert not any("confirmed AUTOMATICALLY" in w
                   for w in outcome.result.warnings)


# ---------------------------------------------------------------------------
# D-4 — the reduction plan
# ---------------------------------------------------------------------------


@pytest.fixture
def profiled(tmp_path, library):
    """A real run with a profile chosen and NO omission approved.

    D-34's shipped state: the template recognizes sections and nothing drops.
    Named ``profiled`` still because the profile is still an input to the run —
    it just no longer decides anything.
    """
    _write(library, MPR)
    pipe = adapter.RealPipeline(ocr_enabled=False)
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    outcome, _ = _run(pipe, _request(tmp_path, "matter", chosen))
    return outcome


@pytest.fixture
def approved(tmp_path, library):
    """The same run with one omission ENGAGED, through the seam's own capture
    point.

    :meth:`RealPipeline.set_omission` rather than a hand-built
    :class:`ApprovedOmission`, because that is where D-34 says the approver is
    read off the machine — a test that composed the name would prove the
    plumbing and not the ruling.
    """
    _write(library, MPR)
    pipe = adapter.RealPipeline(ocr_enabled=False)
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    approval = pipe.set_omission(FIXTURE_FAMILY, True, "the test matter")
    assert approval is not None
    outcome, _ = _run(pipe, _request(tmp_path, "approved", chosen,
                                     approvals=(approval,)))
    return outcome, approval


def test_the_plan_is_built_from_counted_pages(approved):
    """REPOINTED at D-34/D-35, on two points.

    **The fixture moved from ``profiled`` to ``approved``.** It asserted
    ``plan.pages_dropped == profiled.result.pages_dropped``, and after D-35 both
    sides are 0 for a run with no approval — the assertion held and measured
    nothing. It is now made on a run where three pages actually dropped, so the
    two figures can disagree.

    **``all(lever.tokens > 0)`` is withdrawn.** It encoded "a profile only ruled
    on sections that had text". Recognition rules on every section it can place,
    and a recognized ``Blank page`` is worth one page and zero tokens — that is
    the measurement, not a hole in it. ``pages > 0`` is the invariant that
    survives: a row on the waterfall describes at least one real page.
    """
    outcome, approval = approved
    plan = outcome.plan
    assert plan is not None, "a run with recognized sections produced no waterfall"
    assert plan.levers
    assert all(not lever.estimated for lever in plan.levers), (
        "a counted saving was flagged as projected"
    )
    assert all(lever.pages > 0 for lever in plan.levers), (
        "a waterfall row describes no page"
    )
    assert plan.pages_dropped == outcome.result.pages_dropped > 0, (
        "the waterfall and the run disagree about how many pages were omitted"
    )
    # The engaged row is the approved one, and it names the person who approved
    # it. Nothing else on the waterfall does.
    engaged = plan.engaged
    assert [le.family_id for le in engaged] == [FIXTURE_FAMILY]
    assert engaged[0].approved_by == approval.approved_by
    assert all(not le.approved_by for le in plan.levers if le not in engaged)
    # Every row states how the section was recognized (§5.4), because a run has
    # now read documents and the tiers are a fact about them.
    assert all(le.tier for le in plan.levers), (
        "a row on the waterfall does not say how the section was recognized"
    )


def test_the_plan_and_the_headline_cannot_disagree(profiled):
    """One measurement, read twice — not two estimators that usually agree."""
    plan = profiled.plan
    assert plan.full_tokens == profiled.tokens_before.structural_tokens
    assert sum(lever.tokens for lever in plan.levers) <= plan.full_tokens
    assert plan.remaining_tokens >= 0
    assert plan.basis.provenance == profiled.tokens_before.provenance
    assert plan.basis.is_structural


def test_expert_and_automatic_savings_are_never_merged(profiled):
    """Principle 3: only the expert's omissions are the expert's to defend.

    There is no automatic lever at all, and that is the honest state — DocIQ
    detects exact-hash duplicates and removes nothing. A lever here would claim
    a reduction the deliverable does not contain.
    """
    plan = profiled.plan
    assert all(lever.kind == LEVER_EXPERT for lever in plan.levers)
    assert not [le for le in plan.levers if le.kind == LEVER_AUTOMATIC]
    assert plan.automatic_tokens == 0
    assert plan.expert_tokens == sum(le.tokens for le in plan.engaged)


def test_the_capacity_line_is_the_named_constant(profiled):
    """D-21: 200,000, carried as a reference line, with no setter."""
    assert profiled.plan.capacity == DIRECT_CONTEXT_TOKENS == 200_000


def test_toggling_a_lever_still_reprojects_the_same_run(profiled):
    plan = profiled.plan
    key = plan.levers[0].key
    flipped = plan.with_toggled(key)
    assert flipped.levers[0].engaged != plan.levers[0].engaged
    assert flipped.full_tokens == plan.full_tokens
    assert flipped.capacity == plan.capacity


def test_a_run_with_no_approval_has_a_waterfall_that_drops_nothing(real_run):
    """REPOINTED from ``test_a_run_with_no_profile_has_no_waterfall``.

    **The withdrawn claim.** ``plan is None`` for a run with no profile, on the
    reasoning that "``None`` means the screen shows the record at full size —
    the truth for a corpus nothing was dropped from". A profile was then the
    only thing that produced levers. D-35 makes the waterfall a RECOGNITION
    waterfall: the shipped template names sections whether or not a profile was
    chosen, so a plan exists for this run and ``None`` would now mean "no
    section was recognized at all".

    The sentence the old test was protecting — the screen must show the record
    at full size when nothing was omitted — survives exactly, and is the
    arithmetic below: no row engaged, no approver named, nothing dropped, and
    ``remaining_tokens == full_tokens``. That is a stronger assertion than
    ``plan is None``, because it would also catch a template that dropped a page
    on its own, which is precisely what D-34 forbids and ``None`` could not see.

    ``plan is None`` is still reachable and still asserted, for the case it now
    means: a run that published nothing (see
    ``test_a_cancelled_run_publishes_nothing_and_hands_over_no_figures``).
    """
    outcome, _events, _root = real_run
    plan = outcome.plan
    assert plan is not None, (
        "a published run recognized no section at all — the fixture corpus "
        "recognizes two, so this is a recognition regression, not an empty "
        "waterfall")
    assert plan.levers
    assert plan.engaged == (), "a row is engaged and no one approved anything"
    assert not any(le.approved_by for le in plan.levers), (
        "the approver field holds a fiction (D-34)")
    assert plan.pages_dropped == 0
    assert outcome.result.pages_dropped == 0
    assert plan.remaining_tokens == plan.full_tokens, (
        "the screen shows the record at less than full size for a corpus "
        "nothing was omitted from")


# ---------------------------------------------------------------------------
# The swap itself
# ---------------------------------------------------------------------------


def test_the_default_pipeline_is_the_real_one():
    set_pipeline(None)
    try:
        assert isinstance(get_pipeline(), adapter.RealPipeline)
        assert get_pipeline().disclosure() == "", (
            "the real pipeline must not carry a stand-in's notice"
        )
    finally:
        set_pipeline(None)


def test_the_mock_is_still_installable():
    """The seam is only proven while two implementations satisfy it."""
    from dociq.gui.mock_pipeline import MockPipeline

    mock = MockPipeline()
    set_pipeline(mock)
    try:
        assert get_pipeline() is mock
        assert get_pipeline().disclosure()
    finally:
        set_pipeline(None)
    assert isinstance(get_pipeline(), adapter.RealPipeline)


def test_the_window_builds_against_the_real_pipeline(library):
    """The swap, end to end: the shipped entry point, the real adapter, no mock.

    A unit test of ``get_pipeline()`` proves the wiring and not the fit — the
    window calls ``profiles()`` and ``disclosure()`` during construction, and an
    adapter that returned an empty tuple or a stand-in's notice would build a
    picker with nothing in it or a fixture banner over a real run.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from dociq.gui.app import build_app
    from dociq.gui.widgets import DisclosureBar

    QApplication.instance() or QApplication([])
    set_pipeline(None)
    _app, window = build_app([])
    try:
        assert isinstance(window._pipeline, adapter.RealPipeline)
        assert not window.findChildren(DisclosureBar), (
            "a real run is wearing a stand-in's disclosure bar"
        )
        preview = window._pipeline.preview_folder(str(FIXTURES))
        assert preview.file_count > 0
    finally:
        window.close()


def test_both_implementations_satisfy_the_same_protocol():
    from dociq.gui.mock_pipeline import MockPipeline

    for pipe in (adapter.RealPipeline(), MockPipeline()):
        assert isinstance(pipe.profiles(), tuple)
        assert all(isinstance(p, ProfileInfo) for p in pipe.profiles())
        assert isinstance(pipe.disclosure(), str)


# ---------------------------------------------------------------------------
# D-5 / A-12 — §8 handoff: Path B checked, Path A built
# ---------------------------------------------------------------------------


def test_path_b_note_says_it_LOOKED(real_run):
    """FAIL-BEFORE: describing the folder from §7 rather than checking it is the
    one thing Path B's claim cannot be proven by. `expert_assist_layout` inspects
    disk; the note must be its report, not a restatement of the spec."""
    outcome, _events, root = real_run
    note = adapter.RealPipeline().matter_layout_note(outcome)
    assert note.startswith("CHECKED")
    for name in ("clean_text/", "sources.json", "document_index.csv",
                 "processing_log.json"):
        assert name in note
    assert str(root) in note


def test_path_b_note_names_what_is_missing(real_run, tmp_path):
    """A folder that is NOT Expert-Assist-shaped must be reported as such —
    otherwise the check is decoration."""
    from dataclasses import replace

    outcome = replace(real_run[0], output_root=str(tmp_path / "empty"))
    note = adapter.RealPipeline().matter_layout_note(outcome)
    assert "NOT ready" in note
    assert "sources.json" in note


def test_path_b_note_is_empty_when_there_is_nothing_to_look_at(real_run):
    """The seam's documented "" — the pipeline did not look. Distinguished from
    a verified folder, because the screen renders them differently."""
    from dataclasses import replace

    pipe = adapter.RealPipeline()
    assert pipe.matter_layout_note(replace(real_run[0], published=False)) == ""
    assert pipe.matter_layout_note(replace(real_run[0], output_root="")) == ""


def test_build_package_writes_exactly_the_scoped_documents(real_run):
    outcome, _events, root = real_run
    ids = tuple(d.doc_id for d in outcome.result.documents)[:1]
    result = adapter.RealPipeline().build_package(
        outcome, ids, "SCOPE OF THIS PACKAGE\n" + "=" * 60 + "\n  MARKER-Z\n")
    pkg = Path(result.root)
    assert pkg == root / "upload_package"
    assert result.doc_count == 1
    texts = sorted(p.name for p in pkg.glob("*.txt")
                   if p.name != "README_START_HERE.txt")
    assert texts == [f"{ids[0]}.txt"]
    assert "MARKER-Z" in result.scope_statement
    assert (pkg / "README_START_HERE.txt").read_text(
        encoding="utf-8").startswith("SCOPE OF THIS PACKAGE")


def test_build_package_measures_the_SUBSET_not_the_corpus(real_run):
    """FAIL-BEFORE: reading ``outcome.tokens_after`` puts the whole corpus's
    figure in a one-document package's README — and the README's capacity
    sentence is DERIVED from it, so the error arrives as advice."""
    outcome, _events, root = real_run
    docs = outcome.result.documents
    assert len(docs) > 1, "fixture corpus too small to tell the two apart"
    pipe = adapter.RealPipeline()

    pipe.build_package(outcome, (docs[0].doc_id,), "S\n")
    one = (root / "upload_package" / "README_START_HERE.txt").read_text(
        encoding="utf-8")
    pipe.build_package(outcome, tuple(d.doc_id for d in docs), "S\n")
    every = (root / "upload_package" / "README_START_HERE.txt").read_text(
        encoding="utf-8")

    def headline(text: str) -> str:
        return next(ln for ln in text.splitlines() if "Estimated size:" in ln)

    assert headline(one) != headline(every)


def test_build_package_refuses_an_empty_scope(real_run):
    """The seam's contract is that an adapter which cannot do Path A OMITS the
    method. A call that reaches here is one the screen believes will produce a
    package, so returning an empty result would leave a button that appears to
    work."""
    with pytest.raises(ValueError):
        adapter.RealPipeline().build_package(real_run[0], ("LI-99999",), "S\n")


def test_build_package_refuses_a_run_that_published_nothing(real_run):
    from dataclasses import replace

    with pytest.raises(ValueError):
        adapter.RealPipeline().build_package(
            replace(real_run[0], published=False),
            (real_run[0].result.documents[0].doc_id,), "S\n")


def test_the_real_adapter_offers_both_A_12_hooks():
    """The GUI probes for these by ``getattr`` and disables the action WITH THE
    REASON when they are absent (A-12). Absent is what they were: Path A was
    permanently disabled and Path B said the pipeline had not looked."""
    pipe = adapter.RealPipeline()
    assert callable(getattr(pipe, "build_package", None))
    assert callable(getattr(pipe, "matter_layout_note", None))


def test_the_handoff_SCREEN_drives_the_real_adapter_end_to_end(real_run):
    """Track E §6.1/§6.2/§6.3, closed.

    Both §8 screens were built against duck-typed hooks that only the mock
    implemented, and Track E recorded plainly that neither had been driven by a
    real pipeline and that no package had ever been built. This drives the real
    ``MainWindow`` — real adapter, real run, real ``upload_package/`` on disk —
    through the same signals the operator's clicks emit.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from dociq.emit.handoff import README_NAME, assert_only_sanctioned
    from dociq.gui.main_window import MainWindow
    from dociq.gui.view_models import SCOPE_TYPES, PackageScope

    outcome, _events, root = real_run
    QApplication.instance() or QApplication([])
    set_pipeline(None)
    window = MainWindow()
    try:
        assert isinstance(window._pipeline, adapter.RealPipeline)
        window.show_outcome(outcome)
        window.show_handoff()

        # Path B: the screen's note is the CHECKED one, not the mock's words.
        view = window.handoff._view
        assert view.path_b_ready()
        assert view.path_b_note().startswith("CHECKED")
        assert not view.package_blocker(), view.package_blocker()

        # Path A: scope it the way the screen does, then press the button.
        kind = view.doc_types[0]
        window._rescope(PackageScope(kind=SCOPE_TYPES, doc_types=(kind,)))
        window._build_package(window._scope)

        pkg = root / "upload_package"
        assert pkg.is_dir()
        names = assert_only_sanctioned(pkg)
        assert README_NAME in names
        head = (pkg / README_NAME).read_text(encoding="utf-8")
        assert head.startswith("SCOPE OF THIS PACKAGE")
        expected = window.handoff._view.scope_statement()
        assert expected.strip() in head, (
            "the package does not carry the statement the operator was shown"
        )
    finally:
        window.close()


# --- B6: a partly-dropped section must not render as fully KEPT -------------
#
# ``_plan`` set ``engaged = dropped == pages`` and always carried the WHOLE
# section's figures. A section with some pages dropped and some kept therefore
# drew as KEEP with its entire token weight counted as still present, so
# ``remaining_tokens`` overstated and the waterfall disagreed with
# ``tokens_after``.
#
# CONSTRUCTED AND CONFIRMED BEFORE IT WAS FIXED. It was then reachable through
# an ordinary valid profile: ``FormatProfile.validate`` enforces uniqueness on
# ``rule_id`` only, and the deleted Stage 4 keyed a page's section on
# ``rule.label or matched_text`` — so one DROP rule and one KEEP rule sharing a
# label put dropped and kept pages under the same section name.
#
# REPOINTED AT D-35 / D-34 (commits 4092f76, d3cee24). The block splits in two
# and the halves moved differently, which is why this comment is longer than the
# fix:
#
#   * The RENDERING guarantee is intact and still lives in
#     ``adapter._template_lever``: a row whose section is only partly dropped
#     carries the DROPPED part's figures and says "(part — N of M pages)". The
#     three tests below still pin it, now through a template and an approval,
#     because an approval is the only thing that can drop a page.
#
#   * The REACHABILITY claim is WITHDRAWN. ``apply_profiles`` — the function the
#     old reachability test called — was deleted by 4092f76, and the engine that
#     replaced it cannot produce the partial case: ``sections.apply`` drops
#     every page of an approved span or none of them, and ``PageRecord.validate``
#     refuses the one state that could split a section under one label (a page
#     carrying a ``section`` without a ``section_tier``). So the partial branch
#     in ``_template_lever`` is now DEFENSIVE rather than exercised, and saying
#     so is the honest replacement for a test that asserted the opposite. The
#     new all-or-nothing guarantee, plus the enumeration of every place in the
#     package that can drop a page at all, are asserted below.


def _partial_section_docs():
    from tests.fixtures import document, page

    pages = (
        page(1, "PHOTO LOG\n" + "alpha bravo charlie delta " * 40,
             section="Photo logs", disposition=Disposition.DROP,
             drop_rule=f"{PROGRESS_REPORT.template_id}:{FIXTURE_FAMILY}"),
        page(2, "PHOTO LOG\n" + "echo foxtrot golf hotel " * 40,
             section="Photo logs"),
        page(3, "NARRATIVE\n" + "india juliet kilo " * 40, section="Narrative"),
    )
    return document("a.pdf", pages, doc_id="LI-1"), pages


def _plan_for(doc, pages, approvals=(FIXTURE_FAMILY,)):
    """``_plan`` over one hand-built document.

    ``template=`` and ``approvals=`` are now required to get an ENGAGEABLE row
    at all: without a template every section is a locked ``LEVER_RECOGNIZED``
    row, and without an approval no family is engaged. That is D-34 showing
    through the unit boundary, not a test detail.
    """
    from dociq.gui.pipeline import TokenEstimate
    from dociq.verify import tokens as vt

    class _Result:
        documents = (doc,)

    total = sum(vt.measure(p.text).pretokens for p in pages)
    return adapter._plan(
        _Result,
        TokenEstimate(chars=0, ratio_low=3.3, ratio_high=3.6,
                      structural_tokens=total),
        template=PROGRESS_REPORT,
        approvals=tuple(_approval(f) for f in approvals),
    )


def test_a_partly_dropped_section_is_not_drawn_as_kept():
    """FAIL-BEFORE: ``engaged`` False, ``pages`` 2, ``tokens`` 328 — the row
    said the whole section survived, and one of its pages had not.

    REPOINTED: the section is now recognized as the template family
    ``progress-photographs`` and engaged because an approval names it, so the
    row's LABEL is the family's matter-neutral display name (D-24) while the
    document's own words survive as the row's ``key``. The partial marker is
    asserted on the label exactly as before.
    """
    doc, pages = _partial_section_docs()
    lever = next(le for le in _plan_for(doc, pages).levers
                 if le.key == "Photo logs")
    assert lever.engaged, "a section with dropped pages is dropping"
    assert lever.family_id == FIXTURE_FAMILY
    assert lever.pages == 1, "the lever removes the DROPPED pages, not all of them"
    assert "part" in lever.label and "1 of 2 pages" in lever.label


def test_the_waterfall_agrees_with_what_the_run_actually_published():
    """The consequence, asserted as arithmetic rather than as wording.

    FAIL-BEFORE: 451 remaining against 287 actually published — the screen
    overstated the corpus by a whole dropped page."""
    from dociq.verify import tokens as vt

    doc, pages = _partial_section_docs()
    plan = _plan_for(doc, pages)
    published = sum(vt.measure(p.text).pretokens for p in pages
                    if p.disposition is not Disposition.DROP)
    assert plan.remaining_tokens == published


def test_a_wholly_dropped_and_a_wholly_kept_section_are_unchanged():
    """The fix must not move the two cases that were already right, and the
    label must stay clean for them — "(part — 2 of 2)" on a full drop would be
    noise that trains the reader to skip the marker.

    REPOINTED on the KEPT row's label. "Narrative" matches no family the shipped
    template names, so it is a recognized, LOCKED row and keeps the document's
    own words. The wholly-dropped row is a family and reads as the family —
    ``Progress photographs``, not ``Photo logs`` — which is D-24 deliberately:
    the label an expert approves against is matter-neutral, and what the
    document itself called the section is preserved on ``key`` and in the drop
    log.
    """
    doc, pages = _partial_section_docs()
    levers = {le.key: le for le in _plan_for(doc, pages).levers}
    kept = levers["Narrative"]
    assert not kept.engaged and kept.pages == 1 and kept.label == "Narrative"
    assert kept.kind == LEVER_RECOGNIZED and kept.locked, (
        "a section the template names no family for was drawn as engageable"
    )

    from tests.fixtures import document, page

    both_dropped = (
        page(1, "PHOTO LOG\nx " * 30, section="Photo logs",
             disposition=Disposition.DROP,
             drop_rule=f"{PROGRESS_REPORT.template_id}:{FIXTURE_FAMILY}"),
        page(2, "PHOTO LOG\ny " * 30, section="Photo logs",
             disposition=Disposition.DROP,
             drop_rule=f"{PROGRESS_REPORT.template_id}:{FIXTURE_FAMILY}"),
    )
    d2 = document("b.pdf", both_dropped, doc_id="LI-2")
    lev = _plan_for(d2, both_dropped).levers[0]
    assert lev.engaged and lev.pages == 2
    assert lev.label == "Progress photographs", (
        "a wholly dropped section carries the partial marker, or lost its "
        "family name")
    assert "part" not in lev.label


def test_a_recognized_family_nobody_approved_is_drawn_off(library):
    """D-34 at the same unit boundary — the state the partial-case tests can no
    longer reach.

    New with this repointing, and it is the assertion the old ``engaged ==
    (dropped == pages)`` rule made impossible to write: a section the template
    names, offers, and could drop must still draw OFF, with no approver, when
    nobody approved it. Under the old rule "nothing dropped" and "nothing
    approved" were the same fact; under D-34 they are two, and only one of them
    is a decision.
    """
    from tests.fixtures import document, page

    pages = (
        page(1, "PHOTO LOG\nalpha bravo " * 20, section="Photo logs"),
        page(2, "PHOTO LOG\ncharlie delta " * 20, section="Photo logs"),
    )
    doc = document("c.pdf", pages, doc_id="LI-3")
    lever = _plan_for(doc, pages, approvals=()).levers[0]

    assert lever.family_id == FIXTURE_FAMILY
    assert lever.kind == LEVER_EXPERT, "an offered family was locked"
    assert not lever.engaged, "a lever nobody engaged is dropping pages"
    assert lever.approved_by == "", "an omission attributed to nobody (D-34)"
    assert lever.pages == 2, "an unengaged row must still show the whole section"


def test_the_only_drop_site_drops_a_whole_span():
    """REPOINTED from ``test_the_partial_case_is_reachable_from_a_valid_profile``.

    That test proved the partial case REACHABLE through
    ``dociq.profiles.apply.apply_profiles``, which commit 4092f76 deleted. It is
    not repointed at an equivalent — there is no equivalent, and asserting one
    would be inventing a guarantee. What replaced the reachability is the
    opposite property, and it is worth more: the single site that can drop a
    page drops an approved span WHOLE, so a section cannot come out of the
    pipeline half-omitted at all.

    That is D-35's own sentence made checkable — "a drop is bounded by the span
    that caused it" — with both ends measured: nothing inside the span survives,
    and nothing outside it is touched.
    """
    from dociq.contracts import RecognitionTier
    from dociq.sections.apply import apply_sections
    from dociq.sections.resolve import spans_from_pages
    from tests.fixtures import document, page

    pages = (
        page(1, "COVER\ntitle"),
        page(2, "PHOTO LOG\nsite photos one",
             section="Photo logs", section_tier=RecognitionTier.PAGE_CLASS),
        page(3, "PHOTO LOG\nsite photos two",
             section="Photo logs", section_tier=RecognitionTier.PAGE_CLASS),
        page(4, "DRAWING LIST\ndrawings",
             section="Drawing list", section_tier=RecognitionTier.PAGE_CLASS),
    )
    doc = document("m.pdf", pages, doc_id="LI-1")

    spans = spans_from_pages(doc.pages)
    result = apply_sections(doc, spans, template=PROGRESS_REPORT,
                            approvals=(_approval(),))
    out = result.documents[0]
    by_no = {p.page_no: p for p in out.pages}

    assert by_no[2].disposition is Disposition.DROP
    assert by_no[3].disposition is Disposition.DROP, (
        "the span's last page survived — a section came out half-omitted")
    assert by_no[1].disposition is Disposition.KEEP
    assert by_no[4].disposition is Disposition.KEEP, (
        "the drop reached past its span, which is the D-35 defect exactly")

    # No section name carries both dispositions: the partial case, refused.
    by_section: dict[str, set] = {}
    for p in out.pages:
        if p.section:
            by_section.setdefault(p.section, set()).add(p.disposition)
    assert all(len(v) == 1 for v in by_section.values()), by_section

    # Every drop is attributed to the person who approved it.
    assert {d.page_no for d in result.drops} == {2, 3}
    assert all(d.approved_by == "j.long" for d in result.drops)
    assert all(d.tier is RecognitionTier.PAGE_CLASS for d in result.drops)


def test_no_second_place_in_the_package_can_drop_a_page():
    """The class assertion behind the one above, enumerated rather than asserted.

    ``test_the_only_drop_site_drops_a_whole_span`` is worth nothing if a second
    site can also set ``Disposition.DROP`` — the D-35 engine WAS such a site, and
    it lived beside the honest one for a whole sprint. So this enumerates every
    keyword assignment of ``Disposition.DROP`` in the shipped package and pins
    the result, which means a third one has to be argued for here before it can
    exist.
    """
    import dociq

    root = Path(dociq.__file__).resolve().parent
    sites: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.keyword) and node.arg == "disposition"):
                continue
            if any(isinstance(n, ast.Attribute) and n.attr == "DROP"
                   for n in ast.walk(node.value)):
                sites.add(path.relative_to(root).as_posix())

    assert sites == {
        # The one drop site in the pipeline (D-34/D-35). It requires an
        # ApprovedOmission naming a person and is bounded by a span.
        "sections/apply.py",
        # The Sprint-1 stand-in behind the seam, which fabricates a corpus to
        # exercise the GUI and never touches an evidence file. It is not the
        # pipeline and `disclosure()` says so on screen.
        "gui/mock_pipeline.py",
    }, (
        f"a second place can drop a page: {sorted(sites)}. Every drop must go "
        f"through an ApprovedOmission, or Principle 3's separation between the "
        f"tool's reductions and the expert's omissions is gone."
    )


# --- B5: UploadPackage.missing must not be dropped on the floor -------------


def test_the_seam_result_carries_the_missing_doc_ids(real_run):
    """Codex review #2, B-3. The value must be on the RETURNED RECORD.

    **What this test used to assert, and why that was worthless.** It read
    ``RealPipeline.last_package_missing`` — a private attribute the GUI never
    touched — and passed, for the whole sprint, while ``build_package`` built its
    ``PackageResult`` without ``missing=`` and every consumer of the declared
    seam saw an empty tuple. The docstring claimed the value was held "where a
    screen can reach it"; no screen ever did. Both the claim and the attribute
    are withdrawn.

    FAIL-BEFORE: with ``missing=package.missing`` removed from the construction
    in :meth:`RealPipeline.build_package`, the assertion below reads ``()`` for a
    scope that asked for a document the matter folder does not hold.
    """
    outcome, _events, _root = real_run
    pipe = adapter.RealPipeline()
    assert not hasattr(pipe, "last_package_missing"), (
        "the private holding attribute is back; the seam field is the only home"
    )

    real = tuple(d.doc_id for d in outcome.result.documents)[:1]
    # A Doc ID with no clean_text file, alongside one that has it. The scope
    # asks for two documents and the folder can only hold one.
    result = pipe.build_package(outcome, real + ("LI-99999",), "SCOPE\n")
    assert result.doc_count == 1
    assert result.missing == ("LI-99999",), (
        "the seam dropped the emit layer's own report of what the package "
        "could not include"
    )

    # And a complete package says so on the same field — a stale name beside a
    # complete package is the same defect pointing the other way.
    assert pipe.build_package(outcome, real, "SCOPE\n").missing == ()
