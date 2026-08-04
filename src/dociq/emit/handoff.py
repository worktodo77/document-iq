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
import time
from dataclasses import dataclass, replace
from pathlib import Path

from dociq.contracts import IdRegime, canonical_json
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
    "PackageSwapError",
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


class PackageSwapError(RuntimeError):
    """The package was assembled, or abandoned, but the FOLDER could not be put
    into the state the screen is about to describe (Codex review #2 fix round,
    finding A-4).

    Distinct from :class:`PackageContentError`, which is about what a package
    contains. This one is about what is on disk under
    ``upload_package`` and its siblings after a build that did not go through,
    and it exists because the alternative — absorbing a failed cleanup and
    returning — is exactly how a partial folder came to be presented as a
    complete one. Its message names the directory involved, because that is the
    only thing the operator can act on.
    """


_INCOMING_SUFFIX = ".incoming"
"""Where a package is ASSEMBLED. Never the folder an operator uploads."""

_SUPERSEDED_SUFFIX = ".superseded"
"""Where the previous package waits while the new one takes its name."""


def _remove_tree(path: Path, *, attempts: int = 8, delay: float = 0.02) -> bool:
    """Remove ``path`` if it is there. **Returns whether it is gone.**

    ``shutil.rmtree(..., ignore_errors=True)`` on its own is the pattern Codex
    named in B-4: the error is absorbed and the caller goes on to act as though
    the directory were removed. Here the errors are still swallowed — retrying
    is the right response to the Windows case this exists for, an on-access
    scanner or backup agent holding one file open for a moment — but the
    ANSWER is the state of the disk afterwards, not the absence of an
    exception, and every caller below branches on it.

    Deliberately local rather than reused from :mod:`dociq.emit.paths`, whose
    ``_retry_io`` is private and under concurrent revision for B-4.
    """
    for attempt in range(attempts):
        if not path.exists():
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(delay * (attempt + 1))
    return not path.exists()


