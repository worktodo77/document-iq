"""THE REAL PIPELINE, on the GUI's side of the seam.

:mod:`dociq.gui.pipeline` defines what the GUI is allowed to ask for; this
module answers it with :func:`dociq.pipeline.run`. It is the Sprint-2 swap the
seam was built for, and after it the GUI still knows nothing about ``ingest``,
``identify``, ``docid``, ``profiles``, ``emit`` or ``verify``.

**Why it does not live in ``dociq/gui/``.** The obvious home is
``gui/real_pipeline.py`` and it is the wrong one: ``tests/test_import_graph.py``
enforces the pagemodel freeze's Track-C rule by scanning every file under
``src/dociq/gui/`` for an import of a pipeline package, and this module cannot
exist without six of them. Putting it there would mean weakening the check that
keeps the GUI honest — trading a permanent guarantee for a file location. So the
adapter sits on the pipeline side and imports the seam's presentation records,
which is the direction the freeze allows: the GUI depends on nothing new, and
``get_pipeline()`` reaches this module through a function-local import so merely
importing the GUI still pulls in no pipeline code.

Two rules from the seam's docstring govern everything here:

1. Everything crossing is a frozen contract object or one of the seam's small
   presentation records.
2. **The GUI computes nothing the pipeline is responsible for.** Every figure
   below — the token estimates, the reduction plan's lever savings, the
   reconciliation — is read from what the run produced or measured here, never
   re-derived in a widget.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dociq import pipeline as core
from dociq.contracts import (
    DocIQError,
    Disposition,
    RunResult,
    canonical_tokens,
    matter_key,
    recognition_fingerprint,
)
from dociq.gui.pipeline import (
    LEVER_RECOGNIZED,
    OmissionApproval,
    LEVER_EXPERT,
    BatesConfirm,
    BatesProposal,
    FolderPreview,
    ProgressEvent,
    Reconciliation,
    ReconciliationRow,
    ReductionLever,
    PackageResult,
    ReductionPlan,
    RunOutcome,
    RunRequest,
    TokenBasis,
    TokenEstimate,
    config_from,
)
from dociq.ingest import walker
from dociq.operator import operator_stamp
from dociq.sections.model import ApprovedOmission
from dociq.sections.normalize import family_key
from dociq.sections.templates import PROGRESS_REPORT, template_by_id
from dociq.verify import tokens as vt

__all__ = [
    "RealPipeline",
    "SECONDS_PER_GB_OCR_ON",
    "SECONDS_PER_GB_OCR_OFF",
    "seconds_per_gb",
    "measured_basis",
    "ESTIMABLE_EXTENSIONS",
]

# ---------------------------------------------------------------------------
# Refusing an omission
# ---------------------------------------------------------------------------


class OmissionRefused(DocIQError):
    """An omission was offered against a family the template does not define, or
    one it defines and refuses to offer.

    Was ``ProfileError`` until D-38 deleted the profile system. The refusals it
    carries were never about profiles — they are D-34's, about who may approve
    what — and they outlived the exception class they happened to borrow."""




# ---------------------------------------------------------------------------
# The measured throughput figures — one per OCR setting
# ---------------------------------------------------------------------------

MEASURED_GIGABYTES = 2.6
"""The D-12 corpus, to two significant figures — the denominator of both rates
below. "GB" is not stated as decimal or binary, which is about 7% of ambiguity
before anything else."""

MEASURED_SECONDS_OCR_ON = 6182.4
MEASURED_SECONDS_OCR_OFF = 3046.7

SECONDS_PER_GB_OCR_ON = MEASURED_SECONDS_OCR_ON / MEASURED_GIGABYTES
"""≈2,378 s/GB — the rate for the SHIPPED DEFAULT, which is OCR on.

From the decision register, "§10 measured again, from scratch, WITH OCR
(2026-08-02)": the D-12 corpus, **OCR enabled, from scratch, not resumed,
through** :class:`RealPipeline` **itself — 6,182.4 s = 103.0 min** for 368
documents / 18,556 pages.

**Why this replaced the OCR-off rate.** :class:`RealPipeline` constructs with
``ocr_enabled=True``, so until 2026-08-03 the figure beside the primary action
was derived from a run that did no OCR: ≈51 minutes for the corpus whose one
measured OCR-on run took 103. The old docstring's "the ONLY wall-clock rate
DocIQ has measured end to end" was true when it was written and stopped being
true when the acceptance run landed. Both statements are corrected here rather
than one number being swapped under the other.

**It is one run on one machine, and the machine was BUSY.** The register is
explicit: another agent's OCR job and repeated ``pytest`` processes ran
throughout, sampled CPU load was 100% for most of the window, and 103.0 minutes
therefore *corroborates* the ≈100-minute upper bound rather than establishing an
idle-machine rate. The estimate this feeds is consequently pessimistic on an idle
machine and roughly right on a working one — the opposite direction of error from
the one it replaced, and the safer one to be wrong in beside a button the
operator is about to press.

**The scanned share is 2.2%, and that is what "OCR on" cost here.** A folder of
scanned productions puts a far larger share of pages through OCR at ≈2.0–2.3×
extraction, and nothing knowable before the walk distinguishes the two. This rate
is still optimistic by construction on heavily scanned material — less so than
the OCR-off rate was, not immune.

Which is why :func:`RealPipeline.preview_folder` returns 0 — "no estimate", and
the screen then says nothing — for any folder outside the shape these were
measured on, rather than extrapolating a number it cannot defend.
"""

SECONDS_PER_GB_OCR_OFF = MEASURED_SECONDS_OCR_OFF / MEASURED_GIGABYTES
"""≈1,172 s/GB — the rate when OCR is turned OFF.

