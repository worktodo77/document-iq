"""Presentation projections over a :class:`~dociq.gui.pipeline.RunOutcome`.

Everything here is *selection and wording* — grouping already-computed contract
data into the chips and lists the summary screen shows, and turning it into the
plain language Alex reads. Nothing here computes a pipeline quantity: page
counts come from the contract's derived properties and the token estimate comes
from the pipeline (see the seam's docstring and ``docs/contracts/amendments.md``).

Kept out of the widgets so the wording can be asserted in a test without a
QApplication, which is also the only way to keep "kept" and "dropped" from
quietly swapping places in a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath

from dociq.contracts import (
    Disposition,
    IdRegime,
    PageKind,
    ProcessingStatus,
    RunResult,
)
from dociq.gui.pipeline import (
    DIRECT_CONTEXT_TOKENS,
    LEVER_AUTOMATIC,
    LEVER_EXPERT,
    PackageResult,
    ProfileInfo,
    ReductionLever,
    ReductionPlan,
    RunOutcome,
    TokenBasis,
    TokenEstimate,
)
from dociq.runstate import COMPLETED, RunTermination

FLAG_OCR = "ocr"
FLAG_UNSUPPORTED = "unsupported"
FLAG_RECONCILIATION = "reconciliation"


@dataclass(frozen=True, slots=True)
class FlagItem:
    """One line of a flag's detail view."""

    primary: str
    secondary: str
    locator: str = ""
    """Where it is — a document-relative path or an index row. Shown in mono."""


@dataclass(frozen=True, slots=True)
class FlagGroup:
    """A chip on the summary screen, and the list behind it."""

    key: str
    label: str
    count: int
    items: tuple[FlagItem, ...]
    explanation: str
    """One plain sentence: what this flag means and what to do about it. Shown
    at the top of the detail view — the operator is a claims professional, not
    an engineer, and a bare list of filenames answers nothing."""


CAPACITY_LABEL = "Claude Project direct context"
"""What the D-21 reference line is CALLED, everywhere it appears.

D-21 rules the line is rendered as a **named, sourced reference**, never as a
budget or a target. Naming it once here is what keeps the waterfall row, the
capacity sentence and the handoff screen from drifting into three different
words for one line — and a line with three names reads as three quantities.

The figure itself is :data:`~dociq.gui.pipeline.DIRECT_CONTEXT_TOKENS` and is
never re-stated as a literal here.
"""

CAPACITY_SOURCE = (
    "a working figure for how much text a Claude Project holds before it falls "
    "back to retrieval; ruled D-21 (2026-08-01), not confirmed against "
    "Anthropic's published limits"
)
"""Where the reference line comes from. Shown wherever the line is, because
D-21 asks for a *sourced* reference and an unsourced one invites the reader to
treat it as a target."""


@dataclass(frozen=True, slots=True)
class CapacityReading:
    """One end of the run's token estimate, against the D-21 reference line.

    **Withdrawn 2026-08-01 (Track E, D-21).** This class used to carry
    ``caption()`` and ``verdict()``. ``verdict()`` ended "Drop more sections, or
    split the matter" — an instruction to get under the line, which is exactly
    the budget/target framing D-21 forbids and which D-15 already rules against.
    Both methods were dead: no screen called either, only their own tests did.
    The wording that ships is :meth:`SummaryView.capacity_line` and
    :meth:`SummaryView.route_line`, and the tests that asserted the withdrawn
    sentences are deleted rather than reworded.
    """

    tokens: TokenEstimate
    capacity: int = DIRECT_CONTEXT_TOKENS

    @property
    def fits(self) -> bool:
        """Conservative: it fits only if the *upper* end of the range fits."""
        return self.tokens.high <= self.capacity


def format_tokens(estimate: TokenEstimate) -> tuple[str, str]:
    """(headline, unit) — e.g. ``("152–166", "thousand tokens")``.

    A range, never a single number: D-03 rules the estimate is a chars-per-token
    ratio and must be shown conservatively rather than as a false precision. No
    calibration against a real tokenizer was performed — see
    :mod:`dociq.verify.tokens`.
    """
    lo, hi = estimate.low, estimate.high
    if hi >= 1_000_000:
        return f"{lo / 1e6:.2f}–{hi / 1e6:.2f}", "million tokens"
    if hi >= 10_000:
        return f"{lo / 1e3:.0f}–{hi / 1e3:.0f}", "thousand tokens"
    return f"{lo:,}–{hi:,}", "tokens"


def _ocr_flags(result: RunResult) -> FlagGroup:
    threshold = result.config.ocr_conf_threshold
    items: list[FlagItem] = []
    total = 0
    for doc in result.documents:
        low = [
            p for p in doc.pages
            if p.kind is PageKind.OCR and p.ocr_conf is not None
            and p.ocr_conf < threshold
        ]
        if not low:
            continue
        total += len(low)
        worst = min(p.ocr_conf for p in low)  # type: ignore[type-var]
        pages = ", ".join(str(p.page_no) for p in low[:8])
        if len(low) > 8:
            pages += f", +{len(low) - 8} more"
        items.append(
            FlagItem(
                primary=f"{doc.filename} — {len(low)} pages below "
                        f"{result.config.ocr_conf_threshold_pct}%",
                secondary=f"lowest confidence {worst * 100:.0f}% · original pages {pages}",
                locator=doc.rel_path,
            )
        )
    return FlagGroup(
        key=FLAG_OCR,
        label="Low-confidence OCR",
        count=total,
        items=tuple(items),
        explanation=(
            "These pages were read by OCR and some lines came back below the "
            f"{result.config.ocr_conf_threshold_pct}% confidence threshold. Nothing was "
            "dropped — the text is in the output — but check these pages against "
            "the originals before relying on them."
        ),
    )


