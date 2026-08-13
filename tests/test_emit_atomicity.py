"""Stage 5 writes to staging and swaps — the scheduled Sprint-1 gap.

``docs/reviews/sprint-1_merge_readiness.md``, NOT PROVEN item 8: *"A crash during
emit, after the purge, can still leave a partly-replaced output folder. Distinct
from the aborted-walk case that B-1 closed; it needs write-to-staging-then-swap."*

The failure it names is specific and worth restating, because the tests below are
shaped by it rather than by the words "atomic". A re-run used to delete the
previous run's deliverables and then write the new ones into the same folder. A
crash in between — and emit is where a full-corpus run spends real time — left
``clean_text/`` holding some of run 1's documents and some of run 2's, under Doc
IDs that need not agree, with an ``output_manifest.json`` from run 1 describing a
set that no longer exists. Nothing on disk said so.

Three properties are proven here:

1. **Crash-during-emit leaves the previous run complete.** Not "mostly complete",
   not "the manifest is stale": byte-for-byte the folder run 1 left.
2. **An interrupted swap is rolled forward**, so the exposed state is the moves
   themselves rather than the whole of emit — and the next run finishes them
   before it reads anything.
3. **The staging path is in no hashed artifact** (criterion 7). Two runs whose
   staging directories are at different absolute paths produce one corpus hash,
   one log ``content`` hash, and byte-identical adjacent files. This is the check
   that would have caught ``output_root`` inside hashed log content in Sprint 1,
   and it is the one this change could most plausibly break.

**What this file does NOT prove, and where that lives instead.** Codex review #2
found two things these three properties are silent about, both in
``tests/test_publication_gate.py``: that a set which fails §4 Stage 6 must never
be marked ready at all (B-1 — the tests here all start from a run whose gates
were green), and that an interrupted swap whose MARKER is unreadable must fail
closed rather than roll forward with an empty supersede list (B-2 — property 2
above only ever exercises a readable one).

**D-31 (2026-08-05) — the swap no longer deletes before it publishes**, and
section 4 below is rewritten against that. The old design removed the previous
run's deliverables and then moved the staged ones in, so every failure mode was
"half-deleted" and three consecutive review rounds each found a new window inside
the previous round's fix (B-1/B-2, then B-4/B-5, then B-6). The swap now RENAMES
the current set into ``.dociq/<aside>/``, renames the staged set into place, and
deletes only after that. A fourth property is therefore proven here:

4. **Nothing under the matter root is ever deleted or overwritten by the swap or
   by recovery.** Not "the deletion is retried and proven" — there is no
   deletion. That is asserted as a CLASS, by auditing every destructive
   filesystem call made during a swap, rather than one failure mode at a time.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from dociq import pipeline
from dociq.contracts import RunConfig
from dociq.emit import paths as emit_paths
from dociq.emit.paths import OutputLayout
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import OperatorStamp
from dociq.verify import manifest as mf

from .conftest import FIXTURES

STAMP = OperatorStamp("test", "2026-07-30T00:00:00Z", "test-host")


def _run(out, **kw):
    cfg = RunConfig(
        source_root=str(FIXTURES),
        output_root=str(out),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        cfg,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False),
            matter_name="fixture corpus",
            stamp=STAMP,
            **kw,
        ),
    )


def _fingerprint(root) -> dict[str, str]:
    """Every file in the matter folder, hashed. DocIQ's own run state is
    excluded — it is scratch, not a deliverable, and the manifest excludes it for
    the same reason."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{emit_paths.STATE_DIRNAME}/"):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class Boom(RuntimeError):
    """A crash in the middle of emit, at a point chosen to be maximally
    destructive under the old write path: after the purge, after some of
    ``clean_text/`` is written, before the index and the log."""


# ---------------------------------------------------------------------------
# 1. The crash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "victim",
    [
        "dociq.pipeline.write_sources_json",
        "dociq.pipeline.write_index_csv",
        "dociq.pipeline.write_processing_log",
        "dociq.pipeline.build_upload_package",
    ],
)
def test_a_crash_during_emit_leaves_the_previous_run_untouched(
    tmp_path, monkeypatch, victim
):
    """FAIL-BEFORE: with Stage 5 writing straight into the matter folder (the
    Sprint-1 shape), every one of these four crash points leaves the folder
    holding a purged, half-written mixture — watched red before the fix by
    reverting Stage 5 to the destination layout.

    The four points are an ENUMERATION of the class, not one repro: one per
    emitter that writes a deliverable after the first bytes of ``clean_text/``
    land, so a future emitter added between any two of them is covered by its
    neighbours.
    """
    out = tmp_path / "matter"
    first = _run(out)
    assert first.published
    before = _fingerprint(out)
    assert before, "the first run wrote nothing to compare against"

    module, name = victim.rsplit(".", 1)
    monkeypatch.setattr(
        f"{module}.{name}",
        lambda *a, **k: (_ for _ in ()).throw(Boom(name)),
    )
    with pytest.raises(Boom):
        _run(out)

    assert _fingerprint(out) == before, (
        f"a crash in {name} changed the matter folder; the previous run's "
        "deliverables must survive a failed re-run byte for byte"
    )
    # And the wreckage is not left where a reader could mistake it for output.
    assert not emit_paths.pending_swap(OutputLayout.at(out)), (
        "a run that never finished staging must not leave a readiness marker"
    )


def test_the_next_run_discards_an_unfinished_staging_directory(tmp_path, monkeypatch):
    """The half-written set from the crash above is not a head start."""
    out = tmp_path / "matter"
    _run(out)
    monkeypatch.setattr(
        "dociq.pipeline.write_index_csv",
        lambda *a, **k: (_ for _ in ()).throw(Boom("index")),
    )
    with pytest.raises(Boom):
        _run(out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    assert staging.is_dir(), "the crashed run left no staging to discard"

    monkeypatch.undo()
    third = _run(out)
    assert third.published
    assert not staging.exists(), "staging survived a completed run"
    assert third.ok


# ---------------------------------------------------------------------------
# 2. The interrupted swap
# ---------------------------------------------------------------------------


def test_an_interrupted_swap_is_rolled_forward_by_the_next_run(tmp_path):
    """Stage a complete set, mark it ready, move ONE file, then stop — the
    mid-swap state — and prove the next run completes it before doing anything
    else, rather than reading a folder that is half of each run."""
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)

    # A second run's complete set, captured before the swap.
    second_out = tmp_path / "second"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    expected = _fingerprint(staging)
    emit_paths.mark_ready(layout, ("document_index.csv",))

    # The interruption: one file moved, then nothing.
    moved = staging / "sources.json"
    moved.replace(out / "sources.json")
    assert emit_paths.pending_swap(layout)

    recovered = emit_paths.recover_pending(layout)
    assert "document_index.csv" in recovered
    assert not emit_paths.pending_swap(layout), "the marker outlived the swap"
    assert not staging.exists()

    after = _fingerprint(out)
    for rel, digest in expected.items():
        assert after.get(rel) == digest, f"{rel} did not survive the roll-forward"


