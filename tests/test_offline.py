"""Acceptance criterion 6 / Principle 4 — the offline claim, tested.

The claim §10 makes is "makes no outbound connections", not "survives having
none". These tests exist because the difference is not academic: the probe that
shipped in Sprint 1 blocked ``socket.socket`` and asserted OCR still worked, and
:func:`test_a_swallowed_attempt_is_still_a_finding` is the case that construction
passes and this one catches.
"""

from __future__ import annotations

import os
import socket
import ssl
import sys
import threading
import urllib.request

import pytest

from dociq.verify import offline


PROBE_SOURCE = """
import sys, tempfile, pathlib
sys.path.insert(0, sys.argv[1])
import make_fixtures
from dociq import pipeline
from dociq.contracts import RunConfig
from dociq.ingest import extract as ex, walker
from dociq.profiles.model import OperatorStamp
from dociq.verify import offline

work = pathlib.Path(tempfile.mkdtemp())
src = make_fixtures.build(work / 'fx')
ex._OCR_ENGINE = None
with offline.no_network() as guard:
    pipeline.run(
        RunConfig(source_root=str(src), output_root=str(work / 'out'),
                  ocr_engine_version=ex.ocr_engine_version()),
        pipeline.PipelineOptions(walk=walker.WalkOptions(resume=False),
                                 matter_name='probe',
                                 stamp=OperatorStamp('p', '2026-08-01T00:00:00Z',
                                                     'p')))
_child = set(offline.enumerate_child_process_entry_points())
_spawn = [a for a in guard.attempts if a.entry_point in _child]
_net = [a for a in guard.attempts if a.entry_point not in _child]
print('ATTEMPTS=' + str(len(guard.attempts)))
print('ATTEMPTS_NET=' + str(len(_net)))
print('ATTEMPTS_SPAWN=' + str(len(_spawn)))
print('FETCH=' + ','.join(offline.audit_model_fetch_imports()))
print('TRANSPORT=' + ','.join(offline.audit_transport_imports()))
if guard.attempts:
    print('DETAIL_BEGIN')
    print(guard.render())
    print('DETAIL_END')
"""
"""Run in a SUBPROCESS so the answer is about a clean interpreter. In-process,
pytest itself, the plugins, and every earlier test in the session have already
imported half the stdlib — a `sys.modules` question asked inside a test session
is asked of the wrong process."""



def test_the_guarded_set_is_enumerated_not_described():
    """Fix the class: the covered entry points are a value, not prose."""
    names = offline.enumerate_guarded_entry_points()
    assert "socket.socket" in names
    assert "_socket.socket" in names, (
        "the C base class is reachable by `import _socket` and bypasses a "
        "rebind of socket.socket entirely")
    assert "socket.getaddrinfo" in names, "DNS is an outbound packet"
    assert "ssl.SSLContext.wrap_socket" in names
    assert len(set(names)) == len(names)


def test_no_outbound_capable_socket_sibling_is_unguarded():
    """The global class assertion, read off the LIVE socket module.

    A list written once drifts; this reads ``dir(socket)`` so a name a future
    CPython adds surfaces here instead of silently becoming a hole.
    """
    assert offline.audit_siblings() == ()


def test_a_clean_block_reports_clean():
    with offline.no_network() as guard:
        pass
    assert guard.clean
    assert "no outbound or child-process attempt" in guard.render()
    # The count in the sentence must be the count of things actually guarded,
    # not a literal that drifts as the guarded set grows.
    total = (len(offline.enumerate_guarded_entry_points())
             + len(offline.enumerate_child_process_entry_points()))
    assert f"{total} guarded entry points" in guard.render()


@pytest.mark.parametrize("call", [
    pytest.param(lambda: socket.socket(), id="socket.socket"),
    pytest.param(lambda: socket.create_connection(("127.0.0.1", 9)),
                 id="create_connection"),
    pytest.param(lambda: socket.getaddrinfo("example.invalid", 80),
                 id="getaddrinfo"),
    pytest.param(lambda: socket.gethostbyname("example.invalid"),
                 id="gethostbyname"),
    pytest.param(lambda: socket.socketpair(), id="socketpair"),
    pytest.param(lambda: ssl.create_default_context().wrap_socket(
        socket.socket()), id="ssl.wrap_socket"),
    pytest.param(lambda: urllib.request.urlopen("http://example.invalid"),
                 id="urllib-through-the-stack"),
    pytest.param(lambda: __import__("_socket").socket(), id="_socket.socket"),
])
def test_every_guarded_entry_point_is_actually_reached(call):
    """The fail-before, one case per entry point.

    Each of these WOULD have gone out on a connected machine. If any one stops
    being recorded, the guard has a hole and this goes red — which is the only
    way a coverage claim about a guard means anything.
    """
    with offline.no_network() as guard:
        with pytest.raises(Exception):
            call()
    assert not guard.clean, "the attempt was not recorded"
    assert guard.attempts[0].stack, "an attempt with no stack names nobody"


