## Package: spreadsheet fidelity, review ROUND 5 under D-55, `<package>` = `excel_r5`

* Worktree `C:\Users\Alex\AppData\Local\Temp\claude\C--Users-Alex\c4101d6e-425a-4e46-b1e2-205e8e47cc10\scratchpad\wt-excel`,
  branch `build/sprint-5-excel`, clean. Round 4 reviewed `1619fc7`; fix round 4 is **`cf327a1`** ("disclosure by
  construction"). Review `git diff 1619fc7 cf327a1`, then the whole package `git diff ea4f1cb cf327a1`.
* **D-55: fix and review alternate until a review confirms no A or B finding.** Rounds: 2A+8B, 9B, 4B, 5B. Round 4
  was not converging because each round found another unread-and-unnamed Excel feature; fix round 4's central job
  was to make that class impossible BY CONSTRUCTION (code tables of READ / COUNTED / NO EVIDENCE; everything else
  named in one FINAL note; a derived guard with invented names). Rate honestly both ways.
* Earlier rounds under `...\scratchpad\recovered\review_excel*\`. Fix brief `excel_brief_fix4.md`; builder evidence
  `...\recovered\excel_fix4\` (red run `x11_red_1619.out.txt`, 204 mutants, corpus `x14_corpus.out.txt`, real-Excel
  files rebuilt via COM under `excel_real\`). Real Excel is installed; if you drive it through COM, close it after
  use and never leave an Excel process running.
* Rulings (read-only register, `build/sprint-5` @ `46d36f6`): D-12, D-50, D-55.

### What round 5 must establish

1. **The construction holds.** Try to get content past it: a part reachable only through an unusual relationship
   chain, an `mc:AlternateContent` whose Choice is not the first or holds content the Fallback does not, a content
   type the census misclassifies, a cell attribute/child in an extension namespace, a drawing element inside a group,
   BIFF records inside a CONTINUE, an OLE stream nested in a storage. For each, is the content READ or NAMED? Is any
   table entry marked NO EVIDENCE or structural that actually carries text a reader would want (say which and why)?
2. The derived guard: does it really derive (would a new table entry added carelessly defeat it)?
3. Round 4's five findings closed (re-run the round-4 probes); the C items the builder fixed; the ones it left for a
   ruling (percent negative-section decimals, last half-second of a day, `vbaProject` named not ruled, serial 60) are
   not findings unless their recording is untrue.
4. The corpus change: FINAL notes 21 -> 155 in 95 of 138 workbooks, page text unchanged. Is every new note true, and
   is naming `customXml`, hidden constant defined names and data-validation lists the right level (noise that
   buries real losses is itself a disclosure defect: say if so)?
5. Mutants: re-run 10 of the builder's 204 on a fresh copy and try 5 of your own.

### Not findings

Document properties rated NO EVIDENCE product-wide (a pending judgment for Alex). Stale-journal replay across builds
(main session, at merge).