def _retry_rename(src: Path, dst: Path, *, attempts: int = 8,
                  delay: float = 0.02) -> None:
    """Rename with the same retry discipline, and NO absorption: the last
    :class:`OSError` propagates."""
    for attempt in range(attempts):
        try:
            src.rename(dst)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


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
    capacity_tokens: int = ProjectLimits().direct_context_tokens,
) -> str:
    """The §8 Path A ``README_START_HERE.txt``.

    Two audiences in one file: the operator, who needs to know what to do with
    the folder, and Claude, which needs the citation conventions stated before
    it reads a page marker. The instruction block is written to be pasted
    verbatim into a Project's instructions field.

    ``scope_statement`` (D-20, amendment A-12) is emitted **first — before the
    title line and before any count**. Position is the whole point: a scope
    caveat under a "368 documents, N pages" headline is read after the reader
    has already formed the belief it exists to prevent. If it is empty a
    full-scope statement is authored by :func:`default_scope_statement`, because
    a package that says nothing about its scope is exactly the artifact D-20
    forbids.

    ``capacity_tokens`` is the limit the capacity sentence is computed against,
    and it is a **parameter rather than a default this function reaches for**.
    It used to call the bare ``estimate.capacity()``, which falls back to
    :data:`dociq.verify.tokens.DIRECT_CONTEXT_TOKENS` — a *different* literal
    from the ``ProjectLimits.direct_context_tokens`` that
    :func:`build_upload_package` checks the package against. With any override
    the two disagreed, and the disagreement was not subtle: one reproduced
    package carried ``mode_statement`` "Fits directly in a Claude Project
    without retrieval mode (about 20% of direct-context capacity)" while **this
    README** told the recipient "About 181–197% of direct-context capacity — the
    Project will operate in retrieval (RAG) mode."

    The recipient reads the README. The operator reads ``mode_statement``. They
    are now computed from one limit and one verdict, so they cannot disagree by
    construction, and ``tests/test_emit.py`` asserts the verdict appears
    verbatim in the file rather than asserting the two happen to match today.
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
  {estimate.capacity(capacity_tokens).statement}

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


def _filtered_sources(layout: OutputLayout, keep: set[str]) -> str:
    """``sources.json`` restricted to ``keep``. Raises if it cannot be filtered.

    A subset package that carried the whole matter's ``sources.json`` would hand
    the reader a manifest naming documents the package does not contain — and
    §7 makes ``sources.json`` the thing Expert Assist reads to find text, so
    every one of those names is a path that resolves to nothing. Filtering keeps
    the manifest true of the folder it sits in.

    **Every failure raises; none returns a value the caller can fall back from.**
    This function used to return ``None`` on ``OSError``, on invalid JSON and on
    a non-dict payload, and the caller then copied the whole matter's manifest
    into the subset package — the exact failure this function exists to prevent,
    reached by the ordinary path on this machine (antivirus holding a lock on
    ``sources.json`` is an ``OSError``). :func:`assert_only_sanctioned` cannot
    catch it either, because ``sources.json`` is a sanctioned *name*: the file
    that would be uploaded is correctly named and wrong inside.
    """
    import json

    try:
        raw = layout.sources_json.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageContentError(_UNFILTERABLE.format(
            name="sources.json", why=f"it could not be read ({exc})")) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise PackageContentError(_UNFILTERABLE.format(
            name="sources.json", why=f"it is not valid JSON ({exc})")) from exc
    if not isinstance(payload, dict):
        raise PackageContentError(_UNFILTERABLE.format(
            name="sources.json",
            why=f"its top level is {type(payload).__name__}, not an object "
                "keyed by Doc ID"))
    return canonical_json(
        {k: v for k, v in payload.items() if k in keep}
    ) + "\n"


def _filtered_index_csv(layout: OutputLayout, keep: set[str]) -> str:
    """``document_index.csv`` restricted to ``keep``, header preserved.

    Rows are matched on column 0 (``Doc ID``), which is
    :data:`dociq.emit.indexbook.INDEX_COLUMNS`\\ [0] — asserted below rather than
    assumed, because a column reorder would otherwise filter on ``Filename`` and
    quietly produce an empty index.

    Raises rather than returning ``None``, for the reason given in
    :func:`_filtered_sources`.
    """
    assert INDEX_COLUMNS[0] == "Doc ID"
    import csv
    import io

    try:
        text = layout.index_csv.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageContentError(_UNFILTERABLE.format(
            name="document_index.csv",
            why=f"it could not be read ({exc})")) from exc
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise PackageContentError(_UNFILTERABLE.format(
            name="document_index.csv",
            why="it is empty, so it carries no header to preserve"))
    header, body = rows[0], rows[1:]
    kept = [r for r in body if r and r[0] in keep]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(kept)
    return out.getvalue()


_UNFILTERABLE = (
    "This package covers a SUBSET of the matter, so {name} has to be filtered "
    "to match it — and it cannot be, because {why}. DocIQ refuses rather than "
    "copying the whole matter's {name} into a scoped package: the recipient "
    "would get a manifest naming documents the folder does not contain, and "
    "every one of those names is a citation that resolves to nothing. Fix the "
    "file (or release whatever is holding it) and build the package again."
)


def _assert_manifest_matches_folder(
    root: Path, expected: set[str], *, check_index: bool
) -> None:
    """Every Doc ID named by the package's manifests is a file in the package.

    The class-level guard, not the instance one. It does not care *how* a wrong
    manifest got there — a fallback copy, a future writer, a stale file left by
    something else — it asserts the property that makes a package citable: what
    the manifest names, the folder holds.

    ``sources.json`` is checked always: §7 makes it the thing Expert Assist
    reads to FIND text, so a name in it that has no file is a citation that
    resolves to nothing.

    ``document_index.csv`` is checked only for a SUBSET package
    (``check_index``), and the asymmetry is deliberate rather than a weakening.
    A whole-record index legitimately carries a row for every §5 *unsupported*
    file — inventoried, hashed, given a Doc ID and marked Unsupported so the
    production stays complete — and those files have no ``clean_text`` by
    definition. Asserting over it would flag the §5 rule as a defect. A subset's
    index is filtered to the selected Doc IDs, so there the property holds and
    is worth asserting.
    """
    import csv
    import io
    import json

    named: set[str] = set()
    sources = root / "sources.json"
    if sources.is_file():
        payload = json.loads(sources.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            named |= set(payload)
    index = root / "document_index.csv"
    if check_index and index.is_file():
        rows = list(csv.reader(io.StringIO(
            index.read_text(encoding="utf-8"), newline="")))
        named |= {r[0] for r in rows[1:] if r and r[0]}

    phantom = sorted(named - expected)
    if phantom:
        raise PackageContentError(
            f"{len(phantom)} document(s) are named by this package's manifests "
            f"but are not in it: {', '.join(phantom[:10])}"
            + (" …" if len(phantom) > 10 else "")
            + ". §7 makes sources.json the thing Expert Assist reads to FIND "
              "text, so each of those names is a path that resolves to nothing."
        )


def build_upload_package(
    layout: OutputLayout,
    *,
    matter_name: str = "",
    date_range: str = "",
    document_count: int = 0,
    page_count: int = 0,
    estimate: TokenEstimate | None = None,
    has_bates: bool = False,
    id_regime: str = IdRegime.NATIVE.value,
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

    **The folder an operator uploads is never a work in progress** (Codex
    review #2 fix round, finding A-4). This function used to delete the
    previous package, create ``upload_package/`` and write into it file by
    file, so any exception after the first copy — a locked README, a filter
    that raised, a §7/§8 validation that failed — left a CURRENT, partial,
    unvalidated package sitting under the name the operator drags into a
    Project. The GUI's failure state told them, in terms, that any package on
    disk was from an earlier build. Both statements could not be true and the
    false one was the reassuring one.

    So the assembly happens in a sibling ``upload_package.incoming/`` and the
    published name is only claimed after **every** copy, filter, README and
    validation has passed:

    * any failure discards the sibling and leaves ``upload_package/``
      byte-for-byte as the earlier build the screen says it is;
    * the previous package is moved aside, not deleted, and is only removed
      once the new one holds the name — so nothing is destroyed before its
      replacement is in place, and a failed move restores it;
    * every removal is checked for whether the directory is actually GONE, and
      a failure raises :class:`PackageSwapError` naming the directory rather
      than being absorbed.
    """
    lim = limits or ProjectLimits()
    published = layout.upload_package
    staging = published.with_name(published.name + _INCOMING_SUFFIX)
    superseded = published.with_name(published.name + _SUPERSEDED_SUFFIX)

    # Residue from an earlier attempt that died between these same steps. It is
    # removed BEFORE anything is assembled, and a failure to remove it stops the
    # build: assembling into a directory that still holds another attempt's
    # files is the mixed-set failure with a different name on it.
    for leftover in (staging, superseded):
        if not _remove_tree(leftover):
            raise PackageSwapError(
                f"{leftover} is left over from an earlier package build and "
                f"could not be removed, so this build was not started. The "
                f"package folder was NOT touched. Close anything holding files "
                f"in that folder open and try again."
            )

    staging.mkdir(parents=True, exist_ok=False)
    try:
        assembled = _assemble_package(
            layout, staging,
            matter_name=matter_name, date_range=date_range,
            document_count=document_count, page_count=page_count,
            estimate=estimate, has_bates=has_bates, id_regime=id_regime,
            lim=lim, doc_ids=doc_ids, scope_statement=scope_statement,
            unsupported=unsupported,
        )
    except BaseException as exc:
        # EVERY exception, including KeyboardInterrupt — a build abandoned by
        # the operator leaves the same partial directory as one abandoned by an
        # OSError, and the operator reads the same screen afterwards.
        if _remove_tree(staging):
            raise
        raise PackageSwapError(
            f"{exc} The partial package this attempt was assembling could "
            f"also not be cleaned up: {staging} is still on disk. The package "
            f"folder itself was NOT touched and still holds the earlier build "
            f"— do NOT upload {staging.name}."
        ) from exc

    return _publish_package(assembled, staging, published, superseded)


