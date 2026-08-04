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

THE OTHER WAY OUT: A CHILD PROCESS
Every guard above is a rebind inside **this** interpreter, so the claim it
supports is scoped to this interpreter. A process that spawns a child has left
that scope: the child gets a pristine ``socket`` module, and the parent's
``attempts == 0`` remains perfectly true while the child does whatever it likes.
That is not a hypothetical shape for DocIQ — ``verify/determinism.py`` and
``packaging/dociq_launcher.py`` both spawn subprocesses by design, and
``gui/main_window.py`` calls ``os.startfile`` to open the output folder.

So process creation is guarded on the same terms as the socket class: recorded,
then raised. :func:`enumerate_child_process_entry_points` is the enumeration and
:func:`audit_child_process_siblings` is the live-module audit, mirroring the
socket pair above. Nothing DocIQ does *inside* a guarded block spawns anything —
the determinism probe and the launcher both spawn outside the guard, and a
pipeline run uses a thread pool, not a process pool — so this closes a hole
rather than constraining the product.

**Blocking, not merely counting, is the right default here** and it is worth
saying why the reasoning differs from ``TRANSPORT_MODULES``. An imported
``urllib.request`` is inert until called; a spawned child is an execution the
guard can no longer see. The conservative treatment of an unobservable thing is
to refuse it, not to note it.

ONE CHILD PROCESS IS PERMITTED, BY NAME (ruling D-30, 2026-08-04)
And it was found by this guard, which is the argument for having built it.
``platform.uname()`` probes the Windows version by running ``ver`` through the
shell, once per interpreter; ``onnxruntime`` calls ``platform.system()`` at
import time; ``extract.ocr_model_dir()`` imports it. So a whole pipeline run
creates a child process, the verification note asserted "the cost is zero:
nothing DocIQ does inside a guarded block spawns anything" on reasoning rather
than measurement, and the reasoning was wrong.

For three review rounds that spawn was reported as an intermittent *outbound
network* risk — because the probe printed a COUNT of ``attempts`` and discarded
the stacks recorded on every one, so the socket guard and this one arrived as a
single number that the assertion then called "outbound". Measured with the
stacks kept: 84 occurrences over 75 runs, every one this call, and zero socket
attempts in any of them.

:data:`PERMITTED_SPAWNS` permits exactly it. **By identity, not by category** —
this entry point, this exact command string, ``shell=True``, called from
``_syscmd_ver`` in the standard library's own ``platform.py``, within four
frames. "Allow spawns during import", "allow short commands" and "allow anything
from site-packages" were each considered and refused: every one of them readmits
the whole class this guard was added for. Everything above about a child being
an execution the guard can no longer observe still holds, and every other spawn
still raises for exactly that reason.

A permitted spawn is **recorded** on :attr:`NetworkGuard.exempted` with its
stack and named by :meth:`NetworkGuard.render` even on a clean report, because a
permission nobody can see is indistinguishable from a hole.
:data:`CRITERION_6_CLAIM` is the resulting claim as a single value, so the code
and the documents that assert it cannot drift.

WHAT THIS CANNOT PROVE, STATED PLAINLY
1. A C extension that calls the Winsock API directly, without going through
   CPython's ``socket``/``_socket`` objects, is invisible here. Nothing in the
   dependency set is known to do that, and "not known to" is not "does not".
2. A child process spawned **outside** a guarded block is not covered by
   anything in this module, and DocIQ spawns some. What each of those children
   does is a separate claim, proven separately: the determinism runner's child
   is the pipeline itself, and ``tests/test_offline.py`` runs a whole pipeline
   run in a child *under its own guard* and reports that child's attempt count.
3. A C extension that spawns a process through the Win32 API rather than through
   the ``os``/``subprocess`` layer is invisible for the same reason as (1).

