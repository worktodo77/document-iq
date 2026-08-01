"""Stage 1 — folder walk, hashing, tiering, and the extraction fan-out.

Adapted from MIP 3.9's ``api/ingest_folder.py``, which was built against real
multi-thousand-file productions and carries scars worth keeping: a per-file
watchdog that measures EXECUTION time rather than queue time, a disk preflight
that fails before hours of work rather than during them, resume after a crash,
and a live error log so a six-hour run is diagnosable while it runs.

Three things differ from the original, all forced by the frozen contract:

* **Deterministic result order.** MIP 3.9 consumed pool results as they
  completed. Every document here is sorted by
  :func:`~dociq.contracts.document_sort_key` before it reaches the result, so
  completion order — which is a function of thread scheduling — cannot reach
  disk.
* **Archive members are child documents**, with ``parent_doc_id`` and
  ``container_order``, rather than concatenated into the parent's text.
* **Tier 2 is inventoried, never attempted.** §3 requires the listed-only set
  to be hashed and reported without blocking, so those files are never handed
  to an extractor at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as fwait
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import (
    CONTRACT_VERSION,
    Disposition,
    DocumentRecord,
    EffectiveLimits,
    PageKind,
    PageRecord,
    ProcessingStatus,
    RunConfig,
    RunResult,
    canonical_json,
    document_sort_key,
    to_jsonable,
)
from ..runstate import COMPLETED, RunTermination, TerminalStatus
from . import extract as ex
from .dating import detect_dates

_HASH_CHUNK = 1 << 20

_DEFAULT_WORKERS = int(os.environ.get(
    "DOCIQ_WORKERS", str(min(16, max(1, (os.cpu_count() or 2) - 2)))))
_DEFAULT_FILE_TIMEOUT = float(os.environ.get("DOCIQ_FILE_TIMEOUT", "3600"))
_DISK_HEADROOM = float(os.environ.get("DOCIQ_DISK_HEADROOM", "1.15"))

_RETRY_MAX = int(os.environ.get("DOCIQ_RETRY_MAX", "500"))
_RETRY_BUDGET_S = float(os.environ.get("DOCIQ_RETRY_BUDGET_S", "1800"))
"""Bounds on the serial-retry pass. Both are disclosed in the run notes
whenever they bite — a cap that quietly stops retrying would reintroduce
exactly the silence the retry exists to remove."""

_MAX_DATES_PER_DOC = 200
"""Cap on ``detected_dates``. A 900-page register can carry tens of thousands
of dates and the index column shows a handful; the cap is disclosed as a
document note whenever it bites, per the no-silent-caps rule."""

STATE_DIR = ".dociq"
"""Run scratch + resume state, under the output root so §10 holds."""


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file found by the walk, before any extraction."""

    path: Path
    rel_path: str
    """POSIX separators, NFC-normalized — the contract's primary sort key."""
    sha256: str
    size_bytes: int
    ext: str
    tier: int
    unreadable: bool = False
    """The file could not be opened for hashing, twice. It is still inventoried
    (never dropped), but it is demoted to Tier 2 for a reason that has nothing
    to do with its format, and saying "Unrecognized format" about a .pdf that
    was merely locked is a false statement in the deliverable."""


@dataclass
class RunNotes:
    """Facts about THIS invocation — never about the inputs.

    Everything here is excluded from the log's hashed ``content`` by
    construction, because every field can differ between two runs over
    byte-identical inputs: whether a previous run crashed and left a journal,
    whether a file lost a race under extraction load, whether the operator
    cancelled. Putting any of it in ``content`` would make the byte-identical
    claim false for reasons that have nothing to do with the evidence.

    It is an explicit out-parameter rather than a field on
    :class:`~dociq.contracts.RunResult` for the same reason: ``RunResult``'s
    warnings ARE hashed, and a channel that is easy to mix up with them is a
    channel that will be mixed up with them.
    """

    load_dependent: list[str] = field(default_factory=list)
    """One entry per document that failed under load and was re-read serially.
    Prominent by design — a run that needed a retry must say so."""

    invocation: list[str] = field(default_factory=list)
    """Resume, cancellation, and read-retry notes."""

    termination: RunTermination = COMPLETED
    """How the walk ENDED (Codex B-1).

    It lives here, beside the other facts about this invocation, for the
    reason this class exists: two runs over byte-identical inputs can differ in
    it — the operator cancelled one of them — so it must never reach the hashed
    content. It is nonetheless the single most consequential thing the walk can
    report, because :mod:`dociq.pipeline` refuses to publish anything unless it
    says :attr:`~dociq.runstate.TerminalStatus.COMPLETED`.

    Defaulting to COMPLETED is safe only because every abort path in
    :func:`run` sets it before returning, and ``tests/test_incomplete_runs.py``
    enumerates those paths. A new early return that forgets to set it is the
    one way this can fail open, which is why the test enumerates rather than
    samples.
    """

    def messages(self) -> list[str]:
        """Everything, for the operator-facing warning list.

        The termination headline goes FIRST when the run did not complete. A
        run that was blocked or cancelled has exactly one thing the operator
        needs to read before anything else, and the summary screen and the run
        summary both truncate their warning lists.
        """
        head = [] if self.termination.complete else [self.termination.headline()]
        return head + list(self.load_dependent) + list(self.invocation)


def effective_limits(
    options: "WalkOptions | None" = None, *, ocr_enabled: bool | None = None
) -> EffectiveLimits:
    """Everything environment- or option-controlled that can change output bytes.

    Amendment A-04, raised by Codex review #1 finding B-2. ``RunConfig``'s own
    contract is that anything influencing output and absent from it is a
    determinism bug, and these settings were absent: five caps in
    :mod:`dociq.ingest.extract`, the per-file timeout, the two retry bounds,
    whether the walk recursed, and which OCR model bytes read the scanned pages.
    When any of them bites, two runs over the same folder, profile and index
    produce different evidence while presenting the same hashed configuration.

    Assembled here rather than in the pipeline because this module and
    :mod:`dociq.ingest.extract` are where the values live; a copy anywhere else
    is a copy that can go stale.

    ``ocr_model_id`` is left empty when OCR did not run — an identity for models
    that read nothing would be noise in the hash, and the disabled case is
    already recorded by ``RunConfig.ocr_engine``.

    Two settings that look as though they belong here and do not:

    * ``DOCIQ_DISK_HEADROOM`` gates whether the run starts at all rather than
      what a completed run emits, and it is a float, which Principle 5 bars from
      identity fields. :class:`EffectiveLimits` has no field for it. Recorded in
      the log's ``run`` section instead — see ``docs/contracts/amendments.md``
      A-05 for the disclosure.
    * ``DOCIQ_OCR_WORKERS``, like ``workers``, is pool width. Pool width must not
      change output; if it ever does that is a determinism defect to fix, not a
      value to absorb into the identity.
    """
    opts = options or WalkOptions()
    caps = ex.effective_caps()
    ocr_on = opts.ocr_enabled if ocr_enabled is None else ocr_enabled
    return EffectiveLimits(
        xlsx_max_rows=caps["xlsx_max_rows"],
        csv_max_rows=caps["csv_max_rows"],
        zip_max_mb=caps["zip_max_mb"],
        zip_max_members=caps["zip_max_members"],
        zip_max_depth=caps["zip_max_depth"],
        # MILLISECONDS, and integer (amendment A-08, round-2 F-4b). These were
        # ``round(seconds)``, so 1.1 s and 1.4 s recorded the identical
        # identity while abandoning different files — a determinism collision
        # inside the field added to close one. Rounded rather than truncated
        # for the original reason: a sub-unit timeout truncating to 0 would
        # record "no timeout" for a run that timed out on everything.
        file_timeout_ms=round(opts.file_timeout_s * 1000),
        retry_max=_RETRY_MAX,
        retry_budget_ms=round(_RETRY_BUDGET_S * 1000),
        recurse=opts.recursive,
        ocr_model_id=ex.ocr_model_id() if ocr_on else "",
        workers=opts.workers,
    )


