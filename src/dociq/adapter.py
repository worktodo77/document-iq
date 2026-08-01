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
    "MEASURED_SECONDS_PER_GB",
    "MEASURED_BASIS",
    "ESTIMABLE_EXTENSIONS",
]

# ---------------------------------------------------------------------------
# The one built-in profile
# ---------------------------------------------------------------------------

NO_PROFILE = ProfileInfo("none", "-", "No profile — keep every page", 0)
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
# The one measured throughput figure
# ---------------------------------------------------------------------------

MEASURED_SECONDS = 3046.7
MEASURED_GIGABYTES = 2.6
MEASURED_SECONDS_PER_GB = MEASURED_SECONDS / MEASURED_GIGABYTES
"""≈1,172 s/GB — the ONLY wall-clock rate DocIQ has measured end to end.

From the decision register, "§10 restated against a completed full-corpus run
(2026-07-31)": the D-12 corpus, **OCR disabled, from scratch, idle machine,
3,046.7 s**. Deliberately not the 2,848.5 s OCR-on figure, which resumed 62
documents from an interrupted attempt and is therefore not a from-scratch rate at
all; the register says so and refuses to restate ≈100 minutes as anything but an
upper bound.

Three things this rate cannot do, stated here because the field it feeds is a
single integer with nowhere to carry a caveat:

* **It cannot see OCR.** The measured run did none. The register puts OCR at
  ≈2.0–2.3× extraction on a corpus that was 2.6% scanned; a folder of scanned
  productions could take twice this or worse, and nothing knowable before the
  walk distinguishes the two. The estimate is therefore optimistic by
  construction on scanned material.
* **Its denominator is 2.6 GB to two significant figures** (D-12), and "GB" there
  is not stated as decimal or binary — about 7% of ambiguity before anything else.
* **It is one run on one machine.** A second measurement does not exist.

Which is why :func:`RealPipeline.preview_folder` returns 0 — "no estimate", and
the screen then says nothing — for any folder outside the shape this was measured
on, rather than extrapolating a number it cannot defend.
"""

MEASURED_BASIS = (
    "one measured run: the full MODEC/Petrobras corpus, OCR disabled, from "
    "scratch on an idle machine — 3,046.7 s for 2.6 GB (decision register, §10 "
    "restated 2026-07-31). OCR is not in this rate."
)

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


def _minutes_for(total_bytes: int, sized: dict[str, int]) -> int:
    """Wall clock for this folder under :data:`MEASURED_SECONDS_PER_GB`, or 0.

    Zero is the seam's documented "no estimate", and it is returned for every
    folder this rate was not measured on. See :data:`MEASURED_SECONDS_PER_GB` for
    what the rate does and does not cover.
    """
    if total_bytes <= 0:
        return 0
    gigabytes = total_bytes / 1_000_000_000
    if gigabytes > ESTIMABLE_MAX_GB:
        return 0
    covered = sum(b for ext, b in sized.items() if ext in ESTIMABLE_EXTENSIONS)
    if covered / total_bytes < ESTIMABLE_SHARE:
        return 0
    return round(gigabytes * MEASURED_SECONDS_PER_GB / 60)


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


def _plan(result: RunResult, before: TokenEstimate) -> ReductionPlan | None:
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
    sections: dict[str, list[int]] = {}
    for doc in result.documents:
        for page in doc.pages:
            if not page.section:
                continue
            row = sections.setdefault(page.section, [0, 0, 0])
            row[0] += vt.measure(page.text).pretokens
            row[1] += 1
            row[2] += 1 if page.disposition is Disposition.DROP else 0

    levers = tuple(
        ReductionLever(
            key=name,
            label=name,
            tokens=tok,
            pages=pages,
            kind=LEVER_EXPERT,
            # Engaged means "currently dropping". A section is dropping when its
            # pages are dropped — read off the run rather than off the profile,
            # because the run is what happened.
            engaged=dropped == pages,
            estimated=False,
        )
        for name, (tok, pages, dropped) in sorted(sections.items())
    )
    if not levers:
        return None
    return ReductionPlan(
        full_tokens=before.structural_tokens,
        levers=levers,
        basis=TokenBasis.of(before),
    )


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
            estimated_minutes=_minutes_for(total, sized),
        )

    def run(self, request: RunRequest, on_progress, should_cancel) -> RunOutcome:
        """One real run of §4's six stages, reported as the GUI reads it."""
        profiles = self._profiles_for(request)
        config = config_from(self._without_sentinel(request))
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
                # NOT auto-confirmed. §4 Stage 3 requires the detected Bates
                # format to be confirmed with the operator, and the seam has no
                # callback with which to ask — so a GUI run behaves exactly as
                # every other unattended run does: the format is detected, NOT
                # applied, and the run says so in its warnings. Setting this True
                # here would be the machine confirming on the expert's behalf and
                # recording that it had done so, which is worse than the gap.
                # Raised as a seam change; see the verification note.
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
            plan=_plan(result, before) if outcome.published else None,
            termination=outcome.termination,
            published=outcome.published,
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
        chosen = request.profile
        if chosen is None or chosen.profile_id == NO_PROFILE.profile_id:
            return ()
        directory = profile_library_dir(self._library_dir)
        path = directory / f"{chosen.profile_id}.v{chosen.version}.yaml"
        if not path.is_file():
            raise ProfileError(
                f"The profile '{chosen.label}' ({chosen.profile_id} "
                f"v{chosen.version}) is no longer in the profile library at "
                f"{directory}. It was offered when this run was set up and has "
                "been moved, renamed or deleted since. DocIQ will not run "
                "without the rules it was told to apply."
            )
        return (load_profile(path),)


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
