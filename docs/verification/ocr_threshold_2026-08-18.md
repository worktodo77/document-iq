# The OCR review flag: one predicate, a measured threshold, and blank pages

**Date:** 2026-08-18
**Branch:** `build/sprint-4`
**Raised by:** Alex, driving the packaged `.exe` on a real matter — *"we had 99
low-confidence OCRs… the files OCR'd are in good shape and it's not apparent why
the OCR performed was low-confidence."*
**Corpus:** `GTG — Progress Report`, 11 PDFs, 1,598 pages, 377 OCR'd. Client
data: no page text and no document name from the matter appears here.

The instinct was right. The documents were not the problem.

---

## 1. The screen said 99 and the log said 80

Both numbers were computed from the same run and both were "correct" — by
different rules.

| where | comparison | result |
|---|---|---|
| `gui/view_models._ocr_flags` | `page.ocr_conf < 0.85` — the **raw float** | 99 |
| `emit/log._flagged_pages` | `round(conf*100) < 85` — the **rounded percent** | 80 |

Nineteen pages sat in the gap, at confidences like 84.73%, 84.72%, 84.83%. They
were shown to the operator as needing review and **left out of the audit
record.**

In a tool whose central argument is that `processing_log.json` is the auditable
account of what happened, the log and the screen disagreeing about which pages a
human must check is not a cosmetic defect.

**Fixed as a single predicate**, `contracts.needs_ocr_review`, used by the log,
the screen and the extractor's document-status computation. The disagreement is
not repaired at three call sites; it is unrepresentable.

**The rounded comparison won.** The integer percent is what is written to disk
and rendered on screen, so deciding on it means the number a reader sees explains
the decision they are looking at. Deciding on a hidden float leaves a page marked
for review beside a rendered "85%" — the same confusion one level down.

## 2. The threshold was never calibrated against this engine

Measured over **all 377 OCR pages**, not just the flagged ones — the run's own
log records only pages below the line, so the run could not answer the question
that mattered: one population, or two?

```
 65-69     1  #
 70-74     8  #
 75-79    26  ####
 80-84    64  ###########
 85-89   236  ########################################   <-- 85% cut here
 90-94    42  #######
```

Median **86.32%**, p10 80.32%, p90 90.32%, max 93.89%. **One population**, and
the threshold was planted on the left edge of its modal band.

The corroboration predates the run: D-19's bake-off measured this engine at
**mean 0.8628** on 20 scanned pages of this same corpus. The average healthy page
cleared the old bar by 1.3 points, so ordinary variation dipped under it. 85 came
from the requirements as a plausible figure; rapidocr's confidence is not a
probability of correctness, and 84% does not mean "16% of this page is wrong."

| cut-off | flagged | share of OCR pages |
|---|---:|---:|
| <78% | 13 | 3.4% |
| <80% | 35 | 9.3% |
| <82% | 56 | 14.9% |
| **<85% (old default)** | **99** | **26.3%** |
| <88% | 271 | 71.9% |

**80% is where the distribution breaks.** Fitted to one corpus of 11 documents —
better grounded than 85, and not thereby universal. It is a hashed identity
input, so the change moves the run identity of every run that does not set it.

## 3. Eleven flagged pages had nothing on them

Of the 99, **11 carried fewer than 20 characters**. Their confidences repeat
exactly across different documents — 71.76% three times, 74.97% twice — which is
one speck of scanner noise recognized as one token. A confidence score over two
glyphs measures nothing, and a review flag that fires on blank pages teaches an
operator to ignore review flags.

Such pages are now excluded from review. **They are not excluded from the
record:** the log counts them per document as
`ocr_pages_without_usable_text`. Principle 1 forbids a page quietly leaving the
account; it does not require sending an expert to proofread a blank.

### The guard the corpus does not exercise

"Few characters" alone would have been wrong. Few characters across **many
lines** is not a blank page — it is a dense page whose reading collapsed, and
that is exactly a page a human must see.

**Measured: all 84 low-character pages of this run returned 0–2 lines**, median
1 line, median 26 characters. The failure mode is absent here. The predicate
requires **both** conditions anyway, because "the corpus does not exercise it"
selects nothing — the failure would arrive on the first matter that scans worse
than this one, and it would arrive silently.

## 4. What the three fixes do together

| | screen | log |
|---|---:|---:|
| before | 99 | 80 |
| after | **11** | **11** |

Plus 84 pages counted as having no usable text rather than presented as needing
review.

## What this does NOT establish

* **It is one corpus.** 11 documents, one matter, one language mix.
* **It does not improve OCR.** The text is exactly what it was; what changed is
  which pages are put in front of a person. Two hypotheses about the OCR itself
  were tested and both failed: rasterizing at 300 and 400 dpi made confidence
  monotonically **worse** (85.06% → 84.55% → 83.29%), and the "blank pages" that
  looked like the whole story in the worst-8 sample turned out to be 11 of 99.
* **The residual is language-shaped, and that is a separate question.** Failure
  rate by language over the 377 OCR pages: **English 10.9%** (183 pages, mean
  87.4%), **Portuguese 43.7%** (119 pages, mean 84.3%). A Portuguese page is four
  times more likely to fail. Portuguese is 19.3% of this corpus and was 77% of
  its failures — which is also why the first language reading of the *flagged*
  pages was misleading: the flagged set is selected for failure, so it cannot be
  read as a sample of the corpus. The engine question lives there, not in the
  English material.
* **The language split is a regex over stopwords**, not a language detector.
  Good enough to establish a 4× difference; not good enough to quote.