@dataclass
class WalkOptions:
    workers: int = _DEFAULT_WORKERS
    file_timeout_s: float = _DEFAULT_FILE_TIMEOUT
    recursive: bool = True
    resume: bool = True
    ocr_enabled: bool = True
    progress: Callable[[dict], None] | None = None
    """Called with a status dict after every completion batch. Track C's
    progress bar reads this; nothing in it reaches disk."""
    cancelled: Callable[[], bool] | None = None


@dataclass
class _Errors:
    """Every failure, visible and capped, in a deterministic order.

    Both properties are load-bearing and neither is obvious:

    * A systemic failure must be legible immediately, not present as a silent
      failed-count at the end — hence the cap has a disclosure row.
    * The cap is applied AFTER sorting, not as records arrive. Errors arrive in
      thread-completion order, so capping on arrival would keep a different
      2,000 errors on every run — and these strings land in the log's hashed
      ``content`` section. That is a determinism bug that only shows up on a
      corpus with more than 2,000 failures, i.e. never in a test and always in
      a real production.
    """

    items: list[dict] = field(default_factory=list)
    cap: int = 2000

    def record(self, rel: str, msg: str) -> None:
        self.items.append({"file": rel,
                           "error": ex.clip_message(msg or "unknown", 300)})

    def as_list(self) -> list[dict]:
        ordered = sorted(self.items, key=lambda d: (d["file"], d["error"]))
        if len(ordered) <= self.cap:
            return ordered
        return ordered[:self.cap] + [
            {"file": "…",
             "error": f"{len(ordered) - self.cap} further error(s) omitted; the "
                      f"first {self.cap} in path order are listed"}]


