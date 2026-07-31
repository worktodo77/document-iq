"""Stage 1 — the walk, and the run it drives."""

from __future__ import annotations

import json
import subprocess

import pytest

from dociq.contracts import ProcessingStatus, RunConfig, document_sort_key
from dociq.ingest import extract as ex
from dociq.ingest import walker

from .conftest import FIXTURES


def _cfg(tmp_path) -> RunConfig:
    return RunConfig(source_root=str(FIXTURES), output_root=str(tmp_path / "out"),
                     ocr_engine_version=ex.ocr_engine_version())


def _fast(**kw) -> walker.WalkOptions:
    # OCR off unless a test is about OCR: the walk's behaviour under test is
    # ordering, tiering and accounting, and paying for OCR to prove those
    # would make the suite too slow to run often enough to matter.
    kw.setdefault("ocr_enabled", False)
    kw.setdefault("resume", False)
    return walker.WalkOptions(**kw)


def test_scan_is_in_contract_order_and_hashes_everything():
    entries = walker.scan(FIXTURES)
    assert entries == sorted(entries, key=lambda e: (e.rel_path, e.sha256))
    assert all(len(e.sha256) == 64 for e in entries)
    assert all("\\" not in e.rel_path for e in entries)


def test_tiering_follows_section_3():
    tiers = {e.rel_path: e.tier for e in walker.scan(FIXTURES)}
    assert tiers["01_native_report.pdf"] == 1
    assert tiers["13_legacy.doc"] == 2


def test_unknown_extension_defaults_to_tier_2():
    assert walker.tier_of(".xer") == 2
    assert walker.tier_of(".wibble") == 2
    assert walker.tier_of(".PDF") == 1


def test_duplicate_by_hash_is_detected():
    dups = walker.duplicate_groups(walker.scan(FIXTURES))
    assert any(sorted(v) == ["07_ncr_log.csv",
                             "attachments/12_ncr_log_copy.csv"]
               for v in dups.values())


def test_run_orders_documents_by_the_contract_key(tmp_path):
    r = walker.run(_cfg(tmp_path), _fast())
    assert list(r.documents) == sorted(r.documents, key=document_sort_key)
    assert list(r.unsupported) == sorted(r.unsupported, key=document_sort_key)


def test_tier2_files_are_inventoried_hashed_and_never_block(tmp_path):
    r = walker.run(_cfg(tmp_path), _fast())
    doc = next(d for d in r.unsupported if d.ext == ".doc")
    assert doc.sha256 and doc.size_bytes > 0
    assert doc.status is ProcessingStatus.UNSUPPORTED
    assert "Save-As" in (doc.error or "")


def test_archive_children_carry_parent_and_order(tmp_path):
    r = walker.run(_cfg(tmp_path), _fast())
    kids = [d for d in r.documents if d.parent_doc_id]
    assert kids and all(d.parent_doc_id == "11_production.zip" for d in kids)
    assert sorted(d.container_order for d in kids) == [0, 1, 2]


def test_every_record_validates(tmp_path):
    r = walker.run(_cfg(tmp_path), _fast())
    for d in list(r.documents) + list(r.unsupported):
        d.validate()


def test_eml_attachment_becomes_a_child_document_with_parent_and_order(tmp_path):
    """§3: MSG/EML attachments are Tier-1 child documents. The walker must
    route .eml through the same container pattern as .zip — attachment bytes
    become their own DocumentRecord, not vanish."""
    from email.message import EmailMessage

    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    msg = EmailMessage()
    msg["From"] = "engineer@example.com"
    msg["Subject"] = "Notice with attachment"
    msg.set_content("Please see the attached daily log.")
    msg.add_attachment(b"2024-07-16 daily log body", maintype="text",
                       subtype="plain", filename="daily_log.txt")
    (src / "notice.eml").write_bytes(msg.as_bytes())

    cfg = RunConfig(source_root=str(src), output_root=str(out),
                    ocr_engine_version=ex.ocr_engine_version())
    r = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=False))

    parent = next(d for d in r.documents if d.rel_path == "notice.eml")
    child = next(d for d in r.documents if d.parent_doc_id == "notice.eml")
    assert child.rel_path == "notice.eml/daily_log.txt"
    assert child.container_order == 0
    assert "daily log body" in child.pages[0].text
    assert any("attachment(s) extracted as child document" in n
              for n in parent.notes)
    for d in r.documents:
        d.validate()


