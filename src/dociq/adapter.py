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
from dociq.contracts import Disposition, RunResult
from dociq.gui.pipeline import (
    LEVER_EXPERT,
    BatesConfirm,
    BatesProposal,
    FolderPreview,
    ProfileInfo,
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
from dociq.profiles.model import (
    FormatProfile,
    ProfileError,
    load_profile,
    profile_library_dir,
)
from dociq.verify import tokens as vt

__all__ = [
    "RealPipeline",
    "NO_PROFILE",
    "SECONDS_PER_GB_OCR_ON",
    "SECONDS_PER_GB_OCR_OFF",
    "seconds_per_gb",
    "measured_basis",
    "ESTIMABLE_EXTENSIONS",
]

# ---------------------------------------------------------------------------
# The one built-in profile
# ---------------------------------------------------------------------------

NO_PROFILE = ProfileInfo("none", "-", "No profile — keep every page",
                         section_rules=0)
"""The choice that drops nothing, always offered and always last.

It is the only profile DocIQ ships, and that is a decision rather than an
omission. §6 step 4 requires a profile to be saved with the expert's username and
timestamp, and :meth:`~dociq.profiles.model.SectionRule.validate` refuses a DROP
rule that carries no note recording why the section is omitted and who approved
it. A built-in "MODEC monthly progress report" profile with photo logs pre-marked
DROP would therefore either fail validation or ship an omission decision no
expert made, attributed to nobody — under a Principle-3 product whose entire
argument is that the expert's omissions are separable from the tool's. The
profiling workflow (§6) is where a real profile comes from; until an expert has
run it, the honest library is empty and the honest picker offers this.
"""


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


def _section_lever(
    name: str, tokens: int, pages: int, dropped_tokens: int, dropped_pages: int,
    rules: dict[str, list],
) -> ReductionLever:
    """One waterfall row for one section, whatever fraction of it was dropped.

    **The defect this shape exists to prevent, confirmed before it was fixed.**
    The engaged flag used to be ``dropped == pages`` and the lever always
    carried the WHOLE section's figures. A section with some pages dropped and
    some kept therefore drew as KEEP, and its entire token weight counted as
    still present: on a three-page reproduction the waterfall reported 451
    tokens remaining where the run had actually published 287. The waterfall and
    ``tokens_after`` disagreed, and the row said the opposite of what happened.

    **It is reachable through an ordinary valid profile.** Only ``rule_id`` is
    checked for uniqueness (:meth:`dociq.profiles.model.FormatProfile.validate`);
    ``label`` is not, and Stage 4 keys a page's section on
    ``rule.label or matched_text``. So one DROP rule and one KEEP rule sharing a
    label — in one profile, or in two profiles claiming different documents —
    put dropped and kept pages under the same section name. Constructed and run
    before this was changed; it is not a phantom.

    A lever means "what this removes when engaged", so a partly-dropped section
    carries the DROPPED part's figures and is engaged. A section nothing was
    dropped from carries the whole section's figures and is not engaged: that is
    the projection "if you dropped this too". Both readings are then consistent
    with ``ReductionPlan.remaining_tokens``.

    The label says when a row is partial. ``ReductionLever`` has no field for it
    and the seam is frozen; the label is the string every screen already renders
    for a lever, so it is the one place the fact cannot be lost.

    ``rule`` and ``note`` are the profile's own pattern and the expert's own
    stated reason (A-11b). They are populated HERE from the rule that decided
    the section, and they were populated nowhere before Codex review #2's
    seam-population probe enumerated every field of every presentation record
    and found them empty at every construction site in this module — the same
    shape as B-3, one amendment older.
    """
    rule, note = _rule_text(name, rules)
    if dropped_pages == 0:
        return ReductionLever(key=name, label=name, tokens=tokens, pages=pages,
                              kind=LEVER_EXPERT, engaged=False,
                              estimated=False, rule=rule, note=note)
    label = name if dropped_pages == pages else (
        f"{name} (part — {dropped_pages:,} of {pages:,} pages)")
    return ReductionLever(key=name, label=label, tokens=dropped_tokens,
                          pages=dropped_pages, kind=LEVER_EXPERT,
                          engaged=True, estimated=False, rule=rule, note=note)


def _section_rules(profiles: tuple[FormatProfile, ...]) -> dict[str, list]:
    """Section name → every rule that could have produced it.

    A LIST, not a rule. Stage 4 keys a page's section on ``rule.label or
    matched_text`` and only ``rule_id`` is checked for uniqueness, so two rules
    can share a section name — the documented partial case. Attributing one
    rule's pattern and one expert's note to pages the other rule decided would
    put words in an expert's mouth about an omission they did not approve, so
    an ambiguous name yields nothing rather than a guess.
    """
    found: dict[str, list] = {}
    for profile in profiles:
        for rule in profile.section_rules:
            found.setdefault(rule.label or rule.rule_id, []).append(rule)
    return found


def _rule_text(name: str, rules: dict[str, list]) -> tuple[str, str]:
    """The verbatim pattern and note for a section, or ``("", "")``.

    Empty is a state the checklist renders — "the profile stated no reason" —
    and never a silent blank: an omission with no stated reason and an omission
    whose reason was lost on the way to the screen must not look the same.
    """
    found = rules.get(name, ())
    if len(found) != 1:
        return "", ""
    return found[0].pattern, found[0].notes or ""


def _plan(result: RunResult, before: TokenEstimate,
          profiles: tuple[FormatProfile, ...] = ()) -> ReductionPlan | None:
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
    for doc in result.documents:
        for page in doc.pages:
            if not page.section:
                continue
            row = sections.setdefault(page.section, [0, 0, 0, 0])
            tok = vt.measure(page.text).pretokens
            row[0] += tok
            row[1] += 1
            if page.disposition is Disposition.DROP:
                row[2] += tok
                row[3] += 1

    rules = _section_rules(profiles)
    levers = tuple(_section_lever(name, *totals, rules=rules)
                   for name, totals in sorted(sections.items()))
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
                 ocr_enabled: bool = True) -> None:
        self._library_dir = library_dir
        self._ocr_enabled = ocr_enabled
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

    def profiles(self) -> tuple[ProfileInfo, ...]:
        """§6's library, plus :data:`NO_PROFILE`.

        Never empty — an empty picker is indistinguishable from a broken one.
        ``section_rules`` is the real count of rules the profile carries, so the
        operator can see at a glance whether a profile will remove anything;
        nothing here is a placeholder.
        """
        found: list[ProfileInfo] = []
        issues: list[str] = []
        directory = profile_library_dir(self._library_dir)
        if directory.is_dir():
            for path in sorted(directory.glob("*.yaml")):
                try:
                    profile = load_profile(path)
                except (ProfileError, OSError) as exc:
                    issues.append(f"{path.name}: {exc}")
                    continue
                found.append(
                    ProfileInfo(
                        profile_id=profile.profile_id,
                        version=profile.version,
                        label=profile.display_name or profile.profile_id,
                        section_rules=len(profile.section_rules),
                    )
                )
        self.library_issues = tuple(issues)
        found.sort(key=lambda p: (p.label.lower(), p.profile_id, p.version))
        return tuple(found) + (NO_PROFILE,)

    def profile_rules(
        self, profile: ProfileInfo
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
        chosen = self._load(profile)
        if chosen is None:
            return (), TokenBasis(), ""

        levers: list[ReductionLever] = []
        seen: set[str] = set()
        for rule in chosen.section_rules:
            # The section as a human names it — the checklist renders this as
            # `section "<key>"` and an expert has to recognize it. Rule ids are
            # for the audit trail, and are the fallback only when a label would
            # be ambiguous, because two rows reading "Photo logs" would be two
            # omissions the expert cannot tell apart.
            name = rule.label or rule.rule_id
            if name in seen:
                name = f"{name} (rule {rule.rule_id})"
            seen.add(name)
            levers.append(
                ReductionLever(
                    key=name,
                    label=name,
                    tokens=0,
                    pages=0,
                    kind=LEVER_EXPERT,
                    # A TOTAL lookup, not `is Disposition.DROP`. This is the
                    # one place a Disposition is flattened into a bool that the
                    # §6 checklist then renders as the WORD "DROP" or "KEEP"
                    # (view_models.LeverRow.disposition_word). A third member
                    # would arrive here as `False` and be printed to the expert
                    # as "KEEP" — a rule they are signing off as harmless. Same
                    # class as finding A-3; same discipline as
                    # dociq.runstate.STATUS_PROSE.
                    engaged=_LEVER_ENGAGED[rule.disposition],
                    estimated=True,
                    # A-11b's two fields, populated at last. This is the screen
                    # the amendment was written for — §6's checklist, where an
                    # expert approves an omission before the run commits to it —
                    # and until Codex review #2's seam-population probe both
                    # were left at their defaults here, so the checklist could
                    # show only that a DROP rule existed, never what it catches
                    # or who approved it.
                    rule=rule.pattern,
                    note=rule.notes or "",
                )
            )

        source = (
            f"read from the profile library: {chosen.profile_id} "
            f"v{chosen.version}"
            + (f", saved by {chosen.created_by}" if chosen.created_by else "")
            + (f" on {chosen.created_at}" if chosen.created_at else "")
            + ". These are the profile's rules; no pages have been read for "
            "this matter yet, so no row carries a page or token count."
        )
        return tuple(levers), TokenBasis(), source

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
        profiles = self._profiles_for(request)
        config = config_from(self._without_sentinel(request))
        config = replace(
            config, bates_pattern=stored_bates_pattern(request.output_root))
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
                profiles=profiles,
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
            plan=(_plan(result, before, profiles) if outcome.published
                  else None),
            termination=outcome.termination,
            published=outcome.published,
            # A-16. Carried across even though it describes a SUCCESS: the swap
            # published correctly and could not delete what it set aside, and
            # nobody opens `.dociq/` to find out. Populating it here rather than
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

    def _without_sentinel(self, request: RunRequest) -> RunRequest:
        """:data:`NO_PROFILE` is a picker entry, not a profile.

        Letting it through ``config_from`` would stamp ``profile_id="none"`` into
        :class:`~dociq.contracts.RunConfig` — a hashed identity field — so a run
        with no profile and a run with a profile called "none" would be recorded
        identically, and every document in the index would be labelled with a
        profile that does not exist.
        """
        if request.profile is not None and request.profile.profile_id == \
                NO_PROFILE.profile_id:
            return replace(request, profile=None)
        return request

    def _profiles_for(self, request: RunRequest) -> tuple[FormatProfile, ...]:
        """Load the chosen profile from the library.

        The GUI passes an identifier; the run needs the rules. A chosen profile
        that cannot be loaded RAISES rather than falling back to "no profile":
        silently running without an expert's rules and publishing the result as a
        reduced corpus is the failure mode this whole product is against.
        """
        chosen = self._load(request.profile)
        return (chosen,) if chosen is not None else ()

    def _load(self, profile: ProfileInfo | None) -> FormatProfile | None:
        """The library file behind a picker entry, or ``None`` for no profile.

        One loader for the run and for the §6 checklist, so the rules an expert
        approves on screen are read from the same file, by the same parser, as
        the rules the run applies. Two readers would eventually show one thing
        and drop another.
        """
        if profile is None or profile.profile_id == NO_PROFILE.profile_id:
            return None
        directory = profile_library_dir(self._library_dir)
        path = directory / f"{profile.profile_id}.v{profile.version}.yaml"
        if not path.is_file():
            raise ProfileError(
                f"The profile '{profile.label}' ({profile.profile_id} "
                f"v{profile.version}) is no longer in the profile library at "
                f"{directory}. It was offered when this run was set up and has "
                "been moved, renamed or deleted since. DocIQ will not run "
                "without the rules it was told to apply."
            )
        return load_profile(path)


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