# ---------------------------------------------------------------------------
# Stage 1 — inventory
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_path_of(path: Path, root: Path) -> str:
    """Contract-shaped relative path: ``/`` separators, NFC.

    NFC matters on this exact key: macOS-authored filenames arrive NFD and a
    byte-comparison against an NFC master index would miss every accented name,
    which is the §5 match key and the primary sort key both.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:  # pragma: no cover — path always under root here
        rel = Path(path.name)
    return unicodedata.normalize("NFC", rel.as_posix())


def tier_of(ext: str) -> int:
    """1 = extracted, 2 = inventoried only. Unknown extensions are Tier 2:
    the §3 table ends in "unknown formats", so the default is listed-not-lost.
    """
    return 1 if ex.is_tier1(ext) else 2


def _iter_files(root: Path, *, recursive: bool,
                notes: list[str] | None = None,
                failures: list[str] | None = None) -> list[Path]:
    """Every file under ``root``, guarded against a symlink/junction cycle.

    ``Path.rglob`` alone will happily walk into a directory that loops back to
    an ancestor — a Windows directory junction pointed at itself, or a
    symlink cycle on any platform — re-discovering the same files at ever
    deeper synthetic paths until a filesystem path-length limit finally stops
    it (confirmed: ~260 chars on Windows, ~20 nesting levels here). Nothing
    else in the pipeline catches this: every re-discovery hashes as a distinct
    ``FileEntry`` with a distinct ``rel_path``, so the corpus inflates with
    phantom duplicate documents that get extracted (and OCR'd, if scanned)
    all over again — a determinism and cost hazard hiding inside what looks
    like a normal file count.

    ``Path.is_symlink()`` is not the guard: an NTFS directory junction is a
    different reparse tag than a symlink and does not report as one, so a
    symlink check silently misses exactly this case (junctions are the
    Windows-native way productions get mapped in). ``os.path.realpath()``
    resolves both kinds, so cycle detection tracks each directory's resolved
    real path instead.

    **One traversal for both modes** (Codex review #1 round 2, F-3). The
    non-recursive mode used to be a separate one-liner —
    ``[p for p in root.glob("*") if p.is_file()]`` — and that is a silent-loss
    path, because ``Path.is_file()`` swallows the ``OSError`` a failed ``stat``
    raises and answers ``False``. An entry that could not be examined therefore
    left the inventory before :func:`scan` could make it an unreadable Tier-2
    record, and the recursive branch's disclosure was never reached: same
    folder, ``recursive=False``, no warning, no index row, no accounting
    representation. The two modes now differ in exactly one statement — whether
    a directory is pushed onto the stack — so no future correction can land on
    one of them only.

    ``failures`` is the **enumeration**-failure channel, and it is not the same
    thing as ``notes`` (round-2 F-2). A directory that would not list is not a
    degradation to disclose and continue past: DocIQ has not established what
    is under it, so it has no completeness claim to make over the folder, and
    :func:`run` turns a non-empty ``failures`` into a BLOCKED, non-publishable
    run. An entry that cannot be *stat'd* is a different case and stays in
    ``notes`` — it is one known path, and it is inventoried rather than lost.
    """
    files: list[Path] = []
    seen_real = {os.path.realpath(root)}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda q: q.as_posix())
        except OSError as exc:
            # Codex review #1, B-3 sibling sweep. This was a bare ``continue``:
            # a directory that would not list — permissions, a share that went
            # away, a lock — removed EVERY file beneath it from the inventory,
            # not merely from extraction. No note, no warning, no line in any
            # deliverable. It is the largest silent deletion in the pipeline,
            # and it is invisible precisely because the missing files never
            # became records that something downstream could miss.
            #
            # Round 2 (F-2): a warning was not enough. The run went on to
            # publish, which meant an inventory known to be short by an unknown
            # amount replaced a previous complete one — the same destruction
            # B-1 is about, reached by a different door. It is recorded in
            # ``failures`` as well, and ``failures`` blocks the run.
            msg = (
                f"a folder could not be listed and NOTHING inside it was "
                f"inventoried: '{rel_path_of(current, root)}' ({exc}). "
                "Check the folder's permissions and re-run; the documents "
                "under it are absent from this run entirely.")
            if notes is not None:
                notes.append(msg)
            if failures is not None:
                failures.append(msg)
            continue
        for p in children:
            try:
                is_dir = stat.S_ISDIR(os.stat(p).st_mode)
            except OSError as exc:
                # ``os.stat`` rather than ``Path.is_dir()`` precisely so this
                # branch is REACHABLE: ``is_dir()`` swallows the error and
                # answers False, which routed an unexaminable entry down the
                # file path in one mode and off the end of the world in the
                # other.
                #
                # The entry is kept, not skipped. ``scan`` will try to hash it,
                # fail twice, and inventory it as an unreadable Tier-2 record —
                # which is where a present-but-unreadable file belongs, and is
                # what the earlier draft's ``continue`` denied it. A path that
                # cannot be examined is a finding, and a finding is a row.
                if notes is not None:
                    notes.append(
                        f"an entry could not be examined and is inventoried as "
                        f"unreadable rather than dropped: "
                        f"'{rel_path_of(p, root)}' ({exc})")
                files.append(p)
                continue
            if is_dir:
                if not recursive:
                    continue
                real = os.path.realpath(p)
                if real in seen_real:
                    if notes is not None:
                        notes.append(
                            f"a symlink or junction loop was not followed at "
                            f"'{rel_path_of(p, root)}' (already visited as "
                            f"'{real}')")
                    continue
                seen_real.add(real)
                stack.append(p)
            else:
                files.append(p)
    files.sort(key=lambda q: q.as_posix())
    return files


def scan(root: Path, *, recursive: bool = True,
        notes: list[str] | None = None,
        run_notes: RunNotes | None = None,
        failures: list[str] | None = None) -> list[FileEntry]:
    """Inventory every file under ``root``, hashed and tiered, in contract order.

    Sorting happens here rather than at emit time so every consumer of a scan
    sees the same order — including the resume path, which would otherwise
    fast-forward a different set of files on the second run.

    A file that will not open is tried a second time before it is written off.
    Backup agents, anti-virus scanners and Windows share locks all produce a
    transient ``OSError`` on a file that is perfectly readable a moment later,
    and the first draft demoted such a file to Tier 2 permanently — with a
    zero hash, a ``-1`` size and, worse, the "Unrecognized format" hint, which
    says something false about a .pdf that was merely locked. The second
    attempt is what separates "locked for an instant" from "unreadable", and
    it is recorded in ``run_notes`` rather than the hashed warnings because
    which of the two runs hit the lock is not a fact about the evidence.

    ``failures`` is passed straight through to :func:`_iter_files` and collects
    the directories whose enumeration failed. A caller that ignores it gets a
    best-effort inventory and no completeness claim; :func:`run` does not
    ignore it (round-2 F-2).
    """
    root = Path(root)
    entries: list[FileEntry] = []
    for p in _iter_files(root, recursive=recursive, notes=notes,
                         failures=failures):
        if STATE_DIR in p.parts:  # our own run state is not evidence
            continue
        ext = p.suffix.lower()
        rel = rel_path_of(p, root)
        first_error: OSError | None = None
        for attempt in (1, 2):
            try:
                entries.append(FileEntry(
                    path=p, rel_path=rel, sha256=sha256_file(p),
                    size_bytes=p.stat().st_size, ext=ext, tier=tier_of(ext)))
                if attempt == 2 and run_notes is not None:
                    run_notes.invocation.append(
                        f"TRANSIENT READ: '{rel}' could not be opened for "
                        f"hashing on the first attempt ({first_error}) and was "
                        "read on an immediate second attempt; it is inventoried "
                        "normally. Nothing in the deliverables differs, but the "
                        "source volume produced a transient error.")
                break
            except OSError as exc:
                first_error = first_error or exc
                if attempt == 1:
                    # A share lock or a backup agent's handle is measured in
                    # milliseconds. Retrying in the same instruction would test
                    # almost nothing; a tenth of a second costs nothing on a
                    # path that only runs when a read has already failed.
                    time.sleep(0.1)
                if attempt == 2:
                    # Unreadable at hash time, twice. Recorded as a Tier-2
                    # zero-hash entry so it still appears in the inventory: a
                    # file that cannot be opened is a finding, and dropping it
                    # here would be a silent deletion.
                    entries.append(FileEntry(
                        path=p, rel_path=rel, sha256="", size_bytes=-1,
                        ext=ext, tier=2, unreadable=True))
    entries.sort(key=lambda e: (e.rel_path, e.sha256))
    return entries


def unreadable_hint(entry: FileEntry) -> str:
    """What the Unsupported list says about ``entry``.

    A file that could not be opened is not a format DocIQ does not support, and
    conflating the two puts "Unrecognized format — inventoried and hashed only"
    next to a .pdf whose only problem was a lock. It is also not hashed, so the
    row cannot honestly claim to be "hashed only" either.
    """
    if entry.unreadable:
        return ("Could not be opened for reading, on two attempts — it is "
                "listed here so it is not lost, but it was NOT hashed and NOT "
                "extracted. Check the file's permissions, its lock state, and "
                "the source volume, then re-run.")
    return ex.tier2_hint(entry.ext)


def duplicate_groups(entries: list[FileEntry]) -> dict[str, list[str]]:
    """``{sha256: [rel_path, ...]}`` for hashes appearing more than once (§4
    Stage 1 duplicate detection). Exact-hash only — §12 excludes near-duplicate
    detection from v1."""
    by_hash: dict[str, list[str]] = {}
    for e in entries:
        if e.sha256:
            by_hash.setdefault(e.sha256, []).append(e.rel_path)
    return {h: sorted(v) for h, v in sorted(by_hash.items()) if len(v) > 1}


def preflight_disk(entries: list[FileEntry], output_root: Path) -> str | None:
    """Actionable message when the output volume is too small, else ``None``.

    Extracted text is smaller than the raw bytes it came from, so raw total ×
    headroom is a safe over-estimate — and failing before a six-hour OCR run
    beats failing during it.
    """
    try:
        raw_total = sum(max(0, e.size_bytes) for e in entries)
        free = shutil.disk_usage(str(output_root)).free
    except OSError:
        return None  # cannot measure — do not block
    if raw_total and free < raw_total * _DISK_HEADROOM:
        return (f"Insufficient disk to process ~{raw_total / 1024**3:.1f} GB "
                f"({free / 1024**3:.1f} GB free on the output volume). Free "
                "space or split the matter across runs.")
    return None


# ---------------------------------------------------------------------------
# Resume — records replayed from the previous, interrupted run
# ---------------------------------------------------------------------------


def _resume_path(output_root: Path) -> Path:
    return Path(output_root) / STATE_DIR / "resume.jsonl"


def _resume_identity(config: RunConfig) -> str:
    """What a resume file must match before its records may be replayed.

    **The run's own identity projection, not a hand-picked subset of it**
    (Codex review #1 round 2, F-4a). This used to name seven fields, and the
    list went stale the moment amendment A-04 added ``RunConfig.limits``: a
    record cached with OCR disabled, with different OCR model bytes, or under
    different XLSX/CSV/ZIP caps satisfied it and was replayed under a run whose
    manifest then honestly hashed the *new* settings. The documents were not
    produced under the configuration the deliverable claims. Neither "OCR
    disabled" nor a successfully truncated spreadsheet is a degradation, so
    nothing else in the resume path stopped those records either.

    Deriving it from :func:`~dociq.contracts.to_jsonable` with
    ``for_identity=True`` makes the resume key and the manifest's identity the
    same function of the same object, by construction. A future field added to
    :class:`RunConfig` is covered on the day it is added, which is precisely
    what a hand-picked list cannot promise.

    Two consequences worth naming. ``workers`` is in ``_IDENTITY_EXCLUDED``, so
    resuming on a differently-sized pool still works — pool width must not
    change output, and if it ever does that is a defect to fix rather than a
    reason to re-extract. And the key is now *stricter* than it was: a changed
    master-index snapshot refuses the journal. That costs re-extraction, which
    is the cheap side of this trade.

    The caller must hand this the **effective** configuration — the one
    carrying ``limits`` and the actual OCR engine — before the walk, not the
    caller's original. :mod:`dociq.pipeline` builds it pre-walk for that reason.
    """
    return canonical_json({
        "contract": CONTRACT_VERSION,
        "config": to_jsonable(config, for_identity=True),
    })


def _page_from_jsonable(d: dict) -> PageRecord:
    return PageRecord(
        page_no=d["page_no"], text=d["text"], kind=PageKind(d["kind"]),
        ocr_conf=d["ocr_conf"], ocr_line_count=d["ocr_line_count"],
        ocr_low_conf_lines=d["ocr_low_conf_lines"], bates=d["bates"],
        section=d["section"], disposition=Disposition(d["disposition"]),
        drop_rule=d["drop_rule"], notes=tuple(d["notes"]))


def _doc_from_jsonable(d: dict) -> DocumentRecord:
    return DocumentRecord(
        doc_id=d["doc_id"], rel_path=d["rel_path"], filename=d["filename"],
        sha256=d["sha256"], size_bytes=d["size_bytes"], ext=d["ext"],
        pages=tuple(_page_from_jsonable(p) for p in d["pages"]),
        status=ProcessingStatus(d["status"]), parent_doc_id=d["parent_doc_id"],
        container_order=d["container_order"],
        detected_dates=tuple(d["detected_dates"]), doc_type=d["doc_type"],
        profile_id=d["profile_id"], profile_version=d["profile_version"],
        li_file_no=d["li_file_no"], notes=tuple(d["notes"]), error=d["error"])


def _load_resume(config: RunConfig) -> dict[str, list[DocumentRecord]]:
    """``{rel_path: [records]}`` from a previous run, or empty."""
    p = _resume_path(Path(config.output_root))
    if not p.is_file():
        return {}
    out: dict[str, list[DocumentRecord]] = {}
    seen_batch: dict[str, str] = {}
    try:
        with open(p, encoding="utf-8") as fh:
            header = json.loads(fh.readline() or "{}")
            if header.get("identity") != _resume_identity(config):
                return {}
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    # A crash mid-write truncates the last line. Everything
                    # before it is intact; stopping here is the safe read.
                    break
                rel = rec["source_rel_path"]
                # LAST batch wins. One source file can be journaled twice in a
                # single run — the serial-retry pass re-journals whatever it
                # adopted — and the first draft appended, so a replay would
                # have handed the run BOTH the failed record and the retried
                # one for the same file: a duplicate Doc ID and a resurrected
                # failure, from a fix. The batch number is written by
                # _ResumeWriter.add, which is the unit that is written
                # atomically-enough to be a group.
                batch = str(rec.get("batch", ""))
                if seen_batch.get(rel) != batch:
                    seen_batch[rel] = batch
                    out[rel] = []
                out[rel].append(_doc_from_jsonable(rec["doc"]))
    except (OSError, KeyError, ValueError):
        return {}
    return out


class _ResumeWriter:
    """Append-only resume journal. Never fails a run: a resume file that
    cannot be written costs re-extraction, not correctness."""

    def __init__(self, config: RunConfig, enabled: bool,
                 *, append: bool = False) -> None:
        self._fh = None
        self._lock = threading.Lock()
        self._batch = 0
        # The batch token is namespaced per writer. A resumed run appends to
        # the journal a previous writer started, so a bare counter would emit a
        # "batch 1" that collides with the earlier run's "batch 1" for the same
        # file and merge two groups that must not merge. Nothing here reaches
        # hashed content, so a wall-clock nonce is free.
        self._session = f"{time.time_ns():x}"
        if not enabled:
            return
        p = _resume_path(Path(config.output_root))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fresh = not append
            self._fh = open(p, "a" if append else "w", encoding="utf-8",
                            newline="\n")
            if fresh:
                self._fh.write(json.dumps(
                    {"identity": _resume_identity(config)}) + "\n")
                self._fh.flush()
        except OSError:
            self._fh = None

    def add(self, source_rel: str, docs: list[DocumentRecord]) -> None:
        if self._fh is None:
            return
        # One conversion, one dump. The obvious spelling —
        # canonical_json({..., "doc": json.loads(canonical_json(d))}) — walks
        # the document's entire text three times and parses it once more in
        # between, on the coordinating thread, under a lock. On a 900-page
        # scanned PDF that stalls the run's whole progress and watchdog loop;
        # measured on the 17,732-page corpus, where the loop stopped reporting
        # for tens of minutes while extraction carried on underneath it.
        with self._lock:
            self._batch += 1
            batch = f"{self._session}-{self._batch}"
            try:
                for d in docs:
                    self._fh.write(json.dumps(
                        {"source_rel_path": source_rel, "batch": batch,
                         "doc": to_jsonable(d)},
                        sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")) + "\n")
                self._fh.flush()
            except (OSError, ValueError):
                self._fh = None

    def close(self, *, discard: bool, output_root: Path) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        if discard:
            try:
                _resume_path(output_root).unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Per-file extraction → DocumentRecords
# ---------------------------------------------------------------------------


def _dated(pages: tuple[PageRecord, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(detected_dates, notes)`` — dates in first-appearance order, capped."""
    seen: list[str] = []
    known: set[str] = set()
    capped = False
    for p in pages:
        for iso in detect_dates(p.text):
            if iso not in known:
                known.add(iso)
                seen.append(iso)
                if len(seen) >= _MAX_DATES_PER_DOC:
                    capped = True
                    break
        if capped:
            break
    note = ((f"date detection capped at {_MAX_DATES_PER_DOC} distinct dates",)
            if capped else ())
    return tuple(seen), note


def _record(entry: FileEntry, filename: str, ext: str, size: int, sha: str,
            got: ex.ExtractedDoc, *, parent: str | None = None,
            order: int | None = None, rel_path: str | None = None,
            config: RunConfig | None = None) -> DocumentRecord:
    dates, date_notes = _dated(got.pages)
    # Every DocumentRecord in the run is built here, which makes this the one
    # place that has to scrub absolute paths out of hashed content.
    notes = tuple(ex.sanitize_message(n) for n in got.notes + date_notes)
    doc = DocumentRecord(
        doc_id="",  # Stage 3b (Track B) assigns it
        rel_path=rel_path if rel_path is not None else entry.rel_path,
        filename=filename, sha256=sha, size_bytes=size, ext=ext,
        pages=got.pages, status=got.status, parent_doc_id=parent,
        container_order=order, detected_dates=dates,
        profile_id=config.profile_id if config else None,
        profile_version=config.profile_version if config else None,
        notes=notes,
        error=ex.sanitize_message(got.error) if got.error else None)
    doc.validate()
    return doc


def _extract_one(entry: FileEntry, config: RunConfig,
                 opt: ex.ExtractOptions) -> list[DocumentRecord]:
    """One source file → one record, or many when it is an archive.

    Never raises: a pool worker that raises turns one bad file into a dead run.
    """
    try:
        raw = entry.path.read_bytes()
    except OSError as exc:
        return [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                        entry.sha256,
                        ex.ExtractedDoc(status=ProcessingStatus.FAILED,
                                        error=ex.clip_message(f"read failed: {exc}", 200)),
                        config=config)]
    if entry.ext == ".zip":
        return _extract_archive(entry, raw, config, opt)
    if entry.ext in (".eml", ".email", ".msg"):
        return _extract_message(entry, raw, config, opt)
    got = ex.extract(entry.path.name, raw, opt)
    return [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                    entry.sha256, got, config=config)]


def _child_records(entry: FileEntry, exp: ex.ZipExpansion, config: RunConfig,
                   opt: ex.ExtractOptions) -> list[DocumentRecord]:
    """One :class:`DocumentRecord` per container member (archive member or
    email attachment), parented to ``entry``. Shared by both container kinds
    so a Tier-2-inside-a-container is handled identically either way (§3: a
    Tier-2 file inside a container is still Tier-2, never blocking, never
    silently extracted)."""
    parent_key = entry.rel_path
    out: list[DocumentRecord] = []
    for m in exp.members:
        child_ext = Path(m.name).suffix.lower()
        child_sha = hashlib.sha256(m.raw).hexdigest()
        child_rel = f"{entry.rel_path}/{unicodedata.normalize('NFC', m.name)}"
        if ex.is_tier1(child_ext) and child_ext != ".zip":
            got = ex.extract(Path(m.name).name, m.raw, opt)
        else:
            got = ex.ExtractedDoc(status=ProcessingStatus.UNSUPPORTED,
                                  error=ex.tier2_hint(child_ext))
        out.append(_record(entry, Path(m.name).name, child_ext, len(m.raw),
                           child_sha, got, parent=parent_key, order=m.order,
                           rel_path=child_rel, config=config))
    return out


def _extract_archive(entry: FileEntry, raw: bytes, config: RunConfig,
                     opt: ex.ExtractOptions) -> list[DocumentRecord]:
    """The archive itself plus one child record per member.

    The archive gets a zero-page record of its own so it appears in the index
    and the accounting: a ZIP that expanded to nothing must still be visible,
    and its notes are where a bitten anti-DoS cap is disclosed.
    """
    try:
        exp = ex.expand_zip(raw)
    except Exception as exc:
        return [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                        entry.sha256,
                        ex.ExtractedDoc(status=ProcessingStatus.FAILED,
                                        error=ex.clip_message(str(exc), 300)),
                        config=config)]
    out = [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                   entry.sha256,
                   ex.ExtractedDoc(notes=exp.notes + (
                       f"archive expanded to {len(exp.members)} member(s)",)),
                   config=config)]
    out.extend(_child_records(entry, exp, config, opt))
    return out


