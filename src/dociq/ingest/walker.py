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
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as fwait
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import (
    CONTRACT_VERSION,
    Disposition,
    DocumentRecord,
    PageKind,
    PageRecord,
    ProcessingStatus,
    RunConfig,
    RunResult,
    canonical_json,
    document_sort_key,
)
from . import extract as ex
from .dating import detect_dates

_HASH_CHUNK = 1 << 20

_DEFAULT_WORKERS = int(os.environ.get(
    "DOCIQ_WORKERS", str(min(16, max(1, (os.cpu_count() or 2) - 2)))))
_DEFAULT_FILE_TIMEOUT = float(os.environ.get("DOCIQ_FILE_TIMEOUT", "3600"))
_DISK_HEADROOM = float(os.environ.get("DOCIQ_DISK_HEADROOM", "1.15"))

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


def scan(root: Path, *, recursive: bool = True) -> list[FileEntry]:
    """Inventory every file under ``root``, hashed and tiered, in contract order.

    Sorting happens here rather than at emit time so every consumer of a scan
    sees the same order — including the resume path, which would otherwise
    fast-forward a different set of files on the second run.
    """
    root = Path(root)
    it = root.rglob("*") if recursive else root.glob("*")
    entries: list[FileEntry] = []
    for p in sorted(it, key=lambda q: q.as_posix()):
        if not p.is_file():
            continue
        if STATE_DIR in p.parts:  # our own run state is not evidence
            continue
        ext = p.suffix.lower()
        try:
            entries.append(FileEntry(
                path=p, rel_path=rel_path_of(p, root), sha256=sha256_file(p),
                size_bytes=p.stat().st_size, ext=ext, tier=tier_of(ext)))
        except OSError:
            # Unreadable at hash time. Recorded as a Tier-2 zero-hash entry so
            # it still appears in the inventory: a file that cannot be opened
            # is a finding, and dropping it here would be a silent deletion.
            entries.append(FileEntry(path=p, rel_path=rel_path_of(p, root),
                                     sha256="", size_bytes=-1, ext=ext, tier=2))
    entries.sort(key=lambda e: (e.rel_path, e.sha256))
    return entries


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

    Everything that changes output bytes is in here. A resume file written by a
    different profile or a different OCR engine would replay text this run
    would not have produced — silently, and with this run's identity on it.
    """
    return canonical_json({
        "contract": CONTRACT_VERSION,
        "source_root": config.source_root,
        "profile_id": config.profile_id or "",
        "profile_version": config.profile_version or "",
        "ocr_engine": config.ocr_engine,
        "ocr_engine_version": config.ocr_engine_version,
        "ocr_conf_threshold_pct": config.ocr_conf_threshold_pct,
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
                out.setdefault(rec["source_rel_path"], []).append(
                    _doc_from_jsonable(rec["doc"]))
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
        with self._lock:
            try:
                for d in docs:
                    self._fh.write(canonical_json(
                        {"source_rel_path": source_rel,
                         "doc": json.loads(canonical_json(d))}) + "\n")
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
    got = ex.extract(entry.path.name, raw, opt)
    return [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                    entry.sha256, got, config=config)]


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
    parent_key = entry.rel_path
    out = [_record(entry, entry.path.name, entry.ext, entry.size_bytes,
                   entry.sha256,
                   ex.ExtractedDoc(notes=exp.notes + (
                       f"archive expanded to {len(exp.members)} member(s)",)),
                   config=config)]
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


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(config: RunConfig, opts: WalkOptions | None = None) -> RunResult:
    """Stages 1 and 2 end to end: inventory → extraction → contract records.

    Returns a :class:`RunResult` whose ``documents`` are Tier-1 records in
    contract order and whose ``unsupported`` are the Tier-2 inventory, also in
    contract order. Warnings carry everything the operator must see and the
    §4 Stage-6 gate does not: duplicates, disk, resume, extraction failures.
    """
    opts = opts or WalkOptions()
    root = Path(config.source_root)
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    scratch = output_root / STATE_DIR / "scratch"

    entries = scan(root, recursive=opts.recursive)
    warnings: list[str] = []

    dups = duplicate_groups(entries)
    for h, paths in dups.items():
        warnings.append(f"duplicate content (sha256 {h[:12]}…): "
                        + ", ".join(paths))

    disk = preflight_disk(entries, output_root)
    if disk:
        return RunResult(config=config, warnings=(disk,))

    tier1 = [e for e in entries if e.tier == 1]
    tier2 = [e for e in entries if e.tier == 2]

    unsupported = tuple(sorted(
        (DocumentRecord(doc_id="", rel_path=e.rel_path, filename=e.path.name,
                        sha256=e.sha256, size_bytes=e.size_bytes, ext=e.ext,
                        status=ProcessingStatus.UNSUPPORTED,
                        error=ex.tier2_hint(e.ext)) for e in tier2),
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
    for e in tier1:
        cached = replay.get(e.rel_path)
        if cached is not None:
            documents.extend(cached)
        else:
            todo.append(e)
    if replay:
        warnings.append(f"resumed: {len(tier1) - len(todo)} document(s) replayed "
                        "from the previous interrupted run")

    cancelled = opts.cancelled or (lambda: False)
    t0 = time.monotonic()
    done_n = 0

    def emit_progress(current: str) -> None:
        if opts.progress:
            opts.progress({"done": done_n, "total": len(todo), "file": current,
                           "failed": len(errors.items),
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
                    documents.extend(recs)
                    journal.add(e.rel_path, recs)
                    for r in recs:
                        if r.status is ProcessingStatus.FAILED:
                            errors.record(r.rel_path, r.error or "")
                        for n in r.notes:
                            if "recovered via" in n:
                                errors.record(r.rel_path, n)
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
                        rec = _record(e, e.path.name, e.ext, e.size_bytes,
                                      e.sha256,
                                      ex.ExtractedDoc(
                                          status=ProcessingStatus.FAILED,
                                          error=f"abandoned after "
                                                f"{int(now - st)}s (extraction "
                                                f"did not finish)"),
                                      config=config)
                        documents.append(rec)
                        journal.add(e.rel_path, [rec])
                        errors.record(rec.rel_path, rec.error or "")
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
    journal.close(discard=not was_cancelled, output_root=output_root)
    _clear_scratch(scratch)

    if was_cancelled:
        warnings.append("run cancelled by the operator; partial results only")
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
    emit_progress("")
    return RunResult(config=config, documents=tuple(documents),
                     unsupported=tuple(all_unsupported),
                     warnings=tuple(warnings))


def _clear_scratch(scratch: Path) -> None:
    """§10: nothing DocIQ writes outside the deliverables survives the run."""
    try:
        if scratch.is_dir():
            shutil.rmtree(scratch, ignore_errors=True)
    except OSError:
        pass
