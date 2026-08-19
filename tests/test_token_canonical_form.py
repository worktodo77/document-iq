"""A canonical form must be the form the behavior keys on.

D-39's first canonicalization upper-cased, de-duplicated and sorted. Both halves
of that were wrong in ways the tests it shipped with could not see:

* **It folded less than the matching does.** `strip_project_tokens` folds each
  token with `normalize_label` — accents removed, non-alphanumerics collapsed —
  so `PETROBRÁS` and `PETROBRAS` strip identically. Upper-casing alone left them
  as two run identities for one reduction, which is the exact defect
  canonicalization was added to close.
* **Sorting changed the reduction.** Tokens were applied in the caller's order,
  and a token that is a word-subsequence of another gives a different result
  depending which goes first. Sorting `("FPSO ALMIRANTE BARROSO", "BARROSO")`
  puts the SHORT one first and strands `FPSO ALMIRANTE` in the label — so the
  canonical form silently altered the reduction it claimed to canonicalize.

These are one class, not two: a normalization that does not agree with the
behavior it normalizes. Both are asserted here against the real functions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace  # noqa: E402

from dociq.contracts import (  # noqa: E402
    RunConfig,
    canonical_tokens,
    fold_label,
    run_identity,
)
from dociq.sections.normalize import (  # noqa: E402
    normalize_label,
    strip_project_tokens,
)

# Accents, punctuation, case, spacing — every axis `fold_label` collapses.
EQUIVALENT_SPELLINGS = [
    ("PETROBRÁS", "PETROBRAS"),
    ("T1-R1", "T1 R1"),
    ("mv32", "MV32"),
    ("BOMESC  YARD", "BOMESC YARD"),
    ("MODEC/MV32", "MODEC MV32"),
]


def _config(tokens: tuple[str, ...]) -> RunConfig:
    return replace(
        RunConfig(source_root=r"D:\m", output_root=r"D:\m\out"),
        project_tokens=tokens,
    )


def test_the_canonical_form_is_the_form_the_matching_uses():
    """One fold, asserted across the module boundary rather than assumed.

    `normalize_label` and `canonical_tokens` live in different packages for a
    dependency reason, which is exactly the arrangement in which two folds drift
    apart without anyone noticing.
    """
    for a, b in EQUIVALENT_SPELLINGS:
        assert normalize_label(a) == fold_label(a)
        assert canonical_tokens((a,)) == (normalize_label(a),)
        assert canonical_tokens((a,)) == canonical_tokens((b,)), (a, b)


def test_spellings_that_strip_identically_are_one_run_identity():
    """The property canonicalization exists for, stated over the axes that broke
    it. `PETROBRÁS` and `PETROBRAS` remove the same text from the same labels;
    two identities for that is a false "different configuration"."""
    for a, b in EQUIVALENT_SPELLINGS:
        assert run_identity(_config((a,))) == run_identity(_config((b,))), (a, b)


def test_tokens_that_strip_differently_still_move_the_identity():
    """The other half — canonicalization must not flatten a real difference."""
    ids = {run_identity(_config(t)) for t in
           [(), ("MV32",), ("MV32", "BOMESC"), ("BOMESC",)]}
    assert len(ids) == 4


def test_stripping_does_not_depend_on_the_order_the_operator_typed():
    """The identity sorts the token list, so a reduction that depends on order
    is a reduction the canonical form changes. Overlapping multi-word tokens are
    where that bites, and an operator naming both a vessel and its short name is
    an ordinary thing to do, not a contrived one."""
    label = "FPSO ALMIRANTE BARROSO MV32 APPENDICES"
    long_first = strip_project_tokens(label, ("FPSO ALMIRANTE BARROSO", "BARROSO"))
    short_first = strip_project_tokens(label, ("BARROSO", "FPSO ALMIRANTE BARROSO"))
    assert long_first == short_first
    # And the surviving text is the one that lets a family match: the whole
    # project name goes, not a fragment of it.
    assert long_first == "MV32 APPENDICES"


def test_the_canonical_list_reduces_exactly_as_the_typed_list_did():
    """The end-to-end statement. Whatever the operator types, the list the run
    hashes must strip the same text — otherwise canonicalization is not a
    renaming, it is a behavior change nobody asked for."""
    label = "FPSO ALMIRANTE BARROSO MV32 APPENDICES"
    # Typed LONG-first on purpose: `canonical_tokens` sorts alphabetically and
    # puts `BARROSO` first, so this is the case where canonicalization reverses
    # the operator's order. A fixture typed short-first would agree with the
    # canonical order by accident and discriminate nothing.
    typed = ("fpso almirante barroso", "BARROSO", "MV32")
    assert strip_project_tokens(label, typed) == strip_project_tokens(
        label, canonical_tokens(typed))


def test_an_empty_or_punctuation_only_token_is_dropped_not_hashed():
    """An operator types a stray comma or a lone hyphen. Neither strips
    anything, so neither may mint a new run identity."""
    assert canonical_tokens(("MV32", "", "   ", "-", "//")) == ("MV32",)
    assert run_identity(_config(("MV32", "-"))) == run_identity(_config(("MV32",)))
