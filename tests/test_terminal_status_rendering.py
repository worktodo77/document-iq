"""Every terminal status is rendered as ITSELF — Codex review #2 fix round, A-3.

``RunTermination.headline()`` recognized ``COMPLETED`` and ``BLOCKED`` and let
every other member fall through a ternary to the word ``CANCELLED``. Amendment
A-15 added :attr:`~dociq.contracts.TerminalStatus.REFUSED` and did not touch
that renderer, so ``run_status.json`` was machine-readable as
``terminal_status: "refused"`` while the headline beside it — and the summary
PDF's masthead, and the GUI's banner — said ``RUN CANCELLED``. The operator was
told that somebody stopped the run, when nobody did: DocIQ read a COMPLETE
corpus, assigned an identifier to every document, and rejected the set at its
own §4 Stage 6 integrity gate.

The GUI added a second false statement on top of the first, for every
non-complete status: that the figures beside it "describe only what was read
before the run stopped".

**This is the fifth instance of one class** — a contract enum gains a member and
nothing greps for the places that turn it into words (A-12, A-14, B-3, A-11b
were the others). So this module does not only assert that ``REFUSED`` prints
the right word. It asserts that:

* every member of ``TerminalStatus`` has its OWN entry in
  :data:`dociq.runstate.STATUS_PROSE` and renders text no other member renders;
* the three consumers — ``run_status.json`` on disk, the summary PDF/summary
  data, and the GUI banner — each carry that text;
* and, as a standing probe, that **no operator-facing enum renderer anywhere in
  ``src/dociq`` has a silent fallback**. The last one is the tripwire an enum
  member added next year has to trip.

**Disclosed and it stays disclosed: nobody has ever driven this GUI with a
mouse.** The banner assertions read view-model text offscreen.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from dociq.contracts import TerminalStatus
from dociq.emit.paths import OutputLayout
from dociq.runstate import (
    INCOMPLETE_DIR,
    STATUS_FILENAME,
    STATUS_PROSE,
    RunTermination,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "dociq"


# ---------------------------------------------------------------------------
# The table is total, and each member says something only it says
# ---------------------------------------------------------------------------


def test_every_terminal_status_has_its_own_prose() -> None:
    assert set(STATUS_PROSE) == set(TerminalStatus), (
        "a TerminalStatus member with no entry in STATUS_PROSE is finding A-3: "
        "it will be rendered by whatever branch happens to catch it"
    )


@pytest.mark.parametrize("status", list(TerminalStatus))
def test_no_status_is_rendered_in_another_status_s_words(status) -> None:
    """FAIL-BEFORE: ``REFUSED`` produced the byte-identical headline to
    ``CANCELLED`` apart from the reason string, so this fails on the REFUSED
    case against the old ternary."""
    mine = RunTermination(status, "the reason.")
    others = [
        RunTermination(s, "the reason.")
        for s in TerminalStatus if s is not status
    ]
    for other in others:
        assert mine.headline() != other.headline(), (
            f"{status.name} and {other.status.name} render the SAME headline; "
            f"one of them is being described as the other"
        )


DISTINCTIVE = {
    TerminalStatus.COMPLETED: "completed",
    TerminalStatus.BLOCKED: "RUN BLOCKED",
    TerminalStatus.CANCELLED: "RUN CANCELLED",
    TerminalStatus.REFUSED: "PUBLICATION REFUSED",
}


def test_the_distinctive_words_are_stated_for_every_member() -> None:
    """The table above is only worth asserting against if it is itself total —
    otherwise a new member is silently untested here as well."""
    assert set(DISTINCTIVE) == set(TerminalStatus)


@pytest.mark.parametrize("status", list(TerminalStatus))
def test_each_headline_carries_its_own_word_and_no_other(status) -> None:
    head = RunTermination(status, "the reason.").headline()
    assert DISTINCTIVE[status] in head
    for other, word in DISTINCTIVE.items():
        if other is status:
            continue
        if word in DISTINCTIVE[status]:  # "completed" inside a longer word
            continue
        assert word not in head, (
            f"a {status.name} run's headline contains {word!r}")


def test_a_refused_run_is_not_described_as_stopped_part_way() -> None:
    """The second half of A-3. A refused run read the COMPLETE corpus; the
    coverage sentence that belongs to a cancelled run is false about it."""
    refused = RunTermination(TerminalStatus.REFUSED, "accounting did not balance.")
    note = refused.coverage_note()
    assert "COMPLETE corpus" in note
    assert "before the run stopped" not in note, (
        "a refused run is being told the figures cover only a partial read")
    assert "Nobody stopped this run" in refused.headline()


def test_a_completed_run_offers_no_coverage_caveat() -> None:
    assert RunTermination().coverage_note() == ""


# ---------------------------------------------------------------------------
# Consumer 1 — the GUI banner
# ---------------------------------------------------------------------------


def _summary_view(termination: RunTermination):
    from dociq.gui.view_models import CapacityReading, SummaryView
    from dociq.verify.tokens import estimate_tokens

    reading = CapacityReading(estimate_tokens("some text"), 200_000)
    return SummaryView(
        documents=19, unsupported=0, pages_in=100, pages_kept=100,
        pages_dropped=0, capacity=reading, capacity_before=reading,
        flags=(), output_root=r"D:\m\out", id_regime_note="",
        termination=termination, published=termination.publishable,
    )


@pytest.mark.parametrize("status", list(TerminalStatus))
def test_the_summary_banner_states_this_status_and_only_this_status(status):
    """FAIL-BEFORE (REFUSED case): the banner read "RUN CANCELLED … The figures
    below describe only what was read before the run stopped." Both halves were
    false about a refused run."""
    view = _summary_view(RunTermination(status, "the reason."))
    banner = view.status_banner()
    if status is TerminalStatus.COMPLETED:
        assert banner == "", "a banner on every run is a banner nobody reads"
        return
    assert DISTINCTIVE[status] in banner
    for other, word in DISTINCTIVE.items():
        if other is not status and word not in DISTINCTIVE[status]:
            assert word not in banner


def test_the_banner_for_a_refused_run_does_not_claim_a_partial_read():
    banner = _summary_view(
        RunTermination(TerminalStatus.REFUSED, "the manifest carried an "
                       "output it could not classify.")).status_banner()
    assert "PUBLICATION REFUSED" in banner
    assert "CANCELLED" not in banner
    assert "before the run stopped" not in banner
    assert "COMPLETE corpus" in banner


# ---------------------------------------------------------------------------
# Consumer 2 — the summary PDF and the data behind it
# ---------------------------------------------------------------------------


def test_the_summary_pdf_masthead_says_publication_refused(tmp_path):
    """The quarantined ``run_summary.pdf`` is read away from the tool, by
    somebody who has only the paper. It said CANCELLED."""
    pytest.importorskip("pypdf")
    import pypdf

    from dociq.emit.summary import build_summary_data, write_run_summary
    from dociq.verify.tokens import estimate_for_texts

    from .test_emit import assigned

    docs = assigned(2)
    refused = RunTermination(
        TerminalStatus.REFUSED,
        "page accounting did not reconcile.")
    data = build_summary_data(
        matter_name="Project 495",
        source_root=r"C:\matter\native",
        output_root=r"C:\matter\dociq",
        generated_at="2026-08-04 12:00 UTC",
        operator="abachowski",
        documents=docs,
        unsupported=(),
        tokens_before=estimate_for_texts(p.text for d in docs for p in d.pages),
        tokens_after=estimate_for_texts(p.text for d in docs for p in d.pages),
        ocr_threshold_pct=85,
        id_regime="master-index",
        master_index="index.xlsx",
        bates_note="",
        termination=refused,
    )
    assert data.termination.headline().startswith("PUBLICATION REFUSED"), (
        "the summary DATA — what every renderer of it reads — still says the "
        "wrong word")

    path = write_run_summary(data, OutputLayout.at(tmp_path))
    text = " ".join(
        p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages
    ).replace("\n", " ")
    assert "PUBLICATION REFUSED" in text
    assert "CANCELLED" not in text, (
        "the quarantined PDF tells the reader somebody stopped this run")


# ---------------------------------------------------------------------------
# Consumer 3 — run_status.json, on disk, from a real refused run
# ---------------------------------------------------------------------------


def test_run_status_json_headline_agrees_with_its_own_machine_field(
        tmp_path, monkeypatch):
    """The exact artifact Codex reproduced: ``terminal_status: "refused"`` and,
    two lines away in the same file, ``"headline": "RUN CANCELLED …"``.

    Driven through the real pipeline with a real red Stage-6 gate rather than by
    constructing a status object, because the point of the finding is that the
    two fields are written side by side by code nobody re-read."""
    from .test_publication_gate import _force_accounting_red, _run

    out = tmp_path / "matter"
    _force_accounting_red(monkeypatch)
    outcome = _run(out)

    assert outcome.termination.status is TerminalStatus.REFUSED
    status = json.loads(
        (out / INCOMPLETE_DIR / STATUS_FILENAME).read_text(encoding="utf-8"))

    assert status["terminal_status"] == "refused"
    assert status["headline"].startswith("PUBLICATION REFUSED"), status["headline"]
    assert "CANCELLED" not in status["headline"], (
        "the durable record's own headline contradicts its machine field")
    assert status["complete"] is False
    assert status["published"] is False


# ---------------------------------------------------------------------------
# The CLASS probe — no operator-facing enum renderer may have a fallback
# ---------------------------------------------------------------------------


ENUM_NAMES = frozenset({
    "TerminalStatus", "ProcessingStatus", "IdRegime", "Disposition",
    "PageKind", "IdNamespace", "DecisionStatus",
    # A-18/A-20, added when 4092f76 and d3cee24 landed the section engine.
    # Adding a name here is the CHEAP half; the half that matters is walking
    # every renderer of the member, which was done and is recorded below.
    #
    # ``RecognitionTier`` — five renderers, none of which chooses a word:
    #   sections/resolve.py::_REBUILT_EVIDENCE  member -> sentence dict,
    #       subscripted (not ``.get``), total over all four members including
    #       the two Sprint 3 does not build. Asserted below by
    #       ``test_each_member_to_text_map_is_total``.
    #   sections/apply.py                       carries the MEMBER onto
    #       ``SectionDropEntry.tier``; nothing is chosen.
    #   emit/log.py, emit/indexbook.py          write ``.value`` — the enum's
    #       own stable audit string, which every member has and no member
    #       borrows from another.
    #   adapter.py                              collects ``.value`` into
    #       ``ReductionLever.tier``; the sort that picks the strongest tier
    #       sorts those same stable ``t1_``…``t4_`` strings, so a new member
    #       takes its declared position rather than a guessed one.
    #   contracts.py::PageRecord.validate       interpolates ``.value`` into a
    #       refusal message.
    #
    # ``Risk`` — one renderer: ``adapter.py`` writes ``family.risk.value`` onto
    # ``ReductionLever.risk``. ``""`` appears there only where there is no
    # family at all, which is an absent value and not a member's word put in
    # another member's mouth.
    #
    # Neither enum is flattened to a bool and neither is selected by a string
    # ternary — the two probes below now scan for both and find nothing. See
    # the report for the one gap this walk DID find: ``ReductionLever.risk``
    # reaches no widget, so §4's risk grade is on the seam and not on screen.
    "RecognitionTier", "Risk",
})
"""Every enumeration in the product whose members could reach an operator as a
word. Kept as names rather than classes because the probe reads SOURCE, not
imports — a renderer in a module that fails to import is still a renderer."""


def test_the_enum_list_this_probe_scans_for_is_complete() -> None:
    """A probe that scans an out-of-date list of enums is a probe that reports
    green about code it never looked at."""
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = (base.attr if isinstance(base, ast.Attribute)
                        else getattr(base, "id", ""))
                if name == "Enum":
                    found.add(node.name)
    assert found <= ENUM_NAMES, (
        f"enumeration(s) this probe has never scanned: {sorted(found - ENUM_NAMES)}. "
        f"Add them to ENUM_NAMES and check their renderers — an enum nobody "
        f"enumerated is finding A-3 waiting to happen."
    )


def _is_enum_test(node: ast.AST) -> str:
    """The enum class name this comparison tests against, or ""."""
    if not isinstance(node, ast.Compare):
        return ""
    for side in [node.left, *node.comparators]:
        if (isinstance(side, ast.Attribute)
                and isinstance(side.value, ast.Name)
                and side.value.id in ENUM_NAMES):
            return side.value.id
    return ""


def _yields_text(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _sites(kind: str) -> list[tuple[str, str, int, str]]:
    """Every offending site of one shape, as ``(path, expression, line, enum)``.

    ``kind`` is ``"ternary"`` (an enum-tested string ternary) or ``"flatten"``
    (an enum comparison stored into a value). Collected once, here, so the two
    probes below and the sanction-hygiene probe all read the SAME enumeration —
    a sanction list checked against a different walk from the one that enforces
    it is a sanction list that can drift out of agreement with its own probe.
    """
    out: list[tuple[str, str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if kind == "ternary":
                if not isinstance(node, ast.IfExp):
                    continue
                enum_name = _is_enum_test(node.test)
                if not enum_name:
                    continue
                if not (_yields_text(node.body) and _yields_text(node.orelse)):
                    continue
                out.append((rel, ast.unparse(node), node.lineno, enum_name))
                continue
            candidates: list[ast.AST] = []
            if isinstance(node, ast.Call):
                candidates += [kw.value for kw in node.keywords]
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
                candidates.append(node.value)
            for value in candidates:
                enum_name = _is_enum_test(value)
                if enum_name:
                    out.append(
                        (rel, ast.unparse(value), value.lineno, enum_name))
    return out


ALLOWED_TERNARIES: frozenset[tuple[str, str]] = frozenset()
"""Sanctioned enum-tested string ternaries, as ``(relative path, expression)``.

