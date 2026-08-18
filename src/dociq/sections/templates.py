"""The standard templates DocIQ ships (D-24).

D-24: *"there should be standard templates for what types of pages get dropped,
and they must not be attributable to any of the corpus projects — no 'MODEC MPR
profile', no 'Petrobras CER profile', because a template named after a matter
implies decisions taken on that matter."*

**Shipped as code rather than as data files, deliberately.** An expert-authored
profile is YAML in the D-05 library because an expert writes it. A template is a
Long International artifact that versions with the build, and shipping it as
Python means it cannot arrive malformed on a client machine, cannot be edited
into something matter-specific without a commit, and — the part that matters —
inherits :class:`SectionFamily`'s structural refusal to carry a disposition.
There is no file here in which somebody could write "drop".

**Every family below is a page TYPE.** None names a project, a vessel, a client
or a yard, and `test_no_template_names_a_project` asserts it against the real
corpus's own project tokens rather than against a list of words someone chose.

**The risk column is §4's and is deliberately not correlated with size.** The two
largest levers in this file are `schedule-activity-tables` (33.9% of the measured
corpus) and `table-of-contents` (5.4%); the most dangerous are
`weather-logs` and `progress-photographs`, which are worth almost nothing in
tokens and can be the only proof of a site condition on a date. A checklist that
sorted by saving would put a HIGH-risk row next to a large number and an easy
click, which is what §5.3 exists to prevent.
"""

from __future__ import annotations

from dociq.sections.model import Risk, SectionFamily, SectionTemplate

__all__ = ["PROGRESS_REPORT", "BUILT_IN_TEMPLATES", "template_by_id"]


def _f(
    family_id: str,
    display_name: str,
    patterns: tuple[str, ...],
    risk: Risk,
    rationale: str,
    *,
    offer: bool = True,
) -> SectionFamily:
    return SectionFamily(
        family_id=family_id,
        display_name=display_name,
        patterns=patterns,
        risk=risk,
        rationale=rationale,
        offer=offer,
    )


# ---------------------------------------------------------------------------
# Recurring progress reports — monthly and weekly
# ---------------------------------------------------------------------------
#
# Patterns match a normalized FAMILY KEY, never raw page text: upper-case,
# accent-folded, leading section numbering removed, matter tokens removed. That
# is why `^` anchors are safe here and would not have been on the old engine —
# the string being matched is an outline entry's title, not a line of prose.

