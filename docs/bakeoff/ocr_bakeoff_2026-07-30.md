# rapidocr characterization on the real MODEC/Petrobras MPR corpus

> **RETITLED 2026-08-03, as D-19 records.** This document was written as
> "D-01 OCR bake-off". D-19 (2026-07-31) wrote Tesseract off — "D-01's
> conditional swap and acceptance criterion 9's comparison are both **cancelled,
> not deferred**" — and ruled that this artifact "stands as the methodology
> artifact D-01 asked for, **retitled to what it is**". The retitling had not
> been done, so a cancelled comparison went on being described here as pending
> and §2 went on recommending an install to enable it. It is a **rapidocr
> characterization**, and that is its final form.

**Date:** 2026-07-30 (retitled 2026-08-03) · **Track:** A (ingestion spine),
Sprint 1
**Decision it was written under:** D-01 — build on rapidocr, compare against
Tesseract on ~20 real scanned MPR pages, swap only if Tesseract wins decisively.
**Decision that closed it:** D-19 — Tesseract written off; the comparison and
acceptance criterion 9's head-to-head are **cancelled**. No future sprint owes
one.
**Status:** **rapidocr characterized. No comparison was made and none is
outstanding.** The liability D-19 records — that the shipped engine was chosen on
in-house familiarity and ONNX bundling convenience and never benchmarked against
an alternative on this corpus — stands, and D-25/D-28/D-29 attach a measured
page-level cost to it.

## 1. What was done

The harness is `tools/ocr_bakeoff.py`, committed. It samples genuinely scanned
pages from the D-12 corpus, rasterizes them at the pipeline's own 200 dpi,
runs every OCR engine present on the machine, and writes the page images, the
recognized text, the per-page measurements and a side-by-side hand-check sheet.

**No ground truth is asserted here.** Ground truth on a scanned MPR page is a
human reading the page; a harness that generated one would be grading itself.
What this produces is the measurable characterization plus the material that
makes Alex's hand-check cheap.

**Client data.** The corpus, the rasterized pages, the recognized text and the
hand-check sheet are client-derived and are **not** in the repository. They are
at:

```
…\scratchpad\bakeoff\hand_check.html      ← open this; 20 pages, side by side
…\scratchpad\bakeoff\pages\*.png          ← the 20 rasterized pages
…\scratchpad\bakeoff\measurements.json    ← every number quoted below
```

The harness refuses to write inside the repository.

### Sample

20 pages, drawn evenly across the scanned pages of the two D-12 PDFs —
`CER-1-145.pdf` (10 pages, of 175 scanned in 222) and `CER-1-113.pdf` (10, of
91 in 228). **Evenly spaced, not the first N**: the leading scanned pages of an
MPR are cover and separator sheets, which are the easiest pages in the file and
would flatter any engine. The sampling is the disclosed cap — 20 of 461 scanned
pages in the corpus, 4.3%.

## 2. Tesseract: absent, so no comparison was made

Tesseract is **not installed on this machine**. Checked: `PATH`, the four
standard Windows install locations, and the `pytesseract` / `tesserocr`
bindings — none present. The brief forbids installing it, and rightly: a
bake-off that changes the machine in order to run has measured something other
than the machine.

**Consequence, stated plainly.** This artifact characterizes rapidocr. It does
**not** compare the two engines.

**RULED, 2026-07-31 (D-19): option (b), and the comparison is cancelled.** Two
ways forward were put to Alex when this was written — **(a)** install Tesseract 5
on the build machine and re-run the same harness, or **(b)** rule D-01 on the
rapidocr characterization alone and record that no head-to-head was performed.
The recommendation was (a). **Alex ruled (b).** Tesseract is written off, D-01's
conditional swap and acceptance criterion 9's comparison are cancelled rather
than deferred, and no future sprint owes an install.

The original text of this section carried recommendation (a) as a live
recommendation for eight days after it was overruled — a standing instruction to
enable a comparison that had been cancelled. It is recorded here rather than
deleted, because what the ruling rejected is part of what the ruling means.

**What the ruling costs, on the record.** D-19 states it and D-25, D-28 and D-29
measure it: DocIQ ships an OCR engine never benchmarked against an alternative on
this corpus, and criterion 4 is NOT MET on scanned productions — 31.250% of OCR'd
pages carry a Bates locator (0 wrong, 0 false positives), against 100.000% on
native-text pages. "Tesseract is the industry-recognizable name for law-firm IT
review", the original argument for the bake-off, remains unaddressed.

