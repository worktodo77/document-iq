"""Output layout, the single deterministic write path, and publication.

Two files exist to be boring: the directory names every emitter agrees on, and
one text-writing function. The second matters more than it looks — a single
``open(..., "w")`` without ``newline=""`` on Windows turns every ``\\n`` into
``\\r\\n`` and silently breaks the byte-identical claim for that one file. There
is one write path so there is one place that can go wrong.

The third thing here is **staging and publication**, and after D-32 it is short
enough to hold in your head, which is the entire point of it being short.

**THE PUBLICATION RULE, in one sentence:** *publication deletes the previous
run's deliverables from the matter folder and then moves each staged file onto
its final name, in that order, once — with no marker, no set-aside copy, no
inventory, and no recovery.*

**THE WINDOW THAT RULE LEAVES OPEN, stated at full width rather than softened.**
Between the first removal and the last move the matter folder holds part of the
previous run's evidence and part of this run's. A process that dies inside that
window leaves it that way **permanently**: nothing on disk records that a
publication was in progress, no later run detects the mixture or repairs it, and
the manifest describing the folder is itself one of the files that may or may not
have landed. The window is the time to remove one file per superseded name and
rename one file per staged file — on the measured corpus, thousands of clean-text
files, so **seconds, not milliseconds**. The one thing that survives is the new
set: publication removes the staging tree only after every file has left it, so
an interrupted publication leaves the complete new set under
``.dociq/staging/``, and the next run reports finding it before discarding it
(:func:`state_residue`).

**Staging still exists, and it is not vestigial.** Deliverables are built in
``.dociq/staging/`` and §4 Stage 6 audits *that* set; a red gate refuses and
discards it, so the previous run's deliverables are never replaced by a set that
failed its own audit. That is Codex review #2's finding B-1, it was accepted, and
removing staging would reintroduce it. What D-32 removed is the multi-phase
publication *protocol*, not the staging directory.

**WHAT WAS REMOVED, so that nobody rebuilds it by accident (D-32, 2026-08-06).**
``classify_swap`` and its state table, the ``pending → aside → publishing →
published`` marker protocol in ``.dociq/staging_ready.json``, the durable
``.dociq/published_set.json`` inventory, the set-aside (``superseded*``) trees,
and the roll-forward / roll-back re-entry paths are **gone**. They are not
deferred and they are not disabled behind a flag. Six consecutive review
generations each found a new defect inside the previous generation's fix, and the
diagnosis was that the design could not represent its own remaining failure
modes: every row its state table enumerated was sound, and the defects lived in
rows its axes could not express. Alex ruled the trade explicitly — *one hole that
fits in a sentence, over many that hide.* Two guarantees died with the machinery
and are named here because a reader of this module would otherwise assume they
still hold:

* an interrupted publication is **no longer** rolled forward, or detected at all;
* the plan of what to remove is built from **this build's own output patterns**
  only (:data:`dociq.pipeline._STALE_PATTERNS`), so a deliverable an older DocIQ
  wrote under a name this build no longer writes is **left in the matter folder**
  — Codex review #2's finding B-8, reopened knowingly. The durable inventory that
  closed it was part of the removed protocol.

Before changing anything below, read ``docs/decisions/decision_register.md``
("D-32 EXECUTED") and ``docs/verification/d32_descope_2026-08-06.md``.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..contracts import DocIQError

__all__ = [
    "OutputLayout",
    "write_text_deterministic",
    "safe_component",
    "STATE_DIRNAME",
    "STAGING_DIRNAME",
    "staging_layout",
    "discard_staging",
    "state_residue",
    "publish_staging",
    "PublishResult",
    "PublicationFailed",
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


# ---------------------------------------------------------------------------
# Staging and publication
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
stylistic: publication has to be a **move within one filesystem**. A staging
directory on another volume turns every move into a copy, which on a matter
folder that is a network share would copy the whole corpus twice, and would make
each file's arrival non-atomic as well as the set's.
"""

