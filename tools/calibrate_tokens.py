"""D-03 calibration probe, run against a real MPR corpus outside the repo.

    python tools/calibrate_tokens.py "C:\\path\\to\\Project FIles" [sample_size]

Prints summary statistics only — character counts, pre-token counts, implied
ratio bands. No document text, filename or path is printed or written.

**This does not measure Claude tokens.** It cannot: DocIQ is offline by
principle and no tokenizer is available in the build environment. What it
measures is the corpus's pre-token structure, which bounds the token count from
below for any byte-level BPE tokenizer, and it reports whether the D-03 ruled
band of 3.30-3.60 chars/token is consistent with that structure. Read the module
docstring of ``dociq.verify.tokens`` before quoting any number from here.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# The probes print typographic characters; a cp1252 console would otherwise
# crash on them and lose the whole report to an encoding error.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dociq.verify.tokens import DEFAULT_BASIS, calibrate, estimate_tokens, measure

DEFAULT_SAMPLE = 40
MAX_PAGES_PER_DOC = 40
"""Bounded so the probe finishes in minutes on a 2.6 GB corpus. Stated, and the
actual page count read is reported, so the sample is never silently narrow."""


def extract(paths, rng) -> tuple[list[str], int, int]:
    import fitz  # PyMuPDF

    texts: list[str] = []
    pages_read = 0
    docs_read = 0
    for path in paths:
        try:
            with fitz.open(path) as doc:
                count = min(len(doc), MAX_PAGES_PER_DOC)
                for i in range(count):
                    text = doc[i].get_text()
                    if text.strip():
                        texts.append(text)
                pages_read += count
                docs_read += 1
        except Exception as exc:  # a corrupt file must not stop the probe
            print(f"  (skipped one file: {type(exc).__name__})")
    return texts, docs_read, pages_read


def main(root: str, sample: int) -> int:
    rng = random.Random(20260730)
    pdfs = sorted(Path(root).rglob("*.pdf"))
    print(f"PDFs found                 : {len(pdfs)}")
    if not pdfs:
        print("nothing to calibrate against")
        return 2
    chosen = rng.sample(pdfs, min(sample, len(pdfs)))
    texts, docs_read, pages_read = extract(chosen, rng)
    print(f"documents sampled          : {docs_read}")
    print(f"pages read (cap {MAX_PAGES_PER_DOC}/doc)  : {pages_read}")
    print(f"non-empty page texts       : {len(texts)}")

    report = calibrate(texts)
    p = report.profile
    print(f"characters                 : {p.chars:,}")
    print(f"utf-8 bytes                : {p.utf8_bytes:,}")
    print(f"pre-tokens                 : {p.pretokens:,}")
    print(f"characters unmatched       : {p.unmatched_chars}")
    print(f"digit chars (share)        : {p.digit_chars:,} ({round(100*p.digit_chars/max(p.chars,1))}%)")
    print(f"whitespace chars (share)   : {p.whitespace_chars:,} ({round(100*p.whitespace_chars/max(p.chars,1))}%)")
    print(f"chars per pre-token        : {report.chars_per_pretoken_x100/100:.2f}")
    print(
        "implied chars/token band   : "
        f"{report.implied_low_x100/100:.2f}-{report.implied_high_x100/100:.2f}"
    )
    print(f"shipped band               : {DEFAULT_BASIS.display}")
    print(f"bands overlap              : {report.consistent}")
    print(f"recommended band           : {report.recommended.display}")
    for note in report.notes:
        print(f"  note: {note}")

    est = estimate_tokens(p)
    print(f"estimate for this sample   : {est.headline}")
    print(f"  hard floor (pre-tokens)  : {p.token_floor:,}")
    print(f"  hard ceiling (bytes)     : {p.token_ceiling:,}")
    print(f"  ratio band refuted       : {est.ratio_refuted}")
    print(f"  clamped low / high       : {est.clamped_low} / {est.clamped_high}")
    print(f"  {est.capacity().statement}")

    # Stability: the measurement must be exact, not approximate.
    joined = "".join(texts)
    first = measure(joined)
    stable = all(measure(joined) == first for _ in range(8))
    print(f"measurement stable x8      : {stable}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(
        main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_SAMPLE)
    )