def test_the_roll_forward_happens_before_the_next_run_reads_anything(tmp_path):
    """It is the first statement of :func:`dociq.pipeline.run` for a reason: the
    ledger the D-04 renumbering check reads lives in the folder a pending swap is
    still replacing.

    **This test used to pass with recovery switched off.** A rehearsal review
    replaced ``recover_pending``'s call site with ``recovered = ()`` — nothing
    rolled forward at all — and it stayed green. Two reasons, both in the setup:
    it staged a single bogus ``sources.json`` and called ``mark_ready`` with an
    EMPTY superseded list, so there was nothing for recovery to move; and it
    asserted only that a disclosure APPEARED, which is gated on a marker having
    been present at start rather than on recovery having done anything. A run
    that recovered nothing and then simply succeeded on its own was
    indistinguishable from a run that rolled a swap forward.

    It now stages a complete set the way the sibling test above does, and
    asserts the disclosure NAMES what was rolled forward."""
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)

    # A complete set from a real run, staged and marked ready — so recovery has
    # something real to move, and its absence is observable.
    second_out = tmp_path / "second"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    superseded = ("document_index.csv",)
    emit_paths.mark_ready(layout, superseded)
    assert emit_paths.pending_swap(layout)

    second = _run(out)
    assert second.published
    assert not emit_paths.pending_swap(layout)
    assert any("RECOVERED" in n for n in second.walk_notes.invocation), (
        "a swap completed by a later run must be disclosed, not silent"
    )
    payload = json.loads((out / "processing_log.json").read_text(encoding="utf-8"))
    assert "recovered_swap" in payload["run"]

    # The load-bearing part: the disclosure must name what was actually rolled
    # forward. With recovery disabled this is empty, and the test goes red.
    rolled = payload["run"]["recovered_swap"]
    assert rolled, (
        "the run disclosed a recovered swap that moved nothing — the "
        "disclosure is gated on a marker being present, not on recovery "
        "having happened, so this must assert what moved"
    )
    assert any(name in json.dumps(rolled) for name in superseded), (
        f"the recovery disclosure does not name the superseded deliverables "
        f"{superseded} it completed: {rolled!r}"
    )
    assert "recovered_swap" not in json.dumps(payload["content"]), (
        "an invocation fact reached the hashed content"
    )


# ---------------------------------------------------------------------------
# 3. Criterion 7 — the staging path is in nothing that is hashed
# ---------------------------------------------------------------------------


def test_two_destinations_produce_one_identity_and_identical_bytes(tmp_path):
    """The check that catches a staged path leaking into a hashed artifact.

    Two runs, two destinations, therefore two different staging paths. If the
    staging directory reached ``sources.json``, ``_hashes_of``'s relative keys,
    the log's hashed content or the upload package, exactly one of these
    assertions fails — and which one names where it leaked.
    """
    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")

    assert a.manifest.corpus_sha256 == b.manifest.corpus_sha256
    assert a.manifest.run_identity_sha256 == b.manifest.run_identity_sha256
    assert a.manifest.log_content_sha256 == b.manifest.log_content_sha256
    assert not a.manifest.unclassified and not b.manifest.unclassified

    assert a.manifest.deterministic == b.manifest.deterministic
    assert a.manifest.adjacent == b.manifest.adjacent, (
        "an adjacent deliverable differs between destinations"
    )
    assert mf.compare(a.manifest, b.manifest) == []

    for name in ("clean_text", "sources.json", "document_index.csv",
                 "upload_package", "doc_ids_issued.json"):
        left, right = tmp_path / "a" / name, tmp_path / "b" / name
        if left.is_file():
            assert left.read_bytes() == right.read_bytes(), name


def test_no_hashed_artifact_mentions_the_staging_directory(tmp_path):
    """Belt and braces, and it fails differently from the test above: a leak
    into a NON-compared artifact (the summary PDF, the workbook) is invisible to
    a two-destination hash comparison of the deterministic set."""
    out = tmp_path / "matter"
    _run(out)
    needles = (emit_paths.STAGING_DIRNAME.encode(),
               emit_paths.STATE_DIRNAME.encode())
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(out).as_posix()
        if rel.startswith(f"{emit_paths.STATE_DIRNAME}/"):
            continue
        blob = path.read_bytes()
        for needle in needles:
            assert needle not in blob, f"{rel} names DocIQ's own run state"


def test_the_state_directory_name_matches_the_walkers(tmp_path):
    """A copied constant is a constant that can drift. :mod:`dociq.emit` may not
    import :mod:`dociq.ingest`, so the two literals are checked here instead."""
    assert emit_paths.STATE_DIRNAME == walker.STATE_DIR


def test_the_upload_package_no_longer_survives_a_shrinking_re_run(tmp_path):
    """FAIL-BEFORE: with ``upload_package`` absent from ``_STALE_PATTERNS`` — as
    it was, on the reasoning that the emitter rebuilt it in place — the swap
    leaves the previous run's copies in the folder an operator uploads whole.

    The reasoning was true of the old write path and is withdrawn with it: the
    rebuild now happens in staging and never touches the destination.
    """
    out = tmp_path / "matter"
    _run(out)
    stray = out / "upload_package" / "LI-99999-not-from-this-run.txt"
    stray.write_text("evidence from another run\n", encoding="utf-8", newline="")

    _run(out)
    assert not stray.exists(), (
        "a previous run's upload_package file survived a re-run"
    )


# ---------------------------------------------------------------------------
# 4. DELETE-LAST — the swap renames, and recovery destroys nothing published
#    (D-31; the class B-1/B-2, B-4/B-5 and B-6 were instances of)
# ---------------------------------------------------------------------------


def _stage_a_second_set(tmp_path, out, superseded):
    """A complete second set in ``out``'s staging directory, marked ready.

    The set is a real run's output rather than hand-built files, because the
    property under test is about the swap moving a COMPLETE set over a
    superseded one — a synthetic staging directory would prove the same code
    path over evidence nobody would publish.
    """
    layout = OutputLayout.at(out)
    second_out = tmp_path / "second"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    emit_paths.mark_ready(layout, superseded)
    return layout, staging


def test_a_directory_removal_that_fails_cannot_publish_a_mixed_set(tmp_path):
    """FAIL-BEFORE (Codex review #2 fix round, B-4).

    One file inside a superseded DIRECTORY is held open, which is the ordinary
    Windows case this project's environment notes already document: an antivirus
    or backup agent has a handle on a file the swap is about to delete. The
    removal genuinely fails — no mock, a real ``PermissionError`` from a real
    open handle.

    With ``shutil.rmtree(path, ignore_errors=True)``, which is what this
    replaced, the swap absorbed that failure, recorded the directory as removed,
    moved the new package in beside the survivor and DELETED the readiness
    marker: a folder holding two builds with nothing left saying so. The
    assertions below are written against that state, so the test is red on the
    old code for the reason B-4 names rather than for an incidental one.
    """
    out = tmp_path / "matter"
    _run(out)
    package = out / "upload_package"
    assert package.is_dir(), "the fixture run did not produce a package to supersede"

    stale = package / "LI-99999-from-an-earlier-run.txt"
    stale.write_text("evidence from another run\n", encoding="utf-8", newline="")
    layout, staging = _stage_a_second_set(tmp_path, out, ("upload_package",))
    staged_now = _fingerprint(staging)

    held = stale.open("rb")  # the antivirus handle
    try:
        with pytest.raises(OSError) as excinfo:
            emit_paths.commit_staging(layout)
        assert str(stale) in str(excinfo.value) or "upload_package" in str(
            excinfo.value
        ), "the failure does not name the directory it could not remove"

        # The three facts B-4 is about, in the order they were violated.
        assert emit_paths.pending_swap(layout), (
            "the readiness marker was deleted over a superseded directory that "
            "is still on disk — nothing remains to disclose or repair the folder"
        )
        assert stale.exists(), "the fixture's premise (a surviving stale file) is gone"
        # Nothing moved. The new set is still whole, in staging, where a
        # roll-forward can still publish it as a set — rather than half in the
        # folder beside a superseded file that outlived its own removal.
        assert _fingerprint(staging) == staged_now, (
            "the new set was moved into the folder over a superseded directory "
            "that is still on disk — this is the mixed set"
        )
    finally:
        held.close()

    # Roll-forward is still possible: the marker survived, so the next run
    # finishes the swap the moment the lock is released.
    emit_paths.recover_pending(layout)
    assert not emit_paths.pending_swap(layout)
    assert not stale.exists(), "the roll-forward left the stale file behind"
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} did not survive the roll-forward"


