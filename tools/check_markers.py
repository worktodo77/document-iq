"""Acceptance criterion 3: do page markers resolve to the right original page?

    python tools/check_markers.py <matter_output_root> <source_root> [--sample 50]

§13's criterion 3 says "every page marker in clean_text resolves to the correct
page of the original PDF on spot-check (sample: 50 random markers)". This is that
spot-check, mechanized so it can be re-run on any matter instead of done by eye
once.

**The check is an argmax, not a threshold.** For each sampled marker, the text
under it is compared against the same page of the original PDF read by a
*different* extractor — PyMuPDF, where the pipeline used pypdf — and against
that page's neighbours. The emitted block must resemble page *n* more than it
resembles *n-1* or *n+1*.

That distinction is the whole design. A similarity threshold would measure how
alike two extractors are, which is not the question and would fail on any page
where they disagree about layout. An argmax measures alignment, which is the
question, and an off-by-one page is exactly what it catches.

Three kinds of page are set aside and counted rather than scored:

* a marker whose emitted block carries too few tokens to discriminate,
* a page where the reference extractor sees no text at all — a scanned page,
  which the pipeline reached by OCR and ``get_text()`` cannot reach by
  definition, and
* a page that is token-identical to a neighbour, where no evidence can
  distinguish the two and either assignment fits equally.

Counting them as passes would inflate the result; counting them as failures
would report a defect that is not there. They are reported separately so the
denominator is visible.

Prints counts and scores only — never document text. Safe to run against client
material.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

MARKER = re.compile(r"^===== PAGE (\d+)(?: \[BATES: (.*?)\])? =====$")
WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
"""Every letter run and every digit run, however short.

A coarser bag (3+ letters, 2+ digits) was tried first and produced a tie: two
196-page-document pages of section-divider boilerplate that differ by exactly one
digit read as identical, and the argmax had nothing to choose between them.
Keeping single digits and short words costs nothing and is what makes
near-identical boilerplate pages discriminable."""

MIN_WORDS = 12
"""Below this many distinct tokens a page cannot discriminate itself from its
neighbours, in either direction. Stated rather than tuned silently."""


def bag(text: str) -> set[str]:
    return {w.casefold() for w in WORD.findall(text)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def blocks(path: Path) -> dict[int, str]:
    """``{original page number: text under that marker}``."""
    out: dict[int, str] = {}
    current: int | None = None
    buf: list[str] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        m = MARKER.match(line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf)
            current, buf = int(m.group(1)), []
        else:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_markers")
    ap.add_argument("output_root", help="a DocIQ matter output folder")
    ap.add_argument("source_root", help="the folder that was reduced")
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20260731)
    args = ap.parse_args(argv)

    try:
        import fitz  # pymupdf
    except ImportError:  # pragma: no cover - declared dependency
        print("this probe needs PyMuPDF as its independent reference extractor")
        return 2

    out = Path(args.output_root)
    src = Path(args.source_root)
    log = json.loads((out / "processing_log.json").read_text(encoding="utf-8"))
    by_id = {
        d["doc_id"]: d
        for d in log["content"]["documents"]
        if d["ext"] == ".pdf" and d["parent_doc_id"] is None and d["pages_in"] > 2
    }
    if not by_id:
        print("no multi-page top-level PDF in this matter; nothing to check")
        return 2

    rng = random.Random(args.seed)
    pool = sorted(by_id)
    picks = [
        (doc_id, rng.randint(2, by_id[doc_id]["pages_in"] - 1))
        for doc_id in (rng.choice(pool) for _ in range(args.sample))
    ]

    correct = wrong = skipped = missing = tied = 0
    margins: list[float] = []
    for doc_id, page_no in picks:
        entry = by_id[doc_id]
        emitted = blocks(out / "clean_text" / f"{doc_id}.txt")
        if page_no not in emitted:
            missing += 1
            continue
        body = bag(emitted[page_no])
        if len(body) < MIN_WORDS:
            skipped += 1
            continue
        with fitz.open(src / entry["rel_path"]) as pdf:
            here = bag(pdf[page_no - 1].get_text())
            near = [
                bag(pdf[i - 1].get_text())
                for i in (page_no - 1, page_no + 1)
                if 1 <= i <= len(pdf)
            ]
        if max([len(here)] + [len(n) for n in near]) < MIN_WORDS:
            skipped += 1
            continue
        s_here = jaccard(body, here)
        s_near = max((jaccard(body, n) for n in near), default=0.0)
        if s_here > s_near:
            correct += 1
            margins.append(s_here - s_near)
        elif s_here == s_near:
            # The page and a neighbour are token-identical, so no evidence
            # distinguishes them and either assignment fits equally. Counted as
            # undiscriminable rather than as a pass (which would inflate the
            # result) or a failure (which would report a defect that is not
            # there). A genuine off-by-one is strictly worse, not equal, and
            # falls through to the branch below.
            tied += 1
        else:
            wrong += 1
            print(f"  MISALIGNED {doc_id} page {page_no}: this page "
                  f"{s_here:.3f}, nearer neighbour {s_near:.3f}")

    print(f"sampled markers            : {len(picks)}")
    print(f"skipped (cannot discriminate): {skipped}")
    print(f"tied with a neighbour        : {tied}")
    print(f"marker absent from clean_text: {missing}")
    print(f"judged                     : {correct + wrong}")
    print(f"resolved to the right page : {correct}/{correct + wrong}")
    if margins:
        margins.sort()
        print(f"median margin over the nearer neighbour: "
              f"{margins[len(margins) // 2]:.3f}")
    if missing:
        print("a marker missing from clean_text is a Principle-2 failure, not a "
              "skip: the page was accounted for but its locator was not emitted")
    return 0 if (correct + wrong) and wrong == 0 and missing == 0 else 1


if __name__ == "__main__":  # pragma: no cover - developer entry point
    raise SystemExit(main())
