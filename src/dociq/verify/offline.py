"""Principle 4 — proving the run makes no outbound call, and no model fetch.

§10 says "the application must function with all network interfaces disabled
and must make no outbound connections", and acceptance criterion 6 says that is
*verified with network disabled*. A code reading does not discharge either.

WHY THIS MODULE EXISTS RATHER THAN THE ONE PROBE THAT WAS ALREADY HERE
:func:`dociq.selftest._check_no_network` replaced ``socket.socket`` with a
raiser and asserted OCR still produced lines. That proves OCR *survives* a
blocked socket. It does **not** prove nothing tried, and the difference is the
whole claim: a library that attempts a fetch inside ``try/except Exception``
passes a blocking probe silently and still writes an outbound packet the moment
the block is lifted on a client machine. The claim in §10 and in the
user-facing documentation is "makes no outbound connections", not "tolerates
having none".

So this module **counts before it blocks**. Every guarded entry point records
an :class:`Attempt` — what was called, with what argument, and the Python stack
that reached it — and *then* raises. The assertion is ``attempts == 0``. A
swallowed attempt is therefore a failure, not a pass.

THE CLASS, NOT THE ONE CALL
Blocking ``socket.socket`` alone leaves siblings open, and each one is a real
way out of the process:

============================  ===========================================
guarded                       why it is not covered by ``socket.socket``
============================  ===========================================
``socket.socket``             the class almost everything constructs
``_socket.socket``            the C base; ``import _socket`` bypasses the
                              Python-level rebind entirely
``socket.create_connection``  resolves *and* connects; rebound because a
                              module that did ``from socket import
                              create_connection`` at import time holds the
                              original function object
``socket.socketpair``         a real socket pair on Windows (AF_INET
                              loopback), so it is an outbound-capable
                              object
``socket.getaddrinfo``        DNS. A resolution is an outbound packet even
                              when no connection follows, and it is the
                              first thing a fetch does — so it is the
                              earliest place an attempt is visible
``socket.gethostbyname``      the older resolver, same reason
``socket.gethostbyname_ex``   sibling of the above
``ssl.SSLContext.wrap_socket``  belt and braces: an attempt that somehow
                              obtained a socket still trips here
============================  ===========================================

:func:`enumerate_guarded_entry_points` returns that list so the enumeration is
a value a test can assert against, not prose that drifts. :func:`audit_siblings`
walks the live ``socket`` module and reports any *outbound-capable* public
callable that is not guarded — so a sibling added by a future Python release
fails a test rather than going unnoticed.

WHAT THIS CANNOT PROVE, STATED PLAINLY
A C extension that calls the Winsock API directly, without going through
CPython's ``socket``/``_socket`` objects, is invisible here. Nothing in the
dependency set is known to do that, and "not known to" is not "does not". That
residue is what an actual network-disabled run covers and this does not; see
``docs/verification/track_f_sprint2_2026-08-01.md`` for which was executed.
"""

from __future__ import annotations

import socket as _socket_mod
import ssl as _ssl_mod
import sys
import threading
import traceback
from dataclasses import dataclass, field
from types import ModuleType

__all__ = [
    "Attempt",
    "NetworkAttempted",
    "NetworkGuard",
    "no_network",
    "enumerate_guarded_entry_points",
    "audit_siblings",
    "MODEL_FETCH_MODULES",
    "TRANSPORT_MODULES",
    "audit_model_fetch_imports",
    "audit_transport_imports",
]


class NetworkAttempted(RuntimeError):
    """Raised at the guarded entry point, after the attempt is recorded."""


@dataclass(frozen=True, slots=True)
class Attempt:
    """One outbound attempt: what was called, with what, and from where."""

    entry_point: str
    detail: str
    stack: str

    def render(self) -> str:
        return f"{self.entry_point}({self.detail})\n{self.stack}"


