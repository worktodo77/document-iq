"""A failure caused by LOAD must not be recorded as a fact about the document.

Two full runs over the real 368-document corpus, byte-identical inputs and
byte-identical settings, disagreed about one PowerPoint file: under an
OCR-loaded worker pool it was recorded ``failed`` with a malformed-XML error and
zero pages, and with OCR off it read all 35 slides. Ten isolated re-reads of
that file parsed cleanly; 72 concurrent attempts across 12 rounds never
reproduced the failure. The mechanism is not known.

The mechanism is also not the defect. The defect is that a load-dependent event
was written into the deliverables as a property of the evidence — "this document
is unreadable" — so the corpus a reviewer receives depended on how busy the
machine was when it was built. Principle 1 held (the failure was recorded,
loudly). Principle 5 did not.

Everything here is about the remedy, which needs no mechanism: nothing that
failed while sixteen other documents were being extracted is written off until
it has been tried once more, alone, and a run that needed that retry says so —
in the log's ``run`` section, where run-varying facts already live, and never in
the hashed ``content``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace

from dociq import pipeline
from dociq.contracts import ProcessingStatus, RunConfig, to_jsonable
from dociq.ingest import extract as ex
from dociq.ingest import walker


def _corpus(tmp_path, n: int = 6):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(n):
        (src / f"{i:02d}_doc.txt").write_text(f"document {i} body text",
                                              encoding="utf-8")
    return src


def _cfg(src, out) -> RunConfig:
    return RunConfig(source_root=str(src), output_root=str(out))


def _opts(**kw) -> walker.WalkOptions:
    kw.setdefault("ocr_enabled", False)
    kw.setdefault("resume", False)
    return walker.WalkOptions(**kw)


def _fail_first_time(monkeypatch, target: str, *, error: str | None = None,
                     marker: str | None = None):
    """Make ``target`` fail exactly once, then behave normally.

    This is the shape of the observed defect: the same bytes, read twice, with
    two different outcomes. Which of the two the run keeps is the whole
    question.
    """
    real = ex.extract
    fired: list[str] = []

    def flaky(filename, raw, opt=None):
        if filename == target and not fired:
            fired.append(filename)
            if marker is not None:
                got = real(filename, raw, opt)
                return ex.ExtractedDoc(
                    pages=tuple(replace(p, notes=(marker,)) for p in got.pages),
                    notes=got.notes, status=got.status)
            return ex.ExtractedDoc(status=ProcessingStatus.FAILED,
                                   error=error or "transient: lost a race")
        return real(filename, raw, opt)

    monkeypatch.setattr(walker.ex, "extract", flaky)
    return fired


# ---------------------------------------------------------------------------
# The claim itself
# ---------------------------------------------------------------------------


def test_a_load_dependent_failure_leaves_no_trace_in_the_hashed_content(
        tmp_path, monkeypatch):
    """The gate's actual claim: identical inputs, identical output — whether or
    not one document happened to lose a race on one of the two runs.

    Before the serial retry this failed exactly as the real corpus did: the
    flaky run carried a ``failed`` record with zero pages and an extra warning,
    so its hashed content — and therefore ``corpus_sha256`` — differed from the
    clean run's with no difference in the evidence anywhere."""
    src = _corpus(tmp_path)
    clean = walker.run(_cfg(src, tmp_path / "clean"), _opts())

    _fail_first_time(monkeypatch, "03_doc.txt")
    flaky = walker.run(_cfg(src, tmp_path / "flaky"), _opts())

    assert [to_jsonable(d) for d in flaky.documents] == \
        [to_jsonable(d) for d in clean.documents]
    assert flaky.warnings == clean.warnings


def test_the_retry_is_disclosed_in_the_log_run_section_not_its_content(
        tmp_path, monkeypatch):
    """Silently repairing the corpus would be worse than the defect. The run
    that needed a retry has to say so — and it has to say so where run-varying
    facts already live, or the disclosure itself breaks the byte-identical
    claim it was written to defend."""
    src = _corpus(tmp_path)
    _fail_first_time(monkeypatch, "02_doc.txt",
                     error="transient: lost a race under load")
    outcome = pipeline.run(
        _cfg(src, tmp_path / "out"),
        pipeline.PipelineOptions(walk=_opts(), write_workbook=False,
                                 write_summary_pdf=False, write_package=False))

    disclosure = outcome.log.run["load_dependent_extraction"]
    assert any("02_doc.txt" in d for d in disclosure), disclosure
    one = next(d for d in disclosure if "02_doc.txt" in d)
    assert "lost a race under load" in one   # the pooled outcome
    assert "Serial:" in one                  # and the serial one
    assert "RESOLVED" in one

    blob = json.dumps(outcome.log.content)
    assert "load_dependent" not in blob
    assert "lost a race under load" not in blob
    # and the operator sees it
    assert any("LOAD-DEPENDENT" in w for w in outcome.result.warnings)


