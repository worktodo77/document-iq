"""A deterministic stand-in for the real pipeline.

It exists so the shell can be built, rendered and tested before Track A's spine
lands, and it is deliberately the *only* place in the GUI that manufactures
data. Everything it returns is a frozen contract object or one of the
presentation records defined in :mod:`dociq.gui.pipeline`.

Deterministic on purpose: no randomness, no clock, no filesystem read. The same
call returns byte-identical records every time, so a screen render is a
regression test rather than a snapshot of whatever the mock felt like producing.

Scaled to the corpus DocIQ actually emitted on its first full run — 368
documents, 18,521 pages (the source folder is 298 PDF / 53 DOCX / 17 PPTX / 7
DOC; see the decision register, "Corpus reality vs the spec's assumption") —
because the design decision that matters most, that a fully reduced matter still
does not fit in direct context, is only exercised at that size.

**Sprint 2 did not delete this module, and the plan that said it would is
withdrawn.** :func:`dociq.gui.pipeline.get_pipeline` now returns
:class:`dociq.adapter.RealPipeline`, and the mock is installed by
:func:`~dociq.gui.pipeline.set_pipeline` instead — by ``tests/test_gui_states.py``
and ``tests/test_view_models.py``, which are the only thing that can demonstrate
the seam still holds (a seam with one implementation is an interface nobody has
tested), and by ``python -m dociq.gui.app --mock`` for reviewing a screen without
a corpus to hand. Its :meth:`MockPipeline.disclosure` is what keeps that safe.

Every number in here remains a FIXTURE and none of it is measured. Nothing in
this module may be imported by the real adapter, and nothing here may grow a
second life as a default — that is what :meth:`MockPipeline.disclosure` is
policing, and it is why the real adapter's disclosure is empty.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace

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
    DIRECT_CONTEXT_TOKENS,
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
    TokenBasis,
    TokenEstimate,
    config_from,
)
from dociq.runstate import RunTermination, TerminalStatus

# ---------------------------------------------------------------------------
# THE FIXTURE. Every illustrative number in the Sprint-1 shell is here and
# nowhere else, so swapping in Track B's real calibration is a local edit.
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN_LOW = 3.3
CHARS_PER_TOKEN_HIGH = 3.6
"""D-03's ruled band — **CONFIGURATION, NOT A MEASUREMENT.**

Track B measured 3.03 chars per pre-token on a 40-PDF sample and 2.53 across the
full record (298 PDFs / 17,732 pages / 49,031,833 chars / 19,388,495 pre-tokens).

An earlier version of this comment called the band *refuted* on that evidence,
on the argument that a tokenizer cannot emit fewer tokens than the text has
pre-tokens. Codex review #1 finding B-6 established that the argument does not
hold: those pre-tokens are DocIQ's own approximate split, and a real tokenizer
with a coarser one merges across boundaries DocIQ invented. Under the
assumptions stated in :mod:`dociq.verify.tokens`, 2.53 chars/pre-token is
CONSISTENT with the ruled band.

It stays in the fixture because the run records what was configured — and
nothing derived from it is displayed as fact. Do not replace these numbers with
a guessed band."""

# The measured record, for the disclosure the shell shows above every screen.
MEASURED_DOCUMENTS = 368
MEASURED_PAGES = 18_521
MEASURED_CHARS = 50_190_410
MEASURED_PRETOKENS = 17_252_003
"""The full-corpus pre-token count **of the text DocIQ actually emits**, under
DocIQ's own approximate split. Source: the first full pipeline run, 2026-07-31,
368 documents, 2.91 chars/pre-token.

**Not a token floor.** Naming it one was the defect in Codex review #1 finding
B-6. It is a structural measurement of the emitted text; the token figure it
implies depends on assumptions stated in :mod:`dociq.verify.tokens`.

**This constant was 19,388,495 and that figure is SUPERSEDED for any statement
about the deliverable** (decision register, 2026-07-31). It came from
``tools/calibrate_tokens.py``, which reads with PyMuPDF, skips whitespace-only
pages, applies no normalization, runs no OCR, and covers the 298 PDFs only — so
it describes what the source PDFs contain under a different reader, not what
DocIQ ships. On 131 identical pages PyMuPDF yields 16.7% more pre-tokens than
the pypdf text DocIQ extracts, and contract normalization removes about 5% more.