def _unsupported_flags(result: RunResult) -> FlagGroup:
    # The Doc ID leads the line (Codex review #1, finding B-7). The explanation
    # below tells the operator these files are recorded in the document index —
    # true only since B-7 gave them identifiers and index rows — and a claim
    # that a row exists is worth nothing without the key to find it by.
    items = tuple(
        FlagItem(
            primary=f"{doc.doc_id} — {doc.filename}" if doc.doc_id else doc.filename,
            secondary=doc.error or "listed on the unsupported inventory",
            locator=doc.rel_path,
        )
        for doc in result.unsupported
    )
    return FlagGroup(
        key=FLAG_UNSUPPORTED,
        label="Unsupported formats",
        count=len(items),
        items=items,
        explanation=(
            "These files were inventoried and hashed but their text was not "
            "extracted. Each one carries a Doc ID and a row in "
            "document_index.csv with the processing status Unsupported, so the "
            "production stays complete; each line says how to include it if you "
            "need its contents."
        ),
    )


def _reconciliation_flags(outcome: RunOutcome) -> FlagGroup | None:
    recon = outcome.reconciliation
    if recon is None:
        return None
    items = tuple(
        FlagItem(primary=f"{row.filename}", secondary=row.detail, locator=row.doc_id)
        for row in recon.rows
    )
    return FlagGroup(
        key=FLAG_RECONCILIATION,
        label="Index mismatches",
        count=len(items),
        items=items,
        explanation=(
            f"{recon.matched} files matched the master index. These did not: a "
            "file with no index row, an index row with no file, or a field that "
            "disagrees. This is the production completeness check — none of it "
            "changed what was processed."
        ),
    )


def compact(value: int) -> str:
    """Shared with the waterfall so the headline and the bars agree."""
    if value >= 1_000_000:
        return f"{value / 1e6:.2f}M"
    if value >= 1_000:
        return f"{value / 1e3:.0f}K"
    return f"{value:,}"


def _projection_note(rows) -> str:
    """The aggregate's version of :meth:`ChecklistRow.scale`'s marker.

    A row that says "(projected, not counted)" beside a summary line that does
    not is the same defect one level up — and the summary is the sentence
    approval is given against, so it is the one that must not read as a count.

    The dangerous case is a projected ZERO. ``RealPipeline.profile_rules``
    returns ``tokens=0, pages=0, estimated=True`` for every row, because before
    a run there is nothing to count. Unmarked, the approval sentence reads
    "3 section types left out on your approval: 0 pages, about 0 tokens" — an
    expert reads "these drops cost nothing", approves, and the run drops real
    pages. So the zero is named as an absence of measurement, not a saving.

    Takes the levers themselves, so every aggregate over levers — the
    checklist's two summaries and the summary screen's split line — gets the
    same marker from the same place rather than each growing its own version of
    it, or, as here, not growing one at all.
    """
    rows = tuple(rows)
    estimated = [r for r in rows if r.estimated]
    if not estimated:
        return ""
    if len(estimated) == len(rows):
        note = " Both figures are projected, not counted."
    else:
        note = (f" The figures for {len(estimated)} of these {len(rows)} are "
                "projected, not counted.")
    if not any(r.pages or r.tokens for r in estimated):
        note += (" A zero here is the absence of a measurement, not a saving of "
                 "nothing: no page of this matter has been counted against "
                 "these rules yet.")
    return note


