"""End-to-end self-test for the Track A ingestion spine. Exit 0 is the gate.

    python -m dociq.selftest [--runs N] [--keep]

It builds the synthetic fixture corpus, runs the full walk → extract → page
model → probe emit → verify path over it, and asserts the things that would
otherwise be discovered in a client matter:

1. every fixture format produced the pages it should have, including the mixed
   native+scanned PDF and the genuinely blank page;
2. no ``PageRecord.text`` contains a page marker (the freeze's one absolute);
3. normalization is idempotent on every page that came out of the corpus;
4. the §4 Stage-6 accounting gate reconciles to zero discrepancy;
5. OCR ran from bundled models with no network call available;
6. the outputs are byte-identical across repeated runs with varied hash seeds.

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

from .contracts import PageKind, ProcessingStatus, RunConfig
from .ingest import extract as ex
from .ingest import walker
from .ingest.pagemodel import normalize
from .verify import accounting, determinism, manifest, probe_emit

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
    socket.socket = _no_socket  # type: ignore[assignment]
    try:
        from PIL import Image, ImageDraw
        import numpy as np

        img = Image.new("L", (600, 120), 255)
        ImageDraw.Draw(img).text((10, 40), "OFFLINE OCR CHECK 2024", fill=0)
        arr = np.repeat(np.array(img)[:, :, None], 3, axis=2)
        text, confs = ex._ocr_array(arr)
        chk.expect(bool(confs), "OCR ran with sockets disabled",
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
        result = walker.run(cfg, walker.WalkOptions(resume=False))

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

        print("\nStage 6 — accounting and manifest")
        probe_emit.write(result)
        report = accounting.check(result)
        chk.expect(report.ok, "page accounting reconciles to zero discrepancy",
                   report.render().splitlines()[0])
        if not report.ok:
            for d in report.discrepancies:
                print(f"        {d}")
        man = manifest.build(out)
        chk.expect(not man.unclassified, "every output is classified by the "
                   "byte-identical claim", man.render().splitlines()[0])
        chk.expect(man.log_content_sha256 is not None,
                   "the log's content section is hashed separately from its run "
                   "section")

        print("\nPrinciple 5 — determinism")
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
