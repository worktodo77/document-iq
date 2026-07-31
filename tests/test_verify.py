"""The Stage-6 gate, the manifest, and the byte-identical claim."""

from __future__ import annotations

import json

import pytest

from dociq.contracts import (
    Disposition,
    DocumentRecord,
    PageKind,
    PageRecord,
    ProcessingStatus,
    RunConfig,
    RunResult,
)
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.verify import accounting, manifest, probe_emit

from .conftest import FIXTURES


def _page(n: int, **kw) -> PageRecord:
    return PageRecord(page_no=n, text=f"page {n}", kind=PageKind.NATIVE, **kw)


def _doc(rel: str = "a.pdf", pages=(1, 2), **kw) -> DocumentRecord:
    return DocumentRecord(doc_id="", rel_path=rel, filename=rel, sha256="0" * 64,
                          size_bytes=1, ext=".pdf",
                          pages=tuple(_page(n) for n in pages), **kw)


def _result(*docs, **kw) -> RunResult:
    cfg = RunConfig(source_root="s", output_root="o")
    return RunResult(config=cfg, documents=tuple(docs), **kw)


def test_clean_corpus_reconciles():
    rep = accounting.check(_result(_doc()))
    assert rep.ok
    assert "PAGE ACCOUNTING OK" in rep.render()


def test_a_dropped_page_without_a_rule_is_named_not_just_counted():
    doc = DocumentRecord(
        doc_id="", rel_path="bad.pdf", filename="bad.pdf", sha256="0" * 64,
        size_bytes=1, ext=".pdf",
        pages=(PageRecord(page_no=1, text="x", kind=PageKind.NATIVE,
                          disposition=Disposition.DROP, drop_rule=""),))
    rep = accounting.check(_result(doc))
    assert not rep.ok
    assert any("bad.pdf" in str(d) for d in rep.discrepancies)


def test_a_page_number_gap_is_reported_against_its_document():
    doc = DocumentRecord(doc_id="", rel_path="gap.pdf", filename="gap.pdf",
                         sha256="0" * 64, size_bytes=1, ext=".pdf",
                         pages=(_page(1), _page(3)))
    rep = accounting.check(_result(doc))
    assert not rep.ok
    assert any(d.rel_path == "gap.pdf" and d.kind == "contract"
               for d in rep.discrepancies)


def test_an_orphan_container_child_is_reported():
    child = _doc("z.zip/a.pdf", parent_doc_id="missing.zip", container_order=0)
    rep = accounting.check(_result(child))
    assert any(d.kind == "orphan-child" for d in rep.discrepancies)


def test_a_failed_document_with_no_message_is_a_discrepancy():
    doc = _doc("f.pdf", pages=(1,), status=ProcessingStatus.FAILED)
    rep = accounting.check(_result(doc))
    assert any(d.kind == "silent-failure" for d in rep.discrepancies)


def test_two_documents_sharing_a_path_would_overwrite_and_are_caught():
    rep = accounting.check(_result(_doc("dup.pdf"), _doc("dup.pdf")))
    assert any(d.kind == "duplicate-record" for d in rep.discrepancies)


def test_report_names_the_document_not_just_a_boolean():
    doc = DocumentRecord(doc_id="", rel_path="deep/nested/thing.pdf",
                         filename="thing.pdf", sha256="0" * 64, size_bytes=1,
                         ext=".pdf", pages=(_page(2),))
    text = accounting.check(_result(doc)).render()
    assert "deep/nested/thing.pdf" in text


# -- manifest ---------------------------------------------------------------


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    out = tmp_path_factory.mktemp("emit")
    cfg = RunConfig(source_root=str(FIXTURES), output_root=str(out),
                    ocr_engine_version=ex.ocr_engine_version())
    result = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=False))
    probe_emit.write(result)
    return out, result


def test_manifest_covers_exactly_the_claimed_files(emitted):
    out, _ = emitted
    man = manifest.build(out)
    assert "sources.json" in man.deterministic
    assert "document_index.csv" in man.deterministic
    assert any(k.startswith("clean_text/") for k in man.deterministic)
    assert man.log_content_sha256


