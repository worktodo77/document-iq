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
"""

from __future__ import annotations

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
    "MARKER_NAME",
    "staging_layout",
    "discard_staging",
    "mark_ready",
    "commit_staging",
    "recover_pending",
    "pending_swap",
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
        os.replace(tmp, path)
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

MARKER_NAME = "staging_ready.json"
"""Written when staging is COMPLETE and the swap may begin; removed when the
swap has finished. Its presence is the only thing that authorizes moving files
into the matter folder, and it is what makes an interrupted swap recoverable —
see :func:`commit_staging`.

Written by :func:`replace_text_deterministic`, so it is never observed
half-written: the marker either does not exist or holds the whole document. A
marker that nonetheless cannot be parsed is corruption or a hand edit, and
:func:`commit_staging` refuses to act on it rather than guessing — see
:class:`PendingSwapUnreadable`."""


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


def _read_marker(marker: Path) -> tuple[str, ...]:
    """The supersede list a readiness marker declares, or fail closed.

    Every deviation is fatal, including ones a permissive reader would shrug at
    (a missing ``superseded`` key, a non-list, a non-string entry): the marker is
    written by exactly one function, atomically, so anything this cannot parse is
    corruption or a hand edit, and neither is a state to guess through.
    """
    try:
        raw = marker.read_text(encoding="utf-8")
    except OSError as exc:
        raise PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker, why=f"it could not be read ({exc})",
            staging=marker.parent / STAGING_DIRNAME)) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker, why=f"it is not valid JSON ({exc})",
            staging=marker.parent / STAGING_DIRNAME)) from exc
    if not isinstance(payload, dict):
        raise PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker,
            why=f"its top level is {type(payload).__name__}, not an object",
            staging=marker.parent / STAGING_DIRNAME))
    if "superseded" not in payload:
        raise PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker, why="it names no `superseded` list",
            staging=marker.parent / STAGING_DIRNAME))
    entries = payload["superseded"]
    if not isinstance(entries, list):
        raise PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker,
            why=f"`superseded` is {type(entries).__name__}, not a list",
            staging=marker.parent / STAGING_DIRNAME))
    try:
        return tuple(_validate_superseded_entry(e) for e in entries)
    except ValueError as exc:
        raise PendingSwapUnreadable(_UNREADABLE.format(
            marker=marker, why=str(exc),
            staging=marker.parent / STAGING_DIRNAME)) from exc


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
    folder's deliverables — that is the whole point of writing elsewhere."""
    root = _staging_root(destination)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def mark_ready(destination: OutputLayout, superseded: tuple[str, ...] = ()) -> Path:
    """Record that staging is complete and the swap may proceed.

    ``superseded`` is the list of the previous run's deliverables this swap
    replaces, relative to the matter root. It is carried in the marker rather
    than recomputed at swap time so that a roll-forward removes the same files
    the interrupted attempt was going to remove, whatever the folder looks like
    when the roll-forward happens.
    """
    marker = _marker_path(destination)
    marker.parent.mkdir(parents=True, exist_ok=True)
    for rel in superseded:
        _validate_superseded_entry(rel)
    replace_text_deterministic(
        marker,
        json.dumps(
            {
                "staging": STAGING_DIRNAME,
                "superseded": sorted(superseded),
                "note": (
                    "A DocIQ run finished writing its deliverables into "
                    f"{STATE_DIRNAME}/{STAGING_DIRNAME}/ and was moving them "
                    "into this folder. If this file is still here, the move did "
                    "not finish: the next run completes it before doing anything "
                    "else. Do not delete it by hand — deleting it abandons a "
                    "complete set of deliverables."
                ),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
    )
    return marker


def pending_swap(destination: OutputLayout) -> bool:
    """Whether a completed staging set is waiting to be moved into place."""
    return _marker_path(destination).is_file()


def commit_staging(destination: OutputLayout) -> tuple[str, ...]:
    """Replace the matter folder's deliverables with the staged ones.

    Returns the paths removed, relative to the matter root, in sorted order.

    **What this guarantees, precisely.** Every deliverable is written and hashed
    in staging first, so a crash at any point during emit — which is where the
    time goes, and where the Sprint-1 note found the gap — leaves the previous
    run's complete deliverables untouched. Once staging is marked ready the swap
    is *idempotent and roll-forward*: whatever fraction of it completed, calling
    it again finishes it, and :func:`recover_pending` makes every run call it
    again before doing anything else.

    **What it does not guarantee, stated rather than implied.** The swap is a
    sequence of moves, not one atomic operation — Windows offers no atomic
    replacement of a directory whose target is non-empty, and the deliverables
    live at the matter root by design (§8 Path B: Expert Assist reads
    ``clean_text/`` and ``sources.json`` from exactly there). So a *reader* that
    opens the folder during the moves, or between a crash and the next run, can
    still see a mixture. What changed is the size of that window: from the whole
    of Stage 5 — minutes, and every OCR page of it — to a sequence of same-volume
    metadata operations, with a marker on disk saying the folder is mid-swap and
    a roll-forward that completes it. Closing the window entirely needs a
    published-set indirection that §8's fixed paths currently forbid.
    """
    marker = _marker_path(destination)
    if not marker.is_file():
        return ()
    # Read and validated BEFORE anything moves or is deleted. An unreadable
    # marker raises :class:`PendingSwapUnreadable` from here, with the folder
    # untouched — see that class for why the permissive read this replaces was
    # the more dangerous of the two options (Codex review #2, B-2).
    superseded = _read_marker(marker)

    root = destination.root
    removed: list[str] = []
    for rel in superseded:
        path = root / rel
        if path.is_file():
            path.unlink()
            removed.append(rel)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(rel)

    staging = _staging_root(destination)
    if staging.is_dir():
        for src in sorted(staging.rglob("*")):
            if not src.is_file():
                continue
            dst = root / src.relative_to(staging)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
        shutil.rmtree(staging, ignore_errors=True)

    marker.unlink(missing_ok=True)
    return tuple(sorted(removed))


def recover_pending(destination: OutputLayout) -> tuple[str, ...]:
    """Finish an interrupted swap, if there is one.

    Safe to call always, in the sense that it does nothing when no swap is
    pending. It is NOT total: a marker that exists and cannot be parsed raises
    :class:`PendingSwapUnreadable` and leaves the folder untouched, which is the
    B-2 fix. :func:`dociq.pipeline.run` calls this as its first statement and
    turns that into a blocked run with the ordinary ``incomplete_run/`` record,
    so an operator sees the reason rather than a traceback.
    """
    if not pending_swap(destination):
        return ()
    return commit_staging(destination)
