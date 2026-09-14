"""Per-page text extraction — vendored from MIP 3.9 ``api/docs_extract.py``.

The §11 reuse audit ruled REUSE on that module, so its hard-won behavior is
kept verbatim: hybrid per-page native/OCR routing, content-sniffing recovery
for misnamed production files, ZIP anti-DoS guards, per-page OCR failure
isolation, XLSX/CSV row caps with disclosed truncation, CSV encoding and
delimiter fallback, and the deterministic ``[PHOTO]`` EXIF block.

What changed, and why each change was mandatory rather than cosmetic:

* **Returns pages, not one joined string.** The original joined pages with an
  inline ``[page N]`` marker. DocIQ needs per-page OCR confidence (§4 Stage 2)
  and per-page KEEP/DROP (§4 Stage 4), and the freeze forbids a marker inside
  ``PageRecord.text`` — markers are rendered by ``emit/cleantext.py`` alone.
* **No network.** The original's ``_ocr_engine`` called ``enable_os_trust()``
  so a one-time OCR model download could get through a corporate proxy.
  Principle 4 admits no network call at all. Models are loaded from the
  installed ``rapidocr_onnxruntime`` package directory and their absence is a
  loud, actionable failure — never a download.
* **Per-line OCR confidence is captured.** The original discarded ``line[2]``,
  which is exactly the number §4 Stage 2 needs.
* **No extract cache.** The original persisted extracted text under
  ``~/.mip39/`` — outside the working folder, which §10 forbids. See
  :ref:`no-cache` below for why it was removed rather than relocated.
* **No AI captioning.** The original could call a local vision model to
  describe a photo. §12 puts any AI processing out of scope and the contract's
  ``PageKind.PHOTO`` says "never AI-captioned in DocIQ".
* **Scratch files stay inside the working folder.** ``.msg`` parsing needs a
  real file on disk; the caller supplies where.

.. _no-cache:

**Why the cache was removed, not relocated.** Relocating it under the output
root would satisfy §10's letter and defeat the determinism proof: runs 2..N of
an identical-input repeat would replay cached bytes instead of re-extracting,
so the proof would demonstrate that a cache is a cache. A content-hash-keyed
cache is also a standing correctness hazard — a stale entry written by a
different engine version replays old text under a new run's identity. The
expensive path (OCR) is the one worth caching, and Sprint 2 can reintroduce
caching inside the matter folder *behind* a flag that the determinism harness
disables. Nothing depends on it today.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..contracts import (
    ExtractionError,
    PageKind,
    PageRecord,
    ProcessingStatus,
    needs_ocr_review,
)
from ..identify.bates import FOOTER_BLOCK_MAX_LINES, BatesZone
from ..sections.model import SectionSpan
from ..sections.resolve import resolve_sections
from ..sections.tier1_outline import spans_from_outline
from ..sections.tier3_pageclass import PageSignals, PHOTO_MIN_IMAGE_AREA_SHARE
from .pagemodel import M_OCR_BLANK, make_page, normalize, synthetic_pages

# Tier 1 (§3) — extracted page by page.
TIER1_EXTENSIONS = {
    ".pdf": "PDF",
    ".docx": "Word",
    ".xlsx": "Excel",
    ".xlsm": "Excel",
    ".xls": "Excel (legacy)",
    ".csv": "CSV",
    ".txt": "Text",
    ".md": "Markdown",
    ".log": "Log",
    ".eml": "Email",
    ".email": "Email",
    ".msg": "Outlook email",
    ".pptx": "Presentation",
    ".zip": "Archive",
}

# Tier 2 (§3, as amended by D-02 and D-10) — inventoried and hashed, never
# extracted, never blocking. The remediation hint is what turns "unsupported"
# into an action the operator can take.
TIER2_EXTENSIONS = {
    ".doc": "Legacy Word — open in Word and Save-As .docx or .pdf to include",
    ".rtf": "Rich Text — open in Word and Save-As .docx or .pdf to include",
    ".xer": "Primavera P6 export — schedule data, out of scope for v1",
    ".mpp": "Microsoft Project — export to PDF to include",
    ".dwg": "CAD drawing — plot to PDF to include",
    ".rar": "RAR archive — re-pack as .zip to include its members",
    ".png": "Image — no text layer; §3 lists images, they are not OCR'd as documents",
    ".jpg": "Image — no text layer; §3 lists images, they are not OCR'd as documents",
    ".jpeg": "Image — no text layer; §3 lists images, they are not OCR'd as documents",
    ".tif": "Image — no text layer; §3 lists images, they are not OCR'd as documents",
    ".tiff": "Image — no text layer; §3 lists images, they are not OCR'd as documents",
    ".bmp": "Image — no text layer; §3 lists images, they are not OCR'd as documents",
}

UNKNOWN_HINT = "Unrecognized format — inventoried and hashed only"

# A page with less native text than this is treated as image-only and routed to
# OCR. Inherited verbatim from the MIP 3.9 extractor, where it was measured
# against real scanned productions: a genuine text page clears it trivially,
# and a scanned page's stray header text does not.
_NATIVE_TEXT_FLOOR = 40

# ---------------------------------------------------------------------------
# Degradation markers — the one list of "this document did not read cleanly"
# ---------------------------------------------------------------------------
#
# Every place in this module that swallows an exception and carries on with
# LESS content than the source holds emits a note containing exactly one of
# these phrases. The walker's serial-retry pass keys off them, so the phrases
# are constants used in the f-strings rather than prose repeated by hand: a new
# degradation path that forgets to use one is invisible to the retry, and a
# reworded note that drifts from the matcher is the same defect a year later.
#
# A whole-document FAILED status is deliberately NOT in this list — it is a
# status, not a note, and the walker tests it directly.

M_OCR_PAGE = "ocr: page failed"
M_OCR_DOC = "OCR pass failed for this document"
M_OCR_FOOTER = "footer re-OCR pass failed"
M_ATTACH_ENUM = "could not enumerate attachments"
M_MSG_ATTACH = "could not read .msg attachments"
M_ATTACH_READ = "an attachment could not be read"
M_ZIP_MEMBER = "archive member unreadable"
M_ZIP_ATTACH = "attached archive unreadable"
M_PHOTO_PROBE = "image/EXIF probe failed"
M_SLIDE_NOTES = "slide notes could not be read"
M_EML_BODY = "email body could not be read"

TRANSIENT_MARKERS: tuple[str, ...] = (
    M_OCR_PAGE,
    M_OCR_DOC,
    M_OCR_FOOTER,
    M_ATTACH_ENUM,
    M_MSG_ATTACH,
    M_ATTACH_READ,
    M_ZIP_MEMBER,
    M_ZIP_ATTACH,
    M_PHOTO_PROBE,
    M_SLIDE_NOTES,
    M_EML_BODY,
)

# The second half of the vocabulary (Codex review #1, B-3).
#
# Some evidence gaps are not worth re-reading for: the same bytes re-parsed the
# same way will reach the same wall. An embedded .msg that this pass cannot
# flatten, a MIME part with no decodable payload, an email whose envelope will
# not parse at all — none of those are races, and putting them in
# TRANSIENT_MARKERS would spend the whole retry budget proving it.
#
# They are still evidence gaps, and B-3's requirement is that EVERY exception
# path yielding less evidence carries a marker, not only the retryable ones.
# Without a final vocabulary the choice was between "retry something that
# cannot improve" and "disclose in prose that nothing downstream can find
# mechanically", and the second is how these paths went unnoticed.

M_SECTIONS = "section recognition failed for this document"
M_EML_PARSE = "email envelope would not parse"
M_ATTACH_SKIPPED = "attachment content was not brought in"

# A-24. B-3 required a marker on every EXCEPTION path yielding less evidence.
# The 2026-09-10 fidelity sweep found the larger set it did not cover: paths
# that yield less evidence WITHOUT raising — a routing decision, a format the
# extractor never opens, a cell whose value was not stored. Nothing throws, so
# nothing was marked, and the document reported FULL.
#
# This is the vocabulary for that set. The rule it enforces is B-3's, widened
# from "every exception path" to "every path that yields less evidence than the
# document holds".
M_IMAGE_UNREAD = "page image content was not read"

# The Word fidelity package. Two markers, not one folded into
# M_IMAGE_UNREAD, because they answer different questions: one is "this
# construct was never read at all" (altChunk, a chart, SmartArt, a large
# picture), the other is "this text WAS read and then deliberately left out
# of the default view" (a tracked deletion). Keeping them apart means the
# still-open ruling on whether deleted text should ALSO be shown (Word spec
# part 8, zero corpus exposure) can change without touching the vocabulary the
# unread-construct notes key off.
M_WORD_UNREAD = "Word content was not read"
M_WORD_TRACKED_DELETION = "tracked deletion(s) were not shown"

FINAL_MARKERS: tuple[str, ...] = (
    M_EML_PARSE,
    M_ATTACH_SKIPPED,
    # Section recognition is arithmetic over the document's own outline and the
    # geometry of its pages. The same bytes yield the same answer, so a failure
    # here is FINAL: a re-read cannot find an outline the file does not have.
    # It is disclosed rather than swallowed because its silent form is the one
    # that matters — a document whose recognition failed keeps every page,
    # which looks exactly like a document with nothing to recognize.
    M_SECTIONS,
    # Routed to OCR, engine ran, nothing came back. Defined in
    # :mod:`dociq.ingest.pagemodel` and imported here so it is classified with
    # the rest of the vocabulary rather than living outside it — the class
    # assertion in the extraction tests is what surfaced the omission.
    #
    # FINAL, not transient: the same bytes through the same engine reach the
    # same wall, and a corpus of blank scans would otherwise spend the whole
    # serial-retry budget proving it. It is the one marker whose meaning is
    # ambiguous per page and unambiguous in bulk — one page is an unreadable
    # scan, every page is a dead engine — which is exactly what
    # :func:`ocr_yield` is for.
    M_OCR_BLANK,
    # A-24. FINAL, not transient: the image is still there and the same bytes
    # re-read the same way reach the same wall. What changes the answer is
    # enabling OCR or installing the models — an operator action, not a retry.
    M_IMAGE_UNREAD,
    # The Word fidelity package: the same bytes hold the same unread
    # construct and the same omitted deletion every time this file is
    # re-read.
    M_WORD_UNREAD,
    M_WORD_TRACKED_DELETION,
)


def has_transient_marker(text: str | None) -> bool:
    """True when a note says this document read with less than it holds, and a
    re-read alone might recover it.

    "Transient" is the possibility being tested, not a claim: the same phrase
    covers a permanently corrupt page and a page that lost a race under load,
    and telling them apart is exactly what the walker's serial retry does.
    """
    return bool(text) and any(m in text for m in TRANSIENT_MARKERS)


def has_final_marker(text: str | None) -> bool:
    """True when a note says evidence is missing and a re-read will not help.

    Not retried, still audited: :mod:`dociq.verify.accounting` counts these so
    a final gap is a number on the run's own accounting line rather than one
    sentence inside one document's notes.
    """
    return bool(text) and any(m in text for m in FINAL_MARKERS)


def has_evidence_marker(text: str | None) -> bool:
    """True when a note says ANY evidence is missing, transient or final.

    The check a caller wants when the question is "did this document read
    completely", as opposed to "should this document be re-read".
    """
    return has_transient_marker(text) or has_final_marker(text)


# A-24 bounds, named and disclosed like every other bound in this module. A page
# can legitimately carry hundreds of small images (a chart built from sprites, a
# scanned form with per-field stamps), and one OCR call per image would make a
# single pathological page cost more than the document. A region below
# _MIXED_MIN_REGION_PX on either side is smaller than a legible glyph at 200 dpi.
# Neither bound is silent: whatever they skip sets the page's evidence marker,
# because "we knew there was image content and did not read it" is the exact
# condition this amendment exists to disclose.
_MIXED_MAX_REGIONS = int(os.environ.get("DOCIQ_MIXED_MAX_REGIONS", "24"))
_MIXED_MIN_REGION_PX = 8

_XLSX_MAX_ROWS = int(os.environ.get("DOCIQ_XLSX_MAX_ROWS", "50000"))
_CSV_MAX_ROWS = int(os.environ.get("DOCIQ_CSV_MAX_ROWS", "50000"))
_ZIP_MAX_MB = int(os.environ.get("DOCIQ_ZIP_MAX_MB", "500"))
_ZIP_MAX_MEMBERS = int(os.environ.get("DOCIQ_ZIP_MAX_MEMBERS", "2000"))
_ZIP_MAX_DEPTH = int(os.environ.get("DOCIQ_ZIP_MAX_DEPTH", "3"))


def effective_caps() -> dict[str, int]:
    """The caps this process will actually apply, for the run identity.

    Read from the same module-level constants the extractors use, not from the
    environment a second time: a second read could disagree with the first if
    the environment changed after import, and the identity must record what the
    run *did*, not what it was asked to do.

    Codex review #1 finding B-2: when one of these bites, the same folder,
    profile and index produce different evidence under an identical hashed
    configuration. Per-document truncation notes disclose the effect; they do
    not repair the identity.
    """
    return {
        "xlsx_max_rows": _XLSX_MAX_ROWS,
        "csv_max_rows": _CSV_MAX_ROWS,
        "zip_max_mb": _ZIP_MAX_MB,
        "zip_max_members": _ZIP_MAX_MEMBERS,
        "zip_max_depth": _ZIP_MAX_DEPTH,
    }


@dataclass(frozen=True, slots=True)
class ExtractedDoc:
    """What one Tier-1 file yielded. Never raises out of :func:`extract`."""

    pages: tuple[PageRecord, ...] = ()
    notes: tuple[str, ...] = ()
    status: ProcessingStatus = ProcessingStatus.FULL
    error: str | None = None

    spans: tuple[SectionSpan, ...] = ()
    """Sections the document asserted about itself (Tiers 1 and 3).

    Empty is the ordinary answer, not a failure: a format with no outline and no
    recognizable page class places nothing, and every one of its pages keeps.
    On the real corpus 29.61% of pages end up here.

    Consumed by :func:`dociq.ingest.walker._record`, which stamps ``section``
    and ``section_tier`` onto the pages each span covers. It is deliberately not
    carried any further as a span: the frozen contract's records are what cross
    the stage boundaries, and Stage 4 rebuilds the spans from the stamped pages
    (:func:`dociq.sections.resolve.spans_from_pages`) so that a resumed run and
    a fresh run see the same sections."""


@dataclass
class ExtractOptions:
    """Everything that can change extracted bytes, passed explicitly.

    Module-level mutable state would make two concurrently-running matters
    influence each other's output, which the determinism contract forbids.
    """

    conf_threshold: float = 0.85
    scratch_dir: Path | None = None
    """Where formats that need a real file on disk may write one. §10 forbids
    persistent temp files outside the working folder, so the caller — which
    knows the output root — decides. ``None`` falls back to the system temp
    directory and the file is unlinked in a ``finally``."""

    ocr_enabled: bool = True
    """Off only for tests that must exercise the native path in isolation."""

    project_tokens: tuple[str, ...] = ()
    """Matter-specific tokens stripped from a section label before a template
    matches it — a vessel, a client, a yard.

    Supplied per matter and NEVER defaulted to real project names: 30.5% of the
    measured corpus's section vocabulary carries project-identifying text
    (`MV32 APPENDICES`, `STATUS OF PETROBRAS TQ`), and D-24 forbids a Long
    International template attributable to a corpus project. Empty is the safe
    value in both directions — an unstripped label simply matches no family, and
    a page whose family is unknown keeps.
    """

    footer_reocr: bool = True
    """The D-25 targeted footer re-OCR.

    A field rather than a module flag for the same reason as everything else
    here: two matters running concurrently must not be able to change each
    other's bytes. Off only for the tests that measure what the pass costs and
    what it recovers, which need the before as well as the after."""


# ---------------------------------------------------------------------------
# OCR — engine, models, and per-line confidence
# ---------------------------------------------------------------------------

_OCR_ENGINE = None
_OCR_LOCK = threading.Lock()
_OCR_POOL = None

_OCR_PAGE_WORKERS = int(os.environ.get(
    "DOCIQ_OCR_WORKERS", str(min(16, max(1, (os.cpu_count() or 2) - 2)))))

_MODEL_FILES = (
    "ch_PP-OCRv3_det_infer.onnx",
    "ch_PP-OCRv3_rec_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
)


def ocr_model_dir() -> Path:
    """Directory holding the bundled ONNX models.

    ``DOCIQ_OCR_MODEL_DIR`` overrides it, which is how an operator points a run
    at models held somewhere else. The default is the installed package's own
    ``models/`` directory — the wheel ships them, so a correct install already
    has every byte the OCR path needs and nothing is ever fetched.

    **Frozen builds resolve it from the bundle, not from ``__file__``.** In a
    PyInstaller build ``rapidocr_onnxruntime.__file__`` names a path inside the
    PYZ archive that does not exist on disk; the models are real files unpacked
    under ``sys._MEIPASS``. The frozen branch is explicit rather than relying on
    the two happening to coincide, because if they ever stop coinciding the
    failure is "OCR unavailable" on a client machine and nowhere else.
    """
    override = os.environ.get("DOCIQ_OCR_MODEL_DIR")
    if override:
        return Path(override)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "rapidocr_onnxruntime" / "models"
    import rapidocr_onnxruntime

    return Path(rapidocr_onnxruntime.__file__).parent / "models"


def ocr_models_present() -> tuple[bool, str]:
    """``(ok, message)``. The message names the missing file and the fix."""
    try:
        d = ocr_model_dir()
    except Exception as exc:
        return False, f"OCR engine 'rapidocr_onnxruntime' is not importable: {exc}"
    missing = [m for m in _MODEL_FILES if not (d / m).is_file()]
    if missing:
        return False, (
            f"OCR models are missing from {d}: {', '.join(missing)}. "
            "DocIQ never downloads them (Principle 4 — no network). Reinstall "
            "rapidocr-onnxruntime, or set DOCIQ_OCR_MODEL_DIR to a directory "
            "containing the three .onnx model files."
        )
    return True, ""


def ocr_available() -> bool:
    """True when the local OCR stack is importable AND its models are on disk.

    **A PRESENCE check, not a capability check — the name overstates it and the
    docstring is where that stops.** It imports two modules and stats three
    ``.onnx`` files. It never constructs the engine and never runs inference,
    so an engine that imports cleanly, finds its models and then produces
    nothing on every page passes it. That is where the Sprint-2 burn happened:
    *inside* inference, under :func:`_ocr_pdf_pages`'s per-page
    ``except Exception``, where a totally dead engine and a few bad pages look
    identical page by page.

    The real capability check is :func:`dociq.selftest`'s cold-construction
    probe, which builds the engine and OCRs a synthetic image — and
    ``build.py --skip-verify`` bypasses it. The run-level backstop for a build
    that shipped anyway is :func:`ocr_yield`, whose whole subject is the case
    this function cannot see.
    """
    try:
        import fitz  # noqa: F401  (pymupdf)
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return ocr_models_present()[0]


OCR_DEAD_ENGINE = (
    "OCR produced no text on ANY of the {attempted:,} page(s) it was run on in "
    "this run. A single page that recovers nothing is ordinary — a blank or "
    "unreadable scan — but every page recovering nothing is what a dead OCR "
    "engine looks like from the outside, and the per-page notes offer the "
    "innocent explanation first. Before relying on this corpus, run "
    "`dociq selftest` (it builds the engine and OCRs a test image); if that "
    "passes, these pages really are unreadable and the run stands."
)
"""The run-level alarm §4's per-page notes structurally cannot raise.