def test_a_swallowed_attempt_is_still_a_finding():
    """The whole reason this module replaced the old probe.

    A library that tries to fetch inside ``try/except Exception`` passes a
    blocking probe in total silence and still writes a packet the moment the
    block is lifted on a client machine. Counting is what makes it visible.
    """
    def politely_gives_up():
        try:
            socket.create_connection(("example.invalid", 443), timeout=1)
        except Exception:
            return "fell back to the bundled asset"
        return "fetched"

    with offline.no_network() as guard:
        assert politely_gives_up() == "fell back to the bundled asset"

    assert not guard.clean, (
        "a swallowed attempt reported clean — this is exactly the failure the "
        "blocking-only probe had")
    assert "create_connection" in guard.attempts[0].entry_point


def test_attempts_from_worker_threads_are_recorded():
    """OCR fans across ~16 threads; an attempt from any of them must land."""
    def worker():
        try:
            socket.socket()
        except Exception:
            pass

    with offline.no_network() as guard:
        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert len(guard.attempts) == 12


def test_the_guard_restores_everything_it_touched():
    before = {
        "socket.socket": socket.socket,
        "_socket.socket": sys.modules["_socket"].socket,
        "socket.create_connection": socket.create_connection,
        "socket.getaddrinfo": socket.getaddrinfo,
        "ssl.wrap_socket": ssl.SSLContext.wrap_socket,
    }
    with offline.no_network():
        assert socket.socket is not before["socket.socket"]
    assert socket.socket is before["socket.socket"]
    assert sys.modules["_socket"].socket is before["_socket.socket"]
    assert socket.create_connection is before["socket.create_connection"]
    assert socket.getaddrinfo is before["socket.getaddrinfo"]
    assert ssl.SSLContext.wrap_socket is before["ssl.wrap_socket"]


def test_nested_guards_restore_in_order():
    original = socket.socket
    with offline.no_network():
        with offline.no_network():
            pass
        assert socket.socket is not original, (
            "the inner guard's exit uninstalled the outer guard")
    assert socket.socket is original