def test_a_genuinely_unreadable_document_still_fails_after_the_serial_retry(
        tmp_path):
    """The retry must not become a way to launder a real failure. A file that
    fails alone as well as under load is recorded FAILED, exactly as before."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.pdf").write_bytes(b"this is not a PDF at all")
    r = walker.run(_cfg(src, tmp_path / "out"), _opts())
    doc = next(d for d in r.documents if d.rel_path == "broken.pdf")
    assert doc.status is ProcessingStatus.FAILED
    assert any("broken.pdf" in w for w in r.warnings)


def test_a_page_level_degradation_also_triggers_the_serial_retry(
        tmp_path, monkeypatch):
    """A whole-document failure is the loud member of the class. The quiet ones
    — a page that would not rasterize, an attachment list that would not
    enumerate, an archive member that would not read — degrade the corpus
    silently and were just as load-dependent."""
    src = _corpus(tmp_path)
    clean = walker.run(_cfg(src, tmp_path / "clean"), _opts())

    fired = _fail_first_time(monkeypatch, "04_doc.txt",
                             marker=f"{ex.M_OCR_PAGE} to rasterize or read")
    notes = walker.RunNotes()
    flaky = walker.run(_cfg(src, tmp_path / "flaky"), _opts(), notes)

    assert fired, "the injection never fired"
    assert [to_jsonable(d) for d in flaky.documents] == \
        [to_jsonable(d) for d in clean.documents]
    assert any("04_doc.txt" in d for d in notes.load_dependent)


def test_every_marker_the_extractor_emits_is_one_the_retry_looks_for():
    """The registry is the mechanism. A degradation path that invents its own
    wording is invisible to the retry, so the wording lives in constants and
    the constants are the matcher's input — asserted here so the two cannot
    drift apart in a later edit."""
    for marker in ex.TRANSIENT_MARKERS:
        assert ex.has_transient_marker(f"prefix: {marker} and a tail")
    assert not ex.has_transient_marker("XLSX has no page boundaries")
    assert not ex.has_transient_marker(None)


# ---------------------------------------------------------------------------
# The siblings
# ---------------------------------------------------------------------------


def test_a_resumed_run_and_a_fresh_run_have_the_same_hashed_warnings(tmp_path):
    """Whether an earlier run crashed is not a fact about the evidence.

    The resume notes were appended to the hashed warning list, so a matter
    reduced in one pass and the same matter reduced after a crash produced
    different ``content`` bytes and a different ``corpus_sha256`` — a
    determinism break with no bad input anywhere, and the same class as the
    retry defect."""
    src = _corpus(tmp_path)
    cfg = _cfg(src, tmp_path / "out")
    first = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))

    # A completed run discards its journal; re-arm it by hand, as a crash would.
    w = walker._ResumeWriter(cfg, True)
    for d in first.documents:
        w.add(d.parent_doc_id or d.rel_path, [d])
    w.close(discard=False, output_root=tmp_path / "out")

    notes = walker.RunNotes()
    second = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True),
                        notes)
    assert second.warnings == first.warnings
    assert any("RESUMED RUN" in n for n in notes.invocation)


def test_resume_never_replays_a_failure_the_interrupted_run_recorded(tmp_path):
    """The interrupted run is by definition the run that was under stress, so
    its failures are the likeliest load-dependent ones in the whole system —
    and replaying one puts it beyond the reach of the serial retry forever."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("REAL CONTENT", encoding="utf-8")
    cfg = _cfg(src, tmp_path / "out")

    first = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    doc = first.documents[0]
    failed = replace(doc, pages=(), status=ProcessingStatus.FAILED,
                     detected_dates=(), notes=(),
                     error="transient failure under load")
    w = walker._ResumeWriter(cfg, True)
    w.add(doc.rel_path, [failed])
    w.close(discard=False, output_root=tmp_path / "out")

    notes = walker.RunNotes()
    second = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True),
                        notes)
    again = second.documents[0]
    assert again.status is not ProcessingStatus.FAILED
    assert again.pages[0].text == "REAL CONTENT"
    assert any("recorded a failure" in n for n in notes.invocation)


