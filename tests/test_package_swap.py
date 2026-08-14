"""A failed package build never leaves a CURRENT partial folder — A-4, then A-5.

**D-32 (2026-08-06) did NOT touch this file, and that is a decision rather than
an oversight.** The descope removed the MATTER folder's publication protocol.
The package's own delete-last publish is a separate mechanism in
``dociq/emit/handoff.py``; the sixth review generation checked it and found it
sound, so the D-31 references below still describe live code. A reader arriving
from ``tests/test_emit_atomicity.py`` — where the equivalent machinery was
deleted — should not assume the same happened here.

**D-31 (2026-08-05) rewrote the second half of this file.** The publish order it
tested — move the earlier package aside, DELETE it, then rename the new one into
place — was found to have one more window (A-5): ``shutil.rmtree`` deletes part
of a tree before it fails, so "the removal did not complete" meant "an unknown
part of the earlier package is gone", and the code renamed that damaged tree back
under the published name and told the operator it was *"back in place and
intact"*. The order is now **delete-last** — rename aside, rename into place,
delete only after the new package holds the name — and both working directories
moved under ``.dociq/`` so a set-aside tree is not a package-shaped folder beside
the deliverables. The tests below are rewritten against that; the ones asserting
the old order's wording are gone rather than adapted.

Original A-4 statement, which still holds:

Codex review #2 fix round, finding A-4. ``build_upload_package()`` deleted the
prior package, created the final ``upload_package/`` directory, and wrote files
into it one at a time. Any later copy, filter, README write or validation
exception left that current, partial, unvalidated directory in place under the
name an operator drags into a Claude Project — while the GUI's new failure state
told them, in terms, that *"any package already on disk is from an EARLIER
build — check its date before sending it."*

Codex's reproduction: with the README write failing, ``DIQ-1.txt`` and
``sources.json`` **from the failed current attempt** remained in
``upload_package/``, with no README and no completed validation, while the
screen said the package there was an earlier build. An operator following that
assurance uploads a partial current package as if it were complete.

The fix assembles in ``.dociq/package_staging/`` and claims the published name
only after every copy, filter, README and validation has passed. So the screen's
sentence becomes TRUE rather than merely narrower: on any failure the folder
still holds the earlier build, byte for byte.

**Every test here inspects the DISK.** Two of them also inspect the screen, and
the disclosure stands: nobody has ever driven this GUI with a mouse — the
handoff screen is exercised offscreen through the same signal the operator's
click emits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import dociq.emit.handoff as h
from dociq.emit import paths
from dociq.emit.handoff import (
    README_NAME,
    PackageSwapError,
    build_upload_package,
)

from .test_emit import full_matter

LOCK_ERROR = (
    r"[WinError 32] The process cannot access the file because it is being "
    r"used by another process"
)
"""The documented hazard on the machine this ships to (Carbonite / AV file
locks), which is also the failure Codex's own scenario names."""


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _siblings(layout) -> list[str]:
    """Every ``upload_package*`` directory in the matter folder.

    Since D-31 there is only ever one, on every path: the staging and set-aside
    trees live under ``.dociq/`` where nothing mistakes them for a deliverable,
    so this is now an assertion about the matter folder rather than about
    cleanup timing.
    """
    return sorted(
        p.name for p in layout.root.iterdir()
        if p.is_dir() and p.name.startswith("upload_package")
    )


def _state(layout) -> Path:
    return layout.root / paths.STATE_DIRNAME


