"""End to end, in two halves: the composed stages, and the real pipeline.

**Part 1 composes Stages 3 -> 3b -> 4 -> 5 on stub records**, as this module
always has. Track A owns Stages 1-2, so what this half is accountable for is
that the stages compose, that page accounting survives them, and that the
outputs inside the byte-identical claim really are byte-identical across
repeated runs.

**Stage 4 in that composition is no longer a profile (D-35).** Until commit
`4092f76` this file drove ``dociq.profiles.apply.apply_profiles``, which matched
a rule's regex against every line of every page and carried the matched section
forward until another rule matched. That module is deleted. Every guarantee the
old Stage-4 call was here to serve — pages drop, accounting reconciles, markers
keep the ORIGINAL page numbers, the outputs are reproducible — is a guarantee
about *composition* rather than about the matcher, so every one of them is
repointed at the mechanism that replaced it: spans rebuilt from the stamped
pages (:func:`dociq.sections.resolve.spans_from_pages`), a shipped
:class:`~dociq.sections.model.SectionTemplate`, and an
:class:`~dociq.sections.model.ApprovedOmission` naming a person (D-34). No test
was dropped for the swap.

**Part 2 drives the real pipeline** over a matter folder this module authors —
a PDF carrying its own outline, so recognition happens where the product does
it, at extraction. It exists because the chain the redesign actually rests on
has five links and nothing covered all five together:

    the PDF's outline -> ``extract.pdf_spans`` -> ``walker._record`` stamps the
    page -> ``spans_from_pages`` rebuilds the span at Stage 4 -> the disposition
    reaches the emitted clean text.

``tests/test_sections.py`` proves the unit-level rules against hand-built spans.
It cannot prove that a real document produces those spans, and it cannot prove
that a bounded span survives the trip through the page records. That is what is
here.

**No client text anywhere.** Every word of the authored corpus is written in
this file.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from dociq import pipeline
from dociq.contracts import (
    Disposition,
    DocumentRecord,
    OmissionSnapshot,
    RecognitionTier,
    RunConfig,
    run_identity,
)
from dociq.docid.assign import assign_doc_ids
from dociq.docid.reconcile import IssuedIdLedger, detect_renumbering, reconcile
from dociq.emit.cleantext import write_clean_text, write_sources_json
from dociq.emit.handoff import build_upload_package, expert_assist_layout
from dociq.emit.indexbook import (
    build_index_rows,
    write_index_csv,
    write_index_xlsx,
    write_reconciliation_csv,
)
from dociq.emit.log import build_log, write_processing_log
from dociq.emit.paths import OutputLayout
from dociq.emit.summary import build_summary_data, write_run_summary
from dociq.identify.bates import (
    BatesDecision,
    DecisionStatus,
    apply_bates,
    document_ranges,
    propose_format,
    ranges_by_sort_key,
)
from dociq.ingest import extract as ex
from dociq.ingest import walker
from dociq.profiles.model import (
    MATTER_COPY_DIRNAME,
    FormatProfile,
    OperatorStamp,
    SectionRule,
)
from dociq.sections.apply import SectionDropEntry, apply_sections
from dociq.sections.model import ApprovedOmission
from dociq.sections.resolve import spans_from_pages
from dociq.sections.templates import PROGRESS_REPORT
from dociq.verify.tokens import estimate_for_texts
from tests.fixtures import MPR_PAGES, document, page
from tests.test_docid_assign import write_index

STAMP = OperatorStamp("abachowski", "2026-07-30T12:00:00Z", "LI-PC")
MATTER = "Project 495 — QDCPC v Domopan"

INDEX_ROWS = [
    ["1", "MPR-01.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
    ["2", "MPR-02.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
    ["3", "MPR-03.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
    ["4", "absent.pdf", "pdf", r"P 495\reports", "1", "", "", ""],
]


# ---------------------------------------------------------------------------
# Part 1 — the composed stages, on stub records
# ---------------------------------------------------------------------------


MPR_SECTIONS = (
    "MONTHLY PROGRESS REPORT",
    "EXECUTIVE SUMMARY",
    "SCHEDULE STATUS",
    "HSE STATISTICS",
    "PHOTO LOG",
    "ORGANISATION CHART",
)
"""One label per page of :data:`tests.fixtures.MPR_PAGES`, in order.

These are the labels an outline entry would carry, which is what Tier 1 records
verbatim, and they are the section headings the fixture pages already print. The
first is deliberately one no shipped family matches — a section that is
recognized, belongs to no family, and therefore keeps.
"""

APPROVED = ("hse-statistics", "progress-photographs", "organization-chart")
"""The three families this composition approves — pages 4, 5 and 6.