def _extract_message(entry: FileEntry, raw: bytes, config: RunConfig,
                     opt: ex.ExtractOptions) -> list[DocumentRecord]:
    """The message (headers + body, as a normal Tier-1 record) plus one child
    record per attachment.

    §3: "attachments extracted as child documents linked to the parent
    message ID" is a Tier-1 requirement for MSG/EML, not an enhancement.
    ``ex.extract`` alone only ever produced the message's own page — nothing
    walked the attachment list — so every attachment on every email vanished
    with no record and no note, a silent deletion Principle 1 forbids. This
    closes that gap the same way archive members are handled: attachments are
    children with a ``parent_doc_id`` and a ``container_order``, not text
    concatenated into the parent.
    """
    got = ex.extract(entry.path.name, raw, opt)
    try:
        if entry.ext == ".msg":
            exp = ex.expand_msg_attachments(raw, opt.scratch_dir)
        else:
            exp = ex.expand_eml_attachments(raw)
    except Exception as exc:
        exp = ex.ZipExpansion((), (f"{ex.M_ATTACH_ENUM}: {exc}"[:200],))
    extra_notes = exp.notes + ((f"{len(exp.members)} attachment(s) extracted "
                                f"as child document(s)",) if exp.members else ())
    out = [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                   entry.sha256,
                   ex.ExtractedDoc(pages=got.pages, notes=got.notes + extra_notes,
                                   status=got.status, error=got.error),
                   config=config)]
    out.extend(_child_records(entry, exp, config, opt))
    return out


