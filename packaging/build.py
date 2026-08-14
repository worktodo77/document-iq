"""Build the D-22 deliverable: one folder, shipped as one zip. Reproducibly.

    python packaging/build.py [--skip-verify] [--pyinstaller-path DIR]

The build is a script rather than a command in a README because the
requirement is that it be reproducible *from committed artifacts* — a spec file
plus a command line remembered from a shell session is not. Everything the
build depends on that is not in the spec is here: where the output goes, what
is cleaned first, what the zip is called, and — the part a README always drifts
from — the verification that the thing that came out actually runs.

WHAT "VERIFIED" MEANS HERE
Three checks, on the built artifact, not on the source tree:

  1. ``DocumentIQ-cli.exe --version``      — imports resolve, models found,
                                             and the cold-start figure D-22's
                                             reasoning rests on is MEASURED
  2. ``DocumentIQ-cli.exe --offline-probe``— a whole pipeline run under the
                                             network guard, zero attempts
  3. ``DocumentIQ.exe`` launches            — the windowed exe is started and
                                             observed alive, then closed

Check 3 is the one a packaging change most often breaks and the one most often
skipped, because a windowed process writes nothing to a console. It is done by
launching it and asserting the process is still alive after a grace period: a
frozen GUI whose imports fail dies within a second or two, so "still running"
is a real signal rather than a formality.

The zip is deterministic in ORDER (entries sorted) but not byte-identical —
zip entries carry mtimes and PyInstaller stamps a build time. That is stated
rather than papered over: DocIQ's byte-identical claim is about the OUTPUT
CORPUS, never about the installer, and widening it here would be exactly the
"withdraw the claim, not just the code" mistake in reverse.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "packaging" / "DocumentIQ.spec"
DIST = REPO / "build_out" / "dist"
WORK = REPO / "build_out" / "work"
APP_DIR = DIST / "DocumentIQ"

GUI_EXE = APP_DIR / "DocumentIQ.exe"
CLI_EXE = APP_DIR / "DocumentIQ-cli.exe"

COLD_START_RUNS = 30
"""Launch measurements are timing measurements, and one of those establishes
nothing. Thirty, per the standing rule, and the first is reported apart from the
rest because it measures something different (see :func:`verify`)."""

GUI_ALIVE_GRACE_S = 8.0
"""How long the windowed exe must stay up to count as launched. A frozen build
whose imports fail exits well inside this; a healthy one sits in its event loop
forever. Stated, not tuned: it is generous enough that a slow cold start on a
loaded machine is not read as a crash."""


def _human(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def run_pyinstaller(pyinstaller_path: str | None) -> None:
    env = dict(os.environ)
    if pyinstaller_path:
        # Only needed when PyInstaller is not installed into the DocIQ venv.
        # See docs/build/packaging.md — the supported configuration is
        # `pip install pyinstaller==6.20.0` into the venv, and this switch
        # exists so a build is possible before that authorization lands.
        env["PYTHONPATH"] = (pyinstaller_path + os.pathsep
                             + env.get("PYTHONPATH", "")).rstrip(os.pathsep)
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC),
           "--distpath", str(DIST), "--workpath", str(WORK),
           "--noconfirm", "--clean", "--log-level", "WARN"]
    print("  " + " ".join(cmd))
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(REPO), env=env)
    if proc.returncode != 0:
        raise SystemExit(f"PyInstaller failed with exit code {proc.returncode}")
    print(f"  build took {time.perf_counter() - t0:.1f} s")


def make_zip() -> Path:
    zip_path = DIST / "DocumentIQ-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    files = sorted(p for p in APP_DIR.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for f in files:
            zf.write(f, Path("DocumentIQ") / f.relative_to(APP_DIR))
    return zip_path


def verify(app_dir: Path) -> list[str]:
    """Run the built artifact. Returns findings; empty means verified."""
    findings: list[str] = []
    for exe in (GUI_EXE, CLI_EXE):
        if not exe.is_file():
            findings.append(f"missing executable: {exe.name}")
    if findings:
        return findings

    print(f"\n  [1/3] DocumentIQ-cli.exe --version x{COLD_START_RUNS} "
          "(cold-start measurement)")
    walls: list[float] = []
    for i in range(COLD_START_RUNS):
        t0 = time.perf_counter()
        p = subprocess.run([str(CLI_EXE), "--version"], capture_output=True,
                           text=True, timeout=600)
        walls.append(time.perf_counter() - t0)
        if i == 0:
            print("".join(f"        {ln}\n"
                          for ln in (p.stdout or "").splitlines()))
        if p.returncode != 0:
            findings.append(f"--version run {i} exited {p.returncode}: "
                            f"{(p.stderr or p.stdout)[-500:]}")
            break
    if walls:
        ordered = sorted(walls)
        # The FIRST run is reported separately and never folded into the
        # median. On Windows the first execution of the payload — measured at
        # 393.1 MB across 939 files on 2026-08-01, see docs/build/packaging.md;
        # this comment said 388 MB and cited nothing — of never-seen
        # binaries is dominated by the on-access antivirus scan, which is a
        # real cost a client pays exactly once and a completely misleading
        # figure for steady-state launch. Reporting one number for both would
        # either overstate every launch or hide the first-run cost — and the
        # first-run cost is the one D-22's endpoint-protection reasoning is
        # about.
        rest = sorted(walls[1:]) or ordered
        print(f"        first launch (cold, AV-scanned): {walls[0]:.3f} s")
        print(f"        subsequent launches, n={len(rest)}: "
              f"min {rest[0]:.3f} s / median {rest[len(rest) // 2]:.3f} s / "
              f"max {rest[-1]:.3f} s")

    print("  [2/3] DocumentIQ-cli.exe --offline-probe")
    p = subprocess.run([str(CLI_EXE), "--offline-probe"], capture_output=True,
                       text=True, timeout=3600)
    print("".join(f"        {ln}\n" for ln in (p.stdout or "").splitlines()))
    if p.returncode != 0:
        findings.append(f"--offline-probe exited {p.returncode}: "
                        f"{(p.stderr or p.stdout)[-1500:]}")

    print(f"  [3/3] DocumentIQ.exe launches and stays up "
          f"{GUI_ALIVE_GRACE_S:.0f}s")
    proc = subprocess.Popen([str(GUI_EXE)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    time.sleep(GUI_ALIVE_GRACE_S)
    if proc.poll() is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "replace")[-1500:]
        findings.append(f"the windowed exe exited on its own with code "
                        f"{proc.returncode}: {err}")
    else:
        print("        still running — terminating")
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="packaging/build.py")
    ap.add_argument("--skip-verify", action="store_true",
                    help="build only. NOT the supported path: an unlaunched "
                         "packaging change is not a deliverable.")
    ap.add_argument("--pyinstaller-path", default=os.environ.get(
        "DOCIQ_PYINSTALLER_PATH"),
        help="directory to prepend to PYTHONPATH when PyInstaller is not "
             "installed into this interpreter's environment")
    args = ap.parse_args(argv)

    print(f"LI Document IQ — packaging (D-22 one-folder build)\n"
          f"  repo   : {REPO}\n  python : {sys.executable}\n"
          f"  spec   : {SPEC}")
    shutil.rmtree(DIST, ignore_errors=True)
    run_pyinstaller(args.pyinstaller_path)

    if not APP_DIR.is_dir():
        raise SystemExit(f"expected {APP_DIR} to exist after the build")
    payload = _dir_size(APP_DIR)
    n_files = sum(1 for p in APP_DIR.rglob("*") if p.is_file())
    print(f"\n  folder : {APP_DIR}\n  payload: {_human(payload)} "
          f"across {n_files} file(s)")

    findings: list[str] = []
    if not args.skip_verify:
        findings = verify(APP_DIR)

    zip_path = make_zip()
    print(f"\n  zip    : {zip_path} ({_human(zip_path.stat().st_size)})")

    if findings:
        print(f"\nBUILD VERIFICATION FAILED — {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        return 1
    if args.skip_verify:
        print("\nBUILT, NOT VERIFIED (--skip-verify)")
        return 0
    print("\nBUILD VERIFIED — both executables ran from the built folder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