Per-document notes say "N page(s) routed to OCR recovered no text" and are read
one document at a time, where "some bad scans" is the natural reading and is
usually right. Nothing was looking at the whole run, which is the only scale at
which "every attempt, without exception" is visible — and that is the shape of
a dead engine rather than of bad pages.
"""


def ocr_yield(documents) -> tuple[int, int]:
    """``(pages OCR was attempted on, pages that recovered text)`` for a corpus.

    Reconstructed from the final page records rather than from a counter, so it
    describes the deliverable — including after a serial retry replaced a
    document's records wholesale.

    A page counts as ATTEMPTED on the DISCLOSURE, not on the kind. A page routed
    to OCR that recovers nothing is re-labelled ``EMPTY`` by
    :func:`dociq.ingest.pagemodel.make_page` (``EMPTY`` is the only kind the
    contract lets carry no ``ocr_conf``), and page 1 of a photo-only document is
    re-labelled ``PHOTO`` before that. Counting kinds would therefore have
    counted zero attempts on precisely the run this exists to catch — measured,
    not reasoned: a dead-engine walk over the scanned fixture yielded one PHOTO
    page and one EMPTY page and no ``PageKind.OCR`` at all. The
    disclosures below survive both relabellings, which is why they are the
    thing counted:

    * :data:`~dociq.ingest.pagemodel.M_OCR_BLANK` — routed to OCR, recovered
      nothing;
    * :data:`M_OCR_PAGE` — could not even be rasterized or read;
    * ``PageKind.OCR`` — recovered text, i.e. the attempts that worked;
    * ``PageKind.MIXED`` — image regions beside a text layer that recovered
      text (A-24). Also an attempt that worked, counted through
      :attr:`~dociq.contracts.PageRecord.read_by_ocr` rather than by kind;
    * :data:`M_IMAGE_UNREAD` on a PAGE — image regions that could not be read,
      an attempt that failed. Without it, a dead engine over a production of
      mixed pages attempts nothing, recovers nothing, and raises no alarm.
    """
    attempted = recovered = 0
    for doc in documents:
        for page in doc.pages:
            worked = page.read_by_ocr and page.text.strip()
            blank = any(n.startswith(M_OCR_BLANK) or n.startswith(M_OCR_PAGE)
                        or n.startswith(M_IMAGE_UNREAD)
                        for n in page.notes)
            if not (worked or blank):
                continue
            attempted += 1
            if worked:
                recovered += 1
    return attempted, recovered


def ocr_yield_warning(documents) -> str | None:
    """:data:`OCR_DEAD_ENGINE`, filled in, when a run recovered nothing at all."""
    attempted, recovered = ocr_yield(documents)
    if attempted and not recovered:
        return OCR_DEAD_ENGINE.format(attempted=attempted)
    return None


def ocr_engine_version() -> str:
    """Recorded in ``RunConfig`` — the engine identity is part of run identity."""
    try:
        from importlib.metadata import version

        return version("rapidocr_onnxruntime")
    except Exception:
        return "unknown"


_MODEL_ID_CACHE: dict[tuple, str] = {}
_MODEL_ID_LOCK = threading.Lock()


def _model_stat_key(d: Path) -> tuple:
    """Cheap identity of the model directory: name, size and mtime per file.

    Used only as a *cache key*, never as the recorded identity. Sizes and mtimes
    are what a stale cache would miss; the recorded identity is always the
    content hash below.
    """
    out = []
    for name in _MODEL_FILES:
        p = d / name
        try:
            st = p.stat()
            out.append((name, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((name, -1, -1))
    return (str(d),) + tuple(out)


def ocr_model_id() -> str:
    """Stable identity of the OCR model artifacts — package version PLUS a hash
    of the model bytes.

    Recorded in ``RunConfig.limits.ocr_model_id`` and therefore in the hashed
    run identity (Codex review #1 finding B-2). A version string alone is not
    enough: ``DOCIQ_OCR_MODEL_DIR`` can point the same installed package at
    different ONNX files, and two engines that read a page differently are
    different inputs to the run, however they were installed.

    The hash is over the three model files' names and bytes in a fixed order, so
    it is independent of directory listing order and of where the files live.
    Nothing is downloaded — Principle 4 — and a missing or unreadable model
    yields an explicit ``models-unavailable`` identity rather than a silent
    empty string that would compare equal to a run that had no OCR at all.
    """
    version = ocr_engine_version()
    try:
        d = ocr_model_dir()
    except Exception:
        return f"rapidocr_onnxruntime {version}; models-unavailable"
    key = _model_stat_key(d)
    with _MODEL_ID_LOCK:
        hit = _MODEL_ID_CACHE.get(key)
    if hit is not None:
        return hit
    h = hashlib.sha256()
    try:
        for name in _MODEL_FILES:
            h.update(name.encode("utf-8"))
            h.update(b"\0")
            with open(d / name, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    h.update(chunk)
        ident = f"rapidocr_onnxruntime {version}; models {h.hexdigest()[:32]}"
    except OSError:
        ident = f"rapidocr_onnxruntime {version}; models-unavailable"
    with _MODEL_ID_LOCK:
        _MODEL_ID_CACHE[key] = ident
    return ident


def _ocr_engine():
    """The shared RapidOCR engine.

    Construction is LOCKED, inherited from MIP 3.9: the page pool can hit this
    from ~16 threads at once and an unlocked lazy init builds N redundant
    engines (~200 MB of ONNX each — 1.5 GB RSS observed on a 7-PDF batch).

    Model paths are passed explicitly rather than left to the library's
    relative-path default, so a caller running from another working directory
    cannot silently miss them and trigger a fetch attempt.
    """
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            ok, msg = ocr_models_present()
            if not ok:
                raise ExtractionError(msg)
            from rapidocr_onnxruntime import RapidOCR

            d = ocr_model_dir()
            _OCR_ENGINE = RapidOCR(
                det_model_path=str(d / _MODEL_FILES[0]),
                rec_model_path=str(d / _MODEL_FILES[1]),
                cls_model_path=str(d / _MODEL_FILES[2]),
            )
    return _OCR_ENGINE


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One recognized line: where it was, what it said, how sure the engine was.

    The box is carried even though no contract field holds it, because §4
    Stage 3 matches Bates stamps against page *corners and footers* — that is a
    geometry question, and Track B would otherwise have to stand up a second
    OCR engine to ask it. Nothing here reaches disk.
    """

    box: tuple[tuple[float, float], ...]
    text: str
    conf: float


def ocr_lines(arr) -> list[OcrLine]:
    """OCR one image array into per-line results.

    The confidence is the whole point of the change from MIP 3.9, which joined
    ``line[1]`` and dropped ``line[2]`` — and ``line[2]`` is exactly what §4
    Stage 2's 85% review threshold is measured against.
    """
    res, _ = _ocr_engine()(arr)
    out: list[OcrLine] = []
    for line in res or []:
        try:
            conf = float(line[2])
        except (IndexError, TypeError, ValueError):
            # A line without a usable score is not silently perfect: score it
            # zero so it counts against the mean and lands in the low-confidence
            # tally, which is the direction that gets a human to look.
            conf = 0.0
        try:
            box = tuple((float(x), float(y)) for x, y in line[0])
        except (IndexError, TypeError, ValueError):
            box = ()
        out.append(OcrLine(box=box, text=str(line[1]), conf=conf))
    return out


def _ocr_array(arr) -> tuple[str, list[float]]:
    """``(joined text, per-line confidences)`` — what the page model needs."""
    lines = ocr_lines(arr)
    return " ".join(ln.text for ln in lines), [ln.conf for ln in lines]


@dataclass(frozen=True, slots=True)
class _OcrPage:
    text: str = ""
    confs: tuple[float, ...] = ()
    failed: bool = False


def _page_array(page):
    """Rasterize one PDF page straight into an OCR-ready BGR array.

    No PNG round-trip — the encode/decode costs ~0.2 s/page for nothing.
    """
    import cv2
    import numpy as np

    pix = page.get_pixmap(dpi=200)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if pix.n == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)


def _ocr_page_pool():
    global _OCR_POOL
    if _OCR_POOL is None:
        with _OCR_LOCK:
            if _OCR_POOL is None:
                from concurrent.futures import ThreadPoolExecutor

                _OCR_POOL = ThreadPoolExecutor(
                    max_workers=_OCR_PAGE_WORKERS, thread_name_prefix="ocr-page")
    return _OCR_POOL


def _ocr_pdf_pages(raw: bytes, pages: list[int]) -> dict[int, _OcrPage]:
    """OCR only the given 0-based page indices, fanned across the shared pool.

    Results are keyed by page index and the caller reassembles by index, never
    by completion order — the pool returns pages as they finish, and an
    order-of-completion assembly would produce a different document on every
    run. The contract's gapless-1..N check would catch a *missing* page but not
    a *permuted* one, so this is the primary defence, not the backstop.

    Rasterization is chunked: a 300-page scan at 200 dpi is ~11 MB/page, so
    rasterizing everything up front would cost gigabytes per file.
    """
    import fitz  # pymupdf

    out: dict[int, _OcrPage] = {}
    chunk_n = 16
    pool = _ocr_page_pool()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        idxs = [i for i in pages if 0 <= i < len(doc)]
        for c0 in range(0, len(idxs), chunk_n):
            arrays: dict[int, object] = {}
            for i in idxs[c0:c0 + chunk_n]:
                try:
                    arrays[i] = _page_array(doc[i])
                except Exception:
                    out[i] = _OcrPage(failed=True)  # one bad page must not sink the doc
            futs = {i: pool.submit(_ocr_array, a) for i, a in arrays.items()}
            for i, fut in futs.items():
                try:
                    text, confs = fut.result()
                    out[i] = _OcrPage(text=text, confs=tuple(confs))
                except Exception:
                    out[i] = _OcrPage(failed=True)
    return out


def _pdf_image_rects(page) -> list[tuple[float, float, float, float]]:
    """Every embedded raster image's box on one page, merged and in reading order.

    Returns plain ``(x0, y0, x1, y1)`` tuples in PDF user space.

    **Overlaps are unioned, and that is load-bearing rather than tidy.**
    :func:`_page_image_share` records that overlapping images are deliberately
    NOT de-overlapped there, because its number has to keep agreeing with a
    published measurement. Here the consequence is different: two overlapping
    images cropped separately hand the same glyphs to OCR twice, and the page
    would carry the text twice. Merging first is what actually makes the
    duplication A-24 exists to avoid impossible, rather than merely unlikely.

    **The order is total.** Sorting on ``(y0, x0)`` alone leaves ties broken by
    the order PyMuPDF enumerated the page's resources in, which is not a reading
    order and is not promised to be stable — and an unstable order here would
    reorder text inside a page between runs, which is a determinism defect in
    the one product whose headline claim is byte-identical repeat runs. The full
    box is in the key, so two distinct boxes can never tie.
    """
    try:
        raw_rects = [page.get_image_bbox(info) for info in page.get_images(full=True)]
    except Exception:
        return []
    boxes: list[tuple[float, float, float, float]] = []
    for r in raw_rects:
        if r is None or abs(r.get_area()) <= 0:
            continue
        boxes.append((min(r.x0, r.x1), min(r.y0, r.y1),
                      max(r.x0, r.x1), max(r.y0, r.y1)))

    # Union every pair that overlaps, repeatedly, until nothing else merges.
    merged = True
    while merged and len(boxes) > 1:
        merged = False
        out: list[tuple[float, float, float, float]] = []
        for b in boxes:
            for i, a in enumerate(out):
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    out[i] = (min(a[0], b[0]), min(a[1], b[1]),
                              max(a[2], b[2]), max(a[3], b[3]))
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out

    boxes.sort(key=lambda b: (b[1], b[0], b[3], b[2]))
    return boxes


def _merge_image_text(native: str, image: str) -> str:
    """A MIXED page's text: the text layer's opening lines, then the image text,
    then the rest of the text layer (amendment A-24).

    **The position protects the page's locator; it is not a claim about reading
    order.** The Bates zone reads a page's first ``head_lines`` lines and its
    last ``tail_lines`` lines. Image text first shipped APPENDED after the text
    layer, and that moved text-layer lines out of the zone. Measured before this
    placement, over 15,000 randomized layouts after normalization: appending
    pushed a text-layer zone line out in 13,081 of them. This placement: 0.

    The consequence was not only a lost stamp but a WRONG one. From seven or
    eight image lines up, a page's own footer stamp left the tail zone, and if
    an image line carried a different stamp -- a copy of another exhibit
    embedded in the page -- that foreign stamp was the only one left in the zone
    and was returned as this page's locator. A locator that points at a
    different document is the failure criterion 4 forbids outright.

    Inserting after the opening lines keeps every text-layer line in the zone
    exactly where the Bates search expects it, however many image lines there
    are. That is now a backstop rather than the rule. D-49 keeps image lines out
    of every Bates zone read altogether, through
    :attr:`dociq.contracts.PageRecord.locator_text`, because when the text layer
    carries NO stamp no placement can stop an embedded exhibit's stamp becoming
    this page's locator. The placement still protects any reader of raw page
    text.

    The cost is stated rather than hidden: image text now reads after the
    letterhead instead of at the end of the page. Neither is true reading order,
    which would need line positions this extractor does not carry.
    """
    return _merge_image_lines(native, image)[0]


def _merge_image_lines(native: str, image: str) -> tuple[str, tuple[int, int] | None]:
    """:func:`_merge_image_text`, and WHERE the image lines landed (D-49).

    One function computes both, because the position is only meaningful if it is
    the position the text was actually built with. A second function that
    recomputed the head on its own would be two readings of one decision, and
    those drift. The span is ``(first line, line count)`` over the returned text,
    which is already normalized, so :func:`make_page` accepts it.
    """
    image_lines = [ln for ln in normalize(image).split("\n") if ln.strip()]
    if not image_lines:
        return native, None
    lines = normalize(native).split("\n") if native.strip() else []
    head = min(BatesZone().head_lines, len(lines))
    return ("\n".join(lines[:head] + image_lines + lines[head:]),
            (head, len(image_lines)))


def _ocr_pdf_regions(raw: bytes, pages: list[int]) -> dict[int, _OcrPage]:
    """OCR only the IMAGE REGIONS of the given 0-based page indices (A-24).

    These are pages that already have a substantive text layer, so the
    whole-page pass would re-read glyphs the page already carries correctly and
    hand the caller two readings of one line to reconcile. Dedup between a
    native line and its OCR is a similarity judgement — the OCR of a letterhead
    is *nearly* the native text, never equal to it — and a hand-tuned threshold
    is exactly the kind of bound this codebase is trying to stop shipping.

    Reading only the area the text layer does not cover makes the duplication
    impossible by construction instead of filtered afterwards.

    **The page is rendered ONCE and sliced**, for the reason ``_band_tiles``
    records: a per-region ``get_pixmap`` clip re-decodes the page's embedded
    image every time, and on this corpus a page can be a 230 MB photograph.
    """
    import fitz  # pymupdf
    import numpy as np

    out: dict[int, _OcrPage] = {}
    chunk_n = 16
    pool = _ocr_page_pool()
    scale = 200.0 / 72.0  # _page_array renders at 200 dpi; PDF user space is pt
    with fitz.open(stream=raw, filetype="pdf") as doc:
        idxs = [i for i in pages if 0 <= i < len(doc)]
        for c0 in range(0, len(idxs), chunk_n):
            crops: dict[int, list] = {}
            skipped: dict[int, int] = {}
            for i in idxs[c0:c0 + chunk_n]:
                try:
                    page = doc[i]
                    rects = _pdf_image_rects(page)
                    if not rects:
                        continue
                    dropped = max(0, len(rects) - _MIXED_MAX_REGIONS)
                    arr = _page_array(page)
                    h, w = arr.shape[:2]
                    tiles = []
                    for r in rects[:_MIXED_MAX_REGIONS]:
                        x0 = max(0, min(w, int(round((r[0] - page.rect.x0) * scale))))
                        y0 = max(0, min(h, int(round((r[1] - page.rect.y0) * scale))))
                        x1 = max(0, min(w, int(round((r[2] - page.rect.x0) * scale))))
                        y1 = max(0, min(h, int(round((r[3] - page.rect.y0) * scale))))
                        if x1 - x0 < _MIXED_MIN_REGION_PX or y1 - y0 < _MIXED_MIN_REGION_PX:
                            dropped += 1  # smaller than a legible glyph
                            continue
                        tiles.append(np.ascontiguousarray(arr[y0:y1, x0:x1]))
                    if tiles or dropped:
                        crops[i] = tiles
                        skipped[i] = dropped
                except Exception:
                    out[i] = _OcrPage(failed=True)  # one bad page must not sink the doc
            futs = {i: [pool.submit(_ocr_array, t) for t in tiles]
                    for i, tiles in crops.items()}
            for i, fs in futs.items():
                texts: list[str] = []
                confs: list[float] = []
                failed = False
                for f in fs:
                    try:
                        text, cs = f.result()
                        if text.strip():
                            texts.append(text)
                        confs.extend(cs)
                    except Exception:
                        failed = True
                # `failed` is set when ANY region of the page could not be read,
                # even if other regions on the same page read fine. An earlier
                # draft wrote `failed and not texts`, which reported success
                # whenever anything at all came back — so a page with one
                # unreadable region among several lost it silently under a
                # clean status. That is precisely the defect class A-24 exists
                # to close, reintroduced one layer down, and it is recorded
                # rather than quietly corrected because the first draft of the
                # fix made the same mistake as the code it was fixing.
                out[i] = _OcrPage(text="\n".join(texts), confs=tuple(confs),
                                  failed=failed or skipped.get(i, 0) > 0)
    return out


