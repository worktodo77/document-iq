"""§8 Claude handoff — Path A's upload package and Path B's matter layout.

**No network, no browser automation, no upload.** §8 rules that out explicitly
(Claude.ai Projects expose no public upload API, and session-key tools are
excluded on security and ToS grounds), and Principle 4 rules out network access
of any kind regardless. This module produces files and returns paths; opening a
folder or a browser is the GUI's business, and even then it is a launch, not a
transfer.

Path A assembles ``upload_package/`` containing **only** what is meant to be
uploaded: ``clean_text/*.txt``, ``sources.json`` and ``document_index.csv``. The
processing log and run summary stay behind in the matter folder — they are the
audit trail, and uploading them would put DocIQ's own internals into the
evidence corpus. That rule is now *asserted* rather than merely implemented:
see :data:`SANCTIONED_NAMES` and :func:`assert_only_sanctioned`.

Path B writes nothing new: the matter folder *is* the Expert Assist layout, and
this module verifies that rather than rearranging it.

**D-20 (amendment A-12).** A Path A package is a deliberately scoped SUBSET
unless it says otherwise, and once a folder has been dragged into a Project
nobody downstream can tell a subset from a whole record by looking at it. So
:func:`build_upload_package` takes a ``doc_ids`` filter and a
``scope_statement``, and :func:`render_readme` emits that statement **first, at
the top of the file, ahead of everything else** — before the title, before the
counts, before the instruction block.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from dociq.contracts import canonical_json
from dociq.emit.indexbook import INDEX_COLUMNS
from dociq.emit.paths import OutputLayout, safe_component, write_text_deterministic
from dociq.verify.tokens import TokenEstimate

__all__ = [
    "ProjectLimits",
    "PackageCheck",
    "UploadPackage",
    "build_upload_package",
    "render_readme",
    "default_scope_statement",
    "ExpertAssistLayout",
    "expert_assist_layout",
    "assert_only_sanctioned",
    "PackageContentError",
    "README_NAME",
    "SANCTIONED_NAMES",
]

README_NAME = "README_START_HERE.txt"

SANCTIONED_NAMES = frozenset({"sources.json", "document_index.csv", README_NAME})
"""Non-``.txt`` files §8 permits in the package, exhaustively.