@dataclass(frozen=True, slots=True)
class SummaryView:
    """Everything the summary screen paints."""

    documents: int
    unsupported: int
    pages_in: int
    pages_kept: int
    pages_dropped: int
    capacity: CapacityReading
    capacity_before: CapacityReading
    flags: tuple[FlagGroup, ...]
    output_root: str
    id_regime_note: str
    plan: ReductionPlan | None = None
    termination: RunTermination = COMPLETED
    """How the run ENDED (Codex review #1, finding B-1). Read from the outcome,
    never inferred from a count: a cancelled run and a small matter produce the
    same numbers."""

    published: bool = True

    # -- run status ---------------------------------------------------------

    @property
    def complete(self) -> bool:
        return self.termination.complete

    def status_banner(self) -> str:
        """The sentence the summary screen shows above everything else, or "".

        Empty for a completed run: a banner that appears every time is a banner
        nobody reads. For every other status it is the first thing on the
        screen, and it says the two things the operator would otherwise get
        wrong — what these figures actually cover, and that the output folder
        still holds the previous run's deliverables.

        Both sentences now come from
        :data:`dociq.runstate.STATUS_PROSE`, per status. This method used to
        author the second one itself and append it to every non-complete
        status: "the figures below describe only what was read before the run
        stopped". That is true of a cancelled run and FALSE of a refused one,
        which read and identified the complete corpus and was rejected at
        DocIQ's own gate rather than cut short (Codex review #2 fix round,
        finding A-3). The claim is withdrawn, not merely widened — a screen in
        an evidentiary tool does not get to describe coverage it did not
        measure.
        """
        if self.complete:
            return ""
        return f"{self.termination.headline()} {self.termination.coverage_note()}".strip()

    # -- the headline -------------------------------------------------------

    @property
    def tokens_before(self) -> int:
        return (self.plan.full_tokens if self.plan
                else self.capacity_before.tokens.tokens)

    @property
    def tokens_after(self) -> int:
        return (self.plan.remaining_tokens if self.plan
                else self.capacity.tokens.tokens)

    def headline(self) -> str:
        """"1.33M → 678K" — the before/after Alex ruled, not a single figure."""
        return f"{compact(self.tokens_before)} → {compact(self.tokens_after)}"

    def basis_note(self) -> str:
        """Where the figure came from, in the pipeline's words — never the GUI's.

        A screen in an evidentiary tool must not author a provenance claim. The
        earlier version of this method printed the configured chars-per-token
        band as if it were the method used. No literal ratio appears here now —
        only what the pipeline says it did.

        The wording is also where Codex review #1 finding B-6 landed in the UI:
        this method used to say "a floor, not an estimate" whenever a pre-token
        count was present. DocIQ has no floor to offer, so it says "estimated"
        and names where the estimate came from instead.
        """
        basis = self.basis
        if not basis.provenance:
            return "basis not recorded"
        if basis.ratio_refuted:
            # A CONDITIONAL inconsistency, not a proof the ruled band is wrong
            # (Codex finding B-6, and the refutation was itself withdrawn). The
            # sentence therefore says what does not fit under WHICH assumptions,
            # and stops there. Normally False.
            return (f"{basis.provenance}. Under the assumptions this estimate "
                    "was made with, the configured chars-per-token band would "
                    "sit below what this text's structure allows, so the range "
                    "was widened rather than taken from the band alone. That is "
                    "an inconsistency between the band and those assumptions, "
                    "not a finding that the band is wrong.")
        if basis.is_structural:
            return basis.provenance
        return basis.provenance

    @property
    def basis(self) -> TokenBasis:
        if self.plan is not None:
            return self.plan.basis
        return TokenBasis.of(self.capacity.tokens)

    @property
    def is_structural(self) -> bool:
        return self.basis.is_structural

    def headline_unit(self) -> str:
        """The unit under the headline figure.

        Always plain "tokens". It used to read "tokens at least" whenever a
        pre-token count was available, which asserted a lower bound DocIQ cannot
        support (finding B-6). The unit is not where the method is stated —
        :meth:`basis_note` and :meth:`capacity_line` carry that, and a longer
        unit string overflows the headline row at 1040 px, which
        ``test_gui_states`` catches."""
        return "tokens"

    def fits(self) -> bool:
        return self.tokens_after <= self.capacity.capacity

    def capacity_line(self) -> str:
        """Where the corpus stands against the D-21 reference line.

        The over-capacity case is the EXPECTED case on a real matter, so it is
        phrased as a measurement against a *named reference*, not as a shortfall
        against a budget. D-21: the line is never a target, so the sentence says
        what the line IS every time it states a multiple of it."""
        capacity = self.capacity.capacity
        # "about", never "at least": DocIQ has no lower bound on token count
        # (finding B-6), so a lead-in that promises one is a false claim in the
        # one line the operator reads most.
        lead = "about "
        if self.fits():
            pct = 100.0 * self.tokens_after / capacity
            return f"{lead}{pct:.0f}% of the {CAPACITY_LABEL} reference line"
        factor = self.tokens_after / capacity
        # One decimal below 10x, none above: "96.9x" reads as precision the
        # figure does not have, and three digits plus a decimal overflows the
        # caption on a 1280-wide window.
        shown = f"{factor:.1f}" if factor < 10 else f"{factor:.0f}"
        return f"{lead}{shown}× the {CAPACITY_LABEL} reference line"

    def split_line(self) -> str:
        """The two totals, side by side and never added together.

        D-14 and ``LEVER_AUTOMATIC``: only the expert's drops are the expert's
        to defend. One combined "reduced by X" figure would put the tool's
        mechanical savings behind the expert's signature, which is the whole
        thing the profile system exists to prevent. Empty when there is no plan
        — a run with nothing to report says nothing rather than showing zeros.
        """
        if self.plan is None:
            return ""
        expert = [le for le in self.plan.engaged if le.kind == LEVER_EXPERT]
        auto = [le for le in self.plan.engaged if le.kind == LEVER_AUTOMATIC]
        left = (
            f"Left out on your approval: {compact(self.plan.expert_tokens)} "
            f"tokens across {sum(le.pages for le in expert):,} pages, "
            f"{len(expert)} section type{'' if len(expert) == 1 else 's'}"
            if expert else
            "Left out on your approval: nothing — every section is being kept"
        )
        right = (
            f"Removed mechanically by the tool: "
            f"{compact(self.plan.automatic_tokens)} tokens across "
            f"{sum(le.pages for le in auto):,} pages"
            if auto else
            "Removed mechanically by the tool: nothing"
        )
        # The marker goes AFTER each half's full stop, not inside the clause:
        # it qualifies the figure that was just given, and it must not turn one
        # sentence into two run together.
        return (f"{left}.{_projection_note(expert)}"
                f"   {right}.{_projection_note(auto)}")

    def drops_line(self) -> str:
        """What the expert left out, named — D-21's "what was dropped and why".

        Every engaged expert lever is named. Nothing is elided: a list that
        quietly stopped at five would be a silent cap on the one sentence whose
        job is completeness.
        """
        if self.plan is None:
            return ""
        expert = [le for le in self.plan.engaged if le.kind == LEVER_EXPERT]
        if not expert:
            return ("No section type is being left out on your approval — the "
                    "record stands at full size.")
        names = ", ".join(le.label for le in expert)
        estimated = [le.label for le in expert if le.estimated]
        note = ""
        if estimated:
            note = (f" The saving shown for {', '.join(estimated)} is projected "
                    "rather than counted.")
        return (f"You are leaving out {len(expert)} section "
                f"type{'' if len(expert) == 1 else 's'}: {names}. Each one is "
                "listed in the processing log with the profile rule that left "
                f"it out.{note}")

    def capacity_source_line(self) -> str:
        """What the reference line is, in one sentence, wherever it is shown."""
        return f"{CAPACITY_LABEL}: {CAPACITY_SOURCE}."

    def route_line(self) -> str:
        """What to do about it — never a dead end, and never "reduce to fit".

        §8 Path B is the recommended route for forensic matters anyway, and
        D-20 makes it the route proven at full scale: Expert Assist reads the
        matter folder from disk, where no container limit applies. Being above
        the reference line changes which route you take, not whether the work
        can be done — and what an expert defends is what was left out and why,
        never a figure they reduced to."""
        if self.fits():
            return ("It sits inside the "
                    f"{CAPACITY_LABEL} reference line as it stands. That line "
                    "is a reference, not a target — what you would defend is "
                    "what was left out and why.")
        return ("This is the expected state for a full matter record, not a "
                "failure. Analyze it with Expert Assist in Claude Cowork, "
                "which reads the matter folder straight from disk — no upload, "
                "and the reference line above does not apply. Uploading a "
                "scoped package to a Project stays possible; the reference "
                "line is not something to reduce to.")

    @property
    def total_flagged(self) -> int:
        return sum(f.count for f in self.flags)

    def flag(self, key: str) -> FlagGroup:
        for f in self.flags:
            if f.key == key:
                return f
        raise KeyError(key)


