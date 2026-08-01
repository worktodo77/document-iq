# Building the deliverable (D-22)

**What ships:** one folder, zipped once. `DocumentIQ-win64.zip` unpacks to a
`DocumentIQ\` directory containing `DocumentIQ.exe`, `DocumentIQ-cli.exe` and
`_internal\`. The user unpacks it and runs the exe. No installer, no admin
rights, no external runtime dependencies (§10, still binding).

**What does not ship:** a `--onefile` executable. D-22 rules it out and the
reasoning is measured rather than asserted — see *Measured* below.

## The command

```
pip install pyinstaller==6.20.0      # into document-iq\.venv — see "Authorization" below
cd C:\Users\Alex\document-iq-wt\track-f
C:\Users\Alex\document-iq\.venv\Scripts\python.exe packaging\build.py
```

That is the whole build. It cleans `build_out\`, runs PyInstaller against the
committed `packaging\DocumentIQ.spec`, **runs the built artifact**, and writes
the zip. It exits non-zero if the artifact does not run, which is the point: a
packaging change that was never launched is not a deliverable.

| file | role |
|---|---|
| `packaging/DocumentIQ.spec` | the reproducible half — what goes in, what is excluded, both executables, the icon |
| `packaging/build.py` | the other reproducible half — paths, cleaning, zipping, and the three verification checks |
| `packaging/dociq_launcher.py` | the frozen entry point: bundle bootstrap plus `--version` / `--selftest` / `--offline-probe` |
| `packaging/rthook_offline.py` | runtime hook: pins the bundled model directory and the ecosystem offline flags before any import |

### Authorization note — PyInstaller is not installed in the venv

D-11 pins the dependency set to `document-iq\.venv`, and PyInstaller is **not**
in it. Installing it needs Alex's authorization, which had not been given when
this was built. The build above is therefore the *supported* command and is not
the one that produced the first artifact.

What produced it: PyInstaller 6.20.0 and its four dependencies (`altgraph`,
`pefile`, `pywin32_ctypes`, `_pyinstaller_hooks_contrib`) were copied out of
`C:\Users\Alex\mip39-prototype\.venv` — same Python 3.14, no compiled
extensions of its own — into a scratch directory, and put on `PYTHONPATH` for
the build only:

```
set DOCIQ_PYINSTALLER_PATH=<scratch dir with PyInstaller on it>
C:\Users\Alex\document-iq\.venv\Scripts\python.exe packaging\build.py
```

`build.py --pyinstaller-path` exists for exactly that. Nothing was installed,
nothing in the venv was modified, and no network call was made. It is a
workaround and it is recorded as one: **the standing build command is the `pip
install` above, and it should be run once so this note can be deleted.**

## What is bundled, and why each entry is load-bearing

- **The three ONNX OCR models.** Principle 4 admits no network call, so the
  weights must already be present. The spec *fails the build* if it collects
  fewer than three — a build that silently shipped two would OCR nothing and
  the corpus would take the blame.
- **rapidocr's `config.yaml` files.** Read at engine construction even though
  DocIQ passes explicit model paths. Their absence fails only in the frozen
  build.
- **`assets/branding/`** — the D-08 icon and the D-09 lockup, and less
  obviously `li_monogram_source.png`, which `dociq.branding.palette` *samples*
  the brand colours from at import time. Omit it and the packaged GUI raises
  before it draws anything.
- **`tests/fixtures/make_fixtures.py`, as a module.** So `--selftest` and
  `--offline-probe` run on the shipped artifact rather than only on a checkout.

## Verifying the artifact

Three checks, run automatically by `build.py`, and all three runnable by hand —
including by a client's IT reviewer, which is what §10's "must be verifiable"
clause asks for:

```
DocumentIQ\DocumentIQ-cli.exe --version          # models found, cold start
DocumentIQ\DocumentIQ-cli.exe --offline-probe    # ZERO outbound attempts
DocumentIQ\DocumentIQ-cli.exe --selftest         # the full end-to-end gate
DocumentIQ\DocumentIQ.exe                        # the application itself
```

`--offline-probe` runs a whole pipeline over the bundled fixture corpus with
`dociq.verify.offline.NetworkGuard` installed. The guard **counts** attempts
before it blocks them, and the assertion is zero — not "the run survived".
A library that attempts a fetch inside `try/except Exception` passes a
blocking-only probe in silence and still writes a packet the moment the block
comes off on a client machine.

## Measured (2026-08-01, this machine)

| quantity | measured |
|---|---|
| unpacked payload | **388.7 MB across 932 files** |
| shipped zip | **176.2 MB** |
| PyInstaller build wall clock | ~53 s |
| first launch of a never-run copy | **2.2 s** (dominated by the on-access AV scan of 388 MB of new binaries) |
| steady-state launch, 29 further runs | **min 0.31 s / median 0.49 s** |

The steady-state figure is what D-22's reasoning is about and it had never been
measured. It is reported over 30 runs, with the first held out and named,
because the first run measures something different — a cost the client pays
once — and folding it into a median would either overstate every launch or hide
the first-run cost.

`--onefile` was **not** built for comparison, so "onefile would be slower" is
still the argument D-22 makes and not a measurement. What *is* now measured is
that the one-folder build's steady-state launch is half a second, and that the
payload it would have to re-extract on every launch is 388 MB.

## What is deliberately not claimed

The zip is **not** byte-identical between builds. Zip entries carry mtimes and
PyInstaller stamps a build time. DocIQ's byte-identical claim is about the
**output corpus** (Principle 5, the freeze) and has never covered the
deliverable; widening it here would be a claim nobody could keep.