That residue is what an actual network-disabled run covers and this does not;
see ``docs/verification/track_f_sprint2_2026-08-01.md`` §3.4 for which was
executed and which remains open.
"""

from __future__ import annotations

import os as _os_mod
import platform as _platform_mod
import socket as _socket_mod
import ssl as _ssl_mod
import subprocess as _subprocess_mod
import sys
import threading
import traceback
from dataclasses import dataclass, field
from types import ModuleType

__all__ = [
    "Attempt",
    "ExemptedSpawn",
    "SpawnExemption",
    "NetworkAttempted",
    "ProcessSpawnAttempted",
    "NetworkGuard",
    "no_network",
    "enumerate_guarded_entry_points",
    "enumerate_child_process_entry_points",
    "enumerate_permitted_spawns",
    "PERMITTED_SPAWNS",
    "CRITERION_6_CLAIM",
    "audit_siblings",
    "audit_child_process_siblings",
    "MODEL_FETCH_MODULES",
    "TRANSPORT_MODULES",
    "audit_model_fetch_imports",
    "audit_transport_imports",
]

CRITERION_6_CLAIM = (
    "A DocIQ run makes NO outbound network attempt — no socket, no resolver, "
    "no TLS handshake — and creates NO child process, with exactly one named "
    "exception: the Windows version probe `ver` that the standard library's "
    "`platform._syscmd_ver` runs when `platform.uname()` is first called, "
    "which a dependency's import triggers. That exception is permitted by "
    "identity (ruling D-30), is recorded with its stack every time it occurs, "
    "and reaches no network."
)
"""The claim, as a value, so the wording cannot drift between the code that
enforces it and the documents that assert it.