Everything else in the package must be a ``clean_text`` file. This set is the
machine-readable form of §8's "containing only the files intended for upload",
and :func:`assert_only_sanctioned` is what makes it load-bearing rather than
documentation. The failure mode it exists to prevent is specific and bad:
``processing_log.json``, ``run_summary.pdf``, ``output_manifest.json``,
``doc_ids_issued.json``, ``reconciliation.csv`` and the matter's profile copy
are DocIQ's own audit trail, and uploading them puts the tool's internals into
the evidence corpus — where an analyst can quote them back as if they were
project records.
"""


class PackageContentError(RuntimeError):
    """An unsanctioned file reached the upload package (§8).

    Raised rather than warned. A package is dragged into a Project whole; there
    is no step at which an operator inspects it file by file, so a warning about
    an extra file is a warning nobody acts on before the upload happens.
    """


def assert_only_sanctioned(root: Path) -> tuple[str, ...]:
    """Fail unless every file under ``root`` is one §8 sanctions.

    Walks the tree rather than listing the top level: a subdirectory of audit
    files would pass a top-level check and upload just the same.
    """
    names = tuple(
        sorted(str(p.relative_to(root)).replace("\\", "/")
               for p in root.rglob("*") if p.is_file())
    )
    intruders = tuple(
        n for n in names
        if n not in SANCTIONED_NAMES and not (n.endswith(".txt") and "/" not in n)
    )
    if intruders:
        raise PackageContentError(
            "§8 permits only clean_text/*.txt, sources.json, "
            f"document_index.csv and {README_NAME} in an upload package. These "
            f"would have been uploaded into the evidence corpus: "
            f"{', '.join(intruders)}"
        )
    return names


@dataclass(frozen=True, slots=True)
class ProjectLimits:
    """Constraints the package is checked against (§8 Path A).

    These are **operator-configurable assumptions, not fetched facts**. DocIQ
    has no network and cannot ask what today's Project limits are, so hard-coding
    a number as authoritative would be a claim it cannot support. The defaults
    are conservative; ``max_files = 0`` means "no file-count limit is being
    enforced", which the check reports rather than assumes away.
    """

    max_file_bytes: int = 30 * 1024 * 1024
    max_files: int = 0
    direct_context_tokens: int = 200_000


@dataclass(frozen=True, slots=True)
class PackageCheck:
    """Result of checking the assembled package against :class:`ProjectLimits`."""

    file_count: int
    total_bytes: int
    oversized: tuple[tuple[str, int], ...]
    limits: ProjectLimits
    unenforced: tuple[str, ...]
    """Limits that were *not* checked, named. An unchecked limit reported as
    "passed" would be a lie of omission."""

    @property
    def ok(self) -> bool:
        return not self.oversized and (
            self.limits.max_files == 0 or self.file_count <= self.limits.max_files
        )


@dataclass(frozen=True, slots=True)
class UploadPackage:
    root: Path
    files: tuple[str, ...]
    check: PackageCheck
    readme: Path
    mode_statement: str

    doc_count: int = 0
    """Documents whose text is in the package. Not ``len(files)`` minus a
    constant: the caller needs a figure it can put on screen beside the scope
    statement, and deriving it by subtracting "the three non-text files" is a
    rule that breaks the moment the package gains a fourth."""

    scope_statement: str = ""
    """The statement written into the package, returned verbatim so the screen
    shows the operator exactly what the recipient will read (A-12)."""

    missing: tuple[str, ...] = ()
    """Doc IDs that were asked for and have no ``clean_text`` file.

    Reported rather than silently skipped: a package one document short of the
    scope its own statement claims is a smaller version of the D-20 failure, and
    the operator is the only one who can say whether it matters."""


def default_scope_statement(document_count: int, matter_name: str = "",
                            unsupported: int = 0) -> str:
    """The full-scope statement, for a package nobody scoped (D-20).

    **This wording is deliberately duplicated** from
    :meth:`dociq.gui.view_models.PackageScope.statement`, and the duplication is
    structural rather than careless: the pagemodel freeze forbids the GUI from
    importing :mod:`dociq.emit`, so a single shared author is not available to
    both. ``tests/test_emit.py::test_scope_statement_authors_agree`` binds the
    two — the whole-record wording must be character-identical, or one of the
    two is drifting and the test says which.

    A package built with no statement at all is the failure D-20 exists to
    prevent, so there is no "" path: the pipeline's own whole-corpus package
    gets this, and a scoped package gets the operator's.
    """
    listed = (
        f"\n  {unsupported:,} further file{'' if unsupported == 1 else 's'} "
        "in this matter were inventoried and hashed but their text was not "
        "extracted (unsupported formats, §5). They are NOT in this package; "
        "each has a row in document_index.csv with the status Unsupported."
        if unsupported else ""
    )
    head = f"SCOPE OF THIS PACKAGE{(' — ' + matter_name) if matter_name else ''}"
    body = (
        f"This package covers ALL {document_count:,} documents whose text DocIQ "
        "extracted from the matter record."
        + (listed or " It is the complete production as DocIQ processed it.")
    )
    return f"{head}\n{'=' * 60}\n  {body}\n"


def render_readme(
    *,
    matter_name: str,
    document_count: int,
    page_count: int,
    date_range: str,
    estimate: TokenEstimate,
    has_bates: bool,
    id_regime: str,
    scope_statement: str = "",
) -> str:
    """The §8 Path A ``README_START_HERE.txt``.

    Two audiences in one file: the operator, who needs to know what to do with
    the folder, and Claude, which needs the citation conventions stated before
    it reads a page marker. The instruction block is written to be pasted
    verbatim into a Project's instructions field.

    ``scope_statement`` (D-20, amendment A-12) is emitted **first — before the
    title line and before any count**. Position is the whole point: a scope
    caveat under a "368 documents, 18,521 pages" headline is read after the
    reader has already formed the belief it exists to prevent. If it is empty a
    full-scope statement is authored by :func:`default_scope_statement`, because
    a package that says nothing about its scope is exactly the artifact D-20
    forbids.
    """
    scope = scope_statement.strip() or default_scope_statement(
        document_count, matter_name
    ).strip()
    bates_line = (
        "Page markers also carry the Bates number where one was detected, in the "
        "form `===== PAGE 12 [BATES: MNFV 000391] =====`. Cite the Bates number "
        "when it is present."
        if has_bates
        else "This document set is not Bates-stamped; cite the document ID and the "
        "original page number."
    )
    return f"""{scope}
LI DOCUMENT IQ — {matter_name or "matter"} — START HERE
{"=" * 60}

