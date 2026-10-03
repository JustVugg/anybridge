"""Standalone closed-loop remediation primitives for browser execution.

AnyBridge identifies failure scope and emits declarative remediation actions.
The caller decides whether to execute an action and supplies the realized
outcome. The returned telemetry is intentionally compatible with the existing
execution/decomposition observations, without depending on any orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .evidence import EvidenceEnvelope


@dataclass(frozen=True)
class FailureAttribution:
    """A normalized failure tied to an execution step and/or dependency."""

    failure_id: str
    execution_id: str | None
    step_index: int | None
    step: str | None
    failure_class: str
    dependency: str | None
    expected: Any = None
    observed: Any = None
    evidence_confidence: float = 0.5
    source: str = "evidence"


@dataclass(frozen=True)
class RemediationAction:
    """A declarative, safe remediation proposal."""

    remediation_id: str
    failure_id: str
    action: str
    target: str | None
    preconditions: tuple[str, ...]
    rationale: str
    priority: int = 1


@dataclass(frozen=True)
class RemediationOutcome:
    """The caller's realized result after executing a remediation action."""

    remediation_id: str
    failure_id: str
    action: str
    result: str
    recovered: bool
    execution_id: str | None = None
    latency_ms: float | None = None
    evidence_confidence: float = 0.5
    attempt: int = 1
    network_failures: int = 0
    verification_failures: int = 0
    retries: int = 0
    dependency: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemediationFeedback:
    """Closed-loop telemetry suitable for the next execution decision."""

    outcome: RemediationOutcome
    execution_observation: dict[str, Any]
    decomposition_observation: dict[str, Any]
    reasons: tuple[str, ...]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _network_dependency(evidence: EvidenceEnvelope) -> str | None:
    for item in evidence.network_errors:
        if isinstance(item, Mapping):
            host = item.get("host") or item.get("hostname") or item.get("url")
            if host:
                return str(host)
    return None


def _classify(
    *,
    assertion: bool | None,
    action: str | None,
    observed: Any,
    evidence: EvidenceEnvelope,
) -> tuple[str, str | None]:
    network_dependency = _network_dependency(evidence)
    if evidence.network_errors:
        return "network_dependency", network_dependency
    if action in {"click", "fill", "select", "press_key"} and not _text(observed):
        return "interaction_target", None
    if assertion is False:
        return "assertion", None
    if action in {"navigate", "open"}:
        return "navigation", None
    if evidence.console_errors:
        return "browser_runtime", None
    return "execution", None


def correlate_failure(
    evidence: EvidenceEnvelope | Mapping[str, Any],
    *,
    failure_id: str,
    step_index: int | None = None,
    step: str | None = None,
    assertion: bool | None = None,
    dependency: str | None = None,
) -> FailureAttribution:
    """Correlate one failed evidence record to a concrete step/dependency."""
    if isinstance(evidence, EvidenceEnvelope):
        record = evidence
    else:
        record = EvidenceEnvelope(**dict(evidence))

    failure_class, inferred_dependency = _classify(
        assertion=assertion if assertion is not None else (
            record.status == "passed" if record.status in {"passed", "failed"} else None
        ),
        action=record.action,
        observed=record.observed,
        evidence=record,
    )
    return FailureAttribution(
        failure_id=failure_id,
        execution_id=record.execution_id,
        step_index=step_index,
        step=step,
        failure_class=failure_class,
        dependency=dependency or inferred_dependency,
        expected=record.expected,
        observed=record.observed,
        evidence_confidence=max(0.0, min(1.0, record.confidence if record.confidence is not None else 0.5)),
        source="evidence",
    )


def correlate_step_failure(
    *,
    failure_id: str,
    execution_id: str | None,
    step_index: int,
    step: str,
    status: str,
    action: str | None = None,
    expected: Any = None,
    observed: Any = None,
    dependency: str | None = None,
    evidence_confidence: float = 0.5,
) -> FailureAttribution:
    """Create attribution directly from a provider-neutral step result."""
    failed = status.lower() in {"failed", "error", "timeout"}
    failure_class = "assertion" if failed and action in {"assert", "check"} else "execution"
    if dependency:
        failure_class = "network_dependency"
    return FailureAttribution(
        failure_id=failure_id,
        execution_id=execution_id,
        step_index=step_index,
        step=step,
        failure_class=failure_class,
        dependency=dependency,
        expected=expected,
        observed=observed,
        evidence_confidence=max(0.0, min(1.0, evidence_confidence)),
        source="step",
    )