Corrected here because the shell renders these numbers in a banner that says
"the measured record", above every screen. A superseded measurement presented as
the current one is a false claim whether or not the code that computes it is
right, and the fixture is the only place the shell can be wrong about a fact it
did not compute — which is exactly why the disclosure exists."""

AUTOMATIC_SAVING_SHARE = 0.14
"""Share of the record removed as exact-hash duplicates and page furniture.

**ILLUSTRATIVE** — the mock models no duplicates; Track A's inventory (§4 Stage
1) produces the real figure. Held as a share rather than an absolute so it
cannot silently stop matching the corpus it is applied to. Its own lever,
because a mechanical saving must never be merged into the expert's total."""

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
    ("Organization Charts", "Organization charts", False),
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

_PAGE_PROSE = (
    "Progress against the approved baseline is reported below by discipline, "
    "with manhours expended and earned value as at the report date.\n"
)

_PAGE_TABLE_ROWS = (
    "DISC\tPLAN%\tACT%\tVAR\tMHRS\tEV\tCPI\tSPI\n",
    "PIP\t42.1\t38.6\t-3.5\t18422\t0.92\t0.97\t0.91\n",
    "STR\t61.0\t60.4\t-0.6\t24109\t0.99\t1.01\t0.99\n",
    "ELE\t22.8\t17.2\t-5.6\t9033\t0.75\t0.88\t0.76\n",
    "INS\t15.4\t11.9\t-3.5\t4127\t0.77\t0.90\t0.77\n",
    "MEC\t55.2\t54.8\t-0.4\t16240\t0.98\t1.00\t0.99\n",
    "CIV\t88.9\t88.1\t-0.8\t7715\t0.99\t1.02\t0.99\n",
    "TEL\t31.7\t24.3\t-7.4\t2860\t0.71\t0.84\t0.77\n",
)
"""An MPR page is mostly table. Prose-only filler would give the fixture a
chars-per-pre-token profile no page in the real record has, and the shell would
then be reviewed against text that flatters it. Whatever ratio this yields is
measured from the text, never asserted — see :func:`_structural_tokens`."""

_PRE_TOKEN = re.compile(r"\w+|[^\w\s]")
"""An approximation of the pre-token split a BPE tokenizer applies before
merging.

Counting these characterizes the text's structure. It is **not** a lower bound
on token count: the split is this fixture's own, and a tokenizer with a coarser
one merges across boundaries invented here (Codex review #1, finding B-6). The
shell still prefers this figure to a chars-per-token estimate because it is
derived from the text in front of it rather than from a ruled constant — and it
labels it an estimate."""


def _structural_tokens(text: str) -> int:
    return len(_PRE_TOKEN.findall(text))


def _page_text(doc_index: int, page_no: int) -> str:
    """A deterministic MPR-shaped page: a line of prose over a progress table.

    Composed of whole rows rather than truncated to a character budget — a cut
    that lands mid-table would change the page's token profile depending on
    where it fell, which is exactly the kind of accident that makes a fixture
    quietly unrepresentative. No randomness anywhere in the mock: a render must
    be reproducible to be worth reviewing.
    """
    rows = 4 + ((doc_index * 3 + page_no * 5) % 5)
    body = "".join(_PAGE_TABLE_ROWS[i % len(_PAGE_TABLE_ROWS)]
                   for i in range(rows))
    return (_PAGE_PROSE + body).strip()