def test_no_fetch_client_is_loaded_by_a_WHOLE_PIPELINE_RUN():
    """The fetch-client class, closed over a RUN and not merely over imports.

    The first version of this test imported ``dociq.pipeline`` and checked
    ``sys.modules``. It passed, and it was too weak: the frozen build's
    ``--offline-probe`` — which RUNS the pipeline rather than importing it —
    immediately reported ``urllib.request`` and ``http.client`` loaded, because
    ``reportlab`` (run_summary.pdf) and ``python-pptx`` pull them in when they
    are first used. An import-only probe cannot see a module a run loads, and
    the run is what ships.

    So the check is now a subprocess that performs a whole pipeline run and
    then reports. Fetch CLIENTS must be absent; stdlib transport is separately
    reported with attribution (see ``offline.TRANSPORT_MODULES``) and the
    guard's zero-attempt count is the assurance there.

    **This test fails intermittently under concurrent load, and its failure was
    not diagnosable.** Observed 2 failures in 24 concurrent jobs during the
    claims sweep, then 2 in 6 immediately after the A-15 commit — while a
    ``git push`` and other agents were running — then **15 consecutive full-suite
    runs green**, and 6/6 green in isolation. It has never reproduced on demand.

    The three ways this test can fail are not equally serious and the summary
    line does not distinguish them:

    * the **subprocess did not run** — a harness problem under load;
    * **ATTEMPTS was non-zero** — something tried to open a socket;
    * **a fetch client was loaded** — criterion 6 is not met.

    Only the last two are findings about the product, and one of them would
    withdraw a claim made to law-firm IT. So every assertion below now carries
    the return code, stdout and stderr: the next occurrence has to be
    self-explaining, because a rate estimate is not a diagnosis and this has now
    cost three attempts at one.

    **2026-08-04 — the first occurrence that said anything.** Under two
    full-suite runs racing each other on one host (an agent's launcher started
    the same script twice; that is a harness fault, and it is stated because it
    is the load condition, not the cause), the probe reported::

        ATTEMPTS=7  FETCH=  TRANSPORT=http.client,urllib.request

    ``returncode`` was 0 and no fetch client was loaded, so this was NOT the
    harness failure the previous rounds assumed. Seven guarded entry points were
    reached. Which ones, and from where, the probe did not say — it printed a
    COUNT, and a count is the same shape of non-answer as the summary line this
    docstring already complains about. ``NetworkGuard`` records the entry point,
    the argument and the Python stack of every attempt, and the probe threw all
    three away.

    So the probe now prints ``guard.render()`` after a ``DETAIL_BEGIN`` marker
    whenever the count is non-zero, and ``_detail`` carries it into the failure
    message. It is still not retried and still not marked flaky, for the reason
    below.

    **And with the stacks, it reproduced on demand and was not what it said.**
    Three concurrent probe loops, 75 runs: **12 tripped**, and all **84**
    recorded attempts across those 12 were the same thing —
    ``subprocess.Popen('ver', shell=True)``, raised by ``platform.uname()``
    inside ``onnxruntime``'s import, reached from
    ``dociq.ingest.extract.ocr_model_dir()`` during
    ``walker.effective_limits(...)``. **Zero socket attempts, in any run.**
    ``platform.uname()`` caches, so whether the spawn happens at all depends on
    whether something warmed that cache before the guard opened — which is why
    it looked like load-dependent noise for three rounds.

    That makes the old single ``ATTEMPTS`` count the actual defect in this test:
    it folded the socket guard and the child-process guard into one number and
    then labelled the sum "outbound" and "A CRITERION 6 FINDING". Every
    occurrence so far was mis-reported. The counts are split below, and each
    half fails with what it actually means. **Neither half was weakened** — a
    spawn inside the guard still fails the suite; whether DocIQ should tolerate
    ``platform.uname()``'s ``cmd /c ver`` during an OCR-model import is a gate
    question, and answering it by relaxing an assertion is not this test's to
    make.

    Deliberately NOT retried and NOT marked flaky. A retry would convert the one
    signal that distinguishes a harness failure from a real one into silence.
    """
    import subprocess

    code = PROBE_SOURCE
    proc = subprocess.run([sys.executable, "-c", code, str(_fixtures_dir())],
                          capture_output=True, text=True, env=_env_with_src())

    def _detail(headline: str) -> str:
        return (
            f"{headline}\n"
            f"  returncode: {proc.returncode}\n"
            f"  stdout:\n{proc.stdout.strip() or '(empty)'}\n"
            f"  stderr:\n{proc.stderr.strip()[-4000:] or '(empty)'}"
        )

    assert proc.returncode == 0, _detail(
        "the offline probe SUBPROCESS did not complete — this is a harness "
        "failure, not evidence about the product, and it must be read as one")
    # Parse only the header lines. Everything from ``DETAIL_BEGIN`` on is the
    # rendered attempts — entry point, argument and Python STACK for each — and a
    # traceback line containing ``=`` must not be able to invent a key here.
    header: list[str] = []
    for line in proc.stdout.strip().splitlines():
        if line.strip() == "DETAIL_BEGIN":
            break
        header.append(line)
    out = dict(line.split("=", 1) for line in header if "=" in line)
    assert "ATTEMPTS" in out, _detail(
        "the probe ran but reported no ATTEMPTS line — its output is malformed, "
        "so this is a harness failure and NOT an offline finding")
    # The count is split because the two halves are DIFFERENT claims and the
    # single number said the wrong thing about every occurrence so far. Measured
    # 2026-08-04: 12 of 75 probe runs under three concurrent loops tripped the
    # guard, and all 84 recorded attempts across them were
    # `subprocess.Popen('ver', shell=True)` — `platform.uname()` inside
    # `onnxruntime`'s import, reached from `extract.ocr_model_dir()`. Not one
    # socket was touched. The old message called every one of those "outbound"
    # and "A CRITERION 6 FINDING", which is a false statement about the product
    # printed by the test that exists to keep the product's statements true.
    assert "ATTEMPTS_NET" in out and "ATTEMPTS_SPAWN" in out, _detail(
        "the probe reported no split attempt counts — its output is malformed, "
        "so this is a harness failure and NOT an offline finding")
    assert out["ATTEMPTS_NET"] == "0", _detail(
        f"a whole pipeline run made {out['ATTEMPTS_NET']} OUTBOUND attempt(s) "
        f"on a socket or TLS entry point — THIS IS A CRITERION 6 FINDING, not "
        f"a flake. The stack of each attempt is below.")
    assert out["ATTEMPTS_SPAWN"] == "0", _detail(
        f"a whole pipeline run spawned {out['ATTEMPTS_SPAWN']} CHILD "
        f"PROCESS(es) inside the guard. This is NOT an outbound-network "
        f"finding and must not be reported as one — no socket was touched. It "
        f"is the child-process half of the guard, which exists because a child "
        f"leaves the scope of every rebind in this interpreter. The stack of "
        f"each attempt is below and names the caller.")
    assert out["ATTEMPTS"] == "0", _detail(
        f"the guard recorded {out['ATTEMPTS']} attempt(s) that the split above "
        f"did not account for — the classification and the guard disagree")
    fetch = [m for m in out.get("FETCH", "").split(",") if m]
    assert fetch == [], _detail(
        f"a whole pipeline run loaded fetch clients {fetch} — THIS IS A "
        f"CRITERION 6 FINDING, not a flake")


