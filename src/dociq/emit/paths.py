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
* :func:`recover_pending` **never deletes a published file**. Its destructive
  scope is ``.dociq/`` and nothing else, so a stale marker cannot authorize
  destroying a set that is already in place (B-6 is unreachable rather than
  defended against);
* recovery reads **the names on disk** as its primary evidence — what is left in
  staging, and what is already set aside. The marker records the plan; it is not
  the sole authority for destroying anything.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
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
    "PHASE_PENDING",
    "PHASE_ASIDE",
    "PHASE_PUBLISHED",
    "SwapPlan",
    "staging_layout",
    "discard_staging",
    "mark_ready",
    "commit_staging",
    "recover_pending",
    "pending_swap",
    "superseded_residue",
    "PendingSwapUnreadable",
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

PHASE_PENDING = "pending"
"""Nothing has moved yet, or the set-aside renames are unfinished."""

PHASE_ASIDE = "aside"
"""Every planned name is out of the matter folder and in ``.dociq/<aside>/``.
Only the publish renames remain."""

PHASE_PUBLISHED = "published"
"""The staged set holds the published names. Everything that remains is deletion
UNDER ``.dociq/``, and no failure of it can touch a deliverable — which is why
this phase exists as a written record rather than being inferred."""

_PHASES = (PHASE_PENDING, PHASE_ASIDE, PHASE_PUBLISHED)


