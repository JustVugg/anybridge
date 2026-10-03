"""Deterministic WebMCP capability trust and tool provenance.

The registry is deliberately local, bounded, and persistence-agnostic. It binds a
WebMCP capability to its origin and definition fingerprint so callers can detect
tool drift before execution. Site metadata is evidence, not authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping

from .action_policy import ActionRisk, RetryPolicy, assess_action


@dataclass(frozen=True)
class ToolFingerprint:
    origin: str
    name: str
    schema_hash: str
    description_hash: str
    read_only_hint: bool
    consequential_hint: bool
    untrusted_content_hint: bool
    debugging_hint: bool

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ToolTrustRecord:
    fingerprint: ToolFingerprint
    first_seen: int
    last_seen: int
    previous_schema_hash: str | None = None
    previous_description_hash: str | None = None
    outcomes: int = 0
    successful_outcomes: int = 0
    state: str = "new"
    risk: ActionRisk = ActionRisk.UNKNOWN
    retry_policy: RetryPolicy = RetryPolicy.REVALIDATE

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["fingerprint"] = self.fingerprint.to_dict()
        data["risk"] = self.risk.value
        data["retry_policy"] = self.retry_policy.value
        return data


@dataclass(frozen=True)
class ToolTrustAssessment:
    state: str
    origin: str
    name: str
    fingerprint: ToolFingerprint
    changed: bool
    output_untrusted: bool
    requires_confirmation: bool
    risk: ActionRisk
    retry_policy: RetryPolicy
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["fingerprint"] = self.fingerprint.to_dict()
        data["risk"] = self.risk.value
        data["retry_policy"] = self.retry_policy.value
        data["reasons"] = list(self.reasons)
        return data


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return repr(value)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


def fingerprint_tool(tool: Mapping[str, object], origin: str | None = None) -> ToolFingerprint:
    """Fingerprint a site tool without retaining executable code or output data."""
    annotations = tool.get("annotations")
    if not isinstance(annotations, Mapping):
        annotations = {}
    resolved_origin = str(origin or tool.get("origin") or "").strip()
    return ToolFingerprint(
        origin=resolved_origin,
        name=str(tool.get("name") or "").strip(),
        schema_hash=_hash(tool.get("inputSchema") or {}),
        description_hash=_hash(str(tool.get("description") or "")),
        read_only_hint=bool(annotations.get("readOnlyHint", False)),
        consequential_hint=bool(annotations.get("consequentialHint", False)),
        untrusted_content_hint=bool(annotations.get("untrustedContentHint", False)),
        debugging_hint=bool(annotations.get("debugging", False)),
    )


def _risk_for(tool: Mapping[str, object], fp: ToolFingerprint) -> tuple[ActionRisk, RetryPolicy, tuple[str, ...]]:
    action = fp.name or "webmcp_tool"
    target = str(tool.get("description") or "")
    explicit = fp.consequential_hint
    if fp.read_only_hint and not explicit:
        assessment = assess_action("read", target=target, consequential=False)
    else:
        assessment = assess_action(
            "call_webmcp_tool",
            target=f"{action} {target}",
            consequential=explicit,
        )
    reasons = list(assessment.reasons)
    if fp.read_only_hint:
        reasons.append("webmcp_read_only_hint")
    if fp.consequential_hint:
        reasons.append("webmcp_consequential_hint")
    if fp.untrusted_content_hint:
        reasons.append("webmcp_untrusted_content_hint")
    return assessment.risk, assessment.policy, tuple(dict.fromkeys(reasons))


def assess_tool_trust(
    tool: Mapping[str, object],
    previous: ToolTrustRecord | None = None,
    now: int = 0,
) -> ToolTrustAssessment:
    """Assess a discovered tool against a prior definition, if any."""
    fp = fingerprint_tool(tool)
    risk, retry_policy, policy_reasons = _risk_for(tool, fp)
    if not fp.name:
        state = "blocked"
        reasons = ("missing_tool_name",)
        return ToolTrustAssessment(
            state, fp.origin, fp.name, fp, False, fp.untrusted_content_hint,
            True, risk, RetryPolicy.DENY, reasons,
        )

    if previous is None:
        state = "new"
        changed = False
        reasons = ("first_seen_capability",) + policy_reasons
    else:
        changed = (
            previous.fingerprint.origin != fp.origin
            or previous.fingerprint.schema_hash != fp.schema_hash
            or previous.fingerprint.description_hash != fp.description_hash
            or previous.fingerprint.read_only_hint != fp.read_only_hint
            or previous.fingerprint.consequential_hint != fp.consequential_hint
            or previous.fingerprint.untrusted_content_hint != fp.untrusted_content_hint
            or previous.fingerprint.debugging_hint != fp.debugging_hint
        )
        state = "changed" if changed else ("trusted" if previous.state in {"trusted", "unchanged"} else previous.state)
        reasons = (("tool_definition_changed",) if changed else ("tool_definition_unchanged",)) + policy_reasons

    if fp.untrusted_content_hint:
        state = "untrusted_output" if state not in {"blocked"} else state
    if fp.consequential_hint:
        state = "changed" if changed and state != "blocked" else state

    requires_confirmation = (
        risk == ActionRisk.CONSEQUENTIAL
        or retry_policy == RetryPolicy.CONFIRM
        or fp.consequential_hint
        or state == "changed"
    )
    if state == "blocked":
        retry_policy = RetryPolicy.DENY
        requires_confirmation = True

    return ToolTrustAssessment(
        state=state,
        origin=fp.origin,
        name=fp.name,
        fingerprint=fp,
        changed=changed,
        output_untrusted=fp.untrusted_content_hint,
        requires_confirmation=requires_confirmation,
        risk=risk,
        retry_policy=retry_policy,
        reasons=tuple(dict.fromkeys(reasons)),
    )


class ToolTrustRegistry:
    """Bounded in-memory provenance registry owned by the AnyBridge caller."""

    def __init__(self, max_records: int = 512) -> None:
        self.max_records = max(1, int(max_records))
        self._records: dict[tuple[str, str], ToolTrustRecord] = {}
        self._clock = 0

    def observe(self, tool: Mapping[str, object], trusted: bool = False) -> ToolTrustAssessment:
        self._clock += 1
        fp = fingerprint_tool(tool)
        key = (fp.origin, fp.name)
        previous = self._records.get(key)
        assessment = assess_tool_trust(tool, previous=previous, now=self._clock)
        state = "trusted" if trusted and not assessment.changed else assessment.state
        if assessment.state == "untrusted_output":
            state = "untrusted_output"
        record = ToolTrustRecord(
            fingerprint=fp,
            first_seen=previous.first_seen if previous else self._clock,
            last_seen=self._clock,
            previous_schema_hash=previous.fingerprint.schema_hash if previous else None,
            previous_description_hash=previous.fingerprint.description_hash if previous else None,
            outcomes=previous.outcomes if previous else 0,
            successful_outcomes=previous.successful_outcomes if previous else 0,
            state=state,
            risk=assessment.risk,
            retry_policy=assessment.retry_policy,
        )
        self._records[key] = record
        while len(self._records) > self.max_records:
            oldest = min(self._records, key=lambda k: self._records[k].last_seen)
            del self._records[oldest]
        return ToolTrustAssessment(
            state=state,
            origin=assessment.origin,
            name=assessment.name,
            fingerprint=assessment.fingerprint,
            changed=assessment.changed,
            output_untrusted=assessment.output_untrusted,
            requires_confirmation=(\n                assessment.requires_confirmation\n                or state == "changed"\n                or (state == "new" and not trusted)\n            ),
            risk=assessment.risk,
            retry_policy=assessment.retry_policy,
            reasons=assessment.reasons,
        )

    def record_outcome(self, origin: str, name: str, success: bool) -> ToolTrustRecord | None:
        key = (str(origin), str(name))
        previous = self._records.get(key)
        if previous is None:
            return None
        updated = ToolTrustRecord(
            fingerprint=previous.fingerprint,
            first_seen=previous.first_seen,
            last_seen=previous.last_seen,
            previous_schema_hash=previous.previous_schema_hash,
            previous_description_hash=previous.previous_description_hash,
            outcomes=previous.outcomes + 1,
            successful_outcomes=previous.successful_outcomes + (1 if success else 0),
            state=previous.state,
            risk=previous.risk,
            retry_policy=previous.retry_policy,
        )
        self._records[key] = updated
        return updated

    def get(self, origin: str, name: str) -> ToolTrustRecord | None:
        return self._records.get((str(origin), str(name)))

    def list(self) -> tuple[ToolTrustRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda r: (r.fingerprint.origin, r.fingerprint.name)))

    def export(self) -> list[dict]:
        return [record.to_dict() for record in self.list()]


def assess_tool_set(
    tools: Iterable[Mapping[str, object]],
    registry: ToolTrustRegistry | None = None,
) -> tuple[ToolTrustAssessment, ...]:
    registry = registry or ToolTrustRegistry()
    return tuple(registry.observe(tool) for tool in tools)