def _env_with_src():
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    return env


@pytest.mark.slow
def test_a_whole_pipeline_run_makes_zero_outbound_attempts(tmp_path):
    """The real thing: every stage, OCR included, under the counting guard.

    OCR is asserted to have actually happened. A run that never reached the
    OCR path would pass this trivially while proving nothing about the path the
    model download used to live on — "the corpus doesn't exercise it" selects
    nothing.
    """
    sys.path.insert(0, str(_fixtures_dir()))
    import make_fixtures

    from dociq import pipeline
    from dociq.contracts import PageKind, RunConfig
    from dociq.ingest import extract as ex
    from dociq.ingest import walker
    from dociq.profiles.model import OperatorStamp

    src = make_fixtures.build(tmp_path / "fixtures")
    ex._OCR_ENGINE = None  # construction must happen INSIDE the guard
    with offline.no_network() as guard:
        outcome = pipeline.run(
            RunConfig(source_root=str(src), output_root=str(tmp_path / "out"),
                      ocr_engine_version=ex.ocr_engine_version()),
            pipeline.PipelineOptions(
                walk=walker.WalkOptions(resume=False),
                matter_name="offline probe",
                stamp=OperatorStamp("probe", "2026-08-01T00:00:00Z", "probe")))
    ocr_pages = [p for d in outcome.result.documents for p in d.pages
                 if p.kind is PageKind.OCR]
    assert ocr_pages, "the run never took the OCR path"
    assert outcome.accounting.ok
    assert guard.clean, guard.render()


def _fixtures_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# The model-fetch class, closed by DEMONSTRATION rather than by grep
# ---------------------------------------------------------------------------


def test_absent_models_fail_loudly_and_never_reach_for_a_download():
    """Take the weights away and watch what the code does.

    This is the direct proof that the vendored ``enable_os_trust()`` download
    path is closed. Grepping for it shows it is not in the source; it does not
    show what happens when the models are genuinely missing, which is the only
    state in which a fetch would ever have fired. So the model directory is
    pointed at an empty folder and the OCR path is driven under the network
    guard:

      * ``ocr_models_present()`` must be False and say which file and how to fix
      * engine construction must raise ``ExtractionError``, not download
      * the guard must record ZERO attempts — nothing tried to go and get them
    """
    import tempfile

    from dociq.contracts import ExtractionError
    from dociq.ingest import extract as ex

    empty = tempfile.mkdtemp(prefix="dociq-no-models-")
    saved_env = os.environ.get("DOCIQ_OCR_MODEL_DIR")
    saved_engine = ex._OCR_ENGINE
    os.environ["DOCIQ_OCR_MODEL_DIR"] = empty
    ex._OCR_ENGINE = None
    try:
        ok, msg = ex.ocr_models_present()
        assert not ok
        assert "never downloads" in msg, (
            "the failure message must say plainly that DocIQ does not fetch — "
            "an operator reading it decides what to do next")
        assert ".onnx" in msg and empty in msg, msg
        assert ex.ocr_available() is False

        with offline.no_network() as guard:
            with pytest.raises(ExtractionError):
                ex._ocr_engine()
        assert guard.clean, (
            "something tried to reach the network when the models were "
            "missing:\n" + guard.render())

        # And the model IDENTITY degrades to an explicit value rather than an
        # empty string that would compare equal to a run that had no OCR.
        assert "models-unavailable" in ex.ocr_model_id()
    finally:
        ex._OCR_ENGINE = saved_engine
        if saved_env is None:
            os.environ.pop("DOCIQ_OCR_MODEL_DIR", None)
        else:
            os.environ["DOCIQ_OCR_MODEL_DIR"] = saved_env


