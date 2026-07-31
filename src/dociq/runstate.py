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

This module is the fix's vocabulary: one enumeration and one small record,
carried beside the contract rather than inside it. It deliberately does not
live in :mod:`dociq.contracts` — that module is frozen, and widening it is a
stop-the-line event across three tracks (see ``docs/contracts/amendments.md``
A-05, which proposes exactly that as the eventual home). Nothing here imports a
third-party library or another DocIQ package, so the GUI may depend on it under
the freeze's Track-C import rule.

Two properties do all the work:

* :attr:`RunTermination.complete` — the walk covered the whole inventory.
* :attr:`RunTermination.publishable` — this run is allowed to replace a
  previous run's deliverables. It is *derived from* ``complete`` rather than
  set independently, so no caller can grant publication rights to an aborted
  run by filling in a second field.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

__all__ = [
    "TerminalStatus",
    "RunTermination",
    "COMPLETED",
    "INCOMPLETE_DIR",
    "STATUS_FILENAME",
]

INCOMPLETE_DIR = "incomplete_run"
"""Sub-directory of the output root where an aborted run records itself.

A separate directory, not a differently-named file beside the deliverables, so
that no artifact an incomplete run writes can ever occupy the name of a
deliverable a complete run wrote. ``processing_log.json`` inside it is the
aborted run's diagnostic log; ``processing_log.json`` at the root remains the
last COMPLETE run's audit trail, untouched."""

STATUS_FILENAME = "run_status.json"
"""The machine-readable record of an aborted run, inside :data:`INCOMPLETE_DIR`."""


class TerminalStatus(str, enum.Enum):
    """How a run finished. The values reach disk (``processing_log.json``'s
    ``run`` section, ``incomplete_run/run_status.json``), so renaming one is a
    consumer-visible change."""

    COMPLETED = "completed"
    """The walk covered every file the scan found. The only status under which
    deliverables may be published."""

    BLOCKED = "blocked"
    """A preflight refused to start: the source folder is not there, or the
    output volume cannot hold the result. No file was extracted."""

    CANCELLED = "cancelled"
    """The operator stopped the run. Whatever was extracted is partial by
    definition and makes no completeness claim over the corpus."""


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
