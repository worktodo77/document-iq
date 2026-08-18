# DocIQ Decision Register

Rulings by Alex Bachowski, harvested one-at-a-time per the pre-handoff decision protocol.
Status: D-01 through D-06 RULED 2026-07-30; D-07 OPEN.

| # | Decision | Ruling | Date |
|---|---|---|---|
| D-01 | OCR engine (§3/§10 said Tesseract; reused MIP 3.9 code uses rapidocr) | **rapidocr + Sprint-1 bake-off** — build on rapidocr; empirically compare both engines on ~20 real scanned MPR pages against hand-checked ground truth in Sprint 1; swap to Tesseract only if it wins decisively on the actual corpus. Bake-off report becomes a methodology artifact. | 2026-07-30 |
| D-02 | Legacy .doc support (§14.4) | **Tier 2 for v1, flagged loudly** — .doc files inventoried/hashed on the Unsupported list with a "Save-As DOCX/PDF to include" remediation hint in the run summary. No COM automation, no bundled converter. Promote in v2 if matters demand. | 2026-07-30 |
| D-03 | Token estimator basis (§14.3) | **Calibrated chars-ratio, shown as a range** — ratio calibrated during development against the real Claude tokenizer on representative MPR text (expect ≈3.3–3.6 chars/token for table-heavy content); display a conservative range (e.g. "≈ 82–94K tokens"). No bundled foreign tokenizer. | 2026-07-30 |
| D-04 | Doc ID scheme (§14.2) | **Adopt the LI File No. when a master index is supplied** (ruled against recommendation, with mitigations). Audited on the real Project 495 index (9,259 rows): "Original Sort" is a perfect 1..9,259 sequence (0 gaps/dups); filepath+filename is 100% unique (filename alone is NOT — 693 dups); NO Bates column. Scheme: Doc ID = `LI-<zero-padded Original Sort>` for rows matched to the master index (match key: filepath+filename primary, hash secondary, Bates when present); container children (zip/RAR members, email attachments — no LI row exists) get parent-derived IDs (`LI-06881.01`); unmatched folder files get a distinct `DIQ-` synthetic range (never collides with LI numeric space). Mitigations binding on the build: (a) the master index is a HASHED RUN INPUT — determinism contract becomes "same folder + same profile + same index = byte-identical"; running without the index yields DIQ-native IDs and the log records which regime; (b) processing_log records the index snapshot filename+hash; reconciliation WARNS if a later snapshot renumbers IDs already issued. | 2026-07-30 |
| D-05 | Profile library location (§14.1) | **Configurable folder, local default** — default `%APPDATA%\LI DocIQ\profiles`; settings field may point at a shared LI drive path. Per-matter copy in the output folder happens regardless. | 2026-07-30 |
| D-06 | Product name (§14.5) | **LI Document IQ** confirmed (short form "DocIQ"). | 2026-07-30 |
| D-07 | UI design direction (§9) | **Concept C "Counsel docket" + Concept B's capacity gauge** — light editorial chassis: white ground, hairline grid, oversized token headline, flags as chips; with the token capacity bar (fill + capacity marker + "% of direct-context capacity" mono caption) under the headline, and the "offline — no network" indicator in the window chrome. Accent = LI light blue #2E9FD4 with navy #0E4D80 structure (brand art supplied 2026-07-30); LONG INTERNATIONAL wordmark (`assets/branding/li_logo.png`) drawn in the window header, PDF Cleaner-style. Concept sketches approved in-chat 2026-07-30. | 2026-07-30 |
| D-08 | Explorer icon concept | **Concept 2 "Fanned corpus stack"** — LI monogram tile (white monogram + globe on navy, per family grammar) with a fanned three-page stack overlapping lower-right, front page carrying a light-blue "IQ" tab. Generated from `assets/branding/li_monogram_source.png` via the PDF Cleaner `make_brand_icon.py` recipe (multi-size .ico 16–256 px + preview ladder). Below 64 px the overlay simplifies per the family's small-size rule — collapse the fan to a single page silhouette, no lettering — judged on the ladder, same silhouette and color blocking throughout. | 2026-07-30 |

| D-09 | GUI logo lockup | **L1 "App badge lockup"** — the D-08 icon tile (fanned corpus stack, high-res render from the icon generator) at left of the window header, beside "Document IQ" (navy, "IQ" in light blue) over a letterspaced LONG INTERNATIONAL caption. One mark shared across Explorer, taskbar, and window header. Composed deterministically from `li_monogram_source.png` + typeset name; rendered to a shipped PNG in `assets/branding/`; nothing hand-drawn, brand refresh = re-run. | 2026-07-30 |

| D-10 | Roadmap velocity revision | **2 sprints, 3 parallel tracks, 2 Codex reviews** (adopts the p6-kernel endgame pattern). Contract-first: `pagemodel.py` frozen day one; Tracks A (ingestion spine), B (identity + deliverables), C (GUI shell + branding) build concurrently in worktrees; Codex #1 at pipeline-core integration, Codex #2 = merge gate. Scope trims on Project 495 evidence: RTF → Tier 2 (zero occurrences), RAR → Tier 2 listed-only (9 occurrences). Determinism proof, page-accounting gate, and Track-A critic depth explicitly NOT compressed. | 2026-07-30 |

| D-11 | Build environment / dependency location | **Dedicated venv at `document-iq\.venv`** — the full declared set (pypdf, pymupdf, rapidocr-onnxruntime, onnxruntime, opencv-python-headless, python-docx, openpyxl, extract-msg, python-pptx, xlrd, reportlab, PySide6, PyYAML, numpy, Pillow, pytest) installed there, not into system Python and not shared with the mip39 venv. Closes the reuse audit's §5 undeclared-dependency gap and keeps DocIQ decoupled from the mip39 repo's environment. Verified 2026-07-30: all 15 imports green on Python 3.14.5; `onnxruntime==1.28.0` publishes cp314 wheels, so there is no interpreter-version wall. | 2026-07-30 |
| D-12 | OCR bake-off corpus (D-01 / acceptance criterion 9) | **The real MODEC/Petrobras MPR corpus**, supplied by Alex mid-session at `Desktop\Petrobras\Petrobras\Project FIles`. Supersedes the interim "MNFV now, re-run on MODEC later" ruling taken minutes earlier when no MODEC set could be found — that two-cycle path is cancelled, the bake-off is **not** provisional, and D-01 is ruled at the end of Sprint 1 as originally planned. Corpus: 2.6 GB, 298 PDF / 53 DOCX / 17 PPTX / 7 DOC; 17,732 PDF pages, 461 scanned (2.6%), 21 mixed native+scanned PDFs, 0 fully-scanned. The ~20 bake-off pages are sampled from the scanned pages of `CER-1-145.pdf` (175 scanned of 222) and `CER-1-113.pdf` (91 of 228). Client data — never committed; only summary numbers quoted. | 2026-07-30 |
| D-13 | Bates acceptance corpus (acceptance criterion 4) | **The MNFV initial-disclosure production** (`Desktop\Files for Claude\20240529`) — genuinely Bates-stamped and image-only (e.g. `MNFV 0391 - 0696`, 306 pp; `MNFV 02684 - 02705`, 22 pp), so it is the only local set that can exercise the ≥99% Bates-accuracy criterion. A footer-zone probe over all 298 Petrobras PDFs found **no Bates stamps**, confirming §4 Stage 3's "absence is normal, not an error" against a real corpus — so the Petrobras set doubles as the negative case (Bates detection must return `None` throughout without flagging an error). Client data — never committed. | 2026-07-30 |

| D-14 | Token capacity headline design (refines D-07's "capacity gauge") | **Concept B — the reduction waterfall, with clickable rows.** BEFORE → AFTER headline over a stack of shrinking bars, one per reduction lever, with capacity as a fixed dashed reference row. **The rows are the section picker**, not a readout — clicking a lever toggles KEEP/DROP and the waterfall re-flows live, so the picture and the next action are the same object. Expert-approved drops render as interactive accent rows; the tool's own automatic savings (duplicate removal, page furniture) render as a locked muted row and are **never merged into the same number** — the profile system exists to keep "the expert approved this omission" distinct from "the tool did this mechanically". Concepts A (paired rails), C (capacity units) and D (log ruler) were shown and not selected; C remains the strongest option if an explain-to-a-third-party view is ever wanted. | 2026-07-30 |
| D-15 | Over-capacity is the expected state, not an error | On the real corpus (17,732 pages) the record does **not** fit direct context even fully reduced. The UI must therefore design the over-capacity case **first**, with no alarm treatment and no blocked action: state the shortfall factor plainly and pair it with the §8 Path B escape route (Expert Assist / Cowork reads the matter folder from disk, where the container limit does not apply). A dead end here is a design bug. | 2026-07-30 |
| D-16 | Action-button wording and screen sequencing | Alex judged the first sketch "not user friendly or intuitive": "Run" names a mechanism rather than an outcome, and the peer button "Review what gets dropped" read as a prerequisite step. Rulings: the primary button names the **outcome** ("Build the reduced corpus") with scope + time estimate beside it; there is **exactly one forward action per screen**; "Review what gets dropped" is removed as a button because reviewing drops is the whole economizing step, now carried by the D-14 waterfall itself. | 2026-07-30 |

## Open question — does local-folder reading dissolve the capacity problem? (raised by Alex 2026-07-30)

Partly, and the residue matters. Pointing Claude at a local matter folder removes the **container** limits — upload ceiling, project-knowledge cap, and the direct-context-vs-retrieval distinction. It does **not** remove the context window: 17,732 pages cannot occupy one turn, so the constraint changes shape from "does it fit in a box" to "how many turns, and how much drift, before the agent has seen what matters." Reduction still pays on that path, in turns and cost rather than in admission.

Consequence for the product's positioning, flagged: §7 and D-03 make the token estimate *the headline feature*, yet on the recommended route (Path B) it is the metric that binds least. DocIQ's durable value on that path is evidentiary rather than dimensional — original-pagination markers, Bates, the index, zero-discrepancy accounting, byte-identical reruns.

**RULED 2026-07-30 (D-17): keep the GUI as previously decided.** Three summary-screen treatments were drawn and compared — evidentiary lead with tokens demoted, a split headline, and the ruled token lead with an audit panel below. Alex ruled to **stick with the previous decisions**: §7 and D-03 are **not** amended, the token before/after remains the summary headline, and the evidentiary claims (page accounting, omission attribution, run fingerprint, index reconciliation, review flags) stay as a supporting panel. The evidentiary-lead proposal is **declined, not deferred** — do not re-open it in Sprint 2 without a new instruction. D-07, D-14, D-15 and D-16 all stand unchanged; Track C's brief already reflects them and required no correction.

| D-18 | §7 page-marker format — keep or compact | **KEEP §7's format unchanged.** Alex ruled "measure it properly first"; the measurement settles it against changing. Full-corpus measurement (all 298 PDFs / 17,732 pages, via Track B's pre-token proxy): markers are **0.5% of the corpus without Bates, 1.2% with**. Compaction buys almost nothing — `[p1234]` and `===== PAGE 1234 =====` both yield **5 pre-tokens**, because pre-token count tracks the number of symbol runs, not string length; only the Bates variant compacts at all (13 → 10). A two-repo change against DocIQ *and* Expert Assist's parser to recover ~0.5% is not worth it. **Correction on the record:** the initial estimate that framed markers as the single largest economization lever used a chars÷3.5 approximation and was wrong in framing — the absolute figure (~230K pre-tokens for the Bates form) held, but the share did not. Character compaction does not imply token savings. | 2026-07-30 |