**Empty, and that is the point.** Every entry added here is a place where a new
enum member prints a word chosen for a different member. If one is ever
genuinely needed the justification belongs beside it, in this docstring, in
words a reviewer can refuse.

**Keyed on the EXPRESSION, not on the line number** — see
:data:`ALLOWED_FLATTENINGS` for why the line-number form was a defect of the
same class the probes exist to catch.
"""


def test_no_operator_facing_string_is_chosen_by_an_enum_ternary() -> None:
    """The standing tripwire for the whole class.

    ``"BLOCKED" if status is TerminalStatus.BLOCKED else "CANCELLED"`` is the
    exact shape of A-3: the ``else`` is not a default, it is an assertion about
    every member the author did not think of. The same shape hid a wrong word on
    the §6 approval checklist (``"DROP" if dropped else "KEEP"``, fed by a
    ``Disposition`` flattened to a bool) and in the summary screen's ID-regime
    sentence.

    A member→text ``dict`` subscript is the fix in every case: it raises on a
    member nobody thought of instead of printing a word about a different one.
    """
    offenders: list[str] = []
    for rel, expr, lineno, enum_name in _sites("ternary"):
        if (rel, expr) in ALLOWED_TERNARIES:
            continue
        offenders.append(
            f"{rel}:{lineno} — a string chosen by an "
            f"{enum_name} comparison with an `else` branch")
    assert not offenders, (
        "operator-facing text selected by an enum ternary with a silent "
        "fallback:\n  " + "\n  ".join(offenders)
        + "\nUse a member -> text dict subscript so an unhandled member raises."
    )


ALLOWED_FLATTENINGS: frozenset[tuple[str, str]] = frozenset({
    ("gui/mock_pipeline.py", "status is not ProcessingStatus.FULL"),
})
"""Sanctioned enum→bool flattenings, as ``(relative path, expression)``.

