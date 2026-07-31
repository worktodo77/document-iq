"""A deterministic stand-in for the real pipeline (Sprint 1 only).

It exists so the shell can be built, rendered and tested before Track A's spine
lands, and it is deliberately the *only* place in the GUI that manufactures
data. Everything it returns is a frozen contract object or one of the
presentation records defined in :mod:`dociq.gui.pipeline`.

Deterministic on purpose: no randomness, no clock, no filesystem read. The same
call returns byte-identical records every time, so a screen render is a
regression test rather than a snapshot of whatever the mock felt like producing.

Scaled to the corpus that actually exists (decision register, "Corpus reality vs
the spec's assumption": 298 PDF / 53 DOCX / 17 PPTX / 7 DOC, 17,732 PDF pages),
because the design decision that matters most — that a fully reduced matter
still does not fit in direct context — is only exercised at that size.

Sprint 2 deletes this module and :func:`dociq.gui.pipeline.get_pipeline` returns
the real adapter instead.
"""

from __future__ import annotations

import time

from dociq.contracts import (
    Disposition,
    DocumentRecord,
    MasterIndexSnapshot,
    PageKind,
    PageRecord,
    ProcessingStatus,
    RunConfig,
    RunResult,
)
from dociq.gui.pipeline import (
    LEVER_AUTOMATIC,
    LEVER_EXPERT,
    FolderPreview,
    ProfileInfo,
    ProgressEvent,
    Reconciliation,
    ReconciliationRow,
    ReductionLever,
    ReductionPlan,
    RunOutcome,
    RunRequest,
    TokenEstimate,
    config_from,
)

# ---------------------------------------------------------------------------
# THE FIXTURE. Every illustrative number in the Sprint-1 shell is here and
# nowhere else, so swapping in Track B's real calibration is a local edit.
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN_LOW = 3.3
CHARS_PER_TOKEN_HIGH = 3.6
"""D-03's expected calibration band for table-heavy MPR text. **UNCONFIRMED** —
Track B owns the real measurement against the Claude tokenizer; these are the
requirement's stated expectations, not measurements."""

AUTOMATIC_SAVING_TOKENS = 340_000
AUTOMATIC_SAVING_PAGES = 1_180
"""Exact-hash duplicate copies and page furniture the tool removes on its own.
**ILLUSTRATIVE** — the mock models no duplicates; Track A's inventory (§4 Stage
1) produces the real figure. Kept as its own lever because it must never be
merged into the expert's total."""

MINUTES_PER_GIGABYTE = 18
"""**ILLUSTRATIVE** wall-clock rate behind the "about N minutes" line beside the
action. Rated on bytes rather than on file count because a folder of 40 scanned
MPRs and a folder of 40 emails are not the same job. Replaced by a measured rate
once Sprint 2 has a timed end-to-end run."""

PROFILES: tuple[ProfileInfo, ...] = (
    ProfileInfo("modec-mpr", "1.3", "MODEC monthly progress report", 4),
    ProfileInfo("petrobras-cer", "1.0", "Petrobras change/extension request", 2),
    ProfileInfo("none", "-", "No profile — keep every page", 0),
)

# (section, plain label, dropped by default under the MPR profile)
SECTIONS: tuple[tuple[str, str, bool], ...] = (
    ("Executive Summary", "Executive summary", False),
    ("Progress by Discipline", "Progress by discipline", False),
    ("Photographic Record", "Photo logs", True),
    ("HSE Statistics", "HSE statistics tables", True),
    ("Organisation Charts", "Organisation charts", False),
    ("Transmittal Sheets", "Transmittal sheets", False),
)
_DEFAULT_DROPS = frozenset(name for name, _label, drop in SECTIONS if drop)
_LEVER_SECTIONS = tuple(
    (name, label) for name, label, _d in SECTIONS
    if name not in ("Executive Summary", "Progress by Discipline")
)
_LABELS = dict((name, label) for name, label, _d in SECTIONS)