WHAT THIS FOLDER IS
  A reduced, fully traceable text corpus produced by LI Document IQ from the
  matter's native documents. Every file was mechanically derived; nothing was
  summarized, interpreted, or generated.

  {document_count} documents, {page_count} original pages.
  Estimated size: {estimate.headline} ({estimate.basis.label}).
  Method: {estimate.method}. No tokenizer was run, and DocIQ asserts no lower
  bound on token count — only that a text cannot need more tokens than it has
  UTF-8 bytes. Full assumptions in processing_log.json.
  {estimate.capacity().statement}

HOW TO USE IT
  1. Create a Claude Project for this matter.
  2. Drag EVERY file in this folder into the Project's knowledge.
  3. Paste the block below into the Project's instructions field.

{"-" * 60}
PROJECT INSTRUCTIONS (paste this block)
{"-" * 60}
You are assisting with a forensic analysis of the {matter_name or "matter"}
document set.{f" Documents span {date_range}." if date_range else ""}

{scope}

The knowledge base contains {document_count} documents as plain text, one file
per document, named by document ID ({id_regime} numbering).

Every page begins with a marker of the form:
    ===== PAGE 12 =====
The page number is the page of the ORIGINAL native document, never of this
text file. Always cite the document ID and that original page number, so any
quotation can be checked against the source document.
{bates_line}

sources.json maps each document ID to its text file. document_index.csv lists
every document with its filename, format, date, page count and hash.

Quote only what the documents say. If a fact is not in the record, say so
rather than inferring it.
{"-" * 60}

WHAT IS NOT HERE
  The processing log and run summary stay in the matter folder alongside the
  native documents. They are the audit trail for how this corpus was produced
  and are not part of the analysis corpus.