def _section(page_no: int, total: int) -> str:
    tail = max(6, total // 7)
    if page_no <= 3:
        return "Executive Summary"
    if page_no > total - tail:
        return "Photographic Record"
    if page_no % 9 == 0:
        return "HSE Statistics"
    if page_no % 17 == 0:
        return "Organization Charts"
    if page_no % 23 == 0:
        return "Transmittal Sheets"
    return "Progress by Discipline"


_DATE_RE = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")


def _fixture_dates(rel_path: str) -> tuple[str, ...]:
    """ISO dates a real Stage-3 pass would have detected in the document.

    Read off the fixture's own filenames so the set is deterministic AND has
    genuine holes: correspondence and quarterly cost reports carry no ISO date,
    which is what makes the handoff screen's "documents with no detected date
    are not in a date-scoped package" caution exercisable rather than
    theoretical.
    """
    match = _DATE_RE.search(rel_path)
    if match is None:
        return ()
    year, month, day = match.groups()
    return (f"{year}-{month}-{day or '01'}",)


def _fixture_doc_type(rel_path: str) -> str | None:
    """Document type from the fixture's folder — a filename pattern, which is
    what the contract permits (``DocumentRecord.doc_type``: "from the active
    profile or a filename pattern. Never inferred by AI")."""
    folder = rel_path.split("/", 1)[0]
    return {
        "MPR": "Monthly progress report",
        "Correspondence": "Correspondence",
        "Meetings": "Meeting minutes",
        "Cost": "Cost report",
    }.get(folder)


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
        detected_dates=_fixture_dates(rel_path),
        doc_type=_fixture_doc_type(rel_path),
        profile_id=profile.profile_id if (apply_profile and profile) else None,
        profile_version=profile.version if (apply_profile and profile) else None,
        li_file_no=f"{6000 + index * 7}",
    )
    doc.validate()  # the mock must not be able to hand the GUI an invalid record
    return doc


_TOKENS_PER_PRETOKEN_LOW = 0.70
"""The fewest tokens per pre-token the estimator assumes, as a fraction.

Restated rather than imported: the pagemodel freeze forbids the GUI package
from importing ``dociq.verify``. A copied constant is a constant that can drift,
so ``tests/test_tokens.py`` asserts this value equals
``dociq.verify.tokens.TOKENS_PER_PRETOKEN_LOW_X100 / 100`` — the test can import
both sides even though the module may not."""


PROVENANCE_STRUCTURAL = (
    "estimated from this record's own text — one token per pre-token of DocIQ's "
    "approximate split, which is a characterization of the text, not a bound: a "
    "tokenizer with coarser pre-tokenization emits fewer"
)
"""What the shell prints beside every figure it shows. Written here, in the
pipeline, because the GUI must never author a provenance claim of its own.

The earlier wording — "which no tokenizer can go below" — was the claim Codex
review #1 finding B-6 withdrew."""


def _estimate(text_chars: int, structural: int) -> TokenEstimate:
    """Build the figure and its provenance together, so they cannot separate."""
    return TokenEstimate(
        chars=text_chars,
        ratio_low=CHARS_PER_TOKEN_LOW,
        ratio_high=CHARS_PER_TOKEN_HIGH,
        structural_tokens=structural,
        provenance=PROVENANCE_STRUCTURAL,
        # A conditional inconsistency, not a refutation: the ruled band is
        # flagged only when it sits below what the measured structure allows
        # once the coarser-pre-tokenization allowance is applied. See
        # dociq.verify.tokens.TOKENS_PER_PRETOKEN_LOW_X100.
        ratio_refuted=(
            bool(structural)
            and (text_chars / (structural * _TOKENS_PER_PRETOKEN_LOW))
            > CHARS_PER_TOKEN_HIGH
        ),
    )


def _build_plan(documents: tuple[DocumentRecord, ...],
                apply_profile: bool) -> ReductionPlan:
    """Per-section savings, measured off the pages the run actually produced.

    The mock computes these because they are pipeline numbers; the GUI only adds
    up whichever levers are engaged. See ``docs/contracts/amendments.md`` A-01 —
    the frozen contract has nowhere to carry them yet.
    """
    chars: dict[str, int] = {}
    structural: dict[str, int] = {}
    pages: dict[str, int] = {}
    for doc in documents:
        for page in doc.pages:
            key = page.section or "Progress by Discipline"
            chars[key] = chars.get(key, 0) + len(page.text)
            structural[key] = structural.get(key, 0) + _structural_tokens(page.text)
            pages[key] = pages.get(key, 0) + 1

    levers = [
        ReductionLever(
            key=name,
            label=_LABELS[name],
            tokens=structural.get(name, 0),
            pages=pages.get(name, 0),
            kind=LEVER_EXPERT,
            engaged=apply_profile and name in _DEFAULT_DROPS,
        )
        for name, _label in _LEVER_SECTIONS
        if pages.get(name, 0)
    ]
    total_structural = sum(structural.values())
    levers.append(ReductionLever(
        key="automatic",
        label="Duplicate copies and page furniture",
        tokens=round(total_structural * AUTOMATIC_SAVING_SHARE),
        pages=round(sum(pages.values()) * AUTOMATIC_SAVING_SHARE),
        kind=LEVER_AUTOMATIC,
        engaged=True,
        estimated=True,  # the mock models no duplicates; this is a projection
    ))
    return ReductionPlan(
        full_tokens=total_structural,
        levers=tuple(levers),
        basis=TokenBasis.of(_estimate(sum(chars.values()), total_structural)),
    )