PROGRESS_REPORT = SectionTemplate(
    template_id="progress-report",
    version="1",
    display_name="Recurring progress report (monthly / weekly)",
    notes=(
        "Section types common to engineering and construction progress reports. "
        "Ships UNENGAGED: no family here drops a page until an expert engages "
        "it and his name is recorded against it (D-34)."
    ),
    families=(
        # --- offered, low risk -------------------------------------------------
        _f(
            "table-of-contents",
            "Table of contents",
            (r"^TABLE OF CONTENTS$", r"^CONTENTS$", r"^INDICE$"),
            Risk.LOW,
            "Navigation for a paper document. Measured at 5.4% of the text on "
            "the sampled corpus — the second-largest safe lever. Nothing in it "
            "is evidence that is not also in the sections it lists.",
        ),
        _f(
            "cover-page",
            "Cover / title page",
            (r"^COVER( PAGE)?$", r"^TITLE PAGE$", r"^FRONT COVER$"),
            Risk.LOW,
            "Report title, revision and date. The same facts are carried by the "
            "document's own index row and by the page markers, so removing it "
            "loses no locator.",
        ),
        _f(
            "distribution-list",
            "Distribution / circulation list",
            (r"^DISTRIBUTION", r"^CIRCULATION", r"^COPY TO"),
            Risk.LOW,
            "Who received the report. LOW only because it is duplicated on every "
            "issue — but note it can evidence NOTICE, so an expert running a "
            "notice argument should leave it.",
        ),
        _f(
            "document-control",
            "Document control / revision history",
            (r"^DOCUMENT CONTROL", r"^REVISION (HISTORY|RECORD)", r"^APPROVAL SHEET"),
            Risk.LOW,
            "Revision table and sign-off block. Retained in the index either "
            "way; the page itself repeats each issue.",
        ),
        _f(
            "blank-page",
            "Blank page",
            (r"^BLANK PAGE$", r"^PAGINA EM BRANCO$", r"^PAGE (LEFT )?BLANK"),
            Risk.LOW,
            "Pagination filler carrying no content. Present in this form because "
            "the measured corpus labels them explicitly in its own outline, and "
            "the third most frequent label in that corpus is Portuguese.",
        ),
        _f(
            "organization-chart",
            "Organization chart",
            (r"^ORGANI[SZ]ATION", r"^ORG CHART", r"^PROJECT ORGANI[SZ]ATION"),
            Risk.LOW,
            "Names and reporting lines, largely static between issues. Leave it "
            "where the claim turns on who held a role at a date.",
        ),
        # --- offered, medium risk ---------------------------------------------
        _f(
            "schedule-activity-tables",
            "Schedule / activity tables",
            (
                r"^SCHEDULE",
                r"ACTIVITY (LIST|LISTING|TABLE|REPORT)",
                r"^(L[1-4] )?SUMMARY SCHEDULE",
                r"LOOK ?AHEAD",
                r"^THREE WEEK",
                r"^90 DAY",
            ),
            Risk.MEDIUM,
            "The largest lever on the measured corpus by a wide margin — 33.9% "
            "of the text, roughly 170x the photographs, and the only category "
            "whose removal changes whether a matter fits. These are P6 activity "
            "listings pasted into the report; where the native .xer files are in "
            "evidence the pasted grid is a lossy render of a better source. "
            "DEFAULT OFF and it stays off (D-27): a pasted table that DIFFERS "
            "from the native file is itself evidence.",
        ),
        _f(
            "hse-statistics",
            "HSE statistics",
            (r"^HSE", r"^HEALTH,? SAFETY", r"^SAFETY STATISTICS", r"\b(LTIR|TRIR)\b"),
            Risk.MEDIUM,
            "Incident and lost-time rates. Can matter where a safety stand-down "
            "drove delay, so read the narrative before engaging this.",
        ),
        _f(
            "progress-curves",
            "Progress S-curves / % complete charts",
            (r"S ?CURVE", r"^PROGRESS CURVE", r"^PERCENT COMPLETE", r"^HISTOGRAM$"),
            Risk.MEDIUM,
            "Image-dominant charts carrying little extractable text, so the "
            "token saving is small. The underlying percentages usually also "
            "appear in the progress narrative and tables.",
        ),
        _f(
            "risk-register",
            "Risk register extract",
            (r"^RISK REGISTER", r"^RISK LOG", r"^TOP (TEN|10) RISKS"),
            Risk.MEDIUM,
            "Forward-looking risk items. Note that a risk recorded before it "
            "materialized can evidence foreseeability, which is often the point "
            "in dispute.",
        ),
        # --- offered, high risk — offered because the taxonomy offers them, and
        #     each row says plainly what engaging it costs -----------------------
        _f(
            "progress-photographs",
            "Progress photographs",
            (r"^PROGRESS PHOTO", r"^PHOTOGRAPH", r"^PHOTO (LOG|REPORT)", r"^SITE PHOTO"),
            Risk.HIGH,
            "OFTEN THE ONLY PROOF OF SITE CONDITION ON A DATE. And read the "
            "saving before engaging it: photographs are 0.2% of the text on the "
            "measured corpus — roughly two orders of magnitude less than the "
            "Sprint-1 mockups advertised. This is a PAGE-COUNT lever, not a "
            "token lever, and on the route that reads the matter folder from "
            "disk page count does not bind at all.",
        ),
        # --- recognized and never offered -------------------------------------
        _f(
            "executive-summary",
            "Executive summary",
            (r"^EXECUTIVE SUMMARY", r"^SUMMARY$", r"^PROJECT HIGHLIGHTS"),
            Risk.HIGH,
            "The report's own account of where the project stood. Recognized so "
            "it can be found and cited; never offered as an omission.",
            offer=False,
        ),
        _f(
            "critical-path-narrative",
            "Critical path narrative",
            (r"CRITICAL PATH", r"^SCHEDULE NARRATIVE", r"^DELAY (ANALYSIS|NARRATIVE)"),
            Risk.HIGH,
            "The contemporaneous statement of what was driving completion. "
            "Recognized, never offered.",
            offer=False,
        ),
        _f(
            "change-and-variation",
            "Change order / variation log",
            (r"^CHANGE (ORDER|LOG)", r"^VARIATION", r"^CONTRACT ADMINISTRATION"),
            Risk.HIGH,
            "Entitlement evidence. Recognized, never offered.",
            offer=False,
        ),
        _f(
            "quality-ncr",
            "Quality / NCR logs",
            (r"^QUALITY", r"\bNCR\b", r"^QA ?/? ?QC", r"NON ?CONFORMANCE"),
            Risk.HIGH,
            "Defect and rework claims turn on these. Recognized, never offered.",
            offer=False,
        ),
        _f(
            "manpower-histograms",
            "Manpower / staffing histograms",
            (r"MANPOWER", r"^STAFFING", r"^LABOU?R (HISTOGRAM|CURVE)"),
            Risk.HIGH,
            "Decisive in disruption and labour-productivity claims. Recognized, "
            "never offered.",
            offer=False,
        ),
        _f(
            "weather-logs",
            "Weather logs",
            (r"^WEATHER", r"^METEOROLOG", r"^CLIMATIC"),
            Risk.HIGH,
            "Decisive in weather-delay claims, and worth almost nothing in "
            "tokens. Recognized, never offered.",
            offer=False,
        ),
        _f(
            "action-items",
            "Action item register",
            (r"^ACTION (ITEM|REGISTER|LOG)", r"^OPEN ACTIONS", r"^OUTSTANDING ACTIONS"),
            Risk.HIGH,
            "The densest causation evidence in a recurring record. Recognized, "
            "never offered.",
            offer=False,
        ),
        _f(
            "timesheets",
            "Timesheets / labour tickets",
            (r"^TIMESHEET", r"^LABOU?R TICKET", r"^DAILY (LABOU?R|MANHOUR)"),
            Risk.HIGH,
            "The primary record in disruption claims. Recognized, never offered.",
            offer=False,
        ),
    ),
)


BUILT_IN_TEMPLATES: tuple[SectionTemplate, ...] = (PROGRESS_REPORT,)
"""Every template DocIQ ships.

One, deliberately. D-24 asks for standard templates keyed to page type, and the
only recurring format this build has measured is the progress report. A template
for correspondence, meeting minutes or technical records would be written from
§4's table without a measurement behind it, and §2 already showed what a
reasonable-looking unmeasured recognizer does.
"""


def template_by_id(template_id: str) -> SectionTemplate | None:
    for template in BUILT_IN_TEMPLATES:
        if template.template_id == template_id:
            return template
    return None
