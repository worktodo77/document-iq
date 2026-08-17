"""The records section recognition produces, and the ones a template carries.

Three types and one rule that connects them, and the rule is D-34:

    A :class:`SectionTemplate` carries no disposition and no approver. It says
    what a section *is* and what omitting it would cost. It cannot drop a page.

    An :class:`ApprovedOmission` carries an approver, and it is created only
    when a human engages a lever. It is the only thing in this package that can
    turn a KEEP into a DROP.

That separation is structural rather than procedural. ``SectionTemplate`` has no
field in which a disposition could be written, so a shipped template file that
tried to drop a page would not parse — which is what "the approver field never
holds a fiction" has to mean if it is to survive a future edit by someone who
has not read D-34.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

from dociq.contracts import DocIQError, RecognitionTier

__all__ = [
    "ApprovedOmission",
    "Risk",
    "SectionFamily",
    "SectionSpan",
    "SectionTemplate",
    "TemplateError",
]

_ID_RE = re.compile(r"^[a-z0-9._-]{1,64}$")


class TemplateError(DocIQError):
    """A template or an omission record is malformed. Always names the
    offending id: templates are read by experts, not only by machines."""


class Risk(str, enum.Enum):
    """The forensic cost of dropping a section wrongly (`section_taxonomy.md`
    §4).

    **Deliberately not correlated with size.** The most dangerous categories in
    the taxonomy are among the smallest — weather logs and progress photographs
    are trivial in tokens and decisive in a weather-delay or site-condition
    claim. §5.3 is the reason this is a required field rather than a nicety: a
    checklist that sorts by saving puts a HIGH-risk row next to a large number
    and an easy click.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """A contiguous run of pages that one tier placed in one section.

    A span, not a carried state. That is the whole of D-35: the engine this
    replaces held a "current section" that never had to end, so a rule written
    for pages 6-7 governed pages 8 to the end of the document. A span names its
    last page, so it cannot.
    """

    section: str
    """The label as the document itself gave it, for display and for the log.
    Kept verbatim — an expert reading the drop log should see the words the
    document used, not a normalization of them."""

    family: str
    """The normalized key a template matches against
    (:func:`dociq.sections.normalize.family_key`). Numbering and matter-specific
    tokens are already removed."""

    tier: RecognitionTier
    start_page: int
    """1-based, inclusive, in the ORIGINAL document's page numbering."""

    end_page: int
    """1-based, inclusive. Equal to :attr:`start_page` for a single page."""

    evidence: str
    """What the tier actually read, in words an expert can check: the outline
    entry's title, or the page-class rule that matched. Reaches the drop log."""

    def validate(self) -> None:
        if self.start_page < 1:
            raise TemplateError(
                f"span {self.section!r}: start_page must be 1-based, "
                f"got {self.start_page}"
            )
        if self.end_page < self.start_page:
            raise TemplateError(
                f"span {self.section!r}: end_page {self.end_page} precedes "
                f"start_page {self.start_page} — a span must name a real range"
            )
        if not self.section.strip():
            raise TemplateError("a span must carry the document's own label")
        if not self.family.strip():
            raise TemplateError(
                f"span {self.section!r}: empty family key — a label that "
                "normalizes to nothing must not become a span at all"
            )

    def covers(self, page_no: int) -> bool:
        return self.start_page <= page_no <= self.end_page

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1


@dataclass(frozen=True, slots=True)
class SectionFamily:
    """One row of a template: a section type, what it costs to lose, and how to
    recognize it.

    Note what is absent: there is no ``disposition``, and there is no
    ``approved_by``. A family describes; it does not decide.
    """

    family_id: str
    """Stable identifier written into every drop-log entry and into
    ``PageRecord.drop_rule``. Renaming one breaks the audit trail between runs,
    so it is validated as an identifier rather than free text — the same rule
    ``SectionRule.rule_id`` has always had, for the same reason."""

    display_name: str
    patterns: tuple[str, ...]
    """Regexes matched against a span's FAMILY KEY, never against raw page text.

    Matching the key rather than the text is what keeps this on the right side
    of §2's exclusion: the key comes from the document's own outline entry, so a
    pattern cannot match a passing mention in a paragraph. The engine D-35
    removed matched raw lines, and that is exactly how a rule for
    ``PROGRESS PHOTOGRAPHS`` came to drop an executive summary."""

    risk: Risk
    rationale: str
    """Why an expert might omit this, and what he loses — rendered on the
    checklist row beside the saving (§5.3). Required: a lever offered without a
    stated cost is a lever that gets clicked."""

    offer: bool = True
    """Whether the checklist offers this family as an omission at all.

    ``False`` means recognize-and-report-only: the family is named in the log
    and never presented as something to drop. That is the right setting for a
    section whose recognition is useful and whose omission never is."""

    def compiled(self) -> tuple[re.Pattern[str], ...]:
        out = []
        for pattern in self.patterns:
            try:
                out.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                raise TemplateError(
                    f"family {self.family_id!r}: pattern {pattern!r} is not a "
                    f"valid regular expression ({exc})"
                ) from exc
        return tuple(out)

    def matches(self, family_key: str) -> bool:
        return any(p.search(family_key) for p in self.compiled())

    def validate(self) -> None:
        if not _ID_RE.match(self.family_id):
            raise TemplateError(
                f"family id {self.family_id!r} must be lower-case letters, "
                "digits, '.', '_' or '-' (max 64 chars) — it is written into "
                "the audit trail and must stay stable"
            )
        if not self.display_name.strip():
            raise TemplateError(f"family {self.family_id!r}: display_name is required")
        if not self.patterns:
            raise TemplateError(
                f"family {self.family_id!r}: at least one pattern is required"
            )
        if not self.rationale.strip():
            raise TemplateError(
                f"family {self.family_id!r}: a rationale is required — §5.3, a "
                "lever offered without a stated cost is a lever that gets "
                "clicked"
            )
        self.compiled()