# ---------------------------------------------------------------------------
# The finding itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["readme", "sanction-check", "manifest-check"])
def test_a_failure_after_files_are_written_leaves_the_earlier_package_intact(
        tmp_path, monkeypatch, where):
    """FAIL-BEFORE, all three: the old code had already deleted the previous
    package and written text files and manifests into ``upload_package/`` by the
    time any of these raise. The assertion that fails first is
    ``after == before`` — the folder held a partial CURRENT build.

    An enumeration rather than one repro: the README write is the site Codex
    reproduced, and the two validations are the other points after at least one
    file is on disk. A failure added between them has neighbours.

    The failing rebuild is deliberately a **narrower scope** than the first
    build. Against the old in-place code the two validation cases would
    otherwise leave a directory whose contents happened to equal the previous
    package's, and a fail-before that goes green for that reason proves
    nothing — the folder would still have been rebuilt from scratch by a run
    that failed.
    """
    layout, docs = full_matter(tmp_path)
    first = build_upload_package(layout, document_count=len(docs))
    assert first.root == layout.upload_package
    before = _tree(layout.upload_package)
    assert any(n.endswith(".txt") for n in before), "nothing to protect"
    assert README_NAME in before
    assert len(docs) > 1, "the narrower-scope rebuild needs something to narrow"

    def boom(*_a, **_k):
        raise OSError(LOCK_ERROR)

    target = {
        "readme": "render_readme",
        "sanction-check": "assert_only_sanctioned",
        "manifest-check": "_assert_manifest_matches_folder",
    }[where]
    monkeypatch.setattr(h, target, boom)

    with pytest.raises(OSError) as caught:
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")
    assert LOCK_ERROR in str(caught.value), "the reason was lost or paraphrased"

    after = _tree(layout.upload_package)
    assert after == before, (
        "the failed build left a CURRENT partial package under the name the "
        "operator uploads; the GUI is telling them it is an earlier build"
    )
    assert _siblings(layout) == ["upload_package"], (
        f"staging residue was left behind: {_siblings(layout)}")


def test_the_partial_files_of_a_failed_build_are_nowhere_in_the_matter_folder(
        tmp_path, monkeypatch):
    """Codex named the artifacts: the text file and ``sources.json`` from the
    failed attempt. Not "the package is unchanged" — the stronger claim that
    those bytes are not anywhere an operator could pick them up."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    kept = _tree(layout.upload_package)

    monkeypatch.setattr(h, "render_readme", lambda **_k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A SCOPED SUBSET\n")

    assert _tree(layout.upload_package) == kept
    strays = [
        p for p in layout.root.rglob("*")
        if p.is_file() and "upload_package." in p.as_posix()
    ]
    assert not strays, f"files from the abandoned attempt survive at {strays}"


def test_a_failed_first_build_leaves_no_package_at_all(tmp_path, monkeypatch):
    """With no earlier build to fall back on, "any package on disk is from an
    EARLIER build" must be vacuously true — not true of a folder this attempt
    created."""
    layout, docs = full_matter(tmp_path)
    monkeypatch.setattr(h, "render_readme", lambda **_k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        build_upload_package(layout, document_count=len(docs))
    assert not layout.upload_package.exists(), (
        "a package folder exists that only the FAILED attempt could have made")
    assert _siblings(layout) == []


def test_a_successful_build_leaves_exactly_one_package_directory(tmp_path):
    """The staging sibling is an implementation detail and must not outlive the
    build — a second folder full of package-shaped files is the mixed-set
    hazard with a different name on it."""
    layout, docs = full_matter(tmp_path)
    pkg = build_upload_package(layout, document_count=len(docs))
    assert _siblings(layout) == ["upload_package"]
    assert pkg.root == layout.upload_package
    assert pkg.readme == layout.upload_package / README_NAME
    assert pkg.readme.is_file(), "the returned README path points at staging"
    assert README_NAME in pkg.files


def test_the_previous_package_is_replaced_not_merged(tmp_path):
    """The guarantee the staging build must not have weakened: a stale text file
    from an earlier, larger scope is gone, not left beside the new one."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    stale = layout.upload_package / "STALE.txt"
    stale.write_text("old", encoding="utf-8")
    pkg = build_upload_package(layout, document_count=len(docs))
    assert "STALE.txt" not in pkg.files
    assert not stale.exists()


# ---------------------------------------------------------------------------
# Removals fail CLOSED — the B-4 shape, in this module's own code
# ---------------------------------------------------------------------------


