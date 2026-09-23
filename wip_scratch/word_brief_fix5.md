# Word fidelity package, review-fix round 5 (2026-09-23, under D-57)

SP = `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad`.
Worktree `SP\wt-word`, branch `build/sprint-5-word` @ `85efb95` (clean, pushed). Interpreter
`C:\Users\Alex\document-iq\.venv\Scripts\python.exe` from the worktree root; selftest `PYTHONPATH=src`. addopts carry
`-q`. Never `-k`. Helpers as files under `SP\recovered\word_fix5\`, never heredocs.

**D-57:** fix and review alternate until a review confirms no A or B. Rounds: 2A+5B, 6B, 4B, **1B** (round 4).
Leave round 5 nothing serious.

## Read first

`SP\recovered\review_word_r4\findings.md` and `payload.json` (probes under the reviewer's `review_word_r4\`
folders), `SP\recovered\word_brief_fix4.md`, and D-56/D-59 in
`C:\Users\Alex\document-iq\docs\decisions\decision_register.md` (read-only).

## Fix, each watched RED on `85efb95` by full node id (or its named mutant caught)

1. **The B:** no test holds that the deletion list counts toward neither zone bound (mutant R4: apply the skip after
   choosing head/tail windows survives everything; a stamp as the last body line followed by 4+ deletion paragraphs is
   lost silently). Add exact-output tests for the head and the tail, with list lines below and above the stamp, and
   show R4 killed. Class: every property `slice_lines`' docstring asserts gets a test that a mutant of it fails;
   list each property and its test.
2. **The two unpinned precondition mutants R4M1, R4M8** (deletion text with trailing space, NBSP, decomposed accent,
   `&#13;` makes ordinary Word files FAIL with pages=0): make the span's precondition hold by construction for every
   deletion text the reader can emit, with tests over those shapes; and check R4M5/R4M7 and say whether a test can
   pin them.
3. **Every C item** (eight), each fixed or one line on why it waits: marker phrases inside clipped file values (clip
   the value before quoting, not the note); `w:object` wrapping `w:objectEmbed` in deleted content gets the removal
   note; the CHANGELOG A-25/D-59 wording; the Office-zip extras note for Office content under any name or as an
   attachment; symbol-font detection beyond a closed name list (PostScript aliases; fonts whose font table declares a
   symbol charset `w:charset w:val="02"` in `fontTable.xml`), with a corpus count; a deleted text box holding an
   object counted once; a document embedded in a non-displayed header part noted as such.

## Gates, in order

1. Red lines; round 4's 163 mutants re-run on a COPY plus the reviewer's (R4, R4M1-R4M8), all killed or explained.
2. The 21 gate-2 files of round 4 (`SP\recovered\word_fix4\gate2_summary.txt` lists them), each in its own process;
   the selftest.
3. Corpus, counts only (`SP\recovered\word_fix4\measure\`): children 289, new Bates candidates and changed first dates
   vs `85efb95` and `ea4f1cb`, marked notes by marker, symbol glyphs mapped vs placeholdered, failing documents.
4. The full suite once: count line and every failure verbatim.
5. Commit on `build/sprint-5-word`, files listed explicitly, message ending
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Rules

No `pip install`. No deleting files. No git `checkout --`, `reset`, `rebase`, `stash`. Never touch
`C:\Users\Alex\document-iq` except reading. Never another worktree. Invented text only (D-12). US English. Fix the
class, not the repro; enumerate siblings.

## Report, under 1,200 words

First: failed gates, findings you believe are wrong, existing tests changed and why, anything you stopped on. Then each
item with its class deliverable; the C dispositions; corpus counts; gates; commit.