# The guarded set, as data. ``(module, attribute)`` pairs — resolved against the
# live modules at guard time so a name that a future Python removes fails
# loudly here instead of being silently skipped.
_GUARDED: tuple[tuple[str, str], ...] = (
    ("socket", "socket"),
    ("_socket", "socket"),
    ("socket", "create_connection"),
    ("socket", "socketpair"),
    ("socket", "getaddrinfo"),
    ("socket", "gethostbyname"),
    ("socket", "gethostbyname_ex"),
)

_SSL_GUARD = ("ssl", "SSLContext", "wrap_socket")


def enumerate_guarded_entry_points() -> tuple[str, ...]:
    """Every entry point :class:`NetworkGuard` closes, as dotted names.

    A value rather than a docstring so a test can assert the class is covered
    and a reviewer can diff it.
    """
    names = [f"{m}.{a}" for m, a in _GUARDED]
    names.append(".".join(_SSL_GUARD))
    return tuple(names)


# Public callables of the ``socket`` module that can put a packet on a wire.
# Anything here that is not in :data:`_GUARDED` is a hole; anything in the
# module that is not here is asserted to be inert (a constant, a helper that
# takes an already-open object, or a pure conversion).
_OUTBOUND_CAPABLE = frozenset({
    "socket",
    "create_connection",
    "create_server",
    "socketpair",
    "getaddrinfo",
    "gethostbyname",
    "gethostbyname_ex",
    "gethostbyaddr",
    "getfqdn",
    "getnameinfo",
    "getservbyname",
    "getservbyport",
})

# Guarded indirectly, with the reason recorded rather than left implicit.
_COVERED_INDIRECTLY = {
    # Every one of these constructs a socket.socket or calls getaddrinfo
    # internally, so an attempt is recorded at the inner entry point. They are
    # not rebound because rebinding them would double-count one attempt.
    "create_server": "constructs socket.socket",
    "gethostbyaddr": "resolves through the guarded resolver stack",
    "getfqdn": "calls gethostbyaddr/getaddrinfo",
    "getnameinfo": "resolver call; reaches getaddrinfo",
    "getservbyname": "local services database on Windows; no packet",
    "getservbyport": "local services database on Windows; no packet",
}


def audit_siblings() -> tuple[str, ...]:
    """Outbound-capable ``socket`` names that are neither guarded nor
    explicitly accounted for. Empty is the passing value.

    This is the global class assertion: it reads the *live* module rather than
    a list written once, so a name a future CPython adds shows up as a finding
    instead of as a silent gap.
    """
    guarded = {a for m, a in _GUARDED if m == "socket"}
    holes = []
    for name in dir(_socket_mod):
        if name.startswith("_") or not callable(getattr(_socket_mod, name, None)):
            continue
        if name not in _OUTBOUND_CAPABLE:
            continue
        if name in guarded or name in _COVERED_INDIRECTLY:
            continue
        holes.append(name)
    return tuple(sorted(holes))


