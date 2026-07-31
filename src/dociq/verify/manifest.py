"""The output hash manifest (§4 Stage 6, §7).

The manifest exists to make the byte-identical claim *checkable*, and the only
way to do that honestly is to say which files the claim covers. A manifest
that hashes every file in the output folder would go red on the second run for
reasons that have nothing to do with determinism — ``run_summary.pdf`` embeds
a generation timestamp and ``document_index.xlsx``'s container format embeds a
creation time — and a gate that goes red for a known-benign reason is a gate
people learn to ignore.

So the manifest carries three lists, all explicit:

* ``deterministic`` — the freeze document's named four artifacts. Hashed, and
  asserted byte-identical across runs with the same **run identity** (Principle
  5 as amended by D-04 and A-04). This set, and only this set, feeds
  ``corpus_sha256``.

  The identity is the hashed projection of ``RunConfig``, and the manifest names
  it in full in :data:`IDENTITY_NOTE`. Naming only "folder + profile + master
  index" was Codex review #1 finding B-2: environment-controlled caps, the
  per-file timeout, the retry bounds, the recursion flag and the OCR model bytes
  could all change the evidence from outside a claim that did not mention them,
  so the claim was not checkable.
* ``adjacent`` — the rest of §7's deliverables that *are* mechanically derived
  and reproducible (the reconciliation CSV, the issued-ID ledger, the matter
  profile copy, the Path-A upload package). They are hashed and compared
  between runs, so a break in one is still caught, but they are kept out of
  ``corpus_sha256`` because the frozen claim names four artifacts and widening
  a claim quietly is how a claim stops meaning anything. A file here that
  differs between runs is a real finding.
* ``excluded`` — present in the output, deliberately outside the claim, each
  with the reason it cannot be byte-identical.

``processing_log.json`` appears in both: its ``content`` section is hashed, its
``run`` section (timestamp, operator, host) is not. The manifest records the
content-section hash separately so the split is visible in the artifact rather
than only in the prose.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import CONTRACT_VERSION, DocIQError, canonical_json
from ..runstate import INCOMPLETE_DIR

MANIFEST_NAME = "output_manifest.json"

DETERMINISTIC_PATTERNS = ("clean_text/*.txt", "sources.json",
                          "document_index.csv")
"""Covered by the byte-identical claim, in the order the freeze doc lists them."""

ADJACENT_PATTERNS = ("reconciliation.csv", "doc_ids_issued.json",
                     "profile/*.yaml", "upload_package/*")
"""Reproducible §7/§8 deliverables outside the freeze's named four.

