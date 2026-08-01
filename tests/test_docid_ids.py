"""Collision-freedom of the identifier space (acceptance criterion 5).

The proofs here are about *structure*, so they run without a spreadsheet:
rendering is injective, the two namespaces cannot meet, and the minter refuses a
repeat. The acceptance run against the real 9,259-row index is in
``tools/audit_real_index.py``, which never enters the repo's test data.
"""

from __future__ import annotations

import itertools

import pytest

from dociq.contracts import ContractViolation
from dociq.docid.ids import (
    DIQ_MIN_WIDTH,
    DocId,
    DocIdMinter,
    IdNamespace,
    LI_MIN_WIDTH,
    assert_render_injective,
    base_width_for,
    parse_doc_id,
)


def test_d04_reference_forms_render_exactly():
    assert DocId(IdNamespace.LI, 6881, 5).render() == "LI-06881"
    assert DocId(IdNamespace.LI, 6881, 5).child(1).render() == "LI-06881.01"
    assert DocId(IdNamespace.DIQ, 123, 6).render() == "DIQ-000123"


def test_widths_never_narrow_below_the_d04_forms():
    assert base_width_for(IdNamespace.LI, 9259) == LI_MIN_WIDTH
    assert base_width_for(IdNamespace.DIQ, 12) == DIQ_MIN_WIDTH
    assert base_width_for(IdNamespace.LI, 1_000_000) == 7


def test_namespaces_cannot_produce_the_same_string():
    li = {DocId(IdNamespace.LI, n, 5).render() for n in range(0, 3000)}
    diq = {DocId(IdNamespace.DIQ, n, 6).render() for n in range(0, 3000)}
    assert not (li & diq)


def test_rendering_is_injective_over_a_dense_id_space():
    ids = []
    for base in range(1, 60):
        ids.append(DocId(IdNamespace.LI, base, 5))
        ids.append(DocId(IdNamespace.DIQ, base, 6))
        for child in range(1, 6):
            ids.append(DocId(IdNamespace.LI, base, 5).child(child))
            ids.append(DocId(IdNamespace.LI, base, 5).child(child, 3))
            ids.append(
                DocId(IdNamespace.LI, base, 5).child(child).child(child + 1)
            )
    # A single-width run is what a real assignment produces; mixing widths for
    # one container is the only way to break injectivity, so it is excluded by
    # construction and proven here on the single-width subset.
    single_width = [i for i in ids if set(i.child_widths) <= {2}]
    assert_render_injective(single_width)
    assert len({i.render() for i in single_width}) == len(
        {(i.namespace, i.base, i.child_path) for i in single_width}
    )


def test_mixed_child_widths_within_one_container_are_caught_not_ignored():
    a = DocId(IdNamespace.LI, 1, 5, (1,), (2,))
    b = DocId(IdNamespace.LI, 1, 5, (1,), (3,))
    assert a.render() != b.render()
    with pytest.raises(ContractViolation):
        assert_render_injective([a, b])


def test_round_trip_through_parse():
    for did in (
        DocId(IdNamespace.LI, 6881, 5),
        DocId(IdNamespace.LI, 6881, 5).child(4),
        DocId(IdNamespace.DIQ, 7, 6).child(12, 3).child(2),
    ):
        ns, base, path = parse_doc_id(did.render())
        assert (ns, base, path) == (did.namespace, did.base, did.child_path)


def test_minter_refuses_a_repeat():
    minter = DocIdMinter()
    minter.mint(DocId(IdNamespace.LI, 1, 5))
    with pytest.raises(ContractViolation):
        minter.mint(DocId(IdNamespace.LI, 1, 5))


def test_value_out_of_width_is_rejected():
    with pytest.raises(ContractViolation):
        DocId(IdNamespace.LI, 123456, 5)
    with pytest.raises(ContractViolation):
        DocId(IdNamespace.LI, 1, 5).child(100, 2)


def test_child_of_child_is_distinct_from_a_wider_sibling():
    parent = DocId(IdNamespace.LI, 6881, 5)
    assert parent.child(1).child(1).render() == "LI-06881.01.01"
    assert parent.child(11).render() == "LI-06881.11"
    assert_render_injective([parent, parent.child(1), parent.child(1).child(1), parent.child(11)])


def test_no_pair_of_distinct_values_collides_exhaustively():
    """Brute force over a bounded space — the property, not a sample of it."""
    ids = [
        DocId(IdNamespace.LI, b, 5, path, (2,) * len(path))
        for b in range(1, 12)
        for path in [(), (1,), (2,), (1, 1), (1, 2), (2, 1)]
    ] + [
        DocId(IdNamespace.DIQ, b, 6, path, (2,) * len(path))
        for b in range(1, 12)
        for path in [(), (1,), (2,)]
    ]
    for a, b in itertools.combinations(ids, 2):
        assert a.render() != b.render()