_ID_REGIME_NOTE = {
    IdRegime.NATIVE: lambda index: (
        "No master index supplied — document IDs are DocIQ's own DIQ- numbers."
    ),
    IdRegime.MASTER_INDEX: lambda index: (
        f"Document IDs taken from {index.filename} "
        f"({index.row_count:,} rows)."
        if index is not None else
        "Document IDs taken from a master index."
    ),
}
"""Every :class:`~dociq.contracts.IdRegime`, rendered explicitly."""

_UNRENDERED_REGIMES = set(IdRegime) - set(_ID_REGIME_NOTE)
if _UNRENDERED_REGIMES:  # pragma: no cover — import-time tripwire
    raise AssertionError(
        "IdRegime member(s) with no summary-screen sentence: "
        + ", ".join(sorted(m.name for m in _UNRENDERED_REGIMES))
    )
del _UNRENDERED_REGIMES


def build_summary(outcome: RunOutcome,
                  plan: ReductionPlan | None = None) -> SummaryView:
    """Project a run outcome into the summary screen's model.

    ``plan`` overrides the outcome's own when the operator has toggled levers on
    the waterfall: the run is unchanged, the projection of it is not.
    """
    result = outcome.result
    groups = [_ocr_flags(result), _unsupported_flags(result)]
    recon = _reconciliation_flags(outcome)
    if recon is not None:
        groups.append(recon)

    # Branched on the REGIME the run recorded, through a total map, rather than
    # on `master_index is None`. Same two sentences today — `RunConfig.id_regime`
    # is derived from exactly that field — but an `if/else` on a proxy for an
    # enum is the A-3 shape: a third regime would silently print one of these
    # two, and this one names the operator's ID scheme. See
    # `dociq.runstate.STATUS_PROSE` for the same discipline on TerminalStatus.
    index = result.config.master_index
    note = _ID_REGIME_NOTE[result.config.id_regime](index)

    return SummaryView(
        documents=len(result.documents),
        unsupported=len(result.unsupported),
        # Read, never recomputed: the contract's derived properties are the
        # single source of the §4 Stage-6 accounting identity.
        pages_in=result.pages_in,
        pages_kept=result.pages_kept,
        pages_dropped=result.pages_dropped,
        capacity=CapacityReading(outcome.tokens_after),
        capacity_before=CapacityReading(outcome.tokens_before),
        flags=tuple(g for g in groups if g.count),
        output_root=outcome.output_root,
        id_regime_note=note,
        plan=plan if plan is not None else outcome.plan,
        termination=outcome.termination,
        published=outcome.published,
    )


# ---------------------------------------------------------------------------
# E-1 — the §6 profiling checklist
# ---------------------------------------------------------------------------

CHECKLIST_NO_RULES = (
    "This profile's section rules are not available from the pipeline, so this "
    "screen can show nothing. Nothing may be dropped that was not shown here — "
    "run without a profile, or fix the profile, rather than assuming a rule "
    "that is not on screen."
)
"""The empty state, said out loud.

An empty checklist rendered as a tidy blank page is the exact failure Principle
3 is about: the operator reads "no drops" where the truth is "not known". Fail
loud, in the one place a missing rule could hide.
"""


