"""EVERY field of EVERY seam presentation record, enumerated and accounted for.

**Why this file exists.** B-3 was the third instance in one sprint of one
failure: a field added to the seam and never wired through.

* **A-12** was raised by two tracks and applied by neither. The product shipped
  a permanently disabled button.
* **A-14** was applied to the seam and left unwired for the whole sprint, so a
  Bates-stamped production came out of the GUI with **zero locators**.
* **B-3** — ``PackageResult.missing`` existed, ``build_upload_package`` computed
  it, and ``RealPipeline.build_package`` built its result without it. The test
  that guarded it asserted a **private holding attribute** rather than the
  returned record, so it passed while the user-visible path stayed wrong.

``docs/contracts/amendments.toml`` and ``tests/test_amendments.py`` close the
DECLARATION half — an amendment cannot be referenced without an entry saying
what "applied" would mean. This file closes the POPULATION half, which is the
half B-3 was: the declaration was perfect and the value was ().

So the question is not "is ``missing`` propagated". It is: **for every field of
every presentation record on the seam, is it populated by ``RealPipeline`` from
real pipeline data, and does it reach a rendered screen — or is it deliberately
unused, stated as such, here?**

Three probes, and they fail in different ways on purpose:

1. :func:`test_every_seam_field_is_passed_explicitly_by_the_adapter` — SOURCE.
   Every construction of a seam record in ``src/dociq/adapter.py`` must name
   every field, or the omission must be in :data:`UNPASSED` with a reason. This
   is the probe that catches B-3 exactly, before anything runs, and it is
   correct-by-construction: a NEW field on a seam record fails it until someone
   decides where it comes from.
2. :func:`test_every_measurable_seam_field_is_populated_by_a_real_run` —
   RUNTIME. A real end-to-end run through the real adapter, plus a real Path A
   package build, must produce a NON-DEFAULT value for every field declared
   measurable. An explicit keyword that happens to pass a constant default would
   satisfy probe 1 and not this one.
3. :func:`test_every_rendered_seam_field_reaches_a_screen` — RENDER. Fields
   declared as reaching the operator must have their value appear in a rendered
   screen's text. This is the assertion the old B-3 test could not make.

**Nobody has ever driven this GUI with a mouse.** Probe 3 reads widget text
under the offscreen platform plugin. It proves the value reaches a widget; it
does not prove a human can see it on a monitor. That limitation is disclosed in
``docs/verification/codex_r2_uigap_2026-08-04.md`` and stays disclosed.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dociq.gui import pipeline as seam  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "src" / "dociq" / "adapter.py"


def _records() -> dict[str, type]:
    """Every frozen record DEFINED on the seam, found by reflection.

    Reflection, not a hand-written list: a list is a second place a record can
    be forgotten, and forgetting is the entire failure this file is about.
    """
    return {
        name: obj for name, obj in vars(seam).items()
        if dataclasses.is_dataclass(obj)
        and getattr(obj, "__module__", "") == seam.__name__
    }


# ---------------------------------------------------------------------------
# The enumeration. Every entry is a RULING, and an unlisted field fails.
# ---------------------------------------------------------------------------

INBOUND = {"RunRequest"}
"""Records that travel GUI → pipeline, not pipeline → GUI.

``RunRequest`` is built by the setup screen and consumed by the adapter, so
"populated by RealPipeline" is not a property it can have. It is named here
rather than silently skipped.
"""

_NO_BASIS = (
    "TokenBasis() with no arguments is the 'no figures were measured' sentinel "
    "returned by profile_rules for a profile that has not been run against this "
    "matter. Its emptiness IS its meaning, and the checklist renders it as a "
    "loud empty state."
)
_NO_FOLDER = (
    "Omitted only on the not-a-directory branch, FolderPreview(0, 0). There is "
    "no folder to describe. The real branch passes every field."
)

UNPASSED: dict[tuple[str, str], str] = {
    ("ReductionPlan", "capacity"): (
        "D-21's ruled reference line, DIRECT_CONTEXT_TOKENS. The adapter has no "
        "run-specific capacity to supply and passing the constant explicitly "
        "would put the number in a fourth place — its own docstring already "
        "names three."
    ),
    ("TokenEstimate", "ratio_refuted"): (
        "Omitted only on the NO-FIGURE branch (`_estimate(None)`), for a run "
        "that published nothing. There is no measured structure to refute a "
        "band against. The measured branch passes it explicitly."
    ),
    ("TokenBasis", "provenance"): _NO_BASIS,
    ("TokenBasis", "is_structural"): _NO_BASIS,
    ("TokenBasis", "ratio_refuted"): _NO_BASIS,
    ("FolderPreview", "by_extension"): _NO_FOLDER,
    ("FolderPreview", "estimated_minutes"): _NO_FOLDER,
}
"""Construction sites in ``adapter.py`` that may omit a field, and WHY.

