"""How a run ENDED — the typed terminal status (Codex review #1, finding B-1).

Sprint 1 shipped a pipeline that could not tell a completed walk from an
aborted one. The disk preflight returned an empty
:class:`~dociq.contracts.RunResult`; a cancellation returned the documents
gathered so far. Both then travelled the ordinary road — ID assignment, the
stale-deliverable purge, emission, accounting, manifest — and both could report
success, because zero pages in equals zero pages kept plus zero pages dropped
and a partial corpus balances against itself.

The consequence is not a status-display bug. A blocked run purges the previous
run's deliverables and writes an empty set over them, so a failed disk check
destroys the last good reduction of a matter. That is provenance loss.

This module is the fix's vocabulary: one small record and the operator-facing
prose that goes with it.

The enumeration used to be declared here as well, on the reasoning that
:mod:`dociq.contracts` was frozen. Amendment A-06 then added a value-identical
:class:`~dociq.contracts.TerminalStatus` to the contract, and nothing
reconciled the two — the walk carried this module's class while
:class:`~dociq.contracts.RunResult` declared the contract's, so an ``is``
comparison across that seam answered ``False`` about two statuses that were the
same status (Codex review #1 round 2, F-1). Amendment A-07 settles it: the
contract's is the only definition and this module re-exports it. Importing
:mod:`dociq.contracts` costs the GUI nothing — the contract has no third-party
dependency, which is the property Track C's import rule is actually about.

Two properties do all the work:

* :attr:`RunTermination.complete` — the walk covered the whole inventory.
* :attr:`RunTermination.publishable` — this run is allowed to replace a
  previous run's deliverables. It is *derived from* ``complete`` rather than
  set independently, so no caller can grant publication rights to an aborted
  run by filling in a second field.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import RunResult, TerminalStatus

__all__ = [
    "TerminalStatus",
    "RunTermination",
    "RunAborted",
    "COMPLETED",
    "INCOMPLETE_DIR",
    "STATUS_FILENAME",
]


class RunAborted(Exception):
    """The operator abandoned the run from inside a callback the pipeline made.

    Stage 1's cancellation is polled — :class:`~dociq.ingest.walker.WalkOptions`
    carries a ``cancelled`` check and the walk asks it between files. Stage 3's
    Bates confirmation cannot work that way: the pipeline is *blocked inside the
    operator's answer*, and there is no later poll to reach. Closing the window
    while that prompt is open therefore needs a way out of the callback, and it
    must not be a return value — ``BatesConfirm`` returns ``bool``, and every
    bool it can return is a ruling. "The operator walked away" is not a ruling,
    and recording it as a refusal would put a decision in the log that nobody
    made.

    It lives here, in the module that owns how a run ENDS, for two reasons. It
    is the vocabulary of termination, not of Bates — any future mid-run question
    needs the same escape. And the GUI may raise it: Track C may not import
    ``identify``, ``ingest`` or ``emit``, but it already imports this module for
    :class:`RunTermination`, so the exception is reachable from both sides of
    the seam without widening the import rule by one package.

    :mod:`dociq.pipeline` catches it and takes the ORDINARY cancellation path —
    nothing published, the previous run's deliverables untouched,
    ``incomplete_run/`` written. It is not a crash and must never be reported as
    one.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or "the operator stopped the run")
        self.reason = reason or "the operator stopped the run"

INCOMPLETE_DIR = "incomplete_run"
"""Sub-directory of the output root where an aborted run records itself.

A separate directory, not a differently-named file beside the deliverables, so
that no artifact an incomplete run writes can ever occupy the name of a
deliverable a complete run wrote. ``processing_log.json`` inside it is the
aborted run's diagnostic log; ``processing_log.json`` at the root remains the
last COMPLETE run's audit trail, untouched."""

STATUS_FILENAME = "run_status.json"
"""The machine-readable record of an aborted run, inside :data:`INCOMPLETE_DIR`."""


@dataclass(frozen=True, slots=True)
class RunTermination:
    """The terminal status plus the actionable sentence that goes with it."""

    status: TerminalStatus = TerminalStatus.COMPLETED
    reason: str = ""
    """Why, in words the operator can act on. Empty only for
    :attr:`TerminalStatus.COMPLETED`."""

    @property
    def complete(self) -> bool:
        return self.status is TerminalStatus.COMPLETED

    @property
    def publishable(self) -> bool:
        """Whether this run may write deliverables and remove a previous run's.

        Derived, never stored. The whole finding is that a run which did not
        complete could nonetheless publish; a second independently-set field
        would be a second chance to get that wrong.
        """
        return self.complete

    def headline(self) -> str:
        """One line, for the summary screen, the PDF and the log."""
        if self.complete:
            return "Run status: completed — the walk covered every file found."
        word = "BLOCKED" if self.status is TerminalStatus.BLOCKED else "CANCELLED"
        return (
            f"RUN {word} — NO DELIVERABLES WERE WRITTEN and the previous "
            f"run's outputs in this folder were left exactly as they were. "
            f"{self.reason}"
        ).strip()

    def stamp(self, result: RunResult) -> RunResult:
        """Return ``result`` carrying THIS termination in its contract fields.

        The one way a :class:`~dociq.contracts.RunResult` is allowed to acquire
        a terminal status, and the reason F-1 cannot come back. Amendment A-06
        added ``terminal_status`` with a COMPLETED default so the change would
        be additive, and every abort path then took the default: the outcome
        wrapper said ``blocked`` while the machine contract in the same object
        said ``completed``, which is worse than the field's absence. Defaulting
        made the change safe to land and made forgetting it silent.

        A method rather than two keyword arguments at each site because the two
        fields must agree with each other and with :class:`RunNotes`; one call
        cannot set half of them, and one call cannot set them from a different
        termination than the one the pipeline is about to act on.
        """
        return replace(
            result,
            terminal_status=self.status,
            terminal_status_reason=self.reason,
        )

    def as_jsonable(self) -> dict[str, object]:
        return {
            "terminal_status": self.status.value,
            "terminal_status_reason": self.reason,
            "complete": self.complete,
            "published": self.publishable,
        }


COMPLETED = RunTermination()
"""The default. Named so a construction site reads as a claim rather than as an
empty constructor call."""
