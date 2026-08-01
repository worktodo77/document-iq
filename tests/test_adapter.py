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

import json
from pathlib import Path

import pytest

from dociq import adapter
from dociq.contracts import Disposition, RunConfig
from dociq.gui.pipeline import (
    DIRECT_CONTEXT_TOKENS,
    LEVER_AUTOMATIC,
    LEVER_EXPERT,
    ProfileInfo,
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


def _request(tmp_path, name="out", profile=None, index=None) -> RunRequest:
    return RunRequest(str(FIXTURES), str(tmp_path / name), profile, index)


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
    """``section_rules`` is what the profile carries, not a placeholder: the
    operator reads it to see whether a profile will remove anything."""
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


def test_a_folder_that_is_not_there_previews_as_nothing(tmp_path):
    preview = adapter.RealPipeline().preview_folder(str(tmp_path / "nope"))
    assert preview == type(preview)(0, 0)


def test_the_estimate_is_the_measured_rate_and_nothing_else():
    """FAIL-BEFORE: the mock's ``MINUTES_PER_GIGABYTE = 18`` is labelled
    ILLUSTRATIVE, and carrying it into the real adapter would put an invented
    number beside the operator's action.

    The rate here is arithmetic on the register's single measured run, so this
    test is allowed to restate it — if the constant is ever changed without a
    new measurement, this is where the change has to be argued.
    """
    two_gb = 2_000_000_000
    minutes = adapter._minutes_for(two_gb, {".pdf": two_gb})
    assert minutes == round(2.0 * (3046.7 / 2.6) / 60)
    assert minutes == 39


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


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("adapter")
    pipe = adapter.RealPipeline(ocr_enabled=False)
    events: list = []
    outcome = pipe.run(RunRequest(str(FIXTURES), str(out / "matter")),
                       events.append, lambda: False)
    return outcome, events, out / "matter"


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
    """The register measures Stages 1-2 at 99.1% of the run. Without this, the
    progress screen goes quiet for everything after the walk and a long emit
    reads as a hang."""
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
    _write(library, MPR)
    pipe = adapter.RealPipeline(ocr_enabled=False)
    chosen = [p for p in pipe.profiles() if p.profile_id == "mpr-test"][0]
    outcome, _ = _run(pipe, _request(tmp_path, "matter", chosen))
    return outcome


def test_the_plan_is_built_from_counted_pages(profiled):
    plan = profiled.plan
    assert plan is not None, "a profile ran and produced no waterfall"
    assert plan.levers
    assert all(not lever.estimated for lever in plan.levers), (
        "a counted saving was flagged as projected"
    )
    assert all(lever.tokens > 0 for lever in plan.levers)
    assert plan.pages_dropped == profiled.result.pages_dropped


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


def test_a_run_with_no_profile_has_no_waterfall(real_run):
    """``None`` means the screen shows the record at full size — which is the
    truth for a corpus nothing was dropped from."""
    outcome, _events, _root = real_run
    assert outcome.plan is None
    assert outcome.result.pages_dropped == 0


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