One entry, and its justification: ``flagged=status is not
ProcessingStatus.FULL`` on a :class:`~dociq.gui.pipeline.ProgressEvent` renders
as a row COLOR, not as a word. A new ``ProcessingStatus`` member arriving there
is correctly flagged as not-plain, which is the true answer for any member that
is not ``FULL``, and no member's name is printed in another member's place.

Every other flattening is a wrong word waiting to happen — the §6 checklist's
``engaged`` bool was one, and it is now
:data:`dociq.adapter._LEVER_ENGAGED`.

**Keyed on the EXPRESSION, not on the line number, since 4092f76.** The entry
read ``("gui/mock_pipeline.py", 699)`` and the sanctioned call moved to line 704
when the section engine landed, which is how the rot was noticed. Going red
when a sanctioned site MOVES is the harmless direction; the direction that
matters is the other one — a line-number pin also sanctions whatever unrelated
flattening drifts onto line 699 next, silently, which is finding A-3's own shape
one level up: an ``else`` that is not a default but an assertion about
everything the author did not think of. An expression is what was actually
reviewed, so an expression is what is sanctioned.
"""


def test_no_enum_is_flattened_to_a_bool_that_is_later_rendered_as_a_word():
    """The blind spot in the ternary probe above, closed.

    ``"DROP" if self.dropped else "KEEP"`` is not caught by that probe: the
    ternary tests a BOOL, and the enum was thrown away one layer earlier, at
    ``engaged=rule.disposition is Disposition.DROP``. The comparison is where
    the information is lost, so the comparison is what this scans for.

    **What it does not catch, stated rather than implied:** a flattening
    performed inside a helper function and returned, rather than written at a
    call site or an assignment.

    **That shape WAS present, and the claim that it was not is withdrawn.** The
    sentence here used to end "That shape is not present in ``src/dociq``
    today", and it was false as written — ``ReductionLever.locked`` is a
    ``@property`` returning ``self.kind != LEVER_EXPERT``, which is a three-
    valued discriminator flattened to a bool inside a helper and returned, and
    ``ChecklistRow.disposition_word`` then rendered that bool as the WORD
    "AUTOMATIC". A section the template merely RECOGNIZES and will never offer
    — the executive summary, the critical path narrative, the weather log, the
    timesheets — was labelled AUTOMATIC on the §6 approval screen and attributed
    with "was removed mechanically by the tool. No expert approved this", about
    pages that are KEPT. Fixed at the renderer in
    ``dociq/gui/view_models.py``; see
    ``tests/test_gui_screen_states.py::test_a_recognized_never_offered_row_is_
    kept_and_never_called_automatic``.

    **And this probe still would not see it**, for a second reason worth stating
    beside the first: lever ``kind`` is a module-level ``str`` constant, not an
    ``Enum``, so it is outside :data:`ENUM_NAMES` and outside every scan in this
    module. The AST tripwires cover enum members; a hand-rolled string
    vocabulary has to be covered by rendering tests, and now is.
    """
    offenders: list[str] = []
    for rel, expr, lineno, enum_name in _sites("flatten"):
        if (rel, expr) in ALLOWED_FLATTENINGS:
            continue
        offenders.append(f"{rel}:{lineno} — a {enum_name} flattened to a bool")
    assert not offenders, (
        "enum(s) reduced to a boolean at a value site:\n  "
        + "\n  ".join(offenders)
        + "\nIf the bool is later rendered as a word, a new member prints "
          "another member's word. Use a member -> value dict subscript, or "
          "add the site to ALLOWED_FLATTENINGS with a written justification."
    )


@pytest.mark.parametrize("sanctions,kind,name", [
    (ALLOWED_TERNARIES, "ternary", "ALLOWED_TERNARIES"),
    (ALLOWED_FLATTENINGS, "flatten", "ALLOWED_FLATTENINGS"),
])
def test_every_sanction_names_exactly_one_live_site(sanctions, kind, name) -> None:
    """A sanction that covers nothing, or covers more than it was written for.

    Both directions are the same defect and both are silent. A STALE entry
    outlives the code it excused and sits waiting to excuse whatever is written
    next in its place — which is exactly how the line-number form failed. A
    DUPLICATED entry excuses a second, unreviewed site because it happens to be
    spelled the same way; the reviewer approved one expression in one file, not
    every future copy of it.

    So each entry must match exactly one site: not zero, not two.
    """
    counts: dict[tuple[str, str], int] = {}
    for rel, expr, _lineno, _enum in _sites(kind):
        counts[(rel, expr)] = counts.get((rel, expr), 0) + 1
    for entry in sorted(sanctions):
        found = counts.get(entry, 0)
        assert found == 1, (
            f"{entry} is sanctioned in {name} but matches "
            f"{found} site(s). Zero means the sanction is stale and is now a "
            f"blank cheque for the next thing written there; more than one "
            f"means it is excusing a site nobody reviewed."
        )


@pytest.mark.parametrize("mapping_path", [
    ("dociq.runstate", "STATUS_PROSE", "TerminalStatus"),
    ("dociq.adapter", "_LEVER_ENGAGED", "Disposition"),
    ("dociq.gui.view_models", "_ID_REGIME_NOTE", "IdRegime"),
    # A-18. The one member -> text map ``RecognitionTier`` has. Bound here
    # rather than only trusted to its own module docstring: the map says it
    # covers the two tiers Sprint 3 does not build precisely so the first run
    # after Tier 2 lands does not raise inside a drop log an expert is reading,
    # and a claim like that is worth an assertion.
    ("dociq.sections.resolve", "_REBUILT_EVIDENCE", "RecognitionTier"),
])
def test_each_member_to_text_map_is_total(mapping_path) -> None:
    """The maps that replaced the ternaries carry their own import-time
    tripwire; this asserts the tripwire's condition directly, so that deleting
    the tripwire is also a failure."""
    import importlib

    import dociq.contracts as contracts

    module_name, attr, enum_name = mapping_path
    mapping = getattr(importlib.import_module(module_name), attr)
    assert set(mapping) == set(getattr(contracts, enum_name)), (
        f"{module_name}.{attr} does not cover every {enum_name} member")


def test_the_summary_screen_reads_its_id_regime_sentence_from_the_total_map(
        monkeypatch) -> None:
    """FAIL-BEFORE: ``build_summary`` authored the sentence inline from
    ``master_index is None``, so this substitution reached nothing.

    Needed because the two ``IdRegime`` members happen to be distinguishable by
    that proxy today; only a binding assertion shows the map is on the path the
    screen actually takes."""
    from dociq.contracts import IdRegime
    from dociq.gui import view_models as vm

    from .test_view_models import _outcome

    monkeypatch.setitem(vm._ID_REGIME_NOTE, IdRegime.NATIVE,
                        lambda index: "SENTINEL-NATIVE")
    monkeypatch.setitem(vm._ID_REGIME_NOTE, IdRegime.MASTER_INDEX,
                        lambda index: "SENTINEL-INDEX")
    assert vm.build_summary(_outcome(False)).id_regime_note == "SENTINEL-NATIVE"
    assert vm.build_summary(_outcome(True)).id_regime_note == "SENTINEL-INDEX"


def test_the_total_maps_raise_rather_than_guess() -> None:
    """A member with no entry must be LOUD. Simulated by removing one from a
    copy of the map — the product's own maps are never mutated."""
    partial = {k: v for k, v in STATUS_PROSE.items()
               if k is not TerminalStatus.REFUSED}
    with pytest.raises(KeyError):
        partial[TerminalStatus.REFUSED]