# ---------------------------------------------------------------------------
# Targeted footer re-OCR — D-25
# ---------------------------------------------------------------------------
#
# A Bates stamp is not body text and reading it as body text is what the
# criterion-4 acceptance run measured the cost of: 100.000% of native-text pages
# carried their locator and 31.250% of OCR'd pages did (593/648 overall against
# a >=99% bar, 0 wrong, 0 false positives, 55 absent). The whole-page pass is
# one recognition tuned for a page of prose; a six-character stamp in a 10pt
# footer is a rounding error inside it.
#
# So the stamp gets its own recognition. The band of the page where a stamp
# lives is rasterized on its own at a much higher resolution and read again,
# and only the stamp-shaped tokens of that reading are merged back.
#
# THREE PROPERTIES ARE LOAD-BEARING AND EACH IS ENFORCED HERE RATHER THAN
# HOPED FOR:
#
# 1. It runs ONLY where it can help. The pass fires on a page that (a) DocIQ
#    had to OCR at all and (b) whose ordinary reading yielded no stamp-shaped
#    line anywhere in the Bates zone. A native-text page never pays for it, and
#    on the acceptance corpus that is 568 of 648 pages.
# 2. It cannot turn a miss into a WRONG answer. Nothing here writes a locator.
#    It appends candidate TEXT, which Stage 3 then judges with exactly the same
#    grammar and the same operator-confirmed format as any other text. A
#    misread footer produces a token that does not match the confirmed format
#    and is ignored, which leaves the page a flagged miss.
# 3. Nothing about the attempt reaches hashed content. No timing, no retry
#    count, no resolution, no marker. The output is a deterministic function of
#    the page bytes: the same PDF yields the same appended tokens forever.

FOOTER_REOCR_DPI = 400
"""Rasterization resolution for the band pass, against 200 dpi for the page.

Doubling the resolution doubles the glyph height the recognizer sees BEFORE its
own fixed-height crop resize, which is the mechanism: at 200 dpi a 10pt stamp is
~28 px tall and the recognizer's input is 48 px, so it is upsampling guesswork;
at 400 dpi it is downsampling a real reading.

**Measured, and the measurement bounds the claim.** Over the 55 pages the
criterion-4 baseline missed, 400 dpi reads the stamp's DIGITS correctly where
the whole-page pass read nothing at all. 300 dpi is close behind; 600 and 800
dpi are WORSE, not better, which is the opposite of the naive expectation and
the reason this number is measured rather than maximised. See
``docs/verification/bates_d25_2026-08-01.md`` for the sweep, and for the part of
the stamp the band pass does *not* fix.
"""

FOOTER_REOCR_BAND_PT = 90.0
"""Height of the stamp band, in PDF points — a physical 1.25 inches, not a
fraction of the page.

This was a fraction of page height in the first draft and that was a defect,
found by measuring rather than by reading. The acceptance corpus contains pages
that are **2700 x 3681 points** — photographs whose page box is 37 x 51 inches —
and 14% of that page is a seven-inch strip. Two things went wrong at once: the
band became tens of megapixels (a sweep over 55 such pages did not finish in 90
minutes), and its aspect ratio tripped the bypass below.

A Bates stamp is burned in at a physical size, a physical distance from the
physical edge of the page. On the acceptance corpus it sits within ~40 points of
the bottom edge whether the page is letter-size or four feet tall. So the band
is physical too, and its cost stops scaling with page AREA.
"""

_FOOTER_MAX_ASPECT = 8.0
"""rapidocr SKIPS text detection entirely when width/height exceeds its
``width_height_ratio`` (8.0 in the shipped ``config.yaml``) and recognizes the
whole strip as a single line. Measured on the corpus: a 3600pt-wide page's
footer strip is 13:1, detection was bypassed, and the pass returned nothing
readable at all. So the band is TILED — never stretched — and every tile is at
most this ratio wide. It is the engine's own configuration, not a taste."""

_FOOTER_TILE_OVERLAP = 0.2
"""How much consecutive tiles overlap, as a fraction of tile width.

Without it a stamp straddling a tile boundary is cut in half and read as two
things, neither of which is a locator. One fifth is comfortably wider than any
stamp relative to a ten-inch tile."""

_FOOTER_MAX_TILES = 12
"""Hard ceiling on tiles per band, disclosed rather than silent.

Tile width is ``8 x band`` and ``band`` is ``min(1.25in, page height)``, so a
page that is very wide and very SHORT makes tiles arbitrarily narrow: a
612 x 0.1pt page yields nearly a thousand of them. That is a degenerate page
rather than a real one, but "degenerate input cannot reach this" is exactly the
assumption that produces a run that never finishes.

Twelve covers a page ten feet wide at the full band height, which is past any
sheet size a production contains. Beyond it the band is read left to right and
the REMAINDER IS NOT READ — a stamp out there is a miss, which is the failure
direction §4 asks for, and the ceiling is stated here rather than discovered."""

_FOOTER_PROBE_PAGES = 4
"""How many of a document's qualifying pages are probed on BOTH bands before
the pass decides which band, if either, reads this production.

Four rather than one because a production's first scanned page can be a cover
sheet, a photograph, or a fax header, and one unlucky page should not switch the
pass off for a 300-page document. Four rather than forty because the decision is
about where the production burns its stamp, and that does not vary page to page.

Stated rather than tuned: a stamped production resolves the very first page
probed, so this bound costs it nothing; an unreadable scan pays eight
recognitions instead of thousands, and the pages it declines to read are
reported in the document's notes."""

_FOOTER_CHUNK_PAGES = 8
"""Pages whose tiles are rasterized before any of them is recognized. Bounds
peak memory the way :func:`_ocr_pdf_pages` does, and lower here because one page
can be several tiles."""


def _band_tiles(page, dpi: int, band_pt: float, *, top: bool) -> list:
    """The stamp band of one page, as OCR-ready tiles in left-to-right order.

    The clip is taken in PDF user space and rendered at ``dpi``, so each tile is
    a genuine re-render at higher resolution — not an upscale of the 200 dpi
    page image, which would add no information at all.

    Cost is bounded by construction, and the bound is arithmetic rather than a
    cap: a tile is at most ``band_pt`` tall and ``_FOOTER_MAX_ASPECT * band_pt``
    wide, so a tile is ~1.7 Mpx at 400 dpi whatever the page is, and the tile
    COUNT grows with page width alone. A four-foot-wide page costs five tiles,
    not fifty megapixels.

    **The band is rendered ONCE and sliced, and that is a fix rather than a
    style.** Rendering each tile with its own ``get_pixmap`` clip re-decodes the
    page's embedded image every time: on this corpus a page is a 230 MB
    photograph, so five tiles top and bottom meant ten full decodes of it. The
    acceptance run took over an hour and a half that way and had to be killed.
    One render, then numpy views — the pixels are the same and the decode
    happens once.
    """
    import cv2
    import fitz
    import numpy as np

    r = page.rect
    band = min(band_pt, r.height)
    y0 = r.y0 if top else r.y1 - band
    pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(r.x0, y0, r.x1, y0 + band))
    arr = np.frombuffer(pix.samples, np.uint8).reshape(
        pix.height, pix.width, pix.n)
    if pix.n == 1:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif pix.n == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    else:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

    h, w = arr.shape[:2]
    if h <= 0 or w <= 0:
        return []
    tile_w = min(w, max(1, int(round(_FOOTER_MAX_ASPECT * h))))
    step = max(1, int(round(tile_w * (1.0 - _FOOTER_TILE_OVERLAP))))

    spans: list[tuple[int, int]] = []
    x = 0
    while True:
        x1 = min(x + tile_w, w)
        span = (max(0, x1 - tile_w), x1)
        if span not in spans:
            spans.append(span)
        if x1 >= w or len(spans) >= _FOOTER_MAX_TILES:
            break
        x += step
    return [np.ascontiguousarray(arr[:, x0:x1]) for x0, x1 in spans]


def _band_tokens(arr) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """``(stamp-shaped tokens, their confidences)`` from one band tile."""
    from ..identify.bates import stamp_tokens

    lines = ocr_lines(arr)
    tokens = stamp_tokens("\n".join(ln.text for ln in lines))
    if not tokens:
        return (), ()
    # Each token's confidence is the confidence of the recognized line it came
    # out of. A token whose line cannot be identified scores 0.0 rather than
    # silently perfect — the same rule ``ocr_lines`` applies to a line with no
    # usable score.
    confs: list[float] = []
    for tok in tokens:
        best = 0.0
        for ln in lines:
            if tok in " ".join(ln.text.split()):
                best = max(best, ln.conf)
        confs.append(best)
    return tokens, tuple(confs)


def _band_pass(doc, idxs: list[int], top: bool, pool,
               out: dict[int, tuple[tuple[str, ...], tuple[float, ...]]]) -> bool:
    """Read one band of the given pages into ``out``. True if anything read.

    **Assembled in submission order, never completion order.** Tiles are
    submitted page by page and left to right and their results are consumed in
    that same order, so which tile finishes first cannot change a page's text.
    That is the same rule, and the same reason, as :func:`_ocr_pdf_pages`, and
    it is what makes criterion 7 hold over a pass that fans out.
    """
    recovered = False
    for c0 in range(0, len(idxs), _FOOTER_CHUNK_PAGES):
        jobs: list[tuple[int, object]] = []
        for i in idxs[c0:c0 + _FOOTER_CHUNK_PAGES]:
            out.setdefault(i, ((), ()))
            try:
                tiles = _band_tiles(doc[i], FOOTER_REOCR_DPI,
                                    FOOTER_REOCR_BAND_PT, top=top)
            except Exception:
                continue          # one page that will not rasterize, not the doc
            jobs.extend((i, pool.submit(_band_tokens, a)) for a in tiles)
        merged: dict[int, tuple[list[str], list[float]]] = {}
        for i, fut in jobs:
            try:
                toks, confs = fut.result()
            except Exception:
                toks, confs = (), ()
            slot = merged.setdefault(i, ([], []))
            for tok, conf in zip(toks, confs):
                if tok in slot[0]:          # the tile overlap sees it twice
                    continue
                if len(slot[0]) >= FOOTER_BLOCK_MAX_LINES:
                    break
                slot[0].append(tok)
                slot[1].append(conf)
        for i, (toks, confs) in merged.items():
            if toks:
                out[i] = (tuple(toks), tuple(confs))
                recovered = True
    return recovered


def _reocr_bands(raw: bytes, pages: list[int]
                 ) -> dict[int, tuple[tuple[str, ...], tuple[float, ...]]]:
    """Re-read the stamp band of the given 0-based pages.

    §4 says "page corners/footers", so both the bottom and the top of the page
    are covered — a header-stamped production is an ordinary thing and assuming
    it away would be a silent limit.

    **It CALIBRATES, and that is what makes it affordable.** "Only where it can
    help" is a per-PAGE test and a per-page test is not enough: on an image-only
    document every page qualifies, and reading two bands of five tiles each on a
    300-page scan is three thousand recognitions for one document. That is not a
    hypothetical — it is what a criterion-4 re-run was doing when it passed nine
    hours and had to be killed.

    Where a production burns its stamp is a property of the PRODUCTION, not of
    the page. So the first :data:`_FOOTER_PROBE_PAGES` qualifying pages are
    probed on both bands; after that only the band(s) that actually read
    something are used, and if NEITHER read anything the pass stops for that
    document. A scan whose footers cannot be read costs eight recognitions
    instead of three thousand; a stamped production keeps every page it was
    going to get, because a production that stamps its footer resolves the very
    first page probed.

    The calibration is a function of the page contents in page order, so it is
    identical run to run for the same bytes. The pages it declines to read are
    the caller's to disclose: every page this was ASKED about has an entry in
    the returned mapping, so ``len(pages) - len(result)`` is the number skipped.
    """
    import fitz

    out: dict[int, tuple[tuple[str, ...], tuple[float, ...]]] = {}
    pool = _ocr_page_pool()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        idxs = [i for i in pages if 0 <= i < len(doc)]
        if not idxs:
            return out
        probe, rest = idxs[:_FOOTER_PROBE_PAGES], idxs[_FOOTER_PROBE_PAGES:]

        # Phase 1 — probe both bands, bottom first, on the opening pages.
        productive: set[bool] = set()
        for top in (False, True):
            todo = [i for i in probe if not out.get(i, ((), ()))[0]]
            if not todo:
                break
            if _band_pass(doc, todo, top, pool, out):
                productive.add(top)

        # Phase 2 — the remainder, on the bands that demonstrably read this
        # production. Nothing productive means nothing is read at all.
        for top in (False, True):
            if top not in productive:
                continue
            todo = [i for i in rest if not out.get(i, ((), ()))[0]]
            if not todo:
                break
            _band_pass(doc, todo, top, pool, out)
    return out


# ---------------------------------------------------------------------------
# Photo PDFs — deterministic EXIF, no AI (§12)
# ---------------------------------------------------------------------------


def _gps_to_decimal(ref, vals) -> float | None:
    """One GPS coordinate as signed decimal degrees, or ``None``.

    Used to return ``0.0`` from a bare ``except``, which is not a failure value
    at all: it is a valid coordinate on the equator and on the prime meridian,
    and the caller's ``if lat or lon`` test then read an unparseable fix as "no
    fix" and dropped it with no record (Codex review #1, B-3, sibling class).
    ``None`` is unambiguous and the caller discloses it.
    """
    try:
        d, m, s = (float(v) for v in vals)
        dec = d + m / 60.0 + s / 3600.0
        return -dec if str(ref).upper() in ("S", "W") else dec
    except Exception:
        return None


def exif_from_image_bytes(img: bytes) -> tuple[dict, list[str]]:
    """``({'date': ..., 'gps': ...}, notes)`` — best-effort EXIF read.

    **Why this returns notes.** It used to return a bare dict and swallow four
    separate exceptions into ``pass``: the whole-image open, the EXIF-IFD read
    that holds ``DateTimeOriginal``, the GPS-IFD read, and each coordinate's
    own conversion. A site photo's camera date and GPS fix are the only
    evidence such a document carries — OCR never looks where a camera writes —
    so each of those silent paths deleted the entire evidentiary content of the
    page and reported a clean read. That is the same Principle-1 defect as the
    EML body walk, in the class Codex review #1 (B-3) named alongside it.

    A dict cannot say "there was EXIF here and I could not read it", so the
    return type had to widen. The notes carry :data:`M_PHOTO_PROBE`, which is
    already a transient marker, so a photo whose EXIF would not read is re-read
    serially like any other degraded document.
    """
    out: dict = {}
    notes: list[str] = []
    try:
        from PIL import ExifTags, Image

        with Image.open(io.BytesIO(img)) as im:
            exif = im.getexif()
            if not exif:
                return out, notes
            dt = None
            try:  # DateTimeOriginal lives in the EXIF IFD; fall back to DateTime
                ifd = exif.get_ifd(ExifTags.IFD.Exif)
                dt = ifd.get(ExifTags.Base.DateTimeOriginal)
            except Exception as exc:
                # Not silent even though a fallback follows: if the fallback
                # also comes back empty, the document loses its date and the
                # only reason would otherwise be invisible.
                notes.append(f"{M_PHOTO_PROBE}: the EXIF sub-directory holding "
                             f"DateTimeOriginal could not be read ({exc}); the "
                             "capture date falls back to the basic DateTime tag"
                             [:300])
            dt = dt or exif.get(ExifTags.Base.DateTime)
            if dt:
                s = str(dt).strip()  # "YYYY:MM:DD HH:MM:SS"
                if len(s) >= 10 and s[4] == ":" and s[7] == ":":
                    s = s[:10].replace(":", "-") + s[10:]
                out["date"] = s[:16]
            try:
                gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
                if gps:
                    lat = _gps_to_decimal(gps.get(1), gps.get(2) or ())
                    lon = _gps_to_decimal(gps.get(3), gps.get(4) or ())
                    if lat is None or lon is None:
                        if gps.get(2) or gps.get(4):
                            notes.append(
                                f"{M_PHOTO_PROBE}: this image carries a GPS tag "
                                "whose coordinates could not be converted; the "
                                "location is absent from this document")
                    elif lat or lon:
                        out["gps"] = f"{lat:.6f}, {lon:.6f}"
            except Exception as exc:
                notes.append(f"{M_PHOTO_PROBE}: the image's GPS sub-directory "
                             f"could not be read ({exc}); the location is "
                             "absent from this document"[:300])
    except Exception as exc:
        notes.append(f"{M_PHOTO_PROBE}: the embedded image could not be opened "
                     f"for an EXIF read ({exc}); the camera date and GPS are "
                     "absent from this document"[:300])
    return out, notes


def _photo_block(raw: bytes, n_pages: int,
                 content_len: int) -> tuple[str, list[str]]:
    """``(marker text, notes)`` for an image-based PDF (a photo print-out).

    The notes are empty except when the probe itself failed. It used to return
    ``""`` from a bare ``except Exception``, which meant a file whose EXIF read
    threw — for any reason, including a transient one — silently lost its
    ``[PHOTO]`` block and its camera date, and the run said nothing at all. A
    swallowed exception that changes the emitted text is a Principle-1
    violation whatever caused it.

    Widened from one note to a list by Codex review #1 (B-3): the EXIF read
    below can now fail in four distinguishable ways and reporting only the
    first would reintroduce the same silence one level down.

    Photo test: trivial text layer plus at least one large embedded image; EXIF
    comes from the largest such image. A site photo carries its evidence where
    OCR never looks — the camera-stamped date and GPS — and surfacing it as
    text is what lets Stage 1 date the document at all.
    """
    if content_len >= max(40, 8 * n_pages):
        return "", []
    exif_notes: list[str] = []
    try:
        import fitz

        with fitz.open(stream=raw, filetype="pdf") as doc:
            biggest, n_imgs = None, 0
            for page in doc:
                for info in page.get_images(full=True):
                    n_imgs += 1
                    xref = info[0]
                    w, h = int(info[2] or 0), int(info[3] or 0)
                    if w * h < 250_000:  # ignore logos/stamps (<~0.25 MP)
                        continue
                    if biggest is None or w * h > biggest[0]:
                        biggest = (w * h, xref)
            if biggest is None:
                return "", []
            meta: dict = {}
            try:
                meta, exif_notes = exif_from_image_bytes(
                    doc.extract_image(biggest[1])["image"])
            except Exception as exc:
                exif_notes = [(f"{M_PHOTO_PROBE}: the embedded image's EXIF could "
                               f"not be read ({exc}); the camera date and GPS are "
                               "absent from this document")[:300]]
            parts = [f"[PHOTO] Image-based document ({len(doc)} page(s), "
                     f"{n_imgs} image(s))."]
            if meta.get("date"):
                parts.append(f"Camera (EXIF) date: {meta['date']}.")
            if meta.get("gps"):
                parts.append(f"GPS: {meta['gps']}.")
            parts.append("Visual content not machine-read — view the source image "
                         "for what the photo shows.")
            return " ".join(parts), exif_notes
    except Exception as exc:
        return "", [(f"{M_PHOTO_PROBE}: this document was probed as an "
                     f"image-based (photo) PDF and the probe raised ({exc}); "
                     "no [PHOTO] block was emitted")[:300]]