@dataclass(frozen=True, slots=True)
class ChecklistRow:
    """One section rule, as the §6 checklist shows it.

    A projection of a :class:`~dociq.gui.pipeline.ReductionLever` — selection
    and wording only. Nothing here computes a saving; the lever carries it.
    """

    lever: ReductionLever
    profile: ProfileInfo

    @property
    def key(self) -> str:
        return self.lever.key

    @property
    def dropped(self) -> bool:
        return self.lever.engaged

    @property
    def locked(self) -> bool:
        return self.lever.locked

    def disposition_word(self) -> str:
        """DROP / KEEP for the expert's rules; AUTOMATIC for the tool's.

        A locked row is engaged, so it *is* dropping — but rendering it as
        "DROP" in the same column and the same accent as a rule the expert
        approved merges the two kinds of omission at a glance, which is
        precisely what ``LEVER_AUTOMATIC`` and D-14 forbid. Only the first kind
        is the expert's to defend, and only the first kind may look like it.
        """
        if self.locked:
            return "AUTOMATIC"
        return "DROP" if self.dropped else "KEEP"

    @property
    def expert_drop(self) -> bool:
        """A drop the EXPERT is signing for — the only kind drawn in accent."""
        return self.dropped and not self.locked

    def scale(self) -> str:
        """What this rule is worth, with the projected/counted distinction kept.

        ``estimated`` rows say so **in the same string as the figure**, so a
        projected saving cannot be read off the column as a counted one.
        """
        base = (f"{self.lever.pages:,} pages · "
                f"{compact(self.lever.tokens)} tokens")
        return f"{base} (projected, not counted)" if self.lever.estimated else base

    def attribution(self) -> str:
        """*Why* this page is being left out — the thing Principle 3 turns on.

        The rule is named by its own identity: the profile that carries it, the
        version of that profile, and the section it matches. That is what an
        expert would have to point at to defend the omission, and it is what
        ``processing_log.json`` records against every dropped page.

        Automatic rows are attributed to the tool instead, and say plainly that
        no expert approved them.
        """
        if self.locked:
            # NAMES THE CATEGORY, NEVER A MECHANISM. This read "Removed
            # mechanically by DocIQ — exact-hash duplicates and page furniture",
            # which asserts a behavior the pipeline explicitly withdraws:
            # `adapter._plan` states that DocIQ *detects* exact-hash duplicates
            # and warns about them, and "removes neither them nor page
            # furniture" — every page of every duplicate copy is extracted,
            # written to clean_text/ and counted in the accounting identity. The
            # real adapter therefore emits no automatic lever and this branch is
            # unreachable today; reachability is a property of this month's
            # adapter, not of the string, and this string is one an expert
            # reads. Whatever a locked lever turns out to be, `self.lever.label`
            # says what it is — this sentence's job is only to say who decided
            # it and where it is recorded.
            return (f"“{self.lever.label}” was removed mechanically by the "
                    "tool. No expert approved this; it is recorded separately "
                    "from the profile's drops in the processing log.")
        where = f"{self.profile.profile_id} v{self.profile.version}"
        if self.dropped:
            return (f"Rule {where} → section “{self.lever.key}” → DROP. Every "
                    "page it leaves out is listed in processing_log.json "
                    "against this rule.")
        return (f"Rule {where} → section “{self.lever.key}” → KEEP. Nothing is "
                "left out under this rule.")

    def matched_by(self) -> str:
        """The profile's own matching pattern, verbatim (A-11b).

        Attribution by rule IDENTITY — which :meth:`attribution` gives — is true
        and tells the expert nothing about what a DROP actually catches. This is
        the pattern itself. Empty when the pipeline supplied none, and the
        emptiness is stated rather than left blank: a rule whose pattern did not
        reach the screen and a rule with nothing to show must not look alike.
        """
        if self.locked:
            return ""
        if not self.lever.rule:
            return "The pipeline did not supply this rule's matching pattern."
        return f"Matches: {self.lever.rule}"

    def expert_note(self) -> str:
        """The profile's §6 notes for this section — why it is dropped and who
        approved it, IN THE EXPERT'S OWN WORDS (A-11b).

        Carried verbatim and never paraphrased. A GUI-authored rationale for an
        evidentiary omission would be the tool putting words in the expert's
        mouth, which is the one thing this screen exists to prevent.
        """
        if self.locked or not self.lever.note:
            return ""
        return f"“{self.lever.note}”"


