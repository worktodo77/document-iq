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
    matter_key,
    DocumentRecord,
    PageRecord,
    RecognitionTier,
)
from dociq.sections.model import (
    ApprovedOmission,
    SectionSpan,
    SectionTemplate,
    TemplateError,
)
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


def _kept(page: PageRecord, *, section: str | None,
          tier: RecognitionTier | None) -> PageRecord:
    """A page carrying its recognition and NO omission.

    Every field a drop sets is cleared here, rather than only the ones a
    particular caller happened to have set. A KEEP that clears the disposition
    and forgets the ``drop_rule`` would leave the contract's own check to catch
    it (``PageRecord.validate()`` refuses a ``drop_rule`` on a KEEP page) — which
    is a loud failure and still a failure. Clearing both together is what makes
    the pair impossible to separate.
    """
    return page.evolve(
        section=section,
        section_tier=tier,
        disposition=Disposition.KEEP,
        drop_rule=None,
    )


def apply_sections(
    doc: DocumentRecord,
    spans: tuple[SectionSpan, ...],
    *,
    template: SectionTemplate | None = None,
    approvals: tuple[ApprovedOmission, ...] = (),
    matter_root: str = "",
) -> SectionApplyResult:
    """Stamp sections onto a document's pages, and drop only what is approved.

    ``spans`` come from :func:`dociq.sections.resolve.resolve_sections`.
    ``template`` maps a span's family key to a named family. ``approvals`` are
    the levers a human actually engaged.

    **With no approvals the result is recognition only** — every covered page
    gains a ``section`` and a ``section_tier``, and nothing drops. That is D-34's
    unengaged template, and it is the state a freshly-installed DocIQ is in.

    ``matter`` is REQUIRED whenever ``approvals`` is non-empty, and the
    requirement is the fix for a defect Codex reproduced. ``ApprovedOmission``
    records ``matter``, ``template_id`` and ``template_version``, and its own
    contract says an approval is not transferable between matters — but nothing
    compared them, so an approval stamped `Matter-A / old-template v0` dropped a
    page from a different matter under a different template, and the drop log
    recorded `Matter-A` on its face. The record was complete and it proved the
    opposite of authorization.

    A defaulted matter would have been a silent bypass of exactly that check, so
    supplying approvals without one raises instead.
    """
    if template is not None:
        template.validate()
    if approvals and not matter_root.strip():
        raise TemplateError(
            "approvals were supplied without the matter root they were given "
            "on. An approval authorizes an omission on one matter and is not "
            "transferable (D-34); accepting one here with nothing to compare it "
            "against is how a previous matter's ruling drops a later matter's "
            "pages."
        )
    here = matter_key(matter_root) if matter_root.strip() else ""
    approved_by_family: dict[str, ApprovedOmission] = {}
    warnings: list[str] = []
    for approval in approvals:
        approval.validate()
        # SCOPE IS ENFORCED, NOT MERELY RECORDED. Each mismatch is reported and
        # the approval is discarded, so the failure is fail-closed: the pages
        # keep, and an operator is told which ruling did not apply and why.
        # The ROOT decides, not the name. Two clients each with a folder
        # called `Production` are two matters and one name, and comparing the
        # name let the first one's ruling drop the second one's pages.
        if approval.matter_root != here:
            warnings.append(
                f"omission of {approval.family_id!r} was approved on matter "
                f"{approval.matter!r} ({approval.matter_root}) and this run is "
                f"over {here} — it was NOT applied and no page was dropped for "
                "it. An approval is a ruling about one matter's record and does "
                "not carry to another, even one of the same name."
            )
            continue
        if template is not None and (
            approval.template_id != template.template_id
            or approval.template_version != template.version
        ):
            warnings.append(
                f"omission of {approval.family_id!r} was approved against "
                f"template {approval.template_id!r} "
                f"v{approval.template_version} and this run loaded "
                f"{template.template_id!r} v{template.version} — it was NOT "
                "applied and no page was dropped for it. A template version "
                "can change what a family matches, so an approval given "
                "against one is not an approval of the other."
            )
            continue
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
            # No span, so no section, so no approval can reach this page: KEEP,
            # and stated rather than inherited. See the note below — this branch
            # and the recognized-not-approved branch are the same rule.
            pages.append(_kept(page, section=None, tier=None))
            continue

        family = template.classify(span.family) if template is not None else None
        approval = (
            approved_by_family.get(family.family_id) if family is not None else None
        )

        if approval is None or family is None or not family.offer:
            # Recognized, not dropped. The section and its tier are still
            # recorded: the index's "sections dropped" column is only
            # meaningful if the sections NOT dropped are known too.
            #
            # THE DISPOSITION IS SET, NOT LEFT ALONE, and that is a fix rather
            # than a flourish. Leaving it inherited made this function wrong
            # under the one sequence the product invites: engage a lever, run,
            # change your mind, run again. The second run finds no approval,
            # took this branch, and left the page DROP carrying the drop_rule of
            # an approval that no longer exists — while `drops` is empty, so the
            # page is dropped and NOTHING in the log accounts for it. That is
            # the unattributable drop Principle 1 forbids, arrived at by
            # withdrawal rather than by omission.
            #
            # Reproduced before it was fixed: approve, apply, withdraw, apply —
            # both pages stayed dropped. It is not reachable through today's
            # pipeline, because Stage 4 only ever sees fresh or
            # resumed-before-Stage-4 records, and it is reachable through this
            # function, which is public and is the only thing that may drop a
            # page.
            new_page = _kept(page, section=span.section, tier=span.tier)
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
