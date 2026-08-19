"""FROZEN pipeline contract for LI Document IQ.

This module is the cross-track contract frozen on day one of Sprint 1 per the
D-10 contract-first rule. Tracks A (ingestion spine), B (identity and
deliverables) and C (GUI shell) all build against these types in separate
worktrees; only Track A implements them for real.

**Any change to a type in this module after the freeze is a stop-the-line
event across all three tracks, not a local edit.** Additive changes with a
safe default are permitted only via the amendment procedure in
``docs/contracts/pagemodel_freeze.md``.

Nothing here imports a third-party library, a GUI toolkit, or an OCR engine.
The contract must stay importable in a bare interpreter so every track can
depend on it without inheriting another track's dependency set.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Sequence

CONTRACT_VERSION = "2.2.0"
"""Frozen 2026-07-30 at 1.0.0. Bumped only by the amendment procedure.

1.1.0 — amendments A-01 and A-02, raised by Track C under the stop-the-line
rule and applied centrally: :class:`RunResult` gained ``tokens_before``,
``tokens_after`` and ``reconciliation``, all defaulted to ``None``. Additive
with safe defaults, so no existing construction site changes.

1.2.0 — amendment A-03: :class:`TokenEstimate` gained ``ratio_refuted``,
defaulted to ``False``. Raised once the corpus measurement showed D-03's ratio
band to be unreachable rather than merely optimistic, leaving consumers with no
sanctioned way to know the band was not used. *(Codex review #1 finding B-6
later showed that refutation is not established — the flag stays, but what
sets it must be re-grounded.)*

1.3.0 — amendment A-04, from Codex review #1 finding B-2: :class:`RunConfig`
gained ``limits: EffectiveLimits | None``. Environment-controlled caps,
timeouts, retry bounds and the OCR model identity could change output evidence
while sitting outside the hashed configuration — a determinism-identity gap by
:class:`RunConfig`'s own stated contract.

1.4.0 — amendment A-05(a), from Codex review #1 finding B-6:
:class:`TokenEstimate` gained ``structural_tokens`` and ``token_ceiling``, and
``floor_tokens`` is **reserved and withdrawn** — the hard-lower-bound claim it
documented does not hold. A-05(b) (a `EffectiveLimits` field for the
disk-headroom multiplier) is dispositioned as NOT NEEDED: it gates whether a
run *starts* rather than what a completed run emits, an abort is B-1's typed
terminal status rather than differing evidence, and it is a float, which
Principle 5 bars from identity. It is recorded unhashed.

1.5.0 — amendment A-06, from Codex review #1 finding B-1: :class:`RunResult`
gained ``terminal_status`` and ``terminal_status_reason``, defaulted to
COMPLETED so the change is additive.

