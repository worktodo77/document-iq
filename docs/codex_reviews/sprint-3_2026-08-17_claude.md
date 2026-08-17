# Sprint 3 review request — the omission taxonomy, wired

**Path:** `docs/codex_reviews/sprint-3_2026-08-17_claude.md`
**GitHub:** https://github.com/worktodo77/document-iq/blob/build/sprint-3/docs/codex_reviews/sprint-3_2026-08-17_claude.md
**Branch:** `build/sprint-3` @ `ff29d9c`
**Author:** Claude (Opus 5), 2026-08-17
**Reviewer:** Codex

Read this file **from the branch** (`git fetch origin build/sprint-3` then read
at that path), not from chat text.

---

## Read list, in this order

1. `docs/decisions/decision_register.md` — **D-33, D-34, D-35** (the sprint's
   scope and the two rulings it implements), then **D-36 to D-40** (the
   close-out rulings, harvested after the tree went green).
2. `docs/verification/sprint3_red_tree_2026-08-17.md` — the corrected baseline.
   **79 items, not 11.** Read the mechanism, not the number.
3. `docs/verification/sections_2026-08-17.md` — the corpus measurement the
   design rests on (Tier 1 reaches 63.01% of pages; the honest ceiling is ~70%).
4. `docs/verification/sections_wiring_2026-08-17.md` — **the main artifact for
   this review.** What was built, what it cost, what is not established.
5. `docs/contracts/amendments.md` — **A-18, A-19, A-20**, all now APPLIED.

## What changed

`profiles/apply.py` is **deleted**. It matched a rule's regex against every line
of every page and carried the matched section forward until the next match, so a
rule written for `PROGRESS PHOTOGRAPHS` also fired on any page that merely
mentioned it and then governed every following page. D-35 ruled it replaced.

Section resolution is now **spans**: Tier 1 from the document's own PDF outline,
Tier 3 from measurable page classes. A span names its last page, so a drop
cannot run past it.

**Recognition happens at extraction; disposition stays at Stage 4.** Tier 1 needs
the file open and Stage 4 has only records, so `extract.pdf_spans` computes the
spans and `walker._record` stamps `section` + `section_tier` onto the pages.
Stage 4 rebuilds the spans from those pages
(`sections.resolve.spans_from_pages`).

**The carrier is the design decision and resume is what settles it.** A side
channel would be empty for every document replayed from the resume cache, so a
resumed run would drop nothing where a fresh run dropped pages — one folder, two
corpora, which is the Principle-5 break the contract forbids.

**Nothing drops without an `ApprovedOmission` naming a person** (D-34). A
template ships unengaged and is structurally incapable of carrying a
disposition.

## Measured, not asserted

| claim | how it was checked |
|---|---|
| Recognition-only is observable | 8-page PDF, template loaded, nothing approved: **3 sections in `processing_log.json`, each `t1_outline`, 0 drops** |
| D-35 is closed | one approval: **pages 3-4 drop and stop**. The old engine ran such a drop to the end of the document |
| A-19 | adding an approval moves the run identity; changing only the APPROVER moves it again |
| A-20 | 19 families reach the checklist; the 8 `offer=False` are refused by `with_toggled` at the model AND `set_omission` at the pipeline |
| Suite | **1,494 tests, 8/8 runs, exit 0, zero markers, byte-identical output, 259-265 s** |
| Selftest | exit 0, 70 checks, determinism over 8 sequential runs at 1 corpus hash |

**Machine verified quiet before the timing runs and it stayed quiet** (CPU
3/4/0%, no python processes, no agents of ours). The register records what this
project's timing claims made under load were worth; this one is not one of them,
and 4m20s agrees with the post-descope 4m22s already on the record.

## Defects found and closed during the sprint — please re-derive these

Five of these were found by adversarial reviewers re-deriving each package
against the real diff. **The most serious was found by driving the real screen,
not by any test**, and it is the one to attack first.

1. **The §6 approve button was disabled on every path.** `profile_rules` began
   describing the template (19 families, 11 offerable) while
   `ProfileChecklistView.counts_agree` went on comparing `profile.section_rules`
   (2, or 0) against the rows shown. Permanently False, so `approvable` was
   permanently False, behind a message telling the operator rules were hidden
   from them. **A-12's shape.** No mock-driven test could see it:
   `MockPipeline.profile_rules` slices to the profile's own count, so its
   checklist agrees with itself by construction.
2. **Withdrawing an approval left the pages DROP with no drop-log entry** — the
   unattributable drop Principle 1 forbids, reached by withdrawal rather than
   omission. Not reachable through today's pipeline; reachable through
   `apply_sections`, which is public and is the only thing that may drop a page.
3. **`locked` widened and silently re-routed rows.** Every
   recognized-never-offered family — weather logs, timesheets, manpower
   histograms — was drawn on the waterfall row reading *"Removed mechanically by
   the tool"*, while those pages were kept.
4. **The index would have printed a template id under "Omission approved by"**
   (`drop_rule` is `template_id:family_id`; the approver is not on `PageRecord`).
5. **`verify/manifest.py`'s `IDENTITY_NOTE` listed what the identity covers and
   never mentioned the approvals A-19 added.**

## Where I think this is weakest — attack here

* **`spans_from_pages` is an inverse and inverses are where I would look.** It
  rebuilds spans from stamped pages. I claim two bounded departures (Tier-3
  evidence regenerated per tier; two adjacent same-label same-tier spans merge)
  and no others. **Try to construct a third.**
* **Tier 3 in the shipped engine recognizes MORE than the probe measured.** The
  probe classified Tier 3 only in unoutlined documents; the engine classifies
  every page no Tier-1 span covers. The published 1,308-page figure is a floor
  for shipped behavior, not an equality. I have stated this rather than measured
  the difference.
* **`pdf_spans` opens the PDF a second time.** Deliberate, and the cost is not
  measured on the full corpus.
* **The GUI approval path was exercised offscreen, never by a human with a
  mouse.** That non-claim is inherited from Sprint 2 and is not narrowed here.
* **`MockPipeline` cannot capture an approver.** It says so out loud when a lever
  moves, but a stand-in run still shows an engaged row with no approval behind
  it.

## Known-open, by ruling rather than by oversight

* **D-39: project tokens are derived from the corpus in Sprint 4.** Today the
  list is always empty, so ~30.5% of the corpus vocabulary declines to match a
  family. Failure direction is safe (unmatched → kept). Alex ruled the automatic
  derivation with the §2 risk stated; it must be deterministic and shown to the
  expert.
* **D-38: the profile system is deleted in Sprint 4.** Consequence accepted on
  the record: matter folders already on disk will not reproduce byte-for-byte.
* **B-8 stays where D-32 put it.** Not reopened, and nothing here touches it.
* **Tiers 2 and 4 unbuilt.** ~30% of pages are recognized by nothing and kept.

## The standing preamble for this review

Findings rated **D** do not trigger a re-review round. The bar is lighter for
test-harness code than for pipeline code. A finding that assumes an attacker
model beyond in-process Python in an internal desktop app is a **gate question,
not a defect** — say so and I will take it to Alex rather than build against it.

Please return a verdict file at
`docs/codex_reviews/sprint-3_<date>_codex.md` on this branch.