def test_a_file_removal_that_fails_cannot_publish_a_mixed_set(tmp_path):
    """The FILE sibling of the test above, enumerated with it rather than after.

    ``unlink`` was already retried, so this passed before the B-4 fix — it is
    here because "the retried step and the unretried step behave the same way
    under a real lock" is the property, and a class fix that only ever proves
    the branch the reviewer named has not been shown to be a class fix.

    Under D-31 the step it exercises is a RENAME rather than an ``unlink``, and
    the lock is still real: Windows refuses to rename a file another process
    holds open. The property it asserts is unchanged and is the one that
    matters — a superseded deliverable that cannot leave the folder stops the
    swap with the marker still on disk.
    """
    out = tmp_path / "matter"
    _run(out)
    victim = out / "document_index.csv"
    assert victim.is_file()
    layout, staging = _stage_a_second_set(tmp_path, out, ("document_index.csv",))

    held = victim.open("rb")
    try:
        with pytest.raises(OSError):
            emit_paths.commit_staging(layout)
        assert emit_paths.pending_swap(layout), "the marker did not survive"
    finally:
        held.close()

    emit_paths.recover_pending(layout)
    assert not emit_paths.pending_swap(layout)


# --- D-31: the marker cannot authorize destroying a published set (B-6) -----


def _keep_the_marker_name(monkeypatch):
    """``unlink()`` returns and the marker's NAME survives.

    Codex's B-6 scenario verbatim: Windows delete-on-close semantics, an
    on-access scanner, or a filesystem shim lets the call return while the entry
    is still visible. Simulated by monkeypatch rather than by a real exclusive
    lock, because the state being reproduced — *the call succeeded and the name
    is still there* — is precisely the one a real lock does NOT produce (a real
    lock makes ``unlink`` raise, which this code already handles).
    """
    real = Path.unlink

    def keep(self, *a, **kw):
        if self.name == emit_paths.MARKER_NAME:
            return None
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", keep)


def test_a_marker_that_outlives_its_swap_deletes_nothing(tmp_path, monkeypatch):
    """FAIL-BEFORE (Codex review #2, second fix round, B-6).

    The swap completes and publishes the staged set; the marker's ``unlink()``
    returns while its name survives. On the OLD design that marker still carried
    the supersede list and nothing else, so the next ``recover_pending()`` read
    it, deleted the newly published files as superseded, found no staged
    replacement, removed the marker and returned. Codex's reproduction:
    ``after_first: new, marker=True`` then
    ``after_recovery: sources_exists=False, marker=False``.

    Under delete-last the same surviving marker authorizes nothing: it says
    ``published``, and independently of that the staging directory is empty, so
    there is nothing to publish and therefore nothing may be set aside. The
    assertion is the one Codex's reproduction violated — the published bytes are
    still there.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(
        tmp_path, out, ("sources.json", "document_index.csv"))
    staged_now = _fingerprint(staging)

    _keep_the_marker_name(monkeypatch)
    emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert emit_paths.pending_swap(layout), (
        "the fixture's premise is gone: the marker did not survive its unlink")
    published = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert published.get(rel) == digest, f"{rel} was not published"

    emit_paths.recover_pending(layout)

    assert _fingerprint(out) == published, (
        "a recovery driven by a stale marker changed the published set — this "
        "is B-6: the marker outlived the swap and authorized deleting the files "
        "the swap had just put in place")
    assert (out / "sources.json").is_file()
    assert not emit_paths.pending_swap(layout), (
        "the second recovery did not clear the stale marker")


def test_a_stale_pending_marker_beside_an_empty_staging_deletes_nothing(
        tmp_path):
    """The same class one step further out, and it does not need a phase field.

    A marker left at ``pending`` — hand-restored, restored by a backup agent,
    written by an older build — beside a staging directory holding nothing. The
    names on disk are the primary evidence and they say there is nothing to
    publish, so nothing may be set aside. This is the guard that would hold even
    if the recorded phase were wrong.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    published = _fingerprint(out)
    assert published

    emit_paths.mark_ready(layout, ("sources.json", "clean_text", "upload_package"))
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    assert emit_paths.pending_swap(layout)

    moved = emit_paths.recover_pending(layout)

    assert moved == (), f"a stale marker reported replacing {moved}"
    assert _fingerprint(out) == published, (
        "a stale pending marker moved the published set out of the folder")
    assert not emit_paths.pending_swap(layout)


# --- D-31: the class assertion — no destructive call reaches a deliverable --


class _DestructiveCallAudit:
    """Every filesystem-destroying call made while it is installed, by target.

    The class-level probe for D-31. The claim is not "this failure mode no
    longer deletes the published set" — it is that **the swap and its recovery
    contain no code path that deletes or overwrites anything under the matter
    root at all**. That is a property of the whole subsystem, so it is asserted
    over the calls it makes rather than over one scenario's outcome.

    ``os.replace`` is audited alongside the deletions because an overwrite IS a
    delete: it destroys whatever occupied the destination, with no name left to
    read afterwards. That is exactly the substitution D-31 forbids, and the swap
    used to publish with it.
    """

    def __init__(self):
        self.targets: list[str] = []

    def install(self, monkeypatch):
        import os as _os

        for mod, name, argno in (
            (shutil, "rmtree", 0),
            (_os, "remove", 0),
            (_os, "unlink", 0),
            (_os, "rmdir", 0),
            (_os, "replace", 1),  # the DESTINATION is what gets destroyed
        ):
            real = getattr(mod, name)

            def wrapper(*a, _real=real, _argno=argno, **kw):
                if len(a) > _argno:
                    self.targets.append(str(a[_argno]))
                return _real(*a, **kw)

            monkeypatch.setattr(mod, name, wrapper)

        real_unlink = Path.unlink

        def path_unlink(this, *a, **kw):
            self.targets.append(str(this))
            return real_unlink(this, *a, **kw)

        monkeypatch.setattr(Path, "unlink", path_unlink)

        real_replace = Path.replace

        def path_replace(this, target, *a, **kw):
            self.targets.append(str(target))
            return real_replace(this, target, *a, **kw)

        monkeypatch.setattr(Path, "replace", path_replace)

    def outside(self, root: Path) -> list[str]:
        """Targets under ``root`` that are NOT DocIQ's own state."""
        state = (root / emit_paths.STATE_DIRNAME).resolve()
        bad = []
        for t in self.targets:
            path = Path(t)
            if not path.is_absolute():
                continue
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue  # outside the matter folder entirely (a temp file)
            try:
                path.resolve().relative_to(state)
            except ValueError:
                bad.append(t)
        return bad


def test_the_swap_destroys_nothing_under_the_matter_root(tmp_path, monkeypatch):
    """FAIL-BEFORE: on the delete-first design this reports ``sources.json``,
    ``document_index.csv``, ``upload_package`` and every ``clean_text/*.txt``
    the previous run wrote — the supersede loop's ``unlink``/``rmtree`` and the
    publish loop's ``os.replace``. The count is the finding.

    The property is stated as an ENUMERATION of destructive primitives rather
    than as an outcome, so a future step that reaches for a different one is
    covered the day it is written.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(
        tmp_path, out,
        ("sources.json", "document_index.csv", "upload_package"))

    audit = _DestructiveCallAudit()
    audit.install(monkeypatch)
    emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert audit.targets, "the audit recorded nothing; it is not installed"
    assert audit.outside(out) == [], (
        "the swap deleted or overwrote something under the matter root that is "
        f"not DocIQ's own state: {audit.outside(out)}")


def test_recovery_destroys_nothing_under_the_matter_root(tmp_path, monkeypatch):
    """The same probe over RECOVERY, which is where B-6 lived.

    Recovery is the path a stale or hostile marker reaches, so it gets its own
    assertion rather than inheriting the swap's.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(
        tmp_path, out, ("sources.json", "upload_package"))

    # Interrupt mid-swap: one file set aside by hand, marker left as it is.
    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    aside.mkdir(parents=True, exist_ok=True)
    (out / "sources.json").rename(aside / "sources.json")

    audit = _DestructiveCallAudit()
    audit.install(monkeypatch)
    emit_paths.recover_pending(layout)
    monkeypatch.undo()

    assert audit.outside(out) == [], (
        f"recovery destroyed something published: {audit.outside(out)}")
    assert (out / "sources.json").is_file(), "the roll-forward did not finish"
    assert not emit_paths.pending_swap(layout)


