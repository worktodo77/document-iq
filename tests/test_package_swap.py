"""A failed package build never leaves a CURRENT partial folder — A-4.

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

The fix assembles in a sibling ``upload_package.incoming/`` and claims the
published name only after every copy, filter, README and validation has passed.
So the screen's sentence becomes TRUE rather than merely narrower: on any
failure the folder still holds the earlier build, byte for byte.

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
    """Every ``upload_package*`` directory in the matter folder."""
    return sorted(
        p.name for p in layout.root.iterdir()
        if p.is_dir() and p.name.startswith("upload_package")
    )


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

    leftover = layout.root / "upload_package.incoming"
    leftover.mkdir()
    (leftover / "GHOST.txt").write_text("another attempt", encoding="utf-8")
    monkeypatch.setattr(h, "_remove_tree", lambda p, **_k: not p.exists())

    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, document_count=len(docs))
    assert "upload_package.incoming" in str(caught.value)
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
        if dst.name.endswith(".superseded"):
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


def test_an_unremovable_previous_package_is_put_back_and_nothing_is_published(
        tmp_path, monkeypatch):
    """The previous package cannot be removed, so it is restored and the build
    reports failure.

    This is the state the publish ORDER exists to produce. Removing the earlier
    package after the new one has taken the published name would instead leave a
    correct published package beside a stray folder of the previous one — and
    the GUI would then say "The upload package was NOT built" about a package
    that was built, validated and published. A false headline of that shape is
    finding A-4 again, so the order forbids the state rather than the report
    describing it."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))
    before = _tree(layout.upload_package)

    real = h._remove_tree

    def refuse_superseded(path, **kw):
        if path.name.endswith(".superseded") and path.exists():
            return False
        return real(path, **kw)

    monkeypatch.setattr(h, "_remove_tree", refuse_superseded)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, doc_ids=(docs[0].doc_id,),
                             scope_statement="A NARROWER SCOPE\n")

    message = str(caught.value)
    assert "Nothing was published" in message
    assert "back in place and intact" in message
    assert _tree(layout.upload_package) == before, (
        "the earlier package was destroyed for a package that never arrived")
    assert _siblings(layout) == ["upload_package"]


def test_a_publish_that_cannot_take_the_name_leaves_no_package_and_says_so(
        tmp_path, monkeypatch):
    """The one unrecoverable window: the earlier package is deliberately gone
    and the final rename fails. There must be NO package folder afterwards — a
    ``upload_package.incoming`` left behind is a package-shaped folder an
    operator could upload — and the message must not imply one survives."""
    layout, docs = full_matter(tmp_path)
    build_upload_package(layout, document_count=len(docs))

    real = h._retry_rename

    def fail_the_publish(src, dst, **kw):
        if dst.name == "upload_package":
            raise OSError(LOCK_ERROR)
        return real(src, dst, **kw)

    monkeypatch.setattr(h, "_retry_rename", fail_the_publish)
    with pytest.raises(PackageSwapError) as caught:
        build_upload_package(layout, document_count=len(docs))

    assert "no package folder remains" in str(caught.value)
    assert _siblings(layout) == [], (
        f"a package-shaped folder survives: {_siblings(layout)}")


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
