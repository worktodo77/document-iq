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
    SKIP_IMAGES_ON_TEXT_PAGES_DEFAULT,
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

_SCAN_MIN_IMAGE_SHARE = 0.90
"""Images whose drawn areas add up to at least this share of a page make it a
SCAN, read even by a run that skips the images on text pages (D-54).

A text layer of ``_NATIVE_TEXT_FLOOR`` characters keeps a page off whole-page
OCR, and images adding up to ``PHOTO_MIN_IMAGE_AREA_SHARE`` make it MIXED,
which a quick first pass (A-25, D-51) leaves unread. Together they let a typed
stamp decide whether a scan was read: a full-page scan carrying an endorsement
over the text floor came out NATIVE, its text the endorsement alone, while the
same scan with no stamp -- or one under the floor -- was OCR'd whole. That is
the defect D-48 recorded for protective-order legends, returned under D-51's
default. Alex ruled D-54: read it. Such a page is read exactly as a reading run
reads it -- MIXED, its image regions OCR'd, its Bates locator from the text
layer alone (D-49) -- and only image content beside real typed content is
skipped. 90% is the figure the ruling proposed; what it reaches on the
acceptance corpus is D-54's census, in the decision register, not a copy here.

**The share is SUMMED.** :func:`_page_image_share` adds the areas of every image
the page draws without de-overlapping them, capped at 1.0, so four tiles that
each cover under a quarter of a page count as a scan when together they reach
this. Every DRAW counts, not every image object: one image drawn twice (a
thumbnail and the full page, or twice at one place) counts at both placements,
and a scan stored inline in the content stream counts like one stored as an
object. It is an upper bound on what the page shows, so notes quoting it say
the drawn areas add up to a share, never that they cover it.
:func:`_image_placements` says what a draw is."""

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

M_IMAGE_SKIPPED = f"{M_IMAGE_UNREAD}: skipped by this run's quick-pass setting"
"""A-25 (D-51). The page note on a page whose image was left unread BY CHOICE.

It keeps :data:`M_IMAGE_UNREAD` as its prefix, so every consumer of that marker
still counts the page as evidence missing from the corpus, which it is. The one
consumer that must tell the two apart is :func:`ocr_yield`: a skipped page was
never attempted, and counting it as a failed attempt raises the dead-engine
alarm on the default run over any production of letterhead-and-chart pages."""

M_IMAGE_UNMEASURED = f"{M_IMAGE_UNREAD}: image geometry could not be measured"
"""The page note on a page whose image draws could not be interpreted (D-51's
third review round).

Geometry is how a page carrying image content beside its text layer is FOUND.
A page whose content stream MuPDF refuses to interpret (the review's case: an
image drawn inside 2,047 nested graphics states) measured as carrying no image,
and came out NATIVE with no note on either setting; before the every-draw count
the same page had carried :data:`M_IMAGE_UNREAD`. Whether its image holds words
is not known, so the page says so. It keeps the prefix, so accounting counts it
as evidence not in the corpus, and :func:`ocr_yield` leaves it out, because no
OCR was attempted on it.

Since D-60 a run with OCR on reads such a page whole, as a scan is, on either
setting, and the marker stays only where that did not happen: OCR off or
unavailable, or the page could not be rendered either (then beside
:data:`M_OCR_PAGE`, which counts as the attempt)."""

IMAGE_TEXT_MAY_REPEAT = ("may repeat typed words of their text layer, read again "
                         "from image content under or over that text")
"""The phrase of the document note naming MIXED pages where a character of the
text layer is centered inside an image region that was read and whose OCR
returned text (D-58; a page read whole under D-60 is one such region). Tested
per character center, not per word box, and only against regions that yielded
text (D-51 round-4 review): see :func:`_text_on_images`.

Region OCR reads each image region whole. Typed text lying on a picture (a
letter on full-page stationery, a stamp on a scan, a searchable scan's OCR
layer) is therefore read twice: once from the text layer and once from the
image. Alex ruled D-58 that this is accepted, because it loses nothing, and
that it is disclosed. NOT an evidence marker: nothing is missing, and a marker
that fires on every searchable scan would teach an operator to stop reading
markers."""

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
    # A-25. FINAL too, and listed on its own although `M_IMAGE_UNREAD` already
    # matches it: a re-read under the same setting skips the same pages, and
    # what changes the answer is the operator unticking the quick-pass box.
    # Left unlisted, the class assertion in the extraction tests cannot tell a
    # classified marker from a forgotten one -- which is how it was caught.
    M_IMAGE_SKIPPED,
    # D-51 round 3. FINAL for the same reason as M_IMAGE_UNREAD: the same bytes
    # reach the same interpreter limit.
    M_IMAGE_UNMEASURED,
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
# Neither bound loses anything (D-60): a region either leaves unread leaves
# drawn image area outside every region read, and such a page is read whole, as
# a scan is, and named with the bound as its cause. Until D-60 each bound set the
# page's evidence marker instead (from D-51's third review round, whether or not
# another region read), and the log called the skipped regions a failure to
# rasterize or read, which they were not: they were never tried.
_MIXED_MAX_REGIONS = int(os.environ.get("DOCIQ_MIXED_MAX_REGIONS", "24"))
_MIXED_MIN_REGION_PX = 8

# Merging image boxes into regions is exact up to this many distinct draws on a
# page, and beyond it each box is first widened to a grid of
# _MERGE_COARSE_CELLS x _MERGE_COARSE_CELLS cells over the page (D-51 round 3).
# The pairwise merge was quadratic: one image drawn 14,400 times took 16 s to
# merge on one page. Widening only ever makes a region LARGER, so no image
# pixel leaves every crop; a pathological page is read in coarser pieces.
_MERGE_EXACT_MAX_DRAWS = 2048
_MERGE_COARSE_CELLS = 64

_MERGE_GAP_PT = 1.0
"""Boxes this close (in points, on both axes) are merged into one region, so
touching or nearly touching draws are read as one picture (D-51 round-4 review:
a scan stored as touching bands lost every line a band edge cut). One point is
under three pixels at the 200 dpi rendering."""