def test_a_leftover_staging_directory_stops_the_build_rather_than_being_used(
        tmp_path, monkeypatch):
    """FAIL-BEFORE for the ``ignore_errors=True`` shape: if the leftover cannot
    be removed, assembling into it would mix another attempt's files into this
    package. The build must not start, and the published package must not be
    touched."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)

    leftover = _state(layout) / h._INCOMING_NAME
    leftover.mkdir(parents=True)
    (leftover / "GHOST.txt").write_text("another attempt", encoding="utf-8")
    monkeypatch.setattr(h, "_remove_tree", lambda p, **_k: not p.exists())

    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, document_count=len(docs))
    assert h._INCOMING_NAME in str(caught.value)
    assert _tree(layout.upload_package) == before
    assert (leftover / "GHOST.txt").is_file(), (
        "the build claimed it could not remove the leftover and removed it")


def test_a_package_that_cannot_be_moved_aside_leaves_the_earlier_one_in_place(
        tmp_path, monkeypatch):
    """The FIRST rename fails — an antivirus scanner holding a file in the
    published package open. Nothing has been destroyed at that point, and
    nothing may be."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)

    real = h._retry_rename

    def fail_moving_aside(src, dst, **kw):
        if dst.name == h._SUPERSEDED_NAME:
            raise OSError(LOCK_ERROR)
        return real(src, dst, **kw)

    monkeypatch.setattr(h, "_retry_rename", fail_moving_aside)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")

    assert "still holds the earlier build" in str(caught.value)
    assert _tree(layout.upload_package) == before, (
        "the earlier package was destroyed to make room for one that never "
        "arrived")
    assert _siblings(layout) == ["upload_package"]


def _eat_one_file_then_fail(path, **_kw):
    """A ``_remove_tree`` that behaves like the real ``shutil.rmtree`` does under
    an antivirus handle: it removes SOME of the tree and then gives up.

    This is A-5's premise, and the old test double did not have it — it returned
    ``False`` without deleting anything, so it assumed the very property the
    production helper cannot guarantee. Every assertion built on that double was
    therefore true of a tree that had not actually been damaged.
    """
    if not path.exists():
        return True
    victims = sorted(p for p in path.rglob("*") if p.is_file())
    if victims:
        victims[0].unlink()
    return False


def test_a_partly_deleted_superseded_package_is_never_the_published_one(
        tmp_path, monkeypatch):
    """FAIL-BEFORE (Codex review #2, second fix round, A-5).

    An antivirus process holds one file in the superseded package. ``rmtree``
    removes the others and gives up. On the OLD order that removal happened
    BEFORE the new package took the name, so the code renamed the damaged tree
    back to ``upload_package/`` and raised with *"The earlier build is back in
    place and intact."* Codex's reproduction: six files in, one removed, a
    five-file package restored under the published name, and that exact
    assurance on screen. An operator uploads a package missing a document.

    Under delete-last the removal cannot happen until the new package already
    holds the name, so the damaged tree is never a candidate for it. The
    assertions are written against the old state — a five-file *old* package
    under ``upload_package/`` and a ``PackageSwapError`` — so this is red on the
    old code for A-5's reason rather than an incidental one.
    """
    layout, docs = full_matter(tmp_path)
    first = build_upload_package(layout, document_count=len(docs))
    assert len(first.files) > 1, "nothing to partially delete"
    monkeypatch.setattr(h, "_remove_tree", _eat_one_file_then_fail)

    # The build SUCCEEDS. On the old code this raised.
    second = build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                                  scope_statement="A NARROWER SCOPE\n")
    assert second.root == layout.upload_package

    on_disk = _tree(layout.upload_package)
    assert README_NAME in on_disk
    assert "A NARROWER SCOPE" in (
        layout.upload_package / README_NAME).read_text(encoding="utf-8"), (
        "the published folder is not the package this build produced")
    assert set(on_disk) == set(second.files), (
        "the published folder and the package this build reports disagree")

    # And the damaged tree is where nobody uploads from, under a name that says
    # what it is — never at the matter root beside the deliverables.
    assert _siblings(layout) == ["upload_package"], (
        f"a package-shaped folder sits beside the deliverables: "
        f"{_siblings(layout)}")
    assert (_state(layout) / h._SUPERSEDED_NAME).is_dir(), (
        "the fixture's premise is gone: nothing was left partially deleted")


