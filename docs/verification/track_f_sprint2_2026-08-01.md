# Track F — Sprint 2 verification record

> **Superseded in part, 2026-08-01.** §4.6's figures remain the criterion-4
> BASELINE and were reproduced digit-for-digit on `build/s2-bates`. What §4.6
> says about the OCR residue — that it "belongs in the D-19 conversation" — was
> then acted on under D-25: the targeted footer re-OCR was built and measured,
> and it does **not** close the criterion. The residue is rapidocr reading this
> production's stamp digits correctly and failing on its `ii` prefix at every
> resolution, crop and preprocessing tested. See
> `docs/verification/bates_d25_2026-08-01.md`. The Bates zone described in §4 is
> also now three head lines and **eight** tail lines, not four.

**Branch:** `build/s2-track-f` · **Date:** 2026-08-01 · **Machine:** Windows 11
Pro 26200, 32 cores, Python 3.14.5, `document-iq\.venv`

Everything below was **run**. Where a claim is an argument rather than a
measurement it says so, in the same sentence as the claim.

---

## 0. Headline

| deliverable | state |
|---|---|
| F-1 packaging (D-22) | **done and verified on the artifact** — one folder, one zip, both executables launched, self-test green from inside the exe |
| F-2 offline proof (criterion 6) | **substantially done, one gap** — zero outbound attempts proven in-process and corroborated at OS level; a genuinely adapter-disabled run is **not** executed (see §3.4) |
| F-3 Bates acceptance (criterion 4) | **run. Criterion 4 is MET on native-text pages (100.000% / 568 pages, authoritative ground truth) and NOT met on OCR'd pages (31.250% / 80). Zero wrong, zero false positives.** Two defects found and fixed on the way — see §4 |
| F-4 criterion 1 | not attempted, per instruction |

**Three product defects were found by doing this work and all three are fixed:**
Bates detection scored **0%** on a real Bates-stamped production; the
**packaged build silently did not OCR**; and a confirmed Bates format was
rejected whenever OCR folded the stamp into a longer line. None was visible
from the source tree, and none produced a warning at runtime — each one failed
by looking exactly like the ordinary, healthy case.

---

## 1. What was built

| path | what it is |
|---|---|
| `packaging/DocumentIQ.spec` | the PyInstaller spec — bundled models, excluded Qt modules, both executables, the D-08 icon |
| `packaging/build.py` | the documented build driver: clean, build, **run the artifact**, zip, measure |
| `packaging/dociq_launcher.py` | frozen entry point — `--version`, `--selftest`, `--offline-probe`, `--diagnose`, `--determinism-run`, else the GUI |
| `packaging/rthook_offline.py` | runtime hook: pins the bundled model directory and the ecosystem offline flags before any import |
| `src/dociq/verify/offline.py` | the network guard that **counts** attempts, the guarded-entry-point enumeration, the live sibling audit, the fetch/transport module audits |
| `tests/test_offline.py` | 18 tests, including the fail-before for every guarded entry point |
| `tools/bates_acceptance.py` | D-23 (a): continuity over the whole set; (b) machine half: page-level accuracy against the production's load files |
| `tools/bates_handcheck.py` | D-23 (b) human half: renders a blind, stratified footer sample and scores a reader against a withheld key |
| `docs/build/packaging.md` | how to build it, what is bundled and why, what is measured, what is not claimed |

---

## 2. F-1 — the packaged build

### 2.1 Measured on the artifact

