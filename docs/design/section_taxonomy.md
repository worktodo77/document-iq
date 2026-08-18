# What gets omitted, and how the software knows

**Status:** design pass, D-24. Opened by Alex 2026-08-01: *"we should have standard
templates for what types of pages get dropped… they should not be attributable to
any of the corpus projects."* Grounded in a measurement of the real record, not in
assumptions about it.

---

## 1. The line this design may not cross

DocIQ is positioned as a **deterministic reducer, not an interpreter**, and the
economization triage already excluded keyword-relevance filtering outright:
*selecting evidence by content is what opposing counsel attacks.*

Dropping "photographs" is content-based selection. What keeps it on the right side
of the line is not the subject matter but the shape of the decision:

> The expert approves the omission of a **structural category, in advance, as a
> class**. The tool never decides that *this particular page* is unimportant.

"Omit all photo-log pages, approved by J. Long, 2026-08-01" is a decision an expert
can state and defend. "Omit pages that don't appear to discuss the delay" is not,
and no amount of accuracy would make it so. Every mechanism below has to be a rule
an expert can **read before the run** and recognize afterwards in the log.

Two consequences follow, and they are binding on the build:

- **No classifier, no model, no scoring.** A recognizer whose behavior cannot be
  stated as a rule cannot be approved in advance, and cannot be explained in
  cross-examination.
- **Recognition failures must fail toward KEEP.** A section the recognizer misses
  is a page that survives. A section it invents is a page that vanishes. Those two
  errors are not equally bad, and the contract already encodes the asymmetry:
  KEEP is the unconditional default and a DROP without a `drop_rule` raises.

---

## 2. What the record actually contains

Measured over 36 documents / 1,535 pages / 3,337,999 characters, sampled across
the real corpus. Characters are a proxy for tokens — stated as one — but the
ratios are what the design question needs.

| Category (deterministically detectable) | Pages | Share of text |
|---|---:|---:|
| Narrative & everything else | 1,140 | 52.5% |
| **Schedule / activity tables** | 258 | **33.9%** |
| Page furniture (repeated header/footer lines) | — | 8.0% |
| Table of contents | 65 | 5.4% |
| Photo / figure / divider pages | 54 | **0.2%** |
| Empty / image-only | 18 | 0.0% |

### Two results that changed this design

**Photographs are worth 0.2%.** The Sprint-1 mockups advertise "Photo logs" as the
single largest expert lever (−2.49M tokens). That is wrong by roughly two orders of
magnitude, and it is the assumption almost everyone brings to the problem. A photo
page carries almost no *text*, and tokens come from text. **Photo logs are a
page-count lever, not a token lever** — and Path B, the route D-20 makes primary,
does not care about page count at all. Offering photo-dropping as a headline saving
would be selling a reduction that does not exist.

**Schedule/activity tables are 33.9%** — one third of the record, ~170× the
photographs, and the only category whose removal changes the answer to "does this
fit". They are P6 activity listings pasted into progress reports.

### The recognition result that matters most

**40% of the PDFs carry a PDF outline** (bookmarks) with a genuine section
vocabulary authored by the document's own creator ~~40%~~ — **CORRECTED
2026-08-17: 29.866% (89 of 298) over the full corpus.** The 40% figure came
from this section's 36-document sample and is ten points high. The number that
should be carried forward instead is **pages**: outlined documents are the large
ones, so a tier present in under a third of documents places **11,173 pages,
63.01% of the corpus**. See `docs/verification/sections_2026-08-17.md` Q1.