Hashed and compared, but deliberately not part of ``corpus_sha256`` — see the
module docstring. ``upload_package/*`` is copies of files already inside the
claim plus a generated README, so it cannot be the *source* of a break, but a
difference there would mean the copy step is not reproducing what it copied."""

EXCLUDED_REASONS = {
    "run_summary.pdf": "embeds a generation timestamp",
    "document_index.xlsx": "the xlsx container embeds a creation timestamp",
    MANIFEST_NAME: "the manifest cannot hash itself",
}
"""Deliberately outside the claim. Anything not listed here and not matched by
:data:`DETERMINISTIC_PATTERNS` is reported as ``unclassified`` rather than
silently assumed either way — an output nobody decided about is a finding."""

EXCLUDED_PREFIXES = {
    f"{INCOMPLETE_DIR}/": (
        "the record of a run that did NOT complete — it is not a deliverable "
        "of this run and is deliberately outside the byte-identical claim"
    ),
}
"""Whole sub-trees outside the claim, matched by relative-path prefix.

``incomplete_run/`` (Codex review #1, finding B-1) is written by a blocked or
cancelled run, which publishes nothing else. It is classified here so that a
LATER, complete run into the same folder does not report it as an unclassified
output and fail its own gate on the wreckage of an earlier attempt — and a
complete run also purges it (``pipeline._STALE_PATTERNS``), so both halves of
that interaction are covered."""

LOG_NAME = "processing_log.json"
LOG_CONTENT_KEY = "content"
LOG_RUN_KEY = "run"

CLAIM = (
    "byte-identical across runs with the same run identity — for the files "
    f"under 'deterministic' and for the '{LOG_CONTENT_KEY}' section of "
    f"{LOG_NAME} only"
)

IDENTITY_NOTE = (
    "The run identity is the hashed projection of RunConfig: source folder, "
    "output folder, profile id and version, master-index snapshot "
    "(filename, sha256, row count), OCR confidence threshold, OCR engine and "
    "engine version, confirmed Bates pattern, and RunConfig.limits — the "
    "XLSX/CSV row caps, the ZIP size/member/depth caps, the per-file timeout, "
    "the retry maximum and retry budget, whether the walk recursed, and the OCR "
    "model identity (package version plus a hash of the model files). "
    "Thread-pool width is recorded in limits.workers and deliberately EXCLUDED "
    "from the hash: pool width must not change output, and treating it as an "
    "input would hide a determinism defect rather than expose one. Run "
    "timestamp, operator and host are outside the hash by design, so a rerun at "
    "a different time still proves byte-identical content."
)
"""What the claim actually covers, named in full.

Codex review #1 finding B-2: the claim used to name "the same source folder,
profile and master index", while caps, timeouts, retry bounds and the OCR model
bytes could all change the evidence from outside it. A claim that does not name
its own identity is not checkable, which is the one thing this manifest exists
to be."""


@dataclass
class Manifest:
    contract_version: str = CONTRACT_VERSION
    deterministic: dict[str, str] = field(default_factory=dict)
    """``{relative path: sha256}`` — the claim's subject."""
    adjacent: dict[str, str] = field(default_factory=dict)
    """``{relative path: sha256}`` — reproducible, compared, outside the claim."""
    log_content_sha256: str | None = None
    excluded: dict[str, str] = field(default_factory=dict)
    """``{relative path: reason it is outside the claim}``."""
    unclassified: list[str] = field(default_factory=list)

    @property
    def corpus_sha256(self) -> str:
        """One hash over the whole deterministic set — the number to compare
        between two runs. Computed over the canonical JSON of the sorted
        per-file hashes, so a file appearing or vanishing changes it too."""
        payload = canonical_json({
            "contract_version": self.contract_version,
            "files": dict(sorted(self.deterministic.items())),
            "log_content_sha256": self.log_content_sha256 or "",
        })
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_jsonable(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "claim": CLAIM,
            "claim_identity": IDENTITY_NOTE,
            "corpus_sha256": self.corpus_sha256,
            "deterministic": dict(sorted(self.deterministic.items())),
            "adjacent": dict(sorted(self.adjacent.items())),
            "adjacent_note": (
                "reproducible deliverables compared between runs but outside "
                "the frozen four-artifact claim, so they do not feed "
                "corpus_sha256"
            ),
            "log_content_sha256": self.log_content_sha256,
            "excluded": dict(sorted(self.excluded.items())),
            "unclassified": sorted(self.unclassified),
        }

    def render(self) -> str:
        lines = [f"corpus hash {self.corpus_sha256[:16]}… over "
                 f"{len(self.deterministic)} deterministic file(s), "
                 f"{len(self.adjacent)} adjacent file(s) compared separately"]
        if self.log_content_sha256:
            lines.append(f"  {LOG_NAME}[{LOG_CONTENT_KEY}] "
                         f"{self.log_content_sha256[:16]}…")
        for name, why in sorted(self.excluded.items()):
            lines.append(f"  excluded: {name} — {why}")
        for name in sorted(self.unclassified):
            lines.append(f"  UNCLASSIFIED: {name} — not covered by the claim "
                         "and not declared excluded")
        return "\n".join(lines)


def sha256_file(path: Path) -> str:
    """Hash a file in bounded memory. Public because the processing log records
    the same hashes the manifest does, and two implementations of "the hash of
    this file" would eventually disagree."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_sha256_file = sha256_file


def _log_content_hash(path: Path) -> str | None:
    """Hash of the log's ``content`` section, or ``None`` if unreadable.

    Hashed from the parsed structure through the contract's canonical
    serializer rather than from the raw bytes: the claim is about the content,
    and re-serializing through one function is what keeps this hash and the
    log writer's own from ever disagreeing.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if LOG_CONTENT_KEY not in data:
        return None
    return hashlib.sha256(
        canonical_json(data[LOG_CONTENT_KEY]).encode("utf-8")).hexdigest()


class EmptyOutputError(DocIQError):
    """The output root holds none of the artifacts the claim is about.

    An empty manifest still produces a perfectly stable ``corpus_sha256``, so
    two runs that produced *nothing* compare byte-identical and the determinism
    gate goes green on an empty folder. A gate that passes when the pipeline
    did no work is worse than no gate, so this is a hard failure rather than
    an empty result.
    """


def build(output_root: Path, *, require_outputs: bool = True) -> Manifest:
    """Hash the outputs present under ``output_root``.

    Args:
        require_outputs: raise :class:`EmptyOutputError` when nothing the claim
            covers is present. Off only for tests that build a manifest of a
            deliberately empty tree.
    """
    output_root = Path(output_root)
    if require_outputs and not output_root.is_dir():
        raise EmptyOutputError(f"output root does not exist: {output_root}")
    man = Manifest()

    covered: set[Path] = set()
    for pattern in DETERMINISTIC_PATTERNS:
        for p in sorted(output_root.glob(pattern)):
            if p.is_file():
                covered.add(p)
                man.deterministic[p.relative_to(output_root).as_posix()] = \
                    _sha256_file(p)

    for pattern in ADJACENT_PATTERNS:
        for p in sorted(output_root.glob(pattern)):
            if p.is_file() and p not in covered:
                covered.add(p)
                man.adjacent[p.relative_to(output_root).as_posix()] = \
                    _sha256_file(p)

    log = output_root / LOG_NAME
    if log.is_file():
        covered.add(log)
        man.log_content_sha256 = _log_content_hash(log)
        man.excluded[LOG_NAME] = (
            f"the '{LOG_RUN_KEY}' section carries the run timestamp, operator "
            f"and host; only the '{LOG_CONTENT_KEY}' section is hashed")

    for p in sorted(output_root.rglob("*")):
        if not p.is_file() or p in covered:
            continue
        rel = p.relative_to(output_root).as_posix()
        if rel.startswith(".dociq/"):  # run scratch, not a deliverable
            continue
        if rel in EXCLUDED_REASONS:
            man.excluded[rel] = EXCLUDED_REASONS[rel]
            continue
        prefix = next((p for p in EXCLUDED_PREFIXES if rel.startswith(p)), None)
        if prefix is not None:
            man.excluded[rel] = EXCLUDED_PREFIXES[prefix]
        else:
            man.unclassified.append(rel)

    if require_outputs and not man.deterministic:
        raise EmptyOutputError(
            f"{output_root} holds none of {DETERMINISTIC_PATTERNS} — the "
            "byte-identical claim has no subject, and comparing two empty "
            "manifests would pass trivially")
    return man


def write(output_root: Path, man: Manifest | None = None) -> Path:
    """Write the manifest. Returns the path.

    ``man`` is accepted so a caller that already built one does not hash every
    deliverable a second time — on a 17,732-page corpus that is not a rounding
    error — and, more importantly, so the manifest that is written is provably
    the same object the caller asserted against.
    """
    output_root = Path(output_root)
    man = man if man is not None else build(output_root)
    path = output_root / MANIFEST_NAME
    path.write_text(
        json.dumps(man.to_jsonable(), indent=2, sort_keys=True,
                   ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    return path


def compare(a: Manifest, b: Manifest) -> list[str]:
    """Differences between two runs' manifests, restricted to the claim.

    An empty list is the byte-identical result. The excluded set is not
    compared: it is excluded precisely because comparing it proves nothing.
    """
    diffs: list[str] = []
    if a.log_content_sha256 != b.log_content_sha256:
        diffs.append(f"{LOG_NAME}[{LOG_CONTENT_KEY}]: "
                     f"{a.log_content_sha256} != {b.log_content_sha256}")
    for name in sorted(set(a.deterministic) | set(b.deterministic)):
        ha, hb = a.deterministic.get(name), b.deterministic.get(name)
        if ha is None:
            diffs.append(f"{name}: missing from the first run")
        elif hb is None:
            diffs.append(f"{name}: missing from the second run")
        elif ha != hb:
            diffs.append(f"{name}: {ha[:16]}… != {hb[:16]}…")
    for name in sorted(set(a.adjacent) | set(b.adjacent)):
        ha, hb = a.adjacent.get(name), b.adjacent.get(name)
        if ha is None or hb is None:
            diffs.append(f"{name}: present in only one run (outside the "
                         "four-artifact claim, still a finding)")
        elif ha != hb:
            diffs.append(f"{name}: {ha[:16]}… != {hb[:16]}… (outside the "
                         "four-artifact claim, still a finding)")
    for name in sorted(set(a.unclassified) ^ set(b.unclassified)):
        diffs.append(f"{name}: unclassified output present in only one run")
    return diffs