def test_a_publish_that_cannot_take_the_name_restores_an_unmodified_backup(
        tmp_path, monkeypatch):
    """The rollback branch, and the sentence A-5 is about.

    The final rename fails. The earlier package was RENAMED aside, not deleted,
    so putting it back restores exactly the bytes that were there — and the
    message may say so. The old code reached its equivalent sentence over a tree
    ``rmtree`` had already eaten into; this asserts the bytes rather than the
    wording alone.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)
    assert len(before) > 1

    real = h._retry_rename
    attempts = {"n": 0}

    def fail_the_first_publish(src, dst, **kw):
        # The publish rename fails; the rename BACK is allowed, which is the
        # branch under test.
        if dst.name == "upload_package":
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError(LOCK_ERROR)
        return real(src, dst, **kw)

    monkeypatch.setattr(h, "_retry_rename", fail_the_first_publish)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")

    message = str(caught.value)
    assert "Nothing was published" in message
    assert "byte for byte" in message
    assert "never modified" in message
    assert _tree(layout.upload_package) == before, (
        "the restored package is not byte-identical to the one moved aside — "
        "which is the only thing that licenses the message above")
    assert _siblings(layout) == ["upload_package"]


def test_a_publish_that_cannot_take_the_name_and_cannot_roll_back_says_where(
        tmp_path, monkeypatch):
    """Both renames fail. There must be NO package folder at the matter root —
    a package-shaped folder an operator could upload — and the message must name
    where the earlier build actually is, and must not claim it is back.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)

    real = h._retry_rename

    def fail_every_publish(src, dst, **kw):
        if dst.name == "upload_package":
            raise OSError(LOCK_ERROR)
        return real(src, dst, **kw)

    monkeypatch.setattr(h, "_retry_rename", fail_every_publish)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, document_count=len(docs))

    message = str(caught.value)
    assert h._SUPERSEDED_NAME in message, "the message does not say where it is"
    assert "back in place" not in message
    assert _siblings(layout) == [], (
        f"a package-shaped folder survives: {_siblings(layout)}")
    # It is intact — it was moved, never touched — which is what the message
    # tells the operator, so it is asserted rather than believed.
    assert _tree(_state(layout) / h._SUPERSEDED_NAME) == before


def test_the_removal_helper_answers_with_the_state_of_the_disk(tmp_path):
    """``shutil.rmtree(..., ignore_errors=True)`` returns ``None`` whether or
    not the directory went. Every caller here branches on this instead."""
    gone = tmp_path / "never-existed"
    assert h._remove_tree(gone) is True

    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    (real / "sub" / "f.txt").write_text("x", encoding="utf-8")
    assert h._remove_tree(real) is True
    assert not real.exists()


# ---------------------------------------------------------------------------
# Disk AND screen, together — the assertion Codex asked for
# ---------------------------------------------------------------------------