# --- D-31: what each failure LEAVES, read from the names on disk ------------


def test_a_blocked_set_aside_leaves_the_previous_set_and_no_mixture(
        tmp_path, monkeypatch):
    """Step 1 fails partway. The matter folder must hold the previous run's
    files and NONE of the new run's — incomplete is allowed, mixed is not."""
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    superseded = ("sources.json", "document_index.csv")
    layout, staging = _stage_a_second_set(tmp_path, out, superseded)
    staged_now = _fingerprint(staging)

    real = emit_paths._rename_or_fail

    # The plan is acted on in sorted order, so `document_index.csv` moves and
    # `sources.json` is the one that cannot.
    def refuse_the_second(src, dst):
        if src.name == "sources.json":
            raise OSError("[WinError 32] held by another process")
        return real(src, dst)

    monkeypatch.setattr(emit_paths, "_rename_or_fail", refuse_the_second)
    with pytest.raises(OSError):
        emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert emit_paths.pending_swap(layout), "the marker did not survive"
    # Nothing of the new set is in the folder.
    assert _fingerprint(staging) == staged_now, "the new set was disturbed"
    assert (out / "sources.json").is_file(), (
        "the deliverable that could not be moved is gone — a rename that fails "
        "must destroy nothing")
    # What DID leave the folder is INTACT under `.dociq/`, moved not deleted.
    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    kept = aside / "document_index.csv"
    assert kept.is_file()
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == (
        before["document_index.csv"])
    # And the roll-forward finishes it.
    emit_paths.recover_pending(layout)
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} did not survive the roll-forward"
    assert not emit_paths.pending_swap(layout)


def test_a_blocked_publish_leaves_an_incomplete_set_never_a_mixed_one(
        tmp_path, monkeypatch):
    """Step 2 fails partway — the case D-31 is most pointed about.

    The previous set is entirely out of the folder by then, so what a reader
    sees is *some of the new run and none of the old*. That is incomplete and
    it is honest; the old design's equivalent state was some of each, under Doc
    IDs that need not agree, with nothing saying so.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    # NOT `tuple(sorted(before))`, which is what this said and what Codex named
    # in B-8: defining the plan as "everything that is there" assumes exactly the
    # completeness the production enumerator does not guarantee, so the test
    # never entered the branch that handles a name the plan missed. One
    # deliverable is deliberately withheld from the plan here — the same shape as
    # a file an older build wrote under a name this build no longer enumerates —
    # and the mixture assertions below are unchanged. They now have to be
    # satisfied by the pre-publish destination sweep rather than by the fixture.
    unplanned = "document_index.csv"
    assert unplanned in before, "the fixture's premise is gone"
    superseded = tuple(sorted(set(before) - {unplanned}))
    layout, staging = _stage_a_second_set(tmp_path, out, superseded)
    staged_now = _fingerprint(staging)
    assert unplanned in staged_now, (
        "the withheld name has no staged replacement, so it would never reach "
        "the occupant branch this test exists to cover")

    real = emit_paths._rename_or_fail
    state = {"published": 0}

    def refuse_after_two_publishes(src, dst):
        if str(src).startswith(str(staging)):
            state["published"] += 1
            if state["published"] > 2:
                raise OSError("[WinError 32] held by another process")
        return real(src, dst)

    monkeypatch.setattr(emit_paths, "_rename_or_fail", refuse_after_two_publishes)
    with pytest.raises(OSError):
        emit_paths.commit_staging(layout)
    monkeypatch.undo()

    landed = _fingerprint(out)
    assert landed, "nothing was published at all; the fixture proves nothing"
    assert len(landed) < len(staged_now), "the publish did not actually stop"
    for rel, digest in landed.items():
        assert staged_now.get(rel) == digest, (
            f"{rel} in the matter folder belongs to neither the staged set nor "
            "nothing — this is the mixture delete-last exists to forbid")

    # The whole previous set is intact, moved not modified.
    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    for rel, digest in before.items():
        kept = aside / rel
        assert kept.is_file(), f"{rel} is neither published nor set aside"
        assert hashlib.sha256(kept.read_bytes()).hexdigest() == digest

    emit_paths.recover_pending(layout)
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest
    assert not emit_paths.pending_swap(layout)


def test_a_blocked_cleanup_publishes_and_discloses_the_residue(
        tmp_path, monkeypatch):
    """Step 3 fails. This is a SUCCESS with a residue, not a failure.

    A lock on one file in a tree that has already been superseded must not turn
    a published run into a failed one — and the residue must not be silent,
    because nobody opens ``.dociq/``.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(tmp_path, out, ("sources.json",))
    staged_now = _fingerprint(staging)

    def refuse(path):
        if path.name.startswith(emit_paths.ASIDE_PREFIX):
            raise OSError("[WinError 32] held by another process")

    monkeypatch.setattr(emit_paths, "_remove_tree_or_fail", refuse)
    moved = emit_paths.commit_staging(layout)  # no exception
    monkeypatch.undo()

    assert "sources.json" in moved
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} was not published"
    assert not emit_paths.pending_swap(layout), (
        "a cleanup failure left the folder declared mid-swap after a "
        "successful publish")
    residue = emit_paths.superseded_residue(layout)
    assert residue, "the surviving set-aside tree was not disclosed"
    assert all(r.startswith(f"{emit_paths.STATE_DIRNAME}/") for r in residue)

    # The NEXT swap must not collide with it, and must not overwrite it.
    marker = emit_paths.mark_ready(layout, ("sources.json",))
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["aside"] != emit_paths.ASIDE_PREFIX, (
        "the next swap chose a set-aside name the residue already occupies")


# --- D-31: the marker is state read from disk, and is checked as such -------


@pytest.mark.parametrize("aside", [
    "../clean_text", "..", "C:/Windows", "clean_text", "sources.json",
    "superseded/../..", "", 7,
])
def test_a_marker_cannot_point_the_cleanup_outside_dociq(tmp_path, aside):
    """The set-aside name selects what gets DELETED at the end of the swap, so
    a hand-edited or corrupt one must not be able to name a deliverable.

    The sibling of ``_validate_superseded_entry`` pointed the other way, and
    enumerated with it rather than after it: one check governs what a marker can
    move, the other what it can move things INTO and later delete.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    before = _fingerprint(out)

    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME
    marker.write_text(json.dumps({
        "staging": emit_paths.STAGING_DIRNAME,
        "superseded": ["sources.json"],
        "aside": aside,
        "phase": emit_paths.PHASE_PENDING,
    }), encoding="utf-8", newline="")

    with pytest.raises(emit_paths.PendingSwapUnreadable):
        emit_paths.recover_pending(layout)
    assert _fingerprint(out) == before, "the folder was touched by a refusal"
    assert emit_paths.pending_swap(layout), "a refusal deleted the marker"


def test_a_marker_without_a_phase_or_an_aside_fails_closed(tmp_path):
    """The B-2 rule applied to the two fields D-31 added.

    Nothing has shipped, so a marker missing them is not an older format — it is
    a marker this code did not write, and guessing a phase would be guessing
    whether the previous set is still in the matter folder.
    """
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    marker = out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME

    for payload in (
        {"staging": "staging", "superseded": ["sources.json"]},
        {"staging": "staging", "superseded": [], "aside": "superseded"},
        {"staging": "staging", "superseded": [], "aside": "superseded",
         "phase": "halfway"},
    ):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(payload), encoding="utf-8", newline="")
        with pytest.raises(emit_paths.PendingSwapUnreadable):
            emit_paths.recover_pending(layout)


def test_a_locked_marker_does_not_fail_a_run_whose_set_is_published(
        tmp_path, monkeypatch):
    """FAIL-BEFORE, and it was found by ENUMERATION rather than by a reviewer.

    Steps 1-3 of the swap are designed so a failure below the publish is a
    disclosed residue rather than an error — the set-aside removal collects
    survivors and the staging removal is wrapped. The final ``marker.unlink``
    was **not** wrapped: ``_retry_io`` re-raised after eight attempts and nothing
    above ``commit_staging`` handles it. So a transient antivirus lock on
    ``staging_ready.json`` — the same condition every other step here absorbs —
    turned a run whose deliverables were fully published into a traceback.

    The published set is correct at that line, and nothing below it may say
    otherwise. The surviving marker is provably harmless: it says ``published``,
    and the next recovery reads that and touches nothing.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(tmp_path, out, ("sources.json",))
    staged_now = _fingerprint(staging)

    real = Path.unlink

    def lock_the_marker(self, *a, **kw):
        if self.name == emit_paths.MARKER_NAME:
            raise PermissionError(
                32, "[WinError 32] The process cannot access the file")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", lock_the_marker)
    moved = emit_paths.commit_staging(layout)  # must NOT raise
    monkeypatch.undo()

    assert "sources.json" in moved
    after = _fingerprint(out)
    for rel, digest in staged_now.items():
        assert after.get(rel) == digest, f"{rel} was not published"

    # The surviving marker is harmless, and the next recovery says so by doing
    # nothing to the folder and then clearing it.
    assert emit_paths.pending_swap(layout)
    published = _fingerprint(out)
    assert emit_paths.recover_pending(layout) == (), (
        "the recovery reported replacing files the previous run had replaced")
    assert _fingerprint(out) == published
    assert not emit_paths.pending_swap(layout)