def _publish_package(assembled: UploadPackage, staging: Path, published: Path,
                     superseded: Path) -> UploadPackage:
    """Give the validated staging directory the published name.

    Four steps, and the ORDER is the guarantee. Everything that can fail is
    done while the earlier package can still be put back, and the last step —
    the one with no recovery — is the cheapest operation available: renaming a
    directory this function just created, in the same parent, onto a name
    nothing occupies.

    1. Move the earlier package ASIDE. A metadata operation; a failure here
       destroys nothing and the folder still holds the earlier build.
    2. REMOVE it, and check that it is gone. A failure here puts it back.
    3. Rename staging onto the published name.
    4. Nothing.

    **Why the removal is step 2 and not step 4.** The obvious order — publish,
    then tidy up — has one outcome this one does not: the new package holds the
    published name, correct and complete, while a folder of the previous
    package's files sits beside it under a name an operator could still upload.
    Reporting that as a failure makes the GUI say *"The upload package was NOT
    built"* about a package that was built, validated and published, which is a
    false statement of exactly the kind finding A-4 is about; absorbing it
    leaves the stray folder. Removing first means the only reachable states are
    "the earlier build is intact and nothing was published" and "the new
    package is published and it is the only one" — and both of those are states
    the screen can describe truthfully.
    """
    if published.exists():
        try:
            _retry_rename(published, superseded)
        except OSError as exc:
            _remove_tree(staging)
            raise PackageSwapError(
                f"The new package was built and validated but {published} "
                f"could not be moved aside to make room for it: {exc}. Nothing "
                f"was published and the package folder still holds the earlier "
                f"build."
            ) from exc

        if not _remove_tree(superseded):
            put_back = False
            try:
                _retry_rename(superseded, published)
                put_back = True
            except OSError:
                put_back = False
            _remove_tree(staging)
            raise PackageSwapError(
                f"The new package was built and validated but the package it "
                f"replaces could not be removed. Nothing was published. "
                + (f"The earlier build is back in place and intact."
                   if put_back else
                   f"The earlier build is now at {superseded} and there is no "
                   f"package at {published.name} — rename that folder back, or "
                   f"build again.")
            )

    try:
        _retry_rename(staging, published)
    except OSError as exc:
        # Nothing to restore: the earlier package is already gone, deliberately,
        # and the set in staging is not published under any name an operator
        # uploads. Both facts are stated rather than one of them implied.
        _remove_tree(staging)
        raise PackageSwapError(
            f"The new package was built and validated but could not be moved "
            f"into {published}: {exc}. Nothing was published and no package "
            f"folder remains — build again."
        ) from exc

    return replace(assembled, root=published, readme=published / README_NAME)


