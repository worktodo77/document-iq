"""The shape of a Doc ID, and the proof that two of them cannot collide (D-04).

Collision-freedom is treated here as a *structural* property, not a runtime
check that happens to pass on today's corpus. The argument has exactly two
parts, and both are enforced in this module:

1. **Rendering is injective.** Two :class:`DocId` values that render to the same
   string are the same value. See :func:`DocId.render` for the proof sketch and
   :func:`assert_render_injective` for the machine-checkable form.
2. **The values themselves are unique.** Guaranteed by the assigner, which mints
   every identifier through :class:`DocIdMinter`. The minter keeps a registry
   and raises on a repeat — defence in depth behind (1), not the primary
   argument.

Because (1) reduces "no two documents share an ID string" to "no two documents
share an ID value", the assigner only has to keep tuples distinct, which it can
do by construction (a unique index row number, a monotonic counter, or a unique
position within a container).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

from dociq.contracts import ContractViolation

__all__ = [
    "IdNamespace",
    "DocId",
    "DocIdMinter",
    "LI_MIN_WIDTH",
    "DIQ_MIN_WIDTH",
    "CHILD_MIN_WIDTH",
    "assert_render_injective",
    "base_width_for",
    "parse_doc_id",
]


class IdNamespace(str, enum.Enum):
    """The two disjoint identifier spaces of D-04.

    ``LI`` numbers come from the master index's "Original Sort" column; ``DIQ``
    numbers are synthetic. D-04 requires that a synthetic identifier can never
    collide with an LI one. That is achieved by the *prefix*, not by reserving a
    numeric band: a reserved band is a convention that a future index of 100,000
    rows would silently invalidate, whereas a distinct prefix cannot be reached
    from the other namespace at all.
    """

    LI = "LI"
    DIQ = "DIQ"


def _assert_namespaces_disjoint() -> None:
    """No namespace token may be a prefix of another.

    If one were, ``LI-1`` and (say) ``L-I1`` could in principle render alike
    once separators moved. Checked at import so the invariant cannot rot.
    """
    tokens = [ns.value for ns in IdNamespace]
    for a in tokens:
        if not a.isalpha() or not a.isupper():
            raise ContractViolation(f"namespace token must be A-Z only: {a!r}")
        for b in tokens:
            if a is not b and (a.startswith(b) or b.startswith(a)):
                raise ContractViolation(
                    f"namespace tokens {a!r} and {b!r} are prefix-comparable"
                )


_assert_namespaces_disjoint()


LI_MIN_WIDTH = 5
"""D-04 writes the LI form as ``LI-06881`` — five digits. Widened, never
narrowed, if an index ever exceeds 99,999 rows."""

DIQ_MIN_WIDTH = 6
"""D-04 writes the synthetic form as ``DIQ-000123`` — six digits."""

CHILD_MIN_WIDTH = 2
"""D-04 writes a container child as ``LI-06881.01``."""


def base_width_for(namespace: IdNamespace, max_base: int) -> int:
    """Zero-padding width for a run's base numbers.

    Constant per (namespace, run) — that constancy is load-bearing for
    :func:`DocId.render`'s injectivity, because it is what makes the digit run
    between ``-`` and the first ``.`` parse back to a unique integer.
    """
    if max_base < 0:
        raise ContractViolation(f"base numbers are non-negative, got {max_base}")
    floor = LI_MIN_WIDTH if namespace is IdNamespace.LI else DIQ_MIN_WIDTH
    return max(floor, len(str(max_base)))


_RENDER_RE = re.compile(r"^(?P<ns>[A-Z]+)-(?P<base>[0-9]+)(?P<children>(?:\.[0-9]+)*)$")


@dataclass(frozen=True, slots=True)
class DocId:
    """A rendered-once, parsed-never identifier.

    ``child_path`` is the chain of container positions from the top-level
    document down to this record: ``()`` for a folder file, ``(0,)`` for the
    first member of an archive, ``(0, 3)`` for the fourth member of a nested
    archive that was itself the first member. ``child_widths`` records the
    padding used at each level; it is per-container because a 400-member archive
    needs three digits while a two-member one needs two, and mixing widths
    *within* one container would be the only way to break injectivity.
    """

    namespace: IdNamespace
    base: int
    base_width: int
    child_path: tuple[int, ...] = ()
    child_widths: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.base < 0:
            raise ContractViolation(f"base must be non-negative, got {self.base}")
        if len(str(self.base)) > self.base_width:
            raise ContractViolation(
                f"base {self.base} does not fit in width {self.base_width}"
            )
        if len(self.child_path) != len(self.child_widths):
            raise ContractViolation(
                "child_path and child_widths must be the same length"
            )
        for pos, width in zip(self.child_path, self.child_widths):
            if pos < 0:
                raise ContractViolation(f"container position must be >=0, got {pos}")
            if width < CHILD_MIN_WIDTH:
                raise ContractViolation(
                    f"child width {width} below the D-04 minimum {CHILD_MIN_WIDTH}"
                )
            if len(str(pos)) > width:
                raise ContractViolation(
                    f"container position {pos} does not fit in width {width}"
                )

    def render(self) -> str:
        """The disk form. The *only* place a Doc ID string is constructed.

        Injectivity: the string is ``<A-Z token>-<digits>(.<digits>)*``. The
        token cannot contain ``-`` or a digit, so the namespace splits off
        unambiguously; namespaces are pairwise prefix-incomparable, so equal
        tokens mean the same namespace. Within one namespace and run,
        ``base_width`` is constant, so equal strings have equal base digit-runs
        of equal length, hence equal ``base``. The remainder splits on ``.``
        into positional digit-runs; equal digit strings are equal integers
        whatever their padding. Therefore equal renderings imply equal values.
        """
        out = f"{self.namespace.value}-{self.base:0{self.base_width}d}"
        for pos, width in zip(self.child_path, self.child_widths):
            out += f".{pos:0{width}d}"
        return out

    def child(self, position: int, width: int = CHILD_MIN_WIDTH) -> "DocId":
        """Derive a container-member identifier (``LI-06881`` -> ``LI-06881.01``).

        D-04 numbers members from 1 for human readability, so ``position`` is
        the 1-based rendered ordinal; callers convert from the contract's
        0-based ``container_order``.
        """
        return DocId(
            namespace=self.namespace,
            base=self.base,
            base_width=self.base_width,
            child_path=self.child_path + (position,),
            child_widths=self.child_widths + (max(width, CHILD_MIN_WIDTH),),
        )

    @property
    def is_child(self) -> bool:
        return bool(self.child_path)

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.render()


def parse_doc_id(text: str) -> tuple[IdNamespace, int, tuple[int, ...]]:
    """Recover ``(namespace, base, child_path)`` from a rendered identifier.

    Exists for the round-trip test that keeps :meth:`DocId.render` honest, and
    for reading a previous run's issued-ID ledger. Padding is not recoverable
    and is deliberately not returned — it is presentation, not identity.
    """
    m = _RENDER_RE.match(text)
    if not m:
        raise ContractViolation(f"not a DocIQ identifier: {text!r}")
    try:
        namespace = IdNamespace(m.group("ns"))
    except ValueError as exc:
        raise ContractViolation(f"unknown identifier namespace in {text!r}") from exc
    children = m.group("children")
    path = tuple(int(p) for p in children.split(".")[1:]) if children else ()
    return namespace, int(m.group("base")), path


def assert_render_injective(ids: "list[DocId] | tuple[DocId, ...]") -> None:
    """Fail loudly if any two distinct values render alike, or any two equal
    values render differently.

    The second direction matters as much as the first: if the same logical
    document could render two ways (say, because two code paths chose different
    padding), ``sources.json`` and the index would disagree about its name.
    """
    by_string: dict[str, DocId] = {}
    by_value: dict[tuple[IdNamespace, int, tuple[int, ...]], str] = {}
    for did in ids:
        rendered = did.render()
        value = (did.namespace, did.base, did.child_path)
        clash = by_string.get(rendered)
        if clash is not None and (clash.namespace, clash.base, clash.child_path) != value:
            raise ContractViolation(
                f"Doc ID collision: {clash!r} and {did!r} both render {rendered!r}"
            )
        prior = by_value.get(value)
        if prior is not None and prior != rendered:
            raise ContractViolation(
                f"Doc ID renders two ways: {value!r} -> {prior!r} and {rendered!r}"
            )
        by_string[rendered] = did
        by_value[value] = rendered


@dataclass(slots=True)
class DocIdMinter:
    """Registry of every identifier issued in one run.

    Defence in depth behind :func:`assert_render_injective`: the structural
    argument says a collision is unreachable, and this says that if it ever
    became reachable the run stops rather than emitting two documents under one
    name. A silent overwrite in ``clean_text/`` is exactly the class of failure
    Principle 1 exists to prevent.
    """

    _issued: dict[str, DocId] = field(default_factory=dict)

    def mint(self, doc_id: DocId) -> str:
        rendered = doc_id.render()
        prior = self._issued.get(rendered)
        if prior is not None:
            raise ContractViolation(
                f"Doc ID {rendered!r} issued twice ({prior!r} then {doc_id!r})"
            )
        self._issued[rendered] = doc_id
        return rendered

    @property
    def count(self) -> int:
        return len(self._issued)

    def issued(self) -> tuple[str, ...]:
        """Issued identifiers in issue order."""
        return tuple(self._issued)