def test_the_screen_and_the_disk_agree_after_a_failed_build(real_run, monkeypatch):
    """Both halves of A-4 in one test, because the finding is that they
    DISAGREED: the screen said the folder held an earlier build while the folder
    held a partial current one.

    Driven through the real adapter and the real emit layer — no mock package
    result — so the sentence on screen is asserted against the bytes on disk.

    **Nobody has ever driven this GUI with a mouse.** This emits the same signal
    the button emits, under the offscreen platform plugin.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from dociq import adapter
    from dociq.gui.main_window import MainWindow
    from dociq.gui.pipeline import set_pipeline
    from dociq.gui.view_models import PackageScope

    outcome, _events, root = real_run
    layout_root = Path(root)

    QApplication.instance() or QApplication([])
    set_pipeline(None)
    window = MainWindow()
    try:
        assert isinstance(window._pipeline, adapter.RealPipeline)
        window.show_outcome(outcome)
        window.show_handoff()

        # A first, successful build — the "earlier build" the screen will later
        # tell the operator about. It has to actually exist for the sentence to
        # be testable.
        window._build_package(PackageScope())
        assert "built" in window.handoff.package_headline().lower(), \
            window.handoff.package_lines()
        earlier = _tree(layout_root / "upload_package")
        assert README_NAME in earlier

        # Now the README write fails, exactly as Codex reproduced it.
        monkeypatch.setattr(h, "render_readme", _raise_lock)
        window.show_handoff()
        window._build_package(PackageScope())

        headline = window.handoff.package_headline()
        lines = window.handoff.package_lines()
        assert "NOT built" in headline
        assert "EARLIER build" in lines

        on_disk = _tree(layout_root / "upload_package")
        assert on_disk == earlier, (
            "the screen says the package on disk is an earlier build and it is "
            "the partial output of the attempt that just failed"
        )
        assert (layout_root / "upload_package" / README_NAME).is_file()
        assert sorted(
            p.name for p in layout_root.iterdir()
            if p.is_dir() and p.name.startswith("upload_package")
        ) == ["upload_package"]
        # And the package still describes itself: a manifest that survived a
        # failed rebuild is only useful if it still matches its own folder.
        sources = json.loads(
            (layout_root / "upload_package" / "sources.json").read_text(
                encoding="utf-8"))
        assert sources
    finally:
        window.close()


def _raise_lock(**_kwargs):
    raise OSError(LOCK_ERROR)


# ---------------------------------------------------------------------------
# A-6 — "build again" must not delete the only intact package
#
# Codex review #2, third fix round. The two renames in `_publish_package` are
# not a transaction, and the state between them — no `upload_package/`, a
# complete previous package under `.dociq/package_superseded/` — is a state the
# tool's OWN failure message hands to the operator, offering "build again" as a
# way out. The next build opened by classifying both state directories as
# disposable residue and deleting them BEFORE assembling anything, so the
# offered recovery destroyed the only thing worth recovering.
#
# Every test below is a TWO-INVOCATION test. One invocation cannot see this: the
# damage is done by what the next build believes about what the last one left.
# ---------------------------------------------------------------------------


def _raise_lock_rename(_src, _dst, **_kw):
    raise OSError(LOCK_ERROR)


def _interrupt_between_the_two_renames(layout, docs, monkeypatch):
    """Leave the matter in the A-6 state THROUGH THE PRODUCT'S OWN FAILURE PATH.

    Not by hand: this is the state
    ``test_a_publish_that_cannot_take_the_name_and_cannot_roll_back_says_where``
    covers and names, so the fixture is the covered failure rather than a
    plausible reconstruction of it. Returns the tree that must survive.
    """
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)
    assert len(before) > 1

    real = h._retry_rename

    def fail_every_publish(src, dst, **kw):
        if dst.name == "upload_package":
            raise OSError(LOCK_ERROR)
        return real(src, dst, **kw)

    monkeypatch.setattr(h, "_retry_rename", fail_every_publish)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, document_count=len(docs))
    monkeypatch.undo()

    # The premise, asserted rather than assumed.
    assert not layout.upload_package.exists()
    assert _tree(_state(layout) / h._SUPERSEDED_NAME) == before
    return before, str(caught.value)


def test_building_again_after_an_interrupted_publish_does_not_destroy_it(
        tmp_path, monkeypatch):
    """FAIL-BEFORE for A-6, through the product's own offered recovery.

    Invocation 1 fails both renames and leaves the only intact package under
    ``.dociq/package_superseded/`` — the state the failure message describes and
    then offers "build again" for. Invocation 2 takes that offer and hits an
    ordinary assembly error.

    On the old code the startup sweep deleted both state directories before
    assembling, so the end state was Codex's: no published package, no staging
    package, no superseded package. The last good package destroyed before any
    replacement existed. The assertion that goes red first is that a package
    exists at all.
    """
    layout, docs = full_matter(tmp_path)
    before, _message = _interrupt_between_the_two_renames(
        layout, docs, monkeypatch)

    # "Build again" — and this build fails the way ordinary builds fail.
    monkeypatch.setattr(h, "render_readme", _raise_lock)
    with pytest.raises(OSError):
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")

    assert layout.upload_package.is_dir(), (
        "building again destroyed the only intact package and then failed: "
        "nothing published, nothing staged, nothing set aside")
    assert _tree(layout.upload_package) == before, (
        "the recovered package is not byte-identical to the one that was "
        "moved aside and never modified")
    assert _siblings(layout) == ["upload_package"]
    assert not (_state(layout) / h._SUPERSEDED_NAME).exists(), (
        "the package was restored AND left behind, so a second copy of it is "
        "sitting under .dociq/")


def test_the_offered_recovery_is_the_one_the_message_describes(
        tmp_path, monkeypatch):
    """Withdraw the CLAIM, not just the code.

    The message that hands the operator this state offers two ways out. Both
    must be true of the code, so both are asserted here rather than only the one
    the fix touched.
    """
    layout, docs = full_matter(tmp_path)
    before, message = _interrupt_between_the_two_renames(
        layout, docs, monkeypatch)
    assert "build again" in message
    assert h._SUPERSEDED_NAME in message

    # Way out 1, the operator's: rename it back by hand.
    aside = _state(layout) / h._SUPERSEDED_NAME
    aside.rename(layout.upload_package)
    assert _tree(layout.upload_package) == before
    layout.upload_package.rename(aside)

    # Way out 2, the one the message offers: build again — and this one goes
    # through, so the recovered package is replaced by a real new one.
    second = build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                                  scope_statement="A NARROWER SCOPE\n")
    assert second.root == layout.upload_package
    assert "A NARROWER SCOPE" in (
        layout.upload_package / README_NAME).read_text(encoding="utf-8")
    assert set(_tree(layout.upload_package)) == set(second.files)
    assert _siblings(layout) == ["upload_package"]


def test_a_process_death_between_the_two_renames_is_recovered(
        tmp_path, monkeypatch):
    """The same state reached by the OTHER route Codex named: the process dies
    between the two publish renames.

    No exception handler ran, so nothing DocIQ does on the way out can be part
    of the recovery. The state on disk is the whole of what the next invocation
    has, which is the point of enumerating states rather than paths.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)

    # The kill: the rename aside happened, the publish rename never did.
    aside = _state(layout) / h._SUPERSEDED_NAME
    aside.parent.mkdir(parents=True, exist_ok=True)
    layout.upload_package.rename(aside)
    assert not layout.upload_package.exists()

    monkeypatch.setattr(h, "render_readme", _raise_lock)
    with pytest.raises(OSError):
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")

    assert _tree(layout.upload_package) == before, (
        "the package that survived a process kill did not survive the next "
        "build's startup sweep")