The same corpus, OCR disabled, from scratch, on an **idle** machine: 3,046.7 s
(decision register, "§10 restated against a completed full-corpus run",
2026-07-31). Kept rather than deleted because ``RealPipeline(ocr_enabled=False)``
is a supported construction and applying the OCR-on rate to it would overstate
the wait by a factor of two — the same defect as the one being fixed, pointing
the other way.

The two rates are **not** two measurements of one quantity and must never be
averaged or reconciled: they time different work. Their ratio, 2.03, is
independently consistent with the register's ≈2.0–2.3× figure for OCR's share of
extraction over the identical first 62 documents.
"""


def seconds_per_gb(ocr_enabled: bool) -> float:
    """The measured rate for the run that is actually about to happen.

    A function rather than a module constant because there is no single answer:
    the two rates time different work, and picking one for both settings is what
    made the shipped estimate ~2× low. The caller passes the setting the run will
    use, so the branch cannot drift from the run.
    """
    return SECONDS_PER_GB_OCR_ON if ocr_enabled else SECONDS_PER_GB_OCR_OFF


def measured_basis(ocr_enabled: bool) -> str:
    """Where the rate in use came from, in one sentence, for display."""
    if ocr_enabled:
        return (
            "one measured run: the full MODEC/Petrobras corpus, OCR enabled, "
            "from scratch through RealPipeline — 6,182.4 s for 2.6 GB "
            "(decision register, §10 measured again 2026-08-02). The machine was "
            "under load throughout, so this corroborates the ≈100-minute upper "
            "bound rather than establishing an idle-machine rate."
        )
    return (
        "one measured run: the full MODEC/Petrobras corpus, OCR disabled, from "
        "scratch on an idle machine — 3,046.7 s for 2.6 GB (decision register, "
        "§10 restated 2026-07-31). OCR is not in this rate."
    )


_LEVER_ENGAGED: dict[Disposition, bool] = {
    Disposition.KEEP: False,
    Disposition.DROP: True,
}
"""Every :class:`~dociq.contracts.Disposition`, decided explicitly.

