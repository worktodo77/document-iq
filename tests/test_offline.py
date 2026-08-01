"""Acceptance criterion 6 / Principle 4 — the offline claim, tested.

The claim §10 makes is "makes no outbound connections", not "survives having
none". These tests exist because the difference is not academic: the probe that
shipped in Sprint 1 blocked ``socket.socket`` and asserted OCR still worked, and
:func:`test_a_swallowed_attempt_is_still_a_finding` is the case that construction
passes and this one catches.
"""

from __future__ import annotations

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
print('ATTEMPTS=' + str(len(guard.attempts)))
print('FETCH=' + ','.join(offline.audit_model_fetch_imports()))
print('TRANSPORT=' + ','.join(offline.audit_transport_imports()))
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
    assert "no outbound attempt" in guard.render()


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
    """
    import subprocess

    code = PROBE_SOURCE
    proc = subprocess.run([sys.executable, "-c", code, str(_fixtures_dir())],
                          capture_output=True, text=True, env=_env_with_src())
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines()
               if "=" in line)
    assert out.get("ATTEMPTS") == "0", proc.stdout
    fetch = [m for m in out.get("FETCH", "").split(",") if m]
    assert fetch == [], f"a whole pipeline run loaded fetch clients: {fetch}"


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
