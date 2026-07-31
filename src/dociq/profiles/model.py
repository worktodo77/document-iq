"""The format-profile schema, its YAML form, and where profiles live (§6, D-05).

A profile is the written record of an expert's decision to omit something. It
therefore has to be three things at once: machine-readable (the KEEP/DROP
engine consumes it), human-auditable (opposing counsel may read it), and
attributable (§6 step 4: saved with the expert's Windows username and
timestamp). The YAML form is the auditable one, so it is written in a fixed,
readable field order rather than sorted alphabetically — but the *hash* goes
through the contract's canonical serializer, so presentation and identity never
diverge.

Default state for every section is KEEP. A rule that says nothing drops nothing;
the schema has no way to express "drop everything unmatched", by construction.
"""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dociq.contracts import CONTRACT_VERSION, Disposition, DocIQError, content_hash

__all__ = [
    "ProfileError",
    "SectionRule",
    "FormatProfile",
    "OperatorStamp",
    "operator_stamp",
    "profile_library_dir",
    "load_profile",
    "loads_profile",
    "dump_profile",
    "save_to_library",
    "write_matter_copy",
    "MATTER_COPY_DIRNAME",
    "PROFILE_LIBRARY_ENV",
]

PROFILE_LIBRARY_ENV = "DOCIQ_PROFILE_LIBRARY"
"""D-05's "settings field may point the library at a shared LI drive path",
expressed as an environment override so the pipeline core stays GUI-free."""

MATTER_COPY_DIRNAME = "profile"
"""D-05: the per-matter copy is written regardless of whether the shared
library is reachable, so a matter is self-documenting on its own."""

SCHEMA_VERSION = "1"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ProfileError(DocIQError):
    """A profile file is malformed. Always actionable: profiles are hand-edited
    by experts, so the message names the offending key."""


@dataclass(frozen=True, slots=True)
class SectionRule:
    """One expert ruling about one section of a recurring format."""

    rule_id: str
    """Stable identifier written into every drop log entry and into
    ``PageRecord.drop_rule``. Renaming one breaks the audit trail between runs,
    so it is validated as an identifier rather than free text."""

    pattern: str
    """Regex matched against a candidate section-header line."""

    disposition: Disposition = Disposition.KEEP
    label: str | None = None
    notes: str | None = None
    """§6's "why sections were dropped, who approved" — free text, carried
    verbatim into the processing log."""

    case_sensitive: bool = False

    def compiled(self) -> re.Pattern[str]:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        try:
            return re.compile(self.pattern, flags)
        except re.error as exc:
            raise ProfileError(
                f"rule {self.rule_id!r}: pattern is not a valid regular "
                f"expression ({exc})"
            ) from exc

    def validate(self) -> None:
        if not _ID_RE.match(self.rule_id):
            raise ProfileError(
                f"rule id {self.rule_id!r} must be lower-case letters, digits, "
                "'.', '_' or '-' (max 64 chars) — it is written into the audit "
                "trail and must stay stable"
            )
        if not self.pattern.strip():
            raise ProfileError(f"rule {self.rule_id!r}: pattern is empty")
        self.compiled()
        if self.disposition is Disposition.DROP and not (self.notes or "").strip():
            raise ProfileError(
                f"rule {self.rule_id!r}: a DROP rule must carry notes recording "
                "why the section is omitted and who approved it (§6 step 4)"
            )


@dataclass(frozen=True, slots=True)
class OperatorStamp:
    """Who saved a profile and when (§6 step 4)."""

    username: str
    saved_at: str
    """ISO-8601 UTC, seconds precision. Seconds, not microseconds, because the
    stamp is read by humans and compared between runs."""

    host: str = ""


def operator_stamp(*, now: datetime | None = None) -> OperatorStamp:
    """Capture the current Windows user and time.

    ``USERNAME`` first because that is the Windows account name §6 asks for;
    :func:`getpass.getuser` is the cross-platform fallback so the test suite
    runs anywhere.
    """
    username = os.environ.get("USERNAME") or ""
    if not username:
        try:
            username = getpass.getuser()
        except Exception:  # pragma: no cover - only on an account-less host
            username = "unknown"
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return OperatorStamp(
        username=username,
        saved_at=moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        host=os.environ.get("COMPUTERNAME", ""),
    )