| D-19 | Tesseract / the D-01 bake-off | **Written off — rapidocr is the engine, full stop (Alex, 2026-07-31).** D-01's conditional swap and acceptance criterion 9's comparison are both **cancelled, not deferred**: there is no pending Tesseract evaluation, and no future sprint owes one. Tesseract was never installed (installing it needed authorization that was not given, and the build correctly refused to install it unilaterally), so the Sprint-1 bake-off is a **rapidocr characterization** rather than a comparison — and that is now its final form. Measured on 20 real scanned MPR pages from D-12's corpus: mean page confidence **0.8628**, 3 of 17 pages below the 85% review threshold, 37% of *lines* below it, **5.74 s/page**; 3 zero-character pages verified genuinely blank (uniform white, no embedded images) rather than misses. `docs/bakeoff/ocr_bakeoff_2026-07-30.md` stands as the methodology artifact D-01 asked for, retitled to what it is. **Consequence to state plainly rather than bury: §3 and §10's amended wording already name rapidocr, so nothing in the build changes — but the tool now ships an OCR engine chosen on in-house familiarity and ONNX bundling convenience, never benchmarked against an alternative on this corpus. If OCR quality is ever challenged, that is the honest answer, and "Tesseract is the industry-recognizable name for law-firm IT review" (the original argument for the bake-off) remains unaddressed.** | 2026-07-31 |

## Sprint-3 kickoff rulings (Alex, 2026-08-17)

| D-33 | Sprint 3's scope | **BUILD THE OMISSION TAXONOMY (D-24).** Chosen over: driving the GUI with a human first; reopening D-19's Tesseract benchmark for criterion 4; and closing the carried risks (B-8, the offline probe). The reasoning that carried it: the reduction feature is fully designed, measured against the real corpus, and **not built** — DocIQ ships one profile, "keep every page" — so the taxonomy is the difference between a tool that reduces a corpus and one that only measures it. The measured evidence is already in hand (schedule tables 33.9%, furniture 8.0%, TOC 5.4%, photographs 0.2%). **The other four candidates are deferred, not declined**, and B-8 in particular stays where D-32 put it: open, owner-accepted, and NOT to be reopened as a side effect of this sprint. | 2026-08-17 |

| D-34 | The approver problem (`section_taxonomy.md` §6, first bullet) — how a shipped template satisfies `SectionRule.validate()`'s "who approved" check without naming an expert who never saw the matter | **TEMPLATES SHIP UNENGAGED; APPROVAL IS CAPTURED AT THE MOMENT THE EXPERT ENGAGES THE LEVER.** A built-in template carries **no approver and no live DROP**: every lever arrives OFF, and the template's contribution until somebody ticks something is *recognition only*. When the expert engages a lever, DocIQ writes his identity, the time and the matter into the rule and into every drop-log line it produces. Two properties follow and both are the ruling: **a template alone can never drop a page**, and **the approver field never holds a fiction** — it is either a real name that a real person put there by acting, or the rule is not a DROP. Rejected: shipping §4's Default column live with LI recorded as *author* and the expert as *approver* (faster, and it makes "the software's default" the sentence an expert has to defend under cross-examination, having approved LI's judgment by not looking); and recognition-only templates carrying no dispositions at all (safest reading of §1, but it leaves the reduction feature needing hand-authored YAML per matter, which is the gap D-24 was raised to close). **Consequence for the build:** `SectionRule.validate()` today accepts any non-empty free-text `notes` as the approver record. Under this ruling the approver becomes a *structured* field with a real capture point, and an unengaged template rule must be representable without one — which means the KEEP/DROP disposition can no longer be baked into the shipped template file. | 2026-08-17 |

### What the taxonomy sprint found before writing any of it (2026-08-17)

Recorded here because it changes D-24's size and was not visible when D-24 was
written.

**The shipped Stage-4 engine implements the one recognition mechanism the
taxonomy excludes.** `profiles/apply.py` matches a regex against every line of
every page and carries the matched section forward until the next match, while
`section_taxonomy.md` §2 measured that heading-shaped matching returns
`FPSO ALMIRANTE BARROSO MV32` (1,017) and `PETROBRAS` (981) at the top of its
frequency list and struck the tier out on that evidence. Of the four tiers the
design proposes:

- **Tier 1 (the document's own PDF outline)** — the strongest tier, and the one
  §2 calls "a lookup, not an inference" — **has no implementation anywhere in
  the tree.**
- **Tier 2 (TOC parsing)** — proposed, unbuilt, and §6 records it as unmeasured.
- **Tier 3 (measurable page classes)** — unbuilt. `profiles/detect.py` is a
  different thing: a shallow line-shape/recurrence detector that *proposes*
  candidates for a human checklist, and it is the same family of heuristic §2
  measured returning letterhead.
- **Tier 4 (explicit page ranges)** — `SectionRule` is regex-only and cannot
  express a page range.

**Zero built-in profiles ship** (there is no profile YAML on disk), and
`PageRecord` carries no `section_tier` field, which §5.4 requires and which is a
frozen-contract amendment.

So D-24 is not "author the templates". It is **build the recognizer the
templates key to**, and the shipped engine is the tier that was excluded rather
than a foundation to extend.

| D-35 | The shipped Stage-4 engine drops the wrong pages, as a class | **REPLACE IT WITH TIER-BASED SPANS (Alex, 2026-08-17).** `profiles/apply.py` matches a rule's regex against every line of every page and carries the matched section forward until another rule matches — so a rule fires on any line that merely *mentions* its section, and the carried state then governs every following page. Reproduced on **five trigger shapes** with a single DROP rule for `PROGRESS PHOTOGRAPHS`: the document's own table of contents, a body-text cross-reference, a transmittal listing enclosures, an appendix cover sheet, and — the worst, needing no confusing text at all — **a CORRECT first match where no later rule marks the section's end**, which runs the drop to the end of the document. The pages lost in the reproductions are the **executive summary, the critical path narrative, the weather log and the timesheets**, four of the categories §4 grades HIGH risk, and the drop log attributes every one of them to `PROGRESS PHOTOGRAPHS` — an audit trail that is complete and is a misdescription. This inverts §1's binding asymmetry: the recognizer does not miss a section, it **invents** one. Ruling: section resolution moves to Tier 1–3 page **spans** and Tier 4 explicit ranges; regex heading matching and carried section state are **deleted**. Rejected: bounding the rules instead (refusing to load a profile whose DROP sections have no end) — it keeps the mechanism §2 measured returning `FPSO ALMIRANTE BARROSO MV32` (1,017) and `PETROBRAS` (981) as its top matches, and does not touch the three shapes where the text merely mentions a section name; and suppressing matches on TOC pages, which is the repro fix for one of five shapes and is how the same defect returns under a new number. **Exposure stated precisely: `apply_profiles` is wired into the real pipeline (`pipeline.py:1435`) and no test covers the carry-forward path — but no built-in profile ships and there is no profile YAML on disk, so the code is reachable only by a hand-authored profile in the D-05 library, and nobody has authored one. Live and unexercised, which is exactly why it must be settled before templates make it exercised on every run.** | 2026-08-17 |

## Sprint-4 kickoff rulings (Alex, 2026-08-18)

Scoped after a read-only survey of every candidate. **Every figure below was
re-verified against the code before the question was put** — the first fan-out
that was supposed to produce this survey FAILED (all five investigators errored
on worktree creation, because the session's working directory is not the repo),
and its synthesiser correctly refused to pretend otherwise. What follows rests
on a second, working survey plus direct checks.

| # | Decision | Ruling | Date |
|---|---|---|---|
| D-41 | Sprint 4's objective | **MAKE IT SHIPPABLE TO A HUMAN.** D-38 and D-39 are already ruled and land regardless; what the sprint is FOR is getting a packaged product into Alex's hands and having him drive it. Chosen over chasing criterion 4, over closing the carried risks, and over finishing the taxonomy alone. The reasoning: Sprint 3 built a reduction feature that **no human has ever used**, `python -m dociq.selftest` covers **none** of it (measured: `bates`, `omission`, `waterfall`, `approv` and `template` appear **zero** times in `selftest.py`), and no `.exe` has been built since `sections/` came into existence. | 2026-08-18 |
| D-42 | What happens to `profile/*.yaml` in existing matter folders when D-38 stops DocIQ writing it | **TOMBSTONE RETIRED NAMES.** A name this build no longer writes stays in `_STALE_PATTERNS` permanently, marked retired, so the tool still knows how to remove it. **This is B-8's first real instance and the survey is what surfaced that:** `profile/*.yaml` is a live emitted deliverable (`pipeline.py:686`), and across the four commits that have ever touched `_STALE_PATTERNS` **no name has ever been retired** — the list has only grown. B-8 has been harmless because nothing ever triggered it; D-38 triggers it. Rejected: leaving it (B-8's first instance would arrive in a client's matter folder rather than in a document), and re-implementing the durable inventory D-32 deleted (it closes the general case and re-adds a file under `.dociq/` that governs deletion — the class that produced six generations of data-loss defects). The tombstone closes every case DocIQ can itself create, which is all of them. | 2026-08-18 |
| D-43 | The first human-driven session | **THE PACKAGED `.exe`, ON THE PETROBRAS/MODEC CORPUS.** Not the checkout: driving the checkout proves the code, driving the `.exe` proves what would be handed to a colleague — and `packaging/DocumentIQ.spec` has not been touched since 2026-08-03, so it has never been run against a tree containing `sections/`. Not MNFV first: it exercises Sprint-1/2 surface while the taxonomy barely fires on it. **The deciding reason is checkability** — every figure the screen shows on Petrobras/MODEC can be checked against a measurement already on the record (63.01% of pages placed by outline, ~70% recognized in total, ~30% kept unconditionally), so a wrong number is visible rather than merely plausible. | 2026-08-18 |

### What the survey established, and one thing it un-established

* **Criterion 4's blocker is policy, not recognition.** D-28's prefix repair is
  built and accepts every misread this corpus produces — and it **refuses to
  run**, because the misreads themselves register as extra prefixes
  (`refused_reason`: *"3 proposable Bates prefixes ('iCON', 'iiCON', 'jiCON')"*).
  MNFV's documents are mostly one page, so one misread page reads as 100%
  document coverage and becomes a second series. On exactly the hard case the
  rule cancels itself.
* **And nobody has established the ceiling is reachable.** OCR pages are at
  29/80 (36.25%); ≥99% overall needs 74/80. The claim that most residual pages
  carry a repairable token is labelled *"a prediction from measured token data"*
  in its own document, and the itemized evidence covers 22 pages. **Measuring it
  needs no code change** — the 51 residual pages are named in the committed
  artifact. That measurement should precede any decision to fund criterion 4.
* **No inter-process lock exists anywhere in `src/`** (zero hits for any locking
  primitive), and `staging_layout()` unconditionally removes `.dociq/staging/`.
  Two runs against one matter folder is undefined. Classified a CORRECTNESS risk
  rather than a claim risk, and deferred by D-41 rather than closed.
* **The one screen that blocks on a person has never been looked at.**
  `SCREENS` in `tests/test_gui_screen_states.py:350` omits `bates`, and the grid
  asserts its own completeness *against that list* — so "every screen × every
  state" passes over a hole. None of the 18 rendered PNGs is the Bates prompt.

## Sprint-3 close-out rulings (Alex, 2026-08-17)

Harvested one at a time after the sprint went green, per the standing pre-handoff
protocol. Four ratify what was built; **D-38 and D-39 are new work for Sprint 4.**

| # | Decision | Ruling | Date |
|---|---|---|---|
| D-36 | What §6's pre-run checklist describes, now that a profile's rules decide nothing | **THE TEMPLATE.** The screen lists the shipped template's families — 19, of which 11 are offerable and 8 are recognized and never offered — with each family's risk grade and its stated cost. Rejected: rendering the profile's rules marked inert (it makes the pre-run screen a museum of a deleted engine and still shows the expert nothing about what DocIQ will actually recognize), and removing the screen until Sprint 4 (it deletes the only place an omission is reviewed BEFORE a run, which is what §6 exists for). | 2026-08-17 |
| D-37 | Whether the approver belongs in the hashed run identity (A-19) | **YES.** Two runs over one folder differing only in WHO approved the omission are not byte-identical. The determinism claim becomes *same folder + same index + same approvals by the same person*. The reasoning is that the drop log names the approver, so the bytes genuinely differ, and an identity calling those runs identical would assert sameness about two records that disagree on who is answerable. Rejected: hashing the omission but not the approver, which reintroduces exactly the log-differs-identity-does-not gap A-08 was raised to close. | 2026-08-17 |
| D-38 | The profile system, whose engine D-35 deleted | **DELETE IT FROM THE PRODUCT IN SPRINT 4.** The YAML library, the setup-screen picker and the profile fields in the run record all go; one concept (the template) does the job two were doing. **Consequence accepted on the record, having been stated before the ruling: every matter folder already on disk has a run identity computed WITH its profile snapshot in it, so those runs will not reproduce byte-for-byte after the removal.** Chosen over leaving them as an inert recorded input (this sprint's state, which costs no work and leaves the product carrying two concepts) and over re-purposing the profile file to hold the matter's project names. | 2026-08-17 |
| D-39 | Where the matter's project tokens come from — 30.5% of the measured corpus's section vocabulary carries project text (`MV32 APPENDICES`, `STATUS OF PETROBRAS TQ`, `BOMESC YARD`) and no screen supplies the list | **DERIVE THEM AUTOMATICALLY FROM THE CORPUS.** Chosen over a setup-screen field and over shipping the gap disclosed. **The risk was stated before the ruling and is real** — §2 measured what an unmeasured recognizer does on this corpus, returning `FPSO ALMIRANTE BARROSO MV32` (1,017) at the top of its frequency list — but it is *bounded in a way that matters*: stripping a token can only make a section MATCH a family, and under D-34 nothing drops without a human engaging the lever, so a wrong token costs an OFFER, never a page. **Two constraints bind the Sprint-4 build:** the derivation must be deterministic, because the token list is a hashed run input (A-19); and the derived list must be SHOWN to the expert, so "DocIQ treated MV32 and PETROBRAS as project names rather than section names" is a visible statement he can correct rather than an inference. | 2026-08-17 |
| D-40 | Tier 3's instance-level evidence, lost when spans are rebuilt from stamped pages | **ACCEPT THE LOSS; THE TIER IS WHAT MATTERS.** The drop log says *"a page-class rule placed this page in 'Schedule / activity table'"* rather than *"17 dd-Mmm-yy dates on one page"*. §5.4 requires the KIND of evidence per page and that survives on every page — an expert can still distinguish "the document's own outline placed it" from "a page-class rule matched it", which §3 calls the quiet lie when it is missing. Rejected: amending the frozen `DocumentRecord` to carry spans (full fidelity, but a contract change plus the resume serializer plus the log, in a finished sprint), and recomputing the evidence at Stage 4 (a SECOND implementation of the page-class rules that would drift silently from the first). | 2026-08-17 |

### D-34 and D-35 wired, and what the wiring found (2026-08-17)

Full record: `docs/verification/sections_wiring_2026-08-17.md`.

`profiles/apply.py` is deleted. Recognition (Tiers 1 and 3) runs at extraction,
where the file is open; `walker._record` stamps `section` and `section_tier`;
Stage 4 rebuilds the spans from the stamped pages and drops only what an
`ApprovedOmission` naming a person authorizes. Measured end to end: a template
with no approvals recognizes three sections and drops nothing, and one approval
drops pages 3-4 and **stops there** — the shape the old engine ran to the end of
the document.

**A-19 raised, and it is A-08 again.** A-08 put profiles in the run identity
because they decided which pages dropped. D-34/D-35 move that decision to an
approval, so approvals became the deciding input and sat outside the identity
exactly as profiles once had. Not a hypothetical: a template ships unengaged, so
"run, engage a lever, run again" is the ORDINARY path, and those two runs
differed in their corpus and in nothing the identity could see. `RunConfig` gains
`omissions`, `project_tokens` and the template id/version; CONTRACT_VERSION
1.8.0. The claim narrows and correctly — two runs differing only in WHO approved
are no longer byte-identical, because the drop log names the approver.

**Two defects the wiring introduced and closed**, both recorded because each was
a true statement turning false one layer away from where it was edited: the index
would have printed a template id under a column headed "Omission approved by",
and widening `ReductionLever.locked` routed every recognized-never-offered
section — the weather logs, the timesheets, the manpower histograms — into the
waterfall row whose hint reads "Removed mechanically by the tool", while those
pages were kept.

**One decision taken while wiring rather than ruled, flagged for Alex.** §6's
checklist now describes the TEMPLATE rather than the profile. Rendering profile
rules there would print "DROP" beside a rule that drops nothing, on the one
screen whose purpose is approving omissions before a run commits to them.

### The three measured answers (2026-08-17)

Full record: `docs/verification/sections_2026-08-17.md`. Probe:
`tools/measure_sections.py`. Corpus: all 298 PDFs / 17,732 pages, read-only —
the page count reproduces D-12's independently-established figure exactly.

| §6 question | Answer |
|---|---|
| **Tier 1 reach** | **29.866% of documents — but 63.01% of PAGES** (11,173). The taxonomy's "40% of PDFs" was a 36-doc sample and is withdrawn everywhere it was asserted. Vocabulary: **522 distinct section families** (716 raw), too many to enumerate — templates key to a normalized family, not a list. **159 of the 522 (30.5%) carry project-identifying text** (`BOMESC YARD`, `MV32 APPENDICES`), so project tokens are stripped before matching and their list is supplied **per matter, never shipped** — D-24 forbids a template attributable to a corpus project. And the corpus's third most frequent label is **Portuguese** (`PAGINA EM BRANCO`, 61): matching is accent-folded and no template may assume English. |
| **Tier 2 feasibility** | §6's doubt **not reproduced**: offset 0 in all 106 paired lines across 25 documents, within-document spread 0. **But the check reaches only documents that do not need Tier 2** — it requires an outline as ground truth, so the ~193 documents with a TOC and no outline are structurally unreachable. Risk lowered, question open. |
| **Coverage of the rest** | **No.** Tier 3 resolves 20.66% of the 6,331 pages in unoutlined documents and leaves **79.34% resolved by nothing**. Corpus-wide the tiers recognize **~70%** of pages and keep the other **~30%** unconditionally. That is the honest ceiling on the reduction feature and should be the claim the product makes. |

**One instrument correction worth carrying.** The first run of the Tier 2 probe
paired **3 lines out of 3,484** and read as decisive evidence against building
the tier. It was a comparison bug — a TOC prints `3.1 EXECUTIVE SUMMARY` where
the outline says `EXECUTIVE SUMMARY` — and stripping leading section numbering
pairs 103. **A red result from a broken instrument is not evidence**, and this
one would have descoped a tier. The discipline that catches a green result
proving nothing has to run in both directions.

## MEASURED: the "~20 minutes per suite" claim was wrong by 4x, and it was our own doing (2026-08-14)

Timed on a verified-quiet machine, one full suite each:

| tree | full suite |
|---|---|
| pre-descope `2728c96` | **4m40s** |
| post-descope (merged) | **4m22s** |

**Two relays told Codex a single full pass exceeds ten minutes and that its
six-minute cap therefore could not see one finish. Both statements are false.**
At 4m40s the cap was adequate. What actually happened is that our own parallel
agents were saturating the machine underneath Codex's review — up to twelve
concurrent pytest processes at times — so the runs Codex could not complete, and
several we characterized as flaky, were starved by our fan-out rather than slow
by nature.

The test deletion explains **18 seconds of the gap**, not the four-fold
difference. This was contention, start to finish.

**Consequences worth carrying beyond this sprint:**

1. **Timing claims made while agents are running are worthless**, and we made
   several. Any figure quoted to a reviewer must come from a machine verified
   quiet, and must say so.
2. **We blamed the reviewer's tooling for our own load.** Codex was right not to
   count its capped run as green, and right again not to accept our explanation
   for why it capped.
3. **The offline-probe "flake" chased across three rounds was of this family** —
   an implementing agent proved it by building a six-way concurrent probe, which
   then passed 6/6, leaving the original two failures **unreproduced and
   unattributed** rather than closed. That is recorded honestly in §9.4 of
   `docs/verification/d32_descope_2026-08-06.md` and remains open.

## MERGE GATE PASSED — Codex review #2, 2026-08-14 at `57c7cc0`

**PASSED, no new findings.** D-10 designated this review the merge gate for the
whole programme. It ran **four verdict rounds plus two internal adversarial
reviews**, and every A/B finding is closed except one, which is open by ruling.

| round | findings | outcome |
|---|---|---|
| 1 | 2A / 3B / 1D | all closed |
| 2 | 2A / 2B / 1D | all closed |
| 3 | 1A / 2B | B-7 closed; A-5, B-6 moot under D-31 |
| 4 | 1A / 2B (A-8, B-9, D-3) | all closed |
| internal | F-1..F-6, F-A..F-I | closed, or moot under D-32 |

**Codex's own closing words on the last three:** A-8 closed — *"the planner now
emits file paths only… I withdraw my stronger interpretation; that was not the
original TOCTOU property."* B-9 closed — *"safe guidance is pinned three
independent ways."* D-3 closed.

**Independent verification at the passing tip:** full suite **1,392 passed, 1
skipped in 4m53s**; focused A-8/B-9 tests passed; amendment registry green;
`git diff --check` green; worktree clean.

### What passes with the merge, stated as it is

- **B-8 is OPEN and owner-accepted.** Not mitigated, not closed. A deliverable
  an older build wrote under a retired name stays in the matter folder
  permanently. Codex: *"I would not hold the merge solely to restore the removed
  inventory."* It is a live risk carried by D-32's ruling, not a solved problem.
- **Criterion 4 NOT MET** (D-29). 92.130% is a projection; the last measured
  full-corpus figure is **91.512%**. Never quote the projection flat.
- **The offline-probe failures remain unreproduced and unattributed.** Codex's
  run passing them does not close them and neither side treats it as closing
  them.
- **The publication window is WIDER than the design it replaced**, permanently
  so, and no claim is made that the new design is correct — only that it is
  small enough to enumerate.
- Never observed on a real matter: an interrupted publication. Never performed:
  a mouse-driven GUI pass, and an upload actually accepted by a Claude Project.

**Merging `build/sprint-2` to `main` requires Alex's explicit authorization.
The gate passing does not confer it and it is never inferred.**

## D-32 EXECUTED — the swap is descoped (Alex, 2026-08-06)

**The sixth generation arrived, and the rule fired.** A second adversarial
review of the widened dispatch returned **nine findings (F-A .. F-I)**, one
MEDIUM-HIGH data-loss: `classify_swap` computes `landed` at `PHASE_PUBLISHED`
and never reads it, so a completed swap whose root deliverables are then removed
externally classifies as FINISH, records a set that is not there, and **deletes
the only complete set on disk**. The old build had an analogous refusal; it was
dropped without a replacement. And **F-C is F-3 left undone for its sibling** —
the argument for removing the directory collapse was made at length while
`_STALE_PATTERNS` still named `upload_package` as a bare directory, so the same
mechanism deletes an analyst's file saved there mid-run.

Alex ruled: **execute D-32 as written.**

**Counted honestly.** The implementing agent flagged, unprompted and against its
own interest, that F-7 was self-found *inside* the fix round rather than by a new
review, and asked that the count not be settled quietly in its favour. It is not
counted. The sixth generation is F-A..F-I, found by an independent review of the
code that fixed the fifth.

**Scope of the descope, decided at execution and open to correction:** what is
removed is the **multi-phase set-aside / rollback state machine** — `classify_swap`,
the `pending → aside → publishing → published` marker protocol, the durable
`published_set.json` inventory, and the roll-back and roll-forward paths. What is
**kept**, because each passed review on its own terms and none is part of the
unsettled machinery:

- **B-1's publication gate.** A red Stage 6 must still refuse to publish. That
  finding was accepted and its fix is not what failed to converge.
- **The package's assemble-in-`incoming` order** and A-6's recover-before-cleanup,
  which the sixth review checked and found sound.
- **A-16 / A-17's residue disclosure**, to whatever extent residue remains
  possible under the simpler design.

**What this costs, stated rather than discovered later.** Sprint-1's window is
**wider**: a crash between removing the previous deliverables and writing the new
ones leaves a partly-replaced matter folder. That is a bigger hole than any of
the nine findings above. It is accepted because it is **one** hole, it is
describable in a sentence, and six reviews have now shown the alternative hides
holes rather than closing them. The release note and the Codex relay must say
this in those words — a simpler design with a larger known risk, chosen over a
complex one with a smaller unknown one.

### DONE, on `build/s2-descope` (2026-08-06)

**The rule, in one sentence:** *publication deletes the previous run's
deliverables from the matter folder and then moves each staged file onto its
final name, in that order, once — with no marker, no set-aside copy, no
inventory, and no recovery.*

**The window, stated where the ruling asked for it:** a process that dies between
the first removal and the last move leaves the matter folder holding part of two
runs' evidence, **permanently** — nothing on disk records that a publication was
in progress, and no later run detects or repairs it. On the measured corpus that
window is thousands of file operations wide, so **seconds, not milliseconds**.
~~The complete new set survives under `.dociq/staging/`~~ **FALSE — withdrawn
2026-08-14, Codex finding D-3.** A file already moved is no longer in staging,
so once publication has begun the new set survives **split between the matter
root and `.dociq/staging/`**, and staging alone cannot reconstruct the run. The
claim is true only on the REMOVAL path, before any file has moved. The crash
tests assert staging *exists*; they never asserted it was complete, so the
tests were right and the sentence describing them was not. What is true: the
remainder survives under `.dociq/staging/` and the next run discloses
having found it (`run.state_residue_before_run`), which is the only trace.

`emit/paths.py` went **1,827 → 568 lines**. Kept, each on its own terms: B-1's
publication gate (and therefore the staging directory it audits), the package's
own publish order (A-6/A-7), residue disclosure (A-16) in the narrower form
residue now takes, and criterion 7.

**Two things the scope note did not anticipate, decided at execution and open to
correction.** First, **B-8's fix is withdrawn with the inventory that carried
it** — the set-aside plan is again this build's own output patterns only, so a
deliverable an older build wrote under a retired name stays in the folder
permanently. It is disclosed in every run log and pinned by a test that asserts
the file *survives*. Second, **A-16's seam field keeps a name that no longer
describes its contents** (`RunOutcome.superseded_residue`); renaming a wired seam
field is a contract amendment this descope had no authority to make, so the
mismatch is explained at both ends and flagged rather than fixed. **No amendment
was orphaned and no registry entry was deleted.**

Full record, including everything nothing supports:
`docs/verification/d32_descope_2026-08-06.md`.

## D-32 — the swap gets ONE more generation, then it is descoped (Alex, 2026-08-06)

**The cap.** The output swap has now produced five consecutive generations of
defects: Codex rounds 1 (B-1, B-2), 2 (B-4, B-5), 3 (A-5, B-6), 4 (B-8, A-6,
A-7), and our own pre-handoff review of round 4's fix (F-1..F-6, two of them
HIGH data-loss). Alex ruled at round four to keep fixing; this ruling adds the
bound he declined then, on evidence that had not existed yet.

**Fix F-1 through F-6. If a SIXTH generation appears in this subsystem, stop
fixing.** Descope the swap to Sprint-1's write-in-place behaviour, document
every known failure mode in the register and the release note, and merge the
rest of Sprint 2 on Alex's authorization. That is a trade of a **larger,
better-hidden** risk for a **smaller, well-understood** one: Sprint-1's window
is wider — a crash mid-emit leaves a mixed folder — and it is simple enough to
reason about completely, which four rounds have shown the current design is not.

**Why the bound is drawn here rather than at "when it is correct".** The
adversarial reviewer's diagnosis is the reason: *every row the state table
enumerates is sound.* The defects land in rows the table's axes cannot express
— `staging` is binary (holds files / empty), and the aside-tree axis does not
distinguish residue from a completed swap from **this** marker's partially
completed step 1. So each round enumerates, fixes what the enumeration can see,
and the next round finds what it could not represent. A process that cannot
represent its own remaining failure modes does not converge by being run again,
and "one more round" has been the answer four times.

**What this ruling does NOT say.** It does not say the current design is wrong,
and it does not authorize descoping now. Every failure found so far requires a
Windows lock or a process death at a precise instant, and none has been observed
in a real run. The fixes in flight are close and their shapes are known. This
sets the stopping rule *before* the next round rather than after it, which is
the whole point.

**One fact that belongs beside this ruling:** `commit_staging`'s central
guarantee — the whole previous set leaves the matter root before any new file
enters — is **conditional on `published_set.json` existing**, and that file does
not exist on the first run of this build against any pre-existing matter folder.
That is every folder at rollout. It is disclosed in `run.published_set_inventory`
and must be stated plainly in the relay and the release note rather than left in
a field nobody reads.

## Sprint-2 kickoff rulings (Alex, 2026-08-01)

| D-20 | Acceptance criterion 1 — how "loads into a Claude Project" is proven | **Split the criterion along the measured reality.** Criterion 1 as written assumes a matter fits a Project's direct context; Sprint 1 measured the real corpus at ~13.9–15.2M tokens, 70–100× that. **Path B (Expert Assist / Cowork reads the matter folder from disk) is proven at full scale — all 368 documents** — and is the route the criterion's "consumed by evidence-mining without format errors" clause is discharged on. **Path A (browser upload package) is proven on a deliberately scoped subset** (date- or type-scoped) that genuinely fits, and the acceptance note states the scope rather than implying full-corpus coverage. Rejected: uploading the full corpus and counting "no size rejection" as a pass — that would rest the criterion on retrieval-mode recall over 17,732 pages, which is unmeasured and would set an expectation for the analyst the tool cannot back. Criterion 1's wording is amended in requirements to match. | 2026-08-01 |

| D-21 | The reduction waterfall's capacity line (`DIRECT_CONTEXT_TOKENS`, UNCONFIRMED since Sprint 1) | **Keep 200,000, and render it as a NAMED, SOURCED REFERENCE LINE — "Claude Project direct context" — never as a budget or a target.** The constant stays exactly where the seam put it (one named symbol, no inlined literals) so a future change is one line. The waterfall must not imply that getting under the line is the goal: D-15 already rules over-capacity the **expected** state, and D-20 makes Path B the route that does not care about the line at all. Wording on screen must let an expert who drops four section types describe *what was dropped and why*, not "reduced to fit". Rejected: making the line operator-settable (a knob whose wrong setting silently rescales every figure on screen), and removing it (guts D-14's point — the operator loses the only signal that the reduction accomplished anything). | 2026-08-01 |

| D-22 | §10 "PyInstaller single exe" vs. the bundled ONNX payload | **AMENDED to a one-folder build shipped as a zip** (`--onedir`: `DocumentIQ.exe` plus its payload, unpacked once). The bundled OCR models push the payload past ~100 MB, and `--onefile` unpacks the whole of that to a temp directory on **every** launch — multi-second cold starts, and a temp-extract-then-execute pattern that is precisely what endpoint protection on a locked-down law-firm machine quarantines. A tool that does not open at a client site has no other qualities. §10's "single exe" wording is amended with this reasoning recorded; the deliverable is one zip, which preserves the intent (one thing to hand over) without the failure mode. Deferred, not rejected: an Inno Setup installer with the D-08 icon registered — it adds an installer toolchain and a code-signing question Sprint 2 has not budgeted. | 2026-08-01 |

| D-23 | Acceptance criterion 4 — how Bates ground truth is established on MNFV | **Sequence-continuity proof over the whole set + a hand-checked stratified sample of ~100 pages.** MNFV is image-only, so every candidate ground-truth number is itself OCR of a footer stamp; a sample alone cannot see a systematic off-by-one or a document-boundary error, and a full hand-check does not scale. So: machine-verify the *structural* property over every stamped page — each document's Bates range monotonic, gapless within the document, non-overlapping across documents, contiguous across the production — which catches the whole error class; then hand-verify a stratified sample against the page images for absolute correctness. The ≥99% figure is stated **with its method**, and misses are flagged rather than silently corrected (§4). The acceptance note must state plainly that ground truth is OCR-derived and what the continuity proof does and does not establish. | 2026-08-01 |

### CORRECTION to D-13 and D-23's premise — the MNFV ground truth is not all OCR-derived (Track F, 2026-08-01)

D-13 calls the MNFV set "image-only" and D-23 builds its whole method on the
consequence: "every candidate ground-truth number is itself OCR of a footer
stamp". **That is true of part of the set and false of the larger part**, and
the correction is recorded here rather than left in a verification note because
D-23's ruling text asserts it.

`Desktop\Files for Claude\20240529` holds two different things:

| set | documents | pages | ground truth |
|---|---|---|---|
| `20240510 Initial Discl` — a standard e-discovery production, prefix `iiCON` | 2,138 | 11,561 | its own **load files** (`.OPT` / `.LFP` / `.DAT` / `.LST`) — the producing party's authoritative page-level numbering, generated when the stamps were burned in, **not** a re-reading of them. The PDFs also carry the vendor's embedded text layer, so DocIQ reads their stamps on the native path, not through OCR. |
| `Initial Disclosures` + `Supplemental` — combined PDFs, prefix `MNFV` | 16 PDFs | 2,963 | **filename ranges only** — document-level and weak. D-23's caveat applies here in full, and was measured to bite: the filenames pad to four digits (`MNFV 0919`) while the stamp burned into the page pads to five (`MNFV 00919`). |

**D-23's METHOD is unaffected and was followed as ruled** — continuity over the
whole set, plus a hand-checked stratified sample. What changes is the strength
of the resulting number: the page-level figure for the 2,138-document
production rests on authoritative, non-OCR ground truth. The two sets are
reported separately and never averaged, because the average would be the
flattering figure and the less honest one. Detail and results in
`docs/verification/track_f_sprint2_2026-08-01.md` §4.

| D-24 | Built-in profiles — should DocIQ ship any DROP rules? | **Ship STANDARD TEMPLATES keyed to PAGE TYPE, not to any corpus project (Alex, 2026-08-01).** Track D shipped no built-in profiles at all, reasoning that `SectionRule.validate()` refuses a DROP rule without a "who approved" field, so a profile in the box would be an omission decision no expert made. The reasoning is sound and the outcome is not: an analyst opening a matter gets the evidentiary guarantees and none of the reduction until somebody authors YAML. Ruling: there **should** be standard templates for what *types of pages* get dropped, and they must **not be attributable to any of the corpus projects** — no "MODEC MPR profile", no "Petrobras CER profile", because a template named after a matter implies decisions taken on that matter. ⏳ **The template content is NOT yet designed** — Alex: "we will need to figure this out." Open sub-questions for a dedicated design pass: which page types are generic enough to be safe defaults (photo logs? transmittal sheets? distribution lists?); how a template satisfies the approver check without naming an expert who never saw the matter; and whether templates arrive pre-engaged or as an explicit opt-in the checklist forces the expert through. Until that pass lands, Track D's "no profile — keep every page" remains the only built-in, which is the safe direction to be wrong in. | 2026-08-01 |

| D-25 | Acceptance criterion 4 shortfall — 91.5% against a ≥99% bar | **Close it with TARGETED FOOTER RE-OCR, not an engine swap (Alex, 2026-08-01).** Track F measured criterion 4 against the MNFV production's own load files: **593/648 = 91.512% overall, 0 wrong, 0 false positives, 55 missed** — decomposing to **100.000% on native-text pages (568/568)** and **31.250% on OCR'd pages (25/80)**. Every shortfall is an absent locator, never an incorrect one. The residue is rapidocr failing to read a small footer stamp on a whole-page pass optimized for body text. Ruling: treat the Bates stamp as **its own recognition problem** — crop the footer zone and re-OCR it at much higher resolution with settings suited to a short alphanumeric string — rather than reopening D-19. Rejected: reopening the Tesseract benchmark (would reverse a ruling one day old and cost sprint time) and conceding the criterion on scanned productions (Bates detection exists precisely for that class of matter). **Recorded plainly: this is the first concrete cost of D-19's "the shipped OCR engine has never been benchmarked against an alternative", and the targeted fix may not reach 99% either — that will be known only after it is built and measured. If it does not, D-19 is back on the table on evidence.** | 2026-08-01 |

| D-26 | Build environment — PyInstaller and the opencv mismatch | **Both authorized (Alex, 2026-08-01).** Install `pyinstaller==6.20.0` into `document-iq\.venv` and declare it in `pyproject.toml` as a build-time dependency, replacing Track F's scratch-directory `PYTHONPATH` workaround — packaging is a Codex #2 merge-gate deliverable and "reproducible from a committed spec plus a documented command" was not true while the build depended on an undocumented staging step. Also remove the stray non-headless `opencv-python` that was installed alongside `opencv-python-headless` and winning at import, so the venv matches its declaration again. That mismatch is exactly the class of gap D-11 was created to close, and the packaged build has already demonstrated the failure mode concretely: it silently produced **no OCR at all** — models present, `ocr_available()` returning True, every scanned page empty — because an import resolved differently than expected. | 2026-08-01 |

## Correction — D-23's premise was wrong (2026-08-01)

D-23 ruled the Bates ground-truth method on the stated basis that "MNFV is
image-only, so every candidate ground-truth number is itself OCR of a footer
stamp." **That premise is false.** The MNFV production is a 2,138-document set
shipping `.OPT` / `.LFP` / `.DAT` / `.LST` load files and an embedded text
layer, so its ground truth is **authoritative**, not OCR-derived — Track F
scored criterion 4 against the load files rather than against its own reading.

The **method is unaffected and stands**: whole-set sequence continuity plus a
hand-checked sample is the right shape regardless of where truth comes from,
and the continuity half earned itself immediately — it found a real defect in
the client's own production (`MNFV 2836-2899` and `MNFV 2890-2953` overlap by
10 numbers), which was flagged and not corrected. What changes is the *stated
reason*: the acceptance note must not claim ground truth is OCR-derived when it
is not, and the blind hand-check (100/100 read, 0 disagreements) is
corroboration of the load files rather than the primary source it was ruled to
be.