The vocabulary claim below survives and was re-measured, with two additions that
bind the template design: **522 distinct section families** (716 raw, before
numbering is stripped) — far too many to enumerate, so a template must key to a
normalized family rather than a list of literals; and **159 of those 522, 30.5%,
carry project-identifying text** (`BOMESC YARD`, `MV32 APPENDICES`,
`STATUS OF PETROBRAS TQ`), which D-24 forbids a shipped template from matching,
so project tokens must be stripped and their list supplied per matter. A third
fact belongs here too: **the corpus's third most frequent label is Portuguese**
(`PAGINA EM BRANCO`, 61), so matching is accent-folded and no template may
assume the record is in English. Section vocabulary:
`EXECUTIVE SUMMARY`,
`PROGRESS PHOTOS`, `HSE`, `CRITICAL PATH NARRATIVE`, `APPENDICES`,
`OVERALL PROGRESS S-CURVE`, `PROCUREMENT`, `MECHANICAL COMPLETION AND
COMMISSIONING`. Where an outline exists, the section→page map is a **lookup, not an
inference**, and it is evidence the document makes about itself.

**And the obvious mechanism fails.** Matching heading-shaped lines returns, in
frequency order: `FPSO ALMIRANTE BARROSO MV32` (1,017), `PETROBRAS` (981),
`WEEKLY PROGRESS REPORT NO. #` (320), then document numbers and revision codes.
**A heading regex finds letterhead, not sections.** A design resting on "match the
section titles" would have looked entirely reasonable, passed review, and silently
dropped the wrong pages. It is excluded on this evidence, not on taste.

---

## 3. Recognition tiers — strongest evidence first

A page is assigned to a section by the **first tier that resolves it**, and the
tier is recorded per page in `processing_log.json`. An expert reviewing a drop
must be able to see not just which rule fired but **what kind of evidence it
rested on**, because these tiers are not equally strong and presenting them as
though they were would be the quiet lie in this feature.

| Tier | Mechanism | Coverage on the real corpus | Strength |
|---|---|---|---|
| **1** | The document's own **PDF outline / bookmarks** | **29.9% of PDFs — but 63.0% of PAGES** (11,173; measured 2026-08-17 over all 298 PDFs. The earlier "40% of PDFs" was a 36-doc sample and is withdrawn) | Authored by the document's creator. Not inference. |
| **2** | The document's own **table of contents** page, parsed to section→page ranges | TOC present on 65 of 1,535 pages | The document's own statement, but recovered by parsing. |
| **3** | **Measurable page-class rules** — properties of the page, not its meaning | universal | Deterministic and inspectable, but a *class* rule, not a section boundary. |
| **4** | **Explicit page ranges** entered by the expert | universal | The expert's own instruction. Strongest of all, and least scalable. |
| — | ~~Heading-text regex~~ | — | **EXCLUDED.** Measured: finds letterhead. |

### Tier 3 in detail — the measurable properties

These are the only content signals permitted, because each is a *structural
measurement* rather than a reading of what the page says:

- **Schedule/activity table**: presence of activity-grid column headers
  (`ACTIVITY ID`, `TOTAL FLOAT`, `REMAINING DURATION`, `BL PROJECT START`) or a
  density of ≥12 `dd-Mmm-yy` dates on one page.
- **Photo / figure page**: text below a threshold with ≥1 embedded image covering
  a substantial share of the page area.
- **Page furniture**: a line, digit-normalized, repeated on ≥60% of a document's
  pages. Document-relative, so it adapts to each letterhead rather than carrying a
  list of known ones.
- **Table of contents**: `TABLE OF CONTENTS` / `CONTENTS` in the page's opening
  region.
- **Distribution / transmittal**: `DISTRIBUTION LIST`, `CIRCULATION`, `COPY TO:`.
- **Blank / empty**: zero extracted characters and no image.

---

## 4. The taxonomy

Section types common to engineering & construction project records. **Named by page
type, never by project** (D-24) — no "MODEC profile", no "Petrobras profile",
because a template named after a matter implies decisions taken on that matter.

**Risk** is the forensic cost of dropping it wrongly, and it is deliberately *not*
correlated with size. The most dangerous categories in this table are among the
smallest.

**"Default" is a DESIGN INTENT, not a description of the shipped build** (noted
2026-08-03). In particular **nothing marked `automatic` is implemented**: DocIQ
detects exact-hash duplicates (§4 Stage 1) and *warns* about them, and it removes
neither duplicates nor page furniture — every page of every copy is extracted,
written to `clean_text/` and counted in the accounting identity. See
`adapter._plan`, which emits no automatic lever for exactly this reason. Read this
column as what an approved profile would do, never as what a run does today.

