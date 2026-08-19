"""Proposing a matter's project tokens — and being clear that it is a proposal.

`section_taxonomy.md` §2 measured what an unmeasured heading heuristic does on
this material: it returned `FPSO ALMIRANTE BARROSO MV32` (1,017) and `PETROBRAS`
(981) at the top of its frequency list, and the tier was struck out on that
evidence. D-39 rules that DocIQ derives these tokens rather than asking for them,
and the same measurement discipline was applied to the derivation before it was
built. Four candidate rules were run over the real corpus:

===================== ==========================================================
rule                  what it returned
===================== ==========================================================
by frequency          PROGRESS, ENGINEERING, TOPSIDE, PROCUREMENT, MARINE —
                      section words. §2's failure, reproduced.
not in the template   barely different: the template names 19 families and the
                      corpus has 522, so most real section words are "unknown"
in the FILENAMES      MV32, MI20, T1R1 — and REV, 001, WEEKLY
===================== ==========================================================

**No threshold separates them.** Raising the filename bar to catch `MI20` (24)
and `T1R1` (33) drops `MV32` (5), the most important token in the corpus;
lowering it to keep `MV32` admits `TOPSIDE`. And `BOMESC` and `YARD` — the two
highest-frequency project-tokened labels, 48 occurrences each — appear in NO
filename, so this rule cannot find them at all.

**So this proposes; it does not decide.** The list reaches the operator, who
strikes out what is wrong and adds what was missed, and the edited list is what
the run records. That is D-39's second half and it is what makes an unreliable
derivation safe to ship.

**The cost of being wrong is bounded, and in the right direction.** Stripping a
token can only make a section MATCH a template family, and under D-34 a match is
an OFFER, never a drop. A wrong token costs an offer; it cannot lose a page.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

__all__ = ["propose_tokens", "canonical_tokens", "DOCUMENT_LIFECYCLE_WORDS"]

# Re-exported, not defined here: the contract normalizes its own field, and the
# contract may not import this package (see `RunConfig.project_tokens`). Kept
# importable from the module that owns the vocabulary so callers reading about
# tokens find it where they look.
from dociq.contracts import canonical_tokens  # noqa: E402

_WORD = re.compile(r"[A-Za-z0-9]{2,}")

DOCUMENT_LIFECYCLE_WORDS = frozenset({
    "REV", "REVISION", "VERSION", "DRAFT", "FINAL", "ISSUE", "ISSUED",
    "COPY", "REPORT", "REPORTS", "MONTHLY", "WEEKLY", "DAILY", "ANNUAL",
    "PROGRESS", "APPENDIX", "APPENDICES", "ATTACHMENT", "DOC", "DOCUMENT",
    "PDF", "PAGE", "PAGES", "PART", "VOL", "VOLUME", "SECTION",
})
"""Words that describe a DOCUMENT rather than a project.

They come out of the proposal because the measurement showed them arriving in
it: `REV` (21 filenames), `001` and `WEEKLY` (11 each) all score exactly like a
project identifier under a filename rule.

**Generic English, and deliberately so.** D-24 forbids shipping anything
attributable to a corpus project; this list names no project, no vessel, no
client and no yard, and would read the same way for a matter DocIQ has never
seen. It is the complement of the thing D-24 bars.
"""

_STOPWORDS = frozenset({
    "OF", "AND", "THE", "TO", "FOR", "IN", "ON", "AT", "BY", "AN", "A",
    # Portuguese: this corpus is Brazilian and a stopword list that assumed
    # English would propose `DE` and `DA` as project identifiers.
    "DE", "DA", "DO", "DOS", "DAS", "E", "O", "AS", "OS", "NO", "NA", "EM",
    "POR", "COM", "PARA",
})


def _is_noise(token: str) -> bool:
    """Words that cannot be a project identifier, whatever they score.

    ONE predicate rather than the same three conditions written into each of the
    two loops below. Written twice, a mutation of either copy leaves the other
    still filtering, so a test can pass without exercising the guard it names —
    which is exactly how the first draft of this module's tests read green.
    """
    return (
        token in _STOPWORDS
        or token in DOCUMENT_LIFECYCLE_WORDS
        # `001` recurs like an identifier and identifies nothing.
        or token.isdigit()
    )


def propose_tokens(
    labels_by_document: dict[str, list[str]],
    filenames: list[str],
    *,
    min_filename_hits: int = 2,
    min_documents: int = 2,
) -> tuple[str, ...]:
    """Candidate project tokens for one matter, strongest signal first.

    ``labels_by_document`` maps a document's name to the section labels its own
    outline declares; ``filenames`` are the corpus's file stems.

    **The signal is appearing in BOTH.** A project identifier names the document
    set as well as appearing inside its section labels — `MV32` is in the
    filenames, `SCHEDULE` is not. It is the least bad of four measured rules and
    it is still wrong often enough that the result must be shown and edited.

    **Deterministic**, because the result is a hashed run input (A-19). Output is
    ordered by a total key — label frequency, then document spread, then the
    token itself — so two runs over one folder propose the same list in the same
    order regardless of the order the filesystem happened to yield.
    """
    token_labels: Counter[str] = Counter()
    token_docs: defaultdict[str, set[str]] = defaultdict(set)
    for doc_name, labels in labels_by_document.items():
        for label in labels:
            for tok in _WORD.findall(label.upper()):
                if _is_noise(tok):
                    continue
                token_labels[tok] += 1
                token_docs[tok].add(doc_name)

    in_filenames: Counter[str] = Counter()
    for name in filenames:
        for tok in _WORD.findall(name.upper()):
            if _is_noise(tok):
                continue
            in_filenames[tok] += 1

    candidates = [
        tok for tok, hits in token_labels.items()
        if in_filenames.get(tok, 0) >= min_filename_hits
        and len(token_docs[tok]) >= min_documents
    ]
    return tuple(sorted(
        candidates,
        key=lambda t: (-token_labels[t], -len(token_docs[t]), t),
    ))