@dataclass(frozen=True, slots=True)
class SwapPlan:
    """What the readiness marker says, validated.

    ``superseded`` are matter-root-relative names the staged set replaces;
    ``aside`` is the single ``.dociq/`` component they are renamed into; ``phase``
    is how far the swap got. All three are checked at parse time rather than
    trusted, because they select paths that get *moved* — see
    :func:`_validate_superseded_entry` and :func:`_validate_aside_name`.
    """

    superseded: tuple[str, ...]
    aside: str
    phase: str


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
    return SwapPlan(superseded=superseded, aside=aside, phase=phase)


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
    """
    marker = _marker_path(destination)
    marker.parent.mkdir(parents=True, exist_ok=True)
    for rel in superseded:
        _validate_superseded_entry(rel)
    return _write_marker(marker, SwapPlan(
        superseded=tuple(sorted(superseded)),
        aside=_free_aside_name(destination),
        phase=PHASE_PENDING,
    ))


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


def commit_staging(destination: OutputLayout) -> tuple[str, ...]:
    """Replace the matter folder's deliverables with the staged ones.

    Returns the paths the matter folder no longer holds — the ones renamed
    aside — relative to the matter root, in sorted order.

    **DELETE-LAST (D-31).** Nothing under the matter root is ever deleted or
    overwritten here. The sequence is:

    1. **Set aside.** Every planned name is RENAMED into ``.dociq/<aside>/``.
       Nothing is destroyed, and a failure leaves the rest of the previous set
       exactly where it was.
    2. **Publish.** Every staged file is RENAMED into place. Its destination is
       free, because step 1 emptied it; a destination the plan did not cover is
       moved aside too rather than overwritten.
    3. **Then delete** — and only under ``.dociq/``. By this point the matter
       folder holds one complete set and a failure here cannot change that.

    **Why the phases are written down.** The marker records which phase the swap
    reached, and recovery *also* reads the names on disk. The two together are
    what make B-6 unreachable: a marker whose ``unlink()`` returned while its
    name survived used to re-authorize deleting the newly published set, and now
    (a) the plan can only select paths to RENAME INTO ``.dociq/``, never to
    delete, (b) a phase of ``published`` says the publish is done, and (c) an
    empty staging directory says there is nothing to publish, so nothing is set
    aside. Any one of the three is enough; the disk-readable one is primary.

    **What is left behind by a failure, in every case.**

    * a failure in step 1 — the matter folder holds the previous set minus the
      names already moved, all of which are intact in ``.dociq/<aside>/``;
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
    still see a set that is INCOMPLETE. What it can no longer see is a set that
    is MIXED: step 1 takes the whole previous set out before step 2 puts any of
    the new one in, so at every instant the matter root holds files from one run
    only. What also changed is the size of the window: from the whole of Stage 5
    — minutes, and every OCR page of it — to a sequence of same-volume metadata
    operations, with a marker on disk saying which phase the swap is in and a
    roll-forward that completes it. Closing the incompleteness window entirely
    needs a published-set indirection that §8's fixed paths currently forbid.
    """
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

    # THE NAMES ON DISK, read before the marker's phase is acted on and given
    # priority over it (D-31). A staging directory with no files in it means
    # there is nothing to publish, and therefore nothing may be set aside — which
    # is the state B-6's stale marker leaves behind after a successful swap. The
    # old design read a surviving marker and deleted the newly published set;
    # this reads the same marker, sees an empty staging directory, and moves
    # nothing.
    staged = _staged_files(staging)
    phase = plan.phase
    abandoned = not staged and phase == PHASE_PENDING
    if abandoned:
        # A marker that outlived its swap. There is nothing staged, so there is
        # nothing this marker can authorize: skip straight to the cleanup, which
        # cannot reach outside `.dociq/`.
        phase = PHASE_PUBLISHED

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
        _write_marker(marker, SwapPlan(plan.superseded, plan.aside, PHASE_ASIDE))
        phase = PHASE_ASIDE
    elif phase == PHASE_ASIDE:
        # An earlier attempt completed step 1; those names are already out.
        moved.extend(rel for rel in plan.superseded if (aside / rel).exists())

    if phase == PHASE_ASIDE:
        # ---- 2. PUBLISH. Renames only. ------------------------------------
        for src in staged:
            dst = root / src.relative_to(staging)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                # A name the plan did not cover — a deliverable an older run
                # wrote that `_stale_deliverables` no longer enumerates, say. It
                # is moved aside like everything else rather than overwritten:
                # `os.replace` here would be a delete before a publish with a
                # different name on it, and it would be the one destructive act
                # in this function that nothing recorded.
                rel = src.relative_to(staging).as_posix()
                occupant = aside / rel
                occupant.parent.mkdir(parents=True, exist_ok=True)
                _rename_or_fail(dst, occupant)
                moved.append(rel)
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
            marker, SwapPlan(plan.superseded, plan.aside, PHASE_PUBLISHED))
    # A re-entry at `published`, and the abandoned-marker case, deliberately
    # report NOTHING moved: this call did not move anything, and a caller that
    # renders "N superseded file(s) replaced" from the return value would
    # otherwise attribute the previous run's work to this one. Whether a swap
    # was pending at all is a separate question the caller already asks.

    # ---- 3. ONLY NOW, delete — and only under `.dociq/`. -------------------
    #
    # The matter folder is complete and correct at this line, whatever happens
    # below. So none of it raises: a lock on one file in a set-aside tree, or on
    # a drained staging directory, must not turn a published run into a failed
    # one, and it cannot make the evidence set wrong. What survives is named by
    # :func:`superseded_residue` and disclosed by the run rather than absorbed.
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


def recover_pending(destination: OutputLayout) -> tuple[str, ...]:
    """Finish an interrupted swap, if there is one.

    Safe to call always, in the sense that it does nothing when no swap is
    pending. It is NOT total: a marker that exists and cannot be parsed raises
    :class:`PendingSwapUnreadable` and leaves the folder untouched, which is the
    B-2 fix. :func:`dociq.pipeline.run` calls this as its first statement and
    turns that into a blocked run with the ordinary ``incomplete_run/`` record,
    so an operator sees the reason rather than a traceback.

    **What recovery can and cannot do (D-31).** It can rename this folder's names
    into ``.dociq/``, rename staged files into this folder, and delete inside
    ``.dociq/``. It **cannot delete a published file**, and that is a property of
    the code rather than of the marker being correct: the only destructive call
    reachable from here is :func:`_remove_tree_or_fail` on a path
    :func:`_validate_aside_name` produced. That is what makes B-6 unreachable —
    the finding was a stale marker authorizing recovery to delete the newly
    published set, and there is no longer a code path that deletes a published
    set at all.
    """
    if not pending_swap(destination):
        return ()
    return commit_staging(destination)
