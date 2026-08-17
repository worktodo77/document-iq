"""Turning resolved sections into dispositions — the only place a page drops.

Two rules govern everything here, and they are the two the engine this replaces
could not hold:

1. **KEEP is the default and needs no justification.** A page no span covers, a
   page whose section matches no family, and a page whose family carries no
   approval, are all kept. There is no code path that drops a page without an
   :class:`ApprovedOmission` naming a person.

2. **A drop is bounded by the span that caused it.** A span names its last page,
   so an omission of pages 6-7 cannot reach page 8. The engine D-35 removed held
   a carried section with no end, and one rule for `PROGRESS PHOTOGRAPHS` dropped
   an executive summary, a critical path narrative, a weather log and a set of
   timesheets — attributing every one of them to `PROGRESS PHOTOGRAPHS`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from dociq.contracts import (
    Disposition,
    DocumentRecord,
    PageRecord,
    RecognitionTier,
)
from dociq.sections.model import ApprovedOmission, SectionSpan, SectionTemplate
from dociq.sections.resolve import section_for_page

__all__ = ["SectionDropEntry", "SectionApplyResult", "apply_sections"]


@dataclass(frozen=True, slots=True)
class SectionDropEntry:
    """One page's omission, fully attributed (§7 processing_log).

    Carries the **tier** and the **approver** as well as the rule, because §5.4
    requires the kind of evidence and D-34 requires the person. "Dropped because
    the document's own outline placed this page in PROGRESS PHOTOS, omission
    approved by J. Long on 2026-08-17" is a sentence an expert can defend; the
    old log could produce only the first clause, and sometimes produced it
    falsely.
    """

    doc_id: str
    rel_path: str
    page_no: int
    section: str
    family: str
    tier: RecognitionTier
    evidence: str
    family_id: str
    drop_rule: str
    approved_by: str
    approved_at: str
    matter: str
    template_id: str
    template_version: str


@dataclass(frozen=True, slots=True)
class SectionApplyResult:
    documents: tuple[DocumentRecord, ...]
    drops: tuple[SectionDropEntry, ...]
    warnings: tuple[str, ...] = ()

    @property
    def pages_dropped(self) -> int:
        return len(self.drops)


def apply_sections(
    doc: DocumentRecord,
    spans: tuple[SectionSpan, ...],
    *,
    template: SectionTemplate | None = None,
    approvals: tuple[ApprovedOmission, ...] = (),
) -> SectionApplyResult:
    """Stamp sections onto a document's pages, and drop only what is approved.

    ``spans`` come from :func:`dociq.sections.resolve.resolve_sections`.
    ``template`` maps a span's family key to a named family. ``approvals`` are
    the levers a human actually engaged.

    **With no approvals the result is recognition only** — every covered page
    gains a ``section`` and a ``section_tier``, and nothing drops. That is D-34's
    unengaged template, and it is the state a freshly-installed DocIQ is in.
    """
    if template is not None:
        template.validate()
    approved_by_family: dict[str, ApprovedOmission] = {}
    warnings: list[str] = []
    for approval in approvals:
        approval.validate()
        if template is not None and template.family(approval.family_id) is None:
            warnings.append(
                f"omission approved for family {approval.family_id!r}, which "
                f"template {template.template_id!r} v{template.version} does "
                "not define — no page is dropped for it. Reported rather than "
                "ignored: an approval that matches nothing usually means a "
                "template was replaced under a matter that had already ruled."
            )
            continue
        approved_by_family[approval.family_id] = approval

    pages: list[PageRecord] = []
    drops: list[SectionDropEntry] = []
    changed = False

    for page in doc.pages:
        span = section_for_page(spans, page.page_no)
        if span is None:
            pages.append(page)
            continue

        family = template.classify(span.family) if template is not None else None
        approval = (
            approved_by_family.get(family.family_id) if family is not None else None
        )

        if approval is None or family is None or not family.offer:
            # Recognized, not dropped. The section and its tier are still
            # recorded: the index's "sections dropped" column is only
            # meaningful if the sections NOT dropped are known too.
            new_page = page.evolve(section=span.section, section_tier=span.tier)
        else:
            new_page = page.evolve(
                section=span.section,
                section_tier=span.tier,
                disposition=Disposition.DROP,
                drop_rule=approval.drop_rule,
            )
            drops.append(
                SectionDropEntry(
                    doc_id=doc.doc_id,
                    rel_path=doc.rel_path,
                    page_no=page.page_no,
                    section=span.section,
                    family=span.family,
                    tier=span.tier,
                    evidence=span.evidence,
                    family_id=family.family_id,
                    drop_rule=approval.drop_rule,
                    approved_by=approval.approved_by,
                    approved_at=approval.approved_at,
                    matter=approval.matter,
                    template_id=approval.template_id,
                    template_version=approval.template_version,
                )
            )
        new_page.validate()
        if new_page != page:
            changed = True
        pages.append(new_page)

    out = doc if not changed else replace(doc, pages=tuple(pages))
    out.validate()
    return SectionApplyResult(
        documents=(out,), drops=tuple(drops), warnings=tuple(warnings)
    )