@dataclass(frozen=True, slots=True)
class ProfileChecklistView:
    """§6 step 2/3: what this profile KEEPs and DROPs, before a run commits.

    **The completeness claim is the whole point.** §6 makes the checklist the
    place an expert approves omissions, and Principle 3 makes an unapproved
    omission indistinguishable from a missing document. So this view does not
    merely list what it was given — it compares what it was given against the
    rule count the profile declares, and says so when they disagree.
    """

    profile: ProfileInfo
    rows: tuple[ChecklistRow, ...]
    basis: TokenBasis = TokenBasis()
    source: str = ""
    """Where these rules came from, in the pipeline's words. Rendered verbatim;
    empty means the pipeline said nothing and the screen says that instead."""

    @property
    def expert_rows(self) -> tuple[ChecklistRow, ...]:
        return tuple(r for r in self.rows if not r.locked)

    @property
    def automatic_rows(self) -> tuple[ChecklistRow, ...]:
        return tuple(r for r in self.rows if r.locked)

    @property
    def dropped_rows(self) -> tuple[ChecklistRow, ...]:
        return tuple(r for r in self.expert_rows if r.dropped)

    @property
    def kept_rows(self) -> tuple[ChecklistRow, ...]:
        return tuple(r for r in self.expert_rows if not r.dropped)

    @property
    def empty(self) -> bool:
        return not self.rows

    @property
    def keeps_everything(self) -> bool:
        """A profile that genuinely carries no rules.

        Distinct from "the rules could not be read", and the distinction is the
        whole safety property: both render an empty list, one is a fact about
        the profile and the other is an absence of knowledge. Conflating them
        would let an unreadable profile be approved as a harmless one.
        """
        return self.empty and self.profile.section_rules == 0

    def completeness_note(self) -> str:
        """Whether every rule this profile carries is on screen.

        Four outcomes, all stated: a profile with no rules at all, rules that
        could not be read, a count that agrees, and a count that does not. The
        last is the dangerous one — the operator would otherwise approve a list
        believing it was the whole list.
        """
        if self.keeps_everything:
            return ("This profile carries no section rules. Every page is "
                    "kept, and nothing is left out on its authority.")
        if self.empty:
            return CHECKLIST_NO_RULES
        declared = self.profile.section_rules
        shown = len(self.expert_rows)
        if declared != shown:
            return (
                f"This profile declares {declared} section "
                f"rule{'' if declared == 1 else 's'} but "
                f"{shown} {'is' if shown == 1 else 'are'} shown here. Do not "
                "run it until that is explained: a rule that is not on this "
                "screen can still leave pages out."
            )
        return (
            f"All {shown} section rule{'' if shown == 1 else 's'} this profile "
            "carries are listed above. Nothing else is left out on the "
            "profile's authority."
        )

    @property
    def counts_agree(self) -> bool:
        return self.profile.section_rules == len(self.expert_rows)

    @property
    def approvable(self) -> bool:
        """Whether "Use this profile" may be pressed.

        A profile can only be approved from a screen that showed everything it
        does. Rules that could not be read, or a count that disagrees with the
        profile's own declaration, both mean the screen cannot make that claim
        — and a button whose meaning is "I have seen what this drops" must then
        refuse.
        """
        return self.keeps_everything or (not self.empty and self.counts_agree)

    def drop_summary(self) -> str:
        """The expert's side of the ledger, never merged with the tool's.

        "Nothing is left out" is a CLAIM, and it may only be made where the
        rules were actually read. Said over an unreadable profile it is the
        most dangerous sentence on the screen: the operator's own summary line
        telling them an omission they cannot see does not exist.
        """
        if self.empty and not self.keeps_everything:
            return ("Not known. This profile's rules could not be read, so what "
                    "it would leave out cannot be stated — and must not be "
                    "assumed to be nothing.")
        rows = self.dropped_rows
        if not rows:
            return ("Nothing is being left out on your approval — every section "
                    "is kept.")
        pages = sum(r.lever.pages for r in rows)
        tokens = sum(r.lever.tokens for r in rows)
        return (f"{len(rows)} section type{'' if len(rows) == 1 else 's'} left "
                f"out on your approval: {pages:,} pages, about "
                f"{compact(tokens)} tokens.{_projection_note(r.lever for r in rows)}")

    def automatic_summary(self) -> str:
        rows = self.automatic_rows
        if not rows:
            return ""
        pages = sum(r.lever.pages for r in rows)
        tokens = sum(r.lever.tokens for r in rows)
        return (f"Separately, DocIQ removes {pages:,} pages / about "
                f"{compact(tokens)} tokens mechanically. That is the tool's "
                "doing, not yours, and it is never added to the figure above."
                f"{_projection_note(r.lever for r in rows)}")

    def basis_note(self) -> str:
        """The pipeline's own words about where these figures came from."""
        return self.basis.provenance or "basis not recorded"


def build_profile_checklist(
    profile: ProfileInfo,
    levers: tuple[ReductionLever, ...] = (),
    basis: TokenBasis = TokenBasis(),
    source: str = "",
) -> ProfileChecklistView:
    """Project a profile and its section rules into the §6 checklist."""
    return ProfileChecklistView(
        profile=profile,
        rows=tuple(ChecklistRow(lever, profile) for lever in levers),
        basis=basis,
        source=source,
    )


# ---------------------------------------------------------------------------
# E-3 — §9 acceptance criterion 8: analyze in Claude, Paths A and B
# ---------------------------------------------------------------------------

SCOPE_ALL = "all"
SCOPE_DATES = "dates"
SCOPE_TYPES = "types"

PATH_A_UNAVAILABLE = (
    "Building an upload package is pipeline work and this build's pipeline "
    "does not offer it, so DocIQ will not write one. Nothing about the matter "
    "folder changes; Path B below is unaffected."
)
"""Why the Path A action is refused when the adapter has no package builder.

Stated rather than greyed out silently: a disabled button with no reason is
read as "not for me", and the operator then assumes a package exists somewhere.
"""


@dataclass(frozen=True, slots=True)
class PackageScope:
    """What a Path A package covers — and, when it is a subset, that it IS one.

    **D-20.** Path A is proven on a deliberately scoped subset. A package that
    silently contains part of a matter is the worst thing this screen could
    produce: downstream, a subset and a full record are indistinguishable once
    the folder has been dragged into a Project. So the scope is chosen here and
    :meth:`statement` travels *into the package*, not merely onto the screen.
    """

    kind: str = SCOPE_ALL
    date_from: str = ""
    date_to: str = ""
    doc_types: tuple[str, ...] = ()

    @property
    def is_subset(self) -> bool:
        return self.kind != SCOPE_ALL

    def label(self) -> str:
        if self.kind == SCOPE_DATES:
            lo = self.date_from or "the earliest document"
            hi = self.date_to or "the latest document"
            return f"documents dated {lo} to {hi}"
        if self.kind == SCOPE_TYPES:
            return ("documents of type " + ", ".join(self.doc_types)
                    if self.doc_types else "documents of no selected type")
        return "every document in the matter"

    def statement(self, selected: int, total: int, matter: str = "",
                  unsupported: int = 0) -> str:
        """The block written into the package itself (§8 Path A README).

        Authored here because it is *wording over already-selected data*, and
        placed in the package because a scope that lives only on the screen the
        operator saw is not a scope anyone downstream can check.

        ``unsupported`` is the §5 listed-only inventory — files DocIQ hashed and
        indexed but whose text it did not extract. They can never be in a Path A
        package, so a package that called itself "the complete production" while
        they existed would be making the exact claim D-20 exists to prevent, in
        the one file a reader would trust to know better.
        """
        head = f"SCOPE OF THIS PACKAGE{(' — ' + matter) if matter else ''}"
        listed = (
            f"\n  {unsupported:,} further file{'' if unsupported == 1 else 's'} "
            "in this matter were inventoried and hashed but their text was not "
            "extracted (unsupported formats, §5). They are NOT in this package; "
            "each has a row in document_index.csv with the status Unsupported."
            if unsupported else ""
        )
        if not self.is_subset and selected == total:
            body = (
                f"This package covers ALL {total:,} documents whose text DocIQ "
                "extracted from the matter record."
                + (listed or " It is the complete production as DocIQ "
                             "processed it.")
            )
        else:
            body = (
                f"This package is a SUBSET. It covers {selected:,} of the "
                f"{total:,} documents in the matter record — {self.label()}.\n"
                "  Do not treat it as the complete record. Anything absent from "
                "this package may still exist in the matter, and any finding "
                "drawn from it is bounded by this scope.\n"
                "  The complete record is in the matter output folder and can "
                "be read directly from disk (Path B)."
            )
        return f"{head}\n{'=' * 60}\n  {body}\n"