## 3. rapidocr — measured

Engine `rapidocr_onnxruntime` 1.2.3, ONNX models loaded from the installed
package, `use_angle_cls` on, 200 dpi rasterization (the pipeline's own).

| measure | value |
|---|---|
| pages | 20 (17 with recoverable text, 3 blank — see §4) |
| mean page confidence | 0.8628 |
| median / min / max page confidence | 0.8632 / 0.8023 / 0.8952 |
| pages below the §4 Stage-2 threshold (85%) | **3 of 17** |
| text lines recognized | 1,014 |
| lines below 85% | 375 (**37.0%**) |
| line-confidence p05 / p25 / p50 / p75 / p95 | 0.665 / 0.811 / 0.872 / 0.902 / 0.931 |
| characters recognized | 20,530 (mean 1,026/page, max 3,922) |
| time | 114.9 s total, mean 5.7 s/page, median 5.1 s, max 23.3 s |

### The threshold is the finding

The §4 default of 85% sits almost exactly on the median line confidence of this
corpus (0.872). At page level only 3 of 17 pages flag; at line level 37% of
lines do. Two things follow, and both are for the spec rather than for the
code:

1. **The page-level flag is the right one to drive `ProcessingStatus`.** The
   implementation flags on the page's mean, not on the presence of any
   low-confidence line — flagging on the latter would flag essentially every
   scanned page in this corpus and train the operator to ignore the flag.
   `ocr_low_conf_lines` is still recorded per page, so the finer signal is
   available to the summary without driving the status.
2. **85% is a live number on this corpus, not a comfortable one.** A small
   shift either way moves the flagged set materially. If the hand-check finds
   the 0.80–0.85 pages are in fact accurate, the threshold is a candidate for
   re-ruling; the value is in `RunConfig.ocr_conf_threshold_pct` and is part of
   run identity, so changing it is a one-line, fully logged decision.

### Determinism

30 repeated OCR passes over the same rasterized page produced **1 distinct
result** — identical text and identical per-line confidences to 6 decimal
places. The engine is deterministic on this hardware, which is the
precondition for the byte-identical claim over any corpus containing scanned
pages. It is measured, not assumed.

### Timing shape

Time scales with the amount of text on the page, not with page count: the
23.3 s page is the 3,922-character one and the sub-second pages are the blanks.
For a 461-scanned-page corpus this projects to roughly 44 minutes of OCR
single-threaded, and the pipeline fans OCR across a bounded pool. See the
performance note in §5.

## 4. Failure modes observed

**Three pages recognized zero characters** — `CER-1-145.pdf` p.10 and p.172,
`CER-1-113.pdf` p.182. Every one of them was checked directly against the
source PDF and is **genuinely blank**: no embedded images, and a rasterized
page of uniform white (mean pixel 255.0, standard deviation 0.0). These are not
OCR misses; they are separator pages, and DocIQ classifies them as
`PageKind.EMPTY` with a disclosed page note and a document note, which is
exactly the §2 Principle 1 handling. The corpus does contain blank pages inside
scanned runs, so this path is exercised by real data and not only by the
fixture.

**No page produced garbage-but-confident output** in the sample — the low
scores track visibly degraded regions rather than confident nonsense. That is
the failure mode a confidence threshold cannot catch, so it is the one the
hand-check should be looking for.

## 5. What the hand-check has to decide

Open `hand_check.html`. For each of the 20 pages: the rasterized page on the
left exactly as the pipeline sees it, the recognized text on the right with its
confidence. Mark each **correct / partly wrong / wrong**, and specifically:

1. Are the 3 sub-85% pages actually wrong, or merely low-scored?
2. Is any high-confidence page wrong? (Confident nonsense is the dangerous
   case; nothing else in the pipeline can catch it.)
3. Are table figures — the numbers a forensic analysis would rely on —
   transcribed correctly? Character-level accuracy on prose is not the same
   claim as accuracy on a progress table.

## 6. Reproducing

```
python tools/ocr_bakeoff.py --corpus "<corpus root>" --out "<dir outside the repo>"
```

Defaults: `CER-1-145.pdf` and `CER-1-113.pdf`, 20 pages, 30 stability repeats.

The harness still has the Tesseract path wired and would pick a binary up from
`PATH`. That is a property of the code, **not an invitation**: D-19 cancelled the
comparison, so an engine appearing in this harness's output would be an
unrequested measurement, not a pending one.