Recorded rather than quietly amended in place, because the ruling was made on a
factual claim about client material and the claim was checked and found wrong.

| D-27 | Schedule / activity tables — the corpus's largest lever | **Offer as a DROP lever, DEFAULT OFF (Alex, 2026-08-01).** Measured over 36 real documents / 1,535 pages / 3.34M characters: schedule and activity tables are **33.9% of the corpus text across 258 pages** — the only category whose removal changes whether a matter fits, and roughly **170× the photographs**. They are P6 activity listings pasted into progress reports, and where the native `.xer` files are already in evidence the pasted grid is a lossy render of a better source. Ruling: the template recognizes them as a section type and offers them in the checklist with that reason stated on the row, but never drops them unless the expert engages the lever. Rejected: defaulting the lever ON where schedule files are present in the matter folder (it would make a substantive evidentiary decision on a file-presence heuristic, and a pasted table that *differs* from the native file is itself evidence); and refusing to offer them at all (forfeits the only lever that materially reduces this corpus and makes the reduction feature close to cosmetic). | 2026-08-01 |

### OUTCOME of D-25 — the targeted fix was built, and it does NOT close criterion 4 (2026-08-01)

D-25 ruled targeted footer re-OCR and recorded that "the targeted fix may not
reach 99% either — that will be known only after it is built and measured. If it
does not, D-19 is back on the table on evidence."

