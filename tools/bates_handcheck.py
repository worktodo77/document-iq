"""D-23's hand-check half: render a stratified page sample for a human read.

    python tools/bates_handcheck.py --corpus "<MNFV root>" --out <scratch dir>
    python tools/bates_handcheck.py --score <scratch dir> --answers answers.txt

WHY IT IS TWO COMMANDS AND NOT ONE
The first renders footer strips onto contact sheets labelled with an INDEX and
nothing else. The expected Bates number is written to a separate answer key the
reader does not see. The second scores what the reader typed against that key.

Showing the expected value beside the image would not be a check. A reader who
can see the answer confirms the answer; the only question a hand-check answers
is whether an independent read of the page agrees, and independence has to be
constructed rather than assumed.

WHAT IS RENDERED
The footer strip only — the bottom ~9% of the page. That is where the stamp is
burned in, it is the whole content of the check, and it keeps the volume of
client material pulled out of the corpus to the minimum the check needs.
Nothing is written inside the repository; ``--out`` is expected to point
outside it.

STRATIFICATION
Not a uniform random draw. A uniform sample over 14,524 pages would put ~98% of
its picks inside the one production whose ground truth is authoritative, and
almost none on the cases that can actually go wrong:

  * first / interior / last page of a document (document-boundary errors)
  * the 4-digit and 5-digit halves of the MNFV numbering (width handling)
  * the two files whose named ranges OVERLAP (the continuity finding)
  * the combined PDFs, whose ground truth is filename-derived and weakest

Each stratum's size is stated in the manifest, so "100 pages" is never mistaken
for "100 uniformly drawn pages".
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

FOOTER_FRACTION = 0.09
"""How much of the page bottom is rendered. Measured, not guessed: on this
production the stamp sits within the bottom 5-6% and 9% leaves margin for a
page whose content runs low without pulling body text into the strip."""

SHEET_ROWS = 10
DPI = 150


def _render_footer(pdf: Path, page_index: int):
    import fitz

    with fitz.open(str(pdf)) as doc:
        page = doc[page_index]
        r = page.rect
        clip = fitz.Rect(r.x0, r.y1 - r.height * FOOTER_FRACTION, r.x1, r.y1)
        pix = page.get_pixmap(dpi=DPI, clip=clip)
        return pix.tobytes("png")


def build_sheets(picks: list[dict], out: Path) -> None:
    import io

    from PIL import Image, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    sheets: list[list] = []
    for i in range(0, len(picks), SHEET_ROWS):
        sheets.append(picks[i:i + SHEET_ROWS])
    # Every strip is scaled to ONE width. Page sizes in a real production run
    # from letter to a 42-inch architectural sheet, and pasting them at native
    # size makes the drawing sheets illegible next to the letter pages — the
    # first attempt produced a sheet where the stamps on the wide pages were
    # two pixels tall. Uniform width is what makes the sample readable, and a
    # sample that cannot be read is not a hand-check.
    strip_w = 1500
    font = None
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("arial.ttf", 34)
    except Exception:
        pass
    for s_i, sheet in enumerate(sheets):
        strips = []
        for p in sheet:
            png = _render_footer(Path(p["pdf"]), p["page_index"])
            im = Image.open(io.BytesIO(png)).convert("RGB")
            h = max(40, round(im.height * strip_w / im.width))
            strips.append(im.resize((strip_w, h), Image.LANCZOS))
        gutter = 150
        pad = 18
        height = sum(im.height for im in strips) + pad * (len(strips) + 1)
        canvas = Image.new("RGB", (strip_w + gutter, height), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        y = pad
        for p, im in zip(sheet, strips):
            draw.rectangle([0, y - pad // 2, strip_w + gutter, y + im.height
                            + pad // 2], outline=(190, 190, 190))
            draw.text((10, y + max(0, im.height // 2 - 18)),
                      f"#{p['index']:03d}", fill=(200, 0, 0), font=font)
            canvas.paste(im, (gutter, y))
            y += im.height + pad
        canvas.save(out / f"sheet_{s_i:02d}.png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bates_handcheck")
    ap.add_argument("--corpus")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20240529)
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--answers", help="one 'index value' per line")
    args = ap.parse_args(argv)

    out = Path(args.out)
    key_path = out / "answer_key.json"

    if args.score:
        key = json.loads(key_path.read_text(encoding="utf-8"))
        by_index = {int(k["index"]): k for k in key["picks"]}
        read = {}
        for line in Path(args.answers).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            idx, _, val = line.partition(" ")
            read[int(idx.lstrip("#"))] = val.strip()
        def _parts(s):
            """``(prefix, number)`` — case-, space- and padding-insensitive.

            Scored two ways on purpose. Exact-string agreement is the strong
            claim. Numeric agreement is the one that separates "the reader saw
            a different document" from "the ground truth renders the same
            number differently" — and on this corpus the second is real: the
            filenames pad to four digits (``MNFV 0919``) while the burned-in
            stamps pad to five (``MNFV 00919``), and some pages carry no space
            at all (``MNFV0946``). A single exact-match percentage would
            report that as a detection failure, which it is not.
            """
            m = re.match(r"^\s*([A-Za-z][A-Za-z0-9]*?)\s*[ _.-]?\s*(\d+)\s*$", s)
            if not m:
                return (s.strip().lower(), None)
            return (m.group(1).lower(), int(m.group(2)))

        exact = numeric = dis = unread = 0
        rows = []
        for i, k in sorted(by_index.items()):
            got = read.get(i)
            if got is None:
                unread += 1
                continue
            truth = k["expected"]
            if got.replace(" ", "").upper() == truth.replace(" ", "").upper():
                exact += 1
                numeric += 1
            elif _parts(got) == _parts(truth):
                numeric += 1
                rows.append(("PADDING", i, truth, got, k["stratum"]))
            else:
                dis += 1
                rows.append(("DISAGREE", i, truth, got, k["stratum"]))
        total = exact + numeric - exact + dis if False else (numeric + dis)
        print(f"hand-check: {total} of {len(by_index)} page(s) read "
              f"({unread} not read)")
        print(f"  exact string agreement      : {exact} "
              f"({100.0 * exact / total if total else 0:.1f}%)")
        print(f"  prefix+number agreement     : {numeric} "
              f"({100.0 * numeric / total if total else 0:.1f}%)")
        print(f"  DISAGREE on the number      : {dis}")
        for kind, i, truth, got, stratum in rows:
            print(f"    {kind:8s} #{i:03d} truth={truth!r} read={got!r} [{stratum}]")
        return 0

    root = Path(args.corpus)
    rng = random.Random(args.seed)
    picks: list[dict] = []

    # --- stratum 1: the load-file production, by page position -------------
    opts = sorted(root.rglob("*.opt"))
    for opt in opts:
        import csv as _csv

        base = opt.parent.parent
        docs: list[list[tuple[str, str]]] = []
        with opt.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in _csv.reader(fh):
                if len(row) < 4 or not row[0].strip():
                    continue
                if row[3].strip().upper() == "Y" or not docs:
                    docs.append([])
                docs[-1].append((row[0].strip(),
                                 row[2].strip().replace("\\", "/")))
        eligible = [d for d in docs
                    if len(d) >= 3
                    and (base / d[0][1]).is_file()
                    and (base / d[0][1]).stat().st_size < 40 * 1024 * 1024]
        rng.shuffle(eligible)
        for d in eligible[:args.n // 5]:
            for pos, label in ((0, "first page of a document"),
                               (len(d) // 2, "interior page"),
                               (len(d) - 1, "last page of a document")):
                bates, rel = d[pos]
                picks.append({"pdf": str(base / rel), "page_index": pos,
                              "expected": bates, "stratum": label,
                              "ground_truth": "load file (authoritative)"})

    # --- stratum 2: the combined PDFs, filename ranges ----------------------
    rng2 = random.Random(args.seed + 1)
    name_re = re.compile(r"^(?P<prefix>[A-Z]{2,8})\s+(?P<start>\d{3,8})"
                         r"(?:\s*[-‐-―]\s*(?P<end>\d{3,8}))?\b")
    combined = []
    for p in sorted(root.rglob("*.pdf")):
        if "images" in [q.lower() for q in p.parts]:
            continue
        m = name_re.match(p.name)
        if m:
            combined.append((p, m.group("prefix"), int(m.group("start")),
                             len(m.group("start"))))
    for p, prefix, start, width in combined:
        try:
            import fitz

            with fitz.open(str(p)) as doc:
                n = len(doc)
        except Exception:
            continue
        for pos in sorted(rng2.sample(range(n), min(3, n))):
            picks.append({
                "pdf": str(p), "page_index": pos,
                "expected": f"{prefix} {str(start + pos).zfill(width)}",
                "stratum": f"combined PDF, {width}-digit names",
                "ground_truth": "filename range (weak — see D-23)"})

    picks = picks[:args.n]
    for i, p in enumerate(picks, 1):
        p["index"] = i
    out.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps({"picks": picks}, indent=2), encoding="utf-8")
    build_sheets(picks, out)

    from collections import Counter

    strata = Counter(p["stratum"] for p in picks)
    print(f"rendered {len(picks)} footer strip(s) to {out}")
    for k, v in sorted(strata.items()):
        print(f"  {v:3d}  {k}")
    print(f"answer key (NOT to be looked at before reading): {key_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