def test_a_staged_tree_beside_the_only_package_is_not_preferred_to_it(
        tmp_path, monkeypatch):
    """The fourth row of the table: the process died between the renames with
    the staged package still on disk.

    The staged tree may or may not have been validated — nothing on disk says
    which — while the set-aside tree is known complete by construction. So the
    recovery restores the KNOWN one and discards the unknown one, and it does
    the restore first: the other order is A-6 with an extra directory in it.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)

    aside = _state(layout) / h._SUPERSEDED_NAME
    aside.parent.mkdir(parents=True, exist_ok=True)
    layout.upload_package.rename(aside)
    staged = _state(layout) / h._INCOMING_NAME
    staged.mkdir(parents=True)
    (staged / "HALF_A_PACKAGE.txt").write_text("unvalidated", encoding="utf-8")

    monkeypatch.setattr(h, "render_readme", _raise_lock)
    with pytest.raises(OSError):
        build_upload_package(layout, document_count=len(docs))

    assert _tree(layout.upload_package) == before
    assert "HALF_A_PACKAGE.txt" not in _tree(layout.upload_package), (
        "an unvalidated staged tree was published over the known-good one")


def test_an_unrecoverable_restore_deletes_nothing(tmp_path, monkeypatch):
    """The restore itself fails — the folder is held open. The build must stop
    with the set-aside package still on disk, not fall through into the sweep
    that deletes it. Falling through is the whole finding.
    """
    layout, docs = full_matter(tmp_path)
    before, _ = _interrupt_between_the_two_renames(layout, docs, monkeypatch)
    aside = _state(layout) / h._SUPERSEDED_NAME

    monkeypatch.setattr(h, "_retry_rename", _raise_lock_rename)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, document_count=len(docs))

    message = str(caught.value)
    assert h._SUPERSEDED_NAME in message, "the message does not say where it is"
    assert "NOTHING was deleted" in message
    assert _tree(aside) == before, (
        "the build could not restore the only intact package and deleted it")


# The enumeration itself, as a probe. Every state a previous invocation can
# leave on disk, crossed: `upload_package/` present or absent, the two `.dociq/`
# trees present or absent. The invariant under test is one sentence — if a
# complete package existed anywhere when the build started, a complete package
# exists at the published name when it is over, however badly the build went.
_STATES = [
    ("none", False, False, False),
    ("staging-only", False, True, False),
    ("aside-only", False, False, True),
    ("aside-and-staging", False, True, True),
    ("steady", True, False, False),
    ("published-and-staging", True, True, False),
    ("published-and-aside", True, False, True),
    ("published-and-both", True, True, True),
]


@pytest.mark.parametrize("name,published,staging,aside", _STATES,
                         ids=[s[0] for s in _STATES])
def test_no_reachable_start_state_loses_the_only_package(
        tmp_path, monkeypatch, name, published, staging, aside):
    """CLASS PROBE for A-6, not a repro.

    A-6 exists because the package state machine was reasoned forward through
    assembly instead of backward from every state a previous invocation can
    leave behind. This walks the whole cross-product and asserts the same thing
    about each: **no start state that contained a complete package ends without
    one.** A ninth state added later without thought fails to appear here, which
    is the other half of what the enumeration is for.

    The build is always made to fail, because a successful build hides the
    finding: it writes a new package over whatever the sweep destroyed.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    good = _tree(layout.upload_package)
    state = _state(layout)
    state.mkdir(parents=True, exist_ok=True)

    if aside and not published:
        # The only way this state is actually reached: the package was renamed
        # aside and never replaced.
        layout.upload_package.rename(state / h._SUPERSEDED_NAME)
    else:
        if aside:
            (state / h._SUPERSEDED_NAME).mkdir()
            (state / h._SUPERSEDED_NAME / "old.txt").write_text(
                "residue", encoding="utf-8")
        if not published:
            assert h._remove_tree(layout.upload_package)
    if staging:
        (state / h._INCOMING_NAME).mkdir()
        (state / h._INCOMING_NAME / "half.txt").write_text(
            "unvalidated", encoding="utf-8")

    had_a_package = published or aside
    monkeypatch.setattr(h, "render_readme", _raise_lock)
    with pytest.raises((OSError, PackageSwapError)):
        build_upload_package(layout, document_count=len(docs))

    if had_a_package:
        assert layout.upload_package.is_dir(), (
            f"start state {name!r} held a complete package and the build "
            f"destroyed it before failing")
        assert _tree(layout.upload_package) == good, (
            f"start state {name!r} ended with a package that is not the one it "
            f"started with")
    else:
        assert not layout.upload_package.exists(), (
            f"start state {name!r} had no package and a failed build left one")
    assert _siblings(layout) == (["upload_package"] if had_a_package else []), (
        f"start state {name!r} left a package-shaped folder at the matter root")