def test_the_journal_keeps_the_last_batch_for_a_file_not_both(tmp_path):
    """One source file can be journaled twice in a single run now that the
    retry re-journals what it adopted. Appending both — which is what the
    first draft did — would replay the failed record AND the retried one on the
    next resume: a duplicate document under two Doc IDs, and a resurrected
    failure, produced by the fix."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello", encoding="utf-8")
    cfg = _cfg(src, tmp_path / "out")
    first = walker.run(cfg, walker.WalkOptions(ocr_enabled=False, resume=True))
    doc = first.documents[0]
    stale = replace(doc, pages=(), status=ProcessingStatus.FAILED,
                    detected_dates=(), notes=(), error="the pooled attempt")

    w = walker._ResumeWriter(cfg, True)
    w.add(doc.rel_path, [stale])
    w.add(doc.rel_path, [doc])
    w.close(discard=False, output_root=tmp_path / "out")

    replay = walker._load_resume(cfg)
    assert len(replay[doc.rel_path]) == 1
    assert replay[doc.rel_path][0].status is not ProcessingStatus.FAILED


def test_a_file_that_would_not_open_for_hashing_is_tried_a_second_time(
        tmp_path, monkeypatch):
    """A backup agent or a virus scanner holding a file for an instant used to
    demote it to Tier 2 permanently, with a zero hash and a ``-1`` size — the
    same class, at the one stage that runs before any of it."""
    src = _corpus(tmp_path, n=3)
    real = walker.sha256_file
    fired: list[str] = []

    def flaky(path):
        if path.name == "01_doc.txt" and not fired:
            fired.append(path.name)
            raise OSError("the process cannot access the file")
        return real(path)

    monkeypatch.setattr(walker, "sha256_file", flaky)
    notes = walker.RunNotes()
    entries = walker.scan(src, run_notes=notes)

    entry = next(e for e in entries if e.rel_path == "01_doc.txt")
    assert fired and entry.tier == 1 and len(entry.sha256) == 64
    assert not entry.unreadable
    assert any("TRANSIENT READ" in n for n in notes.invocation)


def test_a_permanently_unreadable_file_is_not_called_an_unknown_format(
        tmp_path, monkeypatch):
    """Two attempts, both refused. The file is still inventoried — dropping it
    would be a silent deletion — but the deliverable used to explain a locked
    .pdf as "Unrecognized format — inventoried and hashed only", which is false
    twice over: the format is recognized, and it was not hashed."""
    src = _corpus(tmp_path, n=2)
    (src / "locked.pdf").write_bytes(b"%PDF-1.4 stub")

    real = walker.sha256_file

    def always(path):
        if path.name == "locked.pdf":
            raise OSError("permission denied")
        return real(path)

    monkeypatch.setattr(walker, "sha256_file", always)
    r = walker.run(_cfg(src, tmp_path / "out"), _opts())

    row = next(d for d in r.unsupported if d.rel_path == "locked.pdf")
    assert "Could not be opened for reading" in (row.error or "")
    assert ex.UNKNOWN_HINT not in (row.error or "")


def test_an_abandoned_extraction_records_no_wall_clock_in_hashed_content(
        tmp_path, monkeypatch):
    """A watchdog timeout wrote "abandoned after 37s" into the document's
    error, and that string is hashed content. Two runs over the same corpus
    that both time out on the same file therefore produced different bytes,
    by construction, on a machine that was merely a little busier the second
    time — a determinism break hiding inside the guard that exists to keep a
    stuck file from hanging the run.

    How long it took is a fact about the invocation. That the limit was
    reached is a fact about the document, and it is the one the record keeps.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "slow.txt").write_text("x", encoding="utf-8")
    for i in range(4):  # so the pool loop returns before the slow file does
        (src / f"quick{i}.txt").write_text("y", encoding="utf-8")
    real = ex.extract

    def slow(filename, raw, opt=None):
        if filename == "slow.txt":
            time.sleep(8)
        return real(filename, raw, opt)

    monkeypatch.setattr(walker.ex, "extract", slow)

    errors = []
    for i in range(2):
        notes = walker.RunNotes()
        r = walker.run(_cfg(src, tmp_path / f"out{i}"),
                       _opts(file_timeout_s=0.3), notes)
        doc = next(d for d in r.documents if d.rel_path == "slow.txt")
        assert doc.status is ProcessingStatus.FAILED
        assert not re.search(r"\d", doc.error or ""), doc.error
        errors.append(doc.error)
        assert any("did not finish" in n for n in notes.messages()), notes.messages()

    assert errors[0] == errors[1], errors