Every entry is a ruling that the default is the right value at that site, not a
note that it has not been done yet. B-3 would never have qualified: the emit
layer had computed the real value and the adapter had it in hand.
"""


@pytest.mark.parametrize("record", sorted(_records()))
def test_every_seam_field_is_passed_explicitly_by_the_adapter(record: str) -> None:
    """FAIL-BEFORE (B-3, watched red): with ``missing=package.missing`` removed
    from ``RealPipeline.build_package``, this reports
    ``PackageResult.missing`` unpassed at that line.

    Also found, on its first run: ``ReductionLever.rule`` and
    ``ReductionLever.note`` — A-11b's verbatim pattern and the expert's own
    stated reason for an omission — were at their defaults at ALL THREE
    construction sites in the adapter, including ``profile_rules``, the §6
    checklist path the amendment was written for. One amendment older than B-3
    and the same shape.
    """
    cls = _records()[record]
    if record in INBOUND:
        pytest.skip(f"{record} travels GUI → pipeline; see INBOUND")

    fields = [f.name for f in dataclasses.fields(cls)]
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    sites = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == record):
            continue
        sites += 1
        given = (set(fields[:len(node.args)])
                 | {kw.arg for kw in node.keywords if kw.arg})
        for field in fields:
            if field in given:
                continue
            reason = UNPASSED.get((record, field))
            assert reason, (
                f"{ADAPTER.name}:{node.lineno} builds {record} without "
                f"naming '{field}', and there is no ruling for it in UNPASSED. "
                f"Either populate it from real pipeline data or record WHY the "
                f"default is right at this site. This is the B-3 class: a seam "
                f"field the declaration promises and the construction drops."
            )
    if sites == 0:
        pytest.skip(f"the adapter never constructs {record}")


def test_the_enumeration_covers_every_record_on_the_seam() -> None:
    """The enumeration cannot quietly stop covering something.

    A record added to the seam and not classified here would otherwise be
    checked by a parametrization that simply never ran for it.
    """
    records = set(_records())
    assert records, "reflection found no seam records — the probe is vacuous"
    assert INBOUND <= records
    stale = {r for r, _f in UNPASSED} - records
    assert not stale, f"UNPASSED rules for records that no longer exist: {stale}"
    for record, field in UNPASSED:
        names = {f.name for f in dataclasses.fields(_records()[record])}
        assert field in names, (
            f"UNPASSED names {record}.{field}, which the record does not have — "
            f"a ruling that outlived the thing it ruled on")


def test_no_private_holding_attribute_stands_in_for_a_seam_field() -> None:
    """The specific anti-pattern B-3 named, refused by name.

    ``RealPipeline.last_package_missing`` held the emit layer's report of what a
    package could not include, beside a seam that had nowhere to put it. The
    holding attribute outlived the gap by an amendment, the GUI never read it,
    and the test that guarded it asserted the attribute. Both are gone.
    """
    from dociq import adapter

    pipe = adapter.RealPipeline()
    banned = {"last_package_missing"}
    present = banned & set(vars(pipe))
    assert not present, (
        f"a private attribute is standing in for a seam field again: {present}. "
        f"Report the shape the seam needs and have it applied; do not hold the "
        f"value beside a screen that cannot see it."
    )


# ---------------------------------------------------------------------------
# Probe 2 — the fields a real run must actually populate
# ---------------------------------------------------------------------------

MEASURED: tuple[tuple[str, str], ...] = (
    ("RunOutcome", "result"),
    ("RunOutcome", "tokens_before"),
    ("RunOutcome", "tokens_after"),
    ("RunOutcome", "output_root"),
    ("PackageResult", "root"),
    ("PackageResult", "file_count"),
    ("PackageResult", "total_bytes"),
    ("PackageResult", "scope_statement"),
    ("PackageResult", "doc_count"),
    ("PackageResult", "missing"),
)
"""Fields a real run over the fixture corpus must leave at a NON-DEFAULT value.