# ---------------------------------------------------------------------------
# Per-format extractors — each returns a list of PageRecords
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Section recognition — Tiers 1 and 3, where the file is still in hand (D-35)
# ---------------------------------------------------------------------------
#
# This runs here, at extraction, and nowhere later, for a reason that is about
# availability rather than tidiness: Tier 1 reads the PDF's own outline and
# Tier 3 measures the geometry of its pages, and by Stage 4 the bytes are gone —
# the pipeline holds `DocumentRecord`s, not files. Recognition therefore has to
# happen while the document is open, and its ANSWER travels forward stamped onto
# the pages (`walker._record`), which is also what makes it survive a resumed
# run.
#
# Nothing here decides a disposition. Every page this places gains a section and
# a tier and stays KEEP; only an expert-approved omission can drop one (D-34).


def _page_image_share(page) -> tuple[float, bool]:
    """Share of one page covered by embedded raster images, and whether any
    exist.

    Transcribed from `tools/measure_sections.py`, deliberately including its
    limitation: overlapping images are not de-overlapped, so the share is an
    UPPER bound. That is the direction the probe measured Q3's figures with, and
    the shipped engine has to agree with the measurement that justified it — a
    tidier implementation here would silently invalidate 1,308 pages of
    published number.
    """
    try:
        rects = [page.get_image_bbox(info) for info in page.get_images(full=True)]
    except Exception:
        return 0.0, False
    if not rects:
        return 0.0, False
    page_area = abs(page.rect.get_area())
    if page_area <= 0:
        return 0.0, True
    covered = sum(abs(r.get_area()) for r in rects if r is not None)
    return min(covered / page_area, 1.0), True


def pdf_spans(
    raw: bytes,
    pages: Sequence[PageRecord],
    *,
    project_tokens: tuple[str, ...] = (),
) -> tuple[SectionSpan, ...]:
    """Every section span one PDF asserts about itself.

    ``pages`` supplies the TEXT, and it is the pipeline's own extracted text —
    native layer, OCR where there was none, footer band merged — not a second
    reading taken from PyMuPDF. One reading of a document, for the same reason
    the contract has one serializer: two readings drift, and the one that drifts
    is invisible because both look right in isolation.

    **Two honest differences from the probe that measured Q3, neither hidden.**
    The probe classified Tier 3 only in documents whose outline is not
    substantive; this classifies every page no Tier-1 span covers, including
    pages inside an outlined document, so shipped Tier-3 reach is at or above
    the measured 1,308 pages rather than equal to it. And the probe read
    PyMuPDF's text where this reads the pipeline's, so a scanned page that the
    probe saw as empty is classified here on its OCR text. Both differences make
    this recognize MORE than the measurement did; neither can drop a page,
    because dropping needs an approval.

    Image geometry is measured only for pages Tier 1 did not place — 63.01% of
    the corpus is placed by outline, and the geometry of those pages is work no
    tier would read.
    """
    import fitz  # pymupdf

    texts = {p.page_no: p.text for p in pages}
    page_count = len(pages)
    with fitz.open(stream=raw, filetype="pdf") as doc:
        outline: list[tuple[str, int]] = []
        for item in doc.get_toc(simple=True):
            if len(item) < 3:
                continue
            _level, title, page1 = item[0], item[1], item[2]
            # PyMuPDF reports an unresolved destination as -1. Dropped rather
            # than coerced: a destination that does not resolve is exactly the
            # case where a lookup would place a section on the wrong page, and
            # a wrongly placed section is the failure D-35 exists to prevent.
            if not isinstance(page1, int) or page1 < 1:
                continue
            outline.append((str(title), page1 - 1))

        placed = spans_from_outline(
            outline, page_count, project_tokens=project_tokens
        )
        covered = frozenset(
            n for span in placed for n in range(span.start_page, span.end_page + 1)
        )
        signals: list[PageSignals] = []
        for index in range(min(page_count, doc.page_count)):
            page_no = index + 1
            if page_no in covered:
                continue
            share, has_image = _page_image_share(doc[index])
            signals.append(PageSignals(
                page_no=page_no,
                text=texts.get(page_no, ""),
                has_image=has_image,
                image_area_share=share,
            ))

    return resolve_sections(
        outline=outline,
        page_count=page_count,
        signals=signals,
        project_tokens=project_tokens,
    )