def _corpus() -> tuple[tuple[str, int, int, ProcessingStatus], ...]:
    """The mock matter: monthly reports across three years, plus correspondence.

    Generated from a rule rather than typed out, so the page total lands in the
    same order of magnitude as the real record without 300 literal rows.
    """
    rows: list[tuple[str, int, int, ProcessingStatus]] = []
    for i, (year, month) in enumerate(
        (y, m) for y in (2020, 2021, 2022) for m in range(1, 13)
    ):
        pages = 138 + (i * 7) % 34
        scanned = 18 if i % 5 == 2 else 0
        rows.append((
            f"MPR/{year}-{month:02d} MODEC Monthly Progress Report.pdf",
            pages, scanned,
            ProcessingStatus.PARTIAL_OCR_FLAGGED if scanned else ProcessingStatus.FULL,
        ))
    for i in range(18):
        pages = 96 + (i * 13) % 140
        scanned = 91 if i == 4 else (44 if i == 11 else 0)
        rows.append((
            f"Correspondence/CER-1-{113 + i * 3} Contract Correspondence.pdf",
            pages, scanned,
            ProcessingStatus.PARTIAL_OCR_FLAGGED if scanned else ProcessingStatus.FULL,
        ))
    for i in range(9):
        rows.append((f"Correspondence/Notice of Delay 20{21 + i % 2}-"
                     f"{1 + i:02d}-14.docx", 4 + i % 6, 0, ProcessingStatus.FULL))
    for i in range(6):
        rows.append((f"Meetings/Weekly Progress Meeting 2021-{i + 4:02d}-18.msg",
                     3, 0, ProcessingStatus.FULL))
    for i in range(4):
        rows.append((f"Cost/Topsides Cost Report 2021-Q{i + 1}.xlsx",
                     9 + i, 0, ProcessingStatus.FULL))
    return tuple(rows)


_CORPUS = _corpus()

_UNSUPPORTED: tuple[tuple[str, str], ...] = (
    ("Legacy/Transmittal 2019-11-02.doc",
     "Legacy Word format — open in Word and Save-As DOCX or PDF to include"),
    ("Legacy/Site Instruction 042.doc",
     "Legacy Word format — open in Word and Save-As DOCX or PDF to include"),
    ("Schedule/Topsides IMS rev 14.xer",
     "Primavera schedule — inventoried and hashed; schedule analysis is out of scope"),
    ("Schedule/Topsides IMS rev 14.mpp",
     "Microsoft Project schedule — inventoried and hashed"),
    ("Photos/Site photos 2021-06.rar",
     "RAR archive — listed only; extract it beside the folder to include its contents"),
)

_PAGE_TEXT = (
    "The Contractor advised that topsides module M-04 remained on the fabrication "
    "yard pending release of the revised piping isometrics. Progress against the "
    "approved baseline is reported below by discipline, with manhours expended and "
    "earned value as at the report date. Outstanding technical queries are listed "
    "at Appendix C together with the dates on which each was raised and the "
    "response required date. "
)


def _page_text(doc_index: int, page_no: int) -> str:
    """Deterministic filler of a plausible length. No randomness anywhere in the
    mock: a render must be reproducible to be worth reviewing."""
    want = 430 + ((doc_index * 31 + page_no * 17) % 190)
    reps = want // len(_PAGE_TEXT) + 1
    return (_PAGE_TEXT * reps)[:want].strip()