_PROFILE_SOURCE = (
    "Section rules read from the profile in the DocIQ profile library. Page "
    "and token figures are projected from the fixture corpus, not counted "
    "from a run of this matter."
)

_PROFILE_BASIS = TokenBasis(
    provenance=PROVENANCE_STRUCTURAL + ", projected across the fixture corpus",
    is_structural=True,
)


def _profile_levers() -> tuple[ReductionLever, ...]:
    """The checklist's rows, measured off the fixture without a run.

    ``estimated=True`` on every row and it is not a formality: this is what a
    profile WOULD remove, projected before any page of this matter has been
    read. Standing in the same column as a completed run's counted figures
    without saying so is the claim ``ReductionLever.estimated`` exists to stop.
    """
    structural: dict[str, int] = {}
    pages: dict[str, int] = {}
    for index, (_rel, page_count, _scanned, _status) in enumerate(_CORPUS):
        for page_no in range(1, page_count + 1):
            key = _section(page_no, page_count)
            structural[key] = (structural.get(key, 0)
                               + _structural_tokens(_page_text(index, page_no)))
            pages[key] = pages.get(key, 0) + 1
    levers = [
        ReductionLever(
            key=name,
            label=_LABELS[name],
            tokens=structural.get(name, 0),
            pages=pages.get(name, 0),
            kind=LEVER_EXPERT,
            engaged=name in _DEFAULT_DROPS,
            estimated=True,
        )
        for name, _label in _LEVER_SECTIONS
        if pages.get(name, 0)
    ]
    levers.append(ReductionLever(
        key="automatic",
        label="Duplicate copies and page furniture",
        tokens=round(sum(structural.values()) * AUTOMATIC_SAVING_SHARE),
        pages=round(sum(pages.values()) * AUTOMATIC_SAVING_SHARE),
        kind=LEVER_AUTOMATIC,
        engaged=True,
        estimated=True,
    ))
    return tuple(levers)


_PROFILE_LEVERS = _profile_levers()


def at_measured_scale(plan: ReductionPlan) -> ReductionPlan:
    """The same plan, scaled up to the measured record's structural estimate.

    The fixture corpus is 8,387 pages; the record the pipeline actually emitted
    is 18,521 pages over 368 documents, whose measured structure implies roughly
    17.3M tokens (an estimate, not a floor) — about 86× direct-context capacity,
    not 3.6×. The screens have to be reviewed at the magnitude they will actually
    meet, because a two-digit multiplier and a three-digit one are not the same
    layout problem.

    Shape-preserving and clearly named: this is the fixture at real scale, not a
    second set of invented figures.
    """
    if plan.full_tokens <= 0:
        return plan
    factor = MEASURED_PRETOKENS / plan.full_tokens
    return ReductionPlan(
        full_tokens=MEASURED_PRETOKENS,
        levers=tuple(
            replace(le, tokens=round(le.tokens * factor),
                    pages=round(le.pages * factor))
            for le in plan.levers
        ),
        capacity=plan.capacity,
        basis=plan.basis,
    )


