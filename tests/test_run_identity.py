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
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from dociq import pipeline
from dociq.contracts import (
    EffectiveLimits,
    RunConfig,
    content_hash,
    run_identity,
)
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
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
    for named in ("profile", "master-index", "OCR confidence threshold",
                  "OCR engine", "Bates", "row caps", "ZIP", "per-file timeout",
                  "retry", "recursed", "OCR model identity"):
        assert named in identity, named
    assert "EXCLUDED" in identity and "workers" in identity

    # B-R2-2. The claim has to describe what is ACTUALLY hashed, and the two
    # things it previously got wrong are asserted by name.
    assert "ORDERED" in identity and "profile_hash" in identity, (
        "the claim still describes the identity as profile id and version, "
        "which is not the input Stage 4 uses")
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
            cfg, bates_pattern=None, profile_id=None, profile_version=None)

    assert _comparable(walked) == _comparable(recorded), (
        "the walk ran under a different configuration than the run recorded")


def test_the_walk_does_not_stamp_a_profile_onto_documents_it_did_not_claim(tmp_path):
    """Why ``profile_id`` stays OUT of the pre-walk configuration.

    ``walker._record`` stamps the walk config's profile onto every
    ``DocumentRecord``, and Stage 4 overwrites that stamp only on documents a
    profile actually CLAIMED — an unclaimed document keeps what it arrived
    with. Resolving ``PipelineOptions.profiles[0]`` into the pre-walk config
    (the obvious tidy-up while fixing F-4a, and one this package started to
    make) would therefore label every unclaimed document with a profile that
    did not match it: a false statement in the document index and in hashed
    content both.

    "What the run was configured with" and "what matched this document" are two
    different facts, and only Stage 4 knows the second.
    """
    from dociq.profiles.model import FormatProfile

    never_matches = FormatProfile(
        profile_id="mpr-monthly",
        version="1.0",
        display_name="Monthly progress report",
        header_patterns=("THIS STRING APPEARS IN NO FIXTURE WHATSOEVER",),
    )
    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(tmp_path / "out"),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    outcome = pipeline.run(cfg, pipeline.PipelineOptions(
        walk=walker.WalkOptions(ocr_enabled=False, resume=False),
        profiles=(never_matches,),
        stamp=STAMP,
    ))

    claimed = [d.rel_path for d in outcome.result.documents
               if d.profile_id == "mpr-monthly"]
    assert not claimed, (
        "documents no profile matched were stamped with the profile anyway: "
        + repr(claimed[:5]))
    # The run configuration still records the library that was supplied — that
    # IS true of the run, and it is where the fact belongs.
    assert outcome.result.config.profile_id == "mpr-monthly"


@pytest.mark.parametrize("a,b", [(1.1, 1.4), (0.5, 0.9), (3599.4, 3599.6)])
def test_deadlines_that_differ_are_recorded_differently(a, b):
    """Round-2 F-4b. ``round(seconds)`` collapsed 1.1 s and 1.4 s onto the same
    identity while abandoning different files — a determinism collision inside
    the field A-04 added to close one. Integer milliseconds keep the capability
    and remove the collision (amendment A-08)."""
    la = walker.effective_limits(walker.WalkOptions(file_timeout_s=a))
    lb = walker.effective_limits(walker.WalkOptions(file_timeout_s=b))
    assert la.file_timeout_ms != lb.file_timeout_ms, (
        f"{a}s and {b}s recorded the same deadline")
    assert la != lb
    assert content_hash(RunConfig(source_root="s", output_root="o", limits=la)) \
        != content_hash(RunConfig(source_root="s", output_root="o", limits=lb))
    assert walker._resume_identity(
        RunConfig(source_root="s", output_root="o", limits=la)
    ) != walker._resume_identity(
        RunConfig(source_root="s", output_root="o", limits=lb)
    )


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
# B-R2-2 — the ordered profile set IS the input, and the identity must say so
# ---------------------------------------------------------------------------