@dataclass(frozen=True, slots=True)
class FormatProfile:
    """An expert-approved profile for one recurring document format."""

    profile_id: str
    version: str
    display_name: str
    header_patterns: tuple[str, ...] = ()
    """Patterns identifying the format. Empty means "applies to every
    document" — which is legitimate for a matter-wide profile but is reported
    by :meth:`applies_to` so nobody applies a wide profile by accident."""

    section_rules: tuple[SectionRule, ...] = ()
    bates_pattern: str | None = None
    """The Bates format confirmed for this recurring production, in the
    canonical form :attr:`dociq.identify.bates.BatesFormat.pattern` emits — a
    matching regex carrying a ``(?#dociq-bates:1;...)`` token that records the
    complete grammar. A run loads it and applies it; a run that cannot read it
    back stops rather than proceeding on a format it cannot enforce. Validation
    here stays at "is a compilable regex" deliberately: rejecting a
    hand-written pattern at *load* time would take a profile's rules down with
    it, and Stage 3 is where a Bates format is confirmed."""

    notes: str = ""
    created_by: str = ""
    created_at: str = ""
    host: str = ""
    schema_version: str = SCHEMA_VERSION
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not _ID_RE.match(self.profile_id):
            raise ProfileError(
                f"profile_id {self.profile_id!r} must be lower-case letters, "
                "digits, '.', '_' or '-' (max 64 chars)"
            )
        if not self.version.strip():
            raise ProfileError(f"{self.profile_id}: version is required (§6: profiles are versioned)")
        seen: set[str] = set()
        for rule in self.section_rules:
            rule.validate()
            if rule.rule_id in seen:
                raise ProfileError(
                    f"{self.profile_id}: duplicate rule id {rule.rule_id!r} — "
                    "drop attribution would be ambiguous"
                )
            seen.add(rule.rule_id)
        for pattern in self.header_patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ProfileError(
                    f"{self.profile_id}: header pattern {pattern!r} is not a "
                    f"valid regular expression ({exc})"
                ) from exc
        if self.bates_pattern is not None:
            try:
                re.compile(self.bates_pattern)
            except re.error as exc:
                raise ProfileError(
                    f"{self.profile_id}: bates_pattern is not a valid regular "
                    f"expression ({exc})"
                ) from exc

    @property
    def drop_rules(self) -> tuple[SectionRule, ...]:
        return tuple(r for r in self.section_rules if r.disposition is Disposition.DROP)

    @property
    def profile_hash(self) -> str:
        """Identity of the profile's *content*, through the contract's single
        serializer. The operator stamp is part of it deliberately: a profile
        re-saved by a different expert is a different ruling."""
        return content_hash(self)

    def stamped(self, stamp: OperatorStamp | None = None) -> "FormatProfile":
        s = stamp or operator_stamp()
        return replace(
            self, created_by=s.username, created_at=s.saved_at, host=s.host
        )

    def applies_to(self, sample_text: str) -> bool:
        """Whether this profile claims a document (§4 Stage 4).

        A profile with no header patterns claims everything — that is what
        "matter-wide profile" means — and a document no profile claims passes
        through whole.
        """
        if not self.header_patterns:
            return True
        return any(
            re.search(p, sample_text, re.IGNORECASE) for p in self.header_patterns
        )


# ---------------------------------------------------------------------------
# YAML form
# ---------------------------------------------------------------------------

_FIELD_ORDER = (
    "schema_version",
    "profile_id",
    "version",
    "display_name",
    "created_by",
    "created_at",
    "host",
    "contract_version",
    "notes",
    "bates_pattern",
    "header_patterns",
    "section_rules",
)


def _require_yaml():
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ProfileError(
            "Reading format profiles requires PyYAML, a declared dependency of "
            "DocIQ; the installation is incomplete."
        ) from exc
    return yaml


def dump_profile(profile: FormatProfile) -> str:
    """Serialize to the on-disk YAML form.

    Field order is fixed and human-first (identity, then attribution, then
    rules) because this file is an evidentiary record someone will read. The
    order is also *deterministic*, which is what the byte-identical claim needs
    from the per-matter copy.
    """
    yaml = _require_yaml()
    profile.validate()
    body: dict[str, Any] = {
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id,
        "version": profile.version,
        "display_name": profile.display_name,
        "created_by": profile.created_by,
        "created_at": profile.created_at,
        "host": profile.host,
        "contract_version": profile.contract_version,
        "notes": profile.notes,
        "bates_pattern": profile.bates_pattern,
        "header_patterns": list(profile.header_patterns),
        "section_rules": [
            {
                "rule_id": r.rule_id,
                "label": r.label,
                "pattern": r.pattern,
                "disposition": r.disposition.value,
                "case_sensitive": r.case_sensitive,
                "notes": r.notes,
            }
            for r in profile.section_rules
        ],
    }
    text = yaml.safe_dump(
        body, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
    )
    header = (
        "# LI Document IQ format profile (§6).\n"
        "# Every section is KEPT unless a rule below says DROP, and every DROP\n"
        "# carries the rule id into the processing log (Principle 1).\n"
    )
    return header + text