"""


def _text_files(layout: OutputLayout) -> list[Path]:
    return sorted(
        (p for p in layout.clean_text.glob("*.txt") if p.is_file()),
        key=lambda p: p.name,
    )


def _filtered_sources(layout: OutputLayout, keep: set[str]) -> str | None:
    """``sources.json`` restricted to ``keep``, or ``None`` if unreadable.

    A subset package that carried the whole matter's ``sources.json`` would hand
    the reader a manifest naming documents the package does not contain — and
    §7 makes ``sources.json`` the thing Expert Assist reads to find text, so
    every one of those names is a path that resolves to nothing. Filtering keeps
    the manifest true of the folder it sits in.
    """
    import json

    try:
        payload = json.loads(layout.sources_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return canonical_json(
        {k: v for k, v in payload.items() if k in keep}
    ) + "\n"


def _filtered_index_csv(layout: OutputLayout, keep: set[str]) -> str | None:
    """``document_index.csv`` restricted to ``keep``, header preserved.

    Rows are matched on column 0 (``Doc ID``), which is
    :data:`dociq.emit.indexbook.INDEX_COLUMNS`\\ [0] — asserted below rather than
    assumed, because a column reorder would otherwise filter on ``Filename`` and
    quietly produce an empty index.
    """
    assert INDEX_COLUMNS[0] == "Doc ID"
    import csv
    import io

    try:
        text = layout.index_csv.read_text(encoding="utf-8")
    except OSError:
        return None
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        return None
    header, body = rows[0], rows[1:]
    kept = [r for r in body if r and r[0] in keep]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(kept)
    return out.getvalue()


def build_upload_package(
    layout: OutputLayout,
    *,
    matter_name: str = "",
    date_range: str = "",
    document_count: int = 0,
    page_count: int = 0,
    estimate: TokenEstimate | None = None,
    has_bates: bool = False,
    id_regime: str = "DIQ-native",
    limits: ProjectLimits | None = None,
    doc_ids: tuple[str, ...] | None = None,
    scope_statement: str = "",
    unsupported: int = 0,
) -> UploadPackage:
    """Assemble ``upload_package/`` (§8 Path A).

    The package is rebuilt from scratch each time. A stale text file left over
    from an earlier run would be uploaded as if it were current evidence, which
    is a worse failure than a slow rebuild.

    ``doc_ids`` (A-12) selects which documents' text goes in; ``None`` means all
    of them. When it selects a proper subset, ``sources.json`` and
    ``document_index.csv`` are **filtered to match** rather than copied whole —
    an index listing 368 documents inside a folder holding 12 is an invitation
    to cite the 356 that are not there.

    ``scope_statement`` (D-20) is written into the README ahead of everything
    else. It is never optional in effect: an empty one is replaced by
    :func:`default_scope_statement`.
    """
    lim = limits or ProjectLimits()
    target = layout.upload_package
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    available = _text_files(layout)
    if doc_ids is None:
        selected, missing, subset = available, (), False
    else:
        wanted = {f"{safe_component(d)}.txt": d for d in doc_ids}
        selected = [p for p in available if p.name in wanted]
        found = {p.name for p in selected}
        missing = tuple(sorted(d for n, d in wanted.items() if n not in found))
        subset = len(selected) != len(available)

    copied: list[str] = []
    oversized: list[tuple[str, int]] = []
    total = 0

    def record(name: str, size: int) -> None:
        nonlocal total
        total += size
        copied.append(name)
        if size > lim.max_file_bytes:
            oversized.append((name, size))

    for src in selected:
        dst = target / src.name
        shutil.copyfile(src, dst)
        record(dst.name, dst.stat().st_size)

    keep = {p.stem for p in selected}
    for src, filtered in (
        (layout.sources_json, _filtered_sources(layout, keep) if subset else None),
        (layout.index_csv, _filtered_index_csv(layout, keep) if subset else None),
    ):
        if not src.is_file():
            continue
        dst = target / src.name
        if filtered is None:
            shutil.copyfile(src, dst)
        else:
            write_text_deterministic(dst, filtered)
        record(dst.name, dst.stat().st_size)

    if estimate is None:
        from dociq.verify.tokens import estimate_tokens

        estimate = estimate_tokens("")

    doc_count = document_count or len(selected)
    scope = scope_statement.strip() or default_scope_statement(
        doc_count, matter_name, unsupported
    )
    readme_text = render_readme(
        matter_name=matter_name,
        document_count=doc_count,
        page_count=page_count,
        date_range=date_range,
        estimate=estimate,
        has_bates=has_bates,
        id_regime=id_regime,
        scope_statement=scope,
    )
    readme = write_text_deterministic(target / README_NAME, readme_text)
    copied.append(README_NAME)

    # §8's "only the sanctioned files" rule, checked against what is ON DISK
    # rather than against what this function believes it wrote. The two differ
    # exactly when something else put a file there, which is the case worth
    # catching.
    assert_only_sanctioned(target)

    unenforced: list[str] = []
    if lim.max_files == 0:
        unenforced.append(
            "file-count limit not enforced: DocIQ is offline and cannot confirm "
            "the current Claude Project file-count limit"
        )
    check = PackageCheck(
        file_count=len(copied),
        total_bytes=total + readme.stat().st_size,
        oversized=tuple(oversized),
        limits=lim,
        unenforced=tuple(unenforced),
    )
    verdict = estimate.capacity(lim.direct_context_tokens)
    return UploadPackage(
        root=target,
        files=tuple(sorted(copied)),
        check=check,
        readme=readme,
        mode_statement=verdict.statement,
        doc_count=len(selected),
        scope_statement=scope,
        missing=missing,
    )


@dataclass(frozen=True, slots=True)
class ExpertAssistLayout:
    """Path B: the matter folder read directly from disk, no upload (§8)."""

    matter_root: Path
    clean_text: Path
    sources_json: Path
    document_index_csv: Path
    processing_log: Path
    present: tuple[str, ...]
    missing: tuple[str, ...]
    instructions: str

    @property
    def ready(self) -> bool:
        return not self.missing


def expert_assist_layout(layout: OutputLayout) -> ExpertAssistLayout:
    """Verify that the matter folder is already Expert-Assist-shaped.

    Nothing is moved or copied. §8 Path B's whole claim is that DocIQ writes its
    outputs where evidence-mining already looks, so the correct implementation
    is a check: if this ever needs to rearrange files, the emit layer's paths
    are wrong and *that* is what should change.
    """
    checks = {
        "clean_text/": layout.clean_text,
        "sources.json": layout.sources_json,
        "document_index.csv": layout.index_csv,
        "processing_log.json": layout.processing_log,
    }
    present = tuple(name for name, path in checks.items() if path.exists())
    missing = tuple(name for name, path in checks.items() if not path.exists())
    instructions = (
        f"Open Claude Cowork (or Claude Code) with this folder as the working "
        f"directory:\n\n    {layout.root}\n\n"
        "Then run the Expert Assist intake skill. Expert Assist reads clean_text/ "
        "and sources.json directly from disk — nothing is uploaded, and Claude "
        "Project capacity limits do not apply. This is the recommended route for "
        "forensic matters: the full audit trail stays local, beside the evidence."
    )
    return ExpertAssistLayout(
        matter_root=layout.root,
        clean_text=layout.clean_text,
        sources_json=layout.sources_json,
        document_index_csv=layout.index_csv,
        processing_log=layout.processing_log,
        present=present,
        missing=missing,
        instructions=instructions,
    )
