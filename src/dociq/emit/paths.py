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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "OutputLayout",
    "write_text_deterministic",
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
see :func:`commit_staging`."""


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
    write_text_deterministic(
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
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        superseded = tuple(str(p) for p in payload.get("superseded", ()))
    except (OSError, ValueError):
        # A marker we cannot read still means "staging is complete" — that is
        # what its existence records. Rolling the swap forward with an empty
        # supersede list moves the new deliverables into place and leaves any
        # stale ones, which is recoverable; refusing to move would leave the
        # complete set stranded in a hidden directory, which is not.
        superseded = ()

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
    """Finish an interrupted swap, if there is one. Safe to call always."""
    if not pending_swap(destination):
        return ()
    return commit_staging(destination)
