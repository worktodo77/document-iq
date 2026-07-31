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
evidence corpus.

Path B writes nothing new: the matter folder *is* the Expert Assist layout, and
this module verifies that rather than rearranging it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from dociq.emit.paths import OutputLayout, write_text_deterministic
from dociq.verify.tokens import TokenEstimate

__all__ = [
    "ProjectLimits",
    "PackageCheck",
    "UploadPackage",
    "build_upload_package",
    "render_readme",
    "ExpertAssistLayout",
    "expert_assist_layout",
    "README_NAME",
]

README_NAME = "README_START_HERE.txt"


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


def render_readme(
    *,
    matter_name: str,
    document_count: int,
    page_count: int,
    date_range: str,
    estimate: TokenEstimate,
    has_bates: bool,
    id_regime: str,
) -> str:
    """The §8 Path A ``README_START_HERE.txt``.

    Two audiences in one file: the operator, who needs to know what to do with
    the folder, and Claude, which needs the citation conventions stated before
    it reads a page marker. The instruction block is written to be pasted
    verbatim into a Project's instructions field.
    """
    bates_line = (
        "Page markers also carry the Bates number where one was detected, in the "
        "form `===== PAGE 12 [BATES: MNFV 000391] =====`. Cite the Bates number "
        "when it is present."
        if has_bates
        else "This document set is not Bates-stamped; cite the document ID and the "
        "original page number."
    )
    return f"""LI DOCUMENT IQ — {matter_name or "matter"} — START HERE
{"=" * 60}

WHAT THIS FOLDER IS
  A reduced, fully traceable text corpus produced by LI Document IQ from the
  matter's native documents. Every file was mechanically derived; nothing was
  summarized, interpreted, or generated.

  {document_count} documents, {page_count} original pages.
  Estimated size: {estimate.headline} ({estimate.basis.label}).
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


def _iter_package_sources(layout: OutputLayout) -> list[Path]:
    files = sorted(
        (p for p in layout.clean_text.glob("*.txt") if p.is_file()),
        key=lambda p: p.name,
    )
    for extra in (layout.sources_json, layout.index_csv):
        if extra.is_file():
            files.append(extra)
    return files


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
) -> UploadPackage:
    """Assemble ``upload_package/`` (§8 Path A).

    The package is rebuilt from scratch each time. A stale text file left over
    from an earlier run would be uploaded as if it were current evidence, which
    is a worse failure than a slow rebuild.
    """
    lim = limits or ProjectLimits()
    target = layout.upload_package
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    oversized: list[tuple[str, int]] = []
    total = 0
    for src in _iter_package_sources(layout):
        dst = target / src.name
        shutil.copyfile(src, dst)
        size = dst.stat().st_size
        total += size
        copied.append(dst.name)
        if size > lim.max_file_bytes:
            oversized.append((dst.name, size))

    if estimate is None:
        from dociq.verify.tokens import estimate_tokens

        estimate = estimate_tokens("")

    readme_text = render_readme(
        matter_name=matter_name,
        document_count=document_count or max(len(copied) - 2, 0),
        page_count=page_count,
        date_range=date_range,
        estimate=estimate,
        has_bates=has_bates,
        id_regime=id_regime,
    )
    readme = write_text_deterministic(target / README_NAME, readme_text)
    copied.append(README_NAME)

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