def _profile(pid: str, version: str, *, header: str = "", drop: str | None = None):
    from dociq.contracts import Disposition
    from dociq.profiles.model import FormatProfile, SectionRule

    rules = ()
    if drop is not None:
        rules = (SectionRule(rule_id=f"{pid}-drop", pattern=drop,
                             disposition=Disposition.DROP, label="dropped",
                             notes="test fixture; approved by the test"),)
    return FormatProfile(
        profile_id=pid, version=version, display_name=pid,
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


def test_editing_a_later_profile_without_a_version_bump_moves_the_identity(
    tmp_path,
):
    """Codex round-2 B-R2-2's counterexample, verbatim and end to end.

    Keep profile 1's id and version unchanged, alter profile 2's rule, run the
    same corpus. Before A-08 the recorded identity was byte-identical while the
    corpus hash moved and pages went KEEP to DROP: the manifest asserted "same
    run identity" over evidence that was not the same.

    No attacker model is needed, and that matters — nothing enforces that an
    edited profile gets a new version, so this is the ordinary way a profile
    library drifts between runs.
    """
    p1 = _profile("p1", "1.0", header="NO SUCH HEADER ANYWHERE")
    p2_before = _profile("p2", "2.0", drop="MATCHES NOTHING AT ALL")
    p2_after = _profile("p2", "2.0", drop=r"Daily")

    a = _profiled_run(tmp_path, "a", [p1, p2_before])
    b = _profiled_run(tmp_path, "b", [p1, p2_after])

    assert a.result.config.profile_id == b.result.config.profile_id == "p1"
    assert a.result.config.profile_version == b.result.config.profile_version
    assert a.manifest.corpus_sha256 != b.manifest.corpus_sha256, (
        "the fixture did not actually change the evidence")
    assert a.result.pages_dropped != b.result.pages_dropped

    assert run_identity(a.result.config) != run_identity(b.result.config), (
        "a profile edit that changed the emitted evidence left the run "
        "identity unchanged — the determinism claim would assert sameness the "
        "bytes do not support")
    assert a.manifest.run_identity_sha256 != b.manifest.run_identity_sha256


def test_swapping_profile_precedence_moves_the_identity(tmp_path):
    """The order half of B-R2-2, with every profile's CONTENT untouched.

    ``apply_profiles`` claims a document with the FIRST profile whose header
    patterns match, so precedence alone decides which rules a document is
    subject to. The first profile is held fixed here, because the old config
    recorded only ``profiles[0]`` and would otherwise appear to notice.
    """
    fixed = _profile("p0", "1.0", header="NO SUCH HEADER ANYWHERE")
    x = _profile("px", "1.0", drop=r"Daily")
    y = _profile("py", "1.0", drop=r"NOTICE")

    c = _profiled_run(tmp_path, "c", [fixed, x, y])
    d = _profiled_run(tmp_path, "d", [fixed, y, x])

    assert c.result.config.profile_id == d.result.config.profile_id == "p0"
    assert c.manifest.corpus_sha256 != d.manifest.corpus_sha256, (
        "the fixture did not actually change the evidence")
    assert run_identity(c.result.config) != run_identity(d.result.config), (
        "precedence order changed which pages dropped but not the identity")

    # And the recorded snapshots are the ordered set, not a set.
    assert [s.profile_id for s in c.result.config.profiles] == ["p0", "px", "py"]
    assert [s.profile_id for s in d.result.config.profiles] == ["p0", "py", "px"]


def test_every_profile_in_the_library_is_snapshotted_by_content(tmp_path):
    """The class: not just the first, and not just by name.

    Enumerated over the whole library so a future change that records only some
    of them fails here.
    """
    profiles = [
        _profile("p0", "1.0", header="NO SUCH HEADER ANYWHERE"),
        _profile("p1", "1.1", drop=r"Daily"),
        _profile("p2", "2.5", drop=r"NOTICE"),
    ]
    outcome = _profiled_run(tmp_path, "lib", profiles)
    snaps = outcome.result.config.profiles

    assert len(snaps) == len(profiles), "the library was not recorded in full"
    for snap, prof in zip(snaps, profiles):
        assert snap.profile_id == prof.profile_id
        assert snap.version == prof.version
        assert snap.profile_hash == prof.profile_hash, (
            "the snapshot records a name, not the content that decides drops")
        assert len(snap.profile_hash) == 64


def test_an_unprofiled_run_records_an_empty_profile_set(outcome):
    """The ordinary case (section 4 Stage 4) stays honest: no profiles, not a
    fabricated one."""
    assert outcome.result.config.profiles == ()


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