# ---------------------------------------------------------------------------
# The child-process class — the scope hole, closed (C5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("call, entry_point", [
    pytest.param(lambda: __import__("subprocess").run([sys.executable, "-c", ""]),
                 "subprocess.Popen", id="subprocess.run"),
    pytest.param(lambda: __import__("subprocess").Popen([sys.executable, "-c", ""]),
                 "subprocess.Popen", id="subprocess.Popen"),
    pytest.param(lambda: __import__("subprocess").check_output(
        [sys.executable, "-c", ""]), "subprocess.Popen", id="check_output"),
    pytest.param(lambda: os.system("cmd /c echo hi"), "os.system", id="os.system"),
    pytest.param(lambda: os.popen("cmd /c echo hi"), "os.popen", id="os.popen"),
])
def test_creating_a_child_process_is_recorded_and_refused(call, entry_point):
    """FAIL-BEFORE, watched RED: with the child-process targets removed from
    ``NetworkGuard.__enter__`` every one of these spawns happily and
    ``guard.clean`` stays True — which is the defect. A guard scoped to one
    interpreter proves nothing about a program that starts another.

    ``subprocess.run`` / ``check_output`` are here to demonstrate the helpers
    are covered BY Popen rather than needing their own rebinds; the recorded
    entry point is ``subprocess.Popen`` for all three, which is the assertion
    that the coverage is structural and not a list of names.
    """
    with offline.no_network() as guard:
        with pytest.raises(offline.ProcessSpawnAttempted):
            call()
    assert not guard.clean
    assert [a.entry_point for a in guard.attempts] == [entry_point]
    # A subclass of NetworkAttempted, so any existing handler still catches it.
    assert issubclass(offline.ProcessSpawnAttempted, offline.NetworkAttempted)


def test_every_child_process_entry_point_is_restored_on_exit():
    """The guard must not leave the process unable to spawn. ``verify.determinism``
    spawns a subprocess per repetition immediately after a guarded block runs in
    the same interpreter, so a leaked raiser would break the determinism proof
    rather than the offline one."""
    import subprocess

    before = {name: getattr(sys.modules[name.split(".")[0]], name.split(".")[1])
              for name in offline.enumerate_child_process_entry_points()}
    with offline.no_network():
        pass
    after = {name: getattr(sys.modules[name.split(".")[0]], name.split(".")[1])
             for name in offline.enumerate_child_process_entry_points()}
    assert before == after
    assert subprocess.run([sys.executable, "-c", "print(1)"],
                          capture_output=True, text=True).stdout.strip() == "1"


def test_the_child_process_class_has_no_unaccounted_siblings():
    """Global class assertion, read off the LIVE modules rather than a list
    written once — the same shape as ``audit_siblings`` for sockets. A spawner a
    future CPython adds to ``os`` or ``subprocess`` shows up here."""
    assert offline.audit_child_process_siblings() == ()
    points = offline.enumerate_child_process_entry_points()
    assert "subprocess.Popen" in points
    assert "os.system" in points and "os.popen" in points
    # Platform-split families, guarded by existence rather than by assumption.
    assert any(p.startswith("os.exec") for p in points)
    assert any(p.startswith("os.spawn") for p in points)
    if hasattr(os, "startfile"):
        assert "os.startfile" in points, (
            "gui/main_window.py hands a path to the Windows shell; the shell can "
            "start a browser, which is an outbound action")
    if hasattr(os, "fork"):
        assert "os.fork" in points


def test_a_swallowed_spawn_is_still_a_finding():
    """The counting property, for the child-process class. Code that wraps its
    spawn in ``except Exception`` passes a blocking-only guard in silence; the
    attempt is still recorded here, and ``clean`` is still False."""
    import subprocess

    with offline.no_network() as guard:
        try:
            subprocess.Popen([sys.executable, "-c", ""])
        except Exception:
            pass
    assert not guard.clean, (
        "a spawn swallowed by the caller left no trace — the guard is blocking "
        "rather than counting")