_LEGACY_ASIDE_PREFIX = "superseded"
"""A tree name the REMOVED set-aside protocol used, recognized only to report it.

D-32 deleted the machinery that created these. Nothing in this build writes a
``.dociq/superseded*`` tree. A matter folder last touched by the branch build
that did can still hold one, and it holds a previous run's deliverables, so
:func:`state_residue` still names it rather than leaving a full copy of an old
evidence set sitting unmentioned under ``.dociq/``. When the name stops appearing
in real matter folders this constant can go; it is not a hook for anything to be
rebuilt on.
"""


def _retry_io(what, *, attempts: int = 8, delay: float = 0.02):
    """Run ``what``, retrying an ``OSError`` with a short doubling backoff.

    **Found by repetition, not by reasoning.** A fix round's own suite went red
    on its thirtieth repeat with a ``PermissionError`` on a file DocIQ had
    written one statement earlier. That is not corruption. It is a Windows file
    lock — antivirus and backup agents (Carbonite is documented in this
    project's environment notes) hold a transient deny-write on a file the
    instant it is created, and publication touches every deliverable in the
    matter.

    **Transient I/O and corrupt state are different things.** Only ``OSError``
    is retried, and only where the operation is idempotent.

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


def _remove_or_fail(path: Path) -> None:
    """Remove a file or a directory tree and PROVE the name is gone, or raise.

    Codex review #2 fix round, finding B-4, and the reasoning outlives the
    design it was found in. ``shutil.rmtree(path, ignore_errors=True)`` is not
    "best effort", it is **unobservable failure**: the caller then believes the
    name is free and writes on top of whatever survived.

    Two things are needed rather than one. ``rmtree``/``unlink`` without
    suppression raises on the first failure, which the retry handles; but a
    removal can also report success on Windows while the directory entry lingers
    (an open handle marks the file for delete-on-close and the name remains until
    it is released). ``exists()`` after the fact is therefore checked as well:
    what the caller needs to know is not whether the call returned, it is whether
    the name is gone.

    Idempotent under retry — an already-removed path is simply gone.
    """

    def once() -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        if path.exists():
            raise OSError(
                errno.ENOTEMPTY,
                "the path is still present after it was removed",
                str(path),
            )

    _retry_io(once)


def _state_dir(destination: OutputLayout) -> Path:
    return destination.root / STATE_DIRNAME


def _staging_root(destination: OutputLayout) -> Path:
    return _state_dir(destination) / STAGING_DIRNAME


def staging_layout(destination: OutputLayout) -> OutputLayout:
    """A clean staging layout under ``destination``.

    Any staging left by an earlier attempt is DISCARDED rather than reused: a
    half-written set from a run that died is not a head start, it is a mixture
    waiting to be published.

    Without ``ignore_errors`` on purpose. If a staging tree genuinely cannot be
    removed, this run must stop here — at the start, with the matter folder
    untouched — rather than build a set on top of another run's leavings.
    """
    root = _staging_root(destination)
    if root.exists():
        shutil.rmtree(root)
    return OutputLayout(root).ensure()


def discard_staging(destination: OutputLayout) -> None:
    """Throw away an unfinished staging directory. Never touches the matter
    folder's deliverables — that is the whole point of writing elsewhere.

    This one keeps ``ignore_errors``, and the reason is the test B-4 applies:
    what does an absorbed failure let a reader believe that is false? Nothing
    here. Publication is a direct call — no marker, no state on disk — so a
    surviving staging directory cannot be published by anything, and
    :func:`staging_layout` removes it WITHOUT suppression before the next run
    reuses the name, so a genuinely stuck directory surfaces as a raised error at
    the start of that run rather than as a silent mixture in the matter folder.
    A surviving tree is also reported by :func:`state_residue`.
    """
    root = _staging_root(destination)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def state_residue(destination: OutputLayout) -> tuple[str, ...]:
    """Trees under ``.dociq/`` that DocIQ left behind, matter-root-relative.

    Reported rather than tolerated silently — **nobody opens** ``.dociq/``. This
    is what remains of amendment A-16's disclosure after D-32, and its meaning
    changed with the design, so read what it now names:

    * ``.dociq/staging`` — **a run that did not finish.** A run that publishes
      drains this tree and removes it; a run that is refused or aborted discards
      it. Finding one at the start of the next run means an earlier run died, or
      was cancelled, between building its set and publishing it. That is the
      D-32 window's on-disk signature, and it is the reason this is measured at
      the top of a run rather than only at the end of one.
    * ``.dociq/superseded*`` — a set-aside tree from the removed protocol
      (:data:`_LEGACY_ASIDE_PREFIX`). Nothing in this build creates one.

    **Call it before :func:`staging_layout` has run**, or the staging tree it
    names will be this run's own live one. :mod:`dociq.pipeline` calls it as the
    first thing it does with the folder, and that placement is load-bearing
    rather than incidental.
    """
    state = _state_dir(destination)
    if not state.is_dir():
        return ()
    return tuple(sorted(
        f"{STATE_DIRNAME}/{p.name}"
        for p in state.iterdir()
        if p.is_dir()
        and (
            p.name == STAGING_DIRNAME
            or p.name == _LEGACY_ASIDE_PREFIX
            or p.name.startswith(_LEGACY_ASIDE_PREFIX + ".")
        )
    ))


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


@dataclass(frozen=True, slots=True)
class PublishResult:
    """What :func:`publish_staging` did, for the record and for the screen."""

    removed: tuple[str, ...]
    """Previous-run deliverables this publication took out of the matter folder,
    matter-root-relative and sorted."""

    published: tuple[str, ...]
    """Names the staged set now occupies, matter-root-relative and sorted."""

    residue: tuple[str, ...]
    """``.dociq/`` trees left behind, from :func:`state_residue`'s vocabulary.

    Non-empty here means the publication SUCCEEDED and the drained staging tree
    could not be removed afterwards — the matter folder holds one complete,
    correct set and ``.dociq/staging/`` holds empty directories. A residue, not a
    failure."""


_FAILED_REMOVING = (
    "PUBLICATION FAILED while clearing this folder's previous deliverables, at "
    "{path}: {why}\n\n"
    "THIS FOLDER IS NOW MIXED. {done} of {planned} of the previous run's "
    "deliverables have been removed and none of the new set has been written, "
    "so the folder holds part of one run's evidence and nothing of the other's. "
    "DocIQ will not repair this and no later run will detect it.\n\n"
    "The new run's complete set of deliverables is intact in {staging} and was "
    "NOT discarded. Free whatever is holding the file above — an antivirus scan, "
    "a backup agent, or the file being open — and re-run the matter, or move the "
    "staged files into this folder by hand."
)

_FAILED_MOVING = (
    "PUBLICATION FAILED while moving the new deliverables into place, at "
    "{path}: {why}\n\n"
    "THIS FOLDER IS NOW MIXED. {done} of {planned} of the new set are in place "
    "and the previous run's deliverables have been removed, so the folder holds "
    "part of one run's evidence and part of another's. DocIQ will not repair "
    "this and no later run will detect it.\n\n"
    "The files that have not been moved are still in {staging} and were NOT "
    "discarded. Free whatever is holding the file above — an antivirus scan, a "
    "backup agent, or the file being open — and re-run the matter, or move the "
    "remaining staged files into this folder by hand."
)


class PublicationFailed(DocIQError):
    """A removal or a move failed partway through :func:`publish_staging`.

    **This exception means the matter folder is mixed**, and its message says so
    in those words rather than reporting an I/O error and leaving the operator to
    work out what state the folder is in. There is no recovery path: D-32 removed
    the protocol that had one, having accepted this window in exchange for a
    design whose failure modes fit in a paragraph.

    Raised rather than absorbed because the alternative — publishing on and
    reporting success — is the mixed-evidence set B-2 established as the worst
    outcome available: a folder nobody can tell is wrong.
    """


def publish_staging(
    destination: OutputLayout, superseded: Iterable[str] = ()
) -> PublishResult:
    """Remove ``superseded`` from the matter folder, then move the staged set in.

    **THE RULE, in one sentence:** *publication deletes the previous run's
    deliverables from the matter folder and then moves each staged file onto its
    final name, in that order, once — with no marker, no set-aside copy, no
    inventory, and no recovery.*

    **THE WINDOW IT LEAVES OPEN, stated at full width.** Between the first
    removal and the last move the matter folder holds part of the previous run's
    evidence and part of this run's. A process that dies inside that window —
    power loss, a kill, an unhandled crash — **leaves it that way permanently**.
    Nothing on disk records that a publication was in progress; no later run
    detects the mixture, repairs it, or warns about it; the manifest that
    describes the folder is one of the files that may or may not have landed. A
    later run publishing successfully will end the mixture, but only because it
    replaces everything, and only if it succeeds. The one thing that survives is
    the staged set: publication removes the staging tree only after every file
    has been moved out of it, so a run interrupted mid-publication leaves the
    complete new set under ``.dociq/staging/``, and the next run reports having
    found it (:func:`state_residue`) before discarding it.

    The window is not instantaneous and should not be described as if it were.
    It is the time to remove one previous deliverable per superseded name and
    perform one rename per staged file; on the measured corpus that is thousands
    of clean-text files, so it is **seconds**, not milliseconds.

    This is D-32, and it is a deliberate trade: **one hole that fits in a
    sentence, in place of a multi-phase recoverable protocol whose axes could not
    express its own failure modes.** Six consecutive review generations found a
    new defect inside the previous generation's fix. The reasoning is in
    ``docs/decisions/decision_register.md`` ("D-32 EXECUTED") and the
    consequences are enumerated in
    ``docs/verification/d32_descope_2026-08-06.md``. **Do not rebuild a recovery
    protocol here without a new ruling.**

    What is still guaranteed, and each of these is a property of the code below
    rather than a hope:

    * **The gate ran over this exact set.** Deliverables are built in staging and
      §4 Stage 6 audits staging; a red gate never reaches this function, so the
      previous set is never replaced by one that failed its own audit (Codex
      review #2, B-1). This is the whole reason staging still exists.
    * **Each individual file appears whole or not at all.** The move is
      :func:`os.replace`, which is atomic on NTFS and on POSIX alike. The SET is
      not atomic — that is the window above — but no reader ever sees a truncated
      deliverable.
    * **Publication refuses an empty staging directory** rather than emptying the
      matter folder into nothing.
    * **A removal that cannot be proven complete raises** (:func:`_remove_or_fail`)
      before any staged file is moved past it, so a locked previous deliverable
      stops the publication rather than being silently written over.

    Raises :class:`PublicationFailed` if a removal or a move still fails after
    :func:`_retry_io`'s budget (eight attempts, 2.54 s of waiting on one
    operation). Staging is deliberately NOT discarded on that path: the
    complete new set stays on disk, named in the message, for a human to finish
    by hand. Nothing automatic will finish it.
    """
    root = destination.root
    staging = _staging_root(destination)
    staged = _staged_files(staging)
    if not staged:
        raise PublicationFailed(
            f"refusing to publish: {staging} holds no files, so publishing "
            f"would remove this folder's deliverables and put nothing in their "
            f"place. Nothing was removed."
        )

    plan = tuple(sorted(set(superseded)))
    removed: list[str] = []
    for rel in plan:
        target = root / rel
        if not target.exists():
            continue
        try:
            _remove_or_fail(target)
        except OSError as exc:
            raise PublicationFailed(_FAILED_REMOVING.format(
                path=target, staging=staging, done=len(removed),
                planned=len(plan), why=exc,
            )) from exc
        removed.append(rel)

    published: list[str] = []
    for src in staged:
        rel = src.relative_to(staging).as_posix()
        dst = root / rel
        try:
            # Inside the guard, not before it: a destination directory that
            # cannot be created is the same class of failure as a move that
            # cannot be made, and it happens at the same point in the sequence.
            # Left outside, it escaped as a bare OSError with no statement of
            # what state the folder was in.
            dst.parent.mkdir(parents=True, exist_ok=True)
            _retry_io(lambda s=src, d=dst: os.replace(s, d))
        except OSError as exc:
            raise PublicationFailed(_FAILED_MOVING.format(
                path=dst, staging=staging, done=len(published),
                planned=len(staged), why=exc,
            )) from exc
        published.append(rel)

    # The drained tree. `ignore_errors` here and nowhere else in this function:
    # everything of value has already left it, so a failure to remove it cannot
    # make the matter folder wrong — it leaves empty directories under `.dociq/`,
    # which `state_residue` names and the run discloses (A-16).
    shutil.rmtree(staging, ignore_errors=True)

    return PublishResult(
        removed=tuple(removed),
        published=tuple(sorted(published)),
        residue=state_residue(destination),
    )