**It was built and measured. It does not, and here is the evidence.**

The pass works as ruled: on pages where the whole-page recognition returned
nothing, a 400 dpi tiled crop of the physical stamp band returns a reading. But
the reading is wrong in one specific, repeatable way — **rapidocr reads this
production's stamp DIGITS correctly and cannot resolve its PREFIX.** It returns
`iCON004926`, `jiCON004926`, `liCON002291`, `TiCON005000` for a stamp that reads
`iiCON004926`. Measured over pages drawn from the baseline's own miss list: at
400 dpi, exact recovery is **1 of 12** and **1 of 10** on two independent
subsets, with the six digits correct nearly every time.

**It is not a resolution problem, a cropping problem or a preprocessing
problem**, and each was tested rather than assumed: 600 dpi and 800 dpi score
*below* 400; widening the detector's box (`unclip_ratio` 2.2 and 3.0,
`box_thresh` 0.3) recovers nothing; Otsu binarization gains a little on the
prefix and truncates a digit run elsewhere. It is the recognition model.

That makes this the first hard, page-level cost of D-19's "never benchmarked
against an alternative on this corpus", and **D-19 is therefore back on the
table on evidence, exactly as D-25 provided for.**

**One alternative would close it without reopening D-19, and it needs a
ruling.** A confirmed production's prefix carries no per-page information — every
page has the same one — so a recovered token whose digits, digit width,
separator and suffix match the confirmed format exactly, and whose prefix is a
near-miss of it, could be normalized to the confirmed prefix. It could not point
at a different page. It was **not** done unilaterally: §4 requires misses to be
flagged rather than silently corrected, and `bates.py` deliberately holds that
`iiCON` and `IICON` are two formats and neither is applied to the other's pages.

Full method, the five defects the work found and fixed, the cost measurements,
and — importantly — the two measurements that did NOT complete (the harness
after-number and the Petrobras negative control, both blocked by machine
contention, both with their commands written out) are in
`docs/verification/bates_d25_2026-08-01.md`.