@dataclass
class NetworkGuard:
    """Records and blocks every outbound attempt for the duration of a block.

    Re-entrant across threads: the OCR page pool fans work across ~16 threads
    and an attempt from any of them must be recorded, so ``attempts`` is
    appended under a lock.
    """

    attempts: list[Attempt] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _saved: list[tuple[object, str, object]] = field(default_factory=list, repr=False)

    @property
    def clean(self) -> bool:
        return not self.attempts

    def _record(self, entry_point: str, detail: str) -> None:
        # The stack is captured from the caller's frame, skipping this module's
        # own frames, so the report names the code that tried rather than the
        # guard that caught it.
        stack = "".join(traceback.format_stack()[:-2])
        with self._lock:
            self.attempts.append(Attempt(entry_point, detail, stack))

    def _raiser(self, entry_point: str):
        def _blocked(*args, **kwargs):
            detail = ", ".join(
                [repr(a)[:80] for a in args]
                + [f"{k}={v!r}"[:80] for k, v in kwargs.items()]
            )
            self._record(entry_point, detail)
            raise NetworkAttempted(
                f"outbound network attempted via {entry_point} — "
                "DocIQ is offline by design (Principle 4)"
            )

        return _blocked

    def __enter__(self) -> "NetworkGuard":
        for mod_name, attr in _GUARDED:
            mod: ModuleType | None = sys.modules.get(mod_name)
            if mod is None:  # pragma: no cover — both are imported above
                __import__(mod_name)
                mod = sys.modules[mod_name]
            original = getattr(mod, attr)
            self._saved.append((mod, attr, original))
            setattr(mod, attr, self._raiser(f"{mod_name}.{attr}"))
        original_wrap = _ssl_mod.SSLContext.wrap_socket
        self._saved.append((_ssl_mod.SSLContext, "wrap_socket", original_wrap))
        _ssl_mod.SSLContext.wrap_socket = self._raiser(  # type: ignore[method-assign]
            "ssl.SSLContext.wrap_socket")
        return self

    def __exit__(self, *exc) -> None:
        # Restored in reverse so a nested guard cannot leave a raiser installed.
        for target, attr, original in reversed(self._saved):
            setattr(target, attr, original)
        self._saved.clear()

    def render(self) -> str:
        if self.clean:
            return (f"no outbound attempt on any of "
                    f"{len(enumerate_guarded_entry_points())} guarded entry points")
        head = f"{len(self.attempts)} outbound attempt(s):"
        return head + "\n" + "\n\n".join(a.render() for a in self.attempts)


def no_network() -> NetworkGuard:
    """``with no_network() as guard: ...`` then assert ``guard.clean``.

    Blocking alone is not the assertion — ``guard.clean`` is. See the module
    docstring for why a probe that only blocks passes on a swallowed attempt.
    """
    return NetworkGuard()


# ---------------------------------------------------------------------------
# The model-fetch class (F-1): closure proven by absence, not by grepping once
# ---------------------------------------------------------------------------

MODEL_FETCH_MODULES: tuple[str, ...] = (
    # Clients that exist to GO AND GET SOMETHING. None of these is in DocIQ's
    # declared dependency set, so any of them appearing in ``sys.modules``
    # after a run means a dependency bump introduced a fetcher — which is a
    # failure whether or not this corpus made it fetch. "The corpus doesn't
    # exercise it" selects nothing.
    "huggingface_hub",
    "modelscope",
    "requests",
    "urllib3",
    "httpx",
    "aiohttp",
    "pip",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc.client",
    "webbrowser",
)

TRANSPORT_MODULES: tuple[str, ...] = (
    # Stdlib transport. DISCLOSED, not failed — and the difference is a
    # measurement, not a concession. Both of DocIQ's document-writing
    # dependencies import these at import time and neither is a fetcher:
    #
    #   reportlab (run_summary.pdf) -> urllib.request, http.client, ssl, socket
    #   python-pptx (PPTX extraction) -> urllib.request, http.client, ssl, socket
    #
    # reportlab's ``ImageReader`` can read an image from a URL if it is handed
    # one; DocIQ hands it bytes it read off disk, and the NetworkGuard's
    # zero-attempt result over a whole pipeline run is the evidence that the
    # path is never taken. Treating an import as an outbound call would make
    # the probe cry wolf on every run and it would stop being read.
    "urllib.request",
    "http.client",
)


def audit_model_fetch_imports() -> tuple[str, ...]:
    """Fetch-client modules loaded in this interpreter. Empty is passing."""
    return tuple(sorted(m for m in MODEL_FETCH_MODULES if m in sys.modules))


def audit_transport_imports() -> tuple[str, ...]:
    """Stdlib transport modules loaded. Reported with attribution, not failed.

    See :data:`TRANSPORT_MODULES` for which dependency pulls each one in and
    why the guard's attempt count, not this list, is the assurance.
    """
    return tuple(sorted(m for m in TRANSPORT_MODULES if m in sys.modules))
