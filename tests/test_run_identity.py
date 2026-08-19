"""The run identity — what the byte-identical claim actually covers.

Codex review #1 finding B-2: ``RunConfig`` is documented as holding everything
that can change output bytes, and several things that can were living outside
it — environment-controlled caps, the ZIP depth limit, the per-file timeout, the
retry bounds, whether the walk recursed, and the bytes of the OCR model. Two
runs could then differ in evidence while presenting the same hashed
configuration, which makes the manifest's claim uncheckable rather than false —
a worse failure, because nothing goes red.

Amendment A-04 added ``RunConfig.limits``. These tests are about it being
*populated*, *hashed*, and *named in the claim*, which is the half the amendment
could not do by itself.

**Two tests were removed here by D-38**, both of the profile snapshot set:
``test_every_profile_in_the_library_is_snapshotted_by_content`` and
``test_an_unprofiled_run_records_an_empty_profile_set``, plus the
three that measured profile precedence and per-document stamping. They tested a field
that no longer exists. Their PRINCIPLE -- the input that decides which pages
drop must move the identity -- was A-08's, is now A-19's, and is tested on the
input that decides today by ``test_approving_an_omission_moves_the_identity``
and ``test_changing_who_approved_the_omission_moves_the_identity``.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from dociq import pipeline
from dociq.contracts import (
    matter_key,
    EffectiveLimits,
    RunConfig,
    content_hash,
    run_identity,
)
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.operator import OperatorStamp
from dociq.verify import manifest as mf

from .conftest import FIXTURES

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")


def _run(tmp_path, name="out", **walk_kw):
    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / name),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        cfg,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False, **walk_kw),
            matter_name="fixture corpus",
            stamp=STAMP,
        ),
    )


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("identity"))


# ---------------------------------------------------------------------------
# Populated
# ---------------------------------------------------------------------------


def test_a_real_run_populates_every_effective_limit(outcome):
    limits = outcome.result.config.limits
    assert limits is not None, (
        "RunConfig.limits exists precisely so a run cannot present a hashed "
        "configuration that omits what it used"
    )
    for f in dataclasses.fields(EffectiveLimits):
        value = getattr(limits, f.name)
        if f.name in ("recurse", "ocr_model_id"):
            continue
        assert isinstance(value, int) and value > 0, f"{f.name} = {value!r}"
    assert limits.recurse is True
    assert limits.workers > 0


def test_the_limits_match_the_values_the_modules_actually_hold(outcome):
    """Recorded from the constants the extractors use, not re-read from the
    environment: a second read could disagree with the first."""
    limits = outcome.result.config.limits
    caps = ex.effective_caps()
    assert limits.xlsx_max_rows == caps["xlsx_max_rows"]
    assert limits.csv_max_rows == caps["csv_max_rows"]
    assert limits.zip_max_mb == caps["zip_max_mb"]
    assert limits.zip_max_members == caps["zip_max_members"]
    assert limits.zip_max_depth == caps["zip_max_depth"]
    assert limits.retry_max == walker._RETRY_MAX
    assert limits.retry_budget_ms == round(walker._RETRY_BUDGET_S * 1000)


def test_a_run_with_ocr_off_records_no_model_identity(outcome):
    """An identity for models that read nothing would be noise in the hash."""
    assert outcome.result.config.limits.ocr_model_id == ""


def test_ocr_on_records_the_model_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "ocr_model_id", lambda: "test-engine 9.9; models abc")
    limits = walker.effective_limits(
        walker.WalkOptions(ocr_enabled=True), ocr_enabled=True
    )
    assert limits.ocr_model_id == "test-engine 9.9; models abc"


# ---------------------------------------------------------------------------
# Hashed
# ---------------------------------------------------------------------------


def _limits(**kw) -> EffectiveLimits:
    base = dict(
        xlsx_max_rows=50000, csv_max_rows=50000, zip_max_mb=500,
        zip_max_members=2000, zip_max_depth=3, file_timeout_ms=3_600_000,
        retry_max=500, retry_budget_ms=1_800_000, recurse=True,
        ocr_model_id="engine 1.0; models deadbeef", workers=8,
    )
    base.update(kw)
    return EffectiveLimits(**base)


def _config(**kw) -> RunConfig:
    return RunConfig(source_root="/src", output_root="/out", limits=_limits(**kw))


@pytest.mark.parametrize(
    "field,value",
    [
        ("xlsx_max_rows", 10),
        ("csv_max_rows", 10),
        ("zip_max_mb", 1),
        ("zip_max_members", 1),
        ("zip_max_depth", 1),
        ("file_timeout_ms", 30),
        ("retry_max", 1),
        ("retry_budget_ms", 1),
        ("recurse", False),
        ("ocr_model_id", "engine 1.0; models 0000"),
    ],
)
def test_every_output_affecting_limit_changes_the_run_identity(field, value):
    assert content_hash(_config()) != content_hash(_config(**{field: value})), (
        f"{field} can change the emitted evidence, so it must change the hash"
    )


def test_pool_width_is_recorded_but_not_hashed():
    """Deliberate, and the one exclusion in :class:`EffectiveLimits`.

    Thread-pool width must not change output. Absorbing it into the identity
    would turn a determinism defect into a legitimate-looking hash difference —
    the failure would stop being visible instead of being fixed.
    """
    assert content_hash(_config()) == content_hash(_config(workers=1))
    assert _config(workers=1).limits.workers == 1


# ---------------------------------------------------------------------------
# Named in the claim
# ---------------------------------------------------------------------------


def test_the_manifest_claim_names_the_full_identity_it_covers(outcome):
    data = json.loads(
        (outcome.layout.root / mf.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    identity = data["claim_identity"]
    for named in ("master-index", "OCR confidence threshold",
                  "OCR engine", "Bates", "row caps", "ZIP", "per-file timeout",
                  "retry", "recursed", "OCR model identity"):
        assert named in identity, named
    assert "EXCLUDED" in identity and "workers" in identity

    # Codex Sprint-4 B-3. This block used to assert "profile" and
    # "profile_hash" were PRESENT, so when D-38 removed those fields from the
    # contract the test went on pinning the false claim green. A positive
    # substring list can only ever check that the claim says enough; it cannot
    # check that the claim says nothing retired. So the retired direction is
    # asserted too, and derived from the live contract rather than typed —
    # a hand-written list of removed names has to be maintained by the same
    # change that removes one.
    live = {f.name for f in dataclasses.fields(RunConfig)}
    assert "profiles" not in live, (
        "this guard assumes D-38 removed the profile inputs; if they came "
        "back, the claim must name them again")
    for retired in ("profile_hash", "profile snapshot", "profiles"):
        assert retired not in identity, (
            f"the persisted claim names {retired!r}, which is not an input of "
            f"RunConfig at contract 2.0.0")
    assert "OUTPUT folder" in identity, (
        "the claim does not say the destination is excluded, so it still "
        "disagrees with the log and the acceptance harness")
    assert "run_identity_sha256" in identity, (
        "the claim does not point at the persisted value that settles which "
        "projection is authoritative")


def test_the_processing_log_hashes_the_limits_and_reports_pool_width(outcome):
    data = json.loads(
        outcome.layout.processing_log.read_text(encoding="utf-8")
    )
    limits = data["content"]["config"]["limits"]
    assert limits["zip_max_depth"] == outcome.result.config.limits.zip_max_depth
    assert "workers" not in limits, (
        "pool width must not reach the hashed content section"
    )
    assert data["run"]["pool"]["workers"] == outcome.result.config.limits.workers
    assert data["run"]["pool"]["disk_headroom_x100"] > 0


# ---------------------------------------------------------------------------
# The OCR model identity is the bytes, not just the version
# ---------------------------------------------------------------------------


def _fake_models(root, payload: bytes):
    root.mkdir(parents=True, exist_ok=True)
    for name in ex._MODEL_FILES:
        (root / name).write_bytes(payload + name.encode())
    return root


def test_ocr_model_id_covers_the_model_bytes_not_only_the_version(
    tmp_path, monkeypatch
):
    """Two engines that read a page differently are different inputs.

    ``DOCIQ_OCR_MODEL_DIR`` can point one installed package at different ONNX
    files, so a version string alone does not prove the bytes match.
    """
    ex._MODEL_ID_CACHE.clear()
    a = _fake_models(tmp_path / "a", b"AAAA")
    monkeypatch.setenv("DOCIQ_OCR_MODEL_DIR", str(a))
    first = ex.ocr_model_id()
    assert "models " in first and len(first.split("models ")[1]) == 32

    ex._MODEL_ID_CACHE.clear()
    b = _fake_models(tmp_path / "b", b"BBBB")
    monkeypatch.setenv("DOCIQ_OCR_MODEL_DIR", str(b))
    assert ex.ocr_model_id() != first, (
        "different model bytes must produce a different identity"
    )

    ex._MODEL_ID_CACHE.clear()
    monkeypatch.setenv("DOCIQ_OCR_MODEL_DIR", str(a))
    assert ex.ocr_model_id() == first, "the identity must be stable for the bytes"


def test_ocr_model_id_is_explicit_when_the_models_are_missing(
    tmp_path, monkeypatch
):
    """An empty string would compare equal to a run that used no OCR at all."""
    ex._MODEL_ID_CACHE.clear()
    monkeypatch.setenv("DOCIQ_OCR_MODEL_DIR", str(tmp_path / "nothing-here"))
    ident = ex.ocr_model_id()
    ex._MODEL_ID_CACHE.clear()
    assert ident.endswith("models-unavailable")
    assert ident != ""


def test_ocr_model_id_is_stable_across_repeated_calls(tmp_path, monkeypatch):
    """Ordering- and cache-sensitive, so it is asserted 30 times, not once."""
    ex._MODEL_ID_CACHE.clear()
    root = _fake_models(tmp_path / "m", b"CCCC")
    monkeypatch.setenv("DOCIQ_OCR_MODEL_DIR", str(root))
    first = ex.ocr_model_id()
    for _ in range(30):
        assert ex.ocr_model_id() == first
    ex._MODEL_ID_CACHE.clear()
    assert ex.ocr_model_id() == first
    ex._MODEL_ID_CACHE.clear()


# ---------------------------------------------------------------------------
# The exclusion has to be earned, not assumed
# ---------------------------------------------------------------------------


def test_pool_width_does_not_change_the_output_bytes(tmp_path):
    """``workers`` is excluded from the identity on the ground that it cannot
    change output. That is a claim about this code, so it is measured."""
    runs = [_run(tmp_path, f"w{n}", workers=n) for n in (1, 2, 6, 12)]
    corpus = {r.manifest.corpus_sha256 for r in runs}
    content = {r.log.content_sha256 for r in runs}
    assert len(corpus) == 1, f"pool width changed the corpus hash: {corpus}"
    assert len(content) == 1, f"pool width changed the log content: {content}"


# ---------------------------------------------------------------------------
# Round-2 F-4 — resume must key on the identity that validates what it replays
# ---------------------------------------------------------------------------


def test_the_resume_key_is_the_run_identity_not_a_hand_picked_subset():
    """Round-2 F-4a, at the unit.

    ``_resume_identity`` named seven fields by hand, and the list went stale
    the day amendment A-04 added ``RunConfig.limits``. Codex's probe: two
    configs differing in timeout and recursion produced the identical resume
    identity, so a record cached under one set of limits was eligible for
    replay under another — and the manifest then honestly hashed the settings
    the documents were *not* produced under.

    Asserted as a projection identity rather than field-by-field: the point of
    the fix is that a field added to ``RunConfig`` tomorrow is covered without
    anyone remembering this function exists.
    """
    a = RunConfig(source_root="s", output_root="o", limits=_limits())
    for field, value in (
        ("file_timeout_ms", 30_000),
        ("retry_budget_ms", 1_000),
        ("recurse", False),
        ("ocr_model_id", "engine 1.0; models 0000"),
        ("xlsx_max_rows", 10),
        ("zip_max_depth", 1),
    ):
        b = dataclasses.replace(a, limits=_limits(**{field: value}))
        assert walker._resume_identity(a) != walker._resume_identity(b), (
            f"a journal written under a different {field} would be replayed "
            "into this run and hashed as if it belonged here")


def test_the_resume_key_moves_with_every_identity_field_of_the_config():
    """The class, enumerated. Every hashed field of ``RunConfig`` — present and
    future — must move the resume key, and every excluded one must not."""
    from dociq.contracts import _IDENTITY_EXCLUDED

    base = RunConfig(source_root="s", output_root="o", limits=_limits())
    for f in dataclasses.fields(RunConfig):
        if f.name in ("limits", "profiles"):
            continue  # each covered field-by-field elsewhere in this file
        if f.name in _IDENTITY_EXCLUDED:
            # `output_root` (A-08). The destination is not an input, and the
            # acceptance harness runs to two of them expecting one identity.
            assert walker._resume_identity(base) == walker._resume_identity(
                dataclasses.replace(base, **{f.name: "somewhere-else"})), (
                f"RunConfig.{f.name} is excluded from identity but still moves "
                "the resume key")
            continue
        current = getattr(base, f.name)
        if isinstance(current, bool):
            other = not current
        elif isinstance(current, int):
            other = current + 1
        elif isinstance(current, str):
            other = current + "-x"
        elif current is None:
            other = "something"
        else:
            continue
        moved = dataclasses.replace(base, **{f.name: other})
        assert walker._resume_identity(base) != walker._resume_identity(moved), (
            f"RunConfig.{f.name} changes output bytes but not the resume key")

    # And the one deliberate exclusion holds on this path too: resuming on a
    # different pool width must still work, because pool width must not change
    # output. A resume key stricter than the identity would be a different bug.
    wide = dataclasses.replace(base, limits=_limits(workers=1))
    assert walker._resume_identity(base) == walker._resume_identity(wide)


def test_a_journal_written_under_different_limits_is_refused(tmp_path):
    """The same finding end to end, through the file the pipeline actually
    reads. A unit-level identity mismatch is only interesting if
    ``_load_resume`` acts on it."""
    from dociq.contracts import DocumentRecord

    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / "out"),
        ocr_engine_version=ex.ocr_engine_version(),
        limits=walker.effective_limits(walker.WalkOptions(ocr_enabled=False)),
    )
    # A journal written and left behind — which is the state resume exists for.
    # A run that COMPLETES discards its journal, so running one here would
    # leave nothing to replay and the test would pass vacuously.
    journal = walker._ResumeWriter(cfg, True)
    journal.add("a.txt", [DocumentRecord(
        doc_id="", rel_path="a.txt", filename="a.txt", sha256="0" * 64,
        size_bytes=1, ext=".txt")])
    journal.close(discard=False, output_root=tmp_path / "out")

    assert walker._load_resume(cfg), "the fixture wrote no replayable journal"

    ocr_off_elsewhere = dataclasses.replace(
        cfg, limits=dataclasses.replace(cfg.limits, ocr_model_id="other-engine"))
    assert walker._load_resume(ocr_off_elsewhere) == {}, (
        "a journal produced by different OCR model bytes was replayed")

    capped = dataclasses.replace(
        cfg, limits=dataclasses.replace(cfg.limits, xlsx_max_rows=5))
    assert walker._load_resume(capped) == {}, (
        "a journal produced under different content caps was replayed")


def test_the_pipeline_hands_the_walk_the_configuration_it_will_record(tmp_path):
    """Round-2 F-4a's other half.

    The identity fix is worth nothing if the walk is still handed the caller's
    original config: the journal would be keyed on a configuration with no
    limits, no resolved OCR engine and no master-index snapshot, while the
    deliverables record the completed one. So this asserts the two are the same
    object's worth of information — everything except the Bates pattern, which
    is Stage 3's output and cannot exist before Stage 1.
    """
    seen: dict[str, RunConfig] = {}
    real = walker.run

    def spy(config, opts=None, notes=None):
        seen["walk"] = config
        return real(config, opts, notes)

    import dociq.pipeline as pl

    original = pl.walker.run
    pl.walker.run = spy
    try:
        outcome = _run(tmp_path)
    finally:
        pl.walker.run = original

    walked = seen["walk"]
    recorded = outcome.result.config
    assert walked.limits is not None, "the walk ran with no effective limits"
    assert walked.limits == recorded.limits
    assert walked.ocr_engine == recorded.ocr_engine
    assert walked.ocr_engine_version == recorded.ocr_engine_version
    assert walked.master_index == recorded.master_index

    # Two deliberate carve-outs, and only two. `bates_pattern` is Stage 3's
    # output. `profile_id`/`profile_version` resolve the profile LIBRARY, which
    # Stage 4 resolves per document — see the test below for why the walk must
    # NOT be told about it.
    def _comparable(cfg):
        return dataclasses.replace(
            cfg, bates_pattern=None)

    assert _comparable(walked) == _comparable(recorded), (
        "the walk ran under a different configuration than the run recorded")



def test_every_deadline_limit_is_an_exactly_represented_integer():
    """The class: no float, and no lossy unit, may reach an identity field.
    Enumerated over the dataclass so a future deadline field is covered."""
    limits = walker.effective_limits(walker.WalkOptions(file_timeout_s=1.1))
    for f in dataclasses.fields(EffectiveLimits):
        value = getattr(limits, f.name)
        assert not isinstance(value, float), (
            f"EffectiveLimits.{f.name} is a float; Principle 5 bars floats "
            "from identity fields")
        if "timeout" in f.name or "budget" in f.name:
            assert f.name.endswith("_ms"), (
                f"EffectiveLimits.{f.name} is a deadline recorded in a unit "
                "coarser than the value it records (amendment A-08)")


# ---------------------------------------------------------------------------
# B-R2-2 / A-19 — the input that DECIDES WHICH PAGES DROP must move the identity
#
# A-08 put the ordered profile set in the run identity because profiles decided
# which pages dropped, and proved it with two measured counterexamples: edit a
# second profile's rule without bumping its version, or swap two profiles'
# precedence, and the recorded identity stayed byte-identical while the corpus
# hash moved and pages went KEEP → DROP.
#
# **D-35 deleted that mechanism** (commit 4092f76). ``dociq.profiles.apply`` is
# gone; a profile's DROP rule now drops nothing, and the pipeline reports it as
# an inert input rather than applying it. Both of A-08's counterexamples are
# therefore no longer counterexamples — measured on this branch, two runs that
# differ only in profile content or profile order emit a BYTE-IDENTICAL
# deterministic file set.
#
# A-08's PRINCIPLE is untouched and is carried by A-19: the input that decides
# which pages drop must move the identity. That input is now the set of
# APPROVED OMISSIONS (D-34), plus the template they were given against and the
# project tokens that decide which family a label normalizes to. So the two
# counterexamples below are rebuilt on approvals — approve an omission, and
# change WHO approved it — and the profile tests are kept, repointed at the
# guarantee that survived: profile snapshots are still hashed run inputs, and
# they are still recorded in full and by content.
# ---------------------------------------------------------------------------


def _profile(pid: str, version: str, *, header: str = "", drop: str | None = None):
    from dociq.contracts import Disposition

    rules = ()
    if drop is not None:
        rules = (SectionRule(rule_id=f"{pid}-drop", pattern=drop,
                             disposition=Disposition.DROP, label="dropped",
                             notes="test fixture; approved by the test"),)
    return FormatProfile( version=version, display_name=pid,
        header_patterns=(header,) if header else (),
        section_rules=rules)


def _profiled_run(tmp_path, name, profiles):
    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / name),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(cfg, pipeline.PipelineOptions(
        walk=walker.WalkOptions(ocr_enabled=False, resume=False),
        profiles=tuple(profiles), stamp=STAMP,
        write_workbook=False, write_summary_pdf=False, write_package=False))


# The one template family the fixture corpus exercises: three pages of it, all
# placed by Tier 3, all offered. Approving it turns 0 dropped pages into 3.
FIXTURE_FAMILY = "progress-photographs"


def _omission(approved_by: str = "j.long", family: str = FIXTURE_FAMILY):
    from dociq.sections.model import ApprovedOmission
    from dociq.sections.templates import PROGRESS_REPORT

    return ApprovedOmission(
        family_id=family,
        approved_by=approved_by,
        approved_at="2026-08-17T00:00:00Z",
        matter=APPROVAL_MATTER,
        matter_root=matter_key(str(FIXTURES)),
        template_id=PROGRESS_REPORT.template_id,
        template_version=PROGRESS_REPORT.version,
    )


APPROVAL_MATTER = "fixture corpus"
"""The matter these runs are for, and the matter their approvals are stamped
with. One constant rather than two strings, because Stage 4 now compares them
and a typo would read as "the approval did not apply here" rather than as a
typo."""


def _approved_run(tmp_path, name, approvals=()):
    from dociq.sections.templates import PROGRESS_REPORT

    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / name),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(cfg, pipeline.PipelineOptions(
        walk=walker.WalkOptions(ocr_enabled=False, resume=False),
        # Named, and named to match the approvals. Stage 4 refuses an approval
        # given on a different matter (Codex B-2), and an unnamed matter is
        # refused outright rather than compared against "" — a defaulted matter
        # would be a silent bypass of the check.
        matter_name=APPROVAL_MATTER,
        template=PROGRESS_REPORT, approvals=tuple(approvals), stamp=STAMP,
        write_workbook=False, write_summary_pdf=False, write_package=False))


def test_approving_an_omission_moves_the_identity(tmp_path):
    """A-19's counterexample, measured end to end — A-08's finding on the input
    that replaced the one A-08 was about.

    Two runs over one folder, identical in every other recorded term. One of
    them is missing a section. Without ``RunConfig.omissions`` in the identity
    they would report the same ``run_identity_sha256`` over different evidence,
    which is the uncheckable claim B-2 was raised to end — the same collision,
    one design generation later.

    No attacker model, and no unusual configuration: this is one expert clicking
    one lever between two runs of the same matter.
    """
    none = _approved_run(tmp_path, "none")
    approved = _approved_run(tmp_path, "approved", (_omission(),))

    assert none.result.pages_dropped == 0
    assert approved.result.pages_dropped > 0, (
        "the approval dropped nothing, so this measures nothing — the fixture "
        "corpus must still recognize the family being approved")
    assert none.manifest.deterministic != approved.manifest.deterministic, (
        "the approval did not actually change the emitted evidence")

    assert run_identity(none.result.config) != run_identity(approved.result.config), (
        "an approved omission that removed pages from the deliverable left the "
        "run identity unchanged — the determinism claim would assert sameness "
        "the bytes do not support")
    assert none.manifest.run_identity_sha256 != approved.manifest.run_identity_sha256

    # And the identity records the approval itself, not merely that one existed.
    snaps = approved.result.config.omissions
    assert [s.family_id for s in snaps] == [FIXTURE_FAMILY]
    assert snaps[0].approved_by == "j.long"
    assert snaps[0].matter == APPROVAL_MATTER


def test_changing_who_approved_the_omission_moves_the_identity(tmp_path):
    """The second half of A-19, and the one that narrows the claim on purpose.

    Same folder, same family, same number of pages dropped — a different person
    approved it. ``OmissionSnapshot`` hashes ``approved_by`` and ``approved_at``
    deliberately: the drop log NAMES the approver, so the two runs are not
    byte-identical, and a claim that had to pretend otherwise would be the wrong
    claim to keep. Recording the person is the whole of D-34.

    Measured rather than argued — the assertion below is that the emitted files
    really do differ, not just the hash of the configuration.
    """
    a = _approved_run(tmp_path, "by-long", (_omission("j.long"),))
    b = _approved_run(tmp_path, "by-other", (_omission("a.other"),))

    assert a.result.pages_dropped == b.result.pages_dropped > 0, (
        "the two runs dropped different amounts, so this is not the "
        "approver-only counterexample it claims to be")
    assert a.manifest.deterministic != b.manifest.deterministic, (
        "the approver reaches no emitted file, so the determinism claim would "
        "not need to cover it — check the drop log before relaxing this")

    assert run_identity(a.result.config) != run_identity(b.result.config), (
        "two runs whose deliverables name different approvers reported one "
        "identity")
    assert a.manifest.run_identity_sha256 != b.manifest.run_identity_sha256


def test_the_identity_covers_every_input_that_decides_a_disposition(tmp_path):
    """The CLASS, enumerated, so the next A-19 fails here rather than in review.

    A-08 found one such input outside the identity and A-19 found four more, and
    both times the pattern was the same: a field is added to
    :class:`RunConfig`, the projection picks it up, and something else that
    describes the identity is left behind. So this names all four of A-19's
    fields, asserts each is hashed, and asserts the manifest's own prose claim
    names them too — which is where the omission actually was on 2026-08-17.
    """
    from dociq.contracts import _IDENTITY_EXCLUDED

    a19 = ("omissions", "project_tokens",
           "section_template_id", "section_template_version")
    declared = {f.name for f in dataclasses.fields(RunConfig)}
    assert set(a19) <= declared, (
        f"amendment A-19's fields are not on RunConfig: {set(a19) - declared}")
    for name in a19:
        assert name not in _IDENTITY_EXCLUDED, (
            f"RunConfig.{name} decides which pages drop and is excluded from "
            f"the identity")

    from dociq.contracts import OmissionSnapshot

    base = RunConfig(source_root="s", output_root="o")
    changes = {
        "omissions": (OmissionSnapshot(
            family_id=FIXTURE_FAMILY, approved_by="j.long",
            approved_at="2026-08-17T00:00:00Z", matter=APPROVAL_MATTER,
            matter_root=matter_key(str(FIXTURES)),
            template_id="progress-report", template_version="1"),),
        "project_tokens": ("MV32",),
        "section_template_id": "progress-report",
        "section_template_version": "1",
    }
    for name, value in changes.items():
        other = dataclasses.replace(base, **{name: value})
        assert run_identity(base) != run_identity(other), (
            f"RunConfig.{name} does not move the run identity, so two runs "
            f"that disposed of pages differently would report the same one")

    # The prose claim has to name them too. This is the half that was wrong:
    # `run_identity` hashed all four from 4092f76 and IDENTITY_NOTE described an
    # identity covering neither the approvals nor the template.
    for named in ("APPROVED OMISSION", "approver", "SECTION TEMPLATE",
                  "PROJECT TOKENS"):
        assert named in mf.IDENTITY_NOTE, (
            f"the manifest's claim does not name {named!r}, so it describes an "
            f"identity narrower than the one it publishes — B-2's defect, on "
            f"A-19's fields")




def test_a_run_nobody_ruled_on_records_an_empty_omission_set(outcome):
    """The A-19 sibling of the test above, and the state every freshly-installed
    DocIQ is in.

    D-34: a template ships unengaged. "Nobody approved anything" must be
    recorded as an empty set, not as an approval attributed to the operator who
    happened to run the tool — the approver field holding a fiction is the exact
    failure the ruling was made to prevent, and the identity would carry it.
    """
    assert outcome.result.config.omissions == ()
    assert outcome.result.pages_dropped == 0


# ---------------------------------------------------------------------------
# B-R2-2 — one persisted, authoritative run identity
# ---------------------------------------------------------------------------


def test_the_manifest_persists_the_identity_its_claim_describes(outcome):
    """No durable ``run_identity_sha256`` existed, so "which projection is the
    run identity" had no answer on disk — and four parts of the system gave
    different ones."""
    data = json.loads(
        (outcome.layout.root / mf.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    persisted = data["run_identity_sha256"]
    assert persisted and len(persisted) == 64
    assert persisted == run_identity(outcome.result.config)
    assert persisted == outcome.manifest.run_identity_sha256


def test_the_log_and_the_manifest_agree_on_the_run_identity(outcome):
    """Two artifacts, one number. If they can disagree, neither is authority."""
    log = json.loads(outcome.layout.processing_log.read_text(encoding="utf-8"))
    manifest = json.loads(
        (outcome.layout.root / mf.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert log["content"]["run_identity_sha256"] == manifest["run_identity_sha256"]


def test_the_identity_survives_a_change_of_destination(tmp_path):
    """The mismatch B-R2-2 names, asserted as behavior.

    ``RunConfig`` hashing included ``output_root``, the manifest's claim said
    the output folder was part of the identity, and the criterion-7 harness
    ran the same corpus to two different folders and required one identity.
    Those cannot all be true. The destination is where evidence is written.
    """
    a = _run(tmp_path, "dest-a")
    b = _run(tmp_path, "dest-b")
    assert a.result.config.output_root != b.result.config.output_root
    assert run_identity(a.result.config) == run_identity(b.result.config)
    assert a.manifest.run_identity_sha256 == b.manifest.run_identity_sha256
    assert a.manifest.corpus_sha256 == b.manifest.corpus_sha256
    assert a.log.content_sha256 == b.log.content_sha256, (
        "the hashed log content differed between two destinations")
