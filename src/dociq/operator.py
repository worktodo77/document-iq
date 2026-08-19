"""Who is running this, and when — the product's answer to a question of fact.

**Re-homed out of** ``dociq.profiles.model`` **in Sprint 4 (D-38).** It lived
there because §6 introduced it to record who saved a *profile*, and the profile
system is being deleted. But the stamp long ago stopped being about profiles: it
is what signs an expert's approved omission (D-34), what the processing log
records as the operator of a run, and what the determinism harness holds fixed so
two runs can be compared. Deleting the package it happened to sit in would have
taken all three with it.

Its own module rather than a corner of another one, because it answers one
question and nothing here should ever grow a second. No dependency on the
contract, the pipeline or the GUI — so anything may import it, and it can never
be the reason two packages are entangled.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["OperatorStamp", "operator_stamp"]


@dataclass(frozen=True, slots=True)
class OperatorStamp:
    """Who did this, and when.

    Frozen: a run records the operator it ran as, and a value a later stage
    could edit is not a record of anything.
    """

    username: str
    saved_at: str
    """ISO-8601 UTC, seconds precision. Seconds, not microseconds, because the
    stamp is read by humans and compared between runs."""

    host: str = ""


def operator_stamp(*, now: datetime | None = None) -> OperatorStamp:
    """Capture the current Windows user and time.

    ``USERNAME`` first because that is the Windows account name §6 asks for;
    :func:`getpass.getuser` is the cross-platform fallback so the test suite
    runs anywhere.

    ``now`` is injectable because a harness that needs two runs to compare must
    be able to hold the clock still — the alternative is a determinism proof
    that fails at midnight.
    """
    username = os.environ.get("USERNAME") or ""
    if not username:
        try:
            username = getpass.getuser()
        except Exception:  # pragma: no cover - only on an account-less host
            username = "unknown"
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return OperatorStamp(
        username=username,
        saved_at=moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        host=os.environ.get("COMPUTERNAME", ""),
    )