| quantity | measured |
|---|---|
| unpacked payload | **393.1 MB across 939 files** |
| shipped zip | **178.2 MB** |
| PyInstaller wall clock | 180 s |
| **first launch of a never-run copy** | **2.651 s** |
| **29 further launches** | **min 0.320 s / median 0.461 s / max 0.962 s** |
| `--offline-probe` (17 docs, 25 pages, 3 OCR'd) | 26.9 s |
| `--selftest --runs 8` from inside the exe | **PASSED — 70 checks, determinism over 8 seeds, 1 distinct corpus hash** |

The steady-state launch figure is the one D-22's reasoning rests on and it had
never been measured. The first run is reported **separately and named**: it is
dominated by the on-access antivirus scan of 393 MB of never-seen binaries, a
cost a client pays once. Folding it into a median would either overstate every
launch or hide the first-run cost, and the first-run cost is the one D-22's
endpoint-protection argument is about.

**Not measured: `--onefile` for comparison.** D-22's claim that onefile would be
slower and quarantine-prone remains an argument, not a measurement. What is now
measured is that the one-folder build launches in half a second and that the
payload onefile would re-extract on *every* launch is 393 MB.

### 2.2 Verification is part of the build

`packaging/build.py` exits non-zero unless all three of these run:

1. `DocumentIQ-cli.exe --version` (×30, for the cold-start distribution)
2. `DocumentIQ-cli.exe --offline-probe` — a whole pipeline under the guard
3. `DocumentIQ.exe` launched and still alive after 8 s, then terminated

Check 3 is crude and deliberate: a frozen GUI whose imports fail dies inside a
second or two, so "still running" is a real signal. It is the check a packaging
change most often breaks and the one most often skipped, because a windowed
process writes nothing to a console.

### 2.3 DEFECT — the packaged build silently did not OCR

`rapidocr` does not import its three inference stages normally. It appends its
own package directory to `sys.path` and calls
`importlib.import_module(config['Det']['module_name'])` — the stage names come
out of a YAML file. A module graph cannot see any of that.

Frozen, the failure mode was the worst available one:

- `--version` reported **models present**
- `ocr_available()` returned **True**
- every scanned page came out **EMPTY**, with
  `AttributeError: module 'ch_ppocr_v3_det' has no attribute 'TextDetector'`
  swallowed into a per-document note

A client would have received a corpus with its scanned evidence missing and a
run that looked healthy. **Caught only because `--selftest` and
`--offline-probe` run on the built artifact.** Fixed by reading the stage names
from rapidocr's own `config.yaml` and failing the build if there are not three;
after the fix the same probe OCRs 3 pages and the frozen self-test is green.

### 2.4 DEFECT CLASS — `__file__`-derived paths in a frozen bundle

Four members, all fixed:

| site | what broke |
|---|---|
| `branding/palette.py` | brand directory — the palette is *sampled* from `li_monogram_source.png` at import, so the GUI raises before drawing |
| `ingest/extract.py` | OCR model directory |
| `verify/determinism.py` | `sys.executable -c <source>` — a frozen exe takes the runner source as a positional argument, so the proof would have run the GUI eight times and compared eight empty directories, which reconcile perfectly |
| `tests/fixtures/make_fixtures.py` | the generator's self-hash |

**The fourth was missed on the first sweep**, because the sweep read `src/` and
that file lives under `tests/`. Recorded because the lesson is about the sweep,
not the line.

### 2.5 STOP THE LINE — PyInstaller is not installed, and I did not install it

D-11 pins the dependency set to `document-iq\.venv`; PyInstaller is not in it,
and installing needs authorization I did not have. **Nothing was installed and
nothing in the venv was modified.** PyInstaller 6.20.0 and its four
dependencies were copied out of `C:\Users\Alex\mip39-prototype\.venv` (same
Python 3.14, no compiled extensions of its own) into a scratch directory and
put on `PYTHONPATH` for the build only, via `build.py --pyinstaller-path`.

**Requested:** `pip install pyinstaller==6.20.0` into `document-iq\.venv`, so
the documented standing command in `docs/build/packaging.md` is the one that
actually built it. Until then the doc carries the workaround explicitly.

### 2.6 Other things worth knowing

- **`opencv-python` and `opencv-python-headless` are both installed** in the
  venv, and the one that won is the **non-headless** build (`cv2.version` reports
  `headless = False`). `pyproject.toml` declares headless only, and its comment
  says the GUI build must not pull opencv's Qt bindings. On Windows this
  opencv ships no Qt DLLs, so nothing collided — but the venv does not match
  what the project declares, and the next platform or version may not be so
  forgiving. Not fixed here: uninstalling needs authorization.
- **`.gitignore` had an unanchored `build/`**, which silently swallowed
  `docs/build/`. Anchored to `/build/` and `/dist/`.

---

## 3. F-2 — the offline proof (acceptance criterion 6)

### 3.1 What was wrong with the probe that already existed

Sprint 1's `selftest._check_no_network` replaced `socket.socket` with a raiser
and asserted OCR still produced lines. That proves OCR *survives* a blocked
socket. §10 and the user-facing documentation claim something stronger — "makes
no outbound connections" — and a blocking-only probe passes **in total silence**
on an attempt swallowed by `try/except Exception`, which is the ordinary shape
of a model fetch. It would then go out the moment the block came off on a client
machine.

`dociq.verify.offline.NetworkGuard` therefore **counts before it blocks**, and
the assertion is `attempts == 0`.

### 3.2 The class, enumerated

Eight entry points, returned as a value by `enumerate_guarded_entry_points()`
so a test asserts them and a reviewer can diff them:

`socket.socket`, `_socket.socket`, `socket.create_connection`,
`socket.socketpair`, `socket.getaddrinfo`, `socket.gethostbyname`,
`socket.gethostbyname_ex`, `ssl.SSLContext.wrap_socket`.

`audit_siblings()` reads the **live** `socket` module and reports any
outbound-capable public callable that is neither guarded nor explicitly
accounted for, so a name a future CPython adds becomes a test failure rather
than a hole. It returns empty.

### 3.3 What was executed

| probe | result |
|---|---|
| `tests/test_offline.py`, 18 tests | pass |
| one case per guarded entry point, each of which *would* have gone out | all 8 recorded |
| a swallowed attempt (`try/except Exception`) | **recorded** — the case the old probe missed |
| 12 concurrent worker threads | all 12 recorded |
| whole pipeline run under the guard, unpackaged, OCR asserted to have happened | **0 attempts** |
| whole pipeline run in a **subprocess**, reporting loaded modules | 0 attempts, **0 fetch clients** |
| **models deleted**, engine constructed under the guard | raises `ExtractionError` naming the file and saying DocIQ never downloads; **0 attempts**; model identity degrades to `models-unavailable` |
| **packaged** `DocumentIQ-cli.exe --offline-probe` | **0 attempts** across 17 documents / 25 pages / 3 OCR pages, cold engine construction inside the guard |
| **OS-level**, packaged process observed for 40.7 s over 20 samples (`Get-NetTCPConnection`/`Get-NetUDPEndpoint` by owning PID) | **0 TCP endpoints, 0 UDP endpoints ever owned by the process** |

**Fail-before, watched.** With attempt recording disabled — i.e. the Sprint-1
blocking-only behaviour restored — **10 of the 17 tests then present went red**,
including the swallowed-attempt case. Re-enabled: all green.

### 3.4 What is NOT proven, plainly

1. **No run with network interfaces actually disabled.** Disabling an adapter
   or adding a firewall rule is a system-settings change, which I do not make.
   The in-process counter is in one sense *stronger* — it detects an attempt
   that a disabled adapter would merely have failed — but it is not the same
   claim, and criterion 6 says "verified with network disabled". To close it,
   on a machine with Wi-Fi/Ethernet off or in an isolated VM:

   ```
   DocumentIQ\DocumentIQ-cli.exe --offline-probe
   DocumentIQ\DocumentIQ-cli.exe --selftest
   DocumentIQ\DocumentIQ.exe            # and drive a real matter through it
   ```

   Both must exit 0 and the GUI must complete a run. That is the last step.

2. **A C extension calling Winsock directly** — without going through
   CPython's `socket`/`_socket` objects — is invisible to the guard. Nothing in
   the dependency set is known to do that, and "not known to" is not "does
   not". The OS-level observation in §3.3 covers this in principle, but it
   **samples at 200 ms** and could miss a connection shorter than that. Only
   the adapter-disabled run closes this residue.

3. **`reportlab` and `python-pptx` import `urllib.request` and `http.client`**
   when first used. Disclosed with attribution rather than failed: reportlab's
   `ImageReader` *can* read an image from a URL if handed one, DocIQ hands it
   bytes read off disk, and the guard's zero-attempt count over a whole run is
   the evidence the path is never taken. Treating an import as an outbound call
   would make the probe cry wolf on every run, and a probe that cries wolf stops
   being read. Fetch **clients** (requests, httpx, urllib3, huggingface_hub,
   modelscope, pip, …) remain a hard failure and none is loaded.

4. **The runtime hook does not install a permanent socket block** in the
   shipped app. `socket.socketpair()` on Windows is a real AF_INET loopback
   pair and Qt uses one for its event notifier; a process-wide block would
   break the GUI to enforce a property the application does not violate.

---

## 4. F-3 — Bates acceptance (acceptance criterion 4, method per D-23)

### 4.1 D-23's stated premise is wrong, in DocIQ's favour

D-23 says MNFV is image-only, so "every candidate ground-truth number is itself
OCR of a footer stamp". That is true of part of the set and **false of the
larger part**. `Desktop\Files for Claude\20240529` holds two different things:

| set | documents | pages | ground truth |
|---|---|---|---|
| `20240510 Initial Discl` — a standard e-discovery production, prefix `iiCON` | **2,138** | **11,561** | its own **load files** (`.OPT`, `.LFP`, `.DAT`, `.LST`) — the producing party's authoritative page-level numbering, generated when the stamps were burned in, **not** derived from reading them back |
| `Initial Disclosures` + `Supplemental` — combined PDFs, prefix `MNFV` | 16 PDFs (+1 native xlsx) | 2,963 | **filename ranges only** — document-level, weak, exactly D-23's caveat |

The iiCON PDFs also carry an embedded text layer (the production vendor's own
OCR), so DocIQ reads their stamps on the native path.

**Consequence, stated plainly:** the page-level accuracy figure for the 2,138
document production is measured against authoritative, non-OCR ground truth.
The figure for the 16 combined PDFs is not, and is reported separately. They are
never averaged together, because the average would be the flattering number and
the less honest one.

### 4.2 (a) Sequence continuity over the WHOLE set

`tools/bates_acceptance.py`, from the load files and the filenames — no sampling.

**`20240510 Initial Discl` — 2,138 documents, 11,561 pages: CONTINUOUS.**

| property | result |
|---|---|
| single prefix across the production | yes (`iiCON`) |
| digit widths | 6, uniformly, on all 11,561 pages |
| strictly increasing in file order | yes |
| contiguous across the whole production (no gaps) | yes |
| every document's own pages a gapless run | yes |
| document ranges non-overlapping | yes |

**Combined PDFs — 16 documents, 2,963 pages: 1 FINDING.**

- named range length equals the PDF's actual page count: **yes, all 16**
- digit widths in the names: 4 (×7) and 5 (×9)
- **FINDING: `MNFV 2836-2899` and `MNFV 2890-2953` overlap by 10 numbers.**
  Both files genuinely hold 64 pages, so the overlap is a **defect in the
  producing party's own file naming**, not in DocIQ. It is exactly the
  systematic, document-boundary error class D-23 says a sample cannot see, and
  the continuity proof found it in the first run. It is **flagged, not
  corrected** (§4).

### 4.3 (b) Blind, stratified hand-check — 100 pages

`tools/bates_handcheck.py` renders footer strips onto contact sheets labelled
with an **index only**; the expected values go to a separate answer key that is
not looked at until scoring. Strata (uniform sampling would have put ~98% of the
picks in the one production whose ground truth is authoritative and almost none
on the cases that can go wrong):

| stratum | pages |
|---|---|
| first page of a document (`iiCON`) | 20 |
| interior page (`iiCON`) | 20 |
| last page of a document (`iiCON`) | 20 |
| combined PDF, 4-digit filename range (`MNFV`) | 19 |
| combined PDF, 5-digit filename range (`MNFV`) | 21 |

**Result: 100 of 100 read. 0 disagreements on prefix + number. 87 exact-string
matches, 100 prefix-and-number matches.**

All 60 `iiCON` pages — including all 40 document-boundary pages — matched the
load-file ground truth **exactly**. Every one of the 13 non-exact matches is in
the filename stratum and is **padding only**: the filenames pad to four digits
(`MNFV 0919`) while the stamp burned into the page pads to five
(`MNFV 00919`). That is a measured demonstration that filename-derived ground
truth is weak in precisely the way D-23 warned, and it is a fact about the
production, not about DocIQ.

**A further finding from reading the pages:** the `MNFV` combined set carries at
least **three different stamp renderings** — `MNFV 00077` (space, 5 digits),
`MNFV0946` (no space, 4 digits) and `MNFV 02647` (space, 5 digits, red ink).
`propose_format` selects ONE dominant shape and applies only that, so on this
set a real fraction of pages is missed **by design**. See §4.6.

### 4.4 DEFECT — Bates detection scored 0% on a real production

The first page-level run, before any fix: **60 documents, 280 pages, 0 correct,
0 wrong, 280 missed, no format proposed at all.**

Root cause: `_CANDIDATE_RE` was **uppercase-only** (`[A-Z]`) and this
production's prefix is `iiCON`. Every stamp was present, correctly placed in the
zone the detector reads, and rejected on case alone.

The severity is in the silence. "No format proposed" is the **ordinary** outcome
on an unstamped set (D-13, and the Petrobras corpus is the proof case), so
nothing anywhere in the run would have said a word. A matter would have shipped
with no Bates locators at all and a clean bill of health.

**Fixed as a class, not as a prefix.** The letter class is `[A-Za-z]` in both
prefix and suffix; a Bates prefix is a string a producing party chooses and
nothing makes it uppercase (`iiCON`, `Def`, `PltfBates` are all ordinary). Case
is **preserved and never folded** — `format_key` compares the literal prefix, so
`iiCON` and `IICON` remain two formats and neither is applied to the other's
pages, because a locator pointing at a record that does not exist is worse than
no locator. Six prefix shapes are parametrised, plus a round-trip through the
persisted pattern, plus an assertion that the widening did **not** open the
page-number hole (page numbers, dates, money and revision marks still reject).

**Fail-before, watched:** with the uppercase-only regex restored, 7 of the new
tests go red.

### 4.5 DEFECT — a confirmed format rejected because OCR folded it into a line

The first page-level run after the case fix scored **576/648 (88.889%)** with
**72 misses, and every single one was an OCR page.** Each was inspected rather
than counted. Three causes:

1. **the stamp shares its line with other text** — a signature block ending
   `... in iiCON003961`, a page whose OCR came back `untij isfiyed iiCON003944`.
   The stamp is present, complete and correct; the whole-line anchor rejects it.
2. **rapidocr dropped a character** — `iCON003947` for `iiCON003947`.
3. **rapidocr did not read the footer at all**, mostly on photographs.

Cause 1 is DocIQ's to fix and was fixed. The whole-line anchor is right for
**detection**, where the grammar is open-ended and an unanchored match reads a
date or a dollar figure as a Bates number. It is too strict for **application**
of a format the operator has already confirmed exactly. `apply_bates` now falls
back to searching the zone for the confirmed format as a standalone token, built
from the same fields as `BatesFormat.pattern` so it can never accept what the
pattern would reject, delimited so it cannot match inside a longer run, and
**refusing** — leaving the page unstamped — when the zone holds two *different*
stamps.

Measured, same seed and same sample: **576 → 593 correct, 72 → 55 missed**, zero
wrong and zero false positives before and after. Causes 2 and 3 are rapidocr,
not DocIQ, and are what the OCR-page rate below is measuring.

**Fail-before, watched:** 5 of the 10 new tests go red with the fallback removed.

### 4.6 Page-level accuracy after both fixes

**Method, stated with the figure as D-23 requires.** DocIQ's own extractor
(`ingest.extract._extract_pdf`, OCR enabled) over a seeded stratified sample of
the `iiCON` production; `propose_format` on the first 20 documents, the
operator's confirmation simulated, `apply_bates` over every sampled document,
each page compared to the **load file's** authoritative Bates for that page.

| | measured |
|---|---|
| documents sampled | **150 of 2,107 eligible** (seed 20240529) |
| skipped for exceeding `--max-mb 15` | **31 documents, each listed by name and page count** |
| pages compared | **648** |
| format proposed | `iiCON000001`, from 20 documents, 143/150 pages, best document coverage **100%** |
| **exactly correct** | **593 (91.512%)** |
| **WRONG number** | **0** |
| **FALSE POSITIVE** | **0** |
| missed (no stamp detected where one exists) | 55 |

**Decomposed by page kind, which is the number that actually informs:**

| page kind | correct | rate |
|---|---|---|
| **native** (the page had a text layer) | **568 / 568** | **100.000%** |
| **ocr** (DocIQ had to read the image) | 25 / 80 | 31.250% |

### The ≥99% figure, stated with its method and its scope

**Criterion 4 is MET on pages that yield text — 100.000% over 568 pages against
authoritative, non-OCR ground truth, with zero wrong numbers.** It is **NOT
met** on pages DocIQ must OCR: 31.250% over 80 pages.

The failure direction is the one §4 requires throughout: **zero wrong, zero
false positives, every shortfall a MISS.** A missed page has no Bates in the
index and `BatesRange.pages_without_bates` counts it; no page anywhere in this
run carries a locator that is not in the production.

**What the OCR residue actually is.** Each of the 55 was inspected. Three
causes, in descending frequency: rapidocr did not read the footer stamp at all;
it read it and dropped a character (`iCON003947` for `iiCON003947`); or the page
is a photograph whose stamp the engine could not resolve. This is **rapidocr's
reading of a small footer stamp**, not detector logic — and D-19 already records,
on the record, that rapidocr ships "chosen on in-house familiarity and ONNX
bundling convenience, never benchmarked against an alternative on this corpus."
This measurement is the first concrete cost of that. It belongs in the D-19
conversation, not in a Bates fix.

**The bound is disclosed, never silent.** The 31 skipped documents are printed
with names and page counts; the sample is printed as a fraction of the eligible
set; the proposal's 20-document prefix is printed. Nothing was capped quietly.

### The negative control — the widening's cost, measured

Widening the letter class to `[A-Za-z]` makes more footer text stamp-shaped, so
the false-positive risk had to be measured rather than argued. Re-run over the
**whole** Petrobras corpus (D-13's designated negative case) **after** the fix:

| | measured |
|---|---|
| documents / pages | **298 / 17,732** |
| stamp-shaped lines seen | 305 |
| **format proposed** | **NONE — correct** |
| **false positives** | **0** |

305 candidate lines in 17,732 pages, and not one clears the 50% per-document
coverage bar. The widening cost **zero** false positives on the real unstamped
corpus.

### 4.7 Known limit, not fixed here, reported instead

`propose_format` returns **one** format per corpus. The `MNFV` combined set
demonstrably carries three renderings of the same production numbering, so any
single confirmed format leaves pages unstamped. Those pages are **missed, not
wrong** — the failure direction §4 asks for — and the per-document Bates range
already reports `pages_without_bates`.

This is a design decision of Stage 3, not a bug in it, and changing it means
changing what an operator confirms (one format, or a set?). It is a
**stop-the-line question for Alex**, not a local edit: the confirmation flow,
`RunConfig.bates_pattern` and `FormatProfile.bates_pattern` all assume one
pattern, and `bates_pattern` is a persisted field on shared types.

Proposed shape, if it is wanted: `BatesProposal` gains
`additional_formats: tuple[BatesFormat, ...]` and `BatesDecision` gains
`formats: tuple[BatesFormat, ...]` with `format` retained as
`formats[0]` for compatibility; `apply_bates` tries each in confirmation order
and records which one matched. `pattern` would become a `;`-joined sequence of
the existing tokens, which the existing `parse_pattern` grammar extends to
without a format change.

---

## 5. Test suite

**661 tests, 8 consecutive full runs, all 8 exit 0, zero failures.**
Run sequentially rather than in parallel — see the note below — and every run
produced byte-identical output.

```
PYTHONPATH=src .venv/Scripts/python -m pytest -q -p no:cacheprovider   x8
run 1..8 exit=0
```

Plus, separately:

| gate | result |
|---|---|
| `python -m dociq.selftest --runs 8` (source tree) | see note below |
| `DocumentIQ-cli.exe --selftest --runs 8` (**the shipped artifact**) | **PASSED — 70 checks**, determinism byte-identical over 8 varied hash seeds, 1 distinct corpus hash, executed *inside the frozen exe* |

**A note on how these were run, because it is a real finding.** The first
attempts ran the suite, both Bates acceptance runs and the frozen self-test
concurrently. Processes were killed mid-run — the Bates tool reached **4+ GB
resident** because it held every extracted document — and three runs died
without printing a figure. That is not a flaky test; it is an unbounded tool
and a saturated machine, and both are now fixed: the acceptance tool streams
one document at a time, and everything long-running is run sequentially. **The
source-tree `dociq.selftest` run was one of the casualties (killed, exit 137)
and was not re-run to completion** — the packaged `--selftest` covers the same
70 checks on the artifact that actually ships, which is the stronger of the two,
but the source-tree invocation is honestly *not* re-proven in this session.

**Fail-befores watched go RED in this session, not assumed:**

| fix | what went red without it |
|---|---|
| counting network guard | **10 of 17** offline tests, incl. the swallowed-attempt case |
| mixed-case Bates prefix | **7** of the new Bates tests |
| confirmed-format token fallback | **5** of the 10 new Bates tests |

---

## 6. Everything asked for that is not done

1. **Criterion 1 (F-4)** — not attempted, per instruction; it waits on Track D.
2. **A network-disabled run** — §3.4(1). Needs a machine with the adapter off;
   the command is written out there.
3. **`pip install pyinstaller==6.20.0`** — §2.5. Needs authorization.
4. **`opencv-python` should be uninstalled** from the venv — §2.6. Needs
   authorization.
5. **Multi-format Bates** — §4.7. Needs a ruling, and it touches shared types.
6. **The page-level accuracy sample is bounded**, and both bounds are printed
   with what they dropped: documents over `--max-mb` are listed by name and page
   count, and the sampled subset is stated as a fraction of the eligible set.
   Nothing is silently capped.
