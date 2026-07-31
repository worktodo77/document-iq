"""End-to-end self-test for the whole DocIQ pipeline. Exit 0 is the gate.

    python -m dociq.selftest [--runs N] [--keep]

It builds the synthetic fixture corpus, runs §4's six stages over it through
:func:`dociq.pipeline.run` — the shipped orchestration, writing the shipped
emitters — and asserts the things that would otherwise be discovered in a
client matter:

1. every fixture format produced the pages it should have, including the mixed
   native+scanned PDF and the genuinely blank page;
2. no ``PageRecord.text`` contains a page marker (the freeze's one absolute);
3. normalization is idempotent on every page that came out of the corpus;
4. the §4 Stage-6 accounting gate reconciles to zero discrepancy;
5. OCR ran from bundled models with no network call available;
6. the outputs are byte-identical across repeated runs with varied hash seeds;
7. every §7 deliverable is produced, container members carry a remapped
   ``parent_doc_id``, and the amended ``RunResult`` fields are populated.

Output is deliberately verbose about what passed. A gate whose green output is
one word is a gate nobody can debug when it goes red.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
import tempfile
from pathlib import Path

from . import pipeline
from .contracts import PageKind, RunConfig
from .ingest import extract as ex
from .ingest import walker
from .ingest.pagemodel import normalize
from .profiles.model import OperatorStamp
from .verify import determinism

MARKER_FRAGMENT = "===== PAGE"

_EXPECTED = {
    "01_native_report.pdf": (2, {PageKind.NATIVE}),
    "02_scanned_instruction.pdf": (2, {PageKind.OCR}),
    "03_mixed_transmittal.pdf": (3, {PageKind.NATIVE, PageKind.OCR}),
    "04_empty_page.pdf": (3, {PageKind.NATIVE, PageKind.EMPTY}),
    "05_letter.docx": (1, {PageKind.SYNTHETIC}),
    "06_register.xlsx": (2, {PageKind.SYNTHETIC}),
    "07_ncr_log.csv": (1, {PageKind.SYNTHETIC}),
    "08_daily_log.txt": (1, {PageKind.SYNTHETIC}),
    "09_notice.eml": (1, {PageKind.SYNTHETIC}),
    "14_transmittal.eml": (1, {PageKind.SYNTHETIC}),
    "14_transmittal.eml/attached_report.pdf": (2, {PageKind.NATIVE}),
}


class _Check:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.lines: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        self.lines.append(f"  PASS  {label}" + (f" — {detail}" if detail else ""))

    def fail(self, label: str, detail: str) -> None:
        self.failures.append(f"{label}: {detail}")
        self.lines.append(f"  FAIL  {label} — {detail}")

    def expect(self, cond: bool, label: str, detail: str = "") -> bool:
        if cond:
            self.ok(label, detail)
        else:
            self.fail(label, detail or "condition was false")
        return cond


def _fixture_root(work: Path) -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "fixtures"))
    import make_fixtures

    return make_fixtures.build(work / "fixtures")


def _check_no_network(chk: _Check) -> None:
    """Principle 4. Prove the OCR path needs no socket by taking sockets away.

    ``socket.socket`` is replaced with a raiser for the duration, so any
    outbound attempt — the model download the vendored code used to permit —
    fails loudly instead of quietly succeeding on a connected machine.

    Critically, the shared ``_OCR_ENGINE`` singleton is torn down first. By
    the time this runs, Stage 1-2 has already OCR'd the fixture corpus and
    left a warm engine behind; probing through the warm singleton would only
    prove that *inference* needs no socket; the historical hazard this
    guards against (``enable_os_trust()``) lived in *construction* — a
    cold-cache model load — which a warm-engine probe never exercises. Forcing
    a fresh ``RapidOCR(...)`` build under the blocked socket is the only way
    this check is honest about what it claims.
    """
    real = socket.socket

    class _Blocked(Exception):
        pass

    def _no_socket(*a, **k):
        raise _Blocked("network access attempted during OCR")

    from .ingest.extract import ocr_models_present

    ok, msg = ocr_models_present()
    if not chk.expect(ok, "OCR models present locally", msg or str(ex.ocr_model_dir())):
        return
    was_warm = ex._OCR_ENGINE is not None
    ex._OCR_ENGINE = None  # force a genuine cold construction under the block
    socket.socket = _no_socket  # type: ignore[assignment]
    try:
        from PIL import Image, ImageDraw
        import numpy as np

        img = Image.new("L", (600, 120), 255)
        ImageDraw.Draw(img).text((10, 40), "OFFLINE OCR CHECK 2024", fill=0)
        arr = np.repeat(np.array(img)[:, :, None], 3, axis=2)
        text, confs = ex._ocr_array(arr)
        chk.expect(bool(confs),
                   "OCR ran with sockets disabled, including cold engine "
                   "construction" + (" (previously warm; reset for this "
                   "check)" if was_warm else ""),
                   f"{len(confs)} line(s), text {text[:40]!r}")
    except _Blocked as exc:
        chk.fail("OCR ran with sockets disabled", str(exc))
    except Exception as exc:  # pragma: no cover — engine-level failure
        chk.fail("OCR ran with sockets disabled", f"{type(exc).__name__}: {exc}")
    finally:
        socket.socket = real  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dociq.selftest")
    ap.add_argument("--runs", type=int, default=8,
                    help="determinism repetitions (default 8)")
    ap.add_argument("--keep", action="store_true", help="keep the work directory")
    args = ap.parse_args(argv)

    chk = _Check()
    work = Path(tempfile.mkdtemp(prefix="dociq-selftest-"))
    print(f"LI Document IQ — Track A self-test\n  work dir: {work}")
    try:
        src = _fixture_root(work)
        out = work / "out"
        cfg = RunConfig(source_root=str(src), output_root=str(out),
                        ocr_engine_version=ex.ocr_engine_version())
        outcome = pipeline.run(cfg, pipeline.PipelineOptions(
            walk=walker.WalkOptions(resume=False),
            matter_name="DocIQ self-test corpus",
            stamp=OperatorStamp("selftest", "2026-07-30T00:00:00Z", "selftest")))
        result = outcome.result

        print("\nStage 1-2 — walk and extract")
        by_path = {d.rel_path: d for d in result.documents}
        for name, (n_pages, kinds) in _EXPECTED.items():
            doc = by_path.get(name)
            if doc is None:
                chk.fail(f"{name} extracted", "not in the run")
                continue
            got_kinds = {p.kind for p in doc.pages}
            chk.expect(doc.pages_in == n_pages and got_kinds == kinds,
                       f"{name}",
                       f"{doc.pages_in} page(s), kinds "
                       f"{sorted(k.value for k in got_kinds)}")

        chk.expect(any(d.ext == ".doc" for d in result.unsupported),
                   "Tier-2 .doc inventoried, not extracted",
                   f"{len(result.unsupported)} unsupported file(s)")
        zip_children = [d for d in result.documents if d.parent_doc_id]
        chk.expect(len(zip_children) >= 3, "nested ZIP expanded to children",
                   f"{len(zip_children)} child document(s)")
        chk.expect(all(d.container_order is not None for d in zip_children),
                   "every archive child carries a container_order")
        # Stage 1 names the parent by rel_path; Stage 3b must have replaced that
        # with the parent's assigned Doc ID, or the index ships a "Parent doc"
        # column that resolves to nothing.
        ids = {d.doc_id for d in result.documents}
        chk.expect(bool(zip_children)
                   and all(d.parent_doc_id in ids for d in zip_children),
                   "every container member's parent_doc_id was remapped to a "
                   "Doc ID that is in the run",
                   ", ".join(sorted({d.parent_doc_id or "" for d in zip_children})))
        chk.expect(any("recovered via" in n for d in result.documents
                       for n in d.notes),
                   "misnamed file recovered by content sniffing")
        chk.expect(any("duplicate content" in w for w in result.warnings),
                   "duplicate-by-hash detected")

        print("\nContract invariants")
        pages = [(d.rel_path, p) for d in result.documents for p in d.pages]
        bad_marker = [r for r, p in pages if MARKER_FRAGMENT in p.text]
        chk.expect(not bad_marker, "no page marker inside PageRecord.text",
                   f"{len(pages)} page(s) checked")
        not_idem = [f"{r} p{p.page_no}" for r, p in pages
                    if normalize(p.text) != p.text]
        chk.expect(not not_idem, "normalization is idempotent on every page",
                   f"{len(pages)} page(s) checked")
        gapless = all([p.page_no for p in d.pages] == list(range(1, d.pages_in + 1))
                      for d in result.documents)
        chk.expect(gapless, "page numbering is gapless 1..N in every document")
        ocr_pages = [p for _, p in pages if p.kind is PageKind.OCR]
        chk.expect(all(p.ocr_conf is not None and p.ocr_line_count > 0
                       for p in ocr_pages),
                   "every OCR page carries a confidence and a line count",
                   f"{len(ocr_pages)} OCR page(s), mean conf "
                   f"{sum(p.ocr_conf or 0 for p in ocr_pages) / max(1, len(ocr_pages)):.4f}")
        chk.expect(all(round(p.ocr_conf or 0, 4) == p.ocr_conf for p in ocr_pages),
                   "ocr_conf is rounded to 4dp as the contract requires")

        print("\nPrinciple 4 — no network")
        _check_no_network(chk)

        print("\nStage 5 — the §7 deliverables")
        lay = outcome.layout
        for label, path in (("clean_text/", lay.clean_text),
                            ("sources.json", lay.sources_json),
                            ("document_index.csv", lay.index_csv),
                            ("document_index.xlsx", lay.index_xlsx),
                            ("processing_log.json", lay.processing_log),
                            ("run_summary.pdf", lay.run_summary),
                            ("upload_package/", lay.upload_package),
                            ("doc_ids_issued.json", lay.issued_ids)):
            chk.expect(path.exists(), f"{label} written")
        n_text = len(list(lay.clean_text.glob("*.txt")))
        chk.expect(n_text == len(result.documents),
                   "one clean_text file per document",
                   f"{n_text} file(s), {len(result.documents)} document(s)")
        markers = sum(1 for p in lay.clean_text.glob("*.txt")
                      for line in p.read_text(encoding="utf-8").splitlines()
                      if line.startswith(MARKER_FRAGMENT))
        chk.expect(markers == result.pages_kept,
                   "one page marker per kept page, rendered only at emit",
                   f"{markers} marker(s), {result.pages_kept} kept page(s)")

        print("\nAmended contract fields (A-01, A-02, A-03, A-04)")
        before, after = result.tokens_before, result.tokens_after
        chk.expect(before is not None and after is not None,
                   "RunResult carries the before/after token estimates")
        if before is not None and after is not None:
            chk.expect(before.chars > 0 and before.provenance != "",
                       "the token estimate carries a measured character count "
                       "and a provenance",
                       f"{before.chars} chars")
            chk.expect("PROXY, NOT A TOKENIZER MEASUREMENT" in before.provenance,
                       "the provenance states plainly that no tokenizer was run")
            # Codex review #1 finding B-6: the pre-token count is not a bound
            # for any tokenizer but DocIQ's own regex, so the contract field
            # that means "hard lower bound" must stay empty and the assumptions
            # must travel with the figure instead.
            chk.expect(before.floor_tokens == 0,
                       "no hard token floor is claimed (finding B-6)")
            chk.expect(all(a in before.provenance
                           for a in ("ASSUMPTION A1", "ASSUMPTION A2",
                                     "ASSUMPTION A3")),
                       "every assumption travels with the token figure")
            chk.expect("METHOD FOR THIS FIGURE" in before.provenance,
                       "the provenance names the method this run used")
        limits = result.config.limits
        chk.expect(limits is not None and limits.zip_max_depth > 0
                   and limits.retry_max > 0 and limits.file_timeout_s > 0,
                   "the run identity records its effective limits (A-04)",
                   f"zip depth {limits.zip_max_depth if limits else '-'}, "
                   f"retry max {limits.retry_max if limits else '-'}")
        chk.expect(limits is not None and limits.ocr_model_id != "",
                   "the OCR model identity is recorded, models and all",
                   (limits.ocr_model_id if limits else ""))
        chk.expect(result.reconciliation is None,
                   "reconciliation is None when no master index was supplied — "
                   "not an empty report")

        print("\nStage 6 — accounting and manifest")
        report = outcome.accounting
        chk.expect(report.ok, "page accounting reconciles to zero discrepancy",
                   report.render().splitlines()[0])
        if not report.ok:
            for d in report.discrepancies:
                print(f"        {d}")
        man = outcome.manifest
        chk.expect(not man.unclassified, "every output is classified by the "
                   "byte-identical claim", man.render().splitlines()[0])
        for name in sorted(man.unclassified):
            print(f"        UNCLASSIFIED {name}")
        chk.expect(man.log_content_sha256 is not None,
                   "the log's content section is hashed separately from its run "
                   "section")
        chk.expect(len(man.deterministic) == n_text + 2,
                   "the claim covers clean_text/*, sources.json and "
                   "document_index.csv and nothing else",
                   f"{len(man.deterministic)} file(s) in the claim, "
                   f"{len(man.adjacent)} adjacent")
        chk.expect(set(man.excluded) >= {"run_summary.pdf",
                                         "document_index.xlsx",
                                         "processing_log.json"},
                   "run_summary.pdf and document_index.xlsx are declared "
                   "outside the claim, with reasons",
                   "; ".join(f"{k}" for k in sorted(man.excluded)))

        print("\nPrinciple 5 — determinism (over the REAL emit layer)")
        det = determinism.prove(src, runs=args.runs, workdir=work / "det")
        chk.expect(det.ok, f"outputs byte-identical over {args.runs} runs",
                   det.render().splitlines()[0])
        if not det.ok:
            print(det.render())

        print("\n" + "\n".join(chk.lines))
        n = len(chk.lines)
        if chk.failures:
            print(f"\nSELFTEST FAILED — {len(chk.failures)} of {n} check(s) failed")
            for f in chk.failures:
                print(f"  - {f}")
            return 1
        print(f"\nSELFTEST PASSED — {n} check(s), "
              f"{result.pages_in} page(s) across {len(result.documents)} "
              f"document(s), {len(result.unsupported)} inventoried")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"\nwork dir kept: {work}")


if __name__ == "__main__":  # pragma: no cover — process entry point
    raise SystemExit(main())