# ---------------------------------------------------------------------------
# The serial retry — a load-dependent failure must not become a permanent one
# ---------------------------------------------------------------------------
#
# Measured, on the real 368-document corpus: two full runs over byte-identical
# inputs disagreed about one .pptx. Under an OCR-loaded pool it failed with a
# malformed-XML error from the OOXML parser; with OCR off it read all 35 slides.
# Ten isolated re-reads of that file parsed cleanly, and 72 concurrent attempts
# across 12 rounds never reproduced it, so the mechanism is not known.
#
# The mechanism is also not what makes it dangerous. What makes it dangerous is
# that a failure caused by LOAD was written into the deliverables as a property
# of the DOCUMENT — "this file is unreadable" — and the corpus a reviewer got
# therefore depended on how busy the machine was. Principle 1 held (the failure
# was recorded, loudly); Principle 5 did not (two runs, one corpus, two
# outputs).
#
# The remedy does not need the mechanism: nothing that failed while sixteen
# other documents were being extracted is allowed to be written off until it
# has been tried once more, alone. That is decidable without knowing why.

_RETRY_NOTE_CAP = 5


def _degradations(recs: Sequence[DocumentRecord]) -> list[str]:
    """Every sign that ``recs`` read with less than the source holds.

    A whole-document failure, and every note or page note carrying one of
    :data:`dociq.ingest.extract.TRANSIENT_MARKERS`. The list is what the retry
    triggers on and what its disclosure quotes, so it is built once.
    """
    out: list[str] = []
    for r in recs:
        if r.status is ProcessingStatus.FAILED:
            out.append(f"{r.rel_path}: FAILED: {r.error or 'no message'}")
        out.extend(f"{r.rel_path}: {n}" for n in r.notes
                   if ex.has_transient_marker(n))
        for p in r.pages:
            out.extend(f"{r.rel_path} p.{p.page_no}: {n}" for n in p.notes
                       if ex.has_transient_marker(n))
    return out