The §6 checklist renders this bool back out as a WORD, so an unhandled member
absorbed here becomes a wrong word on the expert's approval screen rather than
a missing feature. Total by the tripwire below."""

_UNMAPPED_DISPOSITIONS = set(Disposition) - set(_LEVER_ENGAGED)
if _UNMAPPED_DISPOSITIONS:  # pragma: no cover — import-time tripwire
    raise AssertionError(
        "Disposition member(s) with no §6-checklist meaning: "
        + ", ".join(sorted(m.name for m in _UNMAPPED_DISPOSITIONS))
    )
del _UNMAPPED_DISPOSITIONS

ESTIMABLE_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".doc"})
"""The formats the measured corpus was made of (D-12: 298 PDF / 53 DOCX / 17
PPTX / 7 DOC). A folder of ``.msg`` or ``.xlsx`` is a different job at the same
byte count, and no run has timed one."""

ESTIMABLE_SHARE = 0.80
"""How much of a folder's BYTES must be in :data:`ESTIMABLE_EXTENSIONS` before
the rate is applied to it. Below this the folder is not the thing that was
measured, and the honest output is no estimate."""

ESTIMABLE_MAX_GB = MEASURED_GIGABYTES * 10
"""The rate is a single point, not a curve. Extrapolating it 200× is not
measurement, so a folder more than ten times the measured corpus gets no estimate
rather than a confident one. Not a silent cap: the screen shows nothing, which is
the documented meaning of zero."""


def _minutes_for(total_bytes: int, sized: dict[str, int], *,
                 ocr_enabled: bool = True) -> int:
    """Wall clock for this folder under :func:`seconds_per_gb`, or 0.

    ``ocr_enabled`` defaults to True because :class:`RealPipeline` does; a
    default of False here would reintroduce the ~2×-low estimate for every
    caller who did not think about it.

    Zero is the seam's documented "no estimate", and it is returned for every
    folder neither rate was measured on. See :data:`SECONDS_PER_GB_OCR_ON` for
    what the rates do and do not cover.
    """
    if total_bytes <= 0:
        return 0
    gigabytes = total_bytes / 1_000_000_000
    if gigabytes > ESTIMABLE_MAX_GB:
        return 0
    covered = sum(b for ext, b in sized.items() if ext in ESTIMABLE_EXTENSIONS)
    if covered / total_bytes < ESTIMABLE_SHARE:
        return 0
    return round(gigabytes * seconds_per_gb(ocr_enabled) / 60)


# ---------------------------------------------------------------------------
# Projections onto the seam's records
# ---------------------------------------------------------------------------

_NO_FIGURE_BAND = (
    vt.DEFAULT_BASIS.low_x100 / 100,
    vt.DEFAULT_BASIS.high_x100 / 100,
)


def _estimate(value, label: str) -> TokenEstimate:
    """The contract's token estimate, projected onto the seam's record.

    A read, not a computation (seam rule 2). ``provenance`` is the pipeline's own
    sentence — :meth:`dociq.verify.tokens.TokenEstimate.provenance_text` — carried
    across verbatim, so the figure on screen and the figure in ``run_summary.pdf``
    give the same account of themselves.
    """
    if value is None:
        return TokenEstimate(
            chars=0,
            ratio_low=_NO_FIGURE_BAND[0],
            ratio_high=_NO_FIGURE_BAND[1],
            structural_tokens=0,
            provenance=(
                f"no {label} figure: this run published nothing, so there is no "
                "corpus to measure. A number here would describe part of a "
                "folder and read as though it described all of it."
            ),
        )
    return TokenEstimate(
        chars=value.chars,
        ratio_low=value.ratio_low,
        ratio_high=value.ratio_high,
        structural_tokens=value.structural_tokens,
        provenance=value.provenance,
        ratio_refuted=value.ratio_refuted,
    )


def _reconciliation(result: RunResult) -> Reconciliation | None:
    """§5's report, projected. ``None`` when no master index was supplied —
    which the contract distinguishes from a reconciliation that found nothing,
    and so must this."""
    report = result.reconciliation
    if report is None:
        return None
    return Reconciliation(
        matched=report.matched,
        rows=tuple(
            ReconciliationRow(r.category, r.doc_id, r.filename, r.detail)
            for r in report.rows
        ),
    )


def _family_of(section: str, template, project_tokens: tuple[str, ...]):
    """The template family a recognized section belongs to, or ``None``.

    ``None`` is a real and common answer — 522 distinct section families were
    measured in the real corpus against a template that names eighteen — and it
    means the row is shown and cannot be engaged. Under §1's asymmetry that is
    the correct direction: a section the template does not know is a section
    nobody ruled on, and an unruled section keeps.
    """
    if template is None:
        return None
    key = family_key(section, project_tokens)
    return None if key is None else template.classify(key)


def _template_lever(
    name: str, tokens: int, pages: int, dropped_tokens: int, dropped_pages: int,
    *, family, tier: str, approval,
) -> ReductionLever:
    """One waterfall row for one recognized section (D-14, D-34, A-20).

    Three outcomes, and which one applies is the template's to decide rather
    than the screen's:

    * **no family** — the template does not name this section. Recognized,
      shown, not engageable.
    * **family with** ``offer=False`` — the template names it and refuses to
      offer it: the executive summary, the critical path narrative, the weather
      log, the timesheets. Recognized, shown, not engageable. §4 grades these
      HIGH risk and §1 is why the refusal is structural rather than advisory.
    * **family with** ``offer=True`` — a lever, carrying its risk, its tier and
      its rationale, engaged only if an approval names a person.

    ``note`` is the family's own ``rationale`` — §5.3's stated cost, required on
    every family precisely so this row can never be drawn without one. ``rule``
    is the family's matching patterns, which is what A-11b asked for and what
    the old profile pattern used to supply: what a drop actually catches, not
    merely that a drop exists.
    """
    if family is None or not family.offer:
        label = name if family is None else family.display_name
        return ReductionLever(
            key=name, label=label, tokens=tokens, pages=pages,
            kind=LEVER_RECOGNIZED, engaged=False, estimated=False,
            family_id=family.family_id if family is not None else "",
            risk=family.risk.value if family is not None else "",
            tier=tier,
            # Passed on this branch too. A family that is never OFFERED still
            # has patterns, and they are what an expert reads to check that the
            # right pages were recognized as the weather log; a blank here would
            # be the A-11b defect on the eight rows where getting recognition
            # wrong is most expensive. Empty only where there is genuinely no
            # family, and that emptiness is a fact rather than a dropped field.
            rule=" | ".join(family.patterns) if family is not None else "",
            # Empty, and it is a FACT rather than a dropped field: this row can
            # never be engaged, so no approval can exist for it and no name may
            # appear against it. D-34 — the approver field never holds a
            # fiction, and a recognized-never-offered section is the one row
            # where a name would be the plainest fiction available.
            approved_by="",
            note=(family.rationale if family is not None else
                  "Recognized, and no template family names this section — so "
                  "there is nothing to approve and the pages are kept."),
        )
    engaged = approval is not None
    shown_tokens, shown_pages = (
        (dropped_tokens, dropped_pages) if dropped_pages else (tokens, pages)
    )
    label = family.display_name
    if dropped_pages and dropped_pages != pages:
        label = f"{label} (part — {dropped_pages:,} of {pages:,} pages)"
    return ReductionLever(
        key=name, label=label, tokens=shown_tokens, pages=shown_pages,
        kind=LEVER_EXPERT, engaged=engaged, estimated=False,
        family_id=family.family_id, risk=family.risk.value, tier=tier,
        rule=" | ".join(family.patterns), note=family.rationale,
        approved_by=approval.approved_by if approval is not None else "",
    )


def _plan(result: RunResult, before: TokenEstimate,
          *, template=None,
          approvals: tuple[ApprovedOmission, ...] = (),
          project_tokens: tuple[str, ...] = ()) -> ReductionPlan | None:
    """The D-14 waterfall, from figures this run counted.

    One lever per section a profile ruled on, and the lever's saving is the
    measured structure of the pages in that section — counted, not projected, so
    every :attr:`~dociq.gui.pipeline.ReductionLever.estimated` flag here is False.
    The pages are the run's own; the measurement is
    :func:`dociq.verify.tokens.measure`, the same function whose per-text sum
    produces ``tokens_before``, so the levers and the headline cannot disagree.

    **There is no automatic lever, and its absence is the honest answer.**
    :data:`~dociq.gui.pipeline.LEVER_AUTOMATIC` is for savings the tool makes
    mechanically — exact-hash duplicates, page furniture. DocIQ *detects*
    exact-hash duplicates (§4 Stage 1, :func:`dociq.ingest.walker.duplicate_groups`)
    and warns about them; it removes neither them nor page furniture. Every page
    of every duplicate copy is extracted, written to ``clean_text/`` under its own
    Doc ID and counted in the accounting identity. A lever claiming that saving
    would be claiming a reduction the deliverable does not contain — and it would
    be subtracted from the figure the operator reads as "what Claude has to
    swallow". The Sprint-1 mock's 14% automatic row was labelled ILLUSTRATIVE
    for exactly this reason; the real adapter shows nothing rather than
    inheriting it. See the verification note for the two ways out.
    """
    # Four running totals per section, not three: the whole section's tokens and
    # pages, AND the dropped part's. See below — the dropped part is what the
    # lever removes, and it is not always all of the section.
    sections: dict[str, list[int]] = {}
    # The tier is per SECTION here, and a section recognized by two tiers in one
    # corpus keeps the STRONGEST — sorting the enum values puts `t1_outline`
    # ahead of `t3_page_class`, which is the order §3 states and the order the
    # tiers are declared in. Showing the weaker one would overstate nothing and
    # understate the evidence, but it would still be the wrong sentence on the
    # row the expert is deciding from.
    tiers: dict[str, set[str]] = {}
    for doc in result.documents:
        for page in doc.pages:
            if not page.section:
                continue
            row = sections.setdefault(page.section, [0, 0, 0, 0])
            tok = vt.measure(page.text).pretokens
            row[0] += tok
            row[1] += 1
            if page.section_tier is not None:
                tiers.setdefault(page.section, set()).add(page.section_tier.value)
            if page.disposition is Disposition.DROP:
                row[2] += tok
                row[3] += 1

    by_family = {a.family_id: a for a in approvals}
    levers = []
    for name, totals in sorted(sections.items()):
        family = _family_of(name, template, project_tokens)
        levers.append(_template_lever(
            name, *totals, family=family,
            tier="; ".join(sorted(tiers.get(name, ()))),
            approval=by_family.get(family.family_id) if family else None,
        ))
    levers = tuple(levers)
    if not levers:
        return None
    return ReductionPlan(
        full_tokens=before.structural_tokens,
        levers=levers,
        # ``capacity`` is deliberately left at its default, and the probe in
        # tests/test_seam_population.py records that as a declared exemption
        # rather than an oversight: DIRECT_CONTEXT_TOKENS is D-21's ruled
        # reference line and the adapter has no second, run-specific figure to
        # put here. Passing the same constant explicitly would create a second
        # place the number lives (see its docstring — there are already three).
        basis=TokenBasis.of(before),
    )


# ---------------------------------------------------------------------------
# §4 Stage 3 — the operator's confirmation, across the seam (A-14)
# ---------------------------------------------------------------------------


def _proposal_for_gui(proposal, other_prefixes: tuple[str, ...] = ()) -> BatesProposal:
    """``identify.bates.BatesProposal`` → the seam's presentation record.

    A translation, not a pass-through, and the seam requires it: the detector's
    proposal carries a :class:`~dociq.identify.bates.BatesFormat`, and a format
    object on the GUI side would be a pipeline internal crossing the seam.

    What the operator is shown is chosen here rather than in a widget for the
    same reason every other figure is: **the operator cannot confirm a regex.**
    They can confirm "IICON 000123 — 1,842 of 1,910 pages, across 20
    documents", so :attr:`~dociq.gui.pipeline.BatesProposal.example` is a real
    locator read off a real page (``samples[0]``), never a rendering of the
    pattern. If the detector proposed a format it never saw an instance of,
    the example is empty and the screen says so rather than inventing one.
    """
    return BatesProposal(
        pattern=proposal.format.label,
        example=proposal.samples[0] if proposal.samples else "",
        documents=proposal.documents_matched,
        pages=proposal.pages_matched,
        coverage_pct=float(proposal.coverage_pct),
        # D-28's OWN CENSUS, and emphatically NOT `proposal.alternatives`.
        #
        # The seam says this field means "other prefixes seen in the same
        # matter" and that a non-empty value means the production is
        # multi-series — the condition D-28 refuses prefix repair on. Only
        # `identify.bates.matter_prefixes` answers that question: it applies the
        # same two bars a proposal has to clear. `BatesProposal.alternatives` is
        # `ranked[1:4]` with NO bar at all, and on the real MNFV production it
        # comes back as `Check 0001` and `retained 90095 49 00001` — two stray
        # lines in a single-series production. Rendering those as "this
        # production carries more than one stamp series" would be a false
        # statement about the record, made on the screen where the operator is
        # being asked to rule on exactly that. Measured, not reasoned: the first
        # draft of this adapter did it, and the client-corpus run is what caught
        # it.
        alternatives=other_prefixes,
    )


def _translate(confirm: BatesConfirm | None):
    """Wrap a seam-side confirmation so the PIPELINE never sees the GUI's type.

    ``None`` in, ``None`` out — and that is load-bearing. A wrapper that turned
    "no operator" into a callable would make every headless run look attended,
    which is the failure mode this whole finding is about, inverted.
    """
    if confirm is None:
        return None

    def ask(proposal, other_prefixes: tuple[str, ...] = ()) -> bool:
        return bool(confirm(_proposal_for_gui(proposal, other_prefixes)))

    return ask


def stored_bates_pattern(output_root: str | Path | None) -> str | None:
    """The Bates format this matter's LAST run confirmed, or ``None``.

    §4 says the format is "confirmed once per document set, then applied
    automatically". Without this the second half of that sentence was false
    through the GUI: :func:`dociq.gui.pipeline.config_from` builds a
    ``RunConfig`` from the setup screen alone, which carries no
    ``bates_pattern``, so every re-run of a matter would put the same question
    to the same operator again — and a tool that re-asks a ruling it was already
    given teaches the operator to click past it.

    Read from ``processing_log.json`` in the output root, which is where the
    completed run recorded its effective configuration. The same folder and the
    same precedent as ``PipelineOptions.previous_ledger``: what a matter carries
    is what its last complete run left in its output folder.

    Every failure is silent and returns ``None`` — a missing, unreadable or
    older log means "not confirmed yet", which re-asks. That is the safe
    direction: the cost of failing to read it is one dialog, and the cost of
    guessing is a locator regime nobody approved. A pattern that is present but
    unreadable is NOT swallowed here; :func:`dociq.pipeline.run` raises on it,
    because a stored confirmation that cannot be enforced must stop the run.
    """
    if not output_root:
        return None
    log = Path(output_root) / "processing_log.json"
    try:
        import json

        raw = json.loads(log.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    for section in (raw.get("content"), raw):
        if isinstance(section, dict):
            config = section.get("config")
            if isinstance(config, dict) and config.get("bates_pattern"):
                return str(config["bates_pattern"])
    return None


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class RealPipeline:
    """Implements :class:`dociq.gui.pipeline.PipelineAPI` against real runs."""

    def __init__(self, *, library_dir: str | Path | None = None,
                 ocr_enabled: bool = True,
                 template_id: str = "progress-report",
                 project_tokens: tuple[str, ...] = ()) -> None:
        self._library_dir = library_dir
        self._ocr_enabled = ocr_enabled
        self._template = template_by_id(template_id) or PROGRESS_REPORT
        """The section template every run of this adapter recognizes against.

        Defaulted to the one template DocIQ ships rather than to ``None``,
        because a template costs nothing to load and changes nothing on its own:
        D-34 makes it structurally incapable of dropping a page. What it buys is
        the checklist — recognized sections gain a name, a risk grade and a
        stated cost — and withholding that by default would leave the reduction
        feature invisible in the product that contains it."""

        self._project_tokens = tuple(project_tokens)
        """Matter tokens stripped before a family is matched (D-24).

        Empty by default and supplied per matter. Never a list of real project
        names living in the source: measured, 30.5% of the corpus's section
        vocabulary carries project-identifying text, and a default here would
        compile one client's vessel name into the product.

        **NO SCREEN SETS THIS, and saying so is the point.** The parameter
        reaches the run identity and the family matching; the setup screen has
        no field for it, so in the shipped GUI it is always empty. That is the
        A-12 shape — a capability declared, wired to the pipeline, and
        unreachable by the operator — and A-12 is the amendment this project
        shipped a permanently disabled button behind, so the gap is written down
        rather than left to be found.

        What it costs is bounded and it is in the safe direction: an unstripped
        `MV32 APPENDICES` matches no family, and a section with no family keeps.
        Measured, that is 30.5% of the vocabulary declining to match rather than
        matching wrongly. Nothing is dropped that should not be; some things that
        could be offered are not. Closing it is a setup-screen field and a
        `RunRequest` entry, and it is named in the Sprint-3 handoff."""
        self.library_issues: tuple[str, ...] = ()
        """Profile files in the library that could not be read, with the reason.

        Populated by :meth:`profiles`. **The seam has no channel for this** —
        ``disclosure()`` is reserved for saying a pipeline is not real, and a
        profile the run did not use must not enter ``RunResult.warnings``, which
        is hashed content. So it is recorded here, and the coordinator is asked
        for a way to put it on screen; see the verification note."""

        # There WAS a ``last_package_missing`` holding attribute here, carrying
        # the emit layer's report of what a package could not include. It is
        # GONE (Codex review #2, B-3), and so is the claim it was written under
        # — that it lived "where a screen can reach it". No screen ever reached
        # it. ``PackageResult.missing`` is on the seam now and is the only home:
        # a private attribute beside the seam is a place the GUI does not look,
        # and the test that guarded it asserted the attribute rather than the
        # returned record, so it passed while the user-visible path stayed
        # wrong.

    # -- the API ------------------------------------------------------------

    def disclosure(self) -> str:
        """Empty, and the emptiness is the point: the standing notice exists so
        a stand-in pipeline cannot be mistaken for a real one, and this is the
        real one."""
        return ""

    def set_omission(
        self, family_id: str, engaged: bool, matter: str, source_root: str = "",
        project_tokens: tuple[str, ...] = (),
    ) -> OmissionApproval | None:
        """Record or withdraw one expert-approved omission (D-34).

        **This is the capture point the ruling names.** Until this call, the
        template's contribution to a run is recognition and nothing else; after
        it, a named person has approved an omission on a named matter at a named
        time, and every drop-log line that omission produces will carry all
        three.

        The approver is :func:`dociq.operator.operator_stamp`'s reading of
        who is running the tool — the same stamp a saved profile carries, for
        the same reason. It is never a parameter: a caller that could pass a
        name could pass somebody else's.

        Withdrawal returns ``None`` and leaves nothing behind. An approval that
        was given and taken back before any run acted on it did not omit
        anything, and a record of it would put a person's name against an
        omission that never happened.

        Refuses a family the loaded template does not define, and refuses one it
        defines with ``offer=False``. The screen already locks those rows; this
        is the same refusal at the layer a screen cannot reach past, because
        "the widget will not send it" is not a guarantee, it is a hope about
        every future widget.
        """
        if not engaged:
            return None
        family = self._template.family(family_id) if self._template else None
        if family is None:
            raise OmissionRefused(
                f"no family {family_id!r} in template "
                f"{self._template.template_id if self._template else '(none)'} "
                "— an approval must name a section the template defines, or the "
                "drop log would attribute an omission to a rule nobody wrote"
            )
        if not family.offer:
            raise OmissionRefused(
                f"family {family_id!r} is recognized and never offered "
                f"({family.display_name}). {family.rationale}"
            )
        stamp = operator_stamp()
        if not source_root.strip():
            raise OmissionRefused(
                "an omission cannot be approved without the folder it is being "
                "approved on. The matter's NAME does not scope an approval — "
                "two clients with a 'Production' folder are two matters and one "
                "name — so a capture point with no root is one whose record "
                "nothing can check."
            )
        return OmissionApproval(
            family_id=family_id,
            approved_by=stamp.username,
            approved_at=stamp.saved_at,
            matter=matter,
            matter_root=matter_key(source_root),
            template_id=self._template.template_id,
            template_version=self._template.version,
            # The recognition configuration this ruling was given against
            # (Codex B-1). Canonical, so one review is one value however the
            # operator spelled the list.
            project_tokens=canonical_tokens(project_tokens),
            # The whole recognition configuration, not only the half we have
            # so far been bitten by. `ocr_ran` is this pipeline's own setting:
            # an approval reviewed on a run that read the scans is not an
            # approval for a run that did not.
            recognition=recognition_fingerprint(
                project_tokens=project_tokens,
                template_id=self._template.template_id,
                template_version=self._template.version,
                ocr_ran=self._ocr_enabled,
            ),
        )

    def template_families(
        self,
    ) -> tuple[tuple[ReductionLever, ...], TokenBasis, str]:
        """§6's checklist: which rules this profile carries, and where they came
        from. Amendment A-11's shape, implemented.

        **A-11 is APPLIED** (``docs/contracts/amendments.md``, 2026-08-01) and
        this method is on :class:`~dociq.gui.pipeline.PipelineAPI`. The previous
        docstring said it was "raised and not yet applied" and that Track E
        reached it only through an optional ``getattr`` hook; that was true when
        this module was written and stopped being true when the seam was
        amended. Track E's hook still probes rather than calling the Protocol
        method directly, because the loud empty state it renders is the right
        behavior for a stand-in that cannot supply the rules — but absence is
        no longer the expected case, and it is not the case here.

        **Every row is `estimated=True` and carries no figures**, because before
        a run there is nothing to count: these are the profile's rules, not this
        matter's pages. §6 step 2's "frequency across the sample, average page
        count" comes from a profiling run over a sample, which does not exist
        yet. Zero with `estimated=True` is the honest encoding the seam has —
        and it renders as "0 pages · 0 tokens (projected, not counted)", which
        understates the case. See the verification note: the wording is Track
        E's to fix, and the number is not one I may invent.
        """
        template = self._template
        if template is None:  # pragma: no cover — a template always loads
            return (), TokenBasis(), ""

        # THE ROWS ARE THE TEMPLATE'S, NOT THE PROFILE'S, and that is the
        # correction D-35 forces on this screen. A profile's rules no longer
        # decide anything: the engine that applied them is deleted. Rendering
        # them here would put "DROP" beside a rule that drops nothing, on the
        # one screen whose entire purpose is an expert approving omissions
        # before a run commits to them — the most expensive place in the product
        # to state a falsehood.
        #
        # `profile` is accepted and unused. It stays on the Protocol because the
        # seam is shared and a screen still selects a profile for identity, the
        # matter copy and the log; it simply no longer determines what is
        # offered.
        levers: list[ReductionLever] = []
        for family in template.families:
            levers.append(ReductionLever(
                key=family.family_id,
                label=family.display_name,
                tokens=0,
                pages=0,
                # A family the template refuses to offer is a row that cannot be
                # clicked, here as on the summary waterfall. Same rule, same
                # layer, one screen earlier.
                kind=LEVER_EXPERT if family.offer else LEVER_RECOGNIZED,
                # Nothing is engaged. D-34: a template ships unengaged, every
                # lever arrives OFF, and no approver exists until somebody acts.
                engaged=False,
                # Before a run there is nothing to count — these are the
                # template's families, not this matter's pages.
                estimated=True,
                family_id=family.family_id,
                risk=family.risk.value,
                # No tier before a run: which tier will place a section is a
                # property of the documents, and none have been read. Empty is
                # the honest encoding and it is not a default slipped through —
                # see the ruling in tests/test_seam_population.py.
                tier="",
                rule=" | ".join(family.patterns),
                note=family.rationale,
                approved_by="",
            ))

        source = (
            f"the section template DocIQ ships: {template.template_id} "
            f"v{template.version} — {template.display_name}. Every lever is OFF "
            "and no omission is approved until you engage one and your name is "
            "recorded against it. No pages have been read for this matter yet, "
            "so no row carries a page or token count."
        )
        return tuple(levers), TokenBasis(), source

    def propose_project_tokens(self, source: str) -> tuple[str, ...]:
        """Read the matter's own outlines and filenames, and propose (D-39).

        Opens each PDF only for its outline — no page rendering, no OCR — so the
        cost is a header read per file rather than a run.
        """
        root = Path(source)
        if not root.is_dir():
            return ()
        import fitz

        from dociq.sections.normalize import normalize_label, strip_numbering
        from dociq.sections.project_tokens import propose_tokens

        labels: dict[str, list[str]] = {}
        names: list[str] = []
        for pdf in sorted(root.rglob("*.pdf")):
            names.append(pdf.stem)
            try:
                doc = fitz.open(pdf)
            except Exception:
                continue  # an unreadable file proposes nothing and stops nothing
            try:
                found = []
                for item in doc.get_toc(simple=True):
                    if len(item) < 3 or not isinstance(item[2], int) or item[2] < 1:
                        continue
                    key = strip_numbering(normalize_label(str(item[1])))
                    if key:
                        found.append(key)
                if found:
                    # Keyed by path relative to the matter, not by basename: two
                    # `Weekly Report.pdf` in different subfolders are two
                    # documents, and collapsing them undercounts the spread a
                    # token needs to clear `min_documents`. The failure is a
                    # name quietly NOT proposed, which nothing would show.
                    labels[str(pdf.relative_to(root))] = found
            except Exception:
                continue
            finally:
                doc.close()
        return propose_tokens(labels, names)

    def check_folders(self, source: str, output: str) -> str:
        """The run's own preflight, asked early (D-43's first finding).

        One definition, shared: :func:`dociq.ingest.walker.preflight_folders` is
        what :func:`walker.run` calls too, so the warning on the setup screen and
        the refusal at run time cannot give different answers.
        """
        return walker.preflight_folders(source, output)

    def preview_folder(self, path: str) -> FolderPreview:
        """What is in the folder, before anything is read.

        The file list comes from :func:`dociq.ingest.walker.list_files` — the
        run's own traversal — so the count beside the action is the count the run
        will report, junction loops and all.
        """
        root = Path(path)
        if not root.is_dir():
            return FolderPreview(0, 0)

        by_ext: dict[str, int] = {}
        sized: dict[str, int] = {}
        total = 0
        files = walker.list_files(root)
        for file in files:
            ext = file.suffix.lower() or "(no extension)"
            by_ext[ext] = by_ext.get(ext, 0) + 1
            try:
                size = file.stat().st_size
            except OSError:
                # Counted, not dropped: a file DocIQ cannot stat is still a file
                # the run will inventory (as an unreadable Tier-2 record). Its
                # bytes are unknown, which makes the estimate low rather than
                # the count wrong.
                size = 0
            total += size
            sized[ext] = sized.get(ext, 0) + size

        return FolderPreview(
            file_count=len(files),
            total_bytes=total,
            by_extension=tuple(sorted(by_ext.items())),
            # The rate that matches THIS pipeline's OCR setting, read off the
            # instance rather than assumed — the estimate and the run are then
            # the same configuration by construction.
            estimated_minutes=_minutes_for(total, sized,
                                           ocr_enabled=self._ocr_enabled),
        )

    def run(
        self,
        request: RunRequest,
        on_progress,
        should_cancel,
        confirm_bates: BatesConfirm | None = None,
    ) -> RunOutcome:
        """One real run of §4's six stages, reported as the GUI reads it."""
        matter = Path(request.source_root).name
        template = self._template
        # The seam records become the pipeline's own, here and nowhere else.
        # Every field travels: an approval that reached Stage 4 without its
        # approver would be the half-record D-34 forbids, and
        # `ApprovedOmission.validate()` refuses it rather than defaulting one.
        approvals = tuple(
            ApprovedOmission(
                family_id=a.family_id,
                approved_by=a.approved_by,
                approved_at=a.approved_at,
                matter=a.matter,
                matter_root=a.matter_root,
                template_id=a.template_id,
                template_version=a.template_version,
                project_tokens=tuple(a.project_tokens),
                recognition=a.recognition,
            )
            for a in request.approvals
        )
        config = config_from(request)
        config = replace(
            config, bates_pattern=stored_bates_pattern(request.output_root),
            project_tokens=tuple(request.project_tokens) or self._project_tokens)
        emitter = _Progress(on_progress)

        outcome = core.run(
            config,
            core.PipelineOptions(
                walk=walker.WalkOptions(
                    ocr_enabled=self._ocr_enabled,
                    progress=emitter.from_walk,
                    cancelled=should_cancel,
                ),
                on_stage=emitter.from_stage,
                matter_name=Path(request.source_root).name,
                master_index_path=request.master_index_path or None,
                # D-34/D-35. The template is what RECOGNIZES; the approvals are
                # the only thing that can drop. With an empty approval set this
                # run places every page it can in a section, records the tier,
                # and omits nothing — which is the state a freshly-installed
                # DocIQ is in and the state the shipped template arrives in.
                template=template,
                approvals=approvals,
                # §4 Stage 3's confirmation, carried across the seam (A-14,
                # rehearsal A4). Until this existed the GUI had no way to ask,
                # so every GUI run left the decision PENDING and a Bates-stamped
                # production produced NO LOCATORS AT ALL — while the acceptance
                # harness reported 92.130% through a hand-built decision the
                # product could not reach.
                confirm_bates=_translate(confirm_bates),
                # Still False, and now for the ordinary reason. `confirm_bates`
                # takes precedence when it is supplied; when it is not, nobody
                # was asked, and a machine confirmation recorded as one is the
                # honest fallback the headless harnesses already use.
                auto_confirm_bates=False,
            ),
        )

        result = outcome.result
        before = _estimate(result.tokens_before if outcome.published else None,
                           "before-reduction")
        after = _estimate(result.tokens_after if outcome.published else None,
                          "after-reduction")
        return RunOutcome(
            result=result,
            tokens_before=before,
            tokens_after=after,
            reconciliation=_reconciliation(result) if outcome.published else None,
            output_root=request.output_root,
            # No plan for a run that published nothing. The waterfall's levers
            # describe what an expert dropped from a corpus, and a cancelled run
            # has no corpus — the numbers would describe the fraction that
            # happened to be read before the operator pressed stop.
            # The RUN's tokens, canonical, not the adapter's constructor
            # default (Codex Sprint-4 B-2). The GUI supplies tokens on the
            # request and leaves the constructor empty, so this rebuilt the
            # waterfall with a different token set from the one that classified
            # the corpus — the run could DROP a section that the screen then
            # redrew as an unknown, non-engageable row, and an unapproved run
            # never offered the lever at all.
            plan=(_plan(result, before, template=template,
                        approvals=approvals,
                        project_tokens=config.project_tokens)
                  if outcome.published else None),
            termination=outcome.termination,
            published=outcome.published,
            # A-16. Carried across even though it describes a SUCCESS:
            # publication landed correctly and could not remove the drained
            # staging tree afterwards, and nobody opens `.dociq/` to find out. Populating it here rather than
            # deriving it in a screen keeps seam rule 2 — the GUI computes
            # nothing the pipeline is responsible for.
            superseded_residue=tuple(outcome.superseded_residue),
        )

    # -- §8 handoff (amendment A-12) ----------------------------------------

    def matter_layout_note(self, outcome: RunOutcome) -> str:
        """§8 Path B, **having looked** — the folder checked, not described.

        :func:`dociq.emit.handoff.expert_assist_layout` inspects the matter
        folder for the four things Expert Assist reads. Reporting what it found
        rather than what §7 says it writes is the entire value: Path B's claim is
        "DocIQ writes where evidence-mining already looks", and that is precisely
        the sentence worth checking before an operator relies on it.

        Returns "" when there is nothing to look at — a run that published
        nothing, or no output root — so the screen says the pipeline did not
        look instead of implying a verified folder.
        """
        from dociq.emit.handoff import expert_assist_layout
        from dociq.emit.paths import OutputLayout

        if not outcome.output_root or not outcome.published:
            return ""
        found = expert_assist_layout(OutputLayout.at(outcome.output_root))
        if found.missing:
            return (
                "CHECKED — and this folder is NOT ready for Expert Assist. "
                f"Present: {', '.join(found.present) or 'nothing'}. MISSING: "
                f"{', '.join(found.missing)}. Expert Assist reads these by name "
                "from the matter root; re-run the matter before pointing Claude "
                "at it."
            )
        return (
            "CHECKED on disk just now — all four are present at the paths "
            f"Expert Assist reads them from: {', '.join(found.present)}.\n\n"
            + found.instructions
        )

    def build_package(
        self,
        outcome: RunOutcome,
        doc_ids: tuple[str, ...],
        scope_statement: str,
    ) -> PackageResult:
        """§8 Path A: write ``upload_package/`` for exactly ``doc_ids`` (A-12).

        **The token figure is re-measured over the selected documents**, not
        read from :attr:`RunOutcome.tokens_after`. That field describes the whole
        corpus; putting it in a twelve-document package's README would tell the
        recipient the folder in front of them is 70× a Project's capacity when it
        may be a hundredth of that — and the README's capacity sentence is
        derived from it, so the error would arrive as advice.

        Raises rather than returning an empty result when the scope selects
        nothing: the seam's contract is that an adapter which cannot do Path A
        omits the method, so a call that reaches here is a call the screen
        believes will produce a package.
        """
        from dociq.emit.handoff import build_upload_package
        from dociq.emit.paths import OutputLayout

        if not outcome.published or not outcome.output_root:
            raise ValueError(
                "This run published no deliverables, so there is no clean_text/ "
                "to build a package from."
            )
        wanted = set(doc_ids)
        selected = [d for d in outcome.result.documents if d.doc_id in wanted]
        if not selected:
            raise ValueError(
                "This scope selects no documents; there is nothing to package."
            )

        texts = [page.text for doc in selected for page in doc.pages
                 if page.disposition is not Disposition.DROP]
        estimate = vt.estimate_for_texts(texts)

        package = build_upload_package(
            OutputLayout.at(outcome.output_root),
            matter_name=Path(outcome.output_root).name,
            document_count=len(selected),
            page_count=sum(len(doc.pages) for doc in selected),
            estimate=estimate,
            has_bates=any(page.bates for doc in selected for page in doc.pages),
            id_regime=outcome.result.config.id_regime.value,
            doc_ids=tuple(doc_ids),
            scope_statement=scope_statement,
            unsupported=len(outcome.result.unsupported),
        )
        return PackageResult(
            root=str(package.root),
            file_count=package.check.file_count,
            total_bytes=package.check.total_bytes,
            # The statement the package ACTUALLY carries, read back off the
            # result rather than echoed from the argument. They differ exactly
            # when the caller passed nothing and the emit layer authored the
            # default — and the screen's whole job here is to show the operator
            # what the recipient will read.
            scope_statement=package.scope_statement,
            doc_count=package.doc_count,
            # Read off the package, not recomputed: what the emit layer could
            # not find is the only authority on what the folder does not hold.
            # Omitting this keyword — which is exactly what this call site did
            # for the length of the sprint — silently defaults the field to ()
            # and tells the operator a short package is complete.
            missing=package.missing,
            # A-17, from finding A-7. Carried across even though it describes a
            # SUCCESS: the package published and is correct, and a complete old
            # copy of it survives under `.dociq/` where nobody looks. A-16's
            # `superseded_residue` never saw this one — it matches `superseded*`
            # and the package tree is called `package_superseded`.
            residue=package.residue,
        )

    # -- internals ----------------------------------------------------------