def test_dociq_state_inside_staging_is_never_published(tmp_path):
    """A staging layout is an ``OutputLayout``, so anything run against it makes
    its own ``.dociq/`` inside it — the package builder does. Publishing that
    into the matter root would put DocIQ's scratch where the manifest expects
    deliverables. Held by construction rather than by the one consumer that
    happens to clean up after itself."""
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_a_second_set(tmp_path, out, ())
    ghost = staging / emit_paths.STATE_DIRNAME / "scratch.txt"
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text("DocIQ's own state", encoding="utf-8", newline="")

    emit_paths.commit_staging(layout)
    assert not (out / emit_paths.STATE_DIRNAME / "scratch.txt").exists()
    assert not (out / "scratch.txt").exists()


# ---------------------------------------------------------------------------
# 5. B-8: the plan is the COMPLETE previous set, and it leaves FIRST
#
# Codex review #2, third fix round. D-31's load-bearing ordering claim is that
# the whole previous set is out of the matter root before any new file enters
# it. The plan did not inventory that set — it expanded this build's own output
# patterns — and an occupant the plan missed was moved aside only when the
# publish loop REACHED it. Two states followed, both of which Codex reproduced
# on Windows with a real open handle, and both are covered here.
# ---------------------------------------------------------------------------


def _stage_with(tmp_path, out, superseded, extra):
    """``_stage_a_second_set`` plus hand-placed staged files.

    ``extra`` maps a staging-relative name to its contents, added BEFORE
    ``mark_ready`` so the marker's published inventory covers them exactly as it
    would for a file an emitter wrote.
    """
    layout = OutputLayout.at(out)
    second_out = tmp_path / f"second-{len(list(tmp_path.iterdir()))}"
    _run(second_out)
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(second_out, staging, ignore=shutil.ignore_patterns(".dociq"))
    for rel, text in extra.items():
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    emit_paths.mark_ready(layout, superseded)
    return layout, staging


def test_a_locked_unplanned_replacement_publishes_nothing_before_it(tmp_path):
    """FAIL-BEFORE (Codex review #2, third fix round, B-8) — reproduction 1.

    An older DocIQ left ``z_legacy.txt``; this build's pattern list does not name
    it and the folder's inventory (written by a run that predates it) does not
    either, so the set-aside plan misses it. This build still writes a
    replacement at that path.

    The old publish loop recognized the occupant when it REACHED it. Staged
    files sort before it, so ``a_new.txt`` landed first; a real open handle on
    ``z_legacy.txt`` then made its move-aside fail, and the swap stopped with the
    matter root holding ``a_new.txt = NEW`` beside ``z_legacy.txt = OLD``. That
    is the mixed set D-31 says is unreachable, and the assertions below are
    written against exactly that state.

    The handle is real — ``open()`` on Windows, the same mechanism the B-4 test
    uses — not a monkeypatch, because the property is about what the operating
    system does to a rename under an antivirus lock.
    """
    out = tmp_path / "matter"
    _run(out)
    legacy = out / "z_legacy.txt"
    legacy.write_text("OLD\n", encoding="utf-8", newline="")

    # The plan covers what this build knows about. It does NOT cover
    # `z_legacy.txt` — that is the premise, asserted rather than assumed.
    layout, staging = _stage_with(
        tmp_path, out, ("sources.json",),
        {"a_new.txt": "NEW\n", "z_legacy.txt": "NEW\n"})
    plan = json.loads(
        (out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME)
        .read_text(encoding="utf-8"))
    assert "z_legacy.txt" not in plan["superseded"], (
        "the fixture's premise is gone: the plan already covers the unplanned "
        "occupant, so this proves nothing about an occupant it misses")
    assert "a_new.txt" in plan["published"] and "z_legacy.txt" in plan["published"]

    held = legacy.open("rb")  # the antivirus handle
    try:
        with pytest.raises(OSError):
            emit_paths.commit_staging(layout)

        # THE B-8 ASSERTION. Nothing of the new set may be in the folder while a
        # member of the old one is still in it.
        assert not (out / "a_new.txt").exists(), (
            "a staged file was published while an old deliverable the plan "
            "missed was still in the matter root — this is B-8's mixed set")
        assert legacy.read_text(encoding="utf-8") == "OLD\n", (
            "the deliverable that could not be moved was modified; a rename "
            "that fails must destroy nothing")
        assert emit_paths.pending_swap(layout), "the marker did not survive"
        assert (staging / "a_new.txt").is_file(), (
            "the new set is neither in the folder nor in staging")
    finally:
        held.close()

    # And the roll-forward finishes it once the lock is gone.
    emit_paths.recover_pending(layout)
    assert not emit_paths.pending_swap(layout)
    assert (out / "a_new.txt").read_text(encoding="utf-8") == "NEW\n"
    assert (out / "z_legacy.txt").read_text(encoding="utf-8") == "NEW\n"
    assert not (out / emit_paths.STATE_DIRNAME
                / emit_paths.ASIDE_PREFIX).exists()


def test_a_retired_output_leaves_the_folder_on_the_SUCCESS_path(tmp_path):
    """FAIL-BEFORE (Codex review #2, third fix round, B-8) — reproduction 2.

    The success-path sibling, and the one no failure injection can reach. A
    former build emitted ``legacy_report.json``; this build RETIRED it, so no
    staged path ever lands on that name and the lazy occupant branch is never
    entered at all. The swap completed, removed the marker, and left the old file
    beside the new ``sources.json`` — permanently, and reported as a clean run.

    §4 Stage 6 cannot catch it: the manifest is built over STAGING, not over the
    destination root, so nothing in the gate has ever seen the file.

    The only thing that can know about it is a record of what the last run
    actually published. Here that record is made the way a real run makes it —
    ``legacy_report.json`` is published THROUGH the swap, so the inventory is
    written by the code under test rather than by the test.
    """
    out = tmp_path / "matter"
    _run(out)
    # A run of a former build: it publishes `legacy_report.json` along with
    # everything else, through the ordinary swap.
    layout, staging = _stage_with(
        tmp_path, out, ("sources.json", "document_index.csv"),
        {"legacy_report.json": '{"from": "a build that emitted this"}\n'})
    emit_paths.commit_staging(layout)
    retired = out / "legacy_report.json"
    assert retired.is_file(), "the fixture did not publish the retired output"
    inventory = json.loads(
        (out / emit_paths.STATE_DIRNAME / emit_paths.PUBLISHED_NAME)
        .read_text(encoding="utf-8"))
    assert "legacy_report.json" in inventory["published"], (
        "the swap published a file and did not record it in the durable "
        "inventory — the next run cannot know the file is there")

    # This build. It has no pattern for `legacy_report.json` and writes nothing
    # at that name, so the ONLY way it leaves is the inventory.
    assert "legacy_report.json" not in json.dumps(list(pipeline._STALE_PATTERNS))
    outcome = _run(out)

    assert not retired.exists(), (
        "a deliverable the previous run published survived a successful swap "
        "because this build has no pattern for it — this is B-8's success-path "
        "residue, which no gate can see")
    assert "legacy_report.json" in outcome.stale_removed, (
        "the retired output left the folder without being recorded")
    assert (out / "sources.json").is_file()
    assert not (out / emit_paths.STATE_DIRNAME
                / emit_paths.ASIDE_PREFIX).exists(), (
        "the retired output was set aside and the tree was not cleaned up")