def _bytes_phrase(total: int) -> str:
    """A size a claims professional reads, from the byte count the emit layer
    measured. Wording only — the number is the pipeline's."""
    if total < 1024:
        return f"{total:,} bytes"
    if total < 1024 * 1024:
        return f"{total / 1024:,.0f} KB"
    return f"{total / (1024 * 1024):,.1f} MB"


@dataclass(frozen=True, slots=True)
class PackageOutcomeView:
    """What pressing "Build the upload package" actually did (Codex #2, A-1).

    The button used to discard :class:`~dociq.gui.pipeline.PackageResult` and
    repaint the same view, so success, failure and an ignored click were the
    same pixels. A failure went to ``print()`` — and the shipped GUI is a
    windowed executable with no console attached, so that text reached nobody.

    Both outcomes are records rather than screen strings for the reason the rest
    of this module exists: the wording is then asserted without a QApplication,
    and success and failure cannot drift into each other in a refactor.
    """

    ok: bool
    headline: str
    lines: tuple[str, ...] = ()
    """The facts, one per line: where it was written, what is in it, how big."""

    missing: tuple[str, ...] = ()
    """Doc IDs the operator selected that the package could NOT include (B-3).

    Never folded into :attr:`lines`. A package one document short of the scope
    its own statement claims is a different fact from its size, and it is the
    one the operator has to act on."""

    root: str = ""
    scope_statement: str = ""

    def missing_note(self) -> str:
        """Named and counted, never summarised away."""
        if not self.missing:
            return ""
        shown = ", ".join(self.missing[:12])
        more = (f" and {len(self.missing) - 12:,} more"
                if len(self.missing) > 12 else "")
        one = len(self.missing) == 1
        return (
            f"{len(self.missing):,} selected document{'' if one else 's'} "
            f"{'is' if one else 'are'} NOT in this package — no clean text was "
            f"found for {shown}{more}. The scope statement inside the package "
            "still describes the set you asked for, so check "
            "document_index.csv before sending it."
        )


def package_built(result: PackageResult) -> PackageOutcomeView:
    """The success state, read off the record the pipeline returned.

    Every figure is the emit layer's own: the path it wrote, the documents it
    put in, the files it counted, the bytes it measured, and the scope statement
    it actually carries. None of it is echoed back from the request — a screen
    that repeats what it asked for cannot tell the operator what it got.
    """
    docs = result.doc_count
    return PackageOutcomeView(
        ok=True,
        headline="Upload package built.",
        lines=(
            f"Written to: {result.root}",
            f"{docs:,} document{'' if docs == 1 else 's'}, "
            f"{result.file_count:,} file{'' if result.file_count == 1 else 's'}, "
            f"{_bytes_phrase(result.total_bytes)}.",
        ),
        missing=tuple(result.missing),
        root=result.root,
        scope_statement=result.scope_statement,
    )


def package_failed(message: str) -> PackageOutcomeView:
    """The failure state. The pipeline's exception text is carried VERBATIM.

    Paraphrasing it would cost the operator the only specific thing they have —
    "sources.json is locked by another process" is actionable and "the package
    could not be built" is not. The sentence added around it says what did NOT
    happen, because a half-written package and no package are different states
    of the folder.

    **The second sentence is a load-bearing claim, and when it was written it
    was false** (Codex review #2 fix round, finding A-4). ``upload_package/``
    was built in place: a failure after the first copy left a CURRENT partial
    folder under exactly the name this text was telling the operator held an
    earlier build. It is true now, and it is true by construction rather than
    by care — :func:`dociq.emit.handoff.build_upload_package` assembles in a
    sibling directory and claims the published name only after every copy,
    filter, README and validation has passed, so a failed build leaves either
    the earlier package byte-for-byte or no package at all. Both satisfy this
    sentence. ``tests/test_package_swap.py`` asserts the disk and this text
    together, in one test, because the finding was that they disagreed.

    The word "completed" carries the other half: a package that failed
    validation is not a package, and the folder is never left in that state.
    """
    return PackageOutcomeView(
        ok=False,
        headline="The upload package was NOT built.",
        lines=(
            message.strip() or "The pipeline reported no reason.",
            "Nothing was uploaded and no package folder was completed. Any "
            "package already on disk is from an EARLIER build — check its date "
            "before sending it.",
        ),
    )


@dataclass(frozen=True, slots=True)
class HandoffDocument:
    """One document, reduced to what scoping needs. Selection, not computation."""

    doc_id: str
    filename: str
    doc_type: str
    date: str
    """First detected ISO date, or "" — read from the contract, never parsed
    here. Documents with no detected date are never silently in or out of a
    date scope; :meth:`HandoffView.selected` states how many were excluded."""