def _section(page_no: int, total: int) -> str:
    tail = max(6, total // 7)
    if page_no <= 3:
        return "Executive Summary"
    if page_no > total - tail:
        return "Photographic Record"
    if page_no % 9 == 0:
        return "HSE Statistics"
    if page_no % 17 == 0:
        return "Organisation Charts"
    if page_no % 23 == 0:
        return "Transmittal Sheets"
    return "Progress by Discipline"


def _build_document(index: int, rel_path: str, pages: int, scanned: int,
                    status: ProcessingStatus, apply_profile: bool,
                    profile: ProfileInfo | None) -> DocumentRecord:
    records: list[PageRecord] = []
    for page_no in range(1, pages + 1):
        is_ocr = page_no > pages - scanned
        section = _section(page_no, pages)
        drop = apply_profile and section in _DEFAULT_DROPS
        conf = None
        low_lines = 0
        line_count = 0
        if is_ocr:
            # A deterministic spread that straddles the 85% default threshold,
            # so the flag list on the summary screen is exercised by real data
            # rather than by a hand-placed example.
            conf = round(0.62 + ((index * 13 + page_no * 7) % 34) / 100.0, 4)
            line_count = 24 + (page_no % 11)
            low_lines = line_count // 3 if conf < 0.85 else 0
        records.append(
            PageRecord(
                page_no=page_no,
                text=_page_text(index, page_no),
                kind=PageKind.OCR if is_ocr else PageKind.NATIVE,
                ocr_conf=conf,
                ocr_line_count=line_count,
                ocr_low_conf_lines=low_lines,
                bates=None,
                section=section,
                disposition=Disposition.DROP if drop else Disposition.KEEP,
                drop_rule=f"modec-mpr:{section.lower().replace(' ', '-')}"
                if drop else None,
            )
        )
    ext = "." + rel_path.rsplit(".", 1)[1].lower()
    doc = DocumentRecord(
        doc_id=f"LI-{6000 + index * 7:05d}",
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        sha256=f"{index:064x}",
        size_bytes=pages * 184_320,
        ext=ext,
        pages=tuple(records),
        status=status,
        doc_type=profile.label if (apply_profile and profile) else None,
        profile_id=profile.profile_id if (apply_profile and profile) else None,
        profile_version=profile.version if (apply_profile and profile) else None,
        li_file_no=f"{6000 + index * 7}",
    )
    doc.validate()  # the mock must not be able to hand the GUI an invalid record
    return doc


def _tokens(chars: int) -> int:
    """The conservative end of the D-03 range — the number the waterfall shows.

    Conservative means the LOWER chars-per-token ratio, which yields the LARGER
    token count. An estimate that flatters the corpus is the one that gets
    someone half way through an upload before it fails.
    """
    return round(chars / CHARS_PER_TOKEN_LOW)


def _build_plan(documents: tuple[DocumentRecord, ...],
                apply_profile: bool) -> ReductionPlan:
    """Per-section savings, measured off the pages the run actually produced.

    The mock computes these because they are pipeline numbers; the GUI only adds
    up whichever levers are engaged. See ``docs/contracts/amendments.md`` A-01 —
    the frozen contract has nowhere to carry them yet.
    """
    chars: dict[str, int] = {}
    pages: dict[str, int] = {}
    for doc in documents:
        for page in doc.pages:
            key = page.section or "Progress by Discipline"
            chars[key] = chars.get(key, 0) + len(page.text)
            pages[key] = pages.get(key, 0) + 1

    levers = [
        ReductionLever(
            key=name,
            label=_LABELS[name],
            tokens=_tokens(chars.get(name, 0)),
            pages=pages.get(name, 0),
            kind=LEVER_EXPERT,
            engaged=apply_profile and name in _DEFAULT_DROPS,
        )
        for name, _label in _LEVER_SECTIONS
        if pages.get(name, 0)
    ]
    levers.append(ReductionLever(
        key="automatic",
        label="Duplicate copies and page furniture",
        tokens=AUTOMATIC_SAVING_TOKENS,
        pages=AUTOMATIC_SAVING_PAGES,
        kind=LEVER_AUTOMATIC,
        engaged=True,
    ))
    full = _tokens(sum(chars.values()))
    return ReductionPlan(full_tokens=full, levers=tuple(levers))


class MockPipeline:
    """Implements :class:`dociq.gui.pipeline.PipelineAPI` with fixed data."""

    step_delay_s: float = 0.0
    """Set by the app so a run is watchable; 0 in tests and in renders."""

    def __init__(self, step_delay_s: float = 0.0) -> None:
        self.step_delay_s = step_delay_s

    # -- the API ------------------------------------------------------------

    def profiles(self) -> tuple[ProfileInfo, ...]:
        return PROFILES

    def preview_folder(self, path: str) -> FolderPreview:
        by_ext: dict[str, int] = {}
        for rel, *_ in _CORPUS:
            key = "." + rel.rsplit(".", 1)[1].lower()
            by_ext[key] = by_ext.get(key, 0) + 1
        for rel, _hint in _UNSUPPORTED:
            key = "." + rel.rsplit(".", 1)[1].lower()
            by_ext[key] = by_ext.get(key, 0) + 1
        total_files = len(_CORPUS) + len(_UNSUPPORTED)
        return FolderPreview(
            file_count=total_files,
            total_bytes=sum(p * 184_320 for _r, p, _s, _st in _CORPUS),
            by_extension=tuple(sorted(by_ext.items())),
            estimated_minutes=max(
                1, round(sum(p * 184_320 for _r, p, _s, _st in _CORPUS)
                         / 1e9 * MINUTES_PER_GIGABYTE)),
        )

    def run(self, request: RunRequest, on_progress, should_cancel) -> RunOutcome:
        profile = request.profile
        apply_profile = bool(profile and profile.profile_id != "none")
        total = len(_CORPUS) + len(_UNSUPPORTED)
        documents: list[DocumentRecord] = []

        for i, (rel, pages, scanned, status) in enumerate(_CORPUS):
            if should_cancel():
                break
            doc = _build_document(i, rel, pages, scanned, status,
                                  apply_profile, profile)
            documents.append(doc)
            detail = f"read {doc.pages_in} pages"
            if scanned:
                detail += f" · OCR on {scanned}"
            if doc.pages_dropped:
                detail += f" · {doc.pages_dropped} dropped by profile"
            on_progress(ProgressEvent(len(documents), total, doc.filename, detail,
                                      flagged=status is not ProcessingStatus.FULL))
            if self.step_delay_s:
                time.sleep(self.step_delay_s)

        unsupported: list[DocumentRecord] = []
        for j, (rel, hint) in enumerate(_UNSUPPORTED):
            if should_cancel():
                break
            rec = DocumentRecord(
                doc_id=f"DIQ-{900 + j:06d}",
                rel_path=rel,
                filename=rel.rsplit("/", 1)[-1],
                sha256=f"{0xFFFF - j:064x}",
                size_bytes=48_000 + j * 1_024,
                ext="." + rel.rsplit(".", 1)[1].lower(),
                status=ProcessingStatus.UNSUPPORTED,
                error=hint,
            )
            rec.validate()
            unsupported.append(rec)
            on_progress(ProgressEvent(len(documents) + len(unsupported), total,
                                      rec.filename, "listed — not processed",
                                      flagged=True))
            if self.step_delay_s:
                time.sleep(self.step_delay_s)

        config: RunConfig = config_from(request)
        if request.master_index_path:
            config = RunConfig(
                source_root=config.source_root,
                output_root=config.output_root,
                profile_id=config.profile_id,
                profile_version=config.profile_version,
                master_index=MasterIndexSnapshot(
                    filename=request.master_index_path.rsplit("\\", 1)[-1]
                    .rsplit("/", 1)[-1],
                    sha256="9f2b" + "0" * 60,
                    row_count=9_259,
                ),
            )

        result = RunResult(
            config=config,
            documents=tuple(documents),
            unsupported=tuple(unsupported),
            warnings=(),
        )

        chars_before = sum(len(p.text) for d in documents for p in d.pages)
        chars_after = sum(
            len(p.text)
            for d in documents
            for p in d.pages
            if p.disposition is Disposition.KEEP
        )
        recon = None
        if request.master_index_path:
            recon = Reconciliation(
                matched=len(documents) + len(unsupported) - 3,
                rows=(
                    ReconciliationRow(
                        "folder-only", "DIQ-000901", "Site Instruction 042.doc",
                        "in the folder, no matching row in the master index"),
                    ReconciliationRow(
                        "index-only", "LI-06042",
                        "Weekly Progress Meeting 2021-07-02.msg",
                        "row 6042 of the master index; file not found in the folder"),
                    ReconciliationRow(
                        "field-mismatch", "LI-06021",
                        "2021-05 MODEC Monthly Progress Report.pdf",
                        "index page count 158, extracted 161"),
                ),
            )

        return RunOutcome(
            result=result,
            tokens_before=TokenEstimate(chars_before, CHARS_PER_TOKEN_LOW,
                                        CHARS_PER_TOKEN_HIGH),
            tokens_after=TokenEstimate(chars_after, CHARS_PER_TOKEN_LOW,
                                       CHARS_PER_TOKEN_HIGH),
            reconciliation=recon,
            output_root=request.output_root,
            plan=_build_plan(tuple(documents), apply_profile),
        )