class MockPipeline:
    """Implements :class:`dociq.gui.pipeline.PipelineAPI` with fixed data."""

    step_delay_s: float = 0.0
    """Set by the app so a run is watchable; 0 in tests and in renders."""

    def __init__(self, step_delay_s: float = 0.0) -> None:
        self.step_delay_s = step_delay_s

    # -- the API ------------------------------------------------------------

    def disclosure(self) -> str:
        """Say, on screen, that these figures are a fixture — and how far the
        fixture sits from the record that was actually measured.

        A shell that looks like the finished product while showing invented
        numbers is the most expensive misunderstanding this project could ship,
        and "it was in the handover notes" is not a defence once a screenshot
        has been forwarded.
        """
        pages = sum(p for _r, p, _s, _st in _CORPUS)
        factor = MEASURED_PRETOKENS / DIRECT_CONTEXT_TOKENS
        return (
            f"Sample data — Sprint-1 shell. These figures come from a fixture of "
            f"{len(_CORPUS)} documents / {pages:,} pages, not from a real run. "
            # "documents", not "PDFs": 368 is the document count and only 298
            # of them are PDFs. The banner's whole job is to be the one place on
            # screen that states a measured fact, so it does not get to be loose
            # about which fact it is stating.
            f"The measured record is {MEASURED_DOCUMENTS} documents / "
            f"{MEASURED_PAGES:,} pages implying an estimated "
            f"{MEASURED_PRETOKENS / 1e6:.1f}M tokens — about {factor:.0f}× "
            "direct-context capacity."
        )

    def profiles(self) -> tuple[ProfileInfo, ...]:
        return PROFILES

    # -- optional adapter hooks (see docs/contracts/amendments.md A-11/A-13) --
    #
    # NOT part of ``PipelineAPI``: the seam is frozen and shared with Track D,
    # so these are raised as amendments and duck-typed in the meantime. The GUI
    # asks with ``getattr`` and renders the absence rather than assuming it.

    def profile_rules(
        self, profile: ProfileInfo
    ) -> tuple[tuple[ReductionLever, ...], TokenBasis, str]:
        """The §6 checklist's rows: what this profile keeps and drops.

        Sliced to the profile's own declared rule count rather than always
        returning all four fixture sections — a checklist that showed rules a
        profile does not carry is the same defect as one that hides rules it
        does, and the screen's completeness check has to be exercised by a mock
        that can actually disagree with itself.
        """
        if profile.section_rules <= 0:
            return (), _PROFILE_BASIS, _PROFILE_SOURCE
        levers = tuple(
            lever for lever in _PROFILE_LEVERS if not lever.locked
        )[: profile.section_rules]
        automatic = tuple(le for le in _PROFILE_LEVERS if le.locked)
        return levers + automatic, _PROFILE_BASIS, _PROFILE_SOURCE

    def matter_layout_note(self, outcome: RunOutcome) -> str:
        """§8 Path B: what is in the matter folder, in the pipeline's words.

        The real adapter reads this from ``emit.handoff.expert_assist_layout``,
        which CHECKS the folder rather than describing it from memory. The mock
        states the layout it would write and says it checked nothing, because a
        stand-in that reports a verified folder is a stand-in that has told the
        operator something false.
        """
        return (
            "Point Claude at this folder. It holds clean_text/ (one text file "
            "per document, original page numbers in the markers), "
            "sources.json, document_index.csv and processing_log.json — the "
            "layout Expert Assist's evidence-mining skill expects, with no "
            "rearrangement. Sample data: nothing on disk was checked."
        )

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

        # Stamped, not defaulted (Codex review #1 round 2, F-1 sibling sweep).
        # Both loops above ``break`` on ``should_cancel()``, and this result
        # then took the contract's COMPLETED default — so the demo backend
        # handed Track C's GUI a partial corpus labeled complete, which is the
        # exact confusion the typed status exists to remove. The mock is what
        # the GUI is developed against, so a mock that models the failure
        # incorrectly teaches the consumer to trust the wrong field.
        termination = (
            RunTermination(
                TerminalStatus.CANCELLED,
                f"The demonstration run was stopped after {len(documents)} of "
                f"{len(_CORPUS)} document(s) had been read.",
            )
            if should_cancel()
            else RunTermination()
        )
        result = termination.stamp(
            RunResult(
                config=config,
                documents=tuple(documents),
                unsupported=tuple(unsupported),
                warnings=(),
            )
        )

        chars_before = sum(len(p.text) for d in documents for p in d.pages)
        structural_before = sum(_structural_tokens(p.text)
                           for d in documents for p in d.pages)
        chars_after = sum(
            len(p.text)
            for d in documents
            for p in d.pages
            if p.disposition is Disposition.KEEP
        )
        structural_after = sum(
            _structural_tokens(p.text)
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
            tokens_before=_estimate(chars_before, structural_before),
            tokens_after=_estimate(chars_after, structural_after),
            reconciliation=recon,
            output_root=request.output_root,
            plan=_build_plan(tuple(documents), apply_profile),
        )