def _outcome_profile(recs: Sequence[DocumentRecord]) -> tuple[int, int, int, int]:
    """How badly ``recs`` came out. Lower is better, and it is a total order.

    Comparable so the retry's adoption rule is a comparison rather than a
    judgement: failures first, then degradation notes, then (negated, so more
    is better) pages and records recovered.
    """
    return (
        sum(1 for r in recs if r.status is ProcessingStatus.FAILED),
        len(_degradations(recs)),
        -sum(r.pages_in for r in recs),
        -len(recs),
    )


def _summarize(recs: Sequence[DocumentRecord]) -> str:
    """One line describing an outcome, for the disclosure."""
    if not recs:
        return "no records"
    bad = _degradations(recs)
    head = (f"{len(recs)} record(s), {sum(r.pages_in for r in recs)} page(s), "
            f"status {'/'.join(sorted({r.status.value for r in recs}))}")
    if not bad:
        return head + ", no degradation"
    shown = bad[:_RETRY_NOTE_CAP]
    tail = ("" if len(bad) <= _RETRY_NOTE_CAP
            else f" (+{len(bad) - _RETRY_NOTE_CAP} more, capped at "
                 f"{_RETRY_NOTE_CAP} for length)")
    return head + "; " + " | ".join(ex.clip_message(b, 200) for b in shown) + tail


def _abandoned(entry: FileEntry, config: RunConfig, where: str,
               detail: str = "") -> DocumentRecord:
    """The record for a file the watchdog gave up on.

    The message carries NO elapsed time. It used to — "abandoned after 37s" —
    and ``DocumentRecord.error`` is hashed content, so two runs that both timed
    out on the same file produced different bytes because one machine was a
    little busier than the other. That is a determinism break living inside the
    guard whose job is to stop one stuck file hanging the run, and it would
    have shown up on exactly the corpora big enough to need the guard.

    How long it took is a fact about the invocation and goes in the run notes.
    That the limit was reached is a fact about the document, and it is what the
    record keeps.
    """
    msg = f"abandoned {where}: extraction did not finish within the per-file limit"
    if detail:
        msg += f" ({detail})"
    return _record(entry, entry.path.name, entry.ext, entry.size_bytes,
                   entry.sha256,
                   ex.ExtractedDoc(status=ProcessingStatus.FAILED,
                                   error=ex.clip_message(msg, 300)),
                   config=config)


