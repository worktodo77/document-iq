"""Section recognition — the tiers that decide which section a page belongs to.

This package replaces the heading-regex matcher that `profiles/apply.py` shipped
through Sprint 2 (D-35). The difference is not the accuracy of the matching, it
is the *shape of the answer*: a tier returns page **spans** with a start and an
end, where the old engine returned a carried state that never had to end. A span
that names pages 6-7 cannot drop page 8; a carried section could and did.

Tiers, strongest evidence first (`docs/design/section_taxonomy.md` §3):

===== =========================================== ==========================
tier  evidence                                    measured reach
===== =========================================== ==========================
 1    the document's own PDF outline              63.0% of corpus pages
 2    the document's own table of contents        NOT BUILT — see below
 3    measurable page-class rules                 +7.4% of corpus pages
 4    page ranges entered by the expert           NOT BUILT — see below
===== =========================================== ==========================

**Tiers 2 and 4 are deliberately absent, not forgotten.** Sprint 3's scope was
ruled to Tiers 1 and 3 on the measurement in
`docs/verification/sections_2026-08-17.md`: Tier 2's offset check can only be
validated against documents that carry an outline, which is precisely the
population that does not need Tier 2, so building it would ship a tier proven
only where it is unnecessary. Tier 4 is the strongest evidence of all and the
least scalable, and it buys nothing until an operator has somewhere to type a
range.

Everything here obeys the taxonomy's §1 asymmetry, which is the reason the old
engine had to go: **a section the recognizer misses is a page that survives; a
section it invents is a page that vanishes.** A page no tier resolves has no
section, and a page with no section is KEEP by contract. On the real corpus that
is 29.6% of pages, and that is a correct outcome rather than a coverage failure.
"""

from dociq.sections.model import (
    ApprovedOmission,
    SectionFamily,
    SectionSpan,
    SectionTemplate,
)
from dociq.sections.normalize import (
    family_key,
    normalize_label,
    strip_numbering,
    strip_project_tokens,
)
from dociq.sections.resolve import resolve_sections

__all__ = [
    "ApprovedOmission",
    "SectionFamily",
    "SectionSpan",
    "SectionTemplate",
    "family_key",
    "normalize_label",
    "resolve_sections",
    "strip_numbering",
    "strip_project_tokens",
]
