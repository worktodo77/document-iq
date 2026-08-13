"""Output layout, the single deterministic write path, and the staging swap.

Two files exist to be boring: the directory names every emitter agrees on, and
one text-writing function. The second matters more than it looks — a single
``open(..., "w")`` without ``newline=""`` on Windows turns every ``\\n`` into
``\\r\\n`` and silently breaks the byte-identical claim for that one file. There
is one write path so there is one place that can go wrong.

The third thing here is the **staging swap** (Sprint-1 merge readiness note, NOT
PROVEN item 8). Stage 5 used to purge the previous run's deliverables and then
write the new ones straight into the matter folder, so a crash anywhere in
between left the folder holding half of one run and half of another — with
nothing on disk saying so. Deliverables are now written into a staging directory
and moved into place at the end, and an interrupted swap is rolled forward by the
next run rather than left as a mixture. What that does and does not guarantee is
stated on :func:`commit_staging`, in the terms the note used, rather than
softened.

**DELETE-LAST (D-31, 2026-08-05) — read this before changing anything below.**
Three consecutive Codex rounds each found a new defect inside the previous
round's fix, all in this subsystem and all one class: *a destructive filesystem
step that cannot be proven complete when antivirus, a scanner, or Windows
delete-on-close interferes.* B-1/B-2, then B-4/B-5, then B-6 (a marker whose
``unlink`` returned while its name survived authorized the next recovery to
delete the newly published set). Every fix was correct and every fix opened the
next window, **because the design deleted before it published, so every failure
mode was "half-deleted".**

**AND THEN B-8** (third fix round), which is a different class and is why this
paragraph does not end here. Delete-last was implemented correctly and the thing
it renamed aside was **not the previous set** — it was the names *this build's*
output patterns enumerate. A deliverable an older build wrote under a retired
name either survived a successful swap or was moved aside lazily, after new
files had already landed. So the swap now builds its plan from
:func:`published_inventory`, a durable record of what the last run actually
published, and clears every destination in a complete pass before publishing
anything. Re-enumerating the states backwards from the disk — the exercise that
round required — found three more whose next step destroyed a complete set; they
are the ``PHASE_ASIDE``/``PHASE_PUBLISHED`` dispatch in :func:`commit_staging`.
The table is in ``docs/verification/codex_r4_inventory_2026-08-06.md`` §2, and it
is the thing to read before changing this module.

So the swap no longer deletes anything at the matter root. It **renames**. The
current set is renamed aside into ``.dociq/``, the staged set is renamed into
place, and only then is what was moved aside deleted. The substitution carries
the whole design: *a rename on one volume either happened or it did not, and its
outcome is readable from the names on disk*, whereas a delete under lock is
neither provable nor reversible.

What that buys, and what the tests below are written against:

* a crash or a blocked step leaves a clearly-named stale folder under
  ``.dociq/`` **beside** whichever complete set is at the root — never a mixture
  of two runs' evidence;
* :func:`recover_pending` **never deletes anything at the matter root**. Its
  destructive scope is ``.dociq/`` and nothing else, so a stale marker cannot
  authorize destroying a set that is already in place (B-6 is unreachable rather
  than defended against). It does NOT follow that a published file is safe from
  it — the set-aside tree it deletes can hold deliverables the last completed run
  published, and whether deleting it is safe is :func:`classify_swap`'s judgement
  rather than a property of the code's shape. The stronger claim was made for a
  round and withdrawn in the fourth (F-1);
* recovery reads **the names on disk** as its primary evidence — what is left in
  staging *compared against what the marker recorded*, and what is in this
  marker's own set-aside tree. The marker records the plan; it is not the sole
  authority for destroying anything.

**THE FOURTH ROUND'S LESSON, and it is about the enumeration rather than the
code.** The third round's state table was checked row by row and every row it
expressed was sound. Three more defects lived in rows its AXES could not express:
``staging`` was binary (holds files / empty) and could not say WHICH files, and
the set-aside axis conflated residue from a completed swap with this marker's own
partially-completed step 1. Widening those two axes, and adding
:data:`PHASE_PUBLISHING` so that "has anything been published yet" is read from
the marker instead of guessed from a name, is what produced the fixes — not
patching three findings one at a time. :func:`classify_swap` is the table, it is
pure, and it is tested as a table.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..contracts import DocIQError

__all__ = [
    "OutputLayout",
    "write_text_deterministic",
    "replace_text_deterministic",
    "safe_component",
    "STATE_DIRNAME",
    "STAGING_DIRNAME",
    "ASIDE_PREFIX",
    "MARKER_NAME",
    "PUBLISHED_NAME",
    "PHASE_PENDING",
    "PHASE_ASIDE",
    "PHASE_PUBLISHING",
    "PHASE_PUBLISHED",
    "SwapPlan",
    "SwapState",
    "classify_swap",
    "SWAP_ROLL_FORWARD",
    "SWAP_ROLL_BACK",
    "SWAP_ABANDONED",
    "SWAP_FINISH",
    "SWAP_REFUSE",
    "staging_layout",
    "discard_staging",
    "mark_ready",
    "commit_staging",
    "recover_pending",
    "pending_swap",
    "superseded_residue",
    "published_inventory",
    "covering_plan",
    "PendingSwapUnreadable",
    "PendingSwapUnrecoverable",
    "PublishedSetUnreadable",
]

_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_component(name: str) -> str:
    """Make a string safe as a single Windows path component.

    Doc IDs are already safe by construction (``LI-06881.01`` uses only A-Z,
    digits, ``-`` and ``.``), so on the normal path this is the identity. It
    exists for the abnormal path: a caller passing an operator-supplied string
    must not be able to write outside the output root, and a trailing dot or a
    reserved device name must not produce a file Windows refuses to open.
    """
    cleaned = _UNSAFE_RE.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        return "_"
    if cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


@dataclass(frozen=True, slots=True)
class OutputLayout:
    """Every path DocIQ writes, derived from one root.

    ``clean_text/`` and ``sources.json`` sit exactly where Expert Assist's
    evidence-mining expects them (§8 Path B), so the matter folder is
    analysis-ready with no rearrangement.
    """

    root: Path

    @staticmethod
    def at(root: str | Path) -> "OutputLayout":
        return OutputLayout(Path(root))

    @property
    def clean_text(self) -> Path:
        return self.root / "clean_text"

    @property
    def sources_json(self) -> Path:
        return self.root / "sources.json"

    @property
    def index_xlsx(self) -> Path:
        return self.root / "document_index.xlsx"

    @property
    def index_csv(self) -> Path:
        return self.root / "document_index.csv"

    @property
    def processing_log(self) -> Path:
        return self.root / "processing_log.json"

    @property
    def run_summary(self) -> Path:
        return self.root / "run_summary.pdf"

    @property
    def upload_package(self) -> Path:
        return self.root / "upload_package"

    @property
    def issued_ids(self) -> Path:
        """The D-04 renumbering ledger. Lives beside the matter so the *next*
        run can compare against it without an external store."""
        return self.root / "doc_ids_issued.json"

    def clean_text_file(self, doc_id: str) -> Path:
        return self.clean_text / f"{safe_component(doc_id)}.txt"

    def ensure(self) -> "OutputLayout":
        self.root.mkdir(parents=True, exist_ok=True)
        self.clean_text.mkdir(parents=True, exist_ok=True)
        return self


def write_text_deterministic(path: Path, text: str) -> Path:
    """Write UTF-8, LF-only, no BOM, with the newline translation disabled.

    ``newline=""`` is what stops Windows rewriting ``\\n`` as ``\\r\\n``. The
    text is checked rather than trusted: a stray ``\\r`` reaching disk would
    make a rerun on another machine produce different bytes for the same
    content, and the determinism proof would fail somewhere far away from the
    cause.
    """
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def replace_text_deterministic(path: Path, text: str) -> Path:
    """:func:`write_text_deterministic`, but the file never exists half-written.

    The bytes go to a sibling temporary name and are then moved onto ``path``
    with :func:`os.replace`, which is atomic on NTFS and on POSIX alike. A
    process that dies at any instant leaves either the previous file or the new
    one — never a prefix of the new one.

    This exists because of Codex review #2, finding B-2. The swap's readiness
    marker was written with the plain writer, so a crash inside that single
    ``fh.write`` could leave a marker holding truncated JSON. The marker's
    *existence* is what authorizes deleting the previous run's deliverables, so
    a marker that exists and cannot be read is the one state in this module
    where "exists" and "says" disagree — and the recovery had to guess. Making
    the write atomic removes the state rather than handling it.

    The temporary name is derived from the target and removed on failure, so a
    dead run cannot leave litter that a later ``rglob`` would move into the
    matter folder. It is only ever used inside ``.dociq/``, which the manifest
    excludes; a caller that pointed it at a deliverable would still be safe,
    because the temporary is gone before this returns either way.
    """
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # Retried for the same reason the swap's moves are — see `_retry_io`.
        _retry_io(lambda: os.replace(tmp, path))
    except BaseException:
        # Including KeyboardInterrupt: a cancelled run must not leave a
        # `.partial` beside the marker it failed to write.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Staging and the swap
# ---------------------------------------------------------------------------

STATE_DIRNAME = ".dociq"
"""DocIQ's own run state inside the matter folder — never a deliverable.