def _assemble_package(
    layout: OutputLayout,
    target: Path,
    *,
    matter_name: str,
    date_range: str,
    document_count: int,
    page_count: int,
    estimate: TokenEstimate | None,
    has_bates: bool,
    id_regime: str,
    lim: ProjectLimits,
    doc_ids: tuple[str, ...] | None,
    scope_statement: str,
    unsupported: int,
) -> UploadPackage:
    """Write and validate a complete package into ``target``.

    Split out of :func:`build_upload_package` so that "assemble" and "publish"
    are separable, and so the ONE caller that decides which directory this
    writes into is the one that also owns cleaning it up. It writes into
    whatever directory it is given and knows nothing about
    ``upload_package/``; the returned :class:`UploadPackage` therefore names
    the staging paths and is re-pointed by :func:`_publish_package`.
    """
    available = _text_files(layout)
    if doc_ids is None:
        selected, missing, subset = available, (), False
        keep: set[str] = set()
    else:
        wanted = {f"{safe_component(d)}.txt": d for d in doc_ids}
        selected = [p for p in available if p.name in wanted]
        found = {p.name for p in selected}
        missing = tuple(sorted(d for n, d in wanted.items() if n not in found))
        subset = len(selected) != len(available)
        # The Doc IDs themselves, NOT the file stems. They are equal today —
        # ``safe_component`` is the identity on a Doc ID by construction — but
        # ``sources.json`` and ``document_index.csv`` are keyed by Doc ID, so a
        # future ID that needed sanitizing would filter both to nothing and
        # produce a package with text files and an empty manifest.
        keep = {wanted[n] for n in found}

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

    # A subset package's manifests are FILTERED or the package is not built.
    # There is deliberately no `filtered is None` branch here any more: the one
    # that existed fell back to copying the whole matter's manifest whenever
    # filtering failed for any reason, which is the failure the filtering exists
    # to prevent, shipped under a sanctioned filename where the §8 content check
    # cannot see it. The filter functions raise instead.
    for src, filterer in (
        (layout.sources_json, _filtered_sources),
        (layout.index_csv, _filtered_index_csv),
    ):
        if not src.is_file():
            continue
        dst = target / src.name
        if subset:
            write_text_deterministic(dst, filterer(layout, keep))
        else:
            shutil.copyfile(src, dst)
        record(dst.name, dst.stat().st_size)

    if estimate is None:
        from dociq.verify.tokens import estimate_tokens

        estimate = estimate_tokens("")

    doc_count = document_count or len(selected)
    scope = scope_statement.strip() or default_scope_statement(
        doc_count, matter_name, unsupported
    )
    # ONE verdict, computed once, against THIS package's limit — then both the
    # README the recipient reads and the `mode_statement` the operator reads are
    # renderings of the same object. Computing it twice from two different
    # limits is how a package came to say "Fits directly in a Claude Project"
    # and "181-197% of capacity - retrieval (RAG) mode" about itself.
    verdict = estimate.capacity(lim.direct_context_tokens)
    readme_text = render_readme(
        matter_name=matter_name,
        document_count=doc_count,
        page_count=page_count,
        date_range=date_range,
        estimate=estimate,
        has_bates=has_bates,
        id_regime=id_regime,
        scope_statement=scope,
        capacity_tokens=lim.direct_context_tokens,
    )
    readme = write_text_deterministic(target / README_NAME, readme_text)
    copied.append(README_NAME)

    # §8's "only the sanctioned files" rule, checked against what is ON DISK
    # rather than against what this function believes it wrote. The two differ
    # exactly when something else put a file there, which is the case worth
    # catching.
    assert_only_sanctioned(target)

    # …and §7's "the manifest is true of the folder" rule, checked the same way.
    # ``assert_only_sanctioned`` polices file NAMES and cannot police contents,
    # so a whole-matter ``sources.json`` inside a two-document package passes it
    # cleanly. This is the check that does not.
    _assert_manifest_matches_folder(
        target, keep if subset else {p.stem for p in selected},
        check_index=subset)

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