The same three sections the deleted ``mpr_profile`` DROP rules named, so the
page accounting and the marker numbering asserted below are the numbers this
module has always asserted. What changed underneath them is the mechanism, not
the outcome, and that is the point: a repointed test that also moved its numbers
proves nothing about the swap.
"""


def approvals_for(*family_ids: str, approved_by: str = "abachowski") -> tuple[
        ApprovedOmission, ...]:
    """Approvals as the GUI's picker would record them (D-34).

    ``approved_by`` is a parameter because A-19 makes it a hashed identity input:
    two runs that differ only in who approved the omission are not the same run.
    """
    return tuple(
        ApprovedOmission(
            family_id=family_id,
            approved_by=approved_by,
            approved_at="2026-07-30T11:00:00Z",
            matter=MATTER,
            template_id=PROGRESS_REPORT.template_id,
            template_version=PROGRESS_REPORT.version,
        )
        for family_id in family_ids
    )


def stamp_sections(doc: DocumentRecord) -> DocumentRecord:
    """Stamp :data:`MPR_SECTIONS` onto a fixture MPR's pages.

    Stands in for what :func:`dociq.ingest.walker._record` does with the spans
    :func:`dociq.ingest.extract.pdf_spans` returns — the stub records of Part 1
    never went near a PDF. Part 2 drives the real stamping, so this shortcut
    cannot hide a break in it.
    """
    pages = tuple(
        p.evolve(section=MPR_SECTIONS[p.page_no - 1],
                 section_tier=RecognitionTier.OUTLINE)
        if p.page_no <= len(MPR_SECTIONS) else p
        for p in doc.pages
    )
    out = replace(doc, pages=pages)
    out.validate()
    return out


def stamped_corpus(count: int = 3, prefix: str = "MPR") -> tuple[DocumentRecord, ...]:
    """:func:`tests.fixtures.corpus`, with every page carrying its section.

    Not built by stamping ``corpus()`` after the fact so that the hash the
    fixture derives is the hash of the same text either way: a fixture whose
    identity moved when sections were added would make the byte-identity test
    below pass for the wrong reason.
    """
    docs = []
    for i in range(1, count + 1):
        pages = tuple(
            page(n, text, section=MPR_SECTIONS[n - 1],
                 section_tier=RecognitionTier.OUTLINE)
            for n, text in enumerate(MPR_PAGES, start=1)
        )
        docs.append(document(f"reports/{prefix}-{i:02d}.pdf", pages))
    return tuple(docs)


def section_stage(
    docs: tuple[DocumentRecord, ...],
    approvals: tuple[ApprovedOmission, ...] = (),
    *,
    template=PROGRESS_REPORT,
) -> tuple[tuple[DocumentRecord, ...], tuple[SectionDropEntry, ...], tuple[str, ...]]:
    """Stage 4 over a corpus: rebuild each document's spans, then dispose.

    The loop the pipeline runs, in the same order and with the same two calls.
    Kept here rather than inlined so the composition and the real run below are
    demonstrably the same Stage 4 rather than two implementations of it.
    """
    out: list[DocumentRecord] = []
    drops: list[SectionDropEntry] = []
    warnings: list[str] = []
    for doc in docs:
        applied = apply_sections(
            doc, spans_from_pages(doc.pages),
            template=template, approvals=approvals,
        )
        out.extend(applied.documents)
        drops.extend(applied.drops)
        warnings.extend(applied.warnings)
    return tuple(out), tuple(drops), tuple(warnings)


def run_track_b(tmp_path, out_name="out", index=None, approvals=None):
    layout = OutputLayout.at(tmp_path / out_name)
    approvals = approvals_for(*APPROVED) if approvals is None else approvals
    docs = stamped_corpus(3) + (
        document("loose/note.txt", (page(1, "A loose note."),)),
    )

    # Stage 3 — Bates (this corpus is unstamped, the normal case)
    proposal = propose_format(docs)
    decision = (
        BatesDecision(DecisionStatus.CONFIRMED, proposal.format, "abachowski", STAMP.saved_at)
        if proposal
        else None
    )
    docs = apply_bates(docs, decision)
    ranges = document_ranges(docs)

    # Stage 3b — identity
    assignment = assign_doc_ids(docs, index, bates_ranges=ranges_by_sort_key(ranges))
    docs = assignment.documents
    report = reconcile(assignment, index, bates_ranges=ranges)

    # Stage 4 — KEEP/DROP. Sections and an approval, never a profile (D-35).
    # `note.txt` carries no section at all and is here to be the page no tier
    # resolved: under §1 it keeps, and it must survive a stage that drops.
    docs, drops, section_warnings = section_stage(docs, approvals)

    # Stage 5 — emit
    text_result = write_clean_text(docs, layout)
    write_sources_json(text_result, layout)
    rows = build_index_rows(docs, bates_ranges=ranges, drops=drops)
    write_index_csv(rows, layout)
    write_reconciliation_csv(report, layout)
    write_index_xlsx(rows, layout, report, matter_name="Project 495")

    config = RunConfig(
        source_root=str(tmp_path / "native"),
        output_root=str(layout.root),
        master_index=index.snapshot if index else None,
        bates_pattern=decision.pattern() if decision else None,
        # A-19: the approvals decided which pages dropped, so they are part of
        # what the run is. Recorded here exactly as `pipeline.run` records them.
        omissions=tuple(
            OmissionSnapshot(
                family_id=a.family_id,
                approved_by=a.approved_by,
                approved_at=a.approved_at,
                matter=a.matter,
                template_id=a.template_id,
                template_version=a.template_version,
            )
            for a in approvals
        ),
        section_template_id=PROGRESS_REPORT.template_id,
        section_template_version=PROGRESS_REPORT.version,
    )
    ledger = IssuedIdLedger.from_assignment(assignment, config.master_index)
    ledger.write(layout.issued_ids)

    bundle = build_log(
        config,
        docs,
        assignment=assignment,
        reconciliation=report,
        drops=drops,
        bates_decision=decision,
        bates_ranges=ranges,
        token_estimate=estimate_for_texts(
            p.text for d in docs for p in d.pages if p.disposition is Disposition.KEEP
        ),
        warnings=tuple(assignment.warnings) + section_warnings,
        stamp=STAMP,
    )
    write_processing_log(bundle, layout)

    before = estimate_for_texts(p.text for d in docs for p in d.pages)
    after = estimate_for_texts(
        p.text for d in docs for p in d.pages if p.disposition is Disposition.KEEP
    )
    write_run_summary(
        build_summary_data(
            matter_name=MATTER,
            source_root=config.source_root,
            output_root=config.output_root,
            generated_at=STAMP.saved_at,
            operator=STAMP.username,
            documents=docs,
            unsupported=(),
            tokens_before=before,
            tokens_after=after,
            ocr_threshold_pct=config.ocr_conf_threshold_pct,
            id_regime=config.id_regime.value,
            master_index=index.snapshot.filename if index else None,
            bates_note="No Bates stamps detected — absence is normal (§4 Stage 3).",
            warnings=bundle.content["warnings"],
        ),
        layout,
    )
    build_upload_package(
        layout,
        matter_name="Project 495",
        document_count=len(docs),
        page_count=sum(d.pages_in for d in docs),
        estimate=after,
        id_regime=config.id_regime.value,
    )
    return layout, docs, bundle, report, assignment, ledger


def test_every_output_is_produced(tmp_path):
    layout, docs, bundle, report, _, _ = run_track_b(tmp_path)
    for path in (
        layout.clean_text,
        layout.sources_json,
        layout.index_csv,
        layout.index_xlsx,
        layout.processing_log,
        layout.run_summary,
        layout.upload_package,
        layout.issued_ids,
    ):
        assert path.exists(), path
    assert expert_assist_layout(layout).ready


def test_page_accounting_reconciles_to_zero_discrepancy(tmp_path):
    _, docs, bundle, _, _, _ = run_track_b(tmp_path)
    for doc in docs:
        doc.validate()
    acc = bundle.content["accounting"]
    assert acc["pages_in"] == acc["pages_kept"] + acc["pages_dropped"]
    assert acc["pages_dropped"] == len(bundle.content["drops"]) > 0


def test_markers_use_original_page_numbers(tmp_path):
    layout, docs, _, _, _, _ = run_track_b(tmp_path)
    mpr = next(d for d in docs if d.filename.startswith("MPR"))
    body = layout.clean_text_file(mpr.doc_id).read_text(encoding="utf-8")
    numbers = [int(l.split()[2]) for l in body.split("\n") if l.startswith("===== PAGE")]
    assert numbers == [1, 2, 3]
    assert max(int(p.page_no) for p in mpr.pages) == 6


def test_the_dropped_pages_are_the_approved_sections_and_nothing_else(tmp_path):
    """The composed half of D-35's guarantee: three approvals, three pages.

    Named sections rather than a count, because a count is satisfied by dropping
    the wrong three. The two the template recognizes and does NOT drop are
    asserted in the same breath — `SCHEDULE STATUS` is offered and was not
    approved, `EXECUTIVE SUMMARY` is recognized and never offered — since "the
    approval decided it" is only true if not-approving decides it too.
    """
    _, docs, _, _, _, _ = run_track_b(tmp_path)
    mpr = next(d for d in docs if d.filename.startswith("MPR"))
    by_disposition = {
        p.section: p.disposition for p in mpr.pages
    }
    assert by_disposition["HSE STATISTICS"] is Disposition.DROP
    assert by_disposition["PHOTO LOG"] is Disposition.DROP
    assert by_disposition["ORGANISATION CHART"] is Disposition.DROP
    assert by_disposition["SCHEDULE STATUS"] is Disposition.KEEP
    assert by_disposition["EXECUTIVE SUMMARY"] is Disposition.KEEP
    assert by_disposition["MONTHLY PROGRESS REPORT"] is Disposition.KEEP

    note = next(d for d in docs if d.filename == "note.txt")
    assert [p.section for p in note.pages] == [None]
    assert all(p.disposition is Disposition.KEEP for p in note.pages)


def test_a_template_with_no_approval_recognizes_and_drops_nothing(tmp_path):
    """D-34's shipped state, through the whole composition rather than through
    :func:`~dociq.sections.apply.apply_sections` alone.

    The template is the same one; only the approvals are gone. Sections still
    reach the log, the index still reports them, and the accounting reports zero
    omissions — which is what "ships unengaged" has to mean in the deliverables
    an expert actually reads.
    """
    layout, docs, bundle, _, _, _ = run_track_b(tmp_path, "unengaged", approvals=())
    assert bundle.content["drops"] == []
    assert bundle.content["accounting"]["pages_dropped"] == 0
    assert all(
        p.disposition is Disposition.KEEP for d in docs for p in d.pages
    )

    mpr = next(d for d in docs if d.filename.startswith("MPR"))
    entry = next(
        e for e in bundle.content["documents"] if e["doc_id"] == mpr.doc_id
    )
    assert [s["section"] for s in entry["sections"]] == list(MPR_SECTIONS), (
        "recognition is the whole of what an unengaged template does, and it "
        "did not reach the log"
    )

    with layout.index_csv.open(encoding="utf-8-sig", newline="") as fh:
        row = next(r for r in csv.DictReader(fh) if r["Doc ID"] == mpr.doc_id)
    assert row["Sections recognized"] == str(len(MPR_SECTIONS))
    assert row["Recognition tier"] == RecognitionTier.OUTLINE.value
    assert row["Sections dropped"] == "0"
    assert row["Omission approved by"] == ""


def _fingerprint(layout) -> str:
    """Hash only what the byte-identical claim actually covers."""
    h = hashlib.sha256()
    for path in sorted(layout.clean_text.glob("*.txt")):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    h.update(layout.sources_json.read_bytes())
    h.update(layout.index_csv.read_bytes())
    log = json.loads(layout.processing_log.read_text(encoding="utf-8"))
    h.update(log["content_sha256"].encode("utf-8"))
    return h.hexdigest()


@pytest.mark.slow
def test_outputs_are_byte_identical_over_repeated_runs(tmp_path):
    digests = {_fingerprint(run_track_b(tmp_path, f"run{i}")[0]) for i in range(8)}
    assert len(digests) == 1


def test_master_index_regime_assigns_li_numbers(tmp_path):
    index = write_index(tmp_path, INDEX_ROWS)
    layout, docs, bundle, report, assignment, _ = run_track_b(tmp_path, "li", index)
    ids = {d.filename: d.doc_id for d in docs}
    assert ids["MPR-01.pdf"] == "LI-00001"
    assert ids["note.txt"].startswith("DIQ-")
    assert report.totals["index_only"] == 1
    assert report.totals["folder_only"] == 1
    assert bundle.content["doc_ids"]["regime"] == "master-index"


def test_rerunning_against_a_renumbered_index_warns(tmp_path):
    index_a = write_index(tmp_path, INDEX_ROWS, name="a.csv")
    shifted = [["1", "inserted.pdf", "pdf", r"P 495\reports", "1", "", "", ""]] + [
        [str(int(r[0]) + 1)] + r[1:] for r in INDEX_ROWS
    ]
    index_b = write_index(tmp_path, shifted, name="b.csv")
    _, _, _, _, _, first = run_track_b(tmp_path, "r1", index_a)
    _, _, _, _, _, second = run_track_b(tmp_path, "r2", index_b)
    warnings = detect_renumbering(first, second)
    assert any(w.kind == "id-moved" for w in warnings)
    assert all("citations" in w.message or "resolves to" in w.message for w in warnings)


def test_upload_package_excludes_the_audit_trail(tmp_path):
    layout, _, _, _, _, _ = run_track_b(tmp_path)
    names = {p.name for p in layout.upload_package.iterdir()}
    assert "processing_log.json" not in names
    assert "run_summary.pdf" not in names
    assert "sources.json" in names


# ---------------------------------------------------------------------------
# Part 2 — the real pipeline, over a matter folder authored here
# ---------------------------------------------------------------------------
#
# The document below is D-35's reproduction, rebuilt as a real PDF. The register
# names FIVE trigger shapes under which a single DROP rule for PROGRESS
# PHOTOGRAPHS took pages that were not photographs, and it names the four
# HIGH-risk categories it took: the executive summary, the critical path
# narrative, the weather log and the timesheets. All nine are in this file.
#
# Its outline places PROGRESS PHOTOGRAPHS at pages 7-8 and nowhere else. Every
# other appearance of the words is TEXT — a contents line, a cross-reference, an
# enclosure list — and text is not evidence of a section (taxonomy §2).

_REPORT_PAGES: tuple[tuple[str | None, str], ...] = (
    ("TABLE OF CONTENTS",
     "TABLE OF CONTENTS\n"
     "2  EXECUTIVE SUMMARY .................. 2\n"
     "5  PROGRESS PHOTOGRAPHS ............... 7\n"
     "6  WEATHER LOG ........................ 9\n"
     "7  TIMESHEETS ........................ 10"),
    ("EXECUTIVE SUMMARY",
     "EXECUTIVE SUMMARY\n"
     "Engineering completed 62 percent of the planned deliverables.\n"
     "Photographs of the works are at PROGRESS PHOTOGRAPHS, Appendix B."),
    ("CRITICAL PATH NARRATIVE",
     "CRITICAL PATH NARRATIVE\n"
     "Hull fabrication drove completion throughout the period.\n"
     "The topsides lift remains eleven days behind the accepted plan."),
    ("TRANSMITTAL",
     "TRANSMITTAL\nEnclosures:\n"
     "1. Monthly report\n2. PROGRESS PHOTOGRAPHS\n3. Weather log\n4. Timesheets"),
    ("SCHEDULE STATUS",
     "SCHEDULE STATUS\nActivity\tPlanned\tActual\tVariance\n"
     "Hull fabrication\t42.0\t31.5\t-10.5"),
    (None,
     "SCHEDULE STATUS (continued)\nTopsides\t18.0\t18.0\t0.0\n"
     "Commissioning\t4.0\t2.0\t-2.0"),
    ("PROGRESS PHOTOGRAPHS",
     "APPENDIX B - PROGRESS PHOTOGRAPHS\n"
     "Figure 1 - module M12 under assembly\nFigure 2 - quayside"),
    (None,
     "Figure 3 - blast and paint hall\nFigure 4 - pipe rack erection"),
    ("WEATHER LOG",
     "WEATHER LOG\n01 Jun  rain 12 mm  wind 24 kn\n02 Jun  rain 4 mm  wind 11 kn"),
    ("TIMESHEETS",
     "TIMESHEETS\nWelders 42\nFitters 31\nElectricians 18"),
    (None,
     "TIMESHEETS (continued)\nPainters 12\nScaffolders 9"),
    ("ACTION ITEM REGISTER",
     "ACTION ITEM REGISTER\n1  Issue revised IFC drawings\n2  Close out NCR 114"),
)

PHOTO_PAGES = (7, 8)
"""The only pages the outline places in PROGRESS PHOTOGRAPHS."""

D35_TRIGGERS = (
    ("the-documents-own-contents-page", 1, Disposition.KEEP, "TABLE OF CONTENTS"),
    ("a-body-text-cross-reference", 2, Disposition.KEEP, "EXECUTIVE SUMMARY"),
    ("a-transmittal-listing-enclosures", 4, Disposition.KEEP, "TRANSMITTAL"),
    ("an-appendix-cover-sheet", 7, Disposition.DROP, "PROGRESS PHOTOGRAPHS"),
    ("no-later-rule-marks-the-sections-end", 12, Disposition.KEEP,
     "ACTION ITEM REGISTER"),
)
"""D-35's five trigger shapes: the page, its disposition, and its ATTRIBUTION.