_COVERAGE_TOLERANCE_PX = 1.0
"""D-60's tolerance: the drawn image area left outside every region that was
read may add up to at most this many rendered pixels (at 200 dpi) over the
whole page before the page is read whole. Crops are cut outward to whole
pixels, so a region that was read covers its draws exactly and the tolerance
only absorbs floating-point error; a draw smaller than one pixel cannot carry a
legible glyph."""

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

    skip_images_on_text_pages: bool = SKIP_IMAGES_ON_TEXT_PAGES_DEFAULT
    """Leave unread the images on pages that also carry a text layer (A-25, D-51).

    A-24's region OCR, skipped: such a page stays NATIVE, carries
    :data:`M_IMAGE_SKIPPED`, and the document note names it. A page with no
    usable text layer is still OCR'd whole, and a page whose image content
    covers :data:`_SCAN_MIN_IMAGE_SHARE` of it -- a scan carrying a typed stamp
    -- is still read as a reading run reads it (D-54). The walk sets it from
    ``RunConfig`` and never from ``WalkOptions``: one setting with two sources is
    how a resume journal replays a page read under the other one."""

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
    * :data:`M_IMAGE_UNREAD` on a PAGE — image content an attempt could not
      read. Since D-60 region OCR writes none: a region it leaves unread sends
      the page to a whole-page reading, which is an attempt, and a page whose
      reading raised carries :data:`M_OCR_PAGE`, which counts above. So a
      region under the size floor or past the cap is never a failed attempt of
      its own, and a page that only such regions touched raises no dead-engine
      alarm unless the whole-page reading, which does run, recovers nothing.

    **Not** :data:`M_IMAGE_SKIPPED` (A-25), though it carries the same prefix. A
    page the run's setting skipped was never sent to OCR, so it is no attempt at
    all. Counted as a failed one, the default run over any production of
    letterhead-and-chart pages would report a dead engine.

    **Nor** :data:`M_IMAGE_UNMEASURED`: a page whose image geometry could not be
    interpreted was never sent to OCR either.
    """
    attempted = recovered = 0
    for doc in documents:
        for page in doc.pages:
            worked = page.read_by_ocr and page.text.strip()
            blank = any(n.startswith(M_OCR_BLANK) or n.startswith(M_OCR_PAGE)
                        or (n.startswith(M_IMAGE_UNREAD)
                            and not n.startswith(M_IMAGE_SKIPPED)
                            and not n.startswith(M_IMAGE_UNMEASURED))
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
    # Region OCR only (A-24, D-60). ``regions`` is how many image regions the
    # page has. ``whole`` is empty when the regions that were read account for
    # every image draw, and otherwise names why the page was read whole
    # instead, cause codes first (see :func:`_ocr_pdf_regions`). ``raised``
    # counts region OCR calls that raised, a load-dependent failure the walker
    # retries. ``failed`` means the page could not be read at all, not even
    # whole.
    regions: int = 0
    whole: tuple[str, ...] = ()
    raised: int = 0
    # A character of the page's text layer lies inside a region that was read
    # and yielded text (D-58).
    text_on_image: bool = False


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
        for i in pages:
            if not 0 <= i < len(doc):
                # A page the text layer's reader sees and MuPDF does not have
                # cannot be rendered. Left out of ``out`` it came back as an
                # EMPTY page with no note (D-51 round-4 review, C); it is a
                # page that could not be rasterized, and says so.
                out[i] = _OcrPage(failed=True)
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


def _content_textpage(page, flags: int):
    """The page's CONTENT STREAM, through MuPDF's text device, in the
    coordinates of the page as rendered (rotation and CropBox applied).

    Content stream only, annotations excluded: the text layer the routing reads
    is pypdf's, which reads no annotation. A display list built from the
    contents gives it in the same space :func:`_page_array` renders and
    :func:`_image_placements` measures.
    """
    import fitz  # pymupdf

    return fitz.TextPage(page.get_displaylist(annots=False).get_textpage(flags))


_IMAGE_DRAWS_DEVICE = None


def _image_draws_device():
    """The MuPDF device class :func:`_image_placements` runs a page through,
    built on first use so importing this module does not import MuPDF."""
    global _IMAGE_DRAWS_DEVICE
    if _IMAGE_DRAWS_DEVICE is not None:
        return _IMAGE_DRAWS_DEVICE
    import pymupdf.mupdf as mupdf

    class _ImageDraws(mupdf.FzDevice2):
        """Records the device-space box of every image the page PAINTS.

        * ``fill_image`` and ``fill_image_mask`` (a stencil mask paints its
          color through the image) are draws.
        * A ``clip_image_mask`` is a draw when nothing it clips is: a stencil
          mask filled with a shading or a tiling pattern reaches the device as
          that clip, then the fill, then ``pop_clip``, never as
          ``fill_image_mask``, so without it such a scan measured 0 and went
          unread with no note (D-51 round-4 review). An image carrying its own
          transparency (``/SMask``) reaches it as the same clip around a
          ``fill_image``, and that image is already the draw, so the clip is
          not counted again. A clip nothing is painted through counts: the
          measure errs toward reading. These three are every call of MuPDF's
          device interface that takes an image; the other clip calls are
          hooked only so that ``pop_clip`` closes the right one.
        * Between ``begin_mask`` and ``end_mask`` the content is a SOFT MASK:
          it shapes another object's transparency and paints nothing itself,
          so an image drawn there is not a draw.
        * Inside a tiling pattern the tile's content is replayed over the
          pattern's filled area, so an image in a tile is measured as that
          AREA, once per pattern fill, not as one tile.
        """

        def __init__(self):
            super().__init__()
            self.boxes: list[tuple[float, float, float, float]] = []
            self._mask_depth = 0
            self._tiles: list[list] = []
            # One entry per clip pushed: None for a clip that is not an image
            # mask, [box, an image drew inside it] for one that is.
            self._clips: list[list | None] = []
            self._unit = mupdf.FzRect(mupdf.FzRect.Fixed_UNIT)
            for name in ("fill_image", "fill_image_mask", "clip_image_mask", "clip_path",
                         "clip_stroke_path", "clip_text", "clip_stroke_text", "pop_clip",
                         "begin_mask", "end_mask", "begin_tile", "end_tile"):
                getattr(self, "use_virtual_" + name)()

        def _box(self, ctm):
            r = mupdf.ll_fz_transform_rect(self._unit.internal(), ctm)
            return (min(r.x0, r.x1), min(r.y0, r.y1), max(r.x0, r.x1), max(r.y0, r.y1))

        def _record(self, box):
            if self._mask_depth:
                return
            for clip in self._clips:
                if clip is not None:
                    clip[1] = True  # an image drew inside it: the clip is not a draw
            if self._tiles:
                self._tiles[0][1] = True  # the outermost pattern's area counts
                return
            self.boxes.append(box)

        def fill_image(self, ctx, image, ctm, alpha, color_params):
            self._record(self._box(ctm))

        def fill_image_mask(self, ctx, image, ctm, colorspace, color, alpha,
                            color_params):
            self._record(self._box(ctm))

        def clip_image_mask(self, ctx, image, ctm, scissor):
            self._clips.append([self._box(ctm), False])

        def clip_path(self, *args):
            self._clips.append(None)

        def clip_stroke_path(self, *args):
            self._clips.append(None)

        def clip_text(self, *args):
            self._clips.append(None)

        def clip_stroke_text(self, *args):
            self._clips.append(None)

        def pop_clip(self, ctx):
            if not self._clips:
                return
            clip = self._clips.pop()
            if clip is not None and not clip[1]:
                self._record(clip[0])

        def begin_mask(self, ctx, area, luminosity, colorspace, bc, color_params):
            self._mask_depth += 1

        def end_mask(self, ctx, fn):
            self._mask_depth -= 1

        def begin_tile(self, ctx, area, view, xstep, ystep, ctm, id, doc_id):
            # `area` is in pattern space; the tile's ctm takes it to the page.
            r = mupdf.ll_fz_transform_rect(area, ctm)
            self._tiles.append([(min(r.x0, r.x1), min(r.y0, r.y1),
                                 max(r.x0, r.x1), max(r.y0, r.y1)), False])
            return 0  # not cached: run the tile's content once, so it is seen

        def end_tile(self, ctx):
            area, drew = self._tiles.pop()
            if not drew:
                return
            if self._tiles:
                self._tiles[0][1] = True
            elif not self._mask_depth:
                for clip in self._clips:
                    if clip is not None:
                        clip[1] = True
                self.boxes.append(area)

    _IMAGE_DRAWS_DEVICE = _ImageDraws
    return _ImageDraws


def _image_placements(page) -> list[tuple[float, float, float, float]]:
    """One box per image DRAWN on the page, in the rendered page's coordinates,
    clipped to the page.

    **Every draw, not every image object** (D-51 round-2 review). The earlier
    enumeration listed the image objects in the page's resources and took each
    one's FIRST placement, so an image drawn as a thumbnail and then as the full
    page measured as a thumbnail, and its full-page scan went unread with no
    note on either setting. It also could not see a scan written inline in the
    content stream (``BI ... ID ... EI``), which is in no resource list.

    **What a draw is** (D-51 round-3 review), measured by running the page's
    content stream through :func:`_image_draws_device`, annotations excluded:

    * every image painted, inline images, images inside form XObjects and
      stencil masks included, and the same image painted twice at the same place
      is two draws -- the share that sums them is an upper bound on purpose;
    * NOT an image used only inside a soft mask, which paints nothing: counted,
      it made a typed letter over a faded background measure as a full-page
      scan;
    * an image inside a tiling pattern counts as the area the pattern fills,
      not as one tile: counted as a tile, a page painted with a scan through a
      pattern measured as a sliver and went unread with no note;
    * each box is clipped to the page, and a draw with no area on the page is
      dropped: unclipped, an image placed off the page measured as full
      coverage and was reported as content that could not be read.

    An image that is listed but never painted puts nothing on the page and is
    not reported. **Raises** when MuPDF cannot interpret the page (for example
    more than 2,046 nested graphics states); each caller decides what that
    discloses, and the routing discloses it as :data:`M_IMAGE_UNMEASURED`.
    """
    import pymupdf.mupdf as mupdf

    device = _image_draws_device()()
    mupdf.fz_run_page_contents(mupdf.FzPage(page.this), device, mupdf.FzMatrix(),
                               mupdf.FzCookie())
    mupdf.fz_close_device(device)
    px0, py0, px1, py1 = page.rect
    boxes: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1 in device.boxes:
        x0, y0, x1, y1 = max(x0, px0), max(y0, py0), min(x1, px1), min(y1, py1)
        if x1 > x0 and y1 > y0:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _merge_boxes(boxes, page_rect) -> list[tuple[float, float, float, float]]:
    """Union boxes that overlap or lie within :data:`_MERGE_GAP_PT` of each
    other until no two do, in reading order.

    The result is the finest partition whose bounding boxes are pairwise more
    than the gap apart, which does not depend on the order boxes are merged
    in, so any algorithm that reaches it gives the same regions. The pairwise
    loop this replaces reached it in time quadratic in the number of draws
    (16 s for one image drawn 14,400 times on one page, D-51 round-3 review).
    This sweeps the boxes along one axis, merging each into the regions still
    open across it, and repeats until a sweep merges nothing.

    **Touching boxes are one region** (D-51 round-4 review). Only strictly
    overlapping boxes merged, so a scan stored as touching horizontal bands
    was read band by band, and every line of type a band edge cut through was
    lost: OCR of each band saw half a line. The gap is small enough that two
    pictures a reader sees as separate stay separate.

    Identical boxes are one region. Above :data:`_MERGE_EXACT_MAX_DRAWS`
    distinct boxes, each is first widened outward to the
    :data:`_MERGE_COARSE_CELLS` grid over ``page_rect``, so that boxes in
    neighboring cells touch and merge, which bounds the work: at most one
    region per cell comes out. Widening never shrinks a region, so every drawn
    pixel still lies in one; such a page is read in larger pieces.
    """
    import math

    uniq = list(dict.fromkeys(b for b in boxes if b[2] > b[0] and b[3] > b[1]))
    if len(uniq) > _MERGE_EXACT_MAX_DRAWS:
        px0, py0, px1, py1 = page_rect
        cw = (px1 - px0) / _MERGE_COARSE_CELLS
        ch = (py1 - py0) / _MERGE_COARSE_CELLS
        if cw > 0 and ch > 0:
            uniq = list(dict.fromkeys(
                (px0 + math.floor((x0 - px0) / cw) * cw,
                 py0 + math.floor((y0 - py0) / ch) * ch,
                 px0 + math.ceil((x1 - px0) / cw) * cw,
                 py0 + math.ceil((y1 - py0) / ch) * ch)
                for x0, y0, x1, y1 in uniq))
    if len(uniq) > 1:
        axis = 0 if sum(b[2] - b[0] for b in uniq) <= sum(b[3] - b[1] for b in uniq) else 1
        while True:
            uniq, merges = _sweep_merge(uniq, axis)
            if not merges:
                break
    uniq.sort(key=lambda b: (b[1], b[0], b[3], b[2]))
    return uniq


def _near(a, b, gap: float = None) -> bool:
    """Whether boxes ``a`` and ``b`` overlap or lie within ``gap`` points of
    each other on both axes: the one test :func:`_merge_boxes` merges on."""
    g = _MERGE_GAP_PT if gap is None else gap
    return a[0] < b[2] + g and b[0] < a[2] + g and a[1] < b[3] + g and b[1] < a[3] + g


def _sweep_merge(boxes, axis: int):
    """One sweep of :func:`_merge_boxes` along ``axis`` (0 = x, 1 = y):
    ``(regions, merges made)``. A region leaves the open list once the sweep
    has passed its far edge by more than the gap; one that grew afterwards can
    still reach it, which is why the caller sweeps again until a sweep makes
    no merge."""
    lo, hi = axis, axis + 2
    g = _MERGE_GAP_PT
    open_: list[tuple[float, float, float, float]] = []
    closed: list[tuple[float, float, float, float]] = []
    merges = 0
    for b in sorted(boxes, key=lambda q: q[lo]):
        if open_:
            still = []
            for a in open_:
                (still if a[hi] + g > b[lo] else closed).append(a)
            open_ = still
        cur = b
        grew = True
        while grew:
            grew = False
            for k, a in enumerate(open_):
                if _near(a, cur, g):
                    cur = (min(a[0], cur[0]), min(a[1], cur[1]),
                           max(a[2], cur[2]), max(a[3], cur[3]))
                    del open_[k]
                    merges += 1
                    grew = True
                    break
        open_.append(cur)
    return closed + open_, merges


def _text_on_images(page, rects) -> bool:
    """Whether a character of the page's text layer lies inside one of
    ``rects`` (D-58): region OCR reads such a region whole, typed characters
    included, so the page's text may carry them twice. The caller passes only
    the regions whose OCR returned text; a region that read nothing cannot
    have repeated anything.

    **A character lies inside a region when its CENTER does** (D-51 round-4
    review). The word box MuPDF reports starts at the font's ascender, above
    the ink, so an 11-point caption set 3 points under a chart overlapped the
    chart's region by under a point, and the page was named although nothing
    on it could repeat. A character's center is inside its ink, and is inside
    a region exactly when the region's crop holds most of that glyph.

    Visible and invisible (render mode 3) text alike: an invisible OCR layer
    lies over the scan it transcribes, so reading the scan reads its words.
    When the characters cannot be read the answer is True, so the page is
    named rather than silently left out of the disclosure."""
    if not rects:
        return False
    try:
        blocks = _content_textpage(page, 0).extractRAWDICT()["blocks"]
    except Exception:
        return True
    for block in blocks:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for ch in span.get("chars", ()):
                    if not ch["c"].strip():
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    for r in rects:
                        if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
                            return True
    return False


def _pdf_image_rects(page) -> list[tuple[float, float, float, float]]:
    """Every image placement's box on one page, merged and in reading order.

    Returns plain ``(x0, y0, x1, y1)`` tuples in the rendered page's
    coordinates, from the :func:`_image_placements` draws. **Raises** when the
    page cannot be interpreted, so the caller marks the page rather than
    reading it as a page with no image (D-51 round-3 review: returning an empty
    list here dropped the page from region OCR with no note).

    **Overlaps are unioned, and that is load-bearing rather than tidy.**
    :func:`_page_image_share` records that overlapping images are deliberately
    NOT de-overlapped there, because its number is an upper bound Tier 3 was
    measured with. Here the consequence is different: two overlapping images
    cropped separately hand the same pixels to OCR twice, and the page would
    carry the image's text twice. Merging first is what keeps an image from
    being read twice. It does not keep the text layer from being read again:
    typed text lying on an image is inside that image's crop (D-58).

    **The order is total.** Sorting on ``(y0, x0)`` alone leaves ties broken by
    the order the draws were enumerated in, which is not a reading order — and
    an unstable order here would reorder text inside a page between runs, which
    is a determinism defect in the one product whose headline claim is
    byte-identical repeat runs. The full box is in the key, so two distinct
    boxes can never tie.
    """
    return _merge_boxes(_image_placements(page), tuple(page.rect))


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


IMAGE_READ_WHOLE = ("read whole, as a scan is, because the image regions read did not "
                    "account for every picture drawn on it (D-60)")
"""The phrase of the page note, and of the document note naming the pages,
when region OCR fell back to reading the whole page (D-60).