Criterion 6 used to be stated as "no outbound connections". That was believed to
be true and was never measured, because the probe that checked it counted socket
attempts and process spawns in ONE number and then called the sum "outbound" —
so for three review rounds the one thing actually happening was reported as the
one thing that was not. The sentence above is longer and it is the first version
of it that anyone has checked."""


class NetworkAttempted(RuntimeError):
    """Raised at the guarded entry point, after the attempt is recorded."""


class ProcessSpawnAttempted(NetworkAttempted):
    """Raised when guarded code tries to create a child process.

    A subclass of :class:`NetworkAttempted` so existing ``except`` clauses keep
    catching it, and a distinct type because the two findings mean different
    things: one is an outbound call, the other is code stepping outside the
    scope in which "no outbound call" was proven.
    """


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


# ---------------------------------------------------------------------------
# The child-process class
# ---------------------------------------------------------------------------

# Named rather than derived, for the entry points whose names carry no common
# prefix. Every one of these creates or becomes another program.
_CHILD_PROCESS_NAMED: tuple[tuple[str, str], ...] = (
    # Everything in ``subprocess`` funnels through Popen — run, call,
    # check_call, check_output, getoutput, getstatusoutput. Guarding the class
    # guards the module; guarding the helpers would miss a direct construction,
    # which is what ``verify/determinism.py`` would use if it ever needed one.
    ("subprocess", "Popen"),
    ("os", "system"),
    ("os", "popen"),
    # Windows shell-open. ``gui/main_window.py`` uses it for "open the output
    # folder", and handing a path to the shell can start a browser — which is
    # precisely an outbound action this module exists to rule out. The GUI never
    # runs inside a guard, so guarding it costs nothing and closes the case.
    ("os", "startfile"),
    ("os", "fork"),
    ("os", "forkpty"),
)

# Derived, so a name a future CPython adds to either family is covered the day
# it appears rather than the day someone remembers to add it.
_CHILD_PROCESS_PREFIXES = ("spawn", "exec", "posix_spawn")


def _child_process_targets() -> tuple[tuple[str, str], ...]:
    """The child-process entry points that EXIST in this interpreter.

    Resolved live because the families are platform-split — ``os.fork`` and
    ``os.posix_spawn`` are POSIX-only, ``os.startfile`` is Windows-only — and a
    hard-coded list would either raise on the wrong platform or quietly guard
    less than it claims on the right one.
    """
    found: list[tuple[str, str]] = []
    for mod_name, attr in _CHILD_PROCESS_NAMED:
        mod = sys.modules.get(mod_name)
        if mod is not None and callable(getattr(mod, attr, None)):
            found.append((mod_name, attr))
    for name in dir(_os_mod):
        if name.startswith("_") or not name.startswith(_CHILD_PROCESS_PREFIXES):
            continue
        if not callable(getattr(_os_mod, name, None)):
            continue
        if ("os", name) not in found:
            found.append(("os", name))
    return tuple(found)


def enumerate_child_process_entry_points() -> tuple[str, ...]:
    """Every process-creation entry point :class:`NetworkGuard` closes.

    A value rather than prose, for the same reason as
    :func:`enumerate_guarded_entry_points`: a reviewer can diff it and a test
    can assert the class is covered.
    """
    return tuple(sorted(f"{m}.{a}" for m, a in _child_process_targets()))


# Process-creating names this module deliberately does not rebind, each with the
# reason. Anything in ``os`` or ``subprocess`` that spawns and is not here and
# not guarded is a hole, and :func:`audit_child_process_siblings` says so.
_CHILD_COVERED_INDIRECTLY = {
    "subprocess.run": "constructs subprocess.Popen",
    "subprocess.call": "constructs subprocess.Popen",
    "subprocess.check_call": "constructs subprocess.Popen",
    "subprocess.check_output": "constructs subprocess.Popen",
    "subprocess.getoutput": "constructs subprocess.Popen",
    "subprocess.getstatusoutput": "constructs subprocess.Popen",
}


# ---------------------------------------------------------------------------
# The one permitted spawn (ruling D-30, 2026-08-04)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpawnExemption:
    """One child process this guard permits, identified rather than described.

    Every field is part of the identity and every one is checked. A description
    ("a version probe", "a short command", "something during an import") would
    be a CATEGORY, and a category readmits the whole class the guard exists for:
    "during an import" permits any dependency to run anything at import time,
    "short command" permits ``curl x``, "from site-packages" permits every
    dependency. So the match is: this entry point, this exact command string,
    this shell flag, called from this function in this file.
    """

    entry_point: str
    commands: tuple[str, ...]
    shell: bool
    caller_function: str
    caller_file: str
    caller_within_frames: int
    ruling: str
    reason: str

    def describe(self) -> str:
        return (
            f"{self.entry_point}({' | '.join(self.commands)}, shell={self.shell}) "
            f"from {self.caller_function} in {self.caller_file} "
            f"[{self.ruling}]"
        )


@dataclass(frozen=True, slots=True)
class ExemptedSpawn:
    """A permitted spawn that HAPPENED — with the stack that reached it.

    Recorded rather than allowed silently, and this is the whole difference
    between an exemption and a hole. The stack is retained for the same reason
    :class:`Attempt` retains one: the defect that produced this ruling was a
    probe that counted events and discarded the evidence of what they were, so
    an exemption that reported only a tally would repeat it exactly.
    """

    exemption: SpawnExemption
    detail: str
    stack: str

    def render(self) -> str:
        return (f"PERMITTED {self.exemption.describe()}\n"
                f"  reason: {self.exemption.reason}\n"
                f"{self.detail}\n{self.stack}")


_PLATFORM_FILE = _os_mod.path.normcase(
    _os_mod.path.abspath(getattr(_platform_mod, "__file__", "") or ""))

PERMITTED_SPAWNS: tuple[SpawnExemption, ...] = (
    SpawnExemption(
        entry_point="subprocess.Popen",
        # The three strings `platform._syscmd_ver` tries, in its own order.
        # Copied as literals rather than read from the stdlib at run time: a
        # future CPython that changes them must fail this match and be looked
        # at, not be permitted automatically because the exemption follows
        # whatever the library now does.
        commands=("ver", "command /c ver", "cmd /c ver"),
        shell=True,
        caller_function="_syscmd_ver",
        caller_file=_PLATFORM_FILE,
        # `_syscmd_ver` calls `subprocess.check_output`, which calls `run`,
        # which constructs `Popen`. Three frames covers that chain and nothing
        # further out — an arbitrary caller cannot qualify by having
        # `_syscmd_ver` somewhere far up its stack.
        caller_within_frames=4,
        ruling="D-30 (Alex, 2026-08-04)",
        reason=(
            "`platform.uname()` probes the Windows version by running `ver` "
            "through the shell, once per interpreter, and caches the result. "
            "DocIQ reaches it transitively: `extract.ocr_model_dir()` imports "
            "`rapidocr_onnxruntime`, which imports `onnxruntime`, which calls "
            "`platform.system()` at import time. Measured 2026-08-04 over 75 "
            "probe runs: 84 occurrences, all of them this, and ZERO socket "
            "attempts in any run. It opens no socket and resolves no name."
        ),
    ),
)
"""The complete set. One entry, and it is meant to stay that way — a second one
is a decision for the same gate that made this one, not a code change."""


def enumerate_permitted_spawns() -> tuple[str, ...]:
    """The exemptions as dotted descriptions, so a reviewer can diff the set.

    Same reason as :func:`enumerate_guarded_entry_points`: a permission that
    exists only inside an ``if`` is a permission nobody can review.
    """
    return tuple(e.describe() for e in PERMITTED_SPAWNS)


def _permitted_spawn(entry_point: str, args: tuple, kwargs: dict,
                     stack: list) -> SpawnExemption | None:
    """The exemption this call matches, or ``None`` — which means it is refused.

    Fails closed on every axis. An unmatched command, an unmatched shell flag,
    a caller in a different file or too far up the stack, or a missing
    ``platform.__file__`` all return ``None``, and ``None`` raises. The
    exemption cannot widen by accident: there is no branch here that permits
    anything on a partial match.
    """
    for exemption in PERMITTED_SPAWNS:
        if entry_point != exemption.entry_point:
            continue
        if not args or args[0] not in exemption.commands:
            continue
        if kwargs.get("shell") is not exemption.shell:
            continue
        if not exemption.caller_file:  # pragma: no cover — a frozen stdlib
            continue
        for frame in stack[-exemption.caller_within_frames:]:
            if frame.name != exemption.caller_function:
                continue
            if _os_mod.path.normcase(
                    _os_mod.path.abspath(frame.filename)) == exemption.caller_file:
                return exemption
    return None


def audit_child_process_siblings() -> tuple[str, ...]:
    """Process-creating names that are neither guarded nor accounted for.

    Empty is the passing value. Reads the live ``os`` and ``subprocess``
    modules, so a spawner a future CPython adds shows up as a finding instead of
    as a silent gap — the same global class assertion :func:`audit_siblings`
    makes for sockets.
    """
    guarded = set(enumerate_child_process_entry_points())
    holes: list[str] = []
    for name in dir(_subprocess_mod):
        if name.startswith("_"):
            continue
        dotted = f"subprocess.{name}"
        obj = getattr(_subprocess_mod, name, None)
        if not callable(obj):
            continue
        # The spawning surface of ``subprocess`` is Popen plus the helpers that
        # construct it. Everything else in the module is an exception class, a
        # constant, or a completed-process record.
        if name != "Popen" and dotted not in _CHILD_COVERED_INDIRECTLY:
            continue
        if dotted not in guarded and dotted not in _CHILD_COVERED_INDIRECTLY:
            holes.append(dotted)
    for name in dir(_os_mod):
        if name.startswith("_") or not callable(getattr(_os_mod, name, None)):
            continue
        looks_like_spawn = (
            name.startswith(_CHILD_PROCESS_PREFIXES)
            or name in {"system", "popen", "startfile", "fork", "forkpty"}
        )
        if looks_like_spawn and f"os.{name}" not in guarded:
            holes.append(f"os.{name}")
    return tuple(sorted(holes))


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
    """Records and blocks every outbound and process-creating attempt.

    Re-entrant across threads: the OCR page pool fans work across ~16 threads
    and an attempt from any of them must be recorded, so ``attempts`` is
    appended under a lock.

    ``attempts`` mixes both classes deliberately, and ``clean`` is the single
    assertion: an outbound call and an escape from the process in which outbound
    calls are being counted are both failures of the same claim. The entry-point
    name on each :class:`Attempt` says which occurred.
    """

    attempts: list[Attempt] = field(default_factory=list)
    exempted: list[ExemptedSpawn] = field(default_factory=list)
    """Permitted spawns that OCCURRED, each with its stack (ruling D-30).

    Separate from :attr:`attempts` because they are different findings, and
    populated rather than skipped because a permission nobody can see is
    indistinguishable from a hole. ``clean`` does not consult this list — the
    ruling permits the call — but :meth:`render` always names what was
    permitted, so no report can say "clean" without also saying what it let
    through."""

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

    def _raiser(self, entry_point: str, exc=NetworkAttempted,
                what: str = "outbound network attempted via",
                original=None):
        def _blocked(*args, **kwargs):
            detail = ", ".join(
                [repr(a)[:80] for a in args]
                + [f"{k}={v!r}"[:80] for k, v in kwargs.items()]
            )
            # Ruling D-30: exactly one child process is permitted, matched by
            # identity. The check runs BEFORE the record-and-raise so that a
            # permitted call is not filed as a refusal, and it is consulted only
            # for the child-process class — no exemption exists, or may exist
            # here, for a socket. The argument that put the child-process guard
            # in this module is untouched: a spawned child is an execution this
            # guard can no longer observe, and every spawn but this one still
            # raises for exactly that reason.
            if original is not None and exc is ProcessSpawnAttempted:
                stack = traceback.extract_stack()[:-1]
                exemption = _permitted_spawn(entry_point, args, kwargs, stack)
                if exemption is not None:
                    with self._lock:
                        self.exempted.append(ExemptedSpawn(
                            exemption, detail,
                            "".join(traceback.format_stack()[:-1])))
                    return original(*args, **kwargs)
            self._record(entry_point, detail)
            raise exc(
                f"{what} {entry_point} — "
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
        # Process creation, on the same terms: recorded, then refused. A child
        # process is not an outbound call, it is an exit from the scope in which
        # the absence of outbound calls is being proven — see the module
        # docstring, "THE OTHER WAY OUT".
        for mod_name, attr in _child_process_targets():
            mod = sys.modules[mod_name]
            original = getattr(mod, attr)
            self._saved.append((mod, attr, original))
            setattr(mod, attr, self._raiser(
                f"{mod_name}.{attr}",
                ProcessSpawnAttempted,
                "child process creation attempted via",
                # Handed the real callable so the ONE permitted spawn can
                # actually run (D-30). Blocking it instead would not be a
                # stricter guard, it would be a different product: DocIQ would
                # report `models-unavailable` for its OCR engine identity
                # whenever the guard happened to be the first thing to touch
                # `platform.uname()`.
                original=original,
            ))
        return self

    def __exit__(self, *exc) -> None:
        # Restored in reverse so a nested guard cannot leave a raiser installed.
        for target, attr, original in reversed(self._saved):
            setattr(target, attr, original)
        self._saved.clear()

    def render(self) -> str:
        total = (len(enumerate_guarded_entry_points())
                 + len(enumerate_child_process_entry_points()))
        if self.clean:
            head = (f"no outbound or child-process attempt on any of "
                    f"{total} guarded entry points")
        else:
            head = (f"{len(self.attempts)} outbound/child-process attempt(s):"
                    + "\n" + "\n\n".join(a.render() for a in self.attempts))
        # Always appended, including on a clean report. A guard that says
        # "clean" without saying what it permitted is a guard whose reader
        # cannot tell an exemption from a hole — which is the defect that
        # produced D-30 in the first place, one layer down.
        if self.exempted:
            head += (f"\n\n{len(self.exempted)} PERMITTED spawn(s) under a "
                     f"named exemption (not a failure):\n"
                     + "\n\n".join(e.render() for e in self.exempted))
        return head


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