def test_manifest_states_the_split_rather_than_leaving_it_ambiguous(emitted):
    out, _ = emitted
    payload = json.loads(manifest.write(out).read_text(encoding="utf-8"))
    assert "byte-identical" in payload["claim"]
    assert "content" in payload["claim"] and "processing_log.json" in payload["claim"]
    assert manifest.LOG_NAME in payload["excluded"]
    assert "run" in payload["excluded"][manifest.LOG_NAME]


def test_an_undeclared_output_is_reported_unclassified_not_assumed(emitted, tmp_path):
    out, _ = emitted
    (out / "surprise.dat").write_bytes(b"x")
    try:
        man = manifest.build(out)
        assert "surprise.dat" in man.unclassified
        assert "UNCLASSIFIED" in man.render()
    finally:
        (out / "surprise.dat").unlink()


def test_the_excluded_files_are_not_compared(emitted):
    out, _ = emitted
    a = manifest.build(out)
    b = manifest.build(out)
    b.excluded["run_summary.pdf"] = "different reason"
    assert manifest.compare(a, b) == []


def test_compare_names_the_file_that_diverged(emitted):
    out, _ = emitted
    a = manifest.build(out)
    b = manifest.build(out)
    key = next(iter(b.deterministic))
    b.deterministic[key] = "f" * 64
    assert any(key in d for d in manifest.compare(a, b))


def test_clean_text_carries_a_marker_for_every_page_including_empty(emitted):
    out, result = emitted
    doc = next(d for d in result.documents
               if d.rel_path == "04_empty_page.pdf")
    ids = probe_emit._doc_ids(result)
    text = (out / "clean_text" / f"{ids[doc.rel_path]}.txt").read_text(
        encoding="utf-8")
    assert text.count("===== PAGE") == doc.pages_in == 3


def test_an_empty_output_root_is_a_hard_failure_not_a_stable_hash(tmp_path):
    """An empty manifest hashes perfectly stably, so two runs that produced
    nothing would compare byte-identical and the gate would go green on a
    pipeline that did no work."""
    with pytest.raises(manifest.EmptyOutputError):
        manifest.build(tmp_path / "never-created")
    (tmp_path / "empty").mkdir()
    with pytest.raises(manifest.EmptyOutputError):
        manifest.build(tmp_path / "empty")
    assert manifest.build(tmp_path / "empty", require_outputs=False).deterministic == {}


def test_determinism_reports_a_run_that_produced_nothing(tmp_path, monkeypatch):
    from dociq.verify import determinism

    monkeypatch.setattr(determinism, "_one_run", lambda src, out, seed: None)
    rep = determinism.prove(tmp_path, runs=3, workdir=tmp_path / "det")
    assert not rep.ok
    assert len(rep.failures) == 3
    assert any("no subject" in f or "does not exist" in f for f in rep.failures)


def test_pptx_extraction_never_touches_notes_slide_on_a_deck_without_notes(
        monkeypatch):
    """python-pptx's ``notes_slide`` property CREATES a notes slide when none
    exists. Reading it unguarded manufactures parts the source file never had,
    on every slide of every deck. Make the property fatal and prove the
    extractor no longer reaches it."""
    import io as _io

    from pptx import Presentation
    from pptx.slide import Slide

    from dociq.ingest import extract as _ex

    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])
    buf = _io.BytesIO()
    prs.save(buf)

    class _Touched(BaseException):
        """Not an Exception: the extractor wraps the notes read in a broad
        ``except Exception`` — a probe that the code under test can swallow is
        a probe that cannot fail."""

    def explode(self):
        raise _Touched("notes_slide read on a deck with no notes")

    monkeypatch.setattr(Slide, "notes_slide", property(explode))
    got = _ex.extract("deck.pptx", buf.getvalue())
    assert got.status is not ProcessingStatus.FAILED
    assert len(got.pages) == 1