# ---------------------------------------------------------------------------
# A-7 — cleanup residue is disclosed, not reported as an unqualified success
# ---------------------------------------------------------------------------


def test_an_undeletable_old_package_reaches_the_result_and_the_screen(
        tmp_path, monkeypatch):
    """FAIL-BEFORE for A-7, with a REAL open handle at the moment Codex named.

    A scanner locks one file in the old package *after* the new one has taken
    the published name — so the lock lands between the publish rename and the
    cleanup, which is exactly where the discarded boolean was. The tail of
    ``_publish_package`` called ``_remove_tree`` and threw the answer away, so a
    complete stale copy of the previous package survived under ``.dociq/``
    behind an unqualified "Upload package built."

    Asserts BOTH halves, because the finding is that they disagreed: the
    surviving path on disk, and the wording the operator reads.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    old = _tree(layout.upload_package)
    superseded = _state(layout) / h._SUPERSEDED_NAME

    real = h._retry_rename
    handle = {"fh": None}

    def lock_the_old_package_after_publishing(src, dst, **kw):
        real(src, dst, **kw)
        if dst.name == "upload_package" and superseded.is_dir():
            victim = sorted(p for p in superseded.rglob("*") if p.is_file())[0]
            # A real Windows handle, not a stub: this is what an on-access
            # scanner holds, and it is what makes `rmtree` give up part way.
            handle["fh"] = victim.open("rb")

    monkeypatch.setattr(h, "_retry_rename",
                        lock_the_old_package_after_publishing)
    try:
        package = build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                                       scope_statement="A NARROWER SCOPE\n")
        assert handle["fh"] is not None, "the fixture never took a handle"

        # The package published and is correct — that is why this is a success.
        assert package.root == layout.upload_package
        assert "A NARROWER SCOPE" in (
            layout.upload_package / README_NAME).read_text(encoding="utf-8")

        # …and the old copy is still there, named.
        assert superseded.is_dir(), (
            "the fixture's premise is gone: the old package was removed")
        assert package.residue == (str(superseded),), (
            "the cleanup's answer was discarded and the result reports an "
            "unqualified success over a surviving old package")

        # The screen. Success FIRST, then the residue, then the path.
        from dociq.gui.pipeline import PackageResult
        from dociq.gui.view_models import package_built

        view = package_built(PackageResult(
            root=str(package.root),
            file_count=package.check.file_count,
            total_bytes=package.check.total_bytes,
            scope_statement=package.scope_statement,
            doc_count=package.doc_count,
            residue=package.residue,
            missing=package.missing,
        ))
        assert view.ok
        assert view.headline == "Upload package built."
        note = view.residue_note()
        assert str(superseded) in note, (
            "the operator is not told where the surviving old package is")
        assert "complete and was published normally" in note
        assert "do NOT upload it" in note
        assert str(superseded) not in "\n".join(view.lines), (
            "the residue was folded into the facts about the package that was "
            "built; it is a fact about a different folder")
    finally:
        if handle["fh"] is not None:
            handle["fh"].close()
    # The bytes that survived are the OLD package's, which is what makes this a
    # confusion hazard rather than a disk-space one.
    assert set(_tree(superseded)) <= set(old)


def test_a_clean_build_reports_no_residue(tmp_path):
    """The other side of the same field. A residue note that appears on every
    successful build is a note nobody reads on the one build where it matters.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    second = build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                                  scope_statement="A NARROWER SCOPE\n")
    assert second.residue == ()

    from dociq.gui.pipeline import PackageResult
    from dociq.gui.view_models import package_built

    view = package_built(PackageResult(
        root=str(second.root), file_count=second.check.file_count,
        total_bytes=second.check.total_bytes,
        scope_statement=second.scope_statement, doc_count=second.doc_count,
        residue=second.residue, missing=second.missing,
    ))
    assert view.residue_note() == ""