def _extract_pdf(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """Hybrid per-page routing: keep each page's text layer when it is
    substantive, OCR only the pages without one.

    Real productions are mixed — typed correspondence interleaved with scanned
    attachments — so this simultaneously OCRs LESS (text pages skip the
    expensive path) and extracts MORE (scanned attachments inside text-rich
    files previously contributed nothing, because the whole file took the text
    route).
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover — declared, so always present
        raise ExtractionError("PDF support requires 'pypdf'.") from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        native = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise ExtractionError(f"Could not read PDF: {exc}") from exc

    notes: list[str] = []
    n = len(native)
    # Measure actual page CONTENT so an empty text layer is not masked.
    content_len = sum(len(t.strip()) for t in native)
    photo, photo_notes = _photo_block(raw, n, content_len)
    notes.extend(photo_notes)

    ocr_by_page: dict[int, _OcrPage] = {}
    need = [i for i, t in enumerate(native) if len(t.strip()) < _NATIVE_TEXT_FLOOR]
    # Word spec part 10 (recorded under D-49): a scanned page nobody read IS page
    # image content that was not read, so both notes below carry
    # M_IMAGE_UNREAD — the same marker
    # the geometry-based (A-24) notes further down already carry. Only the
    # PREFIX changes; "OCR disabled" and "OCR is unavailable" stay in the
    # text verbatim, because an existing test asserts the first substring.
    if need and opt.ocr_enabled:
        if not ocr_available():
            notes.append(
                f"{M_IMAGE_UNREAD}: {len(need)} page(s) have no usable text "
                f"layer and OCR is unavailable: {ocr_models_present()[1]}")
        else:
            try:
                ocr_by_page = _ocr_pdf_pages(raw, need)
            except Exception as exc:
                notes.append(f"{M_OCR_DOC}: {exc}")
    elif need and not opt.ocr_enabled:
        notes.append(f"{M_IMAGE_UNREAD}: {len(need)} page(s) have no usable "
                     f"text layer; OCR disabled")

    # --- A-24: the page that is BOTH ---------------------------------------
    # The routing above asks one question — does this page have a text layer —
    # so a page with an electronically applied letterhead over a photographed
    # schedule table answers yes and its table is never read. Measured on the
    # acceptance corpus before this existed: 3,573 pages, 20.15% of the
    # production, 290 of 298 documents.
    #
    # The threshold is Tier 3's own PHOTO_MIN_IMAGE_AREA_SHARE, not a second
    # bound invented here. Tier 3 already looks at these pages and calls them
    # "Photograph / figure page — 60% of the page covered by an image". Two
    # subsystems reading one page disagreed and the one that was right ran
    # second; agreeing with it is the fix, and a separate constant would only
    # let them drift apart again.
    region_by_page: dict[int, _OcrPage] = {}
    routed = set(need)
    mixed: list[int] = []
    try:
        import fitz  # pymupdf

        with fitz.open(stream=raw, filetype="pdf") as _geom:
            for i in range(min(n, _geom.page_count)):
                if i in routed:
                    continue  # already going to OCR whole-page
                share, has_image = _page_image_share(_geom[i])
                if has_image and share >= PHOTO_MIN_IMAGE_AREA_SHARE:
                    mixed.append(i)
    except Exception as exc:
        # Geometry is how this page class is FOUND. If it cannot be measured we
        # do not know whether any page carries unread image content, and saying
        # nothing is the failure mode this amendment exists to close.
        notes.append(f"{M_IMAGE_UNREAD}: image geometry could not be measured "
                     f"({sanitize_message(str(exc))})")
    if mixed:
        if not opt.ocr_enabled:
            notes.append(f"{M_IMAGE_UNREAD}: {len(mixed)} page(s) carry an image "
                         f"covering {PHOTO_MIN_IMAGE_AREA_SHARE:.0%} or more of "
                         f"the page beside their text layer; OCR disabled")
        elif not ocr_available():
            notes.append(f"{M_IMAGE_UNREAD}: {len(mixed)} page(s) carry an image "
                         f"beside their text layer and OCR is unavailable: "
                         f"{ocr_models_present()[1]}")
        else:
            try:
                region_by_page = _ocr_pdf_regions(raw, mixed)
            except Exception as exc:
                notes.append(f"{M_IMAGE_UNREAD}: {sanitize_message(str(exc))}")

    # --- D-25: the stamp gets its own recognition, where it can help --------
    # Only pages DocIQ actually OCR'd, and only those whose ordinary reading
    # produced nothing stamp-shaped anywhere in the Bates zone. The trigger is
    # a pure function of the text just read, so it is identical run to run.
    reocr: dict[int, tuple[tuple[str, ...], tuple[float, ...]]] = {}
    n_footer_declined = 0
    if ocr_by_page and opt.footer_reocr:
        from ..identify.bates import zone_has_candidate

        retry = [i for i in sorted(ocr_by_page)
                 if not ocr_by_page[i].failed
                 and not zone_has_candidate(ocr_by_page[i].text)]
        if retry:
            try:
                reocr = _reocr_bands(raw, retry)
                n_footer_declined = len(retry) - len(reocr)
            except Exception as exc:
                notes.append(f"{M_OCR_FOOTER}: {exc}")

    pages: list[PageRecord] = []
    n_ocr_failed = 0
    n_ocr_blank = 0
    n_footer_recovered = 0
    n_mixed = 0
    n_region_failed = 0
    n_region_blank = 0
    for i in range(n):  # strictly by index — never by OCR completion order
        text, kind, confs = native[i], PageKind.NATIVE, None
        page_notes: tuple[str, ...] = ()
        image_span: tuple[int, int] | None = None
        got = ocr_by_page.get(i)
        if got is not None:
            if got.failed:
                n_ocr_failed += 1
                page_notes = (f"{M_OCR_PAGE} to rasterize or read",)
            elif got.text.strip():
                text, kind, confs = got.text, PageKind.OCR, list(got.confs)
            else:
                kind = PageKind.OCR  # routed to OCR, recovered nothing
                confs = list(got.confs)
                n_ocr_blank += 1
            # The recovered tokens are appended to the TAIL, which is where the
            # Bates zone looks; ``BatesZone.tail_lines`` carries the matching
            # margin so the block can never evict a line the ordinary pass put
            # there. Their confidences join the page's, because they are text
            # on the page now and §4 Stage 2's threshold is measured over the
            # text the page actually carries.
            extra, extra_confs = reocr.get(i, ((), ()))
            if extra:
                have = {ln.strip() for ln in text.split("\n")}
                keep = [t for t in extra if t not in have]
                if keep:
                    text = (text.rstrip("\n") + "\n" if text.strip() else "") \
                        + "\n".join(keep)
                    confs = (confs or []) + [c for t, c in zip(extra, extra_confs)
                                             if t in keep]
                    n_footer_recovered += 1
        # --- A-24: merge the image regions this page's text layer did not
        # account for. Placed after the text layer's opening lines, NOT appended
        # after it: appending moved the page's own Bates stamp out of the zone
        # and could leave a foreign stamp from the image as the only one there.
        # The measurement is on :func:`_merge_image_text`.
        region = region_by_page.get(i)
        if region is not None:
            if region.text.strip():
                text, image_span = _merge_image_lines(text, region.text)
                kind = PageKind.MIXED
                confs = (confs or []) + list(region.confs)
                n_mixed += 1
            elif region.failed:
                # We knew there was image content, tried to read it, and could
                # not. That is an evidence gap and it says so on the page.
                page_notes = page_notes + (M_IMAGE_UNREAD,)
                n_region_failed += 1
            else:
                # Read, and there was no text in it. That is a COMPLETE answer,
                # not a gap — a site photograph carries no words — so it gets a
                # counted disclosure rather than an evidence marker. Marking it
                # would flag every genuine photograph in the production as lost
                # evidence, and a warning that fires on the normal case teaches
                # an operator to stop reading warnings.
                n_region_blank += 1
        if i == 0 and photo:
            # The block describes the whole file, so it rides on page 1. When
            # the page also yielded read text the page stays OCR/NATIVE and
            # keeps its confidences — PHOTO is for a page whose only content
            # IS the deterministic block.
            has_read_text = bool(text.strip())
            if has_read_text and image_span is not None:
                # D-49: the block adds lines above the image lines, so their
                # position moves down by exactly that many. Normalized first,
                # because make_page refuses a span over text normalization could
                # still change. Only on a MIXED page, so every other page 1
                # keeps its bytes.
                photo_line = normalize(photo)
                text = photo_line + "\n" + text
                image_span = (image_span[0] + len(photo_line.split("\n")),
                              image_span[1])
            else:
                text = (photo + "\n" + text) if has_read_text else photo
            if not has_read_text:
                kind, confs = PageKind.PHOTO, None
                # The relabelling loses the fact that this page WAS routed to
                # OCR and recovered nothing — ``make_page`` only adds that note
                # for a page handed to it as OCR. Disclosed here instead, so the
                # record still says what happened and :func:`ocr_yield` can
                # count the attempt. Without it, a corpus of photo-only PDFs run
                # against a dead engine reports zero attempts and no alarm.
                if got is not None and not got.failed:
                    page_notes = page_notes + (M_OCR_BLANK,)
        pages.append(make_page(i + 1, text, kind, confidences=confs,
                               conf_threshold=opt.conf_threshold, notes=page_notes,
                               image_line_span=image_span))
    if n_ocr_failed:
        notes.append(f"{n_ocr_failed} page(s) could not be OCR'd; kept as empty pages")
    if n_ocr_blank:
        notes.append(f"{n_ocr_blank} page(s) routed to OCR recovered no text "
                     "(blank page, or nothing the engine could read)")
    if n_mixed:
        notes.append(f"{n_mixed} page(s) carried image content beside their own "
                     f"text layer; the image regions were read separately and "
                     f"placed after the text layer's opening lines, and are never "
                     f"read for Bates stamps (page kind 'mixed')")
    if n_region_failed:
        notes.append(f"{M_IMAGE_UNREAD}: {n_region_failed} page(s) carry image "
                     f"content that could not be rasterized or read")
    if n_region_blank:
        notes.append(f"{n_region_blank} page(s) carried image content that was "
                     f"read and contained no text")
    if n_footer_recovered:
        # Disclosed, like every other bound in this module: an operator can see
        # how much of the production's numbering came from the second pass
        # rather than from the page's ordinary reading.
        notes.append(f"{n_footer_recovered} page(s) had a stamp-shaped token "
                     f"recovered by the targeted footer re-OCR "
                     f"({FOOTER_REOCR_DPI} dpi, "
                     f"{FOOTER_REOCR_BAND_PT / 72:.2f}in band, at most "
                     f"{FOOTER_BLOCK_MAX_LINES} token(s) per page)")
    if n_footer_declined:
        # The calibration bound, disclosed the same way every other bound in
        # this module is: neither band read a stamp on the pages it probed, so
        # the rest of the document was not re-read. Those pages are misses, and
        # an operator can see that they were never looked at rather than looked
        # at and found wanting.
        notes.append(f"{n_footer_declined} page(s) were not re-read by the "
                     f"targeted footer re-OCR: neither the footer nor the "
                     f"header band produced a stamp on the first "
                     f"{_FOOTER_PROBE_PAGES} page(s) probed in this document")
    return pages, notes


##############################################################################
# DOCX — read at the XML level (the Word fidelity package)
##############################################################################
#
# python-docx's own ``Paragraph.text`` and ``iter_inner_content`` walk the
# EDITING model, which is exactly what silently drops content controls,
# tracked insertions and field results — they show what Word would display
# with track-changes-off preview and content controls "flattened", neither
# of which is a promise this codebase can rely on part-by-part. So
# python-docx is used here for one thing only, confirming the package really
# is a Word document, and every character that can reach the page text is
# read straight off the part XML with lxml instead, every relationship
# resolved by :func:`_opc_target`. What follows is the Word spec
# (``docs/design/word_fidelity_spec.md``), parts 1-9, in one reader that
# serves the body, every table cell, every text box, every header and
# footer, and every footnote, endnote and comment — one walker, so a fix to
# how deleted text is skipped cannot be made in only three of the four
# places that needed it.

_DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    # Word spec part 9: the Office 2016+ chart, whose graphicData carries
    # this namespace verbatim rather than a URI ending "/chart".
    "cx": "http://schemas.microsoft.com/office/drawing/2014/chartex",
    # D-50: the embedded object, o:OLEObject.
    "o": "urn:schemas-microsoft-com:office:office",
}

_WORD_GROUP_URIS = (
    "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
)
"""graphicData URIs of a group shape and a drawing canvas: one graphic that
holds other graphics, each of which Word spec part 9 counts on its own."""

WORD_LAYOUT_NOTE = (
    "Word's page layout is not reproduced, including any rendered page "
    "breaks; this document is emitted as one synthetic page"
)
"""Word spec part 7: replaces the old ``"DOCX carries no page boundaries"`` claim,
which was false for the 43-of-53-corpus files that DO carry a rendered page
break — Word simply never re-lays out at extraction time to find out where
one would fall. This wording holds for every DOCX rather than describing one
class of it, so it never needs to know whether THIS file has a break. Not an
evidence marker: it is a description of the page model, not a gap in it."""


def _docx_local(el) -> str:
    """``el``'s tag with its namespace stripped.

    A plain string split rather than ``lxml.etree.QName`` because this runs
    once per element of every text-bearing part: Clark notation is always
    ``"{uri}local"``, so a ``rsplit`` is exact and does not pay for a QName
    object on the hottest path in this reader. Local names are unique enough
    within one OOXML part that comparing them as bare strings reads closer to
    the spec's own wording ("w:sdt", "w:txbxContent") than a wall of
    namespace-qualified constants would.

    Named for the Word reader rather than ``_local``: a second module-level
    ``_local`` elsewhere in this file would silently replace this one, and
    every Word file would then fail to read.
    """
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _on_off(el) -> bool:
    """A WordprocessingML on/off property (``w:titlePg``,
    ``w:evenAndOddHeaders``): present means on, unless its ``w:val`` is
    ``0``, ``false`` or ``off``."""
    val = el.get(_wq("val"))
    return val is None or val.strip().lower() not in ("0", "false", "off")


def _opc_target(source_part: str, target: str) -> str:
    """A relationship ``Target`` as a zip member name, by the Open Packaging
    Conventions: a leading ``/`` makes it package-absolute; anything else is
    relative to the folder of the part whose relationships name it (``""``
    for the package's own ``_rels/.rels``).

    The one resolver for every relationship this module follows — the main
    document part, headers, footers and embedded objects — so no two call
    sites can read one target two ways. They did: ``"/word/header1.xml"``
    joined onto ``word/`` matched no part, and the header vanished while
    python-docx, which resolves it correctly, had opened the file.
    """
    import posixpath

    if target.startswith("/"):
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        folder = source_part.rpartition("/")[0]
        resolved = posixpath.normpath(posixpath.join(folder, target))
    return "" if resolved == "." else resolved


_WORD_MAIN_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
)


def _opc_main_part(zf) -> tuple[str, str] | None:
    """``(part name, content type)`` of the part an Office package's own
    ``_rels/.rels`` names as its main document, or ``None`` when the zip is
    not an Office package. The name is never assumed: ``word/document2.xml``
    is as valid a main part as ``word/document.xml``."""
    from lxml import etree

    names = set(zf.namelist())
    if "_rels/.rels" not in names or "[Content_Types].xml" not in names:
        return None
    try:
        rels = etree.fromstring(zf.read("_rels/.rels"))
        types = etree.fromstring(zf.read("[Content_Types].xml"))
    except Exception:
        return None
    for rel in rels:
        if ((rel.get("Type") or "").rsplit("/", 1)[-1] != "officeDocument"
                or rel.get("TargetMode") == "External"):
            continue
        part = _opc_target("", rel.get("Target") or "")
        if part not in names:
            continue
        ext = part.rpartition(".")[2].lower()
        override = default = ""
        for el in types:
            local = _docx_local(el)
            if (local == "Override"
                    and (el.get("PartName") or "").lstrip("/").lower() == part.lower()):
                override = el.get("ContentType") or ""
            elif local == "Default" and (el.get("Extension") or "").lower() == ext:
                default = el.get("ContentType") or ""
        return part, override or default
    return None


def docx_main_part(zf) -> str | None:
    """The main document part of a Word package, or ``None`` when ``zf`` is
    not one (a workbook, a presentation, a plain archive)."""
    main = _opc_main_part(zf)
    return main[0] if main and main[1] in _WORD_MAIN_CONTENT_TYPES else None


def is_word_package(raw: bytes) -> bool:
    """True when ``raw`` is a Word package, whatever name it arrived under.

    D-50 recovers the documents embedded in a Word file, and a production
    delivers Word files named ``.pdf``, attached to an email as
    ``report.pdf``, or zipped under another name. The content-sniff recovery
    already reads such a file as Word, so whether its embedded documents are
    expanded must be decided by the same bytes, never by the name: a file is
    sent to :func:`expand_docx_embeddings` exactly when this is true, and PDF
    bytes named ``.docx`` never are.
    """
    if sniff_kind(raw) != "zip":
        return False
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return docx_main_part(zf) is not None
    except Exception:
        return False


def _docx_rels(zf, part_name: str) -> dict[str, tuple[str, bool]]:
    """``{r:id: (target, is_external)}`` for the ``.rels`` part belonging to
    ``part_name`` (e.g. ``"word/header2.xml"`` -> ``"word/_rels/header2.xml.rels"``).
    The target is returned as written; :func:`_opc_target` resolves it.

    Absent is ordinary, not corrupt: a part with no relationships of its own
    (a footnotes part with no hyperlinks, say) simply has no ``.rels``
    member.
    """
    from lxml import etree

    dirname, _sep, base = part_name.rpartition("/")
    rels_path = f"{dirname}/_rels/{base}.rels" if dirname else f"_rels/{base}.rels"
    if rels_path not in zf.namelist():
        return {}
    root = etree.fromstring(zf.read(rels_path))
    out: dict[str, tuple[str, bool]] = {}
    for rel in root:
        rid, target = rel.get("Id"), rel.get("Target")
        if rid and target:
            out[rid] = (target, rel.get("TargetMode") == "External")
    return out


class _DocxWalker:
    """Reads one DOCX package's text-bearing parts (Word spec parts 1-9).

    One instance per file, holding the state every part's walk needs: the
    main document part and its relationships, whether the settings part asks
    for even-page headers/footers, the final section's page area (for the
    large-picture test), the disclosure counters, and which physical
    header/footer parts have already been emitted (a part can be referenced
    by more than one section and must still be read only once).
    """

    _PROPERTY_ELEMENTS = frozenset({"pPr", "rPr", "sdtPr", "sdtEndPr"})
    """Property containers hold no text. A tab-stop list (``w:pPr/w:tabs/
    w:tab``) is not a tab character, and a deleted paragraph mark
    (``w:pPr/w:rPr/w:del``) is not deleted text; walking into either put
    characters on the page the document does not contain."""

    def __init__(self, zf, main_part: str):
        self.zf = zf
        self.names = set(zf.namelist())
        self.main_part = main_part
        self.folder = main_part.rpartition("/")[0]
        self.rels = _docx_rels(zf, main_part)
        self.even_and_odd = self._settings_have_even_odd()
        self.page_area_emu2: float | None = None
        self.counters = {"altchunk": 0, "chart": 0, "smartart": 0,
                         "picture": 0, "tracked_del": 0, "picture_unmeasured": 0,
                         "symbol": 0, "subdoc": 0, "hidden_hdr_ftr": 0}
        self._seen_header_parts: set[str] = set()
        self._seen_footer_parts: set[str] = set()

    def _beside_main(self, base: str) -> str:
        """A part named ``base`` in the main document part's own folder."""
        return f"{self.folder}/{base}" if self.folder else base

    def _settings_have_even_odd(self) -> bool:
        part = self._beside_main("settings.xml")
        if part not in self.names:
            return False
        from lxml import etree

        root = etree.fromstring(self.zf.read(part))
        return any(_docx_local(el) == "evenAndOddHeaders" and _on_off(el)
                   for el in root)

    # -- Word spec part 2: one paragraph's inline text, plus any text boxes it
    #    anchors (Word spec part 3), collected but not inlined ------------------

    def _inline_text(self, p_el, part_rels: dict
                     ) -> tuple[str, list[list[str]]]:
        parts: list[str] = []
        boxes: list[list[str]] = []
        # One entry per complex field open in this paragraph, True while its
        # CODE runs (between fldChar begin and separate). A field nested in
        # another field's code (IF { MERGEFIELD } ...) carries its own cached
        # result there, and that result is part of the outer code, not text.
        field_code: list[bool] = []

        def walk(node) -> None:
            for child in node:
                name = _docx_local(child)
                if name in self._PROPERTY_ELEMENTS:
                    continue
                if name in ("del", "moveFrom"):
                    # The default view: insertions read as ordinary text
                    # (below, by falling through to the generic recursion —
                    # w:ins is never special-cased), deletions and
                    # move-froms omitted and counted for disclosure.
                    self.counters["tracked_del"] += 1
                    continue
                if name == "txbxContent":
                    # Walked as BLOCK content and returned to the caller to
                    # emit right after this paragraph's own line (Word spec
                    # part 3) — never inlined into the paragraph's own text.
                    # Its own drawings/altChunks still count (Word spec part
                    # 9): nothing about being inside a text box makes a
                    # chart or a large picture read.
                    boxes.append(self._block_lines(list(child), part_rels))
                    continue
                if name == "AlternateContent":
                    # Read Choice only when present, never both: the text
                    # box in this fixture stores its content verbatim in
                    # BOTH mc:Choice and mc:Fallback, and reading both would
                    # double it.
                    walk(_alternate_choice(child))
                    continue
                if name == "fldChar":
                    kind = child.get(_wq("fldCharType"))
                    if kind == "begin":
                        field_code.append(True)
                    elif kind == "separate" and field_code:
                        field_code[-1] = False
                    elif kind == "end" and field_code:
                        field_code.pop()
                    continue
                if any(field_code) and name in (
                        "t", "tab", "ptab", "br", "cr", "noBreakHyphen", "sym"):
                    continue
                if name == "t":
                    if child.text:
                        parts.append(child.text)
                    continue
                if name in ("tab", "ptab"):
                    # A positional tab (w:ptab: Word's own left/center/right
                    # header layout) separates words exactly as a tab does.
                    parts.append("\t")
                    continue
                if name in ("br", "cr"):
                    parts.append("\n")
                    continue
                if name == "noBreakHyphen":
                    parts.append("-")
                    continue
                if name == "sym":
                    self._symbol(child, parts)
                    continue
                if name in ("instrText", "delText"):
                    continue  # field code / deleted text: excluded by name
                if name == "ruby":
                    # A phonetic guide follows its base text in brackets,
                    # rather than running into it.
                    base = next((c for c in child if _docx_local(c) == "rubyBase"), None)
                    guide = next((c for c in child if _docx_local(c) == "rt"), None)
                    if base is not None:
                        walk(base)
                    if guide is not None:
                        before = len(parts)
                        walk(guide)
                        text = "".join(parts[before:])
                        del parts[before:]
                        if text:
                            parts.append(f"({text})")
                    continue
                if name == "subDoc":
                    # A master document's link to a sub-document stored
                    # elsewhere: its text is not in this package.
                    self.counters["subdoc"] += 1
                    continue
                if name == "hyperlink":
                    before = len(parts)
                    walk(child)
                    display = "".join(parts[before:])
                    rel = part_rels.get(child.get(_rq("id")))
                    # An internal w:anchor link has no r:id and so no
                    # relationship: it adds nothing beyond the display text
                    # already appended above. The target follows "when the
                    # two differ" (Word spec part 2), never merely when the
                    # display text does not contain it.
                    if rel and rel[1] and rel[0].strip() != display.strip():
                        parts.append(f" <{rel[0]}>")
                    continue
                if name == "drawing":
                    # Word spec part 9: counted wherever this walker reaches
                    # it — body, header, footer, footnote, endnote, comment
                    # or text box — not only the body.
                    self._note_drawing(child)
                    walk(child)  # a text box's txbxContent can be nested here
                    continue
                walk(child)  # w:r, w:ins, w:sdt, w:smartTag, ... — transparent

        walk(p_el)
        return "".join(parts), boxes

    def _symbol(self, sym_el, parts: list[str]) -> None:
        """``w:sym``: one character, given as a hex code in a named font. A
        Unicode character is text. A code in the private-use area belongs to
        a symbol font (Wingdings' tick is ``F0FC``) and means nothing as a
        character, so it is counted for disclosure instead."""
        try:
            code = int(sym_el.get(_wq("char")) or "", 16)
        except ValueError:
            code = -1
        if (0 < code < 0x110000 and not 0xD800 <= code <= 0xDFFF
                and not 0xE000 <= code <= 0xF8FF):
            parts.append(chr(code))
        else:
            self.counters["symbol"] += 1

    # -- Word spec part 9: chart / SmartArt / large-picture disclosure ----------

    def _note_drawing(self, drawing_el) -> None:
        """Classify one ``w:drawing`` for disclosure, wherever it was found.

        This used to be called only for body drawings, on the reasoning that
        a header/footer logo should not count — but that same gate silently
        ate a chart or a large picture placed in a header, footer, footnote,
        endnote, comment or text box. Every caller now reaches this
        regardless of where the drawing lives.
        """
        for gd, extent_host in self._own_graphicdata(list(drawing_el)):
            extent = (next(extent_host.iter(f"{{{_DOCX_NS['wp']}}}extent"), None)
                      if extent_host is not None else None)
            self._classify_graphic(gd, _emu_extent(extent))

    def _classify_graphic(self, gd, extent: tuple[float, float] | None) -> None:
        uri = gd.get("uri", "")
        # A classic chart's graphicData URI ends "/chart"; an Office 2016+
        # chartEx (waterfall, funnel, ...) uses the chartex namespace instead.
        if uri.endswith("/chart") or uri == _DOCX_NS["cx"]:
            self.counters["chart"] += 1
        elif uri == _DOCX_NS["dgm"]:
            self.counters["smartart"] += 1
        elif uri.endswith("/picture"):
            self._note_picture(extent)
        elif uri in _WORD_GROUP_URIS:
            self._note_group(gd)

    def _note_group(self, el) -> None:
        """The charts, SmartArt and pictures inside a group shape or a
        drawing canvas. The group's own URI is neither, so what it holds was
        never looked at. A picture is measured by its own ``a:xfrm`` extent.
        A text box inside the group is not looked at here: its content is
        block text the paragraph walk reads, drawings and all."""
        for child in el:
            name = _docx_local(child)
            if name in ("drawing", "txbxContent"):
                continue
            if name == "AlternateContent":
                self._note_group(_alternate_choice(child))
                continue
            if name == "graphicData" and child.tag == f"{{{_DOCX_NS['a']}}}graphicData":
                if child.get("uri", "").endswith("/picture"):
                    self._note_picture(_xfrm_extent(
                        next(child.iter(f"{{{_DOCX_NS['pic']}}}pic"), None)))
                else:
                    self._classify_graphic(child, None)
                continue
            if name == "pic":
                self._note_picture(_xfrm_extent(child))
                continue
            self._note_group(child)

    def _own_graphicdata(self, elements):
        """Every ``a:graphicData`` that belongs to THIS drawing, paired with
        the nearest ``wp:inline``/``wp:anchor`` ancestor that carries its
        ``wp:extent`` — never one belonging to a NESTED ``w:drawing`` (a text
        box's own contents), which the walk visits, and counts, on its own
        through ``_block_lines`` -> ``_inline_text`` -> ``_note_drawing``
        again.

        Sweeping every descendant graphicData unconditionally counted a
        chart or picture placed inside a text box twice: once through the
        outer drawing's sweep and again when the walk reached the text box's
        own nested ``<w:drawing>``. Stopping at a nested ``drawing`` tag is
        what makes each real construct counted exactly once, at any nesting
        depth (a text box inside a text box included, since each drawing gets
        its own bounded call the moment the walk actually reaches it).

        ``mc:AlternateContent`` is resolved to ``Choice`` only, matching
        ``_inline_text``'s own rule — a chartEx graphic's picture Fallback
        must never be counted as a second, unread picture.
        """
        for el in elements:
            name = _docx_local(el)
            if name == "drawing":
                continue  # a nested drawing is counted on its own
            if name in ("inline", "anchor"):
                yield from self._own_graphicdata_in(el, el)
                continue
            if name == "AlternateContent":
                yield from self._own_graphicdata(list(_alternate_choice(el)))
                continue
            yield from self._own_graphicdata(list(el))

    def _own_graphicdata_in(self, elements, extent_host):
        """Descend inside one ``wp:inline``/``wp:anchor`` for its OWN
        ``a:graphicData`` (there is exactly one), still stopping at a nested
        ``w:drawing`` and resolving ``AlternateContent`` to ``Choice`` only.
        Split from :meth:`_own_graphicdata` only so ``extent_host`` — the
        ``wp:inline``/``wp:anchor`` whose ``wp:extent`` measures THIS
        graphic — is fixed once and threaded through, rather than
        recomputed or lost across the AlternateContent recursion.
        """
        for el in elements:
            name = _docx_local(el)
            if name == "drawing":
                continue
            if name == "AlternateContent":
                yield from self._own_graphicdata_in(
                    list(_alternate_choice(el)), extent_host)
                continue
            if name == "graphicData" and el.tag == f"{{{_DOCX_NS['a']}}}graphicData":
                yield el, extent_host
                continue
            yield from self._own_graphicdata_in(list(el), extent_host)

    def _note_picture(self, extent: tuple[float, float] | None) -> None:
        if not self.page_area_emu2 or extent is None:
            # The page has no usable size, or the picture no usable extent:
            # its share of the page cannot be measured at all. Counted
            # rather than silently skipped, and disclosed separately from
            # the ordinary "picture(s)" count, which DOES know the share.
            self.counters["picture_unmeasured"] += 1
            return
        cx, cy = extent
        if (cx * cy) / self.page_area_emu2 >= PHOTO_MIN_IMAGE_AREA_SHARE:
            self.counters["picture"] += 1

    # -- Word spec part 1 / part 4: the one block walker for body, cells, text
    #    boxes, headers, footers, footnotes, endnotes and comments --------

    def _block_lines(self, elements, part_rels: dict) -> list[str]:
        lines: list[str] = []
        for el in elements:
            name = _docx_local(el)
            if name == "p":
                text, boxes = self._inline_text(el, part_rels)
                lines.append(text)
                for box in boxes:
                    lines.extend(box)
            elif name == "tbl":
                lines.extend(self._table_lines(el, part_rels))
            elif name == "sdt":
                content = next((c for c in el if _docx_local(c) == "sdtContent"), None)
                if content is not None:
                    lines.extend(self._block_lines(list(content), part_rels))
            elif name == "customXml":
                lines.extend(self._block_lines(list(el), part_rels))
            elif name == "AlternateContent":
                # Block-level alternate content, resolved as the inline walk
                # resolves it: Choice when present, never both.
                lines.extend(self._block_lines(list(_alternate_choice(el)), part_rels))
            elif name == "altChunk":
                # Word spec part 9: counted wherever it is found, same as a
                # drawing — an altChunk in a header/footer/footnote/endnote
                # is exactly as unread as one in the body.
                self.counters["altchunk"] += 1
            # w:sectPr and anything else (bookmarks, proofErr, ...): ignored.
        return lines

    def _wrapped(self, elements, want: str):
        """The ``want`` children (``tr`` of a table, ``tc`` of a row), looking
        through the content controls and custom XML that can wrap them (Word
        spec part 1). A repeating-section content control wraps whole rows,
        and a row or cell reached only through one lost its text."""
        for el in elements:
            name = _docx_local(el)
            if name == want:
                yield el
            elif name == "sdt":
                content = next((c for c in el if _docx_local(c) == "sdtContent"), None)
                if content is not None:
                    yield from self._wrapped(content, want)
            elif name == "customXml":
                yield from self._wrapped(el, want)

    def _table_lines(self, tbl_el, part_rels: dict) -> list[str]:
        rows = []
        for tr in self._wrapped(tbl_el, "tr"):
            cells = [self._cell_text(tc, part_rels) for tc in self._wrapped(tr, "tc")]
            rows.append("\t".join(cells))  # Word spec part 4: row = cells joined by tab
        return rows

    def _cell_text(self, tc_el, part_rels: dict) -> str:
        tc_pr = next((c for c in tc_el if _docx_local(c) == "tcPr"), None)
        if tc_pr is not None:
            vmerge = next((c for c in tc_pr if _docx_local(c) == "vMerge"), None)
            if vmerge is not None and vmerge.get(_wq("val")) != "restart":
                # Word spec part 4: a vMerge continuation cell is empty, never a
                # copy of the cell it continues — whatever its own paragraphs
                # literally hold.
                return ""
        content = [c for c in tc_el if _docx_local(c) != "tcPr"]
        # Word spec part 4: a cell's lines joined by newline; a nested table (the
        # FERRET case) is walked in order with the cell's own paragraphs by
        # the same block walker that reads the body.
        return "\n".join(self._block_lines(content, part_rels))

    # -- Word spec part 5: headers and footers -----------------------------------

    def _rel_part(self, rid: str | None) -> str | None:
        rel = self.rels.get(rid) if rid else None
        if rel is None or rel[1]:
            return None
        return _opc_target(self.main_part, rel[0])

    def _section_refs(self, sect_pr) -> dict[str, tuple[list[str], list[str]]]:
        """For ``headerReference`` and ``footerReference``: this section's
        displayed PART NAMES in priority order — first-page (only when
        ``w:titlePg`` is on for this section), then default, then even (only
        when the settings part turns ``w:evenAndOddHeaders`` on) — and the
        parts it references under a type it does not display."""
        title_pg = any(_docx_local(c) == "titlePg" and _on_off(c) for c in sect_pr)
        shown = (["first"] if title_pg else []) + ["default"] + \
                (["even"] if self.even_and_odd else [])
        out: dict[str, tuple[list[str], list[str]]] = {}
        for kind in ("headerReference", "footerReference"):
            refs = {c.get(_wq("type")): c.get(_rq("id"))
                    for c in sect_pr if _docx_local(c) == kind}
            displayed = [p for p in (self._rel_part(refs.get(t)) for t in shown) if p]
            hidden = [p for p in (self._rel_part(rid) for t, rid in refs.items()
                                  if t not in shown) if p]
            out[kind] = (displayed, hidden)
        return out

    def read_headers_and_footers(self, body_el) -> tuple[list[str], list[str]]:
        header_lines: list[str] = []
        footer_lines: list[str] = []
        hidden: list[str] = []
        for sect_pr in body_el.iter(_wq("sectPr")):
            refs = self._section_refs(sect_pr)
            for part in refs["headerReference"][0]:
                if part in self._seen_header_parts or part not in self.names:
                    continue
                self._seen_header_parts.add(part)
                header_lines.extend(self._read_hdr_ftr_part(part))
            for part in refs["footerReference"][0]:
                if part in self._seen_footer_parts or part not in self.names:
                    continue
                self._seen_footer_parts.add(part)
                footer_lines.extend(self._read_hdr_ftr_part(part))
            hidden.extend(refs["headerReference"][1] + refs["footerReference"][1])
        # A part only a hidden type references, with text in it, is named in
        # a note (Word keeps a first-page header part when "Different first
        # page" is unticked). Sorted, so the count never depends on set order.
        unread = sorted({p for p in hidden if p in self.names}
                        - self._seen_header_parts - self._seen_footer_parts)
        self.counters["hidden_hdr_ftr"] = sum(1 for p in unread if self._has_text(p))
        return header_lines, footer_lines

    def _has_text(self, part_name: str) -> bool:
        from lxml import etree

        try:
            root = etree.fromstring(self.zf.read(part_name))
        except Exception:
            return True  # unreadable: whatever it holds was not read
        return any(t.text and t.text.strip() for t in root.iter(_wq("t")))

    def _read_hdr_ftr_part(self, part_name: str) -> list[str]:
        from lxml import etree

        root = etree.fromstring(self.zf.read(part_name))
        part_rels = _docx_rels(self.zf, part_name)
        return self._block_lines(list(root), part_rels)

    # -- Word spec part 6: footnotes, endnotes, comments -------------------------

    def read_notes(self) -> list[str]:
        lines = self._read_note_part(self._beside_main("footnotes.xml"), "footnote")
        lines += self._read_note_part(self._beside_main("endnotes.xml"), "endnote")
        lines += self._read_comments()
        return lines

    def _read_note_part(self, part_name: str, tag: str) -> list[str]:
        if part_name not in self.names:
            return []
        from lxml import etree

        root = etree.fromstring(self.zf.read(part_name))
        part_rels = _docx_rels(self.zf, part_name)
        out = []
        for note in (c for c in root if _docx_local(c) == tag):
            if note.get(_wq("type")) not in (None, "normal"):
                continue  # separator / continuationSeparator: not real notes
            text = "\n".join(self._block_lines(list(note), part_rels))
            out.append(f"[{tag} {note.get(_wq('id'))}] {text}")
        return out

    def _read_comments(self) -> list[str]:
        part_name = self._beside_main("comments.xml")
        if part_name not in self.names:
            return []
        from lxml import etree

        root = etree.fromstring(self.zf.read(part_name))
        part_rels = _docx_rels(self.zf, part_name)
        out = []
        for c in (c for c in root if _docx_local(c) == "comment"):
            # w:date is deliberately never read: it is metadata about the
            # review, not evidence the document asserts, and rendering it
            # would let a comment's timestamp masquerade as a page's own
            # first date.
            text = "\n".join(self._block_lines(list(c), part_rels))
            out.append(f"[comment by {c.get(_wq('author')) or ''}] {text}")
        return out

    # -- Word spec part 9: the disclosure notes themselves -----------------------

    def disclosure_notes(self) -> list[str]:
        c = self.counters
        notes = []
        if c["altchunk"]:
            notes.append(f"{M_WORD_UNREAD}: {c['altchunk']} altChunk(s)")
        if c["chart"]:
            notes.append(f"{M_WORD_UNREAD}: {c['chart']} chart(s)")
        if c["smartart"]:
            notes.append(f"{M_WORD_UNREAD}: {c['smartart']} SmartArt drawing(s)")
        if c["picture"]:
            notes.append(
                f"{M_WORD_UNREAD}: {c['picture']} picture(s), each covering "
                f"at least {PHOTO_MIN_IMAGE_AREA_SHARE:.0%} of the page")
        if c["picture_unmeasured"]:
            # The PDF path's own marker when image geometry cannot be
            # measured (M_IMAGE_UNREAD): the same fact for a DOCX, whose
            # picture's share of the page is unknown rather than
            # known-and-small.
            notes.append(
                f"{M_IMAGE_UNREAD}: {c['picture_unmeasured']} picture(s) "
                "could not be measured against the page — the final section "
                "carries no usable page size, or the picture no usable extent")
        if c["symbol"]:
            notes.append(f"{M_WORD_UNREAD}: {c['symbol']} symbol character(s) "
                         "from a symbol font, which have no text equivalent")
        if c["subdoc"]:
            notes.append(f"{M_WORD_UNREAD}: {c['subdoc']} sub-document link(s); "
                         "a sub-document's text is stored outside this file")
        if c["hidden_hdr_ftr"]:
            notes.append(
                f"{M_WORD_UNREAD}: {c['hidden_hdr_ftr']} header/footer part(s) "
                "that no section displays (a first-page part without w:titlePg, "
                "or an even-page part without w:evenAndOddHeaders)")
        if c["tracked_del"]:
            notes.append(f"{M_WORD_TRACKED_DELETION}: {c['tracked_del']}")
        return notes


def _alternate_choice(alternate_content):
    """``mc:AlternateContent`` resolved to its ``mc:Choice`` when present, else
    its ``mc:Fallback``, else nothing — never both."""
    choice = next((c for c in alternate_content if _docx_local(c) == "Choice"), None)
    if choice is not None:
        return choice
    fallback = next((c for c in alternate_content if _docx_local(c) == "Fallback"), None)
    return fallback if fallback is not None else ()


def _emu_extent(el) -> tuple[float, float] | None:
    """``(cx, cy)`` in EMU from an extent element, or ``None`` when it is
    absent, unparsable or negative: an extent that cannot be read is a
    picture that cannot be measured, not a small one."""
    if el is None:
        return None
    try:
        cx, cy = float(el.get("cx")), float(el.get("cy"))
    except (TypeError, ValueError):
        return None
    return (cx, cy) if cx >= 0 and cy >= 0 else None


def _xfrm_extent(el) -> tuple[float, float] | None:
    """The extent of a shape inside a group: its ``a:xfrm``'s ``a:ext``."""
    if el is None:
        return None
    xfrm = next(el.iter(f"{{{_DOCX_NS['a']}}}xfrm"), None)
    if xfrm is None:
        return None
    return _emu_extent(next((c for c in xfrm if _docx_local(c) == "ext"), None))


def _wq(local: str) -> str:
    return f"{{{_DOCX_NS['w']}}}{local}"


def _rq(local: str) -> str:
    return f"{{{_DOCX_NS['r']}}}{local}"


def _extract_docx(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """The Word fidelity package: every character in the document's
    text-bearing parts reaches the page text, or is named in a note carrying
    an evidence marker.

    python-docx opens the package only to confirm it really is one --
    ``Document(...)`` raises the same way it always did on a corrupt or
    non-OOXML file. Everything that becomes page text comes from
    :class:`_DocxWalker` reading the part XML directly; see its docstring
    and the Word spec (parts 1-9) for why.
    """
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Word support requires 'python-docx'.") from exc
    try:
        docx.Document(io.BytesIO(raw))
    except Exception as exc:
        raise ExtractionError(f"Could not read Word document: {exc}") from exc

    try:
        import zipfile

        from lxml import etree

        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            main_part = docx_main_part(zf)
            if main_part is None:
                raise ExtractionError(
                    "Could not read Word document: the package names no Word "
                    "main document part")
            doc_root = etree.fromstring(zf.read(main_part))
            body_el = next(c for c in doc_root if _docx_local(c) == "body")
            walker = _DocxWalker(zf, main_part)

            # Word spec part 9: page area for the large-picture test, from the
            # BODY's own (i.e. final) sectPr — the section governing the
            # last page Word would have rendered. A section break's earlier
            # sectPr lives inside a paragraph's pPr and is not this one.
            body_sect_pr = next(
                (c for c in reversed(list(body_el)) if _docx_local(c) == "sectPr"), None)
            if body_sect_pr is not None:
                pg_sz = next((c for c in body_sect_pr if _docx_local(c) == "pgSz"), None)
                if pg_sz is not None:
                    try:
                        w = int(pg_sz.get(_wq("w")))
                        h = int(pg_sz.get(_wq("h")))
                    except (TypeError, ValueError):
                        w = h = 0
                    if w > 0 and h > 0:
                        walker.page_area_emu2 = (w * 635.0) * (h * 635.0)
                    # otherwise unmeasurable: _note_picture discloses rather
                    # than guesses (a negative size once made every share
                    # negative, so no picture was ever large)

            body_lines = walker._block_lines(list(body_el), walker.rels)
            header_lines, footer_lines = walker.read_headers_and_footers(body_el)
            note_lines = walker.read_notes()
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Could not read Word document: {exc}") from exc

    # Word spec part 5: headers START the page text, footers END it, with no
    # label lines — the Bates head zone is 3 lines and a label could push a
    # stamp out of it. Notes (part 6) land after the body and before the
    # footer, per the same reasoning applied to where they appear at all.
    all_lines = header_lines + body_lines + note_lines + footer_lines
    notes = [WORD_LAYOUT_NOTE] + walker.disclosure_notes()
    return synthetic_pages(["\n".join(all_lines)], notes=tuple(notes)), notes


def _xlsx_cell(v) -> str:
    """One cell → text. Dates render ISO so the date extractor anchors them."""
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        # Drop a midnight time component so a pure date reads as 'YYYY-MM-DD'.
        return (v.date().isoformat() if v.time() == datetime.time(0, 0)
                else v.isoformat(sep=" "))
    if isinstance(v, datetime.date):
        return v.isoformat()
    return str(v)


def _extract_xlsx(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """Workbook → one synthetic page per worksheet, tab-delimited.

    ``data_only=True`` yields the last-computed cell VALUES (not formulas) and
    ``read_only=True`` streams rows so a large register stays bounded in RAM.
    The row cap is global across the workbook and its truncation is disclosed
    both in the page text and as a document note — a silent cap would be a
    Principle-1 violation dressed as a performance guard.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Excel support requires 'openpyxl'.") from exc
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise ExtractionError(f"Could not read Excel workbook: {exc}") from exc

    blocks: list[str] = []
    notes: list[str] = []
    rows_emitted = 0
    truncated = False
    try:
        for ws in wb.worksheets:
            parts = [f"[sheet: {ws.title}]"]
            for row in ws.iter_rows(values_only=True):
                if rows_emitted >= _XLSX_MAX_ROWS:
                    truncated = True
                    break
                cells = [_xlsx_cell(c) for c in row]
                if not any(cells):
                    continue
                parts.append("\t".join(cells))
                rows_emitted += 1
            if truncated:
                parts.append(f"[... workbook truncated at {_XLSX_MAX_ROWS} rows]")
            blocks.append("\n".join(parts))
            if truncated:
                break
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if truncated:
        notes.append(f"workbook truncated at {_XLSX_MAX_ROWS} rows; "
                     "later sheets were not read")
    notes.append("XLSX has no page boundaries; one synthetic page per worksheet")
    return synthetic_pages(blocks, notes=(notes[-1],)), notes


def _extract_xls(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """Legacy .xls via xlrd — one synthetic page per sheet, same shape as XLSX."""
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Legacy .xls support requires 'xlrd'.") from exc
    try:
        book = xlrd.open_workbook(file_contents=raw)
    except Exception as exc:
        raise ExtractionError(f"Could not read legacy Excel workbook: {exc}") from exc
    blocks: list[str] = []
    rows_emitted = 0
    truncated = False
    notes: list[str] = []
    for sheet in book.sheets():
        parts = [f"[sheet: {sheet.name}]"]
        for r in range(sheet.nrows):
            if rows_emitted >= _XLSX_MAX_ROWS:
                truncated = True
                break
            cells = ["" if c is None else str(c) for c in sheet.row_values(r)]
            if not any(c.strip() for c in cells):
                continue
            parts.append("\t".join(cells))
            rows_emitted += 1
        if truncated:
            parts.append(f"[... workbook truncated at {_XLSX_MAX_ROWS} rows]")
        blocks.append("\n".join(parts))
        if truncated:
            notes.append(f"workbook truncated at {_XLSX_MAX_ROWS} rows")
            break
    notes.append("XLS has no page boundaries; one synthetic page per worksheet")
    return synthetic_pages(blocks, notes=(notes[-1],)), notes


def _extract_csv(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """CSV → tab-delimited text with an optional header marker.

    Delimiter auto-detection tries ``csv.Sniffer`` on the first 8 KB; if that
    fails it probes comma / semicolon / tab and picks whichever yields the most
    columns on average (ties broken in that order). Non-UTF-8 bytes fall back
    to latin-1. An empty CSV yields an empty page rather than raising — a
    zero-byte register is a fact about the production, not a failure.
    """
    import csv

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    else:  # pragma: no cover — latin-1 decodes every byte sequence
        text = raw.decode("latin-1", errors="replace")

    notes = ["CSV has no page boundaries; emitted as one synthetic page"]
    if not text.strip():
        return synthetic_pages([""], notes=(notes[0],)), notes

    sample = text[:8192]
    delim = ","
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except Exception:
        best_avg = 0.0
        for d in (",", ";", "\t"):
            try:
                rows = list(csv.reader(sample.splitlines()[:200], delimiter=d))
                if rows:
                    avg = sum(len(r) for r in rows) / len(rows)
                    if avg > best_avg:
                        best_avg, delim = avg, d
            except Exception:
                pass

    def _looks_like_header(row: list[str]) -> bool:
        if not row or not all(c.strip() for c in row):
            return False
        return not any(re.fullmatch(r"\d+(\.\d+)?", c.strip()) for c in row)

    parts: list[str] = []
    rows_emitted = 0
    truncated = False
    first = True
    try:
        for row in csv.reader(text.splitlines(), delimiter=delim):
            if rows_emitted >= _CSV_MAX_ROWS:
                truncated = True
                break
            if first:
                first = False
                if _looks_like_header(row):
                    parts.append("[header: " + " | ".join(c.strip() for c in row) + "]")
                    continue
            if not any(c.strip() for c in row):
                continue
            parts.append("\t".join(row))
            rows_emitted += 1
    except Exception as exc:
        raise ExtractionError(f"Could not read CSV: {exc}") from exc
    if truncated:
        parts.append(f"[... CSV truncated at {_CSV_MAX_ROWS} rows]")
        notes.append(f"CSV truncated at {_CSV_MAX_ROWS} rows")
    return synthetic_pages(["\n".join(parts)], notes=(notes[0],)), notes


def _extract_pptx(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """PowerPoint → one synthetic page per slide.

    Unlike the MIP 3.9 original, an empty slide still produces a page: slide 7
    of the source is slide 7 of the output, and Principle 1 accounts for it.
    """
    try:
        from pptx import Presentation  # python-pptx
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Presentation support requires 'python-pptx'.") from exc
    try:
        prs = Presentation(io.BytesIO(raw))
    except Exception as exc:
        raise ExtractionError(f"Could not read PowerPoint file: {exc}") from exc

    blocks: list[str] = []
    notes_failures: list[str] = []
    for slide in prs.slides:
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        parts.append(line)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        parts.append("\t".join(cells))
        # has_notes_slide FIRST: python-pptx's notes_slide property *creates* a
        # notes slide when none exists. Reading it unguarded manufactures parts
        # the source file never had, on every slide of every deck —
        # unacceptable in a tool whose claim is that every output is
        # mechanically derived from the source.
        try:
            if slide.has_notes_slide:
                for ph in slide.notes_slide.placeholders:
                    if ph.has_text_frame:
                        note_text = ph.text_frame.text.strip()
                        if note_text:
                            parts.append(f"[notes] {note_text}")
        except Exception as exc:
            # Was a bare ``pass``: a deck whose speaker notes would not read
            # lost them with no record anywhere, so the emitted text silently
            # differed from the source. Counted per slide and disclosed once.
            notes_failures.append(f"slide {len(blocks) + 1}: {exc}"[:120])
        blocks.append("\n".join(parts))
    note = "PPTX slides are emitted as synthetic pages, one per slide"
    out_notes = [note]
    if notes_failures:
        out_notes.append(
            f"{M_SLIDE_NOTES} on {len(notes_failures)} slide(s); their speaker "
            f"notes are absent from this document ({notes_failures[0]})")
    return synthetic_pages(blocks, notes=(note,)), out_notes


_RE_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    import html as _html

    return _html.unescape(_RE_HTML_TAG.sub(" ", s or "")).strip()


def _extract_eml(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """RFC-822 email → headers (From/To/Cc/Subject/Date) + body text.

    The ``Date`` header gets an extra ISO token appended so the date extractor
    anchors the message's own date; the body prefers text/plain, falling back
    to stripped HTML.
    """
    import email
    from email import policy
    from email.utils import parsedate_to_datetime

    notes: list[str] = []
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:
        # Marked FINAL, not transient: the same bytes through the same parser
        # reach the same wall, so a serial re-read cannot improve it. Nothing
        # is deleted — the raw decode below carries every byte the file holds —
        # but the structure the rest of the pipeline reads (headers, the Date
        # anchor, the attachment list) is gone, and that is a gap the run must
        # be able to find mechanically rather than by reading prose.
        note = clip_message(f"{M_EML_PARSE}: decoded as raw text instead "
                            f"({exc}); headers, the Date anchor and the "
                            "attachment list were not recovered", 300)
        return (synthetic_pages([raw.decode("utf-8", errors="replace")], notes=(note,)),
                [note])
    parts: list[str] = []
    for h in ("From", "To", "Cc", "Subject"):
        v = msg.get(h)
        if v:
            parts.append(f"{h}: {v}")
    hdr_date = msg.get("Date")
    if hdr_date:
        try:
            iso = parsedate_to_datetime(hdr_date).date().isoformat()
        except Exception as exc:
            # The date sibling of B-3. The ISO token is the ONLY thing
            # ``dating.detect_dates`` can anchor an email's own date on —
            # RFC-2822 ("Tue, 16 Jul 2024 09:12:00 +0000") is not one of the
            # patterns it reads — so a Date header that will not parse used to
            # cost the message its date with no record at all.
            #
            # Deliberately NOT marked. Nothing DocIQ read is missing: the raw
            # header is still emitted verbatim below, and what is absent is a
            # derived convenience anchor. Marking it would put a malformed
            # sender's header into the run's evidence-loss tally, which is a
            # different and false claim. Disclosed, not marked.
            iso = ""
            notes.append(clip_message(
                f"the message's Date header {hdr_date!r} could not be parsed "
                f"({exc}); it is emitted verbatim but this document is not "
                "date-anchored on it", 300))
        parts.append(f"Date: {hdr_date}" + (f" ({iso})" if iso else ""))
    body = ""
    try:
        bp = msg.get_body(preferencelist=("plain", "html"))
        if bp is not None:
            body = bp.get_content()
            if bp.get_content_subtype() == "html":
                body = _strip_html(body)
    except Exception as exc:
        # Codex review #1, B-3. This was ``body = ""`` under a bare
        # ``except``: a supported email whose body would not decode came back
        # with its headers, no body, no note, no marker and a FULL status. The
        # message text — the whole evidentiary point of an email — was deleted
        # and the run reported success, so the walker's serial-retry registry
        # never saw the file either.
        body = ""
        notes.append(clip_message(
            f"{M_EML_BODY}: the message body could not be decoded ({exc}); "
            "this document carries its headers only", 300))
    if body and body.strip():
        parts.append("")
        parts.append(body.strip())
    page_note = "email carries no page boundaries; emitted as one synthetic page"
    return (synthetic_pages(["\n".join(parts)], notes=(page_note,)),
            [page_note] + notes)


def _extract_msg(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    """Outlook ``.msg`` → headers + body via ``extract-msg``.

    The library needs a real path, so a scratch file is unavoidable. It goes
    under the caller's working folder when one was supplied (§10) and is
    unlinked in a ``finally`` either way.
    """
    try:
        import extract_msg
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Outlook .msg support requires 'extract-msg'.") from exc
    import tempfile

    scratch = opt.scratch_dir
    if scratch is not None:
        scratch.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".msg", delete=False,
            dir=str(scratch) if scratch is not None else None,
        ) as tf:
            tf.write(raw)
            tmp = tf.name
        m = extract_msg.Message(tmp)
        parts: list[str] = []
        for label, val in (("From", getattr(m, "sender", None)),
                           ("To", getattr(m, "to", None)),
                           ("Cc", getattr(m, "cc", None)),
                           ("Subject", getattr(m, "subject", None)),
                           ("Date", getattr(m, "date", None))):
            if val:
                parts.append(f"{label}: {val}")
        body = getattr(m, "body", None)
        if body:
            parts.append("")
            parts.append(str(body).strip())
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Could not read Outlook .msg: {exc}") from exc
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass
    note = "Outlook message carries no page boundaries; one synthetic page"
    return synthetic_pages(["\n".join(parts)], notes=(note,)), [note]


def _extract_text(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    note = "plain text carries no page boundaries; emitted as one synthetic page"
    return (synthetic_pages([raw.decode("utf-8", errors="replace")], notes=(note,)),
            [note])


# ---------------------------------------------------------------------------
# ZIP — members become child documents, not concatenated text
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ZipMember:
    name: str
    raw: bytes
    order: int
    """Position in ``infolist()`` order. Deterministic child ID assignment
    (D-04) depends on it, so it is carried, not recomputed."""


@dataclass(frozen=True, slots=True)
class ZipExpansion:
    members: tuple[ZipMember, ...] = ()
    notes: tuple[str, ...] = ()


def expand_eml_attachments(raw: bytes) -> ZipExpansion:
    """Attachments of an RFC-822 email, as child members.

    §3 requires MSG/EML attachments to be "extracted as child documents linked
    to the parent message ID" — Tier 1, not optional. ``_extract_eml`` only
    ever produced the message's own headers+body page; nothing walked
    ``iter_attachments()``, so every attachment on every email in a matter
    vanished with no record, no note, and no line in the Unsupported list —
    a silent deletion Principle 1 forbids outright. This is the missing half.

    A zip attachment is flattened one level via :func:`expand_zip`, the same
    treatment a zip-inside-a-zip already gets, so "attach the production as a
    zip" does not reopen the hole this closes.
    """
    import email
    from email import policy

    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:
        # Codex review #1, B-3. This was a bare ``return ZipExpansion()``: the
        # parent message was emitted with ZERO attachments, no note and no
        # marker, so an email carrying the production itself looked like an
        # email carrying nothing. M_ATTACH_ENUM is the transient marker the
        # walker's retry registry keys on, which is what makes the failure
        # re-read rather than written off.
        return ZipExpansion((), (clip_message(
            f"{M_ATTACH_ENUM}: the message envelope would not parse ({exc}), "
            "so NO attachment of this email was brought in", 200),))

    raw_members: list[tuple[str, bytes]] = []
    notes: list[str] = []
    try:
        parts = list(msg.iter_attachments())
    except Exception as exc:
        return ZipExpansion((), (f"{M_ATTACH_ENUM}: {exc}"[:200],))
    for part in parts:
        try:
            name = part.get_filename() or f"attachment_{len(raw_members) + 1}"
            payload = part.get_payload(decode=True)
        except Exception as exc:
            notes.append(f"{M_ATTACH_READ}: {exc}"[:200])
            continue
        if payload is None:
            # Disclosed before, but with no marker of any kind — so nothing
            # downstream could find it mechanically and it sat outside both the
            # retry registry and the accounting tally (Codex review #1, B-3).
            # FINAL rather than transient: a part with no decodable payload
            # decodes to nothing on the second attempt too.
            notes.append(f"{M_ATTACH_SKIPPED}: attachment '{name}' had no "
                         "decodable payload; it is named here and its bytes "
                         "are not in the corpus")
            continue
        raw_members.append((name, payload))

    members: list[ZipMember] = []
    for name, payload in raw_members:
        if _ext(name) == ".zip":
            try:
                inner = expand_zip(payload)
            except Exception as exc:
                notes.append(f"{M_ZIP_ATTACH}: attachment '{name}' is a zip "
                             f"that could not be read: {exc}"[:200])
                continue
            notes.extend(f"{name}: {n}" for n in inner.notes)
            for m in inner.members:
                members.append(ZipMember(f"{name}/{m.name}", m.raw, len(members)))
        else:
            members.append(ZipMember(name, payload, len(members)))
    return ZipExpansion(tuple(members), tuple(notes))


def expand_msg_attachments(raw: bytes, scratch_dir: Path | None) -> ZipExpansion:
    """Attachments of an Outlook ``.msg``, as child members. See
    :func:`expand_eml_attachments` — same requirement, same prior gap.

    ``extract-msg`` needs a real path, same as ``_extract_msg``; the scratch
    file goes under the caller's working folder (§10) and is unlinked either
    way. An embedded-message attachment (an ``.msg`` inside a ``.msg``, which
    the library returns as a nested ``Message`` rather than bytes) is
    disclosed rather than silently skipped: it is real content, just not one
    this pass can flatten without recursing into a second temp-file dance.
    """
    try:
        import extract_msg
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Outlook .msg support requires 'extract-msg'.") from exc
    import tempfile

    if scratch_dir is not None:
        scratch_dir.mkdir(parents=True, exist_ok=True)
    tmp = None
    notes: list[str] = []
    raw_members: list[tuple[str, bytes]] = []
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".msg", delete=False,
            dir=str(scratch_dir) if scratch_dir is not None else None,
        ) as tf:
            tf.write(raw)
            tmp = tf.name
        m = extract_msg.Message(tmp)
        for i, att in enumerate(getattr(m, "attachments", None) or []):
            try:
                name = (getattr(att, "longFilename", None)
                        or getattr(att, "shortFilename", None)
                        or f"attachment_{i + 1}")
                data = getattr(att, "data", None)
            except Exception as exc:
                notes.append(f"{M_ATTACH_READ}: {exc}"[:200])
                continue
            if isinstance(data, (bytes, bytearray)):
                raw_members.append((name, bytes(data)))
            else:
                # An embedded .msg (Outlook nests a Message object, not
                # bytes) or an unreadable attachment kind. Disclosed, not
                # dropped: the operator sees that content exists and was not
                # brought in, rather than the run looking complete.
                notes.append(f"{M_ATTACH_SKIPPED}: attachment '{name}' is an "
                             "embedded message or an unsupported attachment "
                             "kind; it is named here and its bytes are not in "
                             "the corpus")
    except Exception as exc:
        return ZipExpansion((), (f"{M_MSG_ATTACH}: {exc}"[:200],))
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    members: list[ZipMember] = []
    for name, payload in raw_members:
        if _ext(name) == ".zip":
            try:
                inner = expand_zip(payload)
            except Exception as exc:
                notes.append(f"{M_ZIP_ATTACH}: attachment '{name}' is a zip "
                             f"that could not be read: {exc}"[:200])
                continue
            notes.extend(f"{name}: {n}" for n in inner.notes)
            for m2 in inner.members:
                members.append(ZipMember(f"{name}/{m2.name}", m2.raw, len(members)))
        else:
            members.append(ZipMember(name, payload, len(members)))
    return ZipExpansion(tuple(members), tuple(notes))


def expand_zip(raw: bytes, depth: int = 0) -> ZipExpansion:
    """Expand a (possibly nested) ZIP into flat member byte blobs.

    Members are read into memory only — never written to disk, so archive
    path-traversal is moot — and total uncompressed bytes, member count and
    nesting depth are all capped against a malicious or accidental bomb. Every
    cap that bites is disclosed as a note; §"no silent caps" is not satisfied
    by a guard that quietly stops early.

    Unlike MIP 3.9, members are returned rather than concatenated: the contract
    models an archive member as its own :class:`DocumentRecord` with a
    ``parent_doc_id`` and a ``container_order``.
    """
    import zipfile

    notes: list[str] = []
    if depth > _ZIP_MAX_DEPTH:
        return ZipExpansion((), (f"zip nesting deeper than {_ZIP_MAX_DEPTH} "
                                 "levels was not expanded",))
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as exc:
        raise ExtractionError(f"Could not read ZIP archive: {exc}") from exc

    members: list[ZipMember] = []
    total = 0
    cap_bytes = _ZIP_MAX_MB * 1024 * 1024
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if len(members) >= _ZIP_MAX_MEMBERS:
                notes.append(f"archive truncated at {_ZIP_MAX_MEMBERS} members; "
                             "later members were not read")
                break
            if info.file_size and total + info.file_size > cap_bytes:
                notes.append(f"archive truncated at {_ZIP_MAX_MB} MB uncompressed; "
                             f"'{info.filename}' and later members were not read")
                break
            try:
                blob = zf.read(info)
            except Exception as exc:
                notes.append(f"{M_ZIP_MEMBER}: '{info.filename}': "
                             f"{str(exc)[:120]}")
                continue
            total += len(blob)
            if _ext(info.filename) == ".zip":
                inner = expand_zip(blob, depth + 1)
                notes.extend(f"{info.filename}: {n}" for n in inner.notes)
                for m in inner.members:
                    members.append(ZipMember(f"{info.filename}/{m.name}", m.raw,
                                             len(members)))
                continue
            members.append(ZipMember(info.filename, blob, len(members)))
    return ZipExpansion(tuple(members), tuple(notes))


# ---------------------------------------------------------------------------
# D-50: documents embedded in a Word file, as child members
# ---------------------------------------------------------------------------


def _ole_objects_in_order(xml_bytes: bytes) -> list[tuple[str | None, str, str]]:
    """``(r:id or None, Type, ProgID)`` for every ``o:OLEObject`` in one
    part's XML, in document order — ``root.iter()`` visits in document
    order by construction, so no explicit sort is needed here."""
    from lxml import etree

    root = etree.fromstring(xml_bytes)
    out: list[tuple[str | None, str, str]] = []
    for el in root.iter(f"{{{_DOCX_NS['o']}}}OLEObject"):
        out.append((el.get(_rq("id")), el.get("Type") or "Embed",
                   el.get("ProgID") or ""))
    return out


def _parse_ole10_native(data: bytes) -> tuple[str, bytes]:
    """Parse an ``\\x01Ole10Native`` stream (the "Package" object wrapper).

    Layout: DWORD total size; WORD flags; label (NUL-terminated); stored
    filename (NUL-terminated); 4 reserved bytes; DWORD temp-path length;
    temp path; DWORD data length; data. Returns ``(filename, payload)``.

    Every read is bounds-checked against ``len(data)`` — a malformed or
    truncated stream raises :class:`ValueError`, which the caller turns into
    a marked note rather than an exception escaping the expansion (this
    function never raises anything else and never returns partial data)."""
    import struct

    pos = 0

    def need(n: int) -> None:
        if pos + n > len(data):
            raise ValueError(
                f"truncated Ole10Native stream: need {n} more byte(s) at "
                f"offset {pos}, have {len(data) - pos}")

    def read_u32() -> int:
        nonlocal pos
        need(4)
        v = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        return v

    def read_cstr() -> str:
        nonlocal pos
        end = data.find(b"\x00", pos)
        if end == -1:
            raise ValueError(f"unterminated string starting at offset {pos}")
        s = data[pos:end].decode("latin-1", errors="replace")
        pos = end + 1
        return s

    read_u32()  # total size — declarative only; not cross-checked below
    need(2)
    pos += 2  # flags — unused
    read_cstr()  # label — unused; the stored filename is what names the child
    filename = read_cstr()
    need(4)
    pos += 4  # reserved
    temp_len = read_u32()
    need(temp_len)
    pos += temp_len  # temp path — unused
    data_len = read_u32()
    need(data_len)
    return filename, data[pos:pos + data_len]


def expand_docx_embeddings(raw: bytes) -> ZipExpansion:
    """D-50: every document embedded in a Word file, as a child member.

    The walker calls this only for bytes :func:`is_word_package` accepts.
    The main document part is the one ``_rels/.rels`` names, never a
    hard-coded ``word/document.xml``, and the parts below live in its folder.

    Order: every ``o:OLEObject`` in the main document part, in document
    order, then in the header/footer/footnote/endnote parts, in part-name
    order — each resolved through ITS OWN part's relationships, since a
    header's ``r:id`` and the body's ``r:id`` are two different namespaces,
    by :func:`_opc_target`. Then any part under ``embeddings/`` that no
    ``o:OLEObject`` referenced, in name order. Each part is unwrapped once,
    keyed by its resolved part name — an object referenced twice (or picked
    up again by the trailing embeddings sweep) is not read twice. A part
    whose XML will not parse is named in a marked note and costs only the
    objects it references; one malformed part used to abort the whole
    expansion.

    Unwrapped by what the bytes ARE, never by ``ProgID`` alone (a real
    corpus's ProgID is informational, not load-bearing), reading only the
    compound file's OWN root-level streams, by exact name — a legacy Word or
    Excel file keeps its own embedded objects in nested storages, and those
    are not the Word file's objects:

    * a ZIP signature is the Office file itself, named by its part;
    * a compound file whose ``CONTENTS`` stream begins ``%PDF`` is an
      Acrobat object, and the stream is the child, ``<part stem>.pdf``;
    * one carrying ``\\x01Ole10Native`` is a Package object, parsed by
      :func:`_parse_ole10_native`, and the wrapped file is the child, named by
      the basename of its stored filename, or ``<part stem>.bin`` if that is
      empty;
    * one carrying a ``Package`` stream holds an Office file or a PDF in it,
      and that is the child, named ``<part stem>`` plus what it is;
    * a legacy Office document (``WordDocument``, ``Workbook``/``Book``,
      ``PowerPoint Document``, or an Outlook message's
      ``__properties_version1.0``) is kept whole as stored, under its own
      extension, and the walker's Tier-1/Tier-2 split takes it from there.

    A child that is itself a ``.zip`` is flattened into its members, as an
    archive attached to an email is. Anything else — any other compound
    file, a ``Type="Link"`` object, a relationship to a part not in the
    package, or content that is neither a ZIP nor a compound file — is not
    recovered, and is named in a marked note.
    """
    import zipfile

    notes: list[str] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as exc:
        raise ExtractionError(f"Could not read Word package: {exc}") from exc

    members: list[ZipMember] = []
    seen_parts: set[str] = set()
    total = 0
    cap_bytes = _ZIP_MAX_MB * 1024 * 1024
    truncated = False
    rels_by_part: dict[str, dict | None] = {}

    def over_member_cap() -> bool:
        nonlocal truncated
        if len(members) >= _ZIP_MAX_MEMBERS:
            if not truncated:
                # Marked, unlike expand_zip's own cap note: a run that hit the
                # cap must not look, to has_evidence_marker, identical to one
                # that recovered everything.
                notes.append(f"{M_ATTACH_SKIPPED}: embedded objects "
                             f"truncated at {_ZIP_MAX_MEMBERS} members; "
                             "later objects were not read")
                truncated = True
            return True
        return False

    def objects_in(part: str) -> list[tuple[str | None, str, str]]:
        try:
            return _ole_objects_in_order(zf.read(part))
        except Exception as exc:
            notes.append(f"{M_ATTACH_SKIPPED}: '{part}' could not be parsed "
                         f"({clip_message(str(exc), 120)}); any embedded object "
                         "it references was not identified from it")
            return []

    def rels_of(part: str) -> dict | None:
        if part not in rels_by_part:
            try:
                rels_by_part[part] = _docx_rels(zf, part)
            except Exception as exc:
                rels_by_part[part] = None
                notes.append(f"{M_ATTACH_SKIPPED}: the relationships of "
                             f"'{part}' could not be parsed "
                             f"({clip_message(str(exc), 120)}); the embedded "
                             "objects it references are not recovered through "
                             "them")
        return rels_by_part[part]

    def resolve_and_unwrap(owner_part: str, rid: str | None, obj_type: str) -> None:
        nonlocal total, truncated
        if obj_type == "Link":
            notes.append(f"{M_ATTACH_SKIPPED}: an embedded object in "
                         f"'{owner_part}' is a link rather than a stored "
                         "part; it is not recovered")
            return
        if rid is None:
            notes.append(f"{M_ATTACH_SKIPPED}: an embedded object in "
                         f"'{owner_part}' has no relationship id; it is not "
                         "recovered")
            return
        rels = rels_of(owner_part)
        if rels is None:
            return
        target = rels.get(rid)
        if target is None:
            notes.append(f"{M_ATTACH_SKIPPED}: an embedded object in "
                         f"'{owner_part}' names relationship id {rid!r}, "
                         "which does not exist; it is not recovered")
            return
        path, external = target
        if external:
            notes.append(f"{M_ATTACH_SKIPPED}: an embedded object in "
                         f"'{owner_part}' links to an external target "
                         f"'{path}' rather than storing it; it is not "
                         "recovered")
            return
        part_name = _opc_target(owner_part, path)
        if part_name in seen_parts:
            return
        seen_parts.add(part_name)
        if part_name not in names:
            notes.append(f"{M_ATTACH_SKIPPED}: an embedded object in "
                         f"'{owner_part}' names part '{part_name}', which is "
                         "not in the package; it is not recovered")
            return
        if over_member_cap():
            return
        try:
            blob = zf.read(part_name)
        except Exception as exc:
            notes.append(f"{M_ZIP_MEMBER}: '{part_name}': {str(exc)[:120]}")
            return
        total += len(blob)
        if total > cap_bytes:
            if not truncated:
                notes.append(f"{M_ATTACH_SKIPPED}: embedded objects "
                             f"truncated at {_ZIP_MAX_MB} MB uncompressed; "
                             f"'{part_name}' and later objects were not read")
                truncated = True
            return
        _unwrap_embedding(part_name, blob, members, notes)

    # A set, for membership only: every walk below iterates a SORTED list or
    # document order, so no output depends on the hash seed.
    names = set(zf.namelist())
    with zf:
        main_part = docx_main_part(zf)
        if main_part is None:
            raise ExtractionError(
                "Could not read Word package: it names no Word main document part")
        folder = main_part.rpartition("/")[0]
        prefix = f"{folder}/" if folder else ""

        for rid, obj_type, _progid in objects_in(main_part):
            if truncated:
                break
            resolve_and_unwrap(main_part, rid, obj_type)

        def is_aux(name: str) -> bool:
            if not name.startswith(prefix) or "/" in name[len(prefix):]:
                return False
            base = name[len(prefix):]
            return ((base.startswith(("header", "footer")) and base.endswith(".xml"))
                    or base in ("footnotes.xml", "endnotes.xml"))

        for part in sorted(n for n in names if is_aux(n)):
            if truncated:
                break
            for rid, obj_type, _progid in objects_in(part):
                if truncated:
                    break
                resolve_and_unwrap(part, rid, obj_type)

        for part_name in sorted(n for n in names
                                if n.startswith(f"{prefix}embeddings/")):
            if truncated:
                break
            if part_name in seen_parts:
                continue
            seen_parts.add(part_name)
            if over_member_cap():
                break
            try:
                blob = zf.read(part_name)
            except Exception as exc:
                notes.append(f"{M_ZIP_MEMBER}: '{part_name}': {str(exc)[:120]}")
                continue
            total += len(blob)
            if total > cap_bytes:
                notes.append(f"{M_ATTACH_SKIPPED}: embedded objects "
                             f"truncated at {_ZIP_MAX_MB} MB uncompressed; "
                             f"'{part_name}' and later objects were not read")
                break
            _unwrap_embedding(part_name, blob, members, notes)

    return ZipExpansion(tuple(members), tuple(notes))


_LEGACY_OFFICE_STREAMS = (
    ("WordDocument", ".doc"),
    ("Workbook", ".xls"),
    ("Book", ".xls"),
    ("PowerPoint Document", ".ppt"),
    ("__properties_version1.0", ".msg"),
)
"""Root-level streams that make a compound file a legacy Office document in
its own right, with the extension it is kept under. A tuple, checked in this
order, so the answer never depends on iteration order."""


def _unwrap_embedding(part_name: str, blob: bytes, members: list[ZipMember],
                      notes: list[str]) -> None:
    """Append what one embedded part holds to ``members``, or name it in a
    marked note — see :func:`expand_docx_embeddings` for the rules."""
    from pathlib import PurePosixPath

    stem = PurePosixPath(part_name).stem
    basename = PurePosixPath(part_name).name
    kind = sniff_kind(blob)
    if kind == "zip":
        _add_embedded_member(basename, blob, members, notes)
        return
    if kind != "ole":
        notes.append(f"{M_ATTACH_SKIPPED}: '{part_name}' is neither a ZIP nor a "
                     "compound file; it is not recovered")
        return
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover — declared dependency
        notes.append(f"{M_ATTACH_SKIPPED}: '{part_name}' is a compound "
                     f"file and 'olefile' is not installed ({exc}); it "
                     "is not recovered")
        return
    try:
        with olefile.OleFileIO(io.BytesIO(blob)) as ole:
            def root_stream(name: str) -> bytes | None:
                # The compound file's OWN stream, by exact name at the root
                # (CFB compares names case-insensitively, as olefile does).
                if ole.get_type(name) != olefile.STGTY_STREAM:
                    return None
                return ole.openstream(name).read()

            child: tuple[str, bytes] | None = None
            contents = root_stream("CONTENTS")
            native = root_stream("\x01Ole10Native")
            package = root_stream("Package")
            legacy = next((ext for stream, ext in _LEGACY_OFFICE_STREAMS
                           if ole.get_type(stream) == olefile.STGTY_STREAM), "")
            if contents is not None and contents[:4] == _MAGIC_PDF:
                child = (f"{stem}.pdf", contents)
            elif native is not None:
                try:
                    filename, payload = _parse_ole10_native(native)
                except Exception as exc:
                    notes.append(
                        f"{M_ATTACH_SKIPPED}: '{part_name}' carries a "
                        f"malformed Ole10Native stream ({exc}); it is "
                        "not recovered")
                    return
                child = (_ole10_child_name(filename, stem), payload)
            elif package is not None:
                child = (f"{stem}{_package_stream_extension(package)}", package)
            elif legacy:
                child = ((basename if _ext(basename) == legacy
                          else f"{stem}{legacy}"), blob)
    except Exception as exc:
        notes.append(f"{M_ATTACH_SKIPPED}: '{part_name}' is a compound "
                     f"file that could not be read ({exc}); it is not "
                     "recovered")
        return
    if child is None:
        notes.append(f"{M_ATTACH_SKIPPED}: '{part_name}' is a compound file "
                     "holding no PDF, no Package object and no legacy Office "
                     "document; it is not recovered")
        return
    _add_embedded_member(child[0], child[1], members, notes)


def _ole10_child_name(filename: str, stem: str) -> str:
    """A Package object's child name: the basename of its stored filename,
    read as the Windows path it is on every operating system, or
    ``<part stem>.bin`` when that basename is empty, blank, ``.`` or
    ``..``."""
    from pathlib import PureWindowsPath

    base = PureWindowsPath(filename).name if filename else ""
    return base if base.strip(" .") else f"{stem}.bin"


def _package_stream_extension(payload: bytes) -> str:
    """The extension for the file a ``Package`` stream holds, by its bytes."""
    kind = sniff_kind(payload)
    if kind == "pdf":
        return ".pdf"
    if kind != "zip":
        return ".bin"
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            main = _opc_main_part(zf)
    except Exception:
        return ".zip"
    content_type = main[1] if main else ""
    if content_type in _WORD_MAIN_CONTENT_TYPES:
        return ".docx"
    if "spreadsheetml" in content_type or "ms-excel" in content_type:
        return ".xlsx"
    if "presentationml" in content_type or "ms-powerpoint" in content_type:
        return ".pptx"
    return ".zip"


def _add_embedded_member(name: str, payload: bytes, members: list[ZipMember],
                         notes: list[str]) -> None:
    """One recovered child, or — for a ``.zip`` — each of its members, named
    under it, as :func:`expand_eml_attachments` flattens an attached archive.
    Kept whole, a zip became one unsupported record whose members were never
    read."""
    if _ext(name) != ".zip":
        members.append(ZipMember(name, payload, len(members)))
        return
    try:
        inner = expand_zip(payload)
    except Exception as exc:
        notes.append(f"{M_ATTACH_SKIPPED}: embedded archive '{name}' could not "
                     f"be read ({clip_message(str(exc), 120)}); its members "
                     "are not recovered")
        return
    for n in inner.notes:
        notes.append(f"{name}: {n}" if has_evidence_marker(n)
                     else f"{M_ATTACH_SKIPPED}: embedded archive '{name}': {n}")
    for m in inner.members:
        members.append(ZipMember(f"{name}/{m.name}", m.raw, len(members)))


# ---------------------------------------------------------------------------
# Content-sniffing recovery (extension-mismatch)
# ---------------------------------------------------------------------------

_MAGIC_PDF = b"%PDF"
_MAGIC_ZIP = b"PK\x03\x04"
_MAGIC_OLE = b"\xd0\xcf\x11\xe0"

# The zip signature is shared by every OOXML format, so the whole family is
# probed, plain ZIP last.
_SNIFF_CHAINS = {
    "pdf": [(".pdf", "PDF")],
    "zip": [(".docx", "Word"), (".xlsx", "Excel"), (".pptx", "PowerPoint")],
    "ole": [(".msg", "Outlook .msg"), (".xls", "legacy Excel")],
}
_SNIFF_LABELS = {"pdf": "PDF", "zip": "a zip-family container",
                 "ole": "a legacy OLE container"}
# Extensions sharing one extractor — a failed .xlsm must not retry .xlsx.
_EXT_ALIASES = {".xlsm": ".xlsx", ".email": ".eml", ".md": ".txt", ".log": ".txt"}


def _ext(filename: str) -> str:
    name = (filename or "").lower()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


# Windows drive paths, UNC paths, and POSIX absolute paths.
_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![\w.])/)[^\s\"'<>|]*")


def clip_message(msg: str, limit: int) -> str:
    """Truncate a message and SAY that it was truncated.

    Every error string in the pipeline is length-bounded so one pathological
    parser cannot write a megabyte into the log. A bound that removes text
    without a mark is a silent cap, which is the thing the standing rule
    forbids — so the mark is not decoration.
    """
    if msg is None or len(msg) <= limit:
        return msg
    return msg[:limit].rstrip() + f" […truncated at {limit} chars]"


def sanitize_message(msg: str) -> str:
    """Strip absolute paths out of a message that will reach a record.

    An error string is hashed content: it lands in ``DocumentRecord.error``,
    which lands in the log's ``content`` section. A parser that reports
    ``C:\\Users\\...\\Temp\\tmp61yhcl7p\\x.msg`` therefore puts a per-run
    random string inside the byte-identical claim. Reducing every absolute
    path to its final component fixes the whole class at the one place every
    message passes, rather than auditing each ``f"...{exc}"`` for ever.
    """
    if not msg:
        return msg

    def _basename(m: re.Match[str]) -> str:
        return m.group(0).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "<path>"

    return _ABS_PATH.sub(_basename, msg)


def sniff_kind(raw: bytes) -> str:
    """Magic-byte kind (``pdf`` / ``zip`` / ``ole``), or ``""``."""
    if raw.startswith(_MAGIC_PDF):
        return "pdf"
    if raw.startswith(_MAGIC_ZIP):
        return "zip"
    if raw.startswith(_MAGIC_OLE):
        return "ole"
    return ""


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".xlsx": _extract_xlsx,
    ".xlsm": _extract_xlsx,
    ".xls": _extract_xls,
    ".csv": _extract_csv,
    ".pptx": _extract_pptx,
    ".eml": _extract_eml,
    ".email": _extract_eml,
    ".msg": _extract_msg,
    ".txt": _extract_text,
    ".md": _extract_text,
    ".log": _extract_text,
}


def _dispatch(ext: str, raw: bytes, opt: ExtractOptions):
    fn = _EXTRACTORS.get(ext)
    if fn is None:
        raise ExtractionError(f"No Tier-1 extractor for '{ext}'.")
    return fn(raw, opt)


def _retry_by_content(ext: str, raw: bytes, opt: ExtractOptions, original: Exception):
    """Retry with the content-sniffed extractor after ``ext``'s own raised.

    Litigation productions routinely deliver files under the wrong extension —
    PDF bytes named .docx, Word files named .pdf — and an extension-only
    dispatch skips them with "File is not a zip file". Recovery is annotated so
    the mismatch reaches the audit trail rather than being quietly fixed.
    """
    kind = sniff_kind(raw)
    if not kind:
        raise original
    canon = _EXT_ALIASES.get(ext, ext)
    tried: list[str] = []
    for retry_ext, label in _SNIFF_CHAINS[kind]:
        if retry_ext == canon:
            continue  # the extension-selected extractor already failed
        tried.append(label)
        try:
            pages, notes = _dispatch(retry_ext, raw, opt)
        except Exception:
            continue
        return pages, notes + [f"extension {ext} but content is "
                               f"{_SNIFF_LABELS[kind]}; recovered via "
                               f"{label} extractor"]
    raise ExtractionError(
        f"{original} (content sniffed as {_SNIFF_LABELS[kind]}; retry via "
        + (", ".join(tried) if tried else "no alternate extractor")
        + " also failed)"
    ) from original


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def is_tier1(ext: str) -> bool:
    return ext.lower() in TIER1_EXTENSIONS


def tier2_hint(ext: str) -> str:
    """The remediation hint for a Tier-2 or unknown extension (§3, D-02)."""
    return TIER2_EXTENSIONS.get(ext.lower(), UNKNOWN_HINT)


def extract(filename: str, raw: bytes,
            opt: ExtractOptions | None = None) -> ExtractedDoc:
    """Extract one document's pages. Never raises.

    A failure is data — an :class:`ExtractedDoc` with FAILED status and an
    actionable message — because a single unreadable file in a 9,000-file
    production must not abort the run.
    """
    opt = opt or ExtractOptions()
    ext = _ext(filename)
    if ext == ".zip":
        raise ExtractionError(
            "ZIP is expanded by the walker into child documents; "
            "call expand_zip() instead of extract().")
    if not is_tier1(ext):
        return ExtractedDoc(status=ProcessingStatus.UNSUPPORTED,
                            error=tier2_hint(ext))
    if not raw:
        return ExtractedDoc(status=ProcessingStatus.FAILED, error="empty file")
    try:
        pages, notes = _dispatch(ext, raw, opt)
    except Exception as exc:
        try:
            pages, notes = _retry_by_content(ext, raw, opt, exc)
        except Exception as exc2:
            return ExtractedDoc(status=ProcessingStatus.FAILED,
                                error=clip_message(sanitize_message(str(exc2)), 400))
    notes = [sanitize_message(n) for n in notes]
    # §4 Stage 2: the flag is driven by the page's MEAN confidence against the
    # run threshold. A page that failed OCR outright flags too — it is exactly
    # the case a human must look at, and it would otherwise pass as FULL
    # because it has no confidence to be below anything. A page that OCR'd
    # cleanly to nothing does NOT flag: a blank page inside a native PDF is
    # ordinary, and flagging it would train the operator to ignore the flag.
    # It is still disclosed, as a page note and a document note.
    # Same predicate again. A page that FAILED to OCR still flags regardless of
    # confidence — that is a failure, not a reading the threshold can judge.
    threshold_pct = round(opt.conf_threshold * 100)
    flagged = any(
        needs_ocr_review(p, threshold_pct)
        or any(n.startswith(M_OCR_PAGE) for n in p.notes)
        for p in pages
    )
    status = (ProcessingStatus.PARTIAL_OCR_FLAGGED if flagged
              else ProcessingStatus.FULL)

    # Section recognition, for the one format that carries an outline (D-35,
    # Tiers 1 and 3). It runs on the pages just built, so a scanned page is
    # classified on the text OCR actually recovered rather than on the empty
    # text layer it started with.
    #
    # FAIL-OPEN AND DISCLOSED. A document whose recognition raises keeps every
    # page, which is the direction §1 requires — but it is indistinguishable
    # from a document with nothing to recognize, so it must not be silent. The
    # note is a FINAL marker: the same bytes cannot yield a different outline,
    # so the serial retry would spend its budget re-proving the failure.
    spans: tuple[SectionSpan, ...] = ()
    if _ext(filename) == ".pdf" and pages:
        try:
            spans = pdf_spans(raw, pages, project_tokens=opt.project_tokens)
        except Exception as exc:
            notes.append(sanitize_message(
                f"{M_SECTIONS}: {clip_message(str(exc), 200)}; no page in this "
                "document was placed in a section, and every page is kept"))

    return ExtractedDoc(pages=tuple(pages), notes=tuple(notes), status=status,
                        spans=spans)