def test_the_inventory_survives_a_version_that_never_heard_of_the_name(tmp_path):
    """The CLASS, stated without a specific file name.

    B-8 is not about ``legacy_report.json``. It is about the plan being built
    from what this build writes rather than from what the folder holds, so it
    fails for *any* name a version change retires. This asserts the general
    property: whatever the previous run published, the next run's plan covers all
    of it, and the matter root afterwards holds the new set and nothing else.
    """
    out = tmp_path / "matter"
    _run(out)
    strays = {
        "aa_first_alphabetically.txt": "OLD\n",
        "zz_last_alphabetically.dat": "OLD\n",
        "retired_dir/nested/deep.json": "{}\n",
        "profile/not-a-yaml.txt": "OLD\n",
    }
    layout, staging = _stage_with(tmp_path, out, ("sources.json",), strays)
    emit_paths.commit_staging(layout)
    for rel in strays:
        assert (out / rel).is_file(), f"{rel} was not published by the fixture"

    published = set(json.loads(
        (out / emit_paths.STATE_DIRNAME / emit_paths.PUBLISHED_NAME)
        .read_text(encoding="utf-8"))["published"])
    for rel in strays:
        assert rel in published, f"{rel} is missing from the durable inventory"

    _run(out)
    survivors = [rel for rel in strays if (out / rel).exists()]
    assert survivors == [], (
        f"a version change left these behind: {survivors}")
    assert not (out / "retired_dir").exists(), (
        "the retired directory's shell survived its contents")


def test_a_corrupt_published_inventory_fails_closed(tmp_path):
    """The inventory decides what gets moved aside, so an unreadable one is the
    same state as an unreadable marker and gets the same answer (B-2's
    reasoning, one file over): nothing moves, the folder is untouched, and the
    run is BLOCKED with a message naming the file.

    An ABSENT inventory is deliberately NOT this — that is the ordinary folder
    published by a build predating it, and it falls back to the patterns.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    inventory = out / emit_paths.STATE_DIRNAME / emit_paths.PUBLISHED_NAME
    assert inventory.is_file(), "a completed run wrote no inventory"

    inventory.write_text('{"published": [{"not": "a name"}]}\n',
                         encoding="utf-8", newline="")
    with pytest.raises(emit_paths.PublishedSetUnreadable) as excinfo:
        emit_paths.published_inventory(OutputLayout.at(out))
    assert str(inventory) in str(excinfo.value)

    blocked = _run(out)
    assert not blocked.published, "a corrupt inventory published anyway"
    for rel, digest in before.items():
        victim = out / rel
        assert victim.is_file(), f"{rel} left the folder"
        assert hashlib.sha256(victim.read_bytes()).hexdigest() == digest, (
            f"{rel} was modified by a run that should have refused to publish")


def test_an_absent_inventory_falls_back_to_the_patterns_and_says_so(tmp_path):
    """The bootstrap case: a folder last published by a build that predates the
    inventory. The plan is this build's patterns — which is B-8's incomplete
    plan — so the run DISCLOSES that rather than presenting the plan as
    complete."""
    out = tmp_path / "matter"
    _run(out)
    (out / emit_paths.STATE_DIRNAME / emit_paths.PUBLISHED_NAME).unlink()

    second = _run(out)
    payload = json.loads(
        second.layout.processing_log.read_text(encoding="utf-8"))
    disclosed = payload["run"]["published_set_inventory"]
    assert isinstance(disclosed, str) and "absent" in disclosed, (
        "a run whose set-aside plan was built from patterns alone did not say "
        "so; the operator cannot tell a complete plan from an incomplete one")
    assert "published_set_inventory" not in json.dumps(payload["content"]), (
        "the folder's history reached the hashed content (criterion 7)")

    third = _run(out)
    rebuilt = json.loads(
        third.layout.processing_log.read_text(encoding="utf-8")
    )["run"]["published_set_inventory"]
    assert isinstance(rebuilt, list) and rebuilt, (
        "the run after the fallback did not rebuild the inventory")


def test_a_lock_on_the_inventory_keeps_the_marker_as_the_record(tmp_path,
                                                                monkeypatch):
    """The inventory is written BELOW the publish, where nothing may raise. So
    the one thing that must not happen is it silently staying at the previous
    run's set while the marker — which carries the same list — is deleted.

    Enumerated rather than found: the marker's own removal is tolerated for
    exactly this reason, and "tolerated" is how B-6 happened."""
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_with(
        tmp_path, out, ("sources.json",), {"a_new.txt": "NEW\n"})

    real = emit_paths._write_inventory

    def refuse(destination, published):
        raise OSError("[WinError 32] held by another process")

    monkeypatch.setattr(emit_paths, "_write_inventory", refuse)
    emit_paths.commit_staging(layout)
    monkeypatch.undo()

    assert (out / "a_new.txt").is_file(), "the publish did not complete"
    assert emit_paths.pending_swap(layout), (
        "the marker was deleted while the inventory it backs was not written — "
        "the record of what this run published is now gone entirely")
    assert "a_new.txt" in emit_paths.published_inventory(layout), (
        "the marker did not stand in for the inventory it is held back for")

    # And the next call finishes it.
    emit_paths.recover_pending(layout)
    assert not emit_paths.pending_swap(layout)
    assert "a_new.txt" in emit_paths.published_inventory(layout)
    assert real is emit_paths._write_inventory


# ---------------------------------------------------------------------------
# 6. THE STATE ENUMERATION
#
# Codex review #2, third fix round: *"Re-enumerate the redesigned state machines
# from every persistent state."* Every round of this review found a defect that
# came from reasoning FORWARD through the happy path instead of BACKWARD from
# each state that can exist on disk, so the states are enumerated here as a
# table and, where they can be constructed, as probes.
#
# The full table — every combination of marker/phase, staging, matter root,
# set-aside tree and inventory, with the next run's behaviour and whether it is
# correct — is in `docs/verification/codex_r4_inventory_2026-08-06.md` §2. The
# row IDs below are that table's.
#
# Two rows had a next step that DESTROYED evidence, and neither was reachable
# from the code, which is why nothing had looked at them:
#
#   S-09  phase `aside`, staging empty, nothing published — the set-aside tree
#         holds the only copy of the matter's deliverables, and the old code
#         ran an empty publish loop, recorded `published`, and DELETED it.
#   S-13  phase `published`, staging still full — the old code deleted
#         `.dociq/staging`, a complete set of deliverables, as drained scratch.
#
# Both are now refused or rolled back, and both have a probe below.
# ---------------------------------------------------------------------------


def _marker_of(out) -> Path:
    return out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME


def _set_phase(out, phase: str) -> None:
    marker = _marker_of(out)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["phase"] = phase
    marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="")


def _stop_at_phase_aside(layout, staging, monkeypatch):
    """Drive a real swap to the end of step 1 and no further.

    The set-aside completes, the marker records `aside`, and the first publish
    rename raises — which is the state a crash between the two steps leaves.
    """
    real = emit_paths._rename_or_fail

    def refuse_staged(src, dst):
        if str(src).startswith(str(staging)):
            raise OSError("[WinError 32] held by another process")
        return real(src, dst)

    monkeypatch.setattr(emit_paths, "_rename_or_fail", refuse_staged)
    with pytest.raises(OSError):
        emit_paths.commit_staging(layout)
    monkeypatch.undo()
    plan = json.loads(_marker_of(layout.root).read_text(encoding="utf-8"))
    assert plan["phase"] == emit_paths.PHASE_ASIDE, plan["phase"]
    return plan


def test_S09_aside_with_a_lost_staging_restores_the_previous_set(
        tmp_path, monkeypatch):
    """FAIL-BEFORE — the most destructive state in this module, and the one with
    no test.

    The swap has moved the previous set into ``.dociq/<aside>/`` and the staged
    set is then LOST: a cleanup script, a backup agent, or an operator following
    the readiness marker's own "move staging aside" instruction without also
    deleting the marker. The set-aside tree now holds the only copy of the
    matter's deliverables.

    The old code did not ask. It ran the publish loop over nothing, recorded
    ``published``, and then ran the cleanup — which deletes every
    ``.dociq/superseded*`` tree. The folder was left with no deliverables at all
    and nothing on disk saying where they had gone.

    Reached backwards from the disk state rather than forwards from a run, which
    is the whole point of the enumeration.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    layout, staging = _stage_with(
        tmp_path, out, tuple(sorted(before)), {"only_new.txt": "NEW\n"})
    _stop_at_phase_aside(layout, staging, monkeypatch)

    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    assert aside.is_dir(), "the fixture did not reach the set-aside state"
    assert not (out / "sources.json").exists(), (
        "the fixture's premise is gone: the previous set is still at the root")

    shutil.rmtree(staging)  # the staged set is lost

    emit_paths.recover_pending(layout)

    after = _fingerprint(out)
    assert after == before, (
        "the previous set was not restored to the matter folder. It was the "
        f"only copy left: {sorted(set(before) - set(after))}")
    assert not emit_paths.pending_swap(layout), "the abandoned marker survived"
    assert not aside.exists(), "the set-aside tree survived its own rollback"
    assert not (out / "only_new.txt").exists()
    # The inventory still describes what is in the folder, because the swap
    # never reached `published` and never rewrote it.
    assert set(emit_paths.published_inventory(layout)) <= set(before) | {
        rel for rel in before}
    # And an ordinary run over the restored folder works.
    assert _run(out).published


