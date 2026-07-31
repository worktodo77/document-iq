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
from dociq.contracts import EffectiveLimits, RunConfig, content_hash
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
    assert limits.retry_budget_s == round(walker._RETRY_BUDGET_S)


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
        zip_max_members=2000, zip_max_depth=3, file_timeout_s=3600,
        retry_max=500, retry_budget_s=1800, recurse=True,
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
        ("file_timeout_s", 30),
        ("retry_max", 1),
        ("retry_budget_s", 1),
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
