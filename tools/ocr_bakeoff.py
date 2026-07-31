"""D-01 OCR bake-off harness (acceptance criterion 9). Developer tool.

Rasterizes a sample of genuinely scanned pages from a real corpus, runs every
OCR engine that is actually available on this machine, and writes the material
Alex hand-checks: the page images, the recognized text, a per-page measurement
table, and a side-by-side HTML sheet.

It does NOT score accuracy. Ground truth is a human reading the page; a harness
that invented one would be measuring itself. What it produces is the
measurable characterization — confidence distribution, timing, character
counts, and the pages where the engine visibly struggled — plus the artifacts
that make the hand-check cheap.

**Client data.** Everything it writes goes to the directory passed on the
command line, which must be OUTSIDE the repository. Only summary numbers reach
``docs/bakeoff/``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dociq.ingest import extract as ex  # noqa: E402

_NATIVE_FLOOR = ex._NATIVE_TEXT_FLOOR


def scanned_page_indices(pdf: Path) -> list[int]:
    """0-based indices of pages with no usable native text layer."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    return [i for i, p in enumerate(reader.pages)
            if len((p.extract_text() or "").strip()) < _NATIVE_FLOOR]


def sample_pages(pdfs: list[Path], n: int) -> list[tuple[Path, int]]:
    """Evenly spaced scanned pages, split across the named PDFs.

    Evenly spaced rather than the first N: the first scanned pages of a report
    are its cover and separator sheets, which are the easiest pages in the file
    and would flatter any engine.
    """
    picked: list[tuple[Path, int]] = []
    per = max(1, n // max(1, len(pdfs)))
    for pdf in pdfs:
        idxs = scanned_page_indices(pdf)
        if not idxs:
            continue
        step = max(1, len(idxs) // per)
        chosen = idxs[::step][:per]
        picked.extend((pdf, i) for i in chosen)
    return picked[:n]


def rasterize(pdf: Path, index: int, dest: Path, dpi: int = 200):
    """Page → (BGR array, PNG path). Same dpi the pipeline uses, so the
    bake-off measures the pipeline's input, not a nicer one."""
    import cv2
    import fitz

    with fitz.open(str(pdf)) as doc:
        arr = ex._page_array(doc[index])
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), arr)
    return arr, dest