def test_S10_aside_with_everything_already_published_only_cleans_up(
        tmp_path, monkeypatch):
    """The other reading of the same disk shape, and the reason S-09 has to test
    the root rather than trust the phase.

    Every published name IS at the matter root and staging is empty: the publish
    finished and only the marker update was lost. Nothing may move — rolling the
    set-aside tree back here would put the PREVIOUS set on top of the current
    one, which is B-6 with the arrow reversed.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    layout, staging = _stage_with(
        tmp_path, out, tuple(sorted(before)), {"only_new.txt": "NEW\n"})
    plan = _stop_at_phase_aside(layout, staging, monkeypatch)

    # Finish the publish by hand, leaving the marker at `aside`.
    for rel in plan["published"]:
        src, dst = staging / rel, out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    shutil.rmtree(staging)
    published = _fingerprint(out)

    emit_paths.recover_pending(layout)

    assert _fingerprint(out) == published, (
        "a recovery moved the published set; at phase `aside` with everything "
        "already in place there is nothing to move")
    assert (out / "only_new.txt").is_file()
    assert not emit_paths.pending_swap(layout)
    assert not (out / emit_paths.STATE_DIRNAME
                / emit_paths.ASIDE_PREFIX).exists()


def test_S11_aside_with_a_half_published_root_refuses(tmp_path, monkeypatch):
    """The state between S-09 and S-10, which the code cannot produce — a
    publish moves one file at a time, so an interrupted one leaves the rest IN
    staging. It is a restore, a half-finished copy or a hand edit.

    Guessing costs a whole set either way, so it is refused with everything left
    where it was."""
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    layout, staging = _stage_with(
        tmp_path, out, tuple(sorted(before)), {"only_new.txt": "NEW\n"})
    plan = _stop_at_phase_aside(layout, staging, monkeypatch)

    (staging / "only_new.txt").rename(out / "only_new.txt")
    shutil.rmtree(staging)
    frozen = _fingerprint(out)
    aside_before = _fingerprint(
        out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX)

    with pytest.raises(emit_paths.PendingSwapUnrecoverable) as excinfo:
        emit_paths.recover_pending(layout)
    assert emit_paths.ASIDE_PREFIX in str(excinfo.value), (
        "the refusal does not tell the operator where the previous set is")

    assert _fingerprint(out) == frozen, "the refusal moved something"
    assert _fingerprint(
        out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    ) == aside_before, "the refusal touched the set-aside tree"
    assert emit_paths.pending_swap(layout), (
        "the marker was cleared by a refusal — nothing is left to repair from")

    # An ordinary run over that folder is BLOCKED rather than a traceback.
    blocked = _run(out)
    assert not blocked.published
    assert _fingerprint(
        out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    ) == aside_before


def test_S13_published_with_a_full_staging_refuses(tmp_path):
    """A marker saying ``published`` beside a staging directory that still holds
    a complete set.

    The publish loop raises rather than recording ``published`` over an
    unpublished file, so this is a restore or a hand edit. The old code trusted
    the phase, skipped both moving steps, and ran the cleanup —
    ``_remove_tree_or_fail(staging)`` — deleting a complete set of deliverables
    as drained scratch.
    """
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_with(
        tmp_path, out, ("sources.json",), {"only_new.txt": "NEW\n"})
    _set_phase(out, emit_paths.PHASE_PUBLISHED)
    staged_before = _fingerprint(staging)
    root_before = _fingerprint(out)
    assert staged_before, "the fixture staged nothing"

    with pytest.raises(emit_paths.PendingSwapUnrecoverable) as excinfo:
        emit_paths.recover_pending(layout)
    assert emit_paths.STAGING_DIRNAME in str(excinfo.value)

    assert _fingerprint(staging) == staged_before, (
        "the staged set was destroyed by a marker that said the swap was "
        "already finished")
    assert _fingerprint(out) == root_before
    assert emit_paths.pending_swap(layout)


def test_S16_a_residue_tree_does_not_block_or_get_overwritten(tmp_path):
    """A set-aside tree an earlier run could not delete, and no marker.

    It is DocIQ's own state, it is disclosed, and the next swap must neither
    trip over it nor write into it — the two ways a residue turns into a
    second-order defect."""
    out = tmp_path / "matter"
    _run(out)
    residue = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    residue.mkdir(parents=True, exist_ok=True)
    (residue / "left_by_an_earlier_run.txt").write_text(
        "OLD\n", encoding="utf-8", newline="")
    layout = OutputLayout.at(out)
    assert emit_paths.superseded_residue(layout) == (
        f"{emit_paths.STATE_DIRNAME}/{emit_paths.ASIDE_PREFIX}",)

    layout, staging = _stage_with(tmp_path, out, ("sources.json",),
                                  {"only_new.txt": "NEW\n"})
    plan = json.loads(_marker_of(out).read_text(encoding="utf-8"))
    assert plan["aside"] == f"{emit_paths.ASIDE_PREFIX}.1", (
        "the new swap chose a set-aside name the residue already occupies")
    emit_paths.commit_staging(layout)
    assert (out / "only_new.txt").is_file()
    # Both trees are deleted once nothing holds them: the residue is a previous
    # set that has already been replaced, so a later swap finishes the job.
    assert emit_paths.superseded_residue(layout) == ()


@pytest.mark.parametrize("build", [
    "S03_pending_full_staging",
    "S05_pending_empty_staging",
    "S07_aside_full_staging",
    "S08_aside_unplanned_occupant",
    "S09_aside_lost_staging",
    "S12_published_empty_staging",
])
def test_no_persistent_state_lets_two_generations_share_the_matter_root(
        tmp_path, monkeypatch, build):
    """THE CLASS ASSERTION, over the states rather than over one failure.

    ``only_old.txt`` belongs to the previous set and ``only_new.txt`` to the
    staged one. D-31's claim is that the matter root never holds both, at any
    instant, in any state — so it is asserted state by state rather than once on
    the happy path, and before as well as after the recovery.

    States that REFUSE (S-11, S-13) and states that fail closed (an unreadable
    marker) are covered by their own probes above; this one covers the states a
    recovery is expected to complete.
    """
    out = tmp_path / "matter"
    _run(out)
    (out / "only_old.txt").write_text("OLD\n", encoding="utf-8", newline="")
    before = _fingerprint(out)
    plan_names = tuple(sorted(before))
    layout, staging = _stage_with(
        tmp_path, out, plan_names, {"only_new.txt": "NEW\n"})

    def generations():
        return ((out / "only_old.txt").exists(), (out / "only_new.txt").exists())

    if build == "S03_pending_full_staging":
        pass
    elif build == "S05_pending_empty_staging":
        shutil.rmtree(staging)
    elif build == "S07_aside_full_staging":
        _stop_at_phase_aside(layout, staging, monkeypatch)
    elif build == "S08_aside_unplanned_occupant":
        _stop_at_phase_aside(layout, staging, monkeypatch)
        # A name no plan covers reappears at the root before the publish.
        (out / "only_new.txt").parent.mkdir(parents=True, exist_ok=True)
        (staging / "unplanned.txt").write_text(
            "NEW\n", encoding="utf-8", newline="")
        (out / "unplanned.txt").write_text("OLD\n", encoding="utf-8", newline="")
    elif build == "S09_aside_lost_staging":
        _stop_at_phase_aside(layout, staging, monkeypatch)
        shutil.rmtree(staging)
    elif build == "S12_published_empty_staging":
        emit_paths.commit_staging(layout)

    old_here, new_here = generations()
    assert not (old_here and new_here), (
        f"{build}: the matter root holds both generations BEFORE recovery")

    emit_paths.recover_pending(layout)

    old_here, new_here = generations()
    assert not (old_here and new_here), (
        f"{build}: the matter root holds both generations after recovery")
    assert old_here or new_here, (
        f"{build}: recovery left the matter root holding NEITHER generation")
    if build == "S08_aside_unplanned_occupant":
        assert (out / "unplanned.txt").read_text(encoding="utf-8") == "NEW\n"
    assert not emit_paths.pending_swap(layout), f"{build}: the marker survived"


def test_S09_rollback_restores_an_UNPLANNED_occupant_too(tmp_path, monkeypatch):
    """The sibling of S-09, enumerated rather than met.

    Step 2a moves an occupant the plan did not name into the SAME set-aside
    tree, and records it only in ``commit_staging``'s return value — nothing
    durable names it. So a rollback keyed on ``plan.superseded`` would restore
    part of the tree and hand the rest to ``_discard_aside_trees``, which
    deletes it. The rollback is therefore driven by what is in the tree.
    """
    out = tmp_path / "matter"
    _run(out)
    (out / "unplanned.txt").write_text("OLD\n", encoding="utf-8", newline="")
    before = _fingerprint(out)
    # `unplanned.txt` is deliberately NOT in the plan, and has a staged
    # replacement, so only step 2a can move it.
    layout, staging = _stage_with(
        tmp_path, out, tuple(sorted(set(before) - {"unplanned.txt"})),
        {"unplanned.txt": "NEW\n"})

    real = emit_paths._rename_or_fail

    def refuse_staged(src, dst):
        if str(src).startswith(str(staging)):
            raise OSError("[WinError 32] held by another process")
        return real(src, dst)

    # First attempt: step 1 and step 2a complete, step 2b raises.
    monkeypatch.setattr(emit_paths, "_rename_or_fail", refuse_staged)
    with pytest.raises(OSError):
        emit_paths.commit_staging(layout)
    monkeypatch.undo()

    aside = out / emit_paths.STATE_DIRNAME / emit_paths.ASIDE_PREFIX
    assert (aside / "unplanned.txt").is_file(), (
        "the fixture's premise is gone: 2a did not move the unplanned occupant")
    marker = json.loads(
        (out / emit_paths.STATE_DIRNAME / emit_paths.MARKER_NAME)
        .read_text(encoding="utf-8"))
    assert "unplanned.txt" not in marker["superseded"], (
        "the occupant is in the durable plan after all, so a plan-keyed "
        "rollback would have restored it and this proves nothing")

    shutil.rmtree(staging)  # the staged set is lost — S-09
    emit_paths.recover_pending(layout)

    assert _fingerprint(out) == before, (
        "the rollback lost a file step 2a had moved aside: "
        f"{sorted(set(before) - set(_fingerprint(out)))}")
    assert (out / "unplanned.txt").read_text(encoding="utf-8") == "OLD\n"
    assert not emit_paths.pending_swap(layout)
    assert not aside.exists()


def test_a_rollback_is_not_disclosed_as_a_completed_swap(tmp_path, monkeypatch):
    """The recovery's DURABLE description has to match what the recovery did.

    S-09 introduced an outcome the disclosure had never had to describe: the
    interrupted run's staged set is gone and the PREVIOUS run's set is restored,
    so the deliverables in the folder are not the ones the interrupted run
    wrote. The invocation note asserted "it was completed", which is precisely
    the wrong fact about which run's evidence an auditor is looking at.

    Enumerated with the state, not after it: a recovery that describes itself
    wrongly in the processing log is the same class of defect as one that does
    the wrong thing, one layer out.
    """
    out = tmp_path / "matter"
    _run(out)
    before = _fingerprint(out)
    layout, staging = _stage_with(
        tmp_path, out, tuple(sorted(before)), {"only_new.txt": "NEW\n"})
    _stop_at_phase_aside(layout, staging, monkeypatch)
    shutil.rmtree(staging)

    notes: list[str] = []
    emit_paths.recover_pending(layout, notes)
    assert notes and notes[0].startswith("ROLLED BACK"), notes

    # And the same fact reaches the next run's durable record.
    layout2, staging2 = _stage_with(
        tmp_path, out, ("sources.json",), {"only_new.txt": "NEW\n"})
    _stop_at_phase_aside(layout2, staging2, monkeypatch)
    shutil.rmtree(staging2)
    recovered_run = _run(out)
    payload = json.loads(
        recovered_run.layout.processing_log.read_text(encoding="utf-8"))
    invocation = " ".join(payload["run"]["invocation_notes"])
    assert "RECOVERED" in invocation
    assert "ROLLED BACK" in invocation, (
        "the durable record does not say the interrupted run's set was the one "
        f"that did not survive: {invocation!r}")
    assert "invocation_notes" not in json.dumps(payload["content"]), (
        "the recovery outcome reached the hashed content (criterion 7)")


def test_an_ordinary_roll_forward_says_so(tmp_path):
    """The sibling reading, so the note above is a discriminator rather than a
    label the recovery always prints."""
    out = tmp_path / "matter"
    _run(out)
    layout, staging = _stage_with(
        tmp_path, out, ("sources.json",), {"only_new.txt": "NEW\n"})
    notes: list[str] = []
    emit_paths.recover_pending(layout, notes)
    assert notes and notes[0].startswith("ROLLED FORWARD"), notes
    assert (out / "only_new.txt").is_file()


def test_a_marker_that_outlived_its_swap_says_nothing_to_do(tmp_path):
    out = tmp_path / "matter"
    _run(out)
    layout = OutputLayout.at(out)
    emit_paths.mark_ready(layout, ("sources.json",))
    staging = out / emit_paths.STATE_DIRNAME / emit_paths.STAGING_DIRNAME
    if staging.exists():
        shutil.rmtree(staging)
    notes: list[str] = []
    emit_paths.recover_pending(layout, notes)
    assert notes and notes[0].startswith("NOTHING TO DO"), notes
    assert (out / "sources.json").is_file()