NOT an evidence marker: a page read whole has lost nothing, it cost reading
time. What follows the phrase is the cause, as codes from
:data:`WHOLE_PAGE_CAUSES`, each with its count where it has one. A page whose
whole-page reading could not be done either carries :data:`M_OCR_PAGE`
besides, and that is the marker."""

WHOLE_PAGE_CAUSES: tuple[str, ...] = ("unmeasurable", "cap", "floor", "ocr-error",
                                      "uncovered")
"""Why a page was read whole (D-60), in the order its notes list them.

* ``unmeasurable`` -- its image draws could not be measured, by the routing or
  again here, or measuring them again found none;
* ``cap`` -- regions past ``DOCIQ_MIXED_MAX_REGIONS`` (24) were not read;
* ``floor`` -- regions under ``_MIXED_MIN_REGION_PX`` on a side were not read;
* ``ocr-error`` -- a region's OCR raised (and the page is retried, see
  :func:`_ocr_pdf_regions`);
* ``uncovered`` -- drawn image area outside every region read, for a reason
  none of the above names.

The list names causes; it does not DECIDE anything. The decision is the
computed uncovered area, so a cause nobody has listed yet still reads the page
whole, and is called ``uncovered``."""


def _uncovered_draw_area(draws, read_rects):
    """For each box of ``draws``, its area in square points lying outside every
    box of ``read_rects``, which must be pairwise disjoint (regions merged
    :data:`_MERGE_GAP_PT` apart, cut outward by under a pixel, are).

    The computation D-60 turns on, kept apart so that it can be tested alone."""
    import numpy as np

    if not len(draws):
        return np.zeros(0)
    d = np.asarray(draws, dtype=float).reshape(-1, 4)
    area = (d[:, 2] - d[:, 0]) * (d[:, 3] - d[:, 1])
    if not len(read_rects):
        return area
    r = np.asarray(read_rects, dtype=float).reshape(-1, 4)
    w = np.minimum(d[:, None, 2], r[None, :, 2]) - np.maximum(d[:, None, 0], r[None, :, 0])
    h = np.minimum(d[:, None, 3], r[None, :, 3]) - np.maximum(d[:, None, 1], r[None, :, 1])
    inside = (np.clip(w, 0, None) * np.clip(h, 0, None)).sum(axis=1)
    return np.clip(area - inside, 0, None)


def _coverage_causes(draws, regions, status, read_rects, tolerance_pt2) -> tuple[str, ...]:
    """Why the regions read do not account for every draw, or ``()`` when they
    do (D-60).

    ``status[k]`` is ``None`` for a region that was read and otherwise the
    cause code it was not read for. The page is covered when the drawn area
    outside every region read adds up to at most ``tolerance_pt2``. When it is
    not, each uncovered draw is attributed to the region containing it; a draw
    no region contains, or one inside a region that was read, is ``uncovered``.
    """
    import numpy as np

    left = _uncovered_draw_area(draws, read_rects)
    if not len(left) or float(left.sum()) <= tolerance_pt2:
        return ()
    n = len(regions)
    counts = {c: sum(1 for s in status if s == c) for c in WHOLE_PAGE_CAUSES}
    found: set[str] = set()
    d = np.asarray(draws, dtype=float).reshape(-1, 4)
    rg = np.asarray(regions, dtype=float).reshape(-1, 4) if n else np.zeros((0, 4))
    eps = 1e-6
    for j in np.nonzero(left > eps)[0]:
        x0, y0, x1, y1 = d[j]
        home = np.nonzero((rg[:, 0] <= x0 + eps) & (rg[:, 1] <= y0 + eps)
                          & (x1 <= rg[:, 2] + eps) & (y1 <= rg[:, 3] + eps))[0]
        cause = status[int(home[0])] if len(home) else None
        found.add(cause if cause else "uncovered")
    out = []
    for c in WHOLE_PAGE_CAUSES:
        if c not in found:
            continue
        out.append(f"{c} ({counts[c]} of {n} image region(s))"
                   if c in ("cap", "floor", "ocr-error") else c)
    return tuple(out)


def _ocr_pdf_regions(raw: bytes, pages: list[int],
                     whole: frozenset[int] | set[int] = frozenset()) -> dict[int, _OcrPage]:
    """OCR only the IMAGE REGIONS of the given 0-based page indices (A-24), or
    the whole page when the regions read do not account for every picture
    (D-60). Pages in ``whole`` are read whole from the start: the routing could
    not measure them.

    These are pages that already have a substantive text layer, so the
    whole-page pass would re-read glyphs the page already carries correctly and
    hand the caller two readings of one line to reconcile. Dedup between a
    native line and its OCR is a similarity judgement — the OCR of a letterhead
    is *nearly* the native text, never equal to it — and a hand-tuned threshold
    is exactly the kind of bound this codebase is trying to stop shipping.

    Reading only the image regions keeps the text layer's own lines off OCR
    wherever the text layer lies beside its images. **It does not where typed
    text lies ON an image** -- a letter on full-page stationery, a stamp typed
    over a scan, a searchable scan's OCR layer: those words are inside the
    image's crop and are read a second time. Alex ruled D-58 that this stays:
    a mask that painted the text layer out before cropping lost scan content
    under a diagonal watermark and under a wrong OCR layer, with no marker, and
    duplication loses nothing. The page is named in a document note instead
    (:data:`IMAGE_TEXT_MAY_REPEAT`), from ``text_on_image``.

    **D-60: region OCR is used only when it accounts for every picture.** After
    the regions are read, the area of every image draw on the page (as
    :func:`_image_placements` measures it, clipped to the page) that lies
    outside every region actually read -- cut from the rendering and passed to
    OCR without an exception -- is COMPUTED (:func:`_coverage_causes`). Crops
    are cut outward to whole pixels, so a region that was read covers its draws
    exactly; above :data:`_COVERAGE_TOLERANCE_PX` of uncovered area, whatever
    the reason, the page is read whole instead: the page's rendering, the one
    already made, OCR'd exactly as :func:`_ocr_pdf_pages` reads a scan. That
    reading REPLACES the region texts (it holds them), and it is the page's
    image text: the caller places it after the text layer's opening lines and
    marks it with ``image_line_span`` like any image text, so the text layer is
    kept and D-49's locator rule is unchanged. The cause is carried in
    ``whole``. Regions past the cap, under the size floor, or whose OCR raised
    are the causes known today; the computation, not that list, decides, so a
    way of drawing a picture nobody has thought of costs reading time rather
    than evidence.

    **A region whose OCR raised** is counted in ``raised``. The page is still
    read whole, so this reading holds every picture, but the result differs
    from a calm run's region reading, so the caller marks the page with the
    TRANSIENT :data:`M_OCR_PAGE` and the walker re-reads the document alone, as
    it does a scan whose OCR raised (D-51 round-4 review: region failures were
    FINAL and never retried).

    ``failed`` means the page could not be read at all: its rendering raised, or
    the whole-page reading did. Whatever regions did read are kept.

    **The page is rendered ONCE and sliced**, for the reason ``_band_tiles``
    records: a per-region ``get_pixmap`` clip re-decodes the page's embedded
    image every time, and on this corpus a page can be a 230 MB photograph. The
    same rendering is what a whole-page reading reads.
    """
    import math

    import fitz  # pymupdf
    import numpy as np

    out: dict[int, _OcrPage] = {}
    chunk_n = 16
    pool = _ocr_page_pool()
    with fitz.open(stream=raw, filetype="pdf") as doc:
        for i in pages:
            if not 0 <= i < len(doc):
                # A page MuPDF does not have: it can be neither measured nor
                # rendered, so it cannot be read, whole or in part.
                out[i] = _OcrPage(failed=True, whole=("unmeasurable",))
        idxs = [i for i in pages if 0 <= i < len(doc)]
        for c0 in range(0, len(idxs), chunk_n):
            plans: dict[int, dict] = {}
            for i in idxs[c0:c0 + chunk_n]:
                causes: list[str] = []
                regions: list = []
                try:
                    page = doc[i]
                    draws: list = []
                    if i in whole:
                        causes.append("unmeasurable")
                    else:
                        try:
                            draws = _image_placements(page)
                            regions = _merge_boxes(draws, tuple(page.rect))
                        except Exception:
                            draws, regions = [], []
                        if not regions:
                            # The routing measured image content here. Finding
                            # none now is a disagreement between two readings,
                            # not a page without pictures.
                            causes.append("unmeasurable")
                    arr = _page_array(page)
                    h, w = arr.shape[:2]
                    prect = page.rect
                    sx, sy = w / prect.width, h / prect.height
                    status: list[str | None] = []
                    tiles = []
                    for k, r in enumerate(regions):
                        if k >= _MIXED_MAX_REGIONS:
                            status.append("cap")
                            continue
                        # Outward to whole pixels, so the crop holds every
                        # pixel of every draw in the region.
                        x0 = max(0, min(w, math.floor((r[0] - prect.x0) * sx)))
                        y0 = max(0, min(h, math.floor((r[1] - prect.y0) * sy)))
                        x1 = max(0, min(w, math.ceil((r[2] - prect.x0) * sx)))
                        y1 = max(0, min(h, math.ceil((r[3] - prect.y0) * sy)))
                        if x1 - x0 < _MIXED_MIN_REGION_PX or y1 - y0 < _MIXED_MIN_REGION_PX:
                            status.append("floor")  # smaller than a legible glyph
                            continue
                        status.append(None)
                        cut = (prect.x0 + x0 / sx, prect.y0 + y0 / sy,
                               prect.x0 + x1 / sx, prect.y0 + y1 / sy)
                        tiles.append((k, cut, np.ascontiguousarray(arr[y0:y1, x0:x1])))
                    plans[i] = {
                        "page": page, "arr": arr, "draws": draws, "regions": regions,
                        "status": status, "causes": causes,
                        "tolerance": _COVERAGE_TOLERANCE_PX / (sx * sy),
                        "futs": [(k, cut, pool.submit(_ocr_array, t)) for k, cut, t in tiles],
                    }
                except Exception:
                    # The page could not even be rendered: nothing, whole or in
                    # part, can be read from it, as for a scan that will not
                    # rasterize.
                    out[i] = _OcrPage(failed=True, regions=len(regions),
                                      whole=tuple(causes))
            for i, plan in plans.items():
                texts: list[str] = []
                confs: list[float] = []
                read_rects: list = []
                yielded: list = []
                raised = 0
                for k, cut, f in plan["futs"]:
                    try:
                        text, cs = f.result()
                    except Exception:
                        raised += 1
                        plan["status"][k] = "ocr-error"
                        continue
                    read_rects.append(cut)
                    if text.strip():
                        texts.append(text)
                        yielded.append(cut)
                    confs.extend(cs)
                causes = tuple(plan["causes"]) or _coverage_causes(
                    plan["draws"], plan["regions"], plan["status"], read_rects,
                    plan["tolerance"])
                plan.update(texts=texts, confs=confs, yielded=yielded, raised=raised,
                            causes=causes)
                if causes:
                    plan["whole_fut"] = pool.submit(_ocr_array, plan["arr"])
            for i, plan in plans.items():
                page = plan["page"]
                n_regions = len(plan["regions"])
                region_text = "\n".join(plan["texts"])
                fut = plan.get("whole_fut")
                if fut is None:
                    out[i] = _OcrPage(text=region_text, confs=tuple(plan["confs"]),
                                      regions=n_regions, raised=plan["raised"],
                                      text_on_image=_text_on_images(page, plan["yielded"]))
                    continue
                try:
                    text, cs = fut.result()
                except Exception:
                    out[i] = _OcrPage(text=region_text, confs=tuple(plan["confs"]),
                                      failed=True, regions=n_regions,
                                      whole=plan["causes"], raised=plan["raised"],
                                      text_on_image=_text_on_images(page, plan["yielded"]))
                    continue
                if text.strip():
                    out[i] = _OcrPage(text=text, confs=tuple(cs), regions=n_regions,
                                      whole=plan["causes"], raised=plan["raised"],
                                      text_on_image=_text_on_images(page, [tuple(page.rect)]))
                else:
                    # Read whole and found nothing: whatever the regions read
                    # is kept rather than replaced by nothing.
                    out[i] = _OcrPage(text=region_text,
                                      confs=tuple(plan["confs"]) + tuple(cs),
                                      regions=n_regions, whole=plan["causes"],
                                      raised=plan["raised"],
                                      text_on_image=_text_on_images(page, plan["yielded"]))
            plans.clear()  # release the renderings before the next chunk
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
    """Share of one page covered by the raster images it draws, and whether it
    draws any.

    Transcribed from `tools/measure_sections.py`, deliberately including its
    limitation: overlapping images are not de-overlapped, so the share is an
    UPPER bound. That is the direction the probe measured Q3's figures with, and
    the shipped engine has to agree with the measurement that justified it — a
    tidier implementation here would silently invalidate 1,308 pages of
    published number.

    **Where it no longer agrees with that tool, on purpose** (D-51 round-2 and
    round-3 reviews): it sums every DRAW (:func:`_image_placements`, which says
    what a draw is), where the tool took each image object's first placement and
    saw no inline image. The tool's reading left a full-page scan drawn after its
    own thumbnail, or stored inline, measured as a thumbnail or as nothing, and
    unread with no note. Each draw is clipped to the page, an image used only
    in a soft mask is not a draw, and a tiling pattern counts the area it fills.
    The acceptance-corpus pages these changes move were counted when they were
    made; those figures belong to the decision register, not to this docstring.

    **It is a SUM, not the area the page shows.** The same image drawn twice at
    one place counts twice, and so do overlapping images. That is the upper
    bound this measure has always been, kept because the error it makes is to
    read a page, never to leave one unread without a note. So the extractor's
    notes and the setup screen, which quote it, say the drawn areas ADD UP to
    a share of the page, not that they cover it. **Not Tier 3's evidence
    text**, which still says a share of the page is "covered by an image", as
    it has since before D-51: it is section evidence that approvals are
    reviewed against, and rewording it is the taxonomy work's decision, not
    this measure's (D-51 round-4 review, C).

    **Raises** when the page cannot be interpreted, as
    :func:`_image_placements` does, rather than answering "no image": the
    routing marks such a page :data:`M_IMAGE_UNMEASURED`, and :func:`pdf_spans`
    decides for Tier 3.
    """
    boxes = _image_placements(page)
    if not boxes:
        return 0.0, False
    page_area = abs(page.rect.get_area())
    if page_area <= 0:
        return 0.0, True
    covered = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes)
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
            try:
                share, has_image = _page_image_share(doc[index])
            except Exception:
                # Not measurable (D-51 round 3). Tier 3 has no note to write;
                # extraction has already marked this page M_IMAGE_UNMEASURED, or
                # OCR'd it whole. "No image" would let Tier 3 call a page it
                # could not read a blank page, so the answer is "an image of
                # unknown size": never Blank, never Photograph.
                share, has_image = 0.0, True
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
    if need and opt.ocr_enabled:
        if not ocr_available():
            notes.append(
                f"{len(need)} page(s) have no usable text layer and OCR is "
                f"unavailable: {ocr_models_present()[1]}")
        else:
            try:
                ocr_by_page = _ocr_pdf_pages(raw, need)
            except Exception as exc:
                notes.append(f"{M_OCR_DOC}: {exc}")
    elif need and not opt.ocr_enabled:
        notes.append(f"{len(need)} page(s) have no usable text layer; OCR disabled")

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
    scans: set[int] = set()
    unmeasured: list[int] = []
    try:
        import fitz  # pymupdf

        with fitz.open(stream=raw, filetype="pdf") as _geom:
            for i in range(n):
                if i in routed:
                    continue  # already going to OCR whole-page
                # PER PAGE (D-51 round 3). `_page_image_share` used to swallow
                # an interpreter error and answer "no image", so a page MuPDF
                # could not interpret came out NATIVE with no note; and one bad
                # page raising here would have left every later page unmeasured.
                # A page MuPDF does not have at all is unmeasured too.
                try:
                    if i >= _geom.page_count:
                        raise IndexError(i)
                    share, has_image = _page_image_share(_geom[i])
                except Exception:
                    unmeasured.append(i)
                    continue
                if has_image and share >= PHOTO_MIN_IMAGE_AREA_SHARE:
                    mixed.append(i)
                    if share >= _SCAN_MIN_IMAGE_SHARE:
                        scans.add(i)  # D-54: a scan, whatever its stamp says
    except Exception as exc:
        # Geometry is how this page class is FOUND. If it cannot be measured we
        # do not know whether any page carries unread image content, and saying
        # nothing is the failure mode this amendment exists to close.
        notes.append(f"{M_IMAGE_UNREAD}: image geometry could not be measured "
                     f"({sanitize_message(str(exc))})")
    skipped: frozenset[int] = frozenset()
    if mixed or unmeasured:
        # "images whose drawn areas add up to", not "image content covering":
        # the share is the SUM over every image the page draws, overlaps and
        # repeated draws included (`_page_image_share`), not the area it shows.
        if not opt.ocr_enabled:
            if mixed:
                notes.append(f"{M_IMAGE_UNREAD}: {len(mixed)} page(s) carry images "
                             f"whose drawn areas add up to "
                             f"{PHOTO_MIN_IMAGE_AREA_SHARE:.0%} or more of the page "
                             f"beside their text layer; OCR disabled")
        else:
            if opt.skip_images_on_text_pages:
                # A-25 (D-51): a quick first pass leaves these images unread ON
                # PURPOSE. After "OCR disabled", the wider reason, which keeps
                # its own sentence; before the engine check, because a run that
                # reads nothing needs no engine to say so. The pages are named
                # here since page notes never reach the processing log, and the
                # setup screen promises that every page skipped is listed.
                #
                # Except a scan (D-54): images adding up to nearly the whole
                # page are read below exactly as a reading run reads them, so a
                # typed stamp in its text layer cannot decide that it is not.
                skipped = frozenset(i for i in mixed if i not in scans)
                if skipped:
                    notes.append(
                        f"{M_IMAGE_UNREAD}: {len(skipped)} page(s) carry images "
                        f"whose drawn areas add up to "
                        f"{PHOTO_MIN_IMAGE_AREA_SHARE:.0%} or more of the page "
                        f"beside their text layer, left unread because this run "
                        f"skips pictures beside a text layer (a page whose drawn "
                        f"image areas add up to {_SCAN_MIN_IMAGE_SHARE:.0%} or "
                        f"more is read as a scan): page(s) "
                        + ", ".join(str(i + 1) for i in sorted(skipped)))
            read = [i for i in mixed if i not in skipped]
            # D-60: a page whose image geometry could not be measured is read
            # whole, as a scan is, on either setting. The quick pass skips
            # only pictures it MEASURED as beside a text layer and short of a
            # scan; an unmeasured page could be a scan, and reading it costs
            # time where leaving it costs evidence. A skipped page is never in
            # this list, so no fallback brings one back.
            to_read = sorted(set(read) | set(unmeasured))
            if to_read and not ocr_available():
                if read:
                    notes.append(f"{M_IMAGE_UNREAD}: {len(read)} page(s) carry an image "
                                 f"beside their text layer and OCR is unavailable: "
                                 f"{ocr_models_present()[1]}")
            elif to_read:
                try:
                    region_by_page = _ocr_pdf_regions(raw, to_read, frozenset(unmeasured))
                except Exception as exc:
                    # Transient, as the whole-page pass's own failure is: the
                    # walker re-reads the document alone (D-51 round 4).
                    notes.append(f"{M_OCR_DOC} (image regions): "
                                 f"{sanitize_message(str(exc))}")

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
    region_failed: list[int] = []
    read_whole: list[tuple[int, tuple[str, ...]]] = []
    n_region_blank = 0
    may_repeat: list[int] = []
    unmeasured_pages = frozenset(unmeasured)
    for i in range(n):  # strictly by index — never by OCR completion order
        text, kind, confs = native[i], PageKind.NATIVE, None
        page_notes: tuple[str, ...] = ()
        if i in unmeasured_pages:
            page_notes = (M_IMAGE_UNMEASURED,)
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
        # --- A-24: merge the text read from the page's image regions. Placed
        # after the text layer's opening lines, NOT appended after it: appending
        # moved the page's own Bates stamp out of the zone and could leave a
        # foreign stamp from the image as the only one there. The measurement is
        # on :func:`_merge_image_text`. Typed text lying on an image is in the
        # image's region and comes back here a second time (D-58).
        region = region_by_page.get(i)
        if region is not None:
            if i in unmeasured_pages and not region.failed:
                # Read whole (D-60): whatever its geometry, nothing on the page
                # was left unread, so the unmeasured marker no longer applies.
                page_notes = tuple(n for n in page_notes if n != M_IMAGE_UNMEASURED)
            if region.text.strip():
                text, image_span = _merge_image_lines(text, region.text)
                kind = PageKind.MIXED
                confs = (confs or []) + list(region.confs)
                n_mixed += 1
                if region.text_on_image:
                    may_repeat.append(i)
            elif not region.failed:
                # Read, and there was no text in it. That is a COMPLETE answer,
                # not a gap — a site photograph carries no words — so it gets a
                # counted disclosure rather than an evidence marker. Marking it
                # would flag every genuine photograph in the production as lost
                # evidence, and a warning that fires on the normal case teaches
                # an operator to stop reading warnings.
                n_region_blank += 1
            causes = "; ".join(region.whole)
            if region.failed:
                # We knew there was image content, tried to read it, and could
                # not, not even whole. TRANSIENT, as a scan that will not
                # rasterize is (D-51 round 4): the same exception under load is
                # the case the walker's serial retry exists for.
                page_notes = page_notes + (
                    f"{M_OCR_PAGE} to rasterize or read"
                    + (f" the page whole ({causes})" if causes else ""),)
                region_failed.append(i)
            elif region.whole:
                page_notes = page_notes + (f"page {IMAGE_READ_WHOLE}: {causes}",)
                read_whole.append((i, region.whole))
            if region.raised and not region.failed:
                # Read whole, so nothing is missing, but a calm run reads the
                # regions: without a transient marker a load event would be
                # written into the deliverable (D-51 round-4 review).
                page_notes = page_notes + (
                    f"{M_OCR_PAGE} on {region.raised} image region(s); the page "
                    f"was read whole instead",)
        if i in skipped:
            # A-25: the page stays NATIVE and says, on the page, why its image
            # is not in the text.
            page_notes = page_notes + (M_IMAGE_SKIPPED,)
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
    if may_repeat:
        # D-58: disclosed, not marked. It says only that typed words may come
        # back twice; whether anything was lost is said by the notes below,
        # not here (D-51 round 4: "nothing is left out" stood on a page that
        # had lost lines).
        notes.append(f"{len(may_repeat)} page(s) {IMAGE_TEXT_MAY_REPEAT} "
                     f"(D-58): page(s) "
                     + ", ".join(str(i + 1) for i in may_repeat))
    if read_whole:
        # D-60: disclosed, not marked; the page cost reading time and lost
        # nothing. Page notes never reach the processing log, so the pages and
        # their causes are named here.
        notes.append(f"{len(read_whole)} page(s) {IMAGE_READ_WHOLE}: page(s) "
                     + ", ".join(f"{i + 1} ({', '.join(c.split(' ')[0] for c in why)})"
                                 for i, why in read_whole))
    if region_failed:
        notes.append(f"{M_OCR_PAGE}: {len(region_failed)} page(s) carrying image "
                     f"content beside their text layer could not be rasterized or "
                     f"read, not even whole: page(s) "
                     + ", ".join(str(i + 1) for i in region_failed))
    still_unmeasured = [i for i in unmeasured
                        if M_IMAGE_UNMEASURED in pages[i].notes]
    if still_unmeasured:
        notes.append(f"{M_IMAGE_UNMEASURED} on {len(still_unmeasured)} page(s) beside "
                     f"their text layer, so whether they carry image content "
                     f"that was not read is not known: page(s) "
                     + ", ".join(str(i + 1) for i in still_unmeasured))
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


def _extract_docx(raw: bytes, opt: ExtractOptions) -> tuple[list[PageRecord], list[str]]:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover — declared
        raise ExtractionError("Word support requires 'python-docx'.") from exc
    try:
        document = docx.Document(io.BytesIO(raw))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
    except Exception as exc:
        raise ExtractionError(f"Could not read Word document: {exc}") from exc
    note = "DOCX carries no page boundaries; emitted as one synthetic page"
    return synthetic_pages(["\n".join(parts)], notes=(note,)), [note]


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