Deliberately not every field: ``RunOutcome.reconciliation`` is None without a
master index and ``BatesProposal.alternatives`` is empty for a single-series
production, and demanding a value there would be demanding the fixture corpus
change to suit the probe. What is listed is what this corpus does exercise, and
``missing`` is on it because the run below asks for a document the folder does
not hold — which is B-3's own scenario, measured.
"""


@pytest.fixture(scope="module")
def package(real_run):
    """A real Path A package, deliberately one document short.

    ``LI-99999`` has no ``clean_text`` file. This is Codex's failure scenario
    executed rather than described: the operator selects two documents, the
    folder holds one, and the seam has to say so.
    """
    from dociq import adapter

    outcome, _events, _root = real_run
    real = tuple(d.doc_id for d in outcome.result.documents)[:1]
    return adapter.RealPipeline().build_package(
        outcome, real + ("LI-99999",), "SCOPE OF THIS PACKAGE\n")


@pytest.mark.parametrize("record,field", MEASURED,
                         ids=[f"{r}.{f}" for r, f in MEASURED])
def test_every_measurable_seam_field_is_populated_by_a_real_run(
    record: str, field: str, real_run, package
) -> None:
    """FAIL-BEFORE (B-3, watched red): ``PackageResult.missing`` reads () here
    with the propagation removed, while the emit layer reports ``LI-99999``.

    Explicit-keyword passing (probe 1) is not enough on its own — a keyword can
    pass a constant that equals the default. This measures the value.
    """
    outcome, _events, _root = real_run
    obj = {"RunOutcome": outcome, "PackageResult": package}[record]
    spec = {f.name: f for f in dataclasses.fields(obj)}[field]
    value = getattr(obj, field)

    default = spec.default
    if default is not dataclasses.MISSING:
        assert value != default, (
            f"{record}.{field} came back at its DEFAULT ({default!r}) from a "
            f"REAL run. The declaration says the seam carries it; this says the "
            f"pipeline does not put anything in it."
        )
    assert value not in (None, "", (), 0), (
        f"{record}.{field} is empty after a real run")


def test_the_missing_doc_id_is_the_emit_layers_own_report(package) -> None:
    """Not merely non-default — the RIGHT value, from the layer that knows."""
    assert package.missing == ("LI-99999",)
    assert package.doc_count == 1, (
        "the package claims a document it does not contain")


# ---------------------------------------------------------------------------
# Probe 3 — and does it reach a screen the operator reads?
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _screen_text(widget) -> str:
    from PySide6.QtWidgets import QLabel, QLineEdit

    parts = [lab.text() for lab in widget.findChildren(QLabel)]
    parts += [edit.text() for edit in widget.findChildren(QLineEdit)]
    return "\n".join(parts)


def test_every_rendered_seam_field_reaches_a_screen(app, real_run, package):
    """The assertion the old B-3 test could not make.

    It asserted ``RealPipeline.last_package_missing`` — a private attribute —
    and passed while the operator's screen showed nothing. So this drives the
    real ``PackageResult`` through the real window and reads the rendered text.
    """
    from dociq.gui.main_window import MainWindow
    from dociq.gui.view_models import PackageScope

    outcome, _events, _root = real_run

    class _Pipe:
        def profiles(self):
            return ()

        def preview_folder(self, path):
            return seam.FolderPreview(0, 0)

        def disclosure(self):
            return ""

        def run(self, request, on_progress, should_cancel, confirm_bates=None):
            return outcome

        def matter_layout_note(self, outcome):
            return ""

        def build_package(self, outcome, doc_ids, scope_statement):
            return package

    window = MainWindow(pipeline=_Pipe())
    try:
        window.show_outcome(outcome)
        window.show_handoff()
        window.handoff.build_package_requested.emit(PackageScope())
        text = _screen_text(window.handoff)

        assert package.root in text, "PackageResult.root reaches no screen"
        assert f"{package.doc_count:,} document" in text, \
            "PackageResult.doc_count reaches no screen"
        assert f"{package.file_count:,} file" in text, \
            "PackageResult.file_count reaches no screen"
        assert "LI-99999" in text, (
            "PackageResult.missing reaches no screen — B-3's user-visible half, "
            "which is the half the old test could not see"
        )
    finally:
        window.close()


def test_the_checklists_rule_and_note_reach_the_screen(app, tmp_path) -> None:
    """A-11b, on the source that replaced the one it was written against.

    The amendment asked that the checklist show what a DROP actually CATCHES and
    the expert's stated reason, not merely that a rule exists. It was written
    when both came from a profile's pattern and notes field. D-35 moved them to
    the template family's patterns and rationale, and D-38 deleted the profile
    system entirely — so the amendment's guarantee is now stronger than it was:
    a profile's note could be blank, and ``SectionFamily.validate()`` refuses a
    family with no rationale.
    """
    from dociq import adapter
    from dociq.gui.main_window import MainWindow
    from dociq.sections.templates import PROGRESS_REPORT

    win = MainWindow(adapter.RealPipeline())
    try:
        win.show_template_checklist()
        from PySide6.QtWidgets import QLabel
        text = " ".join(w.text() for w in win.checklist.findChildren(QLabel))
        family = PROGRESS_REPORT.families[0]
        assert family.display_name in text, (
            "the checklist does not name the family")
        assert family.rationale[:40] in text, (
            "the expert's stated cost did not reach the screen (A-11b)")
    finally:
        win.deleteLater()