The same literal as :data:`dociq.ingest.walker.STATE_DIR`, restated rather than
imported because :mod:`dociq.emit` must not depend on :mod:`dociq.ingest`. A
copied constant is a constant that can drift, so ``tests/test_emit_atomicity.py``
asserts the two are equal, and :func:`dociq.verify.manifest.build` already skips
this prefix — which is what keeps a staging directory from ever being reported as
an unclassified output.
"""

STAGING_DIRNAME = "staging"
"""Where a run builds its deliverables before they replace the previous run's.

Inside the destination root, not in ``%TEMP%``, for one reason that is not
stylistic: the swap has to be a **move within one filesystem**. A staging
directory on another volume turns every move into a copy, which reopens exactly
the window this exists to close — and on a matter folder that is a network share,
it would copy the whole corpus twice.
"""

ASIDE_PREFIX = "superseded"
"""Where the CURRENT set is renamed to while the staged set takes its place.

Under ``.dociq/``, for the reason §7 forces: Expert Assist reads the matter
folder from disk with no rearrangement (D-20, Path B), so a set-aside name at the
matter root would be a folder full of deliverable-shaped files sitting beside the
deliverables. ``.dociq/`` is DocIQ's own state, the manifest excludes the whole
prefix, and it is on the same volume as the root — which is what makes the move a
**rename** rather than a copy.

The name is chosen per swap (``superseded``, ``superseded.1``, …) and recorded in
the marker, so a set-aside tree that could not be deleted never blocks or is
overwritten by the next run's."""

MARKER_NAME = "staging_ready.json"
"""Written when staging is COMPLETE and the swap may begin; removed when the
swap has finished. It records the swap PLAN — which of this folder's names the
staged set replaces, which ``.dociq/`` name they are being renamed to, and which
phase the swap reached — and it is what makes an interrupted swap recoverable;
see :func:`commit_staging`.

**It is no longer the sole authority for anything destructive** (D-31). Under the
delete-first design a marker was what authorized deleting the previous run's
deliverables, so a marker whose ``unlink()`` returned while its name survived
authorized the next recovery to delete the newly published set (Codex review #2,
second fix round, B-6). Recovery now reads the NAMES ON DISK first — what is left
in staging, and what is already set aside — and the marker's plan can only ever
select paths to *rename into* ``.dociq/``. A stale marker beside an empty staging
directory therefore selects nothing.

Written by :func:`replace_text_deterministic`, so it is never observed
half-written: the marker either does not exist or holds the whole document. A
marker that nonetheless cannot be parsed is corruption or a hand edit, and
:func:`commit_staging` refuses to act on it rather than guessing — see
:class:`PendingSwapUnreadable`."""

PUBLISHED_NAME = "published_set.json"
"""The DURABLE INVENTORY of the set currently published in the matter folder.

Codex review #2, third fix round, **B-8**. D-31's load-bearing claim is that the
*whole* previous set leaves the matter root before any new file enters it. The
set-aside plan did not inventory that set — it expanded
:data:`dociq.pipeline._STALE_PATTERNS`, *this version's* output names. Two things
follow from that, and Codex reproduced both on Windows:

* a deliverable an OLDER DocIQ wrote at a name this version no longer names, but
  still writes a replacement for, was recognized only when publication reached
  the corresponding staged file — by which time earlier staged files had already
  landed. A real open handle on the old file then froze the swap at phase
  ``aside`` with ``a_new.txt = NEW`` beside ``z_legacy.txt = OLD``: the mixed set
  D-31 says is unreachable;
* a deliverable an older DocIQ wrote and this version RETIRED has no staged
  successor at all, so nothing ever noticed it. The swap completed, removed the
  marker, and left the old file beside the new set permanently. §4 Stage 6 cannot
  catch that, because the manifest is built over STAGING, not over the
  destination root.

So the plan is built from what the last run actually published, recorded here
when it published it, rather than from what this build happens to know how to
write. **That is the point: the inventory survives a version change.** A run can
set aside a file whose name its own pattern list has never heard of.

It lives under ``.dociq/`` because §7's layout is fixed — Expert Assist reads the
matter folder with no rearrangement (D-20, Path B) — so a manifest-shaped file at
the matter root is not available. It is DocIQ's own state, excluded from the
manifest with the rest of the prefix, and it reaches no hashed artifact
(criterion 7).

Written by :func:`replace_text_deterministic` like the marker, so it is never
observed half-written. Until it lands, the marker itself carries the same list —
see :func:`published_inventory` for why that fallback is not belt-and-braces but
the thing that makes the record durable across a lock on this one file."""

PHASE_PENDING = "pending"
"""Nothing has moved yet, or the set-aside renames are unfinished."""

PHASE_ASIDE = "aside"
"""Every planned name is out of the matter folder and in ``.dociq/<aside>/``.
The destination sweep and the publish renames remain. **Nothing of the staged
set is in the matter folder at this phase**, which is what lets the next run read
a published name at the root as belonging to the PREVIOUS generation."""

PHASE_PUBLISHING = "publishing"
"""Step 2a is complete: every name the staged set will take is FREE.

Added in the fourth fix round, and it is an axis rather than a nicety. Without
it, "has any staged file entered the matter folder yet?" was not readable from
disk — `aside` covered both "nothing published" and "half published", and the
only way to tell them apart was to test whether a published NAME exists at the
root. A name is not an identity: ``sources.json`` is at the matter root before
the swap starts, because the previous run put it there. Recovery therefore could
not distinguish the previous generation from this one, and F-4 turned that into a
data-loss path.

With this phase the question is answered by the marker instead of guessed from a
name. At ``pending`` and ``aside`` **no staged file has been published**, full
stop. At ``publishing`` and ``published`` a claimed name at the matter root is
provably this run's, because reaching this phase required step 2a to have
vacated every one of those names first."""

PHASE_PUBLISHED = "published"
"""The staged set holds the published names. Everything that remains is deletion
UNDER ``.dociq/``, and no failure of it can touch a deliverable — which is why
this phase exists as a written record rather than being inferred."""

_PHASES = (PHASE_PENDING, PHASE_ASIDE, PHASE_PUBLISHING, PHASE_PUBLISHED)


@dataclass(frozen=True, slots=True)
class SwapPlan:
    """What the readiness marker says, validated.

    ``superseded`` are matter-root-relative names the staged set replaces;
    ``aside`` is the single ``.dociq/`` component they are renamed into; ``phase``
    is how far the swap got. All three are checked at parse time rather than
    trusted, because they select paths that get *moved* — see
    :func:`_validate_superseded_entry` and :func:`_validate_aside_name`.

    ``published`` is the fourth, added for B-8: the matter-root-relative names
    the STAGED set will occupy once this swap finishes — that is, the inventory
    the *next* run must set aside in full. It rides in the marker rather than
    being recomputed at the end of the swap because by then there is nothing left
    to compute it from: a roll-forward that re-enters at phase ``published``
    finds an empty staging directory, and an inventory it could not write would
    silently become the previous run's. See :data:`PUBLISHED_NAME`.
    """

    superseded: tuple[str, ...]
    aside: str
    phase: str
    published: tuple[str, ...] = ()


class PendingSwapUnreadable(DocIQError):
    """A readiness marker exists and cannot be trusted to say what it means.

    Codex review #2, finding B-2. The marker used to be read permissively: an
    ``OSError`` or a ``ValueError`` was absorbed and the swap proceeded with an
    EMPTY supersede list. That reads as conservative and is the opposite.

    Run 1 publishes 100 clean-text files. Run 2 legitimately produces 80, stages
    them, and dies while writing the marker. Under the permissive read the next
    run moves run 2's 80 files over the destination, leaves run 1's other 20
    beside them, and deletes the marker — so the matter folder becomes a
    hundred-file set carrying an eighty-file manifest, with no marker left to say
    that anything happened. Those twenty stale exhibits are indistinguishable
    from current evidence, and the state that would have disclosed them was
    deleted by the recovery that created it.

    So an unreadable marker FAILS CLOSED. Nothing moves, nothing is deleted, the
    marker and the staged set are both left exactly as they are, and the run
    stops with this error. The complete staged set is not lost — it is in
    ``.dociq/staging/``, named in the message, and a repaired or removed marker
    puts the folder back on a road the code can reason about. That is a worse
    Tuesday than a silent roll-forward and a better one than an evidence set
    nobody can tell is mixed.

    Note the asymmetry that decides it: the permissive branch's cost is a wrong
    answer that looks right, and this one's cost is a run that stops and says
    why. Only one of those is discoverable by the person holding the folder.
    """


def _retry_io(what, *, attempts: int = 8, delay: float = 0.02):
    """Run ``what``, retrying an ``OSError`` with a short doubling backoff.

    **Found by repetition, not by reasoning.** The B-2 fix made an unreadable
    marker fail closed, and the thirtieth repeat of the fix round's own suite
    then went red on an ordinary run: ``PermissionError`` reading the marker
    DocIQ had written one statement earlier. That is not corruption. It is a
    Windows file lock — antivirus and backup agents (Carbonite is documented in
    this project's environment notes) hold a transient deny-write on a file the
    instant it is created, and the swap touches every deliverable in the matter.

    The defect it exposed is older than the fix. Under the permissive read this
    replaced, that same ``PermissionError`` was swallowed into ``superseded =
    ()`` — so on a real matter folder, an antivirus scan at the wrong moment
    produced exactly B-2's mixed evidence set, with no crash needed. The fix
    made an invisible failure loud, and this makes the loud one correct.

    **Transient I/O and corrupt state are different things, and the difference
    is the whole design.** Only ``OSError`` is retried, and only where the
    operation is idempotent. A marker whose JSON does not parse is never
    retried: re-reading the same bytes cannot make them valid, and a retry loop
    there would be a delay dressed up as a check.

    Eight attempts with a doubling backoff from 20 ms is **2.54 s** of waiting
    (0.02 × (1+2+…+64)), which is the order an on-access scan takes on one file.
    The figure is stated because a retry budget nobody has multiplied out is a
    number that drifts; after it, the caller's fail-closed path is the right
    answer and waiting longer only delays the operator learning that.
    """
    import time

    last: OSError | None = None
    for attempt in range(attempts):
        try:
            return what()
        except OSError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(delay * (2 ** attempt))
    if last is None:  # unreachable, and not an `assert`: -O strips those
        raise RuntimeError("_retry_io exhausted its attempts without an error")
    raise last