def propose_remediations(
    failure: FailureAttribution,
    *,
    remediation_prefix: str = "rem",
) -> tuple[RemediationAction, ...]:
    """Produce deterministic, non-executing remediation proposals."""
    base = f"{remediation_prefix}-{failure.failure_id}"
    if failure.failure_class == "interaction_target":
        return (
            RemediationAction(
                base,
                failure.failure_id,
                "refresh_snapshot_and_reidentify",
                failure.step,
                ("capture_current_snapshot",),
                "Interaction target may be stale; refresh the page representation before retrying.",
            ),
        )
    if failure.failure_class == "navigation":
        return (
            RemediationAction(
                base,
                failure.failure_id,
                "validate_target_and_url",
                failure.step,
                ("validate_target",),
                "Validate navigation target and resulting URL before retrying.",
            ),
        )
    if failure.failure_class == "network_dependency":
        return (
            RemediationAction(
                base,
                failure.failure_id,
                "inspect_and_approve_dependency",
                failure.dependency,
                ("review_network_policy", "explicit_approval"),
                "Inspect the blocked dependency and require explicit caller approval before access.",
            ),
        )
    if failure.failure_class == "browser_runtime":
        return (
            RemediationAction(
                base,
                failure.failure_id,
                "capture_runtime_diagnostics",
                failure.step,
                ("capture_console_errors",),
                "Capture runtime diagnostics before deciding whether to retry.",
            ),
        )
    if failure.failure_class == "assertion":
        return (
            RemediationAction(
                base,
                failure.failure_id,
                "deepen_verification",
                failure.step,
                ("preserve_failure_evidence",),
                "Increase verification depth rather than mutating the target blindly.",
            ),
        )
    return (
        RemediationAction(
            base,
            failure.failure_id,
            "retry_with_deeper_verification",
            failure.step,
            ("preserve_failure_evidence",),
            "Retry only after retaining the failed execution evidence.",
        ),
    )


def record_remediation_outcome(
    outcome: RemediationOutcome,
    *,
    provider: str | None = None,
    browser: str | None = None,
    task_class: str = "default",
) -> RemediationFeedback:
    """Turn realized remediation into next-cycle execution/decomposition signals."""
    confidence = max(0.0, min(1.0, outcome.evidence_confidence))
    failure_signal = (
        max(0, outcome.verification_failures)
        + max(0, outcome.retries)
        + max(0, outcome.network_failures)
    )
    if outcome.recovered:
        verification_failures = 0
        retries = max(0, outcome.attempt - 1)
        reason = "remediation recovered the flow; preserve successful action evidence"
    else:
        verification_failures = max(1, outcome.verification_failures)
        retries = max(1, outcome.retries, outcome.attempt - 1)
        reason = "remediation did not recover; increase caution for the next decision"

    execution = {
        "provider": provider,
        "browser": browser,
        "success": outcome.recovered,
        "evidence_confidence": confidence,
        "latency_ms": outcome.latency_ms,
        "verification_failures": verification_failures,
        "retries": retries,
        "task_class": task_class,
        "remediation_id": outcome.remediation_id,
        "failure_id": outcome.failure_id,
        "remediation_action": outcome.action,
        "remediation_result": outcome.result,
        "recovered": outcome.recovered,
        "dependency": outcome.dependency,
    }
    decomposition = {
        "independent_work": 1,
        "verification_failures": verification_failures,
        "retries": retries,
        "network_failures": max(0, outcome.network_failures),
        "evidence_confidence": confidence,
        "duration_ms": outcome.latency_ms,
        "task_class": task_class,
        "remediation_id": outcome.remediation_id,
        "failure_id": outcome.failure_id,
        "remediation_action": outcome.action,
        "remediation_result": outcome.result,
        "recovered": outcome.recovered,
        "dependency": outcome.dependency,
    }
    reasons = (
        reason,
        f"failure_signal={failure_signal}",
        "telemetry is caller-owned and can be fed into the next decision/decomposition cycle",
    )
    return RemediationFeedback(outcome, execution, decomposition, reasons)


def remediation_telemetry(
    failure: FailureAttribution,
    actions: Iterable[RemediationAction],
    outcome: RemediationOutcome | None = None,
) -> dict[str, Any]:
    """Serialize the full failure -> action -> outcome chain."""
    payload: dict[str, Any] = {
        "failure": {
            "failure_id": failure.failure_id,
            "execution_id": failure.execution_id,
            "step_index": failure.step_index,
            "step": failure.step,
            "failure_class": failure.failure_class,
            "dependency": failure.dependency,
            "expected": failure.expected,
            "observed": failure.observed,
            "evidence_confidence": failure.evidence_confidence,
        },
        "actions": [
            {
                "remediation_id": a.remediation_id,
                "failure_id": a.failure_id,
                "action": a.action,
                "target": a.target,
                "preconditions": list(a.preconditions),
                "rationale": a.rationale,
                "priority": a.priority,
            }
            for a in actions
        ],
    }
    if outcome is not None:
        payload["outcome"] = {
            "remediation_id": outcome.remediation_id,
            "failure_id": outcome.failure_id,
            "action": outcome.action,
            "result": outcome.result,
            "recovered": outcome.recovered,
            "execution_id": outcome.execution_id,
            "latency_ms": outcome.latency_ms,
            "evidence_confidence": outcome.evidence_confidence,
            "attempt": outcome.attempt,
            "network_failures": outcome.network_failures,
            "verification_failures": outcome.verification_failures,
            "retries": outcome.retries,
            "dependency": outcome.dependency,
            "details": dict(outcome.details),
        }
    return payload