@pytest.mark.parametrize("fail_at", ["aside-rename", "publish-rename"])
def test_a_staged_package_that_survives_a_failed_publish_is_named(
        tmp_path, monkeypatch, fail_at):
    """CLASS FIX for A-7's shape, at the sites that RAISE rather than return.

    ``_remove_tree(staging)`` is on every failure path out of
    ``_publish_package`` and its answer was discarded at all three, exactly as
    the post-publish call discarded it. What survives is worse than what A-7
    describes: a COMPLETE, validated, package-shaped tree under ``.dociq/`` that
    is not the package the operator should upload — and the message they read
    said nothing about it.

    Enumerated rather than reproduced: both raising sites are driven, and the
    third (the leftover-superseded refusal) is unreachable in the same run
    because it precedes the rename it would have to follow.
    """
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    staging = _state(layout) / h._INCOMING_NAME

    real_remove = h._remove_tree

    def keep_the_staged_tree(path, **kw):
        if path.name == h._INCOMING_NAME:
            return not path.exists()
        return real_remove(path, **kw)

    real_rename = h._retry_rename

    seen = {"n": 0}

    def fail(src, dst, **kw):
        target = (h._SUPERSEDED_NAME if fail_at == "aside-rename"
                  else "upload_package")
        if dst.name == target:
            seen["n"] += 1
            # Only the first attempt on the publish name fails, so the ROLLBACK
            # goes through and the earlier package comes back. That is the
            # branch under test: a recovered matter, and a staged tree nobody
            # was told about still sitting under `.dociq/`.
            if fail_at == "aside-rename" or seen["n"] == 1:
                raise OSError(LOCK_ERROR)
        return real_rename(src, dst, **kw)

    monkeypatch.setattr(h, "_remove_tree", keep_the_staged_tree)
    monkeypatch.setattr(h, "_retry_rename", fail)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")

    assert staging.is_dir(), "the fixture's premise is gone"
    message = str(caught.value)
    assert str(staging) in message, (
        "a complete package-shaped tree survived under .dociq/ and the message "
        "the operator reads does not mention it")
    assert "do NOT upload it" in message
    assert _siblings(layout) == ["upload_package"]