An ENUMERATION, not a repro. Four of the five are pages that merely name the
section; the fifth — the appendix cover sheet — is the page where the section
genuinely starts, and it is in the table because the correct match is the shape
that did the most damage: with nothing to mark the section's end, the carried
state ran to the last page of the document. Page 12 is that last page, and it is
the one the old engine could not keep.

**The section is asserted alongside the disposition because D-35 is two harms,
not one.** Pages vanished, and the drop log attributed every one of them to
PROGRESS PHOTOGRAPHS. A page that survives under the wrong section name is a
misdescription waiting for the day someone engages that family, so the
attribution is pinned on the pages that kept as well as on the page that went.

A sixth shape found later has a row to go in, which is the other half of what an
enumeration is for.
"""

APPROVAL = approvals_for("progress-photographs", approved_by="jlong")


def write_report_pdf(path: Path) -> None:
    """Author :data:`_REPORT_PAGES` as a PDF that carries its own outline.

    ``fitz`` rather than reportlab because the outline is the whole point:
    reportlab builds the fixture corpus and gives no way to say "this page
    starts a section", and a document with no outline exercises no Tier 1.
    """
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    toc = []
    for page_no, (title, body) in enumerate(_REPORT_PAGES, start=1):
        doc.new_page().insert_text((72, 100), body, fontsize=11)
        if title is not None:
            toc.append([1, title, page_no])
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def matter(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("d35") / "matter"
    write_report_pdf(root / "reports" / "MPR-01.pdf")
    # A document no tier resolves, in the same folder: §1's 29.6% case has to
    # survive a run that drops pages elsewhere.
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "notes" / "site_note.txt").write_text(
        "A loose site note, in a format that carries no outline.\n",
        encoding="utf-8",
    )
    return root


def run_pipeline(matter: Path, out: Path, approvals=(), **kw):
    config = RunConfig(
        source_root=str(matter),
        output_root=str(out),
        ocr_engine_version=ex.ocr_engine_version(),
    )
    return pipeline.run(
        config,
        pipeline.PipelineOptions(
            walk=walker.WalkOptions(ocr_enabled=False, resume=False),
            matter_name="Project 495",
            stamp=STAMP,
            template=PROGRESS_REPORT,
            approvals=approvals,
            **kw,
        ),
    )


@pytest.fixture(scope="module")
def unengaged(matter, tmp_path_factory):
    """The shipped state: a template, and nobody has ruled on anything."""
    return run_pipeline(matter, tmp_path_factory.mktemp("none") / "out")


@pytest.fixture(scope="module")
def engaged(matter, tmp_path_factory):
    """The same corpus with ONE omission approved by a named person."""
    return run_pipeline(matter, tmp_path_factory.mktemp("one") / "out", APPROVAL)


def _report(outcome):
    return next(d for d in outcome.result.documents if d.filename == "MPR-01.pdf")


def _dropped(doc: DocumentRecord) -> set[int]:
    return {p.page_no for p in doc.pages if p.disposition is Disposition.DROP}


def test_a_real_pdfs_outline_reaches_the_page_records(unengaged):
    """The first two links of the chain, asserted before anything is dropped.

    If this fails, every span-bound assertion below is vacuous — a run that
    recognized nothing drops nothing for a reason that has nothing to do with
    D-35.
    """
    assert unengaged.ok, unengaged.accounting.render()
    doc = _report(unengaged)
    assert [p.section for p in doc.pages] == [
        "TABLE OF CONTENTS", "EXECUTIVE SUMMARY", "CRITICAL PATH NARRATIVE",
        "TRANSMITTAL", "SCHEDULE STATUS", "SCHEDULE STATUS",
        "PROGRESS PHOTOGRAPHS", "PROGRESS PHOTOGRAPHS", "WEATHER LOG",
        "TIMESHEETS", "TIMESHEETS", "ACTION ITEM REGISTER",
    ]
    assert {p.section_tier for p in doc.pages} == {RecognitionTier.OUTLINE}
    assert _dropped(doc) == set(), "an unengaged template dropped a page"

    note = next(d for d in unengaged.result.documents if d.filename == "site_note.txt")
    assert [p.section for p in note.pages] == [None]
    assert [p.section_tier for p in note.pages] == [None]


def test_an_approval_drops_exactly_its_own_spans_pages(engaged):
    """D-35, end to end: the drop stops where the section does.

    The four HIGH-risk categories the register names as the pages the old engine
    took are asserted by name, because "two pages dropped" is also true of a run
    that took the wrong two.
    """
    assert engaged.ok, engaged.accounting.render()
    doc = _report(engaged)
    assert _dropped(doc) == set(PHOTO_PAGES)

    kept = {p.section for p in doc.pages if p.disposition is Disposition.KEEP}
    for survivor in ("EXECUTIVE SUMMARY", "CRITICAL PATH NARRATIVE",
                     "WEATHER LOG", "TIMESHEETS"):
        assert survivor in kept, (
            f"{survivor} was dropped and attributed to PROGRESS PHOTOGRAPHS — "
            "this is the D-35 reproduction, in the shipped engine"
        )


@pytest.mark.parametrize(
    "shape,page_no,expected,section", D35_TRIGGERS, ids=[t[0] for t in D35_TRIGGERS]
)
def test_each_d35_trigger_shape_reaches_only_its_own_span(
        engaged, shape, page_no, expected, section):
    """The register's five shapes, one test each, through the real pipeline.

    ``tests/test_sections.py`` covers the same five against hand-built spans.
    This covers them against spans a real PDF produced, stamped onto real page
    records, rebuilt at Stage 4 from those records — the three steps between the
    rule and the deliverable, none of which the unit test can reach.
    """
    doc = _report(engaged)
    page_record = next(p for p in doc.pages if p.page_no == page_no)
    assert page_record.disposition is expected, (
        f"trigger shape {shape!r} on page {page_no}: "
        f"expected {expected.value}, got {page_record.disposition.value}"
    )
    assert page_record.section == section, (
        f"trigger shape {shape!r} on page {page_no}: the page is recorded "
        f"under {page_record.section!r} rather than {section!r}"
    )


def test_no_drop_anywhere_in_the_run_reaches_past_its_span(unengaged, engaged):
    """CLASS PROBE, not a repro. Every document in the run, both of them.

    Works out which pages an approval was entitled to take, and asserts SET
    EQUALITY against what actually went. A drop one page past a span's end fails
    this; so does a page inside an approved span that survived.

    **The entitlement is computed from the RECOGNITION-ONLY run, and it has to
    be.** The obvious version of this probe — rebuild the spans from the engaged
    run's own pages — is self-confirming and was written that way first. It went
    green against a deliberately broken :func:`~dociq.sections.apply.apply_sections`
    whose approved span ran to the end of the document, because that function
    stamps each dropped page with the OVER-REACHING span's label: the probe then
    rebuilt the over-reaching span from the pages the over-reach had relabelled
    and agreed with itself. Recognition is identical across the two runs — the
    unengaged run asserts it page by page — so the unengaged pages are the only
    honest source for what the spans were before a disposition touched them.
    """
    approved = {a.family_id for a in APPROVAL}
    recognized = {d.doc_id: d for d in unengaged.result.documents}
    assert recognized, "no documents to probe"
    for doc in engaged.result.documents:
        expected: set[int] = set()
        for span in spans_from_pages(recognized[doc.doc_id].pages):
            family = PROGRESS_REPORT.classify(span.family)
            if family is not None and family.offer and family.family_id in approved:
                expected.update(range(span.start_page, span.end_page + 1))
        assert _dropped(doc) == expected, doc.rel_path


def test_the_drop_log_names_the_section_the_tier_and_the_approver(engaged):
    """D-34 and taxonomy §5.4 in the artifact an expert reads.

    "Dropped because the document's own outline placed this page in PROGRESS
    PHOTOGRAPHS, omission approved by jlong" — the sentence has three parts and
    the old log could produce only the first, sometimes falsely.
    """
    log = json.loads(engaged.layout.processing_log.read_text(encoding="utf-8"))
    entries = log["content"]["drops"]
    assert [e["page_no"] for e in entries] == list(PHOTO_PAGES)
    for entry in entries:
        assert entry["section"] == "PROGRESS PHOTOGRAPHS"
        assert entry["family_id"] == "progress-photographs"
        assert entry["tier"] == RecognitionTier.OUTLINE.value
        assert entry["approved_by"] == "jlong"
        assert entry["matter"] == MATTER
        assert entry["template_id"] == PROGRESS_REPORT.template_id
        assert entry["template_version"] == PROGRESS_REPORT.version
        assert "PROGRESS PHOTOGRAPHS" in entry["evidence"]
        assert entry["drop_rule"] == "progress-report:progress-photographs"


def test_the_index_carries_the_approver_for_the_document_that_lost_pages(engaged):
    """The approver reaches the workbook an expert opens, not only the JSON.

    ``build_index_rows`` takes ``drops`` as a separate argument precisely
    because the approver is not on the page; a wiring that forgot to pass it
    would leave this column blank and every other assertion in this file green.
    """
    with engaged.layout.index_csv.open(encoding="utf-8-sig", newline="") as fh:
        rows = {r["Filename"]: r for r in csv.DictReader(fh)}
    report = rows["MPR-01.pdf"]
    assert report["Omission approved by"] == "jlong"
    assert report["Sections dropped"] == "1"
    assert report["Recognition tier"] == RecognitionTier.OUTLINE.value
    assert rows["site_note.txt"]["Omission approved by"] == ""
    assert rows["site_note.txt"]["Sections recognized"] == "0"


def test_one_approval_changes_the_approved_pages_and_the_run_identity(
        unengaged, engaged):
    """Amendment A-19, on the two runs it is about.

    One corpus, two runs, differing in exactly one input: an approval. Three
    things must be true together, and A-19 exists because the third was not.

    1. The KEPT text differs by exactly the approved section's pages.
    2. Nothing else moves — same documents, same Doc IDs, same page count in.
    3. **The run identity differs.** Until A-19 the approvals sat outside
       :func:`~dociq.contracts.run_identity` exactly as profiles once sat
       outside it before A-08, so these two runs — one of them missing a
       section — claimed to be the same run.

    The last assertion is the sharp one: strip the omissions back out of the
    engaged run's config and the two identities become equal again, so what
    moved the hash is the approvals and not some incidental difference between
    two invocations.
    """
    without, with_ = _report(unengaged), _report(engaged)
    assert without.doc_id == with_.doc_id
    assert without.pages_in == with_.pages_in == len(_REPORT_PAGES)

    changed = {
        p.page_no
        for p, q in zip(without.pages, with_.pages)
        if p.disposition is not q.disposition
    }
    assert changed == set(PHOTO_PAGES)

    body_without = unengaged.layout.clean_text_file(without.doc_id).read_text(
        encoding="utf-8")
    body_with = engaged.layout.clean_text_file(with_.doc_id).read_text(
        encoding="utf-8")
    markers = lambda text: [  # noqa: E731 — a two-use local, not an API
        int(line.split()[2])
        for line in text.split("\n")
        if line.startswith("===== PAGE")
    ]
    assert markers(body_without) == list(range(1, len(_REPORT_PAGES) + 1))
    assert markers(body_with) == [
        n for n in range(1, len(_REPORT_PAGES) + 1) if n not in PHOTO_PAGES
    ]
    assert "Figure 1 - module M12 under assembly" in body_without
    assert "Figure 1 - module M12 under assembly" not in body_with

    cold, hot = unengaged.result.config, engaged.result.config
    assert hot.omissions == (
        OmissionSnapshot(
            family_id="progress-photographs",
            approved_by="jlong",
            approved_at="2026-07-30T11:00:00Z",
            matter=MATTER,
            template_id=PROGRESS_REPORT.template_id,
            template_version=PROGRESS_REPORT.version,
        ),
    )
    assert cold.omissions == ()
    assert run_identity(cold) != run_identity(hot), (
        "two runs over one folder, one of them missing a section, share a run "
        "identity — this is A-08's finding on the input that replaced profiles"
    )
    assert run_identity(replace(hot, omissions=())) == run_identity(cold), (
        "the identities differ for some reason OTHER than the approvals, so "
        "this test proves nothing about A-19"
    )


def test_the_approver_is_part_of_the_run_identity(engaged):
    """A-19 states plainly that ``approved_by`` is hashed, and narrows the
    byte-identical claim to say so: two runs that differ only in WHO approved
    the omission are not the same run, because the drop log names the approver.

    Asserted on the projection rather than by a third pipeline run — it is the
    same :func:`~dociq.contracts.run_identity` the run recorded, and a run whose
    only difference is one string in one config field would be an expensive way
    to compare two hashes.
    """
    hot = engaged.result.config
    other = replace(
        hot,
        omissions=(replace(hot.omissions[0], approved_by="someone-else"),),
    )
    assert run_identity(other) != run_identity(hot)


def test_a_profile_supplied_today_drops_nothing_and_says_so(matter, tmp_path_factory):
    """WITHDRAW THE CLAIM: the profile that used to drop these pages.

    D-35 deleted the engine, not the profile format. A profile still loads,
    still hashes into the run identity, and now decides nothing — and an
    operator who authored one and watched every page survive would have no way
    to learn why. Both halves are asserted: the run drops nothing, and it says
    out loud that the DROP rules did nothing.

    The profile is built here rather than imported from
    ``tests/test_profiles.py`` deliberately. This file's claim is about what a
    profile no longer does, so it must be able to collect and run while that
    module is itself being repointed.
    """
    inert = FormatProfile(
        profile_id="modec-mpr",
        version="1",
        display_name="Recurring progress report",
        header_patterns=("MONTHLY PROGRESS REPORT",),
        section_rules=(
            SectionRule("photo-log", r"^PROGRESS PHOTOGRAPHS", Disposition.DROP,
                        notes="Image captions only. Approved by A. Bachowski."),
            SectionRule("weather", r"^WEATHER LOG", Disposition.DROP,
                        notes="Superseded by the met station export. Approved."),
            SectionRule("exec-summary", r"^EXECUTIVE SUMMARY", Disposition.KEEP),
        ),
    )
    outcome = run_pipeline(
        matter, tmp_path_factory.mktemp("inert") / "out", profiles=(inert,)
    )
    assert outcome.ok, outcome.accounting.render()
    assert all(
        p.disposition is Disposition.KEEP
        for d in outcome.result.documents
        for p in d.pages
    ), "a profile DROP rule dropped a page — the D-35 engine is back"

    notice = next(
        (w for w in outcome.result.warnings if "modec-mpr" in w), None
    )
    assert notice is not None, (
        "a profile whose DROP rules stopped working was accepted in silence"
    )
    assert "photo-log, weather" in notice
    assert "removed" in notice and "approval" in notice
    assert "exec-summary" not in notice, (
        "a KEEP rule was reported as having stopped working; it never dropped "
        "anything and nothing changed for it"
    )

    # …and the half that did NOT change. The matter copy is the record of what
    # a run was given and on whose authority (§6 step 4b, D-05); D-35 removed
    # the engine, not the obligation to record the input. Asserted here because
    # the composed run above no longer supplies a profile at all, so without
    # this the write would be exercised by nothing end to end.
    matter_copies = outcome.layout.root / MATTER_COPY_DIRNAME
    assert matter_copies.is_dir(), f"no {MATTER_COPY_DIRNAME}/ in the matter folder"
    names = sorted(p.name for p in matter_copies.glob("*.yaml"))
    assert names == ["modec-mpr.v1.yaml"], (
        f"the profile the run was given was not recorded beside the evidence: "
        f"{names}"
    )