def _extract_serially(entry: FileEntry, config: RunConfig,
                      opt: ex.ExtractOptions,
                      timeout_s: float,
                      notes: RunNotes) -> list[DocumentRecord]:
    """Re-extract ``entry`` alone, still under a watchdog.

    Alone is the point — nothing else of this run's is running — but "alone and
    unbounded" would trade a load-dependent failure for a hang, so the retry
    keeps the same per-file timeout the pool applies. A one-thread executor is
    how the timeout stays enforceable: a plain call cannot be abandoned.

    One honest caveat, stated rather than hidden: a worker the watchdog
    abandoned cannot be killed in Python, so a file that timed out may still
    have a thread running underneath this retry. "Alone" is therefore exact for
    every ordinary failure and approximate for a timeout — which is the one
    case whose retry outcome the disclosure already names both ways.
    """
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dociq-retry")
    t0 = time.monotonic()
    try:
        fut = pool.submit(_extract_one, entry, config, opt)
        try:
            return fut.result(timeout=timeout_s)
        except Exception as exc:
            fut.cancel()
            notes.invocation.append(
                f"SERIAL RETRY ABANDONED: '{entry.rel_path}' was re-read alone "
                f"and still did not finish, after {int(time.monotonic() - t0)}s "
                f"against a {timeout_s:.0f}s limit"
                + (f" ({exc})" if str(exc) else ""))
            return [_abandoned(entry, config, "on the serial retry",
                               ex.sanitize_message(str(exc))[:120])]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _retry_degraded(produced: dict[str, list[DocumentRecord]],
                    entries: dict[str, FileEntry], config: RunConfig,
                    opt: ex.ExtractOptions, timeout_s: float,
                    notes: RunNotes,
                    journal: "_ResumeWriter | None" = None) -> None:
    """Re-read every degraded document serially; adopt the better outcome.

    ``produced`` is mutated in place. The adoption rule is deliberately not
    "always take the retry": the retry is the deterministic reading, so it wins
    ties and wins improvements, but a retry that came out STRICTLY WORSE is a
    second load-dependent event and discarding the better result to satisfy a
    rule would lose evidence. Both outcomes are named either way — the
    disclosure is the deliverable here, not the adoption.
    """
    targets = sorted(rel for rel, recs in produced.items() if _degradations(recs))
    if not targets:
        return
    t0 = time.monotonic()
    for n, rel in enumerate(targets):
        if n >= _RETRY_MAX or time.monotonic() - t0 > _RETRY_BUDGET_S:
            notes.load_dependent.append(
                f"RETRY BUDGET EXHAUSTED: {len(targets) - n} of {len(targets)} "
                f"degraded document(s) were NOT re-read serially (cap "
                f"{_RETRY_MAX} document(s) / {_RETRY_BUDGET_S:.0f}s, "
                f"DOCIQ_RETRY_MAX / DOCIQ_RETRY_BUDGET_S). Their recorded "
                "failures have NOT been distinguished from load-dependent "
                "ones: " + ", ".join(targets[n:n + 20])
                + (" …" if len(targets) - n > 20 else ""))
            break
        entry = entries.get(rel)
        if entry is None:  # pragma: no cover — produced keys come from entries
            continue
        first = produced[rel]
        again = _extract_serially(entry, config, opt, timeout_s, notes)
        p_first, p_again = _outcome_profile(first), _outcome_profile(again)
        if p_again <= p_first:
            produced[rel] = again
            if journal is not None:
                journal.add(rel, again)
        verdict = (
            "RESOLVED: the serial read succeeded and IS what this run recorded"
            if p_again < p_first and not _degradations(again) else
            "UNCHANGED: the serial read reproduced the same outcome, so this is "
            "a property of the document, not of the load"
            if p_again == p_first else
            "IMPROVED: the serial read recovered more and IS what this run "
            "recorded"
            if p_again < p_first else
            "NOT ADOPTED: the serial read came out WORSE than the pooled one, "
            "so the pooled result was kept — this document was degraded on both "
            "attempts and neither reading is trustworthy")
        notes.load_dependent.append(
            f"LOAD-DEPENDENT EXTRACTION CHECK — '{rel}' did not read cleanly "
            f"inside the extraction pool and was re-read serially, alone. "
            f"Pooled: {_summarize(first)}. Serial: {_summarize(again)}. "
            f"{verdict}.")


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(config: RunConfig, opts: WalkOptions | None = None,
        notes: RunNotes | None = None) -> RunResult:
    """Stages 1 and 2 end to end: inventory → extraction → contract records.

    Returns a :class:`RunResult` whose ``documents`` are Tier-1 records in
    contract order and whose ``unsupported`` are the Tier-2 inventory, also in
    contract order. ``RunResult.warnings`` carries everything the operator must
    see that is a fact about the INPUTS — duplicates, disk, extraction failures
    — because those warnings are hashed content.

    ``notes`` is where facts about this INVOCATION go instead: resume,
    cancellation, transient read errors, and the serial-retry disclosures. They
    are just as visible (the pipeline puts them in the log's ``run`` section, in
    ``RunResult.warnings`` at the pipeline level, and in the run summary) and
    they are outside the hash, which is what lets a resumed run and a fresh run
    over the same corpus produce the same bytes.
    """
    opts = opts or WalkOptions()
    notes = notes if notes is not None else RunNotes()
    root = Path(config.source_root)
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    scratch = output_root / STATE_DIR / "scratch"

    warnings: list[str] = []
    scan_notes: list[str] = []
    scan_failures: list[str] = []

    def blocked_result(reason: str, *extra: str) -> RunResult:
        """Record a BLOCKED termination and return the matching result.

        Every abort leaves through here (round-2 F-1). The two contract fields
        and ``RunNotes.termination`` are set from ONE value, so the machine
        result cannot say ``completed`` while the outcome wrapper says
        ``blocked`` — which is exactly what three separate early returns
        produced when each was free to fill in only the half it remembered.
        """
        notes.termination = RunTermination(TerminalStatus.BLOCKED, reason)
        return notes.termination.stamp(
            RunResult(config=config, warnings=(reason,) + extra))

    # Preflight 1 of 3 (Codex B-1). A source root that is not there produces an
    # empty scan, which used to look exactly like a folder containing nothing:
    # the run went green, and — because the pipeline purges before it emits —
    # the previous complete reduction of that matter was deleted and replaced
    # with an empty set. A mistyped path, a disconnected network share and an
    # unmounted volume all land here.
    if not root.is_dir():
        blocked = (
            f"The source folder could not be read: {config.source_root}. It is "
            "not a directory, or the drive or network share it lives on is not "
            "available. Nothing was scanned; check the path and re-run.")
        return blocked_result(blocked)

    entries = scan(root, recursive=opts.recursive, notes=scan_notes,
                   run_notes=notes, failures=scan_failures)
    warnings.extend(sorted(scan_notes))

    # Preflight 2 of 3 (Codex review #1 round 2, F-2). A directory that would
    # not list is not a degradation this run can disclose and carry on past.
    #
    # DocIQ's product is a completeness claim over a folder, and a subtree it
    # could not enumerate is a subtree whose contents it has not established —
    # not "some files were skipped", but "an unknown number of unknown files".
    # The previous draft appended a warning and returned what it had, so a
    # permission error on the root produced a COMPLETED run with zero
    # documents that then purged and replaced a complete prior reduction. A
    # warning does not make an incomplete corpus safe to publish.
    #
    # The boundary matters and is deliberate: a directory that WAS successfully
    # enumerated and holds no files records no failure here, so an empty folder
    # remains a legitimate completed run that may replace prior deliverables.
    # "Successfully enumerated" is the whole distinction.
    if scan_failures:
        return blocked_result(
            f"The folder could not be fully inventoried: "
            f"{len(scan_failures)} folder(s) could not be listed, so DocIQ has "
            "NOT established what this matter contains. No deliverables were "
            "written and the previous run's outputs were left exactly as they "
            "were. Fix the folder permissions or reconnect the share, then "
            "re-run.",
            *sorted(scan_failures))

    dups = duplicate_groups(entries)
    for h, paths in dups.items():
        warnings.append(f"duplicate content (sha256 {h[:12]}…): "
                        + ", ".join(paths))

    # Preflight 3 of 3. Same class as the two checks above: the run never
    # starts, so it has nothing to publish and nothing it may replace.
    disk = preflight_disk(entries, output_root)
    if disk:
        return blocked_result(disk)

    tier1 = [e for e in entries if e.tier == 1]
    tier2 = [e for e in entries if e.tier == 2]

    unsupported = tuple(sorted(
        (DocumentRecord(doc_id="", rel_path=e.rel_path, filename=e.path.name,
                        sha256=e.sha256, size_bytes=e.size_bytes, ext=e.ext,
                        status=ProcessingStatus.UNSUPPORTED,
                        error=unreadable_hint(e)) for e in tier2),
        key=document_sort_key))
    for d in unsupported:
        d.validate()

    opt = ex.ExtractOptions(conf_threshold=config.ocr_conf_threshold,
                            scratch_dir=scratch, ocr_enabled=opts.ocr_enabled)

    replay = _load_resume(config) if opts.resume else {}
    journal = _ResumeWriter(config, opts.resume, append=bool(replay))
    errors = _Errors()
    documents: list[DocumentRecord] = []
    todo: list[FileEntry] = []
    n_stale = 0
    n_degraded = 0
    for e in tier1:
        cached = replay.get(e.rel_path)
        # The journal identity check (contract/profile/OCR engine) says
        # nothing about whether THIS file still has the content it had when
        # the interrupted run read it. A file edited between the crash and
        # the resumed run — the exact recovery scenario resume exists for —
        # would otherwise be replayed under the new run's identity with the
        # OLD text and the OLD sha256, silently: the emitted DocumentRecord
        # would not match the byte on disk it claims to be a reduction of,
        # which is a Principle-2 violation (the source anyone re-checks
        # against no longer says what the record says). The cached list's
        # first record is always the one built from this source file itself
        # (see _extract_one / _extract_archive), so its sha256 is the
        # journal-time hash to compare against the freshly scanned one.
        #
        # A cached record that carries a failure or a degradation marker is
        # never replayed either. Replaying one would cement a failure that
        # might have been load-dependent — the interrupted run is by definition
        # the run that was under stress — and it would do so on a path where
        # the serial retry can no longer reach it. Re-extracting costs one
        # file; replaying costs the corpus.
        degraded = _degradations(cached) if cached else []
        if cached and cached[0].sha256 == e.sha256 and not degraded:
            documents.extend(cached)
        else:
            if cached and degraded:
                n_degraded += 1
            elif cached:
                n_stale += 1
            todo.append(e)
    n_replayed = (len(tier1) - len(todo))
    # Resume state is a fact about this INVOCATION — whether an earlier run
    # crashed here — and never about the corpus. It used to be appended to the
    # hashed warnings, which meant a resumed run and a fresh run over identical
    # inputs produced different `content` bytes and a different corpus hash,
    # with no bad input anywhere. Same class as the retry disclosure below;
    # same remedy.
    if replay:
        notes.invocation.append(
            f"RESUMED RUN: {n_replayed} document(s) were replayed from a "
            "previous interrupted run's journal rather than re-extracted. "
            "Their records were produced by that run.")
    if n_stale:
        notes.invocation.append(
            f"RESUME: {n_stale} document(s) changed on disk since the "
            "interrupted run and were re-extracted rather than replayed.")
    if n_degraded:
        notes.invocation.append(
            f"RESUME: {n_degraded} document(s) had recorded a failure or a "
            "degraded read in the interrupted run and were re-extracted rather "
            "than replayed, so that a load-dependent failure is not carried "
            "forward as a permanent one.")

    cancelled = opts.cancelled or (lambda: False)
    t0 = time.monotonic()
    done_n = 0
    n_failed_so_far = 0
    # Pool results are held per source file rather than appended straight into
    # `documents`, because the serial-retry pass has to be able to REPLACE one
    # file's records wholesale. Appending first and patching later would mean
    # searching a 20,000-record list for the right subset, and getting the
    # child records of an archive right by luck.
    produced: dict[str, list[DocumentRecord]] = {}

    def emit_progress(current: str) -> None:
        if opts.progress:
            opts.progress({"done": done_n, "total": len(todo), "file": current,
                           "failed": n_failed_so_far,
                           "elapsed_s": round(time.monotonic() - t0, 1)})

    if todo:
        pool = ThreadPoolExecutor(max_workers=max(1, opts.workers),
                                  thread_name_prefix="dociq-doc")
        started: dict[str, float] = {}

        def timed(entry: FileEntry) -> list[DocumentRecord]:
            started[entry.rel_path] = time.monotonic()
            return _extract_one(entry, config, opt)

        try:
            maxq = max(1, opts.workers) * 4
            inflight: dict[object, FileEntry] = {}
            i = 0
            while (i < len(todo) or inflight) and not cancelled():
                while i < len(todo) and len(inflight) < maxq:
                    inflight[pool.submit(timed, todo[i])] = todo[i]
                    i += 1
                if not inflight:
                    break
                done_set, _ = fwait(list(inflight), timeout=5,
                                    return_when=FIRST_COMPLETED)
                for fut in done_set:
                    e = inflight.pop(fut, None)
                    if e is None:  # pragma: no cover — the watchdog got it first
                        continue
                    started.pop(e.rel_path, None)
                    try:
                        recs = fut.result()
                    except Exception as exc:  # pragma: no cover — _extract_one
                        recs = [_record(e, e.path.name, e.ext, e.size_bytes,
                                        e.sha256,
                                        ex.ExtractedDoc(
                                            status=ProcessingStatus.FAILED,
                                            error=ex.clip_message(str(exc), 300)),
                                        config=config)]
                    produced[e.rel_path] = recs
                    journal.add(e.rel_path, recs)
                    n_failed_so_far += sum(
                        1 for r in recs if r.status is ProcessingStatus.FAILED)
                    done_n += 1
                # Watchdog on EXECUTION time only. A future still waiting for a
                # worker is merely queued, not stuck — ageing those out
                # mass-cancelled a 2,386-file production in the original tool.
                now = time.monotonic()
                for fut, e in list(inflight.items()):
                    st = started.get(e.rel_path)
                    if st is not None and not fut.done() and \
                            now - st > opts.file_timeout_s:
                        inflight.pop(fut, None)
                        started.pop(e.rel_path, None)
                        fut.cancel()
                        rec = _abandoned(e, config, "inside the extraction pool")
                        notes.invocation.append(
                            f"WATCHDOG: '{e.rel_path}' was abandoned after "
                            f"{int(now - st)}s of execution against a "
                            f"{opts.file_timeout_s:.0f}s per-file limit; it is "
                            "re-read serially before the run writes it off.")
                        produced[e.rel_path] = [rec]
                        journal.add(e.rel_path, [rec])
                        n_failed_so_far += 1
                        done_n += 1
                running = [(e.rel_path, st) for e, st in
                           ((e, started.get(e.rel_path)) for e in inflight.values())
                           if st is not None]
                cur = ""
                if running:
                    rel, st = min(running, key=lambda v: v[1])
                    cur = f"{rel} ({int(now - st)}s)"
                elif inflight:
                    cur = f"{len(inflight)} file(s) queued"
                emit_progress(cur)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    was_cancelled = cancelled()

    # Every document that did not read cleanly gets one serial re-read, before
    # anything is written off. Skipped on a cancelled run: the operator asked
    # for it to stop, and a partial run makes no completeness claim anyway.
    if not was_cancelled:
        _retry_degraded(produced, {e.rel_path: e for e in todo}, config, opt,
                        opts.file_timeout_s, notes, journal)

    documents.extend(r for rel in sorted(produced) for r in produced[rel])
    journal.close(discard=not was_cancelled, output_root=output_root)
    _clear_scratch(scratch)

    if was_cancelled:
        notes.termination = RunTermination(
            TerminalStatus.CANCELLED,
            f"The run was stopped after {len(produced)} of {len(todo)} "
            "file(s) had been read. What was extracted is partial and makes no "
            "completeness claim over the corpus; re-run to produce "
            "deliverables.")
        notes.invocation.append(
            "CANCELLED: the run was cancelled by the operator; these are "
            "partial results and no accounting claim over the corpus holds.")
    # The error list is derived from the FINAL records — after the retry — so a
    # failure the retry resolved leaves no trace in the hashed warnings. That
    # is the point: the hashed content must describe the corpus, and the corpus
    # is what the run finally read. The retry itself is disclosed in `notes`.
    #
    # It is derived from every record in the run, replayed ones included: a
    # resumed run and a fresh run over the same corpus must produce the same
    # warning list, and reading it only off the freshly-extracted records would
    # silently drop the replayed half.
    for r in documents:
        if r.status is ProcessingStatus.FAILED:
            errors.record(r.rel_path, r.error or "")
        for n in r.notes:
            if "recovered via" in n:
                errors.record(r.rel_path, n)
    for item in errors.as_list():
        warnings.append(f"{item['file']}: {item['error']}")

    # A Tier-2 file inside an archive is still a Tier-2 file. It arrives here
    # in the document list because the archive produced it, but §3 puts every
    # listed-only file on the Unsupported list, and an index with .dwg rows in
    # its document section and .dwg rows in its unsupported section is an index
    # that answers "was this processed?" two different ways.
    tier2_children = [d for d in documents
                      if d.status is ProcessingStatus.UNSUPPORTED]
    documents = [d for d in documents
                 if d.status is not ProcessingStatus.UNSUPPORTED]
    documents.sort(key=document_sort_key)
    all_unsupported = sorted(list(unsupported) + tier2_children,
                             key=document_sort_key)
    # The final progress tick reports the count AFTER the retry pass. Leaving
    # the pooled count would tell the operator the run failed on documents it
    # went back and read.
    n_failed_so_far = sum(1 for d in documents
                          if d.status is ProcessingStatus.FAILED)
    emit_progress("")
    # Stamped, not defaulted — and this is the site Codex's F-1 probe did NOT
    # reach, found by enumerating the class rather than the repro. The two
    # blocked returns above are the ones a missing-root probe exercises; THIS
    # one is the CANCELLED path, where the walk sets `notes.termination` at the
    # bottom of the loop and then built a result that took the COMPLETED
    # default anyway. A cancelled run's machine contract claimed a complete
    # corpus, from the same defect, on the path that actually carries documents.
    return notes.termination.stamp(
        RunResult(config=config, documents=tuple(documents),
                  unsupported=tuple(all_unsupported),
                  warnings=tuple(warnings)))


def _clear_scratch(scratch: Path) -> None:
    """§10: nothing DocIQ writes outside the deliverables survives the run."""
    try:
        if scratch.is_dir():
            shutil.rmtree(scratch, ignore_errors=True)
    except OSError:
        pass