def _rename_or_fail(src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst`` by RENAME, retried, and proven by the names.

    **This is the operation D-31 substitutes for deletion**, and the whole
    argument for the redesign is the difference between the two. A rename on one
    volume either happened or it did not, and which of those is true is readable
    from the two names afterwards: there is no "marked for rename on close"
    state, no partial rename, and no rename that removes half of what it moved.
    A delete under an antivirus handle has all three problems, which is what
    three fix rounds kept discovering one window at a time.

    ``os.rename`` rather than ``os.replace``: on Windows it REFUSES when the
    destination exists, and that refusal is wanted. ``os.replace`` would silently
    destroy whatever occupied the destination, which is a delete before a publish
    wearing a different name. Callers that meet an occupied destination move the
    occupant aside first.

    Retried for the reason :func:`_retry_io` gives, and idempotent under retry:
    an attempt that landed and then reported an error leaves ``src`` gone and
    ``dst`` present, which the first branch recognizes as done.
    """

    def once() -> None:
        if not src.exists() and dst.exists():
            return  # a previous attempt landed
        os.rename(src, dst)
        if src.exists() or not dst.exists():
            raise OSError(
                errno.EEXIST,
                f"the rename to {dst} did not take effect",
                str(src),
            )

    _retry_io(once)


def _remove_tree_or_fail(path: Path) -> None:
    """Remove a directory and PROVE it is gone, or raise.

    Codex review #2 fix round, finding B-4. The retry discipline
    :func:`_retry_io` describes was applied to file ``unlink`` and to the staged
    moves, and the one destructive step it was not applied to was the one that
    removes a superseded *directory*: ``shutil.rmtree(path,
    ignore_errors=True)``. ``ignore_errors`` is not "best effort" here, it is
    "unobservable failure" — the caller then recorded the directory as removed,
    moved the new set in on top of whatever survived, and deleted the readiness
    marker. An antivirus holding one old package file open was enough to publish
    a folder holding two builds with nothing left on disk saying so.

    So the claim the fix round made — that every destructive swap step is
    retried and roll-forward remains possible — was false for this step, and is
    withdrawn together with the code that made it false.

    Two things are needed rather than one. ``rmtree`` without ``ignore_errors``
    raises on the *first* failure, which the retry handles; but a removal can
    also report success on Windows while a directory entry lingers (a handle
    still open marks the file for delete-on-close and the name remains until it
    is released). ``exists()`` after the fact is therefore checked as well: what
    the caller needs to know is not whether ``rmtree`` returned, it is whether
    the name is gone. Anything else and the marker would be deleted over a
    directory that is still there.

    Idempotent, which is what makes the roll-forward safe: on the next attempt
    an already-removed path is not a directory, the caller skips it, and the
    partially-completed removal is simply finished.

    **Its scope narrowed to ``.dociq/`` under D-31.** It used to delete a
    superseded deliverable out of the matter folder, and B-4's fix made that
    failure loud rather than silent. It is now only ever pointed at DocIQ's own
    state — a set-aside tree or a drained staging tree — so a failure here can no
    longer leave the evidence set in any state at all. The B-4 reasoning still
    applies to it and is kept: a caller that cannot prove the name is gone must
    not report the directory removed.
    """

    def once() -> None:
        if path.exists():
            shutil.rmtree(path)
        if path.exists():
            raise OSError(
                errno.ENOTEMPTY,
                "the directory is still present after it was removed",
                str(path),
            )

    _retry_io(once)


def _validate_superseded_entry(rel: object) -> str:
    """A supersede entry names a file INSIDE the matter folder, or it is refused.

    The entries drive ``unlink`` and ``rmtree``, and before this they were joined
    to the matter root and acted on unchecked. ``..\\..\\Windows`` or ``C:/`` in
    a hand-edited or corrupt marker was a delete outside the output root. That is
    the same finding as B-2 one layer down — state read from disk deciding a
    destructive act — so it is checked where the state is parsed rather than
    trusted because "we wrote it".
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"superseded entry is not a non-empty string: {rel!r}")
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.is_absolute() or ":" in rel or rel.startswith("/"):
        raise ValueError(f"superseded entry is not relative: {rel!r}")
    if any(part in ("..", "") for part in pure.parts):
        raise ValueError(f"superseded entry escapes the matter folder: {rel!r}")
    if pure.parts and pure.parts[0] == STATE_DIRNAME:
        # DocIQ's OWN state is not a deliverable and may never be selected for
        # moving (fourth fix round, F-4). `covering_plan` carried this guard and
        # this function — the layer whose whole reason for existing is that
        # state read from disk decides a destructive act — did not, which is the
        # guard in the wrong place. A marker naming `.dociq/staging` renames the
        # STAGED SET into the set-aside tree: the swap then publishes nothing,
        # and the next run, seeing the previous generation still under the
        # published names, reports a clean cleanup and deletes what it moved.
        raise ValueError(
            f"superseded entry names DocIQ's own state directory, which is not "
            f"a deliverable and is never moved by a swap: {rel!r}")
    return rel


def _validate_aside_name(name: object) -> str:
    """The set-aside directory is ONE component under ``.dociq/``, or refused.

    The same check as :func:`_validate_superseded_entry` pointed the other way.
    That one stops a marker naming something outside the matter folder to be
    moved; this one stops a marker naming somewhere outside ``.dociq/`` to move
    it TO — and, at the end of the swap, to delete. A hand-edited ``"aside":
    "../clean_text"`` would otherwise make the cleanup step delete the
    deliverables it exists to preserve.

    The ``superseded`` prefix is required as well as the shape, so the only names
    this can ever delete are ones DocIQ's own :func:`_free_aside_name` could have
    issued.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"`aside` is not a non-empty string: {name!r}")
    if name != safe_component(name) or "/" in name or "\\" in name:
        raise ValueError(f"`aside` is not a single safe path component: {name!r}")
    if name != ASIDE_PREFIX and not name.startswith(ASIDE_PREFIX + "."):
        raise ValueError(
            f"`aside` is not a DocIQ set-aside name (it must be "
            f"{ASIDE_PREFIX!r} or {ASIDE_PREFIX}.N): {name!r}"
        )
    return name


def _read_marker(marker: Path) -> SwapPlan:
    """The swap plan a readiness marker declares, or fail closed.

    Every deviation is fatal, including ones a permissive reader would shrug at
    (a missing ``superseded`` key, a non-list, a non-string entry): the marker is
    written by exactly one function, atomically, so anything this cannot parse is
    corruption or a hand edit, and neither is a state to guess through.
    """
    def refuse(why: str, cause: BaseException | None = None):
        exc = PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker, why=why, staging=marker.parent / STAGING_DIRNAME))
        if cause is not None:
            raise exc from cause
        raise exc

    try:
        raw = _retry_io(lambda: marker.read_text(encoding="utf-8"))
    except OSError as exc:
        refuse(f"it could not be read ({exc})", exc)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        refuse(f"it is not valid JSON ({exc})", exc)
    if not isinstance(payload, dict):
        refuse(f"its top level is {type(payload).__name__}, not an object")
    if "superseded" not in payload:
        refuse("it names no `superseded` list")
    entries = payload["superseded"]
    if not isinstance(entries, list):
        refuse(f"`superseded` is {type(entries).__name__}, not a list")
    try:
        superseded = tuple(_validate_superseded_entry(e) for e in entries)
    except ValueError as exc:
        refuse(str(exc), exc)
    # `aside` and `phase` are required with the same strictness as `superseded`
    # and for the same reason: they are read from disk and they select what gets
    # moved and, later, deleted. A marker without them is not an older format —
    # nothing has shipped — it is a marker this code did not write.
    if "aside" not in payload:
        refuse("it names no `aside` directory for the set it moves aside")
    try:
        aside = _validate_aside_name(payload["aside"])
    except ValueError as exc:
        refuse(str(exc), exc)
    phase = payload.get("phase")
    if phase not in _PHASES:
        refuse(f"its `phase` is {phase!r}, not one of {_PHASES}")
    # `published` is required with the same strictness and for a related reason
    # (B-8): it becomes the durable inventory the NEXT run's set-aside plan is
    # built from, so a marker that does not carry it would publish a set the
    # following run cannot enumerate. Validated with the supersede-entry check
    # because it is written back out as a list of matter-root-relative names that
    # a later run will select for renaming.
    if "published" not in payload:
        refuse("it names no `published` inventory for the set it publishes")
    listed = payload["published"]
    if not isinstance(listed, list):
        refuse(f"`published` is {type(listed).__name__}, not a list")
    try:
        published = tuple(_validate_superseded_entry(e) for e in listed)
    except ValueError as exc:
        refuse(f"in `published`: {exc}", exc)
    return SwapPlan(
        superseded=superseded, aside=aside, phase=phase, published=published)


_UNREADABLE = (
    "REFUSING to complete an interrupted swap: the readiness marker at "
    "{marker} exists but {why}.\n"
    "Nothing has been moved or deleted — this folder is exactly as the "
    "interrupted run left it.\n"
    "A COMPLETE set of deliverables is waiting in {staging}. The marker says "
    "which of this folder's files that set replaces, and without it a "
    "roll-forward would leave the ones it cannot name beside the new set, with "
    "nothing recording the mixture.\n"
    "To go on: either restore a readable marker, or move {staging} aside and "
    "delete the marker to re-run from the source documents."
)


SWAP_ROLL_FORWARD = "roll_forward"
"""Finish the swap: publish the staged set."""

SWAP_ROLL_BACK = "roll_back"
"""Undo it: the staged set can never be completed, so put the previous set back."""

SWAP_ABANDONED = "abandoned"
"""Neither: nothing was staged and nothing was set aside. Clear the marker."""

SWAP_FINISH = "finish"
"""The publish is done; only DocIQ's own state under ``.dociq/`` remains."""