## Measured: where the tokens actually are, and what recognizes them (2026-08-01)

Design pass for D-24, over 36 documents / 1,535 pages / 3,337,999 characters of
the real record. Full analysis in `docs/design/section_taxonomy.md`.

| category (deterministically detectable) | pages | share of text |
|---|---:|---:|
| narrative & everything else | 1,140 | 52.5% |
| **schedule / activity tables** | 258 | **33.9%** |
| page furniture (repeated header/footer lines) | — | 8.0% |
| table of contents | 65 | 5.4% |
| photo / figure / divider pages | 54 | **0.2%** |
| empty / image-only | 18 | 0.0% |

**Two results that change the design.**

**Photographs are worth 0.2%, not the headline saving.** The Sprint-1 mockups
advertise "Photo logs" as the largest expert lever at −2.49M tokens; that is
wrong by about two orders of magnitude. A photo page carries almost no *text*,
and tokens come from text. Photo logs are a **page-count** lever, not a token
lever — and Path B, which D-20 makes the primary route, does not care about page
count. The waterfall must stop showing a token saving the corpus cannot support.

**A heading regex finds letterhead, not sections.** Matching heading-shaped
lines returns, in frequency order, `FPSO ALMIRANTE BARROSO MV32` (1,017),
`PETROBRAS` (981), `WEEKLY PROGRESS REPORT NO. #` (320), then document and
revision numbers. The obvious recognition mechanism is **excluded on measurement**
— it would have looked reasonable, passed review, and silently dropped the wrong
pages.

**What works instead: the document's own outline.** ~~40% of the PDFs~~
**CORRECTED 2026-08-17 — 29.866% (89 of 298) on the full corpus; the 40% came
from this pass's 36-document sample. The figure to carry is pages, not
documents: outlined documents are the large ones, so the tier places 11,174
pages, 63.016% of the corpus.** The PDFs that carry them carry PDF
bookmarks with a real section vocabulary — `EXECUTIVE SUMMARY`, `PROGRESS
PHOTOS`, `HSE`, `CRITICAL PATH NARRATIVE`, `APPENDICES`, `OVERALL PROGRESS
S-CURVE`. Where an outline exists the section→page map is a **lookup, not an
inference**, and it is the document's own statement about itself. Recognition is
therefore tiered — outline → the document's own TOC → measurable page-class rules
→ expert-entered page ranges — with the tier recorded per page, because the tiers
are not equally strong and rendering them identically would be this feature's
quiet lie.

**Honest consequence:** the real reduction story on this corpus is furniture and
TOC (13.4%, safe, largely automatic) plus schedule tables (33.9%, the expert's
call under D-27). Everything else is a rounding error. That is a smaller and more
defensible claim than the mockups make, and it is the claim the product should
make.

| D-28 | Bates prefix normalization vs. reopening D-19 | **Normalize ONLY where the matter has exactly ONE confirmed prefix (Alex, 2026-08-02).** D-25's targeted footer re-OCR was built and **did not close criterion 4** — 91.512% unchanged. The wall is precise and was characterized page by page: rapidocr **reads the digits correctly and cannot resolve the `ii` prefix**, returning `iCON004926`, `jiCON004926`, `liCON002291`, `TiCON005000` for `iiCON…`. Exact recovery at 400 dpi is **1 in 12**; 600 and 800 dpi score *below* 400; detector-box widening scores 0 of 10. Ruling: repair the prefix when digits, digit width, separator and suffix match the confirmed format exactly and only a near-miss prefix differs — **and only when the matter carries exactly one confirmed prefix.** On a multi-prefix production, refuse outright and flag as today. The third condition is the ruling: it makes the wrong-series failure **structurally impossible** rather than merely unlikely, and the risk is concrete rather than theoretical — Track F found the MNFV set carries three prefix renderings, and a page filed under the wrong series is a locator an expert cites verbatim and cannot defend. Normalization is **disclosed, never silent**: §4's "flagged, not silently corrected" stands as the rule and this is a narrow ruled exception, so every repaired locator is recorded and distinguishable from a directly-read one. **Consequence to state rather than bury: if MNFV's acceptance subset is multi-prefix, D-28 refuses on it and criterion 4 remains NOT MET on the acceptance corpus** — the criterion would then be met on single-series matters and open on the hard case. D-19 was considered and not reopened; Tesseract stays written off, and the "never benchmarked against an alternative" liability recorded in D-19 now has its **first concrete page-level cost** on the record. | 2026-08-02 |

### OUTCOME of D-28 — built as ruled; the gate decides the acceptance corpus (2026-08-02)

D-28 is implemented in `dociq.identify.bates` with all three ruled conditions
plus a fourth that follows from where the damage comes from, and the whole thing
is reachable only through `apply_bates_reported`, which returns the disclosure.

**The distance rule, written so an expert can apply it by hand.** A read prefix
is a near miss of the confirmed prefix when EXACTLY ONE of these holds:

* **substitution** — same length, differing at exactly one position, and the two
  characters there are in the same stated confusable group (`jiCON` for
  `iiCON`; the first group is thin verticals `i I j J l L 1 | ! t T f r`,
  which is the class this corpus produced);
* **a doubled character read as single** — the read is the confirmed prefix with
  one character deleted, and that character was identical to its neighbour
  (`iCON` for `iiCON`);
* **a single character read as doubled** — the same in the other direction.

Refused: two or more edits; any edit touching a **digit** (with a separator-less
format the prefix abuts the number, and collapsing `iiCON0` to `iiCON` would
move a digit out of a seven-digit number and produce a locator for a page that
does not exist); an insertion or deletion that is not a doubling; and **a pure
case change** — `iiCON` and `IICON` are two formats by deliberate rule elsewhere
in this module, and repair must not fold them back together through a side door.

**Three defects were found by writing the tests.** The case-only acceptance
above was one. The second: a caller that streams documents one at a time — the
acceptance harness does, to bound memory — silently asked the single-prefix gate
about ONE DOCUMENT rather than the matter, which is exactly how a partial view
reports "one prefix" for a matter that has four; the census is now an explicit
parameter and the harness computes it over the whole sample. The third: the
census uses the same grammar as detection, and that grammar reads `sheet 137` as
prefix `sheet`, so ordinary numbered page text can register as a second
"prefix" and switch repair off. That is the conservative direction and it is
left as it is, but it is recorded because an operator reading the refusal should
not be surprised by a prefix that is an English word.

**The gate is what decides the acceptance corpus, and the answer is measured
rather than argued** — see `docs/verification/bates_d25_2026-08-01.md` §3 for
the census over the MNFV subset and what criterion 4 does as a result.

## Acceptance criteria 1 and 8 — DISCHARGED on the real corpus (2026-08-02)

One full-corpus run from scratch through `RealPipeline` (what the GUI calls),
shipped defaults, no environment overrides, not resumed.

- **6,182.4 s = 103.0 min**; 368 documents, 7 listed-only (`.doc`, per D-02)
- **18,556 pages in = 18,556 kept + 0 dropped — zero discrepancy**
- 17,266,810 pre-tokens / 50,251,852 chars; `corpus_sha256 b326d92e…`
- Stages 1–2 = **99.70%** of the run; everything DocIQ adds after extraction = **18.5 s**
- **Path B**: all four deliverables found in place, no rearrangement; §7's format
  contract asserted over the whole corpus — 18,556 markers parsed (equal to
  `pages_in`), `sources.json` resolving for all 368 — **zero format errors**.
- **Path A**: whole-record package 371 files / 51.2 MB, plus a scoped subset
  chosen through the GUI's own scope model — **15 of 368 documents, 0.57 MB,
  154–168K tokens**, against 206 date windows measured of which 14 fit.

**The gap found on the way: amendment A-12 was raised by Track D *and* Track E
and applied by neither.** `RealPipeline` had no `matter_layout_note` and no
`build_package`, so Path A's button was permanently disabled behind its own
"this pipeline does not offer it" message and Path B showed the mock's words.
Both were built before the criteria could be discharged. §8's "only the
sanctioned files are uploaded" rule was implemented and never checked; it now
raises. A subset package would have carried the whole matter's `sources.json`
naming 353 documents that resolve to nothing, and printed the **whole corpus's**
token figure in a 15-document README — where the capacity sentence derives from
it, so the error would have arrived as advice.

**NOT proven, and not to be written up as if it were:** *"accepted by a Claude
Project" was never observed.* Uploading is a network action and nothing was
uploaded; file types, sizes, count, structure and the README are proven, and
that half needs a person with a browser. The 30 MB per-file limit is an
assumption; the file-count limit is not enforced and says so. The 103-minute
wall clock is an **upper bound** — another OCR job held the machine throughout —
so a clean idle-machine figure still does not exist. Expert Assist itself was not
run against the folder: the format contract was proven, not the skill. One run,
so no determinism claim rests on it.

**Open, deliberately not fixed:** the shipped 3,600 s per-file timeout fired on
**six** documents, not the two previously recorded, and all six were recovered in
full by the serial re-read. That makes it **load-dependent** and points at the
extraction pool rather than at the files. The default was not raised — the root
cause is unidentified, the right value is unknown, and it is a hashed
run-identity input under A-04, so it is a ruling rather than a side effect of an
acceptance note.