### Progress reports (monthly / weekly)

| Section type | Recognition | Token weight | Risk | Default |
|---|---|---|---|---|
| Cover / title page | T1/T3 (page 1, low text) | trivial | LOW | drop |
| Table of contents | T3 (`CONTENTS`) | **5.4%** | LOW | drop |
| Distribution / circulation list | T3 | trivial | LOW | drop |
| Document control / revision history | T3 | trivial | LOW | drop |
| Page furniture (headers/footers) | T3 (repetition) | **8.0%** | LOW | **automatic** |
| Executive summary | T1/T2 | small | **HIGH** | keep |
| Critical path narrative | T1/T2 | small | **HIGH** | keep |
| Change order / variation log | T1/T2 | medium | **HIGH** | keep |
| **Schedule / activity tables** | T3 (grid signature) | **33.9%** | MEDIUM | **offer, default OFF** (D-27) |
| Milestone status tables | T1/T2 | medium | MEDIUM | keep |
| Manpower / staffing histograms | T1/T2 | small | **HIGH** — decisive in disruption & labour-productivity claims | keep |
| HSE statistics (LTIR/TRIR) | T1/T2 | small | MEDIUM — can matter where a stand-down drove delay | offer |
| Progress S-curves / % complete charts | T1/T3 (image-dominant) | trivial | MEDIUM | offer |
| Procurement / expediting status logs | T1/T2 | medium | MEDIUM | keep |
| Subcontract status | T1/T2 | medium | MEDIUM | keep |
| Quality / NCR logs | T1/T2 | medium | **HIGH** — defect and rework claims | keep |
| Risk register extract | T1/T2 | medium | MEDIUM | offer |
| Cost report / cash flow | T1/T2 | medium | MEDIUM | keep |
| **Progress photographs** | T3 (low text + image) | **0.2%** | **HIGH** — often the only proof of site condition on a date | **offer, but never sold as a saving** |
| Organization charts | T1/T3 | trivial | LOW | offer |
| **Weather logs** | T1/T2 | trivial | **HIGH** — decisive in weather-delay claims | keep |
| Appendices | T1 | varies | varies | keep |

### Correspondence & email

| Section type | Recognition | Risk | Default |
|---|---|---|---|
| Letterhead / header block | T3 (repetition) | LOW | automatic |
| Signature block / confidentiality disclaimer | T3 | LOW | drop |
| Distribution / cc list | T3 | LOW | drop |
| Attachment cover sheet | T3 | LOW | drop |
| Quoted / threaded reply chains | T3 (quote markers) | **MEDIUM–HIGH** — the quoting itself can evidence notice and receipt | offer, default OFF |

### Meeting minutes

| Section type | Recognition | Risk | Default |
|---|---|---|---|
| Attendance list | T3 | LOW | offer |
| Standing agenda boilerplate | T3 (repetition) | LOW | drop |
| Repetition of previous minutes | T3 (cross-document repetition) | MEDIUM | offer, default OFF |
| **Action item register** | T1/T2 | **HIGH** — the densest causation evidence in the record | keep |

### Technical & commercial

| Section type | Recognition | Risk | Default |
|---|---|---|---|
| Drawing title / revision blocks | T3 | LOW | drop |
| Specification standard clauses | T3 (cross-document repetition) | MEDIUM | offer, default OFF |
| Vendor catalogue cut sheets | T1/T3 | LOW | offer |
| Calculation appendices | T1 | MEDIUM | keep |
| Punch lists / ITRs | T1/T2 | MEDIUM | keep |
| Invoices / payment applications | T3 | MEDIUM | keep |
| **Timesheets / labour tickets** | T3 | **HIGH** — the primary record in disruption claims | keep |
| Rate schedules | T3 | LOW | offer |

---

## 5. What this implies for the product