@dataclass(frozen=True, slots=True)
class SectionTemplate:
    """A shipped, matter-neutral description of what a recurring format contains.

    **Ships unengaged and carries no approver (D-34).** Its contribution to a
    run, until a human engages something, is recognition only: sections named in
    the log, nothing dropped. There is no field here in which a disposition
    could be recorded, and :func:`dociq.sections.apply.plan_omissions` will not
    drop a page without an :class:`ApprovedOmission` naming a person.

    **Never named after a matter (D-24).** ``display_name`` describes a document
    *type* — "Monthly progress report" — never a project, a vessel or a client.
    Measured reason rather than a stylistic one: 30.5% of the real corpus's
    section vocabulary carries project-identifying text, so a template built by
    copying observed labels would ship a client's vessel name inside a Long
    International deliverable.
    """

    template_id: str
    version: str
    display_name: str
    families: tuple[SectionFamily, ...] = ()
    notes: str = ""

    def validate(self) -> None:
        if not _ID_RE.match(self.template_id):
            raise TemplateError(
                f"template_id {self.template_id!r} must be lower-case letters, "
                "digits, '.', '_' or '-' (max 64 chars)"
            )
        if not self.version.strip():
            raise TemplateError(f"{self.template_id}: version is required")
        seen: set[str] = set()
        for family in self.families:
            family.validate()
            if family.family_id in seen:
                raise TemplateError(
                    f"{self.template_id}: duplicate family id "
                    f"{family.family_id!r} — drop attribution would be ambiguous"
                )
            seen.add(family.family_id)

    def family(self, family_id: str) -> SectionFamily | None:
        for candidate in self.families:
            if candidate.family_id == family_id:
                return candidate
        return None

    def classify(self, family_key: str) -> SectionFamily | None:
        """First family whose patterns match, or ``None``.

        Order in the template is meaningful and is the author's: a narrow family
        placed above a broad one behaves the way a reader of the file expects.
        ``None`` means the section is recognized but belongs to no family the
        template knows — which is a KEEP, and is reported rather than silent.
        """
        for family in self.families:
            if family.matches(family_key):
                return family
        return None


@dataclass(frozen=True, slots=True)
class ApprovedOmission:
    """One expert, engaging one lever, on one matter, at one time.

    This record is the whole of D-34. A template proposes; this disposes, and it
    cannot exist without a name in it — :meth:`validate` refuses an empty
    approver, so there is no way to construct a silent drop.

    It is deliberately NOT stored inside the template. The template is a shipped,
    matter-neutral file; this belongs to the matter, is written into the matter
    folder, and names the person who is answerable for the omission.
    """

    family_id: str
    approved_by: str
    """The Windows account of the person who engaged the lever, captured at the
    moment of engagement. Never defaulted, never inferred from the template."""

    approved_at: str
    """ISO-8601 UTC, seconds precision — the same grammar as
    :class:`dociq.profiles.model.OperatorStamp`, and for the same reason: it is
    read by humans and compared between runs."""

    matter: str
    """The matter the approval was given on. An approval is not transferable
    between matters, and recording the matter is what makes that checkable."""

    template_id: str
    template_version: str
    host: str = ""

    def validate(self) -> None:
        if not _ID_RE.match(self.family_id):
            raise TemplateError(
                f"omission: family_id {self.family_id!r} is not a valid id"
            )
        if not self.approved_by.strip():
            raise TemplateError(
                f"omission of {self.family_id!r}: approved_by is empty — "
                "D-34 forbids an omission that names nobody, and a template "
                "cannot supply this because a template approved nothing"
            )
        if not self.approved_at.strip():
            raise TemplateError(
                f"omission of {self.family_id!r}: approved_at is empty"
            )
        if not self.matter.strip():
            raise TemplateError(
                f"omission of {self.family_id!r}: matter is empty — an "
                "approval is not transferable between matters"
            )
        if not self.template_id.strip() or not self.template_version.strip():
            raise TemplateError(
                f"omission of {self.family_id!r}: the template it was given "
                "against must be identified, including its version"
            )

    @property
    def drop_rule(self) -> str:
        """The identifier written into ``PageRecord.drop_rule``.

        Carries the template AND the family, because "which template's
        photo-log family" is a question an expert reading a two-year-old matter
        folder will actually have.
        """
        return f"{self.template_id}:{self.family_id}"
