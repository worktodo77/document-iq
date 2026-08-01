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

from dociq.contracts import Disposition, PageKind, ProcessingStatus, RunResult
from dociq.gui.pipeline import (
    DIRECT_CONTEXT_TOKENS,
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


@dataclass(frozen=True, slots=True)
class CapacityReading:
    """The gauge's numbers, both ends of the D-03 range."""

    tokens: TokenEstimate
    capacity: int = DIRECT_CONTEXT_TOKENS

    @property
    def pct_low(self) -> float:
        return 100.0 * self.tokens.low / self.capacity

    @property
    def pct_high(self) -> float:
        return 100.0 * self.tokens.high / self.capacity

    @property
    def fits(self) -> bool:
        """Conservative: it fits only if the *upper* end of the range fits."""
        return self.tokens.high <= self.capacity

    def caption(self) -> str:
        """The D-07 mono caption under the gauge."""
        if round(self.pct_low) == round(self.pct_high):
            return f"{self.pct_high:.0f}% of direct-context capacity"
        return (f"{self.pct_low:.0f}–{self.pct_high:.0f}% "
                "of direct-context capacity")

    def verdict(self) -> str:
        """Plain language, no jargon: the sentence D-03/§7 asks for."""
        if self.fits:
            return "Fits directly in a Claude Project — no retrieval mode needed."
        return ("Larger than a Claude Project holds directly — it will fall back "
                "to retrieval mode. Drop more sections, or split the matter.")


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
        nobody reads. For a blocked or cancelled run it is the first thing on
        the screen, and it says the two things the operator would otherwise get
        wrong — that these figures cover only part of the record, and that the
        output folder still holds the previous run's deliverables.
        """
        if self.complete:
            return ""
        return (
            f"{self.termination.headline()} The figures below describe only "
            "what was read before the run stopped. Nothing in the output "
            "folder was changed; a full record of this attempt is in "
            "incomplete_run/."
        )

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
            return (f"{basis.provenance}; the configured ratio band sits below "
                    "what this text's structure allows, so the range was "
                    "widened rather than taken from the band alone")
        if basis.is_structural:
            return ("estimated from this text's own measured structure, not a "
                    f"token count — {basis.provenance}")
        return f"estimated — {basis.provenance}"

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
        """The plain statement of where the corpus stands against capacity.

        The over-capacity case is the EXPECTED case on a real matter, so it is
        phrased as a measurement, not as a failure."""
        capacity = self.capacity.capacity
        # "about", never "at least": DocIQ has no lower bound on token count
        # (finding B-6), so a lead-in that promises one is a false claim in the
        # one line the operator reads most.
        lead = "about "
        if self.fits():
            pct = 100.0 * self.tokens_after / capacity
            return f"{lead}{pct:.0f}% of direct-context capacity"
        factor = self.tokens_after / capacity
        # One decimal below 10x, none above: "96.9x" reads as precision the
        # figure does not have, and three digits plus a decimal overflows the
        # caption on a 1280-wide window.
        shown = f"{factor:.1f}" if factor < 10 else f"{factor:.0f}"
        return f"{lead}{shown}× above direct-context capacity"

    def route_line(self) -> str:
        """What to do about it — never a dead end.

        §8 Path B is the recommended route for forensic matters anyway: Expert
        Assist reads the matter folder from disk, where no container limit
        applies. Being over capacity changes which route you take, not whether
        the work can be done."""
        if self.fits():
            return ("It fits in a Claude Project as it stands — direct context, "
                    "no retrieval mode.")
        return ("This is normal for a full matter record. Analyze it with "
                "Expert Assist in Claude Cowork, which reads the matter folder "
                "straight from disk — no upload, no capacity limit. Uploading "
                "to a Project stays possible; it would run in retrieval mode.")

    @property
    def total_flagged(self) -> int:
        return sum(f.count for f in self.flags)

    def flag(self, key: str) -> FlagGroup:
        for f in self.flags:
            if f.key == key:
                return f
        raise KeyError(key)


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

    index = result.config.master_index
    if index is None:
        note = ("No master index supplied — document IDs are DocIQ's own "
                "DIQ- numbers.")
    else:
        note = (f"Document IDs taken from {index.filename} "
                f"({index.row_count:,} rows).")

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