Hashed as landed — **reversed at 1.6.0 by A-07**, see below. The reasoning
recorded here ("a cancelled partial set and a complete set must not hash
identically") assumed the two sets could ever be compared. They cannot: an
incomplete run publishes no corpus and no manifest.

1.6.0 — amendments A-07, A-08, A-09 and A-10, from Codex review #1 round 2
(findings B-R2-1, B-R2-2, B-R2-3).

*A-07 (from B-R2-1).* Two changes, and they pull in opposite directions on
purpose.

First, :class:`TerminalStatus` is now the **only** definition of that
enumeration. :mod:`dociq.runstate` declared a second, value-identical class, so
the walk carried one type while :class:`RunResult` declared the other; an ``is``
comparison across that seam answered ``False`` about two statuses that were the
same status. ``runstate`` re-exports this one. Every returned ``RunResult`` now
actually carries the status and reason of the run that produced it — the
1.5.0 default meant every abort path silently reported COMPLETED, which is the
defect B-R2-1 is about.

Second, **both terminal fields move into** :data:`_IDENTITY_EXCLUDED`,
reversing 1.5.0's decision. Codex's second opinion is adopted, and it is right:
termination is a property of an *invocation*, not of a corpus, and an
incomplete run publishes no corpus and no corpus manifest — so it cannot
collide with a completed corpus hash. The previous completed manifest simply
survives. Calling invocation termination part of a corpus identity for a corpus
that was never published makes the determinism claim describe something other
than the bytes it covers. If the failed attempt ever needs a verifiable
identity of its own, that is a separate attempt identity over the
``incomplete_run/`` audit record, not a term in this one.

*A-08 (from B-R2-2).* :class:`RunConfig` gains
``profiles: tuple[ProfileSnapshot, ...]`` — the **ordered** set of profile
snapshots, in precedence order, each carrying ``profile_id``, ``version`` and
``profile_hash``.

The configuration recorded only ``opts.profiles[0].profile_id`` and its
version, and that is not the input the run actually used.
``profiles/apply.py`` applied the FIRST profile whose header patterns claimed a
document, so both the content of every profile and their precedence order
changed which pages drop — and therefore ``clean_text``,
the index, the sources map and the corpus hash. Measured, with no attacker
model needed: editing a second profile's rule without bumping its version, and
separately swapping two profiles' precedence with no content change at all,
each left the run identity byte-identical while the corpus hash moved.

Snapshots rather than the profiles themselves, for the same reason
:class:`MasterIndexSnapshot` exists: the identity needs an immutable
fingerprint of the input, not the input. ``profile_hash`` is
``FormatProfile.profile_hash`` (deleted with the profile system by D-38), which existed
and which the processing log already writes into hashed content — the fact that
profile *content* is evidence-affecting was established; it was just missing
from the projection the manifest calls the run identity. Hashing the content
also removes the reliance on version immutability, which nothing enforces.

*A-08 also removes* ``output_root`` from the identity projection. Three
statements were in conflict: ``RunConfig`` hashed the destination, the
manifest's ``claim_identity`` said the output folder was part of the run
identity, and both :mod:`dociq.emit.log` and the criterion-7 harness treated it
as irrelevant — the harness deliberately runs to two different destinations and
requires one identity. The destination is where evidence is written, not an
input that changes it, so it leaves the projection and the manifest's
description is corrected to match. :func:`run_identity` is the single
authoritative projection, and it is persisted, so "which hash is the run
identity" has one answer on disk.

*A-09 (from round-2, ours).* ``EffectiveLimits.file_timeout_s`` and
``retry_budget_s`` are renamed ``file_timeout_ms`` / ``retry_budget_ms`` and now
carry integer **milliseconds**. Both are sourced from float-valued deadlines and
were rounded to whole seconds, so 1.1 s and 1.4 s recorded the identical
identity while abandoning different files — a determinism-identity collision
inside the field added to close one. Milliseconds keep the capability (the
deadlines stay float-precise where they are enforced) and remove the collision;
Principle 5's bar on floats in identity fields is honored because the recorded
value is an exact integer.

This is a **renaming** amendment, not an additive one: a consumer reading
``limits.file_timeout_s`` breaks loudly rather than silently reading a second
field. That is deliberate under the freeze procedure — the semantics changed,
so the name had to.

*A-10 (from B-R2-3).* No type change. ``structural_tokens`` and
``token_ceiling``, added by 1.4.0, are now actually populated by the pipeline's
projection; they had stayed at their "not measured" defaults while the same run
wrote both numbers into the processing log. Recorded here because the machine
contract's agreement with the log is a contract property, not an implementation
detail — and because the consequence is not cosmetic. When ``ratio_refuted`` is
true the contract says the ruled band was NOT the method used, yet a consumer
holding only :attr:`RunResult.tokens_before` received zeros and could fall back
to the ruled ratio, displaying a number the pipeline expressly disclaims.

1.7.0 — amendment A-18, from D-35 and the Sprint-3 taxonomy build.
:class:`PageRecord` gains ``section_tier``, and :class:`RecognitionTier` is
added to carry it. `docs/design/section_taxonomy.md` §5.4 required the
recognition tier per page from the day it was written and there was nowhere to
put it, so the strength of the evidence behind an omission was recorded
nowhere: "the document's own outline placed this page in PROGRESS PHOTOS" and
"a photo-page rule matched it" reached the operator as the same sentence.

Additive with a safe default (``None``), so no existing construction site is
obliged to change — but three validation rules land with it, and the third is
not merely additive: **a DROP page must now carry a tier.** That is deliberate.
Principle 1 already makes an unattributable drop impossible; without this an
unattributable *tier* stayed possible, and a guarantee that is optional is one
that regresses silently. The rule is what makes §5.4 correct-by-construction
rather than a docstring.

``section_tier`` is a hashed identity input, unlike ``ocr_conf``: it is a
deterministic property of the document and the tier that read it, carries no
float, and a run that recognized a page by a different tier genuinely produced
different evidence.

1.8.0 — amendment A-19, from D-34 and D-35 as they were wired. :class:`RunConfig`
gains ``omissions: tuple[OmissionSnapshot, ...]``, ``project_tokens``,
``section_template_id`` and ``section_template_version``; :class:`OmissionSnapshot`
is added to carry the first.

**This is A-08's finding on the input that replaced the one A-08 was about.**
A-08 put profiles in the run identity because they decided which pages dropped,
and proved it with two measured counterexamples. D-35 deletes that engine and
D-34 moves the decision to an approval a person gives against a template family,
so approvals are now the deciding input — and until this amendment they sat
outside the identity exactly as profiles once did. Two runs over one folder,
identical in every recorded term, one of them missing a section: the same
collision, one design generation later.

``project_tokens`` is in for the same reason and is easier to miss. It changes
which family a label normalizes to, so supplying `MV32` makes `MV32 APPENDICES`
match an appendices rule and withholding it keeps the page. That is a different
corpus from the same folder and the same approvals.

The template id and version are recorded even when no approval was given,
because "the expert engaged nothing" and "no template was offered" are different
facts about a run and only one of them is a decision.

Additive with safe defaults throughout: an empty approval set is the state of
every freshly-installed DocIQ and of every run nobody has ruled on.

2.0.0 — amendment A-21, from D-38. **The first MAJOR bump, and the first
REMOVAL from the frozen contract.** :class:`ProfileSnapshot` is deleted;
:class:`RunConfig` loses ``profiles``, ``profile_id`` and ``profile_version``;
:class:`DocumentRecord` loses ``profile_id`` and ``profile_version``.

Every previous amendment was additive with a safe default, which is why every
previous bump was MINOR. This one takes fields away, so a caller reading
``config.profiles`` breaks loudly rather than silently reading something else —
and that is the point. The profile system stopped deciding anything when D-35
deleted its engine, and Alex ruled it removed rather than carried
(D-38), with the consequence accepted on the record: **matter folders written
before this bump recorded a run identity computed WITH a profile snapshot in it,
and will not reproduce byte-for-byte afterwards.**

What replaced it is already here: sections recognise, a template names families,
and an :class:`OmissionSnapshot` naming a person decides what drops (A-19).

2.1.0 — amendment A-22, from Codex's Sprint-4 review, finding B-1.
:class:`OmissionSnapshot` gains ``project_tokens``: the canonical project names
the approval was REVIEWED against. Additive with a safe default, so MINOR.

An approval was already refused across matters and across template versions, the
latter because "a template version can change what a family matches, so an
approval given against one is not an approval of the other." Project tokens have
that same power, through the same function, and were not checked — so an
approval retained for the next run of a matter silently widened when the
operator corrected the token list, which is the correction D-39 exists to
invite. Measured: 0 pages dropped before the edit, 1 after, no new approval.
Hashed like every other field of the snapshot, because two approvals reviewed
under different token sets are two configurations.

2.2.0 — amendment A-23, from Alex's ruling of 2026-08-19 after the B-1 sibling
hunt. :class:`OmissionSnapshot` gains ``recognition``: a fingerprint of
everything that decides which family a page lands in. Additive with a safe
default, so MINOR.

A-22 scoped an approval to project tokens. The hunt then found a second
component of the same configuration — whether OCR ran, since a photographed
schedule table classifies as a photograph page unread and as a schedule table
once OCR recovers its grid, so an unchanged approval for progress photographs
drops it in one run and keeps it in the other. Two found one at a time is the
signal: scoping component-by-component leaves the next input to whoever
remembers. The fingerprint covers them all and covers what is added next.

The named fields stay, because they are what the operator's warning says out
loud; "the recognition fingerprint changed" is the wrong half of A-R2-1's
lesson. The fingerprint decides, the named fields explain. An empty fingerprint
means "not recorded" — every approval given before this field existed — and
falls back to the named fields, so an old approval is neither silently widened
nor silently voided.

1.9.0 — amendment A-19, extended, from Codex review r2's finding B-2. :class:`OmissionSnapshot`
gains ``matter_root`` and :func:`matter_key` is added.

The first version scoped an approval by the matter's NAME, and a name is not an
identity: `C:/Client-A/Production` and `D:/Client-B/Production` are one string,
so the first client's ruling survived into the second client's run and dropped
its pages. **The error was deriving a scope key from a display string** — one
answers "what should an expert read", the other "are these the same matter" —
and the fix separates them and gives the second exactly one derivation, shared
by the capture point and by Stage 4.
"""


# ---------------------------------------------------------------------------
# Enumerations
#
# Values are the strings that reach disk (processing_log.json, the index CSV).
# They are part of the determinism contract: renaming one changes output bytes.
# ---------------------------------------------------------------------------


class PageKind(str, enum.Enum):
    """How a page's text was obtained.

    Recorded per page because §3 requires mixed native/scanned PDFs to be
    handled page-by-page, and because the run summary reports OCR exposure.
    """

    NATIVE = "native"
    """Extracted from the document's own text layer."""

    OCR = "ocr"
    """Rasterized and read by the local OCR engine."""

    EMPTY = "empty"
    """The page exists and carries no recoverable text. It is still a page:
    Principle 1 requires it to be accounted for, and the page marker is still
    emitted so downstream page numbers stay aligned with the physical
    document."""

    PHOTO = "photo"
    """An image-based page carrying a deterministic ``[PHOTO]`` block (EXIF
    date/GPS) rather than read text. Never AI-captioned in DocIQ."""

    SYNTHETIC = "synthetic"
    """The source format has no physical pagination (DOCX, EML, MSG, XLSX,
    CSV, TXT). ``page_no`` is an approximation and MUST be reported as such in
    the log, per §3's "page approximation noted in log"."""


class Disposition(str, enum.Enum):
    """Stage-4 section classification outcome for a page.

    Default is KEEP everywhere, unconditionally. Principle 1: unclassified
    content is kept; only an omission an expert approved by name may set DROP
    (D-34). The engine that let a PROFILE RULE set it is deleted (D-35).
    """

    KEEP = "keep"
    DROP = "drop"


class RecognitionTier(str, enum.Enum):
    """How a page's section was recognized (amendment A-18).

    `docs/design/section_taxonomy.md` §5.4: *"Recognition tier belongs in the
    log, per page."* The tiers are **not equally strong**, and presenting them
    identically is what that section calls the quiet lie in this feature —
    "dropped because the document's own outline placed this page in PROGRESS
    PHOTOS" and "dropped because the page matched a photo-page rule" are
    different claims, and an expert defending an omission needs to know which
    one he is making.

    Ordered strongest first. The values are stable strings written into
    `processing_log.json` and must not be renamed: they are the audit trail
    between runs, exactly as ``SectionRule.rule_id`` is.
    """

    OUTLINE = "t1_outline"
    """The document's own PDF outline. A lookup, not an inference — evidence
    the document makes about itself. Measured reach: 63.01% of corpus pages."""

    TOC = "t2_toc"
    """The document's own table of contents, parsed. Its own statement, but
    recovered by parsing. **Not built in Sprint 3** — declared here so the log
    vocabulary is stable when it is."""

    PAGE_CLASS = "t3_page_class"
    """A measurable property of the page (activity-grid headers, image area,
    line recurrence). Deterministic and inspectable, but a *class* rule rather
    than a section boundary. Measured reach: +7.38% of corpus pages."""

    EXPLICIT = "t4_explicit"
    """A page range entered by the expert. Strongest of all and least scalable.
    **Not built in Sprint 3.**"""


class TerminalStatus(str, enum.Enum):
    """How a run ended (amendment A-06, from Codex review #1 finding B-1).

    A run that was blocked before it started, or cancelled part-way, is not a
    run that produced a corpus — and before this existed the pipeline could not
    tell the difference. An empty blocked run satisfied zero-equals-zero page
    accounting, reported success, and replaced a complete prior output set.
    Measured on the fixture corpus: 34 of 44 files destroyed, 10 overwritten,
    ``ok=True``.

    **This is the only definition** (amendment A-07). :mod:`dociq.runstate`
    re-exports it and no longer declares its own.
    """

    COMPLETED = "completed"
    BLOCKED = "blocked"
    """The run never established a corpus it could publish.

    Three ways in: the disk preflight refused, the source root was not
    reachable, or — added by A-07 — the **inventory could not be enumerated**,
    because ``iterdir()`` failed on the root or on a subtree.

    That third one is the subtle one, and it is the round-2 F-2 finding.
    DocIQ's claim over a folder is a completeness claim, and a directory it
    could not list is a directory whose contents it has not established. A
    warning does not repair that: the run would go on to publish an inventory
    it knows to be short by an unknown amount, over the top of a previous
    complete one.

    Distinct from a source folder that was **successfully enumerated** and
    contains no files, which is a legitimate completed run and may replace
    prior deliverables. "Successfully enumerated" is the whole boundary."""

    CANCELLED = "cancelled"
    """Stopped part-way by the operator; documents gathered so far are real but
    the set is partial."""

    REFUSED = "refused"
    """The run produced a complete corpus and **§4 Stage 6 refused to publish
    it** (amendment A-15, from Codex review #2 finding B-1).

    A distinct member rather than a fourth way into :attr:`BLOCKED`, and the
    difference is not bookkeeping. A blocked run never established a corpus —
    the preflight refused, the root was unreachable, the inventory could not be
    enumerated. A refused run established one, assigned an identifier to every
    document, and then failed its own gate: page accounting did not reconcile,
    or the manifest carried an output it could not classify. Those are opposite
    facts about the same folder, and an operator reading "the run never
    established a corpus" about a run that issued 368 Doc IDs would be reading
    something false.

    Correct by construction on both derived properties: :attr:`complete` and
    ``publishable`` are defined against ``COMPLETED``, so a new member is
    unpublishable and incomplete without either being restated. That is the
    whole reason a member was preferred to a flag — the alternative, leaving
    the termination ``COMPLETED``, would print *"Run status: completed — the
    walk covered every file found"* at the top of a refused run's
    ``run_status.json``."""

    @property
    def complete(self) -> bool:
        return self is TerminalStatus.COMPLETED


class ProcessingStatus(str, enum.Enum):
    """Per-document outcome, surfaced in the index deliverable (§5)."""

    FULL = "full"
    PARTIAL_OCR_FLAGGED = "partial-ocr-flagged"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class IdRegime(str, enum.Enum):
    """Which Doc ID scheme a run used (D-04). Recorded in processing_log."""

    MASTER_INDEX = "master-index"
    """A master index was supplied; matched files carry ``LI-`` IDs."""

    NATIVE = "native"
    """No master index; every ID is ``DIQ-`` synthetic."""


# ---------------------------------------------------------------------------
# The per-page record — the core of the freeze
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageRecord:
    """One page of one source document.

    Frozen and slotted: pages are produced in bulk and must not be mutated in
    place by a later stage. Stages 3/3b/4 enrich pages by building new records
    (see :meth:`evolve`), which keeps the pipeline's data flow inspectable and
    makes an accounting discrepancy attributable to a stage.

    Field ordering here is also the canonical serialization order — see
    :func:`canonical_json`.
    """

    page_no: int
    """1-based page number **of the original native document**, never of the
    reduced output (Principle 2). For SYNTHETIC pages this is the approximated
    ordinal within the source."""

    text: str
    """The page's extracted text, already normalized (see
    ``docs/contracts/pagemodel_freeze.md`` §Normalization). Never contains a
    page marker: markers are rendered at the emit layer only."""

    kind: PageKind

    ocr_conf: float | None = None
    """Mean per-line OCR confidence in ``[0.0, 1.0]``, rounded to 4 decimal
    places, or ``None`` when :attr:`kind` is not OCR.

    Rounded because it reaches disk and floats must not destabilize the
    byte-identical contract. It is a *reporting* field only and must never be
    used as an identity/hash input (Principle 5)."""

    ocr_line_count: int = 0
    """Number of text lines the OCR engine returned for this page."""

    ocr_low_conf_lines: int = 0
    """Lines below the run's confidence threshold. Drives the §4 Stage-2
    review flag together with :attr:`ocr_conf`."""

    bates: str | None = None
    """Bates number detected on this page (Stage 3), e.g. ``"MNFV 000391"``.
    ``None`` means not detected. Absence is normal, not an error (§4 Stage 3);
    an un-Bates-stamped matter yields ``None`` on every page."""

    section: str | None = None
    """Section header this page falls under, as resolved by Stage 4. ``None``
    when no tier resolved the page — which means KEEP. On the real corpus that
    is 29.6% of pages, and it is a correct outcome rather than a coverage
    failure (§1: a section the recognizer misses is a page that survives)."""

    section_tier: "RecognitionTier | None" = None
    """WHICH KIND OF EVIDENCE placed this page in :attr:`section` (amendment
    A-18, for §5.4).

    ``None`` exactly when :attr:`section` is ``None``, and required whenever
    the page is DROP — both enforced by :meth:`validate`. The second rule is
    the load-bearing one: it makes an unattributable *tier* as impossible as
    Principle 1 already makes an unattributable *drop*, so the strength of the
    evidence behind an omission cannot silently stop being recorded."""

    disposition: Disposition = Disposition.KEEP
    """KEEP unless an omission an expert approved by name dropped it. Defaulted so
    that any code path that forgets to classify still keeps the page."""

    drop_rule: str | None = None
    """Identifier of the approved omission that set DROP, for the per-drop log
    entry — ``template_id:family_id``
    (:attr:`dociq.sections.model.ApprovedOmission.drop_rule`). It named a
    profile rule until D-35 deleted the engine that applied one. MUST be non-None whenever :attr:`disposition` is DROP — enforced by
    :meth:`validate`. Principle 1 forbids an unattributable drop."""

    notes: tuple[str, ...] = ()
    """Disclosed degradation markers for this page (truncation, undecodable
    region, OCR failure). Disclosure, never silence."""

    def evolve(self, **changes: object) -> "PageRecord":
        """Return a copy with fields replaced. The only sanctioned way for a
        later stage to enrich a page."""
        return replace(self, **changes)  # type: ignore[arg-type]

    def validate(self) -> None:
        """Raise :class:`ContractViolation` if this record is internally
        inconsistent. Cheap; called at every stage boundary."""
        if self.page_no < 1:
            raise ContractViolation(f"page_no must be 1-based, got {self.page_no}")
        if self.kind is PageKind.OCR:
            if self.ocr_conf is None:
                raise ContractViolation(
                    f"page {self.page_no}: OCR page must carry ocr_conf"
                )
        elif self.ocr_conf is not None:
            raise ContractViolation(
                f"page {self.page_no}: ocr_conf set on non-OCR page ({self.kind.value})"
            )
        if self.ocr_conf is not None and not (0.0 <= self.ocr_conf <= 1.0):
            raise ContractViolation(
                f"page {self.page_no}: ocr_conf {self.ocr_conf} outside [0,1]"
            )
        if self.ocr_low_conf_lines > self.ocr_line_count:
            raise ContractViolation(
                f"page {self.page_no}: more low-confidence lines than lines"
            )
        if self.disposition is Disposition.DROP and not self.drop_rule:
            raise ContractViolation(
                f"page {self.page_no}: DROP without a drop_rule — "
                "Principle 1 forbids an unattributable drop"
            )
        if self.disposition is Disposition.KEEP and self.drop_rule:
            raise ContractViolation(
                f"page {self.page_no}: drop_rule set on a KEEP page"
            )
        if self.section_tier is not None and self.section is None:
            raise ContractViolation(
                f"page {self.page_no}: section_tier "
                f"{self.section_tier.value!r} without a section — a tier "
                "records HOW a section was recognized, and there is none here"
            )
        if self.section is not None and self.section_tier is None:
            raise ContractViolation(
                f"page {self.page_no}: section {self.section!r} without a "
                "section_tier — §5.4 requires the kind of evidence to be "
                "recorded per page, because the tiers are not equally strong"
            )
        if self.disposition is Disposition.DROP and self.section_tier is None:
            raise ContractViolation(
                f"page {self.page_no}: DROP without a section_tier — an "
                "expert defending this omission must be able to say whether "
                "the document's own outline placed the page or a page-class "
                "rule matched it"
            )


# ---------------------------------------------------------------------------
# The per-document record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """One source file and every page recovered from it."""

    doc_id: str
    """Assigned at Stage 3b (``LI-06881``, ``LI-06881.01``, ``DIQ-000123``).
    Empty string before Stage 3b has run."""

    rel_path: str
    """Path relative to the scanned root, with ``/`` separators, NFC-normalized.
    The primary master-index match key (§5) and the primary sort key."""

    filename: str
    sha256: str
    size_bytes: int
    ext: str
    """Lowercased, including the dot (``".pdf"``)."""

    pages: tuple[PageRecord, ...] = ()
    status: ProcessingStatus = ProcessingStatus.FULL

    parent_doc_id: str | None = None
    """Set for archive members and email attachments (§5). Container children
    have no master-index row and take parent-derived IDs (D-04)."""

    container_order: int | None = None
    """0-based position within the parent container, in archive member order.
    Makes child ID assignment deterministic (D-04)."""

    detected_dates: tuple[str, ...] = ()
    """ISO-8601 dates found in the document, in first-appearance order."""

    doc_type: str | None = None
    """From a filename pattern. Never inferred by AI.

    Read "from the active profile or a filename pattern" until D-38 deleted the
    profile system; no profile ever set it."""

    li_file_no: str | None = None
    """The master index's "Original Sort" value when matched (§5), else None."""

    notes: tuple[str, ...] = ()
    """Document-level disclosed degradation (content-sniff recovery, member
    cap hit, extractor fallback)."""

    error: str | None = None
    """Actionable message when :attr:`status` is FAILED or UNSUPPORTED."""

    # -- derived accounting -------------------------------------------------

    @property
    def pages_in(self) -> int:
        return len(self.pages)

    @property
    def pages_kept(self) -> int:
        return sum(1 for p in self.pages if p.disposition is Disposition.KEEP)

    @property
    def pages_dropped(self) -> int:
        return sum(1 for p in self.pages if p.disposition is Disposition.DROP)

    def validate(self) -> None:
        """Structural + accounting check for this document.

        This is the per-document half of the §4 Stage-6 zero-discrepancy gate;
        :mod:`dociq.verify.accounting` runs the corpus-wide half.
        """
        for p in self.pages:
            p.validate()
        expected = list(range(1, len(self.pages) + 1))
        actual = [p.page_no for p in self.pages]
        if actual != expected:
            raise ContractViolation(
                f"{self.rel_path}: page numbers must be a gapless 1..N sequence "
                f"in order; got {actual[:8]}{'...' if len(actual) > 8 else ''}"
            )
        if self.pages_kept + self.pages_dropped != self.pages_in:
            raise ContractViolation(
                f"{self.rel_path}: page accounting broken — "
                f"{self.pages_in} in != {self.pages_kept} kept + "
                f"{self.pages_dropped} dropped"
            )
        if self.parent_doc_id is not None and self.container_order is None:
            raise ContractViolation(
                f"{self.rel_path}: container child without a container_order — "
                "child ID assignment would be nondeterministic"
            )


# ---------------------------------------------------------------------------
# Run-level types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MasterIndexSnapshot:
    """Identity of the master index used, per D-04 mitigation (b)."""

    filename: str
    sha256: str
    row_count: int


OCR_REVIEW_MIN_CHARS = 40
"""Below this many characters, a page has nothing for a human to check.

Measured on the GTG progress report: of 99 pages the 85% threshold flagged, 11
carried FEWER THAN 20 CHARACTERS. Their confidences repeat exactly across
different documents — 71.76% three times, 74.97% twice — which is the signature
of the same speck of scanner noise being recognized as the same token. A
confidence score over two glyphs is not a measurement of anything.

Aligned with ``extract._NATIVE_TEXT_FLOOR``, deliberately: that constant decides
a page has no usable TEXT LAYER and must be OCR'd, and this one decides the OCR
came back with no usable text either. Same question, same answer, one number.

Paired with :data:`OCR_REVIEW_MIN_LINES`: both must hold, so a page that
returned little text across many lines still flags.

Such a page is EXCLUDED FROM REVIEW AND NOT FROM THE RECORD — the log counts it
under ``ocr_pages_without_usable_text``. Principle 1 forbids a page quietly
leaving the account; it does not require sending an expert to proofread a blank.
"""


OCR_REVIEW_MIN_LINES = 2
"""Companion to :data:`OCR_REVIEW_MIN_CHARS`, and the half that guards the case
this corpus happens not to contain.

A blank page yields one or two tokens; measured, all 84 low-character pages of
the GTG run returned 0-2 lines and none returned more. A page returning forty
lines that total thirty characters is a different animal — a dense page the
recognizer failed on — and it must reach a human rather than be filed as blank.
"""


def needs_ocr_review(page: "PageRecord", threshold_pct: int) -> bool:
    """**The** definition of "a human should check this page against the
    original" — one predicate, used by the log, the screen and the extractor.

    It exists because those three disagreed. The screen compared the RAW float
    (``ocr_conf < 0.85``), the log compared the value ROUNDED to a whole percent
    (``85 < 85`` is false), and 19 pages of the GTG run — confidences like
    84.73% — were shown to the operator and omitted from the audit record. A
    tool whose argument is that the log is the auditable account cannot have the
    log and the screen disagree about which pages need review.

    **The rounded comparison wins, and that is deliberate.** The integer percent
    is the number written to disk and rendered on screen, so deciding on it means
    the figure a reader sees explains the decision they are looking at. Deciding
    on a hidden float would leave a page marked for review beside a displayed
    "85%", which is the same class of confusion one level down.
    """
    if page.ocr_conf is None:
        return False
    # "Nothing to review" needs BOTH conditions, and the second is the one this
    # corpus does not exercise. Few characters across MANY lines is not a blank
    # page — it is a dense page whose reading collapsed, and that is precisely a
    # page a human must see. Measured on the GTG run: all 84 low-character pages
    # returned 0-2 lines, so the guard changes nothing there. It is written this
    # way because "the corpus does not exercise it" selects nothing: the failure
    # would arrive on the first matter that scans worse than this one.
    if (len(page.text.strip()) < OCR_REVIEW_MIN_CHARS
            and page.ocr_line_count <= OCR_REVIEW_MIN_LINES):
        return False
    return round(page.ocr_conf * 100) < threshold_pct


def matter_key(source_root: str) -> str:
    """The one derivation of "which matter is this" (Codex r2, B-2).

    An approval authorizes an omission on ONE matter. Deciding whether a later
    run is that matter was done with ``Path(source_root).name`` — the folder's
    display name — and two matters called `Production` under different clients
    are the same string. Codex reproduced `C:/Client-A/Production` and
    `D:/Client-B/Production` colliding, so the first client's ruling survived
    into the second client's run and dropped its pages.

    **The defect was deriving a SCOPE KEY from a DISPLAY STRING.** They answer
    different questions: one is what an expert should read in a drop log, the
    other is whether two runs are the same matter. This function is the second,
    it lives here so both the capture point and Stage 4 use the same one, and it
    is deliberately not pretty — nothing renders it.

    ``normcase`` because Windows paths differ in case and not in meaning;
    ``abspath`` because a relative root and the absolute root it resolves to are
    the same folder. Not ``resolve()``: that touches the filesystem and would
    make the key depend on whether a network share happened to be mounted.
    """
    import os

    return os.path.normcase(os.path.abspath(source_root))


@dataclass(frozen=True, slots=True)
class OmissionSnapshot:
    """One expert-approved omission, as the run identity records it
    (amendment A-19).

    The A-08 argument, one level along. A-08 put profiles in the identity
    because they decided which pages dropped; D-34 and D-35 move that decision
    to an approval a person gives against a template family, so the approvals
    are now the input that decides, and an identity that omits them says two
    corpora are the same run when one of them is missing a section.

    A snapshot rather than :class:`dociq.sections.model.ApprovedOmission`
    itself, for the reason every other snapshot here is one — the identity needs
    a fixed fingerprint, not a live object — and for a second reason particular
    to this module: :mod:`dociq.sections` imports the contract, so the contract
    cannot import it back.

    Every field is hashed, ``approved_by`` and ``approved_at`` included. That is
    deliberate and it narrows the determinism claim: two runs over one folder
    that differ only in WHO approved the omission are not byte-identical,
    because the drop log names the approver and the approver differs. Recording
    the person is the whole of D-34; a claim that had to pretend otherwise would
    be the wrong claim to keep.
    """

    family_id: str
    approved_by: str
    approved_at: str
    matter: str
    """The matter's NAME, for a human reading the log. Not what decides scope —
    see :attr:`matter_root`."""

    matter_root: str
    """:func:`matter_key` of the source folder the approval was given on.

    What actually decides whether a later run is the same matter. Separate from
    :attr:`matter` because a display name is not an identity: two clients each
    having a folder called `Production` is ordinary, and it made one client's
    ruling apply to the other's record."""

    template_id: str
    template_version: str

    project_tokens: tuple[str, ...] = ()
    """The canonical project names in force when this approval was given
    (Codex Sprint-4 B-1).

    An approval is a ruling about a set of pages, and the token list decides
    which labels reach a family — the same power the template version has, and
    an approval is already refused across versions for exactly that reason.
    Carried so Stage 4 can refuse an approval reviewed under a different set
    instead of silently widening it."""

    recognition: str = ""
    """Fingerprint of the recognition configuration this approval was REVIEWED
    against (`contracts.recognition_fingerprint`).

    Project tokens and whether OCR ran were each found, one at a time, to change
    which family a page lands in while an approval reached it unchanged. This
    covers both and whatever is added next: a new recognition input joins the
    fingerprint and is enforced for every approval the same day.

    Empty means "not recorded" — every approval given before this field existed.
    Those are compared on the named fields alone, so an old approval is neither
    silently widened nor silently voided.
    """


@dataclass(frozen=True, slots=True)
class EffectiveLimits:
    """Every environment-controlled setting that can change output evidence
    (amendment A-04, from Codex review #1 finding B-2).

    :class:`RunConfig`'s contract is that anything influencing output and not
    in it is a determinism bug. These settings were outside it: caps, timeouts,
    retry bounds and the OCR model identity all live in module-level constants
    read from the environment. When a cap or timeout *bites*, the same folder,
    profile and index produce different evidence under an identical hashed
    configuration — so the determinism identity was incomplete, by the
    contract's own definition.

    Per-document truncation notes disclose the effect but do not repair the
    identity: a consumer comparing two runs' hashes would see agreement that
    the bytes do not support.

    All ints and strings, so identity hashing is unaffected.
    """

    xlsx_max_rows: int
    csv_max_rows: int
    zip_max_mb: int
    zip_max_members: int
    zip_max_depth: int

    file_timeout_ms: int
    """The per-file watchdog deadline, in **integer milliseconds** (A-08).

    Milliseconds rather than seconds because the setting it records
    (``DOCIQ_FILE_TIMEOUT``, ``WalkOptions.file_timeout_s``) is a float used as
    a float-valued deadline. Rounding it to whole seconds made 1.1 s and 1.4 s
    record the same identity while abandoning different files — a determinism
    collision inside the field whose entire purpose is to close one. A float
    here is barred by Principle 5, and rejecting fractional values would have
    removed a capability the watchdog uses, so the unit changed instead."""

    retry_max: int

    retry_budget_ms: int
    """The serial-retry wall-clock budget, in **integer milliseconds** (A-08).
    Same argument as :attr:`file_timeout_ms`; ``DOCIQ_RETRY_BUDGET_S`` is a
    float."""

    recurse: bool

    ocr_model_id: str = ""
    """Stable identity of the OCR model artifact — package version plus a hash
    of the model files. Two engines that read the same page differently are
    different inputs, and a version string alone does not prove the bytes
    match."""

    workers: int = 0
    """Recorded but NOT hashed by convention — see
    :data:`_IDENTITY_EXCLUDED`. Thread-pool width must not change output; if it
    ever does, that is a determinism defect to fix rather than a value to
    absorb into the identity. Recording it keeps a performance report
    interpretable."""


def fold_label(text: str) -> str:
    """Fold text for comparison: accents removed, non-alphanumerics collapsed to
    single spaces, upper-cased.

    **The single definition.** `dociq.sections.normalize.normalize_label` IS
    this function — it delegates rather than reimplementing, and keeps the name
    the pipeline reads it by. It lives here because :func:`canonical_tokens`
    needs the identical fold and the contract may not import a pipeline package.
    Two copies would be a fold that can silently disagree with itself, which is
    the defect this arrangement makes unrepresentable.

    Accent folding is load-bearing rather than tidy — this corpus is Brazilian
    and its outlines carry ``PÁGINA EM BRANCO`` with and without the accent in
    the same production run.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^A-Za-z0-9]+", " ", folded)
    return folded.strip().upper()


def recognition_fingerprint(
    *,
    project_tokens: Iterable[str] = (),
    template_id: str | None = None,
    template_version: str | None = None,
    ocr_ran: bool = True,
) -> str:
    """Everything that decides which template family a page lands in.

    **Why a fingerprint and not one more field.** Two components of this were
    found one at a time, each by a review rather than by the code: project
    tokens (Codex Sprint-4 B-1) and whether OCR ran — a photographed schedule
    table classifies as a photograph page unread and as a schedule table once
    OCR recovers its grid, so an unchanged approval for progress photographs
    drops it in one run and keeps it in the other. Scoping component-by-
    component leaves the next input to whoever remembers, which is the failure
    mode this sprint produced three times.

    An approval carries this value, and Stage 4 refuses an approval whose
    fingerprint is not the run's. A new recognition input joins the arguments
    here and is enforced the same day, for every approval, without anyone
    remembering anything.

    **It does not replace the named fields.** `project_tokens` stays on the
    approval because it is what the operator's warning says out loud, and a
    message reading "the recognition fingerprint changed" would be the wrong
    half of Codex A-R2-1's lesson. The fingerprint decides; the named fields
    explain.

    Stable across spellings that do not change behavior: tokens are
    canonicalized, and the parts are joined by a separator none of them can
    contain, so `("A","B|C")` and `("A|B","C")` cannot collide.
    """
    parts = (
        "v1",
        ",".join(canonical_tokens(project_tokens)),
        template_id or "",
        template_version or "",
        "ocr" if ocr_ran else "no-ocr",
    )
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def canonical_tokens(tokens: Iterable[str]) -> tuple[str, ...]:
    """One spelling of a project-token list, so the identity tracks the BEHAVIOR.

    Matching already ignores case and order — `strip_project_tokens` folds each
    token and removes them all — so `("BOMESC", "MV32")`, `("MV32", "bomesc")`
    and `("MV32", "BOMESC", "MV32")` are ONE run, and before this they were
    three run identities. An identity that moves when the reduction did not is a
    false "different configuration" the next time two runs are compared, which
    is the question the identity exists to answer.

    **Defined here rather than beside the derivation that produces the list**,
    for the reason stated on :class:`OmissionSnapshot`: `dociq.sections` imports
    the contract, so the contract cannot import it back. The first version put
    this in `dociq.sections.project_tokens` and reached back for it from
    `RunConfig.__post_init__` — a deferred import, so nothing failed at load,
    and the rule was broken all the same. `dociq.sections.project_tokens`
    re-exports it, so it is still importable from the module that owns the
    vocabulary.

    **Folded with** :func:`fold_label`, the same fold the matching uses.
    Upper-casing alone was not enough and shipped a defect of exactly the shape
    this function exists to prevent: `PETROBRÁS` and `PETROBRAS` strip
    identically and got two identities, as did `T1-R1` and `T1 R1`. A canonical
    form must be the form the behavior keys on, or it is just another spelling.
    """
    folded = {fold_label(t) for t in tokens}
    return tuple(sorted(f for f in folded if f))


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Everything that can change output bytes.

    The determinism contract is "same folder + same profile + same master
    index = byte-identical" (Principle 5 as amended by D-04). Anything that
    influences output and is NOT in this dataclass is a determinism bug.
    """

    source_root: str

    output_root: str
    """Where the deliverables are written. **Recorded but NOT hashed** — see
    :data:`_IDENTITY_EXCLUDED` (amendment A-08).

    The destination is where evidence is put, not an input that changes it, and
    treating it as identity made three parts of the system contradict each
    other: this projection hashed it, the manifest's claim named it, and both
    the processing log and the criterion-7 harness ignored it — the harness
    runs to two different destinations precisely to prove one identity."""

    master_index: MasterIndexSnapshot | None = None
    ocr_conf_threshold_pct: int = 80
    """§4 Stage 2 default. Pages whose confidence falls below this are flagged
    for human review.

    **80, not 85, and the change is measured rather than tuned.** 85 came from
    the requirements as a plausible-sounding figure and was never calibrated
    against rapidocr, whose confidence is not a probability of correctness. On
    377 OCR pages of the GTG progress report the distribution is ONE population
    — median 86.3%, p90 90.3%, modal band 85-89 holding 236 pages — and 85 was
    planted on that band's left edge, flagging 99 pages (26.3%) of which 58 sat
    in the 80-84 band. D-19's bake-off had already measured the engine's mean at
    86.28% on this corpus, so the average healthy page cleared the old bar by
    1.3 points and ordinary variation dipped under it.

    80 is where the distribution actually breaks: 35 pages (9.3%), against a
    thin tail below it and dense mass above. Full histogram and sweep in
    ``docs/verification/ocr_threshold_2026-08-18.md``.

    **Fitted to one corpus of 11 documents**, which is better-grounded than 85
    and is not thereby universal — and it is a hashed identity input, so
    changing it changes the run identity of every run that does not set it.

    An integer percent, not a float fraction — deliberately. This value is part
    of the run identity (change it and the flagged-page set, and therefore the
    log and summary, change), and Principle 5 forbids floats in identity
    fields. Percent is also the unit §4 states the default in. Compare against
    :attr:`PageRecord.ocr_conf` via :attr:`ocr_conf_threshold`."""

    ocr_engine: str = "rapidocr"
    ocr_engine_version: str = ""
    bates_pattern: str | None = None
    """Confirmed with the user on first detection per set (§4 Stage 3)."""

    limits: EffectiveLimits | None = None
    """The effective environment-controlled settings for this run (A-04).
    ``None`` only for constructions that never reach a real run — the pipeline
    must always populate it, and the manifest names it as part of the identity
    the byte-identical claim covers."""

    omissions: tuple[OmissionSnapshot, ...] = ()
    """Every omission an expert approved for this run, in the order the log
    records them (amendment A-19).

    This is the input that decides which pages drop. Under D-34 a template ships
    unengaged and can never drop a page on its own, so the disposition of a
    corpus is a function of these records and of nothing else in the profile
    system. Leaving them out of the identity would reproduce, exactly, the
    defect A-08 was raised to close: two runs over one folder, one of them
    missing a section, reporting the same identity.

    Empty is the ordinary state of a freshly-installed DocIQ and of every run
    nobody has ruled on — recognition happens, nothing drops."""

    project_tokens: tuple[str, ...] = ()
    """Matter-specific tokens stripped from a section label before a template
    family matches it (A-19).

    In the identity because it changes the answer: with `MV32` supplied,
    `MV32 APPENDICES` normalizes to the family `APPENDICES` and a rule keyed to
    appendices matches it; without, it does not, and the page keeps. Same folder,
    same approvals, different corpus — which is the definition of an identity
    input.

    Supplied per matter and never shipped, because a token list is a list of a
    client's own names (D-24)."""

    section_template_id: str | None = None
    """The template the approvals were given against, and its version. Recorded
    beside :attr:`omissions` rather than derived from them so that a run with no
    approvals still says which template was loaded — the difference between "the
    expert engaged nothing" and "no template was offered" is a fact about the
    run, and only one of them is a decision."""

    section_template_version: str | None = None

    def __post_init__(self) -> None:
        """Canonicalize the token list here, where no caller can skip it.

        Two entry points supply tokens — the setup screen and the CLI — and a
        normalization applied at either would leave the other minting spurious
        identities. Correct by construction: a `RunConfig` cannot hold a token
        list in a spelling that differs from its behavior.
        """
        canon = canonical_tokens(self.project_tokens)
        if canon != self.project_tokens:
            object.__setattr__(self, "project_tokens", canon)

    @property
    def ocr_conf_threshold(self) -> float:
        """The threshold as a ``[0,1]`` fraction, for comparison against
        :attr:`PageRecord.ocr_conf`. Derived — never stored, never hashed."""
        return self.ocr_conf_threshold_pct / 100.0

    @property
    def id_regime(self) -> IdRegime:
        return IdRegime.MASTER_INDEX if self.master_index else IdRegime.NATIVE


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """The §7 token estimate for one body of text (amendment A-01).

    Carried on :class:`RunResult` because §4 Stage 6 computes it, §7 writes it
    to ``run_summary.pdf`` and §9 headlines it on the summary screen. Without
    a field here the GUI would have to recompute it, putting a second estimator
    in the product — and the two numbers would then disagree in the same matter
    folder, one on screen and one in the PDF.
    """

    chars: int

    ratio_low: float
    ratio_high: float
    """The chars-per-token band the range was built from. Floats, therefore
    reporting-only and excluded from identity — see :data:`_IDENTITY_EXCLUDED`."""

    floor_tokens: int = 0
    """**RESERVED — do not populate.** Withdrawn by amendment A-05(a).

    This field once carried the pre-token count and was documented as a hard
    lower bound, on the reasoning that a byte-level BPE cannot merge across a
    pre-token boundary. Codex review #1 (B-6) established that the reasoning
    does not hold: it is true of a tokenizer's **own** pre-tokenization, and
    DocIQ's regex invents boundaries of its own — it splits digit runs every
    three digits — so a coarser real pre-tokenizer merges across them and emits
    *fewer* tokens than DocIQ counts pre-tokens. On 13%-digit material the
    effect is material.

    It stays at ``0`` until a real tokenizer measurement exists to fill it, so
    a consumer reading ``0`` correctly learns "no lower bound was
    established" — which is the true state of the world. Use
    :attr:`structural_tokens` for the measurement and :attr:`token_ceiling` for
    the one bound that is sound."""

    structural_tokens: int = 0
    """Tokens implied by the text's measured pre-token structure **under the
    assumptions stated in :attr:`provenance`** (A-05a). Not a bound in either
    direction. ``0`` means not measured."""

    token_ceiling: int = 0
    """Upper bound: ``tokens <= UTF-8 bytes``, because a byte-level vocabulary
    always contains single-byte fallbacks. **The only tokenizer-independent
    bound DocIQ asserts.** ``0`` means not measured."""

    ratio_refuted: bool = False
    """True when the text's own structure contradicts the configured ratio
    band, so the band was not used (amendment A-03).

    A separate field rather than something a consumer infers, for two reasons.
    Inferring it — say, by comparing ``chars / floor_tokens`` against the band —
    would put the pipeline's refutation test inside whatever code asks the
    question, and two implementations of it would eventually disagree. Parsing
    it back out of :attr:`provenance` would make a display string load-bearing.
    A boolean the producer sets is the only version of this that cannot drift.

    Consumers must render the refuted case differently rather than silently
    showing a number computed some other way."""

    provenance: str = ""
    """How the ratio was obtained, in words, travelling with the number.

    D-03 specifies calibration "against the real Claude tokenizer", which
    cannot be performed under Principle 4 (no network) with no tokenizer
    artifact available offline. Whatever a build actually did must be stated
    here and rendered beside the figure. An evidentiary tool may show an
    approximation; it may not show an approximation dressed as a measurement."""

    def __post_init__(self) -> None:
        if self.ratio_low <= 0 or self.ratio_high < self.ratio_low:
            raise ContractViolation(
                f"token ratio band must be positive and ordered, got "
                f"{self.ratio_low}–{self.ratio_high}"
            )


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    """One discrepancy between the folder and the master index (§5)."""

    category: str
    """``"folder-only"`` | ``"index-only"`` | ``"field-mismatch"``."""

    doc_id: str
    filename: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """The §5 reconciliation, a first-class deliverable (amendment A-02).

    ``DocumentRecord.li_file_no`` records the *result* of one successful match,
    which is not the same information: it cannot express an index row with no
    file at all, nor a field disagreement between a matched pair. Both are
    categories §5 requires the report to carry.
    """

    matched: int
    rows: tuple[ReconciliationRow, ...] = ()

    @property
    def folder_only(self) -> tuple[ReconciliationRow, ...]:
        return tuple(r for r in self.rows if r.category == "folder-only")

    @property
    def index_only(self) -> tuple[ReconciliationRow, ...]:
        return tuple(r for r in self.rows if r.category == "index-only")

    @property
    def field_mismatch(self) -> tuple[ReconciliationRow, ...]:
        return tuple(r for r in self.rows if r.category == "field-mismatch")


@dataclass(frozen=True, slots=True)
class RunResult:
    """The full outcome of one run — what the emit layer writes and the GUI
    displays."""

    config: RunConfig
    documents: tuple[DocumentRecord, ...] = ()
    unsupported: tuple[DocumentRecord, ...] = ()
    """Tier-2 files: inventoried and hashed, never blocking (§3)."""
    warnings: tuple[str, ...] = ()

    tokens_before: TokenEstimate | None = None
    tokens_after: TokenEstimate | None = None
    """§7 token estimate across the corpus, before and after reduction (A-01).
    ``None`` means not computed."""

    terminal_status: TerminalStatus = TerminalStatus.COMPLETED
    """How the run ended (A-06). Defaulted to COMPLETED so the change is
    additive — every existing construction site describes a completed run.

    **Publication rights are derived from this, never granted alongside it.**
    A consumer must not treat a non-COMPLETED result as a corpus."""

    terminal_status_reason: str = ""
    """Why, when :attr:`terminal_status` is not COMPLETED. Empty otherwise."""

    reconciliation: ReconciliationReport | None = None
    """§5 master-index reconciliation (A-02). ``None`` means no master index
    was supplied — which is not the same as a reconciliation that found
    nothing, and the emit layer must not render the two identically."""

    @property
    def pages_in(self) -> int:
        return sum(d.pages_in for d in self.documents)

    @property
    def pages_kept(self) -> int:
        return sum(d.pages_kept for d in self.documents)

    @property
    def pages_dropped(self) -> int:
        return sum(d.pages_dropped for d in self.documents)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DocIQError(Exception):
    """Base class for every DocIQ error."""


class ContractViolation(DocIQError):
    """A record violated the frozen contract. Always a bug, never user input."""


class ExtractionError(DocIQError):
    """A document's text could not be extracted. Carries an actionable message
    and is recorded against the document — it never aborts a run."""


# ---------------------------------------------------------------------------
# The canonical serializer — one function, used for BOTH hashing and
# persistence, per the durable-fingerprinted-artifact rule
# ---------------------------------------------------------------------------

_IDENTITY_EXCLUDED: frozenset[str] = frozenset(
    {
        "ocr_conf",
        "ratio_low",
        "ratio_high",
        "workers",
        "output_root",
        "terminal_status",
        "terminal_status_reason",
    }
)
"""Fields excluded from identity hashing.

``ocr_conf`` is a float and a *reporting* value; including it would make the
byte-identical claim hostage to OCR float jitter. It is still persisted — it
just does not participate in identity. Principle 5: "no floats in identity
fields."

``ratio_low``/``ratio_high`` (A-01) join it for the same reason: the token
band is an estimate about the text, not a property of it, and re-ruling D-03
must not invalidate the identity of runs already produced.

``terminal_status`` and ``terminal_status_reason`` (A-07) are excluded on a
different ground, and 1.5.0 got this wrong in the other direction. Termination
is a property of an INVOCATION, not of a corpus. An incomplete run publishes no
corpus and no corpus manifest, so it cannot collide with a completed corpus
hash — the previous completed manifest survives untouched, which is the whole
point of the publication guard. Hashing termination into a corpus identity
would make the byte-identical claim describe something other than the bytes it
covers, and would make rewording an operator sentence change the identity of
runs already produced. The typed status is still carried on every
:class:`RunResult` and still written to the log and the incomplete-run record;
it is simply not a term in the identity of a corpus that was never published.

``output_root`` (A-08) is the destination, not an input. Hashing it made the
manifest's stated identity disagree with what the pipeline and the acceptance
harness actually compare — the harness runs the same corpus to two different
folders and requires one identity, which is the correct semantics.

Note this is matched by field *name* across every contract dataclass, so a
field named ``ocr_conf`` on a future type is excluded automatically. That is
deliberate — the failure mode it prevents (a float silently entering the hash)
is worse than the one it risks (a field excluded that need not have been).
"""


def to_jsonable(obj: object, *, for_identity: bool = False) -> object:
    """Convert a contract object into JSON-safe primitives.

    The single conversion used by both the hash path and the persistence path.
    Two serializers would eventually disagree; this one cannot.

    Args:
        for_identity: drop :data:`_IDENTITY_EXCLUDED` fields, for hashing.
    """
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, float):
        if for_identity:
            raise ContractViolation("floats must not appear in identity fields")
        return round(obj, 4)
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v, for_identity=for_identity)
                for k, v in sorted(obj.items())}
    if hasattr(obj, "__dataclass_fields__"):
        out: dict[str, object] = {}
        for name in obj.__dataclass_fields__:  # declaration order — stable
            if for_identity and name in _IDENTITY_EXCLUDED:
                continue
            out[name] = to_jsonable(getattr(obj, name), for_identity=for_identity)
        return out
    if isinstance(obj, Sequence):
        return [to_jsonable(v, for_identity=for_identity) for v in obj]
    raise ContractViolation(f"not serializable under the contract: {type(obj)!r}")


