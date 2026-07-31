"""Repeat-run determinism probe (Principle 5, acceptance criterion 7).

One green run proves nothing, so this runs the pipeline N times into N fresh
output roots and compares the manifests. Anything the caller marks as
ordering-, timing- or hash-seed-sensitive gets the long run count and a varied
``PYTHONHASHSEED`` per repetition — a dict-ordering bug is invisible under a
single seed by construction.

Each repetition runs :func:`dociq.pipeline.run` — the shipped orchestration,
writing the shipped emitters. Sprint 1 briefly proved this against
``verify/probe_emit.py``, a stand-in for an emit layer that lived in another
worktree; that proof was about the stand-in, which is a materially weaker claim
than the one the gate needs, and the stand-in is deleted.

Runs are executed in a subprocess when the seed must vary, because
``PYTHONHASHSEED`` is read once at interpreter start: setting it in-process and
declaring the seed varied would be a probe that cannot fail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import manifest as mf


@dataclass
class DeterminismReport:
    runs: int = 0
    seeds: list[str] = field(default_factory=list)
    corpus_hashes: list[str] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diffs and not self.failures and self.runs > 1 and \
            len(set(self.corpus_hashes)) == 1

    def render(self) -> str:
        head = (f"{self.runs} run(s), seeds {sorted(set(self.seeds))}, "
                f"{len(set(self.corpus_hashes))} distinct corpus hash(es)")
        if self.ok:
            return (f"DETERMINISM OK — {head}\n  corpus_sha256 "
                    f"{self.corpus_hashes[0]}")
        lines = [f"DETERMINISM FAILED — {head}"]
        lines.extend(f"  run error: {f}" for f in self.failures)
        lines.extend(f"  {d}" for d in self.diffs)
        lines.extend(f"  hash[{i}] = {h}"
                     for i, h in enumerate(self.corpus_hashes))
        return "\n".join(lines)


_RUNNER = """\
import sys
from dociq.contracts import RunConfig
from dociq.ingest import extract as ex, walker
from dociq import pipeline
from dociq.profiles.model import OperatorStamp

src, out = sys.argv[1], sys.argv[2]
cfg = RunConfig(source_root=src, output_root=out,
                ocr_engine_version=ex.ocr_engine_version())
# A FIXED operator stamp. The stamp reaches only the log's `run` section, the
# summary PDF and the profile copy — none of which are inside the claim — but
# pinning it means a diff anywhere in the deterministic set is unambiguously a
# determinism defect rather than "the clock moved".
stamp = OperatorStamp("determinism-probe", "2026-07-30T00:00:00Z", "probe")
pipeline.run(cfg, pipeline.PipelineOptions(
    walk=walker.WalkOptions(resume=False),
    matter_name="determinism probe",
    stamp=stamp,
))
"""
"""The subprocess body. It runs the REAL pipeline — Track B's emit layer, not a
stand-in — because the byte-identical claim is about the files DocIQ ships. A
proof over a probe emitter proves the probe."""


def _one_run(source_root: Path, out: Path, seed: str) -> str | None:
    """Run the pipeline in a subprocess. Returns an error string or ``None``."""
    env = dict(os.environ, PYTHONHASHSEED=seed,
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    proc = subprocess.run([sys.executable, "-c", _RUNNER, str(source_root),
                           str(out)], env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "unknown failure")[-800:]
    return None


def prove(source_root: Path, *, runs: int = 8,
          seeds: list[str] | None = None,
          workdir: Path | None = None) -> DeterminismReport:
    """Run the pipeline ``runs`` times and compare the deterministic outputs.

    ``seeds`` defaults to a rotation of distinct ``PYTHONHASHSEED`` values, so
    even the short 8-run proof varies the seed rather than repeating one.
    """
    seeds = seeds or [str(1 + (i * 7919) % 4294967295) for i in range(runs)]
    rep = DeterminismReport(runs=runs)
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="dociq-det-"))
    base.mkdir(parents=True, exist_ok=True)

    manifests: list[mf.Manifest] = []
    for i in range(runs):
        seed = seeds[i % len(seeds)]
        rep.seeds.append(seed)
        out = base / f"run{i:02d}"
        err = _one_run(Path(source_root), out, seed)
        if err:
            rep.failures.append(f"run {i} (seed {seed}): {err}")
            continue
        try:
            man = mf.build(out)
        except mf.EmptyOutputError as exc:
            # A run that produced nothing must not be compared as if it had:
            # two empty manifests are byte-identical to each other.
            rep.failures.append(f"run {i} (seed {seed}): {exc}")
            continue
        manifests.append(man)
        rep.corpus_hashes.append(man.corpus_sha256)
        if man.unclassified:
            rep.diffs.append(f"run {i}: unclassified outputs "
                             f"{sorted(man.unclassified)}")

    if len(manifests) < runs:
        rep.diffs.append(f"only {len(manifests)} of {runs} runs produced "
                         "comparable output")
    for i in range(1, len(manifests)):
        for d in mf.compare(manifests[0], manifests[i]):
            rep.diffs.append(f"run 0 vs run {i}: {d}")
    return rep


def prove_json(report: DeterminismReport) -> str:
    return json.dumps({"runs": report.runs, "seeds": report.seeds,
                       "ok": report.ok, "corpus_hashes": report.corpus_hashes,
                       "diffs": report.diffs, "failures": report.failures},
                      indent=2)