SWAP_REFUSE = "refuse"
"""The next step is not deducible. Leave everything and say so."""


@dataclass(frozen=True, slots=True)
class SwapState:
    """What an interrupted swap is, read from the marker and the names on disk."""

    action: str
    why: str


def classify_swap(
    plan: "SwapPlan",
    staged: frozenset[str],
    aside_holds: bool,
    landed: frozenset[str],
) -> SwapState:
    """Decide what to do with an interrupted swap. **Pure — no disk, no I/O.**

    This function IS the state table in
    ``docs/verification/codex_r4_inventory_2026-08-06.md`` §2. It was written as
    a table first and the branches were derived from it, which is the opposite of
    how the three defects below arrived: each came from a state the previous
    round's table could not express, because its axes were too coarse.

    **The axes, and what each one had to be widened to say.**

    * ``plan.phase`` — now four values, not three. :data:`PHASE_PUBLISHING`
      separates "no staged file has entered the matter folder" from "some have",
      which no combination of the other axes could express. See its docstring.
    * ``staged`` — the staged names, compared against ``plan.published`` rather
      than tested for emptiness. The old axis was binary (holds files / empty)
      and could not say **which** files, so a staging directory 30 files short of
      what the marker recorded read as "holds files": the swap moved the whole
      previous set aside, published the 74 that were left, recorded ``published``
      and deleted the previous set. The matter root then held a 74-file set under
      a 104-file manifest (F-2).
    * ``aside_holds`` — whether the PLAN'S OWN set-aside tree holds anything.
      The old axis conflated that with residue from an earlier, completed swap,
      which is a different thing entirely: residue is a set that has already been
      replaced and is always expendable, while this tree may hold the only copy
      of half the current previous set. A crash inside step 1 leaves the marker
      at ``pending`` — it is only rewritten after the whole loop — so a
      ``pending`` marker beside a NON-EMPTY aside tree means the previous set is
      SPLIT. The old code read the empty staging directory, called the marker
      abandoned, restored nothing, and deleted the half that had moved (F-1).
    * ``landed`` — which of ``plan.published`` are at the matter root. Meaningful
      only at ``publishing``/``published``, where step 2a has proven those names
      were free; the caller must pass an empty set at the earlier phases, because
      a claimed name at the root is then the PREVIOUS generation's file under the
      same name. Identity comes from the phase, never from the name (F-4).

    Returning a value rather than acting means the table can be tested directly,
    without constructing each state on a real filesystem — and the states that
    matter most here are the ones the code cannot itself produce.
    """
    claimed = frozenset(plan.published)

    extra = staged - claimed
    if extra:
        return SwapState(SWAP_REFUSE, (
            "the staging directory holds "
            f"{len(extra)} file(s) the marker does not claim it would publish "
            f"({', '.join(sorted(extra)[:3])}"
            f"{', …' if len(extra) > 3 else ''}). Publishing them would put "
            "deliverables in this folder that the durable inventory would not "
            "record, so the run after this one could not move them aside"))

    if plan.phase == PHASE_PUBLISHED:
        # The publish loop raises rather than recording `published` over an
        # unpublished file, so a staging directory with anything in it here was
        # assembled by something other than this code.
        if staged:
            return SwapState(SWAP_REFUSE, (
                f"the staging directory still holds {len(staged)} file(s) after "
                "a marker that says the publish is finished"))
        return SwapState(SWAP_FINISH, (
            "the staged set is fully published; only DocIQ's own state under "
            ".dociq/ remains"))

    # ---- The three questions, in the order that makes them independent ----
    #
    # 1. Can the staged set still be published AS A SET? Only if every name the
    #    marker claims is either still staged or already published.
    # 2. Has any of it been published yet? `landed` answers this, and the caller
    #    only supplies it where the phase makes it MEANINGFUL — at `pending` and
    #    `aside` a claimed name at the matter root is the previous generation's
    #    file under the same name.
    # 3. Is the previous set out of the folder? `aside_holds` answers this, for
    #    THIS marker's tree rather than for residue from an older swap.
    missing = claimed - staged - landed

    if not missing and claimed:
        return SwapState(SWAP_ROLL_FORWARD, (
            "every file the marker says this swap publishes is either staged or "
            "already published"))

    if not landed:
        # Nothing of the new set is in the matter folder, so nothing is lost by
        # undoing the swap — and the staged set can never be completed, so
        # finishing is not on offer. An empty `claimed` lands here too, and must:
        # a marker that publishes NOTHING may not set anything aside, because a
        # set-aside with no publish behind it is a delete before a publish.
        if aside_holds:
            return SwapState(SWAP_ROLL_BACK, (
                f"the staged set is {len(missing) or len(claimed) or 'all'} "
                "file(s) short of what the marker recorded and none of it has "
                "been published, so it can never be published — while part or "
                "all of the previous set has already been moved out of this "
                "folder and is the only copy of what it holds"))
        return SwapState(SWAP_ABANDONED, (
            "nothing publishable was staged and nothing had been set aside, so "
            "this marker can authorize nothing"))

    return SwapState(SWAP_REFUSE, (
        f"{len(missing)} file(s) the marker says this swap publishes are in "
        f"neither the staging directory nor this folder "
        f"({', '.join(sorted(missing)[:3])}"
        f"{', …' if len(missing) > 3 else ''}), and "
        f"{len(landed)} file(s) of the same set are already published. "
        "Finishing would publish an incomplete set over a previous set that is "
        "already out of the folder, and record it as complete; undoing would "
        "destroy the part that is published"))


def _state_dir(destination: OutputLayout) -> Path:
    return destination.root / STATE_DIRNAME


def _staging_root(destination: OutputLayout) -> Path:
    return _state_dir(destination) / STAGING_DIRNAME


def _marker_path(destination: OutputLayout) -> Path:
    return _state_dir(destination) / MARKER_NAME


def _aside_root(destination: OutputLayout, name: str) -> Path:
    return _state_dir(destination) / name


def _aside_names(destination: OutputLayout) -> tuple[str, ...]:
    """Every set-aside directory currently under ``.dociq/``, sorted."""
    state = _state_dir(destination)
    if not state.is_dir():
        return ()
    return tuple(sorted(
        p.name for p in state.iterdir()
        if p.is_dir()
        and (p.name == ASIDE_PREFIX or p.name.startswith(ASIDE_PREFIX + "."))
    ))


def _free_aside_name(destination: OutputLayout) -> str:
    """A set-aside name nothing occupies.

    A previous swap that could not delete its own set-aside tree — an antivirus
    handle on one file in it — leaves that tree on disk. Under D-31 that is a
    tolerated, disclosed residue rather than a failure, so the NEXT swap must not
    collide with it: it takes ``superseded.1`` and the two sit side by side, each
    readable for what it is.

    The ceiling is not a silent cap. A thousand undeleted set-aside trees is not
    a busy matter folder, it is a machine where nothing can be deleted at all,
    and continuing to pile up renames would hide that.
    """
    state = _state_dir(destination)
    for n in range(1000):
        name = ASIDE_PREFIX if n == 0 else f"{ASIDE_PREFIX}.{n}"
        if not (state / name).exists():
            return name
    raise DocIQError(
        f"{state} already holds 1,000 undeleted set-aside directories. Each one "
        f"is a previous run's superseded deliverables that could not be removed, "
        f"so nothing on this machine is deleting files. Clear "
        f"{state}/{ASIDE_PREFIX}* by hand before running again."
    )


def superseded_residue(destination: OutputLayout) -> tuple[str, ...]:
    """Set-aside trees still on disk, relative to the matter root.

    Reported rather than tolerated silently. Under D-31 a swap that publishes
    successfully and then cannot delete what it moved aside is a SUCCESS with a
    residue: the matter folder holds one complete set and ``.dociq/`` holds a
    clearly-named stale one. Nobody reads ``.dociq/``, so the run says so.
    """
    return tuple(
        f"{STATE_DIRNAME}/{name}" for name in _aside_names(destination)
    )


def staging_layout(destination: OutputLayout) -> OutputLayout:
    """A clean staging layout under ``destination``.

    Any staging left by an earlier attempt is DISCARDED rather than reused: a
    half-written set from a run that died is not a head start, it is a mixture
    waiting to be published. (An earlier attempt that got as far as being marked
    ready is a different case entirely and is rolled forward by
    :func:`recover_pending`, which every run calls before this one.)
    """
    root = _staging_root(destination)
    if root.exists():
        shutil.rmtree(root)
    return OutputLayout(root).ensure()