def canonical_json(obj: object, *, for_identity: bool = False) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace, UTF-8, LF.

    Used for every hashed or persisted structure.
    """
    return json.dumps(
        to_jsonable(obj, for_identity=for_identity),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def content_hash(obj: object) -> str:
    """SHA-256 over the identity projection of ``obj``.

    Note the split the manifest must state explicitly (architecture §Determinism
    spine): run timestamp and operator live outside the hashed content, so a
    rerun at a different time still proves byte-identical *content*.
    """
    return hashlib.sha256(
        canonical_json(obj, for_identity=True).encode("utf-8")
    ).hexdigest()


def run_identity(config: RunConfig) -> str:
    """**The** run identity — one projection, named once (amendment A-08).

    Codex review #1 round 2, B-R2-2, found four things claiming to be the run
    identity and disagreeing: ``content_hash(RunConfig)`` hashed the output
    folder, the manifest's ``claim_identity`` said the output folder counted,
    :mod:`dociq.emit.log` deliberately left it out of hashed content, and the
    acceptance harness ran to two different folders and demanded one identity.
    No single value was persisted, so there was nothing to point at and say
    "this is what the claim covers."

    This is that value. It is written into ``output_manifest.json`` and into
    the log's hashed content, so a consumer comparing two runs compares one
    number that both artifacts agree on, and a future edit that changes what is
    hashed changes it visibly rather than silently.

    A thin wrapper over :func:`content_hash` on purpose: a second hashing rule
    here would be exactly the drift this function exists to end.
    """
    return content_hash(config)


# ---------------------------------------------------------------------------
# Canonical ordering
# ---------------------------------------------------------------------------


def document_sort_key(doc: DocumentRecord) -> tuple[str, str, int]:
    """The one true document order: relative path, then SHA-256, then position
    within a container.

    Every emitter, the index, the log and the ID assigner must use this. Two
    files can share a path only across container boundaries, so the hash and
    container order break the tie deterministically.
    """
    return (doc.rel_path, doc.sha256, doc.container_order or 0)


__all__ = [
    "CONTRACT_VERSION",
    "PageKind",
    "Disposition",
    "RecognitionTier",
    "ProcessingStatus",
    "TerminalStatus",
    "IdRegime",
    "PageRecord",
    "DocumentRecord",
    "TokenEstimate",
    "ReconciliationRow",
    "ReconciliationReport",
    "EffectiveLimits",
    "MasterIndexSnapshot",
    "RunConfig",
    "RunResult",
    "DocIQError",
    "ContractViolation",
    "ExtractionError",
    "to_jsonable",
    "canonical_json",
    "content_hash",
    "run_identity",
    "document_sort_key",
]