class _Progress:
    """Turns the pipeline's two progress channels into the seam's one.

    The walk reports files; the stage hook reports which of §4's six stages is
    running. They are merged here rather than in a screen because plain-language
    status text is the pipeline's own account of what it is doing (seam rule 2),
    and because the merge needs a fact the GUI has no way to know: Stages 1-2 are
    99.1% of the wall clock, so the five stages after them must keep talking or
    the run looks hung.

    **A known limitation, stated rather than papered over.**
    :class:`~dociq.gui.pipeline.ProgressEvent` counts FILES — the progress screen
    renders "N of M files" — so once every file is read the bar is legitimately
    full while Stages 3-6 run, and only the status line moves. Making the bar
    stage-aware needs a field on the seam's record, which is Track E's and the
    coordinator's to add; the proposed shape is in the verification note.
    """

    def __init__(self, emit) -> None:
        self._emit = emit
        self._total = 0
        self._done = 0
        self._failed = 0

    def from_walk(self, tick: dict) -> None:
        self._total = tick.get("total", 0) or self._total
        self._done = tick.get("done", 0)
        failed = tick.get("failed", 0)
        pages = tick.get("pages", 0)
        ocr_pages = tick.get("ocr_pages", 0)

        status = f"read {pages:,} pages" if pages else "reading"
        if ocr_pages:
            status += f" · OCR — {ocr_pages:,} pages"
        if failed:
            status += f" · {failed:,} could not be read"

        # Flagged on the tick where a failure APPEARS, not for the rest of the
        # run: the flag marks an event worth looking at, and one that stays on
        # for 300 files marks nothing.
        flagged = failed > self._failed
        self._failed = failed
        self._emit(ProgressEvent(self._done, self._total,
                                 _current_file(tick.get("file", "")),
                                 status, flagged=flagged))

    def from_stage(self, stage: core.StageProgress) -> None:
        if stage.stage <= 2:
            # Stages 1-2 are the walk, and the walk reports itself file by file.
            # A second, coarser voice on the same channel would overwrite the
            # detailed line with a vaguer one.
            return
        detail = f" — {stage.detail}" if stage.detail else ""
        self._emit(ProgressEvent(
            self._done or self._total, self._total, "",
            f"{stage.headline}{detail}", flagged=False))


def _current_file(raw: str) -> str:
    """The walk's ``file`` field, as a filename.

    It arrives as ``'sub/dir/file.pdf (12s)'`` for a file being read, or as
    ``'4 file(s) queued'``, or empty. Only the first form has a filename in it.
    """
    if not raw or raw.endswith("queued"):
        return ""
    name = raw.rsplit(" (", 1)[0] if raw.endswith(")") else raw
    return name.rsplit("/", 1)[-1]