| D-29 | Acceptance criterion 4 — final disposition for Sprint 2 | **SHIP AS NOT MET, carried into Codex review #2 as a known open item (Alex, 2026-08-03).** Every avenue short of changing the OCR engine has been built and run. **Read the composition of the headline figure before quoting it:** **597/648 = 92.130% is a PROJECTION, not a measurement.** It is `568 + 29`, where **568/568 native is Track F's earlier full-corpus measurement** and **29/80 OCR'd = 36.250% is measured** in `docs/verification/artifacts/bates_after_2026-08-02.json` over a deliberately OCR-heavy 61-document / 122-page subset (whose own headline accuracy is 58.197% and is **not** comparable to a full-corpus figure). The subset carries every page that can move — its OCR denominator, 80, equals the full corpus's — which is what makes the arithmetic sound, and it is still arithmetic. The last end-to-end **measured** full-corpus number remains Track F's **593/648 = 91.512%**. Constant across every variant: **0 wrong, 0 false positives.** D-25's band pass recovered 4 pages. D-28's repair **refused on the acceptance corpus exactly as its gate was designed to** — MNFV carries three proposable prefixes (`iCON`, `iiCON`, `jiCON`). The limitation: **on a scanned production an expert gets a locator on roughly a third of OCR'd pages, and never a wrong one.** ⚠️ **And read this next to it:** at the time D-29 was ruled, the figure **through the shipped GUI was 0%** — §4 Stage 3's operator confirmation had never been built, so the format never reached CONFIRMED and no page received a locator. **CLOSED 2026-08-03** by the A-14 confirmation screen; measured on a real MNFV subset (10 documents / 369 pages) through `RealPipeline`: **0.000% before, 88.889% after (328 pages)**. That is a locator-COVERAGE figure for one subset and is **not** comparable to the 92.130% accuracy projection above. D-19's Tesseract benchmark was offered and declined twice; the "never benchmarked" liability stands with a measured page-level cost attached. | 2026-08-03 |
| D-30 | Criterion 6 — a whole pipeline run creates a child process, and the probe that was supposed to catch it never said so | **PERMIT THAT ONE CALL, NARROWLY AND BY NAME (Alex, 2026-08-04).** For three review rounds `test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN` failed intermittently and was reported to Codex as an unexplained **criterion-6 outbound risk**. It was not one. The probe printed a COUNT of guard attempts and threw away the stacks `NetworkGuard` records for every one — so it folded the socket guard and the child-process guard into a single number and labelled the sum "outbound". Measured 2026-08-04 with the stacks retained, 75 probe runs across three concurrent loops: **12 tripped, 84 attempts, every one of them `subprocess.Popen('ver', shell=True)`, and `ATTEMPTS_NET` was 0 in every single run.** It is `platform.uname()`'s one-per-interpreter Windows version probe, reached transitively — `extract.ocr_model_dir()` imports `rapidocr_onnxruntime`, which imports `onnxruntime`, which calls `platform.system()` at import time. Ruling: permit **exactly that call**, as a named exemption carrying its reason (`offline.PERMITTED_SPAWNS`), and keep every other process creation raising. The permission is matched **by identity, not by category** — this entry point, this exact command string, `shell=True`, called from `_syscmd_ver` in the standard library's own `platform.py`, within four frames. Rejected explicitly: "allow spawns during import", "allow short commands", "allow anything from site-packages" — each readmits the whole class the child-process guard was added for. The guard's founding argument is unchanged and stays in the code: a spawned child is an execution the guard can no longer observe. **Criterion 6's claim becomes more specific, not weaker** — it now reads "no network attempts, and no process creation except one named Windows version probe inside a dependency's import, disclosed by name", and that sentence lives in `offline.CRITERION_6_CLAIM` so the code and the documents cannot drift. A permitted spawn is **recorded with its stack** in `guard.exempted` and named in `guard.render()` even on a clean report, because a permission nobody can see is indistinguishable from a hole. If the exemption ever stops matching it must **fail, not widen**: `tests/test_offline.py` perturbs each of the six identity components in turn and proves every one is load-bearing. | 2026-08-04 |

## Bates: the negative control, and what closed the merge gate (2026-08-03)

The merge gate on the Bates work was never the accuracy number — it was whether
raising footer OCR resolution and permitting prefix repair would start
**inventing** stamps on unstamped material. Measured over the full Petrobras
corpus, **298 documents / 17,732 pages**: `(none proposed — CORRECT; 450
stamp-shaped lines seen, none clearing the 50% per-document bar)`,
**0 false positives, 100.0%**. That is the property §4's "absence is normal, not
an error" depends on, and it survived both changes.

**Evidence, committed rather than asserted.** Both runs' machine output is in
the repository: `docs/verification/artifacts/bates_negative_control_2026-08-02.json`
and `bates_after_2026-08-02.json`. An earlier version of these two sections
stated the results while the JSON sat untracked in a worktree and the
verification note still read "did not complete" — so the register asserted
figures the repository could not support. The numbers were real and the
evidence was not landed, which is indistinguishable from the numbers being
invented by anyone reading the repo. Caught by the rehearsal review in one
grep, which is exactly how the external reviewer would have found it.

**D-28's gate fired on real material rather than in a test.** A near-miss prefix
counts toward the single-prefix census — twenty one-page documents misread as
`jiCON…` register as a *second* prefix — so a matter that OCR has confused into
looking multi-series refuses repair. That is the ruling working, not an
oversight, and it is the reason the repair could never have quietly closed the
gap on this corpus.

Three defects were found while writing the tests for it, one of which is worth
keeping: **the single-prefix gate initially asked its question about ONE
DOCUMENT**, which reports "one prefix" for a matter carrying four. The safety
condition would have been decoration precisely where it mattered. The census is
now computed matter-wide and passed in explicitly.

| D-31 | The publish/swap design — patch the fourth window or remove the class (NON-CONVERGENCE) | **REDESIGN TO DELETE-LAST (Alex, 2026-08-05).** Invoked under the standing non-convergence rule, one round later than the rule allows. Codex review #2 has now run three fix rounds, and **each round found a new defect inside the previous round's fix**, all in one subsystem and all one class: *a destructive filesystem step that cannot be proven complete when antivirus, a scanner, or Windows delete-on-close interferes.* Round 1 → B-1/B-2 (unconditional publish; permissive marker read). Round 2 → B-4/B-5 (unproven `rmtree`; diagnosis missing from the durable log). Round 3 → **B-6** (a marker whose `unlink` returned but whose name survived authorizes the next recovery to delete the newly published set), **A-5** (a partly-deleted backup renamed back and reported to the operator as "intact"), B-7 (log projection). Every fix was correct and every fix opened the next window, because **the design deletes before it publishes, so every failure mode is "half-deleted".** Ruling: stop defending windows and make them unreachable. **Never delete before publishing** — build the new set under a staging name, **rename** the current set aside, **rename** the new set into place, and only then delete what was moved aside. The substitution that carries the design is **rename in place of delete**: a rename on one volume either happened or did not, and its outcome is readable from the names on disk, whereas a delete under lock is neither provable nor reversible. A crash or a blocked delete then leaves a clearly-named stale folder **beside a complete published set** — never a mixture, and never a destructively-modified backup presented as intact. A-5 and B-6 stop being reachable rather than being defended against. Rejected: fixing the three findings as filed (the last three rounds are the evidence for what happens next), and descoping the hardening with the windows disclosed (this is the code that decides whether an expert's matter folder holds one run's evidence or two runs mixed). **Implementation note, 2026-08-06 (Codex review #2, third fix round, B-8):** the ruling is unchanged and the first implementation of it did NOT meet it. "Rename the current set aside" was implemented as "rename aside the names this build's output patterns enumerate", which is not the current set: a deliverable an older build wrote under a retired name either survived a successful swap or was moved aside lazily, after new files had already landed. The plan is now built from a durable inventory of what the last run actually published (`.dociq/published_set.json`), and the publish phase clears every destination in a complete pass before renaming anything in. Enumerating the swap's persistent states backwards, as that round's merge condition required, found two more states whose next step destroyed a complete set. See `docs/verification/codex_r4_inventory_2026-08-06.md`. | 2026-08-05 |

## Sprint-1 verification log

