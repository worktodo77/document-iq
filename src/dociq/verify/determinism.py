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

WHAT CRITERION 7'S PROOF ACTUALLY COVERS — read this before quoting it
The claim this module supports is narrower than "DocIQ is deterministic", and
the boundary is worth stating precisely rather than discovering later.

**Covered.**

* The **fixture corpus**, whose OCR pages are synthetic and whose text layer is
  authored — every byte of every deliverable, over ``runs`` repetitions with a
  varied ``PYTHONHASHSEED`` each, compared through :mod:`dociq.verify.manifest`.
* **Concurrency**, when the caller asks for it: ``prove(..., concurrency=N)``
  runs the repetitions simultaneously, so the pipeline meets the regime the
  2026-08-02 acceptance run documented as behaving differently — 2 per-file
  timeouts on an idle machine and 6 on a contended one. Sequential repetitions
  cannot see a contention-dependent difference, and until this parameter existed
  every repetition was sequential.

**NOT covered, and none of these is closed by a green result here.**

1. **The real corpus.** Two full OCR-on runs over the real 368-document record
   did produce one ``corpus_sha256`` (Sprint-1 integration note §6a), and that
   is a separate, stronger, and *unrepeated* observation. It is not this probe.
2. **An OCR page that reads differently on a second successful pass.**
   ``ingest.extract``'s ``TRANSIENT_MARKERS`` / ``_retry_degraded`` fire on
   outright *failure* only; a page that OCR'd "successfully" twice and returned
   different text is invisible to them, and would surface here only if the
   fixture corpus happened to contain such a page.
   ``tests/test_ocr_ordering.py`` probes the engine's own stability directly —
   sequentially, and now concurrently — which is where that claim lives.
3. **ONNX reduction order.** rapidocr constructs its ``SessionOptions``
   internally and DocIQ cannot reach ``intra_op_num_threads``, so the thread
   count is whatever onnxruntime picks from the machine. It is stable *on one
   machine* and is **not pinned**, so byte-identity is not asserted across
   machines with different core counts. Pinning it would mean patching a
   third-party library's session construction, which was considered and rejected
   for a claim-accuracy change: a silent no-op on the next rapidocr release is
   worse than a disclosed gap.
4. **The frozen build's own repetition path** beyond what
   :data:`DETERMINISM_RUN_FLAG` covers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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

    concurrency: int = 1
    """How many repetitions ran at once. Recorded because it is part of what the
    result means: ``concurrency=1`` is a proof over the sequential regime only,
    and the regime the acceptance run flagged as different is the other one."""

    @property
    def ok(self) -> bool:
        return not self.diffs and not self.failures and self.runs > 1 and \
            len(set(self.corpus_hashes)) == 1

    def render(self) -> str:
        regime = ("sequential" if self.concurrency <= 1
                  else f"{self.concurrency} at a time — CONTENDED")
        head = (f"{self.runs} run(s) {regime}, seeds {sorted(set(self.seeds))}, "
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


DETERMINISM_RUN_FLAG = "--determinism-run"
"""How a FROZEN build re-enters itself for one determinism repetition.

``sys.executable -c <source>`` is the interpreter contract, and a PyInstaller
build has no interpreter on the command line: ``sys.executable`` is
``DocumentIQ.exe``, which does not accept ``-c`` and would take the runner
source as a positional argument. Unfixed, the determinism proof either fails or
— worse — silently runs the GUI eight times and compares eight empty output
directories, which reconcile perfectly.

So the frozen build re-invokes itself with this flag and the launcher executes
:data:`_RUNNER`. One runner source, two ways in.
"""


def _one_run(source_root: Path, out: Path, seed: str) -> str | None:
    """Run the pipeline in a subprocess. Returns an error string or ``None``."""
    env = dict(os.environ, PYTHONHASHSEED=seed)
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, DETERMINISM_RUN_FLAG, str(source_root), str(out)]
    else:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        cmd = [sys.executable, "-c", _RUNNER, str(source_root), str(out)]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "unknown failure")[-800:]
    return None


def prove(source_root: Path, *, runs: int = 8,
          seeds: list[str] | None = None,
          workdir: Path | None = None,
          concurrency: int = 1) -> DeterminismReport:
    """Run the pipeline ``runs`` times and compare the deterministic outputs.

    ``seeds`` defaults to a rotation of distinct ``PYTHONHASHSEED`` values, so
    even the short 8-run proof varies the seed rather than repeating one.

    ``concurrency`` is how many repetitions run **at the same time**, and it
    exists because sequential repetitions cannot see a contention-dependent
    difference. The 2026-08-02 acceptance run is the evidence that the two
    regimes are not the same regime: the shipped per-file timeout was crossed by
    2 documents on an idle machine and by 6 on a contended one, all six
    recovered in full by the serial re-read. That is a measured behavioral
    difference under load, in the one regime criterion 7's proof did not
    exercise. ``concurrency=1`` keeps the historical behavior, and the value is
    carried into :attr:`DeterminismReport.concurrency` so a report cannot be
    quoted without saying which regime produced it.

    Repetitions remain in separate subprocesses with separate output roots at
    any concurrency, so running them together adds contention without adding
    shared state — the parallelism is the variable under test, not a new source
    of interference between repetitions.
    """
    seeds = seeds or [str(1 + (i * 7919) % 4294967295) for i in range(runs)]
    concurrency = max(1, min(concurrency, runs))
    rep = DeterminismReport(runs=runs, concurrency=concurrency)
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="dociq-det-"))
    base.mkdir(parents=True, exist_ok=True)

    for i in range(runs):
        rep.seeds.append(seeds[i % len(seeds)])

    def _do(i: int) -> tuple[int, str | None]:
        return i, _one_run(Path(source_root), base / f"run{i:02d}",
                           seeds[i % len(seeds)])

    if concurrency == 1:
        results = [_do(i) for i in range(runs)]
    else:
        # Threads, not processes: ``_one_run`` is already a subprocess and
        # spends its whole life in ``subprocess.run``, so a thread pool is
        # enough to make the children overlap — which is the point.
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = sorted(pool.map(_do, range(runs)))

    manifests: list[mf.Manifest] = []
    for i, err in results:
        seed = seeds[i % len(seeds)]
        out = base / f"run{i:02d}"
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
    return json.dumps({"runs": report.runs, "concurrency": report.concurrency,
                       "seeds": report.seeds,
                       "ok": report.ok, "corpus_hashes": report.corpus_hashes,
                       "diffs": report.diffs, "failures": report.failures},
                      indent=2)