@dataclass(frozen=True, slots=True)
class HandoffView:
    """Both §8 routes off the summary screen, with Path B leading.

    Path B is what D-20 proves at full scale and what §8 recommends for
    forensic matters, so it is first on the screen and is the one described in
    full. Path A is real but bounded, and every part of this view exists to
    keep that boundary visible.
    """

    output_root: str
    published: bool
    documents: tuple[HandoffDocument, ...]
    unsupported: int = 0
    """§5 listed-only files: inventoried and hashed, text not extracted. They
    can never be in a Path A package, so the package must say they exist."""

    scope: PackageScope = PackageScope()
    matter_name: str = ""
    package_available: bool = False
    """Whether the pipeline offers a package builder at all."""

    layout_note: str = ""
    """The pipeline's own statement of what is in the matter folder and what to
    point Claude at. Rendered verbatim; empty means the pipeline did not say."""

    package: "PackageOutcomeView | None" = None
    """The result of the LAST build under THIS scope, or ``None`` (A-1).

    Carried on the view rather than held in the screen because the screen is
    repainted on every scope change, and a success banner that survived a repaint
    would describe a package built under a scope the operator has since changed.
    :meth:`MainWindow._rescope` therefore clears it; a stale "built" state beside
    a different scope statement is the D-20 failure the screen exists to
    prevent, wearing a reassuring green."""

    # -- Path B -------------------------------------------------------------

    def path_b_ready(self) -> bool:
        return bool(self.output_root) and self.published

    def path_b_note(self) -> str:
        if not self.published:
            return ("This run wrote no deliverables, so there is nothing on "
                    "disk to point Claude at. Re-run the matter first.")
        if not self.output_root:
            return "No output folder was recorded for this run."
        return self.layout_note or (
            "The pipeline did not report what is in this folder, so DocIQ "
            "cannot confirm it is Expert-Assist-shaped. Check the folder "
            "before relying on it."
        )

    # -- Path A -------------------------------------------------------------

    @property
    def doc_types(self) -> tuple[str, ...]:
        return tuple(sorted({d.doc_type for d in self.documents if d.doc_type}))

    @property
    def dated(self) -> tuple[str, ...]:
        return tuple(sorted({d.date for d in self.documents if d.date}))

    def selected(self) -> tuple[HandoffDocument, ...]:
        """The documents a Path A package would contain under :attr:`scope`."""
        if self.scope.kind == SCOPE_TYPES:
            wanted = set(self.scope.doc_types)
            return tuple(d for d in self.documents if d.doc_type in wanted)
        if self.scope.kind == SCOPE_DATES:
            lo, hi = self.scope.date_from, self.scope.date_to
            return tuple(
                d for d in self.documents
                if d.date and (not lo or d.date >= lo) and (not hi or d.date <= hi)
            )
        return self.documents

    def undated(self) -> int:
        return sum(1 for d in self.documents if not d.date)

    def scope_statement(self) -> str:
        return self.scope.statement(len(self.selected()), len(self.documents),
                                    self.matter_name, self.unsupported)

    def scope_caution(self) -> str:
        """What the operator must understand before they press the button.

        A date scope silently drops every document DocIQ found no date in; that
        is a second, invisible subsetting on top of the one that was chosen, so
        it is named with its count rather than left to be discovered.
        """
        notes: list[str] = []
        if self.scope.kind == SCOPE_DATES and self.undated():
            notes.append(
                f"{self.undated():,} document{'' if self.undated() == 1 else 's'} "
                "carry no detected date and are therefore NOT in a date-scoped "
                "package. They are not excluded on their content — only on the "
                "absence of a date DocIQ could read."
            )
        if self.scope.is_subset and not self.selected():
            notes.append("This scope selects no documents at all. There is "
                         "nothing to package.")
        return " ".join(notes)

    def package_blocker(self) -> str:
        """Why the package cannot be built, or "" when it can."""
        if not self.published:
            return ("This run wrote no deliverables, so there is nothing to "
                    "package.")
        if not self.package_available:
            return PATH_A_UNAVAILABLE
        if not self.selected():
            return "This scope selects no documents."
        return ""


def build_handoff(outcome: RunOutcome, scope: PackageScope = PackageScope(),
                  package_available: bool = False,
                  layout_note: str = "",
                  package: PackageOutcomeView | None = None) -> HandoffView:
    """Project a run outcome into the §8 handoff screen's model."""
    return HandoffView(
        output_root=outcome.output_root,
        published=outcome.published,
        documents=tuple(
            HandoffDocument(
                doc_id=doc.doc_id,
                filename=doc.filename,
                doc_type=doc.doc_type or "(no type)",
                date=doc.detected_dates[0] if doc.detected_dates else "",
            )
            for doc in outcome.result.documents
        ),
        unsupported=len(outcome.result.unsupported),
        scope=scope,
        # PureWindowsPath, not Path: §10 makes this a Windows-only product, and
        # under a POSIX test runner ``Path(r"D:\m\out").name`` is the whole
        # string — so the matter name would silently become a backslash-laden
        # path in exactly the sentence that is written into the package.
        matter_name=(PureWindowsPath(outcome.output_root).name
                     if outcome.output_root else ""),
        package_available=package_available,
        layout_note=layout_note,
        package=package,
    )


def status_word(status: ProcessingStatus) -> str:
    """Plain language for a §5 processing status. The enum values reach disk;
    they are not what a claims professional should have to read."""
    return {
        ProcessingStatus.FULL: "processed",
        ProcessingStatus.PARTIAL_OCR_FLAGGED: "processed — OCR flagged",
        ProcessingStatus.UNSUPPORTED: "listed only",
        ProcessingStatus.FAILED: "failed",
    }[status]


def disposition_word(disposition: Disposition) -> str:
    return {Disposition.KEEP: "kept", Disposition.DROP: "dropped"}[disposition]