def run_rapidocr(arr) -> dict:
    t0 = time.perf_counter()
    text, confs = ex._ocr_array(arr)
    dt = time.perf_counter() - t0
    return {"engine": "rapidocr", "seconds": round(dt, 3), "text": text,
            "chars": len(text), "lines": len(confs),
            "mean_conf": round(statistics.fmean(confs), 4) if confs else None,
            "min_conf": round(min(confs), 4) if confs else None,
            "p10_conf": (round(sorted(confs)[max(0, len(confs) // 10)], 4)
                         if confs else None),
            "low_conf_lines": sum(1 for c in confs if c < 0.85),
            "confs": [round(c, 4) for c in confs]}


def tesseract_path() -> str | None:
    """Tesseract only if it is ALREADY installed. Never install one: a
    bake-off that changes the machine to run is not a measurement."""
    import shutil

    return shutil.which("tesseract")


def run_tesseract(png: Path, exe: str) -> dict:
    import subprocess

    t0 = time.perf_counter()
    proc = subprocess.run([exe, str(png), "stdout", "-l", "eng"],
                          capture_output=True, text=True)
    dt = time.perf_counter() - t0
    text = " ".join((proc.stdout or "").split())
    return {"engine": "tesseract", "seconds": round(dt, 3), "text": text,
            "chars": len(text), "lines": len((proc.stdout or "").splitlines()),
            "mean_conf": None, "min_conf": None, "p10_conf": None,
            "low_conf_lines": 0, "confs": []}


def stability(arr, repeats: int) -> dict:
    """Is the engine deterministic? Same array, N times, compare bytes.

    This is the question the determinism contract actually turns on for the
    OCR path: if the engine is not stable, no amount of stable plumbing makes
    the corpus byte-identical.
    """
    outs = []
    for _ in range(repeats):
        text, confs = ex._ocr_array(arr)
        outs.append((text, tuple(round(c, 6) for c in confs)))
    return {"repeats": repeats, "distinct_results": len(set(outs)),
            "stable": len(set(outs)) == 1}


_HTML_HEAD = """\
<meta charset="utf-8"><title>DocIQ OCR bake-off — hand-check sheet</title>
<style>
body{font:14px/1.5 -apple-system,Segoe UI,sans-serif;margin:2rem;max-width:1400px}
h1{font-size:1.4rem} .pg{border-top:2px solid #0E4D80;padding:1rem 0;margin:1rem 0}
.row{display:flex;gap:1.5rem;align-items:flex-start;flex-wrap:wrap}
.col{flex:1 1 380px;min-width:340px}
img{max-width:100%;border:1px solid #ccc}
pre{white-space:pre-wrap;background:#f6f8fa;padding:.75rem;border-radius:4px;
    max-height:520px;overflow:auto;font:12px/1.45 ui-monospace,Consolas,monospace}
table{border-collapse:collapse;font-size:13px} td,th{border:1px solid #ddd;padding:4px 8px}
.meta{color:#555;font-size:13px}
</style>
<h1>LI Document IQ — OCR bake-off hand-check sheet</h1>
<p class="meta">Left: the rasterized page exactly as the pipeline sees it
(200 dpi). Right: what the engine read. Mark each page correct / partly wrong /
wrong. No ground truth is asserted here — that is the reader's judgement.</p>
"""


def write_html(rows: list[dict], dest: Path) -> None:
    parts = [_HTML_HEAD]
    for r in rows:
        parts.append(f'<div class="pg"><h2>{escape(r["label"])}</h2>')
        parts.append('<div class="row"><div class="col">'
                     f'<img src="{escape(r["png"])}" alt=""></div>')
        for res in r["results"]:
            conf = ("n/a" if res["mean_conf"] is None
                    else f'{res["mean_conf"]:.4f}')
            parts.append(
                f'<div class="col"><b>{escape(res["engine"])}</b> '
                f'<span class="meta">mean conf {conf}, {res["lines"]} line(s), '
                f'{res["chars"]} chars, {res["seconds"]}s</span>'
                f'<pre>{escape(res["text"]) or "(nothing read)"}</pre></div>')
        parts.append("</div></div>")
    dest.write_text("\n".join(parts), encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ocr_bakeoff")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True, help="MUST be outside the repo")
    ap.add_argument("--pdfs", nargs="+", default=["CER-1-145.pdf", "CER-1-113.pdf"])
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument("--stability-repeats", type=int, default=30)
    args = ap.parse_args(argv)

    corpus = Path(args.corpus)
    out = Path(args.out)
    if out.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        print("refusing to write client-derived material inside the repo")
        return 2
    (out / "pages").mkdir(parents=True, exist_ok=True)

    pdfs = []
    for name in args.pdfs:
        hits = sorted(corpus.rglob(name))
        if not hits:
            print(f"not found in corpus: {name}")
            return 2
        pdfs.append(hits[0])

    picked = sample_pages(pdfs, args.pages)
    print(f"sampled {len(picked)} scanned page(s) from {len(pdfs)} PDF(s)")

    exe = tesseract_path()
    print(f"tesseract: {exe or 'NOT INSTALLED — rapidocr characterized alone'}")

    rows: list[dict] = []
    first_arr = None
    for pdf, idx in picked:
        label = f"{pdf.name} p.{idx + 1}"
        png = out / "pages" / f"{pdf.stem}_p{idx + 1:04d}.png"
        arr, _ = rasterize(pdf, idx, png)
        if first_arr is None:
            first_arr = arr
        results = [run_rapidocr(arr)]
        if exe:
            results.append(run_tesseract(png, exe))
        rows.append({"label": label, "pdf": pdf.name, "page": idx + 1,
                     "png": f"pages/{png.name}", "results": results})
        print(f"  {label}: " + ", ".join(
            f'{r["engine"]} {r["chars"]}ch conf={r["mean_conf"]} {r["seconds"]}s'
            for r in results))

    stab = stability(first_arr, args.stability_repeats) if first_arr is not None else {}
    print(f"stability: {stab}")

    (out / "measurements.json").write_text(
        json.dumps({"corpus": str(corpus), "pdfs": [p.name for p in pdfs],
                    "tesseract": exe, "stability": stab, "pages": rows},
                   indent=2), encoding="utf-8", newline="\n")
    write_html(rows, out / "hand_check.html")
    print(f"\nwrote {out / 'hand_check.html'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