1. **Stop advertising photo-dropping as a saving.** The waterfall must not show a
   token figure for it that the corpus does not support. Where a lever's value is
   in page count rather than tokens, the row has to say so.
2. **The reduction story on this corpus is furniture + TOC (13.4%, safe, largely
   automatic) plus schedule tables (33.9%, the expert's call).** Everything else is
   rounding error. That is a much smaller and much more honest claim than the
   mockups make, and it should be the claim the product makes.
3. **Risk grade belongs on the checklist row**, beside the saving. A template that
   sorts by size puts weather logs and photographs next to a large number and an
   easy click; sorting or flagging by risk is what stops a hurried expert dropping
   the decisive page for a trivial gain.
4. **Recognition tier belongs in the log**, per page. "Dropped because the document's
   own outline placed this page in PROGRESS PHOTOS" and "dropped because the page
   matched a photo-page rule" are different claims with different strengths.

---

## 6. Open — three of four now settled (2026-08-17)

- **The approver problem.** ✅ **RULED — D-34.** Templates ship **unengaged**:
  a built-in template carries no approver and no live DROP, every lever arrives
  OFF, and its contribution until somebody ticks something is *recognition
  only*. The expert's identity, the time and the matter are written into the
  rule and into every drop-log line **at the moment he engages a lever**. Two
  properties carry the ruling: a template alone can never drop a page, and the
  approver field never holds a fiction. Consequence for the build: the KEEP/DROP
  disposition can no longer be baked into a shipped template file, and the
  approver becomes a structured field with a real capture point rather than
  free-text `notes`.
- **Tier 2 feasibility.** ✅ **MEASURED — the doubt is not reproduced, and the
  question is not closed.** Where it could be checked, the printed TOC page
  number *is* the physical page: offset **0 in all 106 paired lines across 25
  documents**, within-document spread **0** everywhere. But the check requires a
  document to carry an outline as ground truth, so it reaches only documents
  that **do not need Tier 2** — the ~193 documents with a TOC and no outline,
  which is Tier 2's whole purpose, are structurally unreachable by this method.
  Risk lowered, question open. `docs/verification/sections_2026-08-17.md` Q2.
- **Coverage of the other 60%.** ✅ **MEASURED, and the answer is "no".** Over
  the 209 documents without a substantive outline (6,331 pages), Tier 3 resolves
  **1,308 pages — 20.66%** and leaves **5,023 — 79.34% — resolved by nothing.**
  Four pages in five, in exactly the documents that need Tier 3 most. Under
  KEEP-by-default that is safe and correct, and it settles what Tier 3 is: a
  supplement, not a fallback. **Corpus-wide the tiers recognize ~70% of pages
  and keep the other ~30% unconditionally** — the honest ceiling on this
  feature, and the claim the product should make.
- **Cross-document repetition** (boilerplate identical across many documents) is
  listed above but is a different mechanism from within-document furniture, and is
  closer to the excluded near-duplicate detection than the other tiers. Needs its
  own ruling before it is built. ⏳ **Still not ruled.**

## 7. The engine this design replaces (2026-08-17)

The shipped Stage-4 engine (`profiles/apply.py`) implements **the tier §3
excludes**: it matches a rule's regex against every line of every page and
carries the matched section forward until another rule matches.

Measured consequence, reproduced on five trigger shapes: one DROP rule for
`PROGRESS PHOTOGRAPHS` drops the executive summary, the critical path narrative,
the weather log and the timesheets — four HIGH-risk categories from §4 — and the
drop log attributes each of them to `PROGRESS PHOTOGRAPHS`. The worst shape needs
no confusing text at all: a **correct** first match with no rule marking the
section's end runs to the end of the document.

That inverts §1's binding asymmetry — the recognizer does not miss a section, it
invents one. **D-35 rules it replaced rather than repaired**: section resolution
moves to Tier 1–3 page *spans* and Tier 4 explicit ranges, and regex heading
matching and carried section state are deleted. Full reproduction and exposure
analysis in `docs/verification/sections_2026-08-17.md`.
