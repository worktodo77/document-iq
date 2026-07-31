"""Stage 4 — the KEEP/DROP engine (§4, Principle 1).

Two rules govern everything here:

1. **KEEP is the default and needs no justification.** A page that matches no
   rule, sits before any recognized header, or belongs to a document no profile
   claims, is kept. There is no code path that drops a page without a rule,
   and the frozen contract enforces the same thing independently
   (``PageRecord.validate()`` raises on DROP without ``drop_rule``).
2. **Every DROP is attributable.** Each dropped page produces a
   :class:`DropLogEntry` naming the rule, the pattern, the section, and the
   text that matched — enough for someone to reconstruct the decision without
   re-running the tool.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from dociq.contracts import (
    ContractViolation,
    Disposition,
    DocumentRecord,
    PageRecord,
    document_sort_key,
)
from dociq.profiles.model import FormatProfile, SectionRule

__all__ = [
    "DropLogEntry",
    "ApplyResult",
    "apply_profile",
    "apply_profiles",
    "HEADER_SAMPLE_PAGES",
]

HEADER_SAMPLE_PAGES = 3
"""How many leading pages are offered to a profile's header patterns.

A format's identifying header is on its cover or first content page. Scanning
the whole document instead would let a passing mention of "MODEC Monthly
Progress Report" on page 90 of an unrelated letter pull that letter under an
MPR profile — a silent, hard-to-see misclassification.
"""


@dataclass(frozen=True, slots=True)
class DropLogEntry:
    """One page's omission, fully attributed (§7 processing_log)."""

    doc_id: str
    rel_path: str
    page_no: int
    section: str | None
    rule_id: str
    pattern: str
    matched_text: str
    profile_id: str
    profile_version: str
    rule_notes: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyResult:
    documents: tuple[DocumentRecord, ...]
    drops: tuple[DropLogEntry, ...]
    profiled_doc_ids: tuple[str, ...]
    """Documents a profile actually claimed. The complement passed through
    whole (§4 Stage 4), which is a KEEP outcome, not a failure."""

    warnings: tuple[str, ...] = ()

    @property
    def pages_dropped(self) -> int:
        return len(self.drops)


def _match_rule(
    line: str, rules: Sequence[SectionRule]
) -> tuple[SectionRule, str] | None:
    """First rule whose pattern matches wins.

    Order in the profile is therefore meaningful, and it is the expert's order:
    a narrow rule placed above a broad one behaves the way a reader of the YAML
    would expect.
    """
    for rule in rules:
        m = rule.compiled().search(line)
        if m:
            return rule, m.group(0)
    return None


def _header_sample(doc: DocumentRecord) -> str:
    return "\n".join(p.text for p in doc.pages[:HEADER_SAMPLE_PAGES])


def apply_profile(doc: DocumentRecord, profile: FormatProfile) -> ApplyResult:
    """Classify one document's pages against one profile.

    Section state carries forward page to page: a header on page 12 governs
    pages 12 onward until the next header. Pages before the first header have
    no section and are kept — front matter is content until an expert rules
    otherwise.
    """
    profile.validate()
    if not profile.applies_to(_header_sample(doc)):
        return ApplyResult(
            documents=(doc,), drops=(), profiled_doc_ids=(), warnings=()
        )

    rules = profile.section_rules
    pages: list[PageRecord] = []
    drops: list[DropLogEntry] = []
    current: tuple[SectionRule, str] | None = None
    changed = False

    for page in doc.pages:
        for line in page.text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            hit = _match_rule(stripped, rules)
            if hit is not None:
                current = (hit[0], hit[1])
                break

        if current is None:
            pages.append(page)
            continue

        rule, matched_text = current
        section_label = rule.label or matched_text
        if rule.disposition is Disposition.DROP:
            new_page = page.evolve(
                section=section_label,
                disposition=Disposition.DROP,
                drop_rule=rule.rule_id,
            )
            drops.append(
                DropLogEntry(
                    doc_id=doc.doc_id,
                    rel_path=doc.rel_path,
                    page_no=page.page_no,
                    section=section_label,
                    rule_id=rule.rule_id,
                    pattern=rule.pattern,
                    matched_text=matched_text,
                    profile_id=profile.profile_id,
                    profile_version=profile.version,
                    rule_notes=rule.notes,
                )
            )
        else:
            # An explicit KEEP rule still records the section: the index's
            # "sections dropped" column is only meaningful if the sections that
            # were *not* dropped are known too.
            new_page = page.evolve(section=section_label)
        if new_page != page:
            changed = True
        pages.append(new_page)

    out = (
        replace(
            doc,
            pages=tuple(pages),
            profile_id=profile.profile_id,
            profile_version=profile.version,
        )
        if changed or doc.profile_id != profile.profile_id
        else doc
    )
    out.validate()
    return ApplyResult(
        documents=(out,), drops=tuple(drops), profiled_doc_ids=(doc.doc_id,)
    )


def apply_profiles(
    documents: Sequence[DocumentRecord],
    profiles: Sequence[FormatProfile],
) -> ApplyResult:
    """Apply a profile library across a corpus.

    Each document is claimed by at most one profile — the first in the supplied
    order whose header patterns match. Multiple claims are reported rather than
    resolved silently, because two profiles disagreeing about a format is an
    operator problem, not a tie to break in code.
    """
    docs = sorted(documents, key=document_sort_key)
    out: list[DocumentRecord] = []
    drops: list[DropLogEntry] = []
    profiled: list[str] = []
    warnings: list[str] = []

    for doc in docs:
        sample = _header_sample(doc)
        claimants = [p for p in profiles if p.applies_to(sample)]
        if not claimants:
            out.append(doc)
            continue
        if len(claimants) > 1:
            warnings.append(
                f"{doc.rel_path}: profiles "
                f"{[p.profile_id for p in claimants]} all claim this document; "
                f"{claimants[0].profile_id!r} was applied (first in the supplied "
                "order). Narrow the header patterns so the choice is explicit."
            )
        result = apply_profile(doc, claimants[0])
        out.extend(result.documents)
        drops.extend(result.drops)
        profiled.extend(result.profiled_doc_ids)
        warnings.extend(result.warnings)

    if len(out) != len(docs):  # pragma: no cover - guards a future refactor
        raise ContractViolation(
            f"Stage 4 changed the document count: {len(docs)} in, {len(out)} out"
        )
    return ApplyResult(
        documents=tuple(out),
        drops=tuple(drops),
        profiled_doc_ids=tuple(profiled),
        warnings=tuple(warnings),
    )