def test_scratch_directory_does_not_survive_the_run(tmp_path):
    cfg = _cfg(tmp_path)
    walker.run(cfg, _fast())
    assert not (tmp_path / "out" / walker.STATE_DIR / "scratch").exists()


def test_resume_replays_the_previous_run_instead_of_re_extracting(tmp_path):
    cfg = _cfg(tmp_path)
    first = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    # A completed run discards its journal, so re-arm it by hand — the case
    # under test is a crash, and a crashed run leaves the journal behind.
    by_source: dict[str, list] = {}
    for d in first.documents:
        by_source.setdefault(d.parent_doc_id or d.rel_path, []).append(d)
    w = walker._ResumeWriter(cfg, True)
    for source, docs in by_source.items():
        w.add(source, docs)
    w.close(discard=False, output_root=tmp_path / "out")

    second = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    assert any("resumed" in w for w in second.warnings)
    assert {d.rel_path for d in second.documents} == \
        {d.rel_path for d in first.documents}


def test_resume_re_extracts_a_file_that_changed_since_the_crash(tmp_path):
    """A file edited between the interrupted run and the resumed one is the
    exact scenario resume exists for (operator fixes a corrupt file, then
    reruns) — and it must not be served stale from the journal. Before the
    fix, the journal was replayed on rel_path alone, with no check against the
    file's current sha256: a changed file came back with yesterday's text
    under today's run identity, silently."""
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    (src / "a.txt").write_text("VERSION ONE", encoding="utf-8")
    cfg = RunConfig(source_root=str(src), output_root=str(out),
                    ocr_engine_version=ex.ocr_engine_version())

    first = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    assert first.documents[0].pages[0].text == "VERSION ONE"

    # Re-arm the journal by hand, as if the run had crashed mid-way (a
    # completed run discards it) — same technique as the sibling test above.
    by_source: dict[str, list] = {}
    for d in first.documents:
        by_source.setdefault(d.parent_doc_id or d.rel_path, []).append(d)
    w = walker._ResumeWriter(cfg, True)
    for source, docs in by_source.items():
        w.add(source, docs)
    w.close(discard=False, output_root=out)

    # The file changes on disk before the resumed run — e.g. the operator
    # fixed the file that caused the crash.
    (src / "a.txt").write_text("VERSION TWO, edited after the crash",
                                encoding="utf-8")

    second = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    doc = second.documents[0]
    assert doc.pages[0].text == "VERSION TWO, edited after the crash"
    assert doc.sha256 == walker.sha256_file(src / "a.txt")
    assert any("changed on disk" in w for w in second.warnings)


def test_resume_is_discarded_when_the_run_identity_changed(tmp_path):
    cfg = _cfg(tmp_path)
    walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    journal = walker._resume_path(tmp_path / "out")
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"identity": "a different profile"}) + "\n",
                       encoding="utf-8", newline="\n")
    assert walker._load_resume(cfg) == {}


def test_a_directory_junction_loop_is_not_followed_forever(tmp_path):
    """``Path.rglob`` alone walks straight into a self-referential Windows
    junction: confirmed by hand it re-discovers the same file at ever deeper
    synthetic paths (``sub/loop/sub/loop/...``) until a filesystem path-length
    limit finally stops it, ~20 levels / dozens of phantom duplicate
    ``FileEntry`` rows deep on this machine. ``Path.is_symlink()`` cannot be
    the guard — a directory junction is a different reparse tag and does not
    report as a symlink — so the fix tracks each directory's resolved real
    path instead (``os.path.realpath``), which resolves a junction the same
    as a symlink."""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.txt").write_text("hi", encoding="utf-8")
    loop = root / "sub" / "loop"
    try:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(loop), str(root)],
                       check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"could not create a directory junction: {exc}")

    notes: list[str] = []
    entries = walker.scan(root, notes=notes)
    assert [e.rel_path for e in entries] == ["sub/f.txt"]
    assert any("symlink or junction loop" in n for n in notes)

    cfg = RunConfig(source_root=str(root), output_root=str(tmp_path / "out"),
                    ocr_engine_version=ex.ocr_engine_version())
    r = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=False))
    assert [d.rel_path for d in r.documents] == ["sub/f.txt"]
    assert any("symlink or junction loop" in w for w in r.warnings)