**Track B master-index robustness (2026-07-30).** The Track B critic found a
High-severity defect: a negative `Original Sort` value loaded cleanly into a
`MasterIndexRow` but violated `DocId`'s `base >= 0` requirement at assignment
time, raising an unhandled `ContractViolation` that named neither the offending
row nor the file — taking down identifier assignment for *every* document in the
corpus, not just the bad row. Fixed at `5eb1f7a` (negative sort treated as "no
usable value": skipped with a warning, row reconciles as index-only), with a
regression test confirmed to go red on `a78f1b5`.

The critic could not test this against the real index, correctly refusing to
touch client data. Audited separately: the real Project 495 index is **clean** —
9,259 rows, a perfect 1..9,259 sequence, zero non-numeric, zero negative, zero
duplicate values. So the builder's "9,698 IDs, 0 collisions" claim is genuine,
but it passed *because the file happened to be clean*, not because the code was
robust to a dirty one. Recorded because it is a textbook instance of "the corpus
doesn't exercise it selects nothing": the fix is warranted by the failure being
loud and total, not by evidence from this corpus.

**Sprint-1 integration (2026-07-31).** The stand-in emitter `verify/probe_emit.py`
is deleted and the determinism proof re-run against the emitters that ship: 30
runs, 30 distinct `PYTHONHASHSEED` values, subprocess per run, **1 distinct
corpus hash, 0 diffs, 0 failures**, and the harness watched going red under three
injected-nondeterminism probes. Seven defects found and closed at the seam, each
with a test proven red beforehand — the un-remapped `parent_doc_id`, D-04's
renumbering check crying wolf on duplicate content, renumbering warnings inside
the hashed log content, a re-run inheriting the previous run's residue, a corpus
that never exercised email-attachment expansion, Stage 3 proposing a Bates format
on the set D-13 designates as the negative case, and a run that skipped OCR still
recording that it had used rapidocr. **One finding is OPEN and material:** a
9 MB PowerPoint extracted as 35 pages on one full-corpus run and failed on
another, so the byte-identical claim is demonstrated on the fixture corpus and
is **not** demonstrated on the real one. Full account in
`docs/verification/sprint1_integration_2026-07-31.md`.

**A note on thread configuration, measured rather than assumed (2026-07-31).**
Track A recorded thread oversubscription as an unquantified hypothesis. It was
partly tested during the full-corpus run: the shipped defaults put **173 OS
threads** on a 32-core box at **~12.5 cores busy**. Reducing to 4 document
workers and 6 OCR page workers made throughput *worse* — 142 threads at **~2
cores busy** — so the naive "pin the threads" remedy is refuted for this
workload, and the run was returned to the defaults. What limits throughput here
is not yet identified; it is a Sprint-2 performance question, and the one thing
now established is that fewer workers is not the answer.

## Measured corpus token load (full corpus, 2026-07-30)

> **CORRECTED 2026-07-31 (Codex review #1, finding B-6).** This section
> originally called the pre-token count a *hard lower bound* on token count for
> any byte-level BPE tokenizer, and treated it as refuting D-03. **Both claims
> were overstated and are withdrawn.** The correction is at the foot of this
> section; the measurements themselves are unchanged and still stand.

Measured over **all 298 PDFs / 17,732 pages** of the real MODEC/Petrobras MPR
corpus using Track B's pre-token proxy (`verify/tokens.measure`):

| quantity | measured |
|---|---|
| characters | 49,031,833 |
| **pre-tokens (DocIQ's own split — see the correction below)** | **19,388,495** |
| density | 2.53 chars/pre-token |
| per page | 2,765 chars / 1,093 pre-tokens |

**This is ~6× the requirements' 3.4M assumption (§1) and ~97× the 200K
direct-context working figure.** The consequence is strategic and should not be
softened: **no combination of reductions brings this record into direct
context.** A 90% reduction still leaves ~2M tokens, 10× over. Path B (Claude
reading the matter folder from disk) is therefore not merely the *recommended*
route for forensic matters — at this scale it is the only viable one, and the
UI must treat direct-context capacity as an aspiration that this class of matter
will not meet. This strengthens D-15 rather than contradicting it.

**SUPERSEDED FOR ANY STATEMENT ABOUT THE DELIVERABLE (2026-07-31).** The first
full pipeline run measured **17,252,003 pre-tokens over 50,190,410 characters**
of its own emitted page text across all 368 documents (2.91 chars/pre-token),
and 17,380,982 over 50,598,897 for `clean_text/` including page markers. More
characters, 11% fewer pre-tokens — because the two figures measure **different
text**, not because the estimator changed. The figure above came from
`tools/calibrate_tokens.py`, which reads with **PyMuPDF `get_text()`**, skips
whitespace-only pages, applies no normalization, runs no OCR and covers the 298
PDFs only. On 131 identical pages, PyMuPDF yields 16.7% more pre-tokens than the
pypdf text DocIQ actually extracts, and contract normalization removes about 5%
more. Use the pipeline's number for the corpus DocIQ ships; the figure above
remains valid as what the source PDFs contain under a different reader. Detail
in `docs/verification/sprint1_integration_2026-07-31.md` §5.

Every token figure in the Sprint-1 UI mockups (3.4M → 850K) remains known to be
far too small and must not be carried into the build as if measured.

### CORRECTION — the "19.4M token floor" and the D-03 refutation (2026-07-31)

Codex review #1 finding B-6 is accepted in full. Two things this section
asserted are not supported by the evidence:

**1. There is no 19,388,495-token floor.** The argument was that a byte-level
BPE tokenizer cannot merge across a pre-token boundary, so it cannot emit fewer
tokens than the text has pre-tokens. That holds for a tokenizer's **own**
pre-tokenization. It does not hold for boundaries DocIQ's approximate regex
invented: `PRETOKEN_RE` splits digit runs every three digits, and a real
tokenizer that keeps longer digit runs together merges straight across those
splits and emits fewer tokens than DocIQ counted pre-tokens. The corpus is
**13% digits**, so this is where the material actually is, not a corner case.

19,388,495 is a **pre-token count under DocIQ's own split** — a characterization
of the text's structure, not a bound on any tokenizer's output.

**The one bound that does survive** is the ceiling: `tokens <= UTF-8 bytes`,
because a byte-level vocabulary always contains single-byte fallbacks. That
holds for any such tokenizer and DocIQ still asserts it.

**2. D-03 is NOT established as refuted, and its status returns to RULED.** With
the assumed allowance for coarser pre-tokenization (0.70–1.60 tokens per DocIQ
pre-token, both assumed and both stated in `verify/tokens.ASSUMPTIONS`), 2.53
chars/pre-token implies roughly **1.58–3.61 chars/token** — which *overlaps*
D-03's ruled 3.30–3.60 band. At the coarse end the corpus lands near 3.5
chars/token, inside the band. The same arithmetic on the 40-PDF sample (3.03)
and on the pipeline's own emitted text (2.91) also overlaps.

What survives, and is worth keeping: this material is far denser than ordinary
prose (4.4–4.5 chars/pre-token), so D-03's band sits at the **coarse end** of
what the structure allows rather than in the middle of it, and a figure built on
the band should be read as an optimistic member of a wide range. That is a
characterization, not a refutation.

**3. The strategic conclusion is unchanged and does not depend on the withdrawn
claims.** Even at the top of the ruled band (3.60 chars/token) the corpus is
~13.6M tokens, ~68× the 200K direct-context working figure; a 90% reduction
still leaves ~1.4M, ~7× over. **No combination of reductions brings this record
into direct context.** Path B (Claude reading the matter folder from disk)
remains the only viable route at this scale, and D-15 stands.

**4. What is needed to settle it.** Someone with network access must count real
Claude tokens on a sample of this material. Until then every DocIQ token figure
is an estimate under stated assumptions, and the deliverables say so — the
processing log carries the assumptions verbatim, the run summary PDF names the
method that run used, and the GUI no longer renders "tokens at least".

## §10 restated against a completed full-corpus run (2026-07-31)

The first end-to-end run of the whole D-12 corpus finished: **368 documents,
18,521 pages, page accounting reconciling to zero discrepancy** (acceptance
criterion 2, on real material, for the first time). Measured:

| | measured |
|---|---|
| walk + extract (Stages 1-2) | 2,848.5 s — **99.1% of the run** |
| everything else — Bates, Doc IDs, reconciliation, classification, all of §7 and §8, the accounting gate, the hash manifest | **25.7 s combined** |
| same corpus with OCR disabled, from scratch, idle machine | **3,046.7 s (50.8 min)** |
| OCR's share, over the identical first 62 documents both ways | **≈ 2.0–2.3× — OCR roughly doubles extraction, for 2.2% of the pages** |

A clean from-scratch OCR-on wall clock is **not** established (the completing run
resumed 62 documents from an interrupted first attempt); ≈100 minutes is the best
available figure and is an upper bound. §10's "under 60 minutes" is not met and
is not restated as a general target until a clean run exists. Two documents also
exceeded the shipped 3,600 s per-file timeout and would have been abandoned —
loudly, never silently — so that default is too tight for this corpus.

The consequence for where effort goes is unambiguous: **optimizing anything but
extraction is optimizing 0.9% of the run.**

## §10 measured again, from scratch, WITH OCR (2026-08-02)

The acceptance run for criteria 1 and 8 is the first completed **from-scratch**
full-corpus run with OCR enabled. It bears on three things this register
asserts, and they are corrected here rather than left to be found in a
verification note.

| | previous entry | this run |
|---|---|---|
| documents / pages | 368 / 18,521 | **368 / 18,556** |
| from-scratch OCR-on wall clock | *"not established"*; ≈100 min an upper bound | **6,182.4 s = 103.0 min** |
| everything after extraction | 25.7 s | **18.5 s (0.30% of the run)** |
| documents exceeding the 3,600 s per-file limit | 2 | **6** |

**The wall clock is still not an idle-machine figure.** Another agent's OCR job
and repeated `pytest` processes ran on the same box throughout; sampled CPU load
was 100% for most of the window. 103.0 minutes is what this corpus cost under
contention, so it **corroborates** the ≈100-minute upper bound rather than
replacing it with a rate. §10's 60-minute target remains missed and is still not
restated as a general target.

**The per-file timeout is load-dependent, which is more than "too tight".** Two
documents crossed it on an idle machine and six on a contended one, and all six
were **recovered in full by the serial re-read** (228, 222, 186, 189, 191 and
187 pages, "no degradation"). That the same files succeed alone and fail in the
pool points at the extraction pool rather than at the files, which is the
Sprint-2 performance question the register already records as unidentified. The
default was deliberately **not** changed on this evidence: it is a hashed
run-identity input (A-04), the right value is unknown, and picking one from the
contended case would be picking the wrong one. Reasoning recorded in
`docs/verification/acceptance_1_8_2026-08-01.md` §9.3 so it can be overturned.

**The 35-page difference is consistent with the open PowerPoint finding and does
not close it.** Sprint-1 recorded "a 9 MB PowerPoint extracted as 35 pages on
one full-corpus run and failed on another"; this run's page count is exactly 35
higher and all 17 `.pptx` documents processed `Full`. The earlier run's
per-document counts are not in hand, so the coincidence of the number is the
whole of the evidence. **The byte-identical claim is still not demonstrated on
the real corpus.**

**One document failed and it is the document's fault.**
`Weekly Progress Reports/Topside Weekly Progress Report/CER-1-345.docx` failed in
the pool *and* on the serial re-read — its content sniffs as a zip-family
container that is not a readable Word file. Recorded `Failed` in the index.

## Acceptance criteria 1 and 8 — discharged (2026-08-02)

Both discharged in `docs/verification/acceptance_1_8_2026-08-01.md`, on
amendment A-12, which Track D and Track E had both raised and neither had
applied — **no upload package had ever been built**.

* **Criterion 1, Path B — proven at full scale.** All 368 documents;
  `expert_assist_layout` checked the folder on disk and found all four
  deliverables where Expert Assist reads them, with no rearrangement; the §7
  format contract was then asserted over the whole corpus — 18,556 markers
  parsed (equal to `pages_in`), `sources.json` resolving for every document,
  **zero format errors**.
* **Criterion 1, Path A — proven on a stated subset.** 206 date windows
  measured, 14 fit the 200,000-token reference line, the widest taken: 15 of 368
  documents, 154–168K tokens. The widest *non*-fitting window is 189 documents
  at 6.4–6.9M tokens, ~32–35× the line — D-20's premise, measured on this
  corpus.
* **Criterion 8 — proven as far as an offline tool can, and no further.** Both
  packages built from the real run and inspected; only `.txt` / `.json` / `.csv`
  present; largest file 1.05 MB (whole record) and 77 KB (subset) against a
  30 MB assumption; §8's "only the sanctioned files" rule now **raises** rather
  than being merely implemented. **"Accepted by a Claude Project" was NOT
  observed** — uploading is a network action Principle 4 forbids and §12
  excludes. That half of criterion 8 needs a person with a browser, and the note
  says so rather than claiming it.

## Corpus reality vs. the spec's assumption (recorded 2026-07-30)

The requirements' motivating figures (§1: "38 MODEC MPRs at ~20 MB / ~150 pages
each"; §10 performance target: "~5,700 pages with ~50% scanned in under 60
minutes") do not match the corpus that actually exists:

| | spec assumption | measured |
|---|---|---|
| documents | 38 MPRs | 298 PDF + 53 DOCX + 17 PPTX + 7 DOC |
| PDF pages | ~5,700 | 17,732 |
| scanned share | ~50% | 2.6% (461 pages) |

Consequences carried into the build rather than left implicit: the OCR-dominated
performance target is far easier than specified (461 pages to OCR, not ~2,850),
but the corpus is **3× larger in page count**, so the non-OCR path — extraction,
hashing, accounting, emit — is the real performance risk, not OCR. The §10
target should be restated against measured numbers once Sprint 1 has a timed
end-to-end run; it is not amended here on prediction.

## Amendments to requirements_v1.0 implied by these rulings

- §3 Tier-1 table: "Local OCR (Tesseract)" → "Local OCR (rapidocr, ONNX; per-page confidence recorded)" subject to the D-01 bake-off.
- §3 Tier-1 table: DOC moves to Tier 2 (listed + remediation hint) for v1.
- §10 stack line: Tesseract 5 → rapidocr_onnxruntime (+ PyMuPDF rasterization), pending bake-off.

These amendments will be folded into a requirements v1.1 once D-04 is ruled.