def discard_staging(destination: OutputLayout) -> None:
    """Throw away an unfinished staging directory. Never touches the matter
    folder's deliverables — that is the whole point of writing elsewhere.

    This one keeps ``ignore_errors``, and the reason is the same test B-4
    applies to the swap: what does an absorbed failure let a reader believe that
    is false? Nothing here. No readiness marker exists on this path, so a
    surviving staging directory cannot be published by anything — and
    :func:`staging_layout` removes it WITHOUT ``ignore_errors`` before the next
    run reuses the name, so a genuinely stuck directory surfaces as a raised
    error at the start of that run rather than as a silent mixture in the
    matter folder."""
    root = _staging_root(destination)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


_MARKER_NOTE = {
    PHASE_PENDING: (
        "A DocIQ run finished writing its deliverables into "
        f"{STATE_DIRNAME}/{STAGING_DIRNAME}/ and is swapping them into this "
        "folder. NOTHING HAS BEEN DELETED. The swap renames this folder's "
        f"current set into {STATE_DIRNAME}/<aside>/, renames the staged set into "
        "place, and only then deletes what it moved aside. If this file is still "
        "here the swap did not finish: the next run completes it before doing "
        "anything else. Do not delete it by hand — deleting it abandons a "
        "complete set of deliverables."
    ),
    PHASE_ASIDE: (
        "A DocIQ swap has renamed this folder's previous set into "
        f"{STATE_DIRNAME}/<aside>/ and is renaming the staged set into place. "
        "The previous set is INTACT under that name — it was moved, not "
        "modified. The next run finishes the swap."
    ),
    PHASE_PUBLISHING: (
        "A DocIQ swap has renamed this folder's previous set into "
        f"{STATE_DIRNAME}/<aside>/ AND cleared every name the new set will "
        "take. No file of the new set has been overwritten and none can be: "
        "every name it is about to occupy is free. The next run finishes the "
        "swap. The previous set is INTACT under that name — it was moved, not "
        "modified."
    ),
    PHASE_PUBLISHED: (
        "A DocIQ swap has PUBLISHED the staged set — the files in this folder "
        "are the current run's, complete. All that remains is deleting what was "
        f"moved aside under {STATE_DIRNAME}/, which is DocIQ's own state and no "
        "part of the evidence. Nothing further will touch this folder's files."
    ),
}