def loads_profile(text: str) -> FormatProfile:
    yaml = _require_yaml()
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:
        raise ProfileError(f"profile is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProfileError("profile must be a YAML mapping at the top level")

    unknown = sorted(set(raw) - set(_FIELD_ORDER))
    if unknown:
        raise ProfileError(
            f"profile contains unknown key(s) {unknown}. A typo in a key would "
            "otherwise silently disable a rule."
        )

    rules_raw = raw.get("section_rules") or []
    if not isinstance(rules_raw, Sequence) or isinstance(rules_raw, (str, bytes)):
        raise ProfileError("section_rules must be a list")
    rules: list[SectionRule] = []
    for i, item in enumerate(rules_raw, start=1):
        if not isinstance(item, Mapping):
            raise ProfileError(f"section_rules[{i}] must be a mapping")
        extra = sorted(
            set(item)
            - {"rule_id", "label", "pattern", "disposition", "case_sensitive", "notes"}
        )
        if extra:
            raise ProfileError(f"section_rules[{i}] contains unknown key(s) {extra}")
        disposition_raw = str(item.get("disposition", Disposition.KEEP.value)).lower()
        try:
            disposition = Disposition(disposition_raw)
        except ValueError as exc:
            raise ProfileError(
                f"section_rules[{i}]: disposition must be "
                f"{Disposition.KEEP.value!r} or {Disposition.DROP.value!r}, "
                f"got {disposition_raw!r}"
            ) from exc
        rules.append(
            SectionRule(
                rule_id=str(item.get("rule_id", "")),
                pattern=str(item.get("pattern", "")),
                disposition=disposition,
                label=(str(item["label"]) if item.get("label") is not None else None),
                notes=(str(item["notes"]) if item.get("notes") is not None else None),
                case_sensitive=bool(item.get("case_sensitive", False)),
            )
        )

    headers = raw.get("header_patterns") or []
    if isinstance(headers, str):
        headers = [headers]

    profile = FormatProfile(
        profile_id=str(raw.get("profile_id", "")),
        version=str(raw.get("version", "")),
        display_name=str(raw.get("display_name", "") or raw.get("profile_id", "")),
        header_patterns=tuple(str(h) for h in headers),
        section_rules=tuple(rules),
        bates_pattern=(
            str(raw["bates_pattern"]) if raw.get("bates_pattern") is not None else None
        ),
        notes=str(raw.get("notes", "") or ""),
        created_by=str(raw.get("created_by", "") or ""),
        created_at=str(raw.get("created_at", "") or ""),
        host=str(raw.get("host", "") or ""),
        schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
        contract_version=str(raw.get("contract_version", CONTRACT_VERSION)),
    )
    profile.validate()
    return profile


def load_profile(path: str | Path) -> FormatProfile:
    p = Path(path)
    if not p.is_file():
        raise ProfileError(f"profile not found: {p}")
    return loads_profile(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Where profiles live (D-05)
# ---------------------------------------------------------------------------


def profile_library_dir(override: str | Path | None = None) -> Path:
    """D-05: ``%APPDATA%\\LI DocIQ\\profiles`` by default, configurable.

    Resolution order is explicit argument, then the environment override, then
    the local default — so a shared-drive setting can be applied per run without
    a global side effect.
    """
    if override:
        return Path(override)
    env = os.environ.get(PROFILE_LIBRARY_ENV)
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "LI DocIQ" / "profiles"
    return Path.home() / ".li-dociq" / "profiles"


def _profile_filename(profile: FormatProfile) -> str:
    return f"{profile.profile_id}.v{profile.version}.yaml"


def save_to_library(
    profile: FormatProfile, library_dir: str | Path | None = None
) -> Path:
    """Write the profile into the reusable library (§6 step 4a).

    The library may be a shared LI drive that is unreachable; a failure here is
    raised rather than swallowed, because the caller must still write the
    per-matter copy and must be told which of the two succeeded.
    """
    target = profile_library_dir(library_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / _profile_filename(profile)
    path.write_text(dump_profile(profile), encoding="utf-8", newline="\n")
    return path


def write_matter_copy(profile: FormatProfile, output_root: str | Path) -> Path:
    """Write the matter's own copy (§6 step 4b, D-05).

    This copy is not optional and does not depend on the library being
    reachable: it is the record of what was excluded and on whose authority,
    and it must sit beside the evidence it describes.
    """
    target = Path(output_root) / MATTER_COPY_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    path = target / _profile_filename(profile)
    path.write_text(dump_profile(profile), encoding="utf-8", newline="\n")
    return path