def test_disk_preflight_fails_before_the_work_not_during(tmp_path, monkeypatch):
    monkeypatch.setattr(walker, "_DISK_HEADROOM", 1e12)
    r = walker.run(_cfg(tmp_path), _fast())
    assert r.documents == ()
    assert any("Insufficient disk" in w for w in r.warnings)


def test_cancel_stops_the_run_and_says_so(tmp_path):
    r = walker.run(_cfg(tmp_path), _fast(cancelled=lambda: True))
    assert any("cancelled" in w for w in r.warnings)


def test_progress_callback_reports_without_reaching_disk(tmp_path):
    seen: list[dict] = []
    walker.run(_cfg(tmp_path), _fast(progress=seen.append))
    assert seen and set(seen[-1]) >= {"done", "total", "file", "failed"}


def test_state_directory_is_never_ingested_as_evidence(tmp_path):
    cfg = _cfg(tmp_path)
    walker.run(cfg, _fast())
    entries = walker.scan(tmp_path / "out")
    assert all(walker.STATE_DIR not in e.rel_path for e in entries)


def test_unreadable_file_is_recorded_not_dropped(tmp_path, monkeypatch):
    real = walker.Path.read_bytes

    def boom(self):
        if self.name == "01_native_report.pdf":
            raise OSError("permission denied")
        return real(self)

    monkeypatch.setattr(walker.Path, "read_bytes", boom)
    r = walker.run(_cfg(tmp_path), _fast())
    doc = next(d for d in r.documents if d.rel_path == "01_native_report.pdf")
    assert doc.status is ProcessingStatus.FAILED and "read failed" in doc.error


def test_warning_order_is_deterministic_under_concurrent_failures(tmp_path):
    """Errors arrive in thread-completion order; the warnings they become are
    hashed content. The order therefore has to come from the data, not from
    the scheduler — so run the same broken corpus at several pool widths."""
    src = tmp_path / "broken"
    src.mkdir()
    for i in range(40):
        (src / f"{i:03d}_bad.pdf").write_bytes(b"not a pdf " + bytes([i]))

    seen = set()
    for workers in (1, 2, 4, 8, 16):
        for _ in range(3):
            cfg = RunConfig(source_root=str(src),
                            output_root=str(tmp_path / f"out{workers}"))
            r = walker.run(cfg, walker.WalkOptions(ocr_enabled=False,
                                                   resume=False,
                                                   workers=workers))
            seen.add(tuple(r.warnings))
    assert len(seen) == 1, f"{len(seen)} distinct warning orders across widths"


def test_error_cap_keeps_a_deterministic_slice_and_says_what_it_dropped():
    errs = walker._Errors(cap=3)
    for name in ("d", "a", "c", "b", "e"):
        errs.record(f"{name}.pdf", "boom")
    out = errs.as_list()
    assert [d["file"] for d in out[:3]] == ["a.pdf", "b.pdf", "c.pdf"]
    assert "2 further error(s) omitted" in out[-1]["error"]


def test_a_long_error_message_says_it_was_truncated():
    errs = walker._Errors()
    errs.record("x.pdf", "y" * 5000)
    assert "truncated at 300 chars" in errs.as_list()[0]["error"]


def test_a_tier2_archive_member_lands_on_the_unsupported_list(tmp_path):
    """§3 puts every listed-only file on the Unsupported list. A .doc that
    arrived inside a ZIP is still a .doc, and an index that answers "was this
    processed?" two different ways is an index nobody can rely on."""
    r = walker.run(_cfg(tmp_path), _fast())
    legacy = [d for d in r.unsupported if d.ext == ".doc"]
    assert len(legacy) == 2, [d.rel_path for d in r.unsupported]
    inside = next(d for d in legacy if d.parent_doc_id)
    assert inside.parent_doc_id == "11_production.zip"
    assert inside.container_order is not None
    assert all(d.status is not ProcessingStatus.UNSUPPORTED
               for d in r.documents)