def _write_marker(marker: Path, plan: SwapPlan) -> Path:
    """The marker's ONE writer. Atomic, so it is never observed half-written."""
    return replace_text_deterministic(
        marker,
        json.dumps(
            {
                "staging": STAGING_DIRNAME,
                "superseded": sorted(plan.superseded),
                "aside": plan.aside,
                "phase": plan.phase,
                "published": sorted(plan.published),
                "note": _MARKER_NOTE[plan.phase],
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
    )


def mark_ready(destination: OutputLayout, superseded: tuple[str, ...] = ()) -> Path:
    """Record that staging is complete and the swap may proceed.

    ``superseded`` is the list of the previous run's deliverables this swap
    replaces, relative to the matter root. It is carried in the marker rather
    than recomputed at swap time so that a roll-forward moves aside the same
    files the interrupted attempt was going to, whatever the folder looks like
    when the roll-forward happens.

    Under D-31 the marker also fixes **where** they go — a set-aside name nothing
    occupies — and the phase the swap has reached. The name is chosen here rather
    than at swap time so that a swap and its roll-forward always agree on it: a
    roll-forward that picked a fresh name would leave the first attempt's
    set-aside tree stranded under a name no marker mentions.

    The marker also carries the inventory of the set being published (B-8),
    computed HERE from the staged files rather than taken as an argument: the
    caller's business is what the swap replaces, and what the swap publishes is a
    fact about the staging directory that is complete at this instant and gone by
    the end of the swap. See :data:`PUBLISHED_NAME`.
    """
    marker = _marker_path(destination)
    marker.parent.mkdir(parents=True, exist_ok=True)
    for rel in superseded:
        _validate_superseded_entry(rel)
    staging = _staging_root(destination)
    published = tuple(sorted(
        p.relative_to(staging).as_posix() for p in _staged_files(staging)))
    return _write_marker(marker, SwapPlan(
        superseded=tuple(sorted(superseded)),
        aside=_free_aside_name(destination),
        phase=PHASE_PENDING,
        published=published,
    ))


class PublishedSetUnreadable(DocIQError):
    """The durable published-set inventory exists and cannot be parsed.

    The same fail-closed reasoning as :class:`PendingSwapUnreadable`, one file
    over (B-8). The inventory decides which of this folder's names the next swap
    moves aside, so a permissive read — absorb the error, fall back to the
    pattern list — is exactly the defect B-8 is: an incomplete plan that looks
    complete, whose failure mode is a matter folder holding two runs' evidence
    with nothing on disk saying so.

    An ABSENT inventory is a different state and is not this error. It means the
    folder was last published by a build that predates the inventory, and the run
    falls back to the pattern list and DISCLOSES that it did. A present-but-
    corrupt one is a hand edit or corruption, and neither is a state to guess
    through.
    """


class PendingSwapUnrecoverable(DocIQError):
    """An interrupted swap is in a state whose next step cannot be deduced.

    Found by ENUMERATING the swap's persistent states backwards rather than by
    following the happy path forwards (Codex review #2, third fix round: *"re-
    enumerate the redesigned state machines from every persistent state"*). Two
    of them had a next step that destroyed evidence, and both are refused here:

    * **phase ``aside``, staging empty, only SOME of the published names at the
      matter root.** The code cannot produce it — a publish moves one file at a
      time, so an interrupted one leaves the rest IN staging — so it is a
      restored backup, a half-copied folder or a hand edit. Guessing costs
      either the previous set (if it is treated as published and the set-aside
      tree is deleted) or the new one.
    * **phase ``published``, staging still holding files.** The publish loop
      raises rather than recording ``published`` over an unpublished file, so
      this state also cannot be reached from the code. Treating the marker as
      true deletes ``.dociq/staging`` — a complete set of deliverables — as
      drained scratch.

    Both leave the folder exactly as found, with the marker on disk, which is
    what makes the situation repairable rather than merely reported.
    """


_UNRECOVERABLE = (
    "REFUSING to complete an interrupted swap: the readiness marker at "
    "{marker} says phase {phase!r}, and {why}.\n"
    "Nothing has been moved or deleted \u2014 this folder is exactly as it was "
    "found.\n"
    "DocIQ cannot reach this state on its own, so it was produced by a restore, "
    "a copy, or a hand edit, and the next step is not deducible from the names "
    "on disk: one reading of them destroys the previous set and the other "
    "destroys the new one.\n"
    "{repair}"
)


_REPAIR = (
    "To go on, decide which set this folder should hold, then remove the "
    "marker.\n"
    "  * The PREVIOUS run's set, or the part of it that had already moved, is "
    "intact under {aside} — it was renamed, never modified.\n"
    "  * The interrupted run's set, or what is left of it, is under {staging}.\n"
    "  * Move whichever you want into the matter folder by hand and delete "
    "{marker}. If {staging} is complete and you want it published, set the "
    "marker's `phase` back to {phase_aside!r} instead and re-run."
)


_INVENTORY_UNREADABLE = (
    "REFUSING to plan a replacement: the published-set inventory at {path} "
    "exists but {why}.\n"
    "Nothing has been read, moved or deleted. That file records which files the "
    "last DocIQ run published into this folder, and without it this run cannot "
    "know which of them to move aside — so it would publish its own set beside "
    "whichever of the previous one it failed to name, with nothing recording "
    "the mixture.\n"
    "To go on: delete {path} to fall back to this build's own output names "
    "(any deliverable an older build wrote under a name this build does not "
    "use will then be left in place and must be cleared by hand), or restore a "
    "readable copy."
)


def _inventory_path(destination: OutputLayout) -> Path:
    return _state_dir(destination) / PUBLISHED_NAME


def _write_inventory(destination: OutputLayout, published: tuple[str, ...]) -> Path:
    path = _inventory_path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    return replace_text_deterministic(
        path,
        json.dumps(
            {
                "published": sorted(published),
                "note": (
                    "The files the last completed DocIQ run published into this "
                    "matter folder. The NEXT run reads this to move the whole "
                    "previous set aside before publishing any of its own — "
                    "including files under names a newer build no longer writes. "
                    "Deleting it does not damage the deliverables; it makes the "
                    "next run fall back to that build's own output names, which "
                    "can leave a retired file behind. DocIQ's own state: it is "
                    "no part of the evidence and no manifest describes it."
                ),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
    )


def published_inventory(destination: OutputLayout) -> tuple[str, ...]:
    """The COMPLETE set the last run published, matter-root-relative and sorted.

    Empty when the folder has never been published by a build that writes the
    inventory — which the caller must treat as "unknown", not as "nothing", and
    disclose (see :data:`PUBLISHED_NAME` and :class:`PublishedSetUnreadable`).

    **Two sources, and the second is not redundancy.** The inventory file is
    written as part of the transition to phase ``published``; everything at and
    below that transition is TOLERATED rather than fatal, because the matter
    folder is already complete and correct and nothing there may turn a published
    run into a failed one. A transient lock on this one file would therefore
    leave the previous run's inventory in place — under-naming the next plan,
    which is precisely B-8. So the marker, which carries the same list and is
    NOT deleted while the inventory is behind it, is read as the authority
    whenever it is ahead. In the ordinary case :func:`recover_pending` has
    already reconciled the two before any caller gets here.
    """
    from_marker: tuple[str, ...] = ()
    marker = _marker_path(destination)
    if marker.is_file():
        try:
            plan = _read_marker(marker)
        except PendingSwapUnreadable:
            # Not this function's failure to report: every entry point calls
            # `recover_pending` first, which raises on exactly this. Leaving it
            # to that call keeps one message for one state.
            from_marker = ()
        else:
            if plan.phase == PHASE_PUBLISHED:
                # ONLY at `published`, and the narrowness is the point. This
                # branch exists for exactly one state: the swap finished, flipped
                # the marker to `published`, and could not write the inventory
                # file, so the marker is deliberately retained as the record. At
                # every earlier phase the staged set has NOT taken those names —
                # the matter root still holds the previous set, in whole or in
                # part — and the file below describes it correctly. Reading the
                # marker at `aside` (which this did for one round) would report
                # the set a rollback is about to undo as the set in the folder.
                from_marker = plan.published

    path = _inventory_path(destination)
    from_file: tuple[str, ...] = ()
    if path.is_file():
        def refuse(why: str, cause: BaseException | None = None):
            exc = PublishedSetUnreadable(
                _INVENTORY_UNREADABLE.format(path=path, why=why))
            raise exc from cause

        try:
            raw = _retry_io(lambda: path.read_text(encoding="utf-8"))
        except OSError as exc:
            refuse(f"it could not be read ({exc})", exc)
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            refuse(f"it is not valid JSON ({exc})", exc)
        if not isinstance(payload, dict):
            refuse(f"its top level is {type(payload).__name__}, not an object")
        if "published" not in payload:
            refuse("it names no `published` list")
        listed = payload["published"]
        if not isinstance(listed, list):
            refuse(f"`published` is {type(listed).__name__}, not a list")
        try:
            from_file = tuple(_validate_superseded_entry(e) for e in listed)
        except ValueError as exc:
            refuse(str(exc), exc)

    return tuple(sorted(set(from_marker or from_file)))


def covering_plan(names: Iterable[str]) -> tuple[str, ...]:
    """Normalise a set-aside plan: drop any entry an ANCESTOR already covers.

    ``upload_package`` and ``upload_package/LI-06881.txt`` in one plan is not
    wrong — the directory moves first and the file is then found already gone —
    but it makes the swap report the same evidence leaving twice, under two
    names, in the durable ``stale_outputs_replaced`` record. The plan is a union
    of a pattern expansion (which names directories) and a file inventory (which
    names their contents), so the overlap is the ordinary case rather than the
    odd one.

    **Pure set arithmetic over the plan. It does not read the disk, and that is
    the point** (fourth fix round, F-3). This function briefly also *collapsed* a
    directory whose every on-disk entry the plan covered into the directory's own
    name, so that a retired directory did not leave an empty shell at the matter
    root. The containment guard for that collapse read the disk **at plan time**,
    at the top of Stage 5 — and the rename it authorised happened after the whole
    of Stage 5 and Stage 6, which on a real matter is minutes. An analyst who
    saved a note into ``clean_text/`` inside that window had it renamed aside
    under the directory's name and then deleted with the set-aside tree, having
    never been in the plan. It also silently coarsened ``stale_outputs_replaced``
    from the files to ``["clean_text"]``.

    **Both are gone rather than fixed**, because re-verifying containment
    immediately before the rename narrows the window without closing it, and what
    the collapse bought was cosmetic: an empty ``retired_dir/`` at the matter
    root holds no evidence, mixes no generations, and is what the pre-B-8 build
    already left behind for ``clean_text/``. A cosmetic residue is a better trade
    than any window in which an analyst's file can be deleted. Stated here rather
    than dropped silently, because "the plan reads the disk" is a property a
    later refactor could reintroduce without noticing what it costs.

    Sorted and deterministic, because it feeds a durable record.
    """
    kept = set(names)
    out: list[str] = []
    for rel in sorted(kept):
        parts = PurePosixPath(rel.replace("\\", "/")).parts
        if any("/".join(parts[:i]) in kept for i in range(1, len(parts))):
            continue
        out.append(rel)
    return tuple(out)


def pending_swap(destination: OutputLayout) -> bool:
    """Whether a completed staging set is waiting to be moved into place."""
    return _marker_path(destination).is_file()


def _staged_files(staging: Path) -> list[Path]:
    """The staged FILES, in a deterministic order.

    ``.dociq/`` inside staging is skipped. A staging layout is an
    :class:`OutputLayout` like any other, so anything that runs against it — the
    package builder does — creates its own state directory inside it. Publishing
    that state into the matter root would put DocIQ's scratch where the manifest
    expects deliverables. It has never happened, because the one such consumer
    cleans up after itself; this makes the property hold by construction rather
    than by that consumer continuing to.
    """
    if not staging.is_dir():
        return []
    out = []
    for src in sorted(staging.rglob("*")):
        if not src.is_file():
            continue
        if src.relative_to(staging).parts[0] == STATE_DIRNAME:
            continue
        out.append(src)
    return out


def commit_staging(
    destination: OutputLayout, notes: list[str] | None = None
) -> tuple[str, ...]:
    """Replace the matter folder's deliverables with the staged ones.

    Returns the paths the matter folder no longer holds — the ones renamed
    aside — relative to the matter root, in sorted order.

    **DELETE-LAST (D-31).** Nothing under the matter root is ever deleted or
    overwritten here. The sequence is:

    1. **Set aside.** Every planned name is RENAMED into ``.dociq/<aside>/``.
       Nothing is destroyed, and a failure leaves the rest of the previous set
       exactly where it was.
    2. **Publish, in two complete passes.** (a) Every destination a staged file
       will take that is still occupied — a name the plan did not cover — is
       RENAMED into ``.dociq/<aside>/`` too, rather than overwritten. (b) Only
       then is any staged file renamed into place. The two passes are the B-8
       fix: folding (a) into (b) meant an unplanned occupant was handled *after*
       earlier staged files had landed.
    3. **Then delete** — and only under ``.dociq/``. By this point the matter
       folder holds one complete set and a failure here cannot change that.

    **Why the phases are written down.** The marker records which phase the swap
    reached, and recovery *also* reads the names on disk. The two together are
    what make B-6 unreachable: a marker whose ``unlink()`` returned while its
    name survived used to re-authorize deleting the newly published set, and now
    (a) the plan can only select paths to RENAME INTO ``.dociq/``, never to
    delete, and (b) :func:`classify_swap` reads the phase together with what
    staging holds *compared against what the marker recorded* and with whether
    this marker's own set-aside tree holds anything.

    *This paragraph used to offer a third guard — "an empty staging directory
    says there is nothing to publish, so nothing is set aside" — and add that
    "any one of the three is enough". Both halves were false* (fourth fix round,
    F-1). An empty staging directory says nothing whatever about what was set
    aside: the marker is only rewritten after the WHOLE of step 1, so a crash
    inside it leaves ``pending`` beside a half-filled aside tree, and reading the
    staging directory alone is what deleted that half. The guards are not
    interchangeable and none of them is sufficient alone, which is why there is
    now one function that reads all of them at once instead of three tests spread
    across the branches that happened to need them.

    **What is left behind by a failure, in every case.**

    * a failure in step 1 — the matter folder holds the previous set minus the
      names already moved, all of which are intact in ``.dociq/<aside>/``. The
      marker still says ``pending``, because it is rewritten only after the whole
      loop; the SPLIT is readable from the aside tree, and
      :func:`classify_swap` reads it there rather than inferring it from the
      phase (F-1). If the staged set is then lost, the next run puts the split
      set back together rather than deleting the half that moved;
    * a failure in step 2 — the matter folder holds *part of the new set and
      nothing of the old*, which is incomplete but is not a MIXTURE; the whole
      previous set is intact in ``.dociq/<aside>/`` and the whole remaining new
      set is intact in staging;
    * a failure in step 3 — the matter folder holds the complete new set and
      ``.dociq/`` holds a clearly-named stale tree. This is not an error and does
      not raise; it is reported by :func:`superseded_residue`.

    Every one of those is readable from the names on disk without the marker.

    **What this guarantees, precisely.** Every deliverable is written and hashed
    in staging first, so a crash at any point during emit — which is where the
    time goes, and where the Sprint-1 note found the gap — leaves the previous
    run's complete deliverables untouched. Once staging is marked ready the swap
    is *idempotent and roll-forward*: whatever fraction of it completed, calling
    it again finishes it, and :func:`recover_pending` makes every run call it
    again before doing anything else.

    **Every step that touches the matter folder fails closed.** Each rename is
    retried and then proven by the names, and a step that cannot be completed
    raises with the readiness marker STILL ON DISK, at the phase the swap
    actually reached. That is what makes the roll-forward claim true rather than
    asserted.

    *This paragraph used to say something stronger about deletions and it is
    withdrawn along with them* (D-31). Two earlier rounds hardened the deletion
    of superseded deliverables — B-4 made a failed ``rmtree`` loud, and
    ``_remove_file_or_fail`` proved a file's name was gone rather than trusting
    ``unlink`` to have returned. Both were correct and both are now **deleted
    rather than kept**, because nothing under the matter root is deleted at all.
    ``_remove_file_or_fail`` no longer exists; the only proof this function needs
    of a superseded deliverable is that its name moved.

    **What it does not guarantee, stated rather than implied.** The swap is a
    sequence of moves, not one atomic operation — Windows offers no atomic
    replacement of a directory whose target is non-empty, and the deliverables
    live at the matter root by design (§8 Path B: Expert Assist reads
    ``clean_text/`` and ``sources.json`` from exactly there). So a *reader* that
    opens the folder during the moves, or between a crash and the next run, can
    still see a set that is INCOMPLETE. What it can no longer see is a set
    that is MIXED, **on the conditions stated next** — and they are stated
    because this paragraph asserted the guarantee flatly for one round while it
    was conditional, which is how B-8 survived a review looking for exactly this.

    The guarantee is: *no staged file enters the matter root until every name it
    will take is free, and every name the last run published has left.* The first
    half is unconditional — step 2a is a complete pass over the staged set's
    destinations and it runs before step 2b touches anything. **The second half
    is conditional on** ``.dociq/published_set.json`` **existing**, because that
    file is the only record of what the last run published under names this build
    may no longer write. Where it is absent the plan falls back to this build's
    output patterns, and a deliverable an older build wrote under a retired name
    is left in the folder beside the new set. That is a MIXTURE, and it is
    disclosed rather than prevented (``run.published_set_inventory``).

    **When is it absent?** On the first run of this build against any matter
    folder a previous build published — which at rollout is every existing
    folder. From that run's own successful swap onward the file exists and the
    guarantee is unconditional. The window is one run per folder, it is announced
    in that run's processing log, and it cannot be closed by code: a sweep of the
    matter root would set aside and then delete files DocIQ never wrote.

    *The flat form of this sentence used to say "step 1 takes the whole previous
    set out", and that was false in a second way as well* (Codex review #2, third
    fix round, B-8): step 1 takes out what the PLAN names, and an occupant it
    missed was moved aside lazily, when the publish loop reached it, after
    earlier staged files had already landed. Step 2a closed that half.

    What also changed is the size of the window: from the whole of Stage 5
    — minutes, and every OCR page of it — to a sequence of same-volume metadata
    operations, with a marker on disk saying which phase the swap is in and a
    roll-forward that completes it. Closing the incompleteness window entirely
    needs a published-set indirection that §8's fixed paths currently forbid.
    """
    def note(what: str) -> None:
        """Say WHICH of this function's outcomes happened.

        A sink rather than a return value because the return value has one
        meaning already — the names that left the matter folder — and it is ``()``
        for three different outcomes, including the rollback. The caller's
        durable disclosure used to assert that an interrupted swap "was
        completed", which the rollback below makes false: the interrupted run's
        set is the one that did NOT survive. A recovery that describes itself
        wrongly in the processing log is the same class of defect as one that
        does the wrong thing, one layer out.
        """
        if notes is not None:
            notes.append(what)

    marker = _marker_path(destination)
    if not marker.is_file():
        return ()
    # Read and validated BEFORE anything moves. An unreadable marker raises
    # :class:`PendingSwapUnreadable` from here, with the folder untouched — see
    # that class for why the permissive read this replaces was the more
    # dangerous of the two options (Codex review #2, B-2).
    plan = _read_marker(marker)

    # Every step below is wrapped in :func:`_retry_io` for the reason given
    # there: on Windows the swap is a burst of metadata operations over files an
    # on-access scanner has just seen change, and a transient lock in the middle
    # of it is the ordinary case rather than the exotic one. Each step is
    # idempotent, so a retry repeats work rather than doing something new — and
    # the roll-forward that follows a genuine failure is idempotent for the same
    # reason.
    root = destination.root
    staging = _staging_root(destination)
    aside = _aside_root(destination, plan.aside)
    moved: list[str] = []

    # ---- THE NAMES ON DISK, then ONE classification ----------------------
    #
    # Read before anything is acted on, and handed to :func:`classify_swap`,
    # which is pure and is the state table. Three rounds' worth of defects lived
    # in states the dispatch could not express because it tested each axis where
    # it happened to need it; there is now one place that decides, and its axes
    # are the table's columns.
    staged = _staged_files(staging)
    staged_rel = frozenset(
        src.relative_to(staging).as_posix() for src in staged)

    # Whether the PLAN'S OWN set-aside tree holds anything. Not "is there a
    # superseded tree" — residue from an earlier, completed swap is a set that
    # has already been replaced and is always expendable, while this tree can
    # hold the only copy of half the current previous set (F-1).
    aside_holds = aside.is_dir() and any(q.is_file() for q in aside.rglob("*"))

    # Which of the claimed names are at the matter root, and ONLY where that
    # question has an answer. At `pending` and `aside` a claimed name at the root
    # is the PREVIOUS generation's file under the same name — `sources.json` is
    # there before any swap begins — so identity comes from the phase, never from
    # the name (F-4). At `publishing` and beyond, step 2a has provably vacated
    # every one of those names, so anything back at them is this run's.
    if plan.phase in (PHASE_PUBLISHING, PHASE_PUBLISHED):
        landed = frozenset(
            rel for rel in plan.published if (root / rel).exists())
    else:
        landed = frozenset()

    state = classify_swap(plan, staged_rel, aside_holds, landed)

    if state.action == SWAP_REFUSE:
        raise PendingSwapUnrecoverable(_UNRECOVERABLE.format(
            marker=marker, phase=plan.phase, why=state.why,
            repair=_REPAIR.format(
                aside=aside, staging=staging, marker=marker,
                phase_aside=PHASE_ASIDE),
        ))

    if state.action == SWAP_ROLL_BACK:
        # The staged set can never be completed and none of it has been
        # published, while part or all of the previous set is out of the folder.
        # Put it back: renames only, into names step 1 vacated.
        #
        # Driven by what is IN the tree, not by `plan.superseded`, and the
        # difference is not cosmetic: step 2a moves unplanned occupants into the
        # same tree and records them only in this call's return value, so a
        # rollback keyed on the plan would restore some of the tree and then hand
        # the rest to `_discard_aside_trees`.
        restore = sorted(q for q in aside.rglob("*") if q.is_file())

        # CHECK EVERY DESTINATION FIRST, then move. The same lesson as step 2a,
        # pointed the other way, and it is not hypothetical: a marker written by
        # the build this replaced records a mid-publish state as `aside`, so a
        # rollback driven by it meets destinations that already hold the NEW
        # generation. Raising on the first occupied one *while renaming* would
        # leave the previous set half restored BESIDE the part already
        # published — the mixture this whole subsystem exists to forbid,
        # created by the code meant to prevent it.
        blocked = [q.relative_to(aside).as_posix() for q in restore
                   if (root / q.relative_to(aside)).exists()]
        if blocked:
            raise PendingSwapUnrecoverable(_UNRECOVERABLE.format(
                marker=marker, phase=plan.phase,
                why=(f"the previous set cannot be put back: {len(blocked)} of "
                     "its names are occupied at the matter root "
                     f"({', '.join(blocked[:3])}"
                     f"{', …' if len(blocked) > 3 else ''}), so something has "
                     "been published into this folder that the marker's phase "
                     "does not account for"),
                repair=_REPAIR.format(
                    aside=aside, staging=staging, marker=marker,
                    phase_aside=PHASE_ASIDE),
            ))
        for src in restore:
            rel = src.relative_to(aside).as_posix()
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _rename_or_fail(src, dst)
        _discard_aside_trees(destination)
        try:
            _remove_tree_or_fail(staging)
        except OSError:
            pass
        try:
            _retry_io(lambda: marker.unlink(missing_ok=True))
        except OSError:
            pass
        note("ROLLED BACK: the interrupted run's staged set was incomplete and "
             "none of it had been published, so the previous run's set — which "
             "was waiting intact under .dociq/ — was moved back into place. "
             "This folder holds the PREVIOUS run's deliverables, not the "
             "interrupted run's. Reason: " + state.why)
        # Nothing left the matter folder: what was moved aside is back in it,
        # and the inventory that described it was never rewritten.
        return ()

    if state.action == SWAP_ABANDONED:
        # Nothing staged and nothing set aside, so this marker can authorize
        # nothing. Only DocIQ's own state is touched.
        _discard_aside_trees(destination)
        try:
            _remove_tree_or_fail(staging)
        except OSError:
            pass
        try:
            _retry_io(lambda: marker.unlink(missing_ok=True))
        except OSError:
            pass
        note("NOTHING TO DO: the readiness marker outlived its own swap. "
             "Nothing was staged and nothing had been set aside, so nothing "
             "could be published and nothing was moved; only DocIQ's own state "
             "under .dociq/ was cleaned up")
        return ()

    # SWAP_ROLL_FORWARD or SWAP_FINISH from here down.
    phase = plan.phase

    if phase == PHASE_PENDING:
        # ---- 1. SET ASIDE. Renames only. Nothing is deleted. ---------------
        for rel in plan.superseded:
            src = root / rel
            if not src.exists():
                # Already moved by an interrupted attempt, or never there. Both
                # are "this name is not in the matter folder", which is the only
                # thing this step is trying to achieve.
                if (aside / rel).exists():
                    moved.append(rel)
                continue
            dst = aside / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _rename_or_fail(src, dst)
            moved.append(rel)
        _write_marker(
            marker,
            SwapPlan(plan.superseded, plan.aside, PHASE_ASIDE, plan.published),
        )
        phase = PHASE_ASIDE
    elif phase in (PHASE_ASIDE, PHASE_PUBLISHING):
        # An earlier attempt completed step 1; those names are already out.
        moved.extend(rel for rel in plan.superseded if (aside / rel).exists())

    if phase == PHASE_ASIDE:
        # ---- 2a. CLEAR EVERY DESTINATION, BEFORE ANY OF 2b. ----------------
        #
        # Runs ONCE, on the `aside` -> `publishing` transition, and never again.
        # Re-running it at `publishing` would find the destinations occupied by
        # files this swap had already PUBLISHED and move them back out — an
        # un-publish. That the phase now separates the two is what makes the
        # single-run property readable rather than assumed.
        #
        # Codex review #2, third fix round, **B-8**. This used to be folded into
        # the publish loop below: a destination the plan had missed was
        # recognized when the loop REACHED it, and moved aside at that point —
        # by which time the staged files sorting before it had already landed.
        # A real open handle on the missed occupant then stopped the swap with
        # the matter root holding `a_new.txt = NEW` beside `z_legacy.txt = OLD`.
        # That is the mixed set delete-last exists to forbid, produced by the
        # one step in the design that ran out of order.
        #
        # So the sweep is a separate, complete pass. Nothing of the new set
        # enters the folder until every name it will take is free, which is the
        # D-31 ordering claim applied to the names the plan did not know about
        # as well as the ones it did. The plan itself is now built from a durable
        # inventory (`published_inventory`), so in the ordinary case this pass
        # finds nothing and the two mechanisms are independent: one makes the
        # plan complete, the other makes an incomplete plan harmless.
        #
        # Idempotent, and it runs on re-entry at phase `aside` as well as on the
        # first attempt — which is the case step 1 cannot cover, because step 1
        # is skipped once the marker says the set-aside is done. It does NOT run
        # at `publishing`: by then these destinations hold files this swap has
        # already published, and sweeping them would be an un-publish.
        for src in staged:
            rel = src.relative_to(staging).as_posix()
            dst = root / rel
            if not dst.exists():
                continue
            # A name the plan did not cover — a deliverable an older build wrote
            # that neither the inventory nor `_stale_deliverables` enumerates.
            # It is moved aside like everything else rather than overwritten:
            # `os.replace` here would be a delete before a publish with a
            # different name on it, and it would be the one destructive act in
            # this function that nothing recorded.
            occupant = aside / rel
            occupant.parent.mkdir(parents=True, exist_ok=True)
            _rename_or_fail(dst, occupant)
            moved.append(rel)

        _write_marker(
            marker,
            SwapPlan(
                plan.superseded, plan.aside, PHASE_PUBLISHING, plan.published),
        )
        phase = PHASE_PUBLISHING

    if phase == PHASE_PUBLISHING:
        # ---- 2b. PUBLISH. Renames only, onto names proven free above. ------
        for src in staged:
            dst = root / src.relative_to(staging)
            dst.parent.mkdir(parents=True, exist_ok=True)
            _rename_or_fail(src, dst)
        leftover = sorted(
            p.relative_to(staging).as_posix() for p in _staged_files(staging)
        )
        if leftover:  # pragma: no cover - `_rename_or_fail` raises before this
            raise OSError(
                errno.ENOTEMPTY,
                "the staged set was not fully published: " + ", ".join(leftover),
                str(staging),
            )
        _write_marker(
            marker,
            SwapPlan(plan.superseded, plan.aside, PHASE_PUBLISHED, plan.published),
        )
    # A re-entry at `published` deliberately reports NOTHING moved: this call did
    # not move anything, and a caller that renders "N superseded file(s)
    # replaced" from the return value would otherwise attribute the previous
    # run's work to this one. Whether a swap was pending at all is a separate
    # question the caller already asks. The abandoned and rolled-back paths
    # return `()` above for the same reason and say which they were in `notes`.

    # ---- 3. ONLY NOW, delete — and only under `.dociq/`. -------------------
    #
    # The matter folder is complete and correct at this line, whatever happens
    # below. So none of it raises: a lock on one file in a set-aside tree, or on
    # a drained staging directory, must not turn a published run into a failed
    # one, and it cannot make the evidence set wrong. What survives is named by
    # :func:`superseded_residue` and disclosed by the run rather than absorbed.
    # The DURABLE INVENTORY of what now holds the matter root (B-8). Written
    # here, below the publish, for the same reason everything else here is: the
    # folder is already correct and a lock on one `.dociq/` file must not fail a
    # published run. But an inventory that silently stayed at the PREVIOUS run's
    # set would under-name the next swap's plan, which is B-8 itself — so the
    # readiness marker, which carries the same list, is kept until this lands.
    # A surviving `published` marker is provably harmless (see below) and
    # :func:`published_inventory` reads it in preference to a file behind it.
    #
    # Reached only by ROLL_FORWARD and FINISH, both of which end with
    # `plan.published` at the matter root. The abandoned and rolled-back paths
    # return above without touching it, because there the folder holds the OLDER
    # set and `plan.published` names files that were never published: writing it
    # would replace a correct inventory with a wrong one.
    try:
        _write_inventory(destination, plan.published)
    except OSError:
        inventory_written = False
    else:
        inventory_written = True

    if state.action == SWAP_FINISH:
        note("CLEANED UP: the interrupted run's staged set was already fully "
             "published; only DocIQ's own state under .dociq/ remained")
    else:
        note("ROLLED FORWARD: the interrupted run's staged set was published "
             "into this folder and the set it replaced was moved aside")

    _discard_aside_trees(destination)
    try:
        _remove_tree_or_fail(staging)
    except OSError:
        pass

    # Last, and TOLERATED like every other step below the publish.
    #
    # A marker whose name lingers is now provably harmless, which is the
    # difference from B-6: it says `published`, and the next roll-forward reads
    # that, finds an empty staging directory, sets nothing aside and deletes
    # nothing outside `.dociq/`. Under the old design the same lingering name
    # authorized deleting the set that had just been published.
    #
    # It is tolerated whether `unlink` RETURNS over a surviving name or RAISES,
    # and the second half of that was a real gap found by enumerating this
    # module's destructive operations rather than by a test: `_retry_io` re-raised
    # after eight attempts, and nothing above `commit_staging` handles it — so a
    # transient antivirus lock on `staging_ready.json`, the same condition every
    # other step here absorbs, turned a run whose deliverables were fully
    # published into a raised exception. The published set is correct at this
    # line; nothing below it may say otherwise.
    #
    # Held back when the inventory above could not be written: the marker is
    # then the only durable record of what this run published, and the next run
    # builds its set-aside plan from it.
    if inventory_written:
        try:
            _retry_io(lambda: marker.unlink(missing_ok=True))
        except OSError:
            pass
    return tuple(sorted(set(moved)))


def _discard_aside_trees(destination: OutputLayout) -> tuple[str, ...]:
    """Delete every ``.dociq/superseded*`` tree that will go. Never raises.

    Returns the names that survived. All of them, not just this swap's: a tree
    left by an earlier run is the same thing — a previous set that has already
    been replaced — and the lock that stopped it being deleted then may be gone
    now. Nothing outside ``.dociq/`` is reachable from here, because
    :func:`_validate_aside_name` is the only thing that ever names one.
    """
    survivors: list[str] = []
    for name in _aside_names(destination):
        try:
            _remove_tree_or_fail(_aside_root(destination, name))
        except OSError:
            survivors.append(name)
    return tuple(survivors)


def recover_pending(
    destination: OutputLayout, notes: list[str] | None = None
) -> tuple[str, ...]:
    """Finish an interrupted swap, if there is one.

    Safe to call always, in the sense that it does nothing when no swap is
    pending. It is NOT total: a marker that exists and cannot be parsed raises
    :class:`PendingSwapUnreadable` and leaves the folder untouched, which is the
    B-2 fix. :func:`dociq.pipeline.run` calls this as its first statement and
    turns that into a blocked run with the ordinary ``incomplete_run/`` record,
    so an operator sees the reason rather than a traceback.

    **What recovery can and cannot do (D-31), restated precisely.** It can rename
    a name at the matter root into ``.dociq/``, rename a staged file into the
    matter root, and delete inside ``.dociq/``. **Nothing at the matter root is
    ever deleted**, and that is a property of the code rather than of the marker
    being correct: the only destructive call reachable from here is
    :func:`_remove_tree_or_fail` on a path :func:`_validate_aside_name` produced.

    *This used to say recovery "cannot delete a published file", and that was
    false* (fourth fix round, F-1). Recovery deletes the set-aside tree, and that
    tree can hold deliverables that were published by the last completed run and
    moved out by an interrupted swap. Whether deleting it is safe is not a
    property of the code's shape at all — it is a judgement about the state, and
    it is :func:`classify_swap`'s, made from the marker's phase, from what the
    staging directory actually holds compared against what the marker recorded,
    and from whether THIS marker's own set-aside tree holds anything. F-1 was
    exactly that judgement being absent: a crash inside step 1 leaves the marker
    at ``pending`` with the previous set SPLIT between the root and the tree, and
    the old code read the empty staging directory, called the marker abandoned,
    and deleted the half that had moved.

    So the honest statement is the narrow one: recovery never deletes at the
    matter root, and it deletes under ``.dociq/`` only after classifying the
    state as one in which that tree has been superseded — never on the strength
    of the marker's phase alone.
    """
    if not pending_swap(destination):
        return ()
    return commit_staging(destination, notes)
