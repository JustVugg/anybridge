"""Failure-aware decomposition suggestions for the execution fabric.

This module is advisory only. caller remains responsible for decomposition,
scheduling, execution, persistence and learning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .execution_fabric import ExecutionObservation


@dataclass(frozen=True)
class DecompositionObservation:
    """Task-level telemetry used to derive conservative decomposition advice."""

    independent_work: int = 1
    verification_failures: int = 0
    retries: int = 0
    network_failures: int = 0
    evidence_confidence: float = 0.5
    duration_ms: float | None = None
    task_class: str = "default"

    @property
    def failure_signal(self) -> int:
        return max(0, self.verification_failures) + max(0, self.retries) + max(0, self.network_failures)


@dataclass(frozen=True)
class DecompositionDecision:
    """Declarative decomposition/remediation recommendation."""

    strategy: str
    suggested_parallelism: int
    verification_depth: str
    preconditions: tuple[str, ...]
    escalation: str
    confidence: float
    reasons: tuple[str, ...]


def _normalize(item: DecompositionObservation | Mapping[str, object]) -> DecompositionObservation | None:
    if isinstance(item, DecompositionObservation):
        return item
    try:
        return DecompositionObservation(
            independent_work=max(1, int(item.get("independent_work", 1))),
            verification_failures=max(0, int(item.get("verification_failures", 0))),
            retries=max(0, int(item.get("retries", 0))),
            network_failures=max(0, int(item.get("network_failures", 0))),
            evidence_confidence=max(0.0, min(1.0, float(item.get("evidence_confidence", 0.5)))),
            duration_ms=None if item.get("duration_ms") is None else max(0.0, float(item["duration_ms"])),
            task_class=str(item.get("task_class", "default")),
        )
    except (TypeError, ValueError):
        return None


def _history(
    observations: Iterable[DecompositionObservation | Mapping[str, object]],
    task_class: str,
) -> tuple[DecompositionObservation, ...]:
    result = []
    for item in observations:
        observation = _normalize(item)
        if observation is not None and observation.task_class in {task_class, "default"}:
            result.append(observation)
    return tuple(result)


def recommend_decomposition(
    *,
    independent_work: int = 1,
    risk: str = "medium",
    destructive: bool = False,
    history: Iterable[DecompositionObservation | Mapping[str, object]] = (),
    task_class: str = "default",
    max_parallelism: int | None = None,
) -> DecompositionDecision:
    """Recommend how caller should split or stage work using observed failures."""
    observations = _history(history, task_class)
    failure_signal = sum(o.failure_signal for o in observations)
    network_signal = sum(o.network_failures for o in observations)
    low_confidence = (
        sum(o.evidence_confidence for o in observations) / len(observations)
        if observations else 0.5
    ) < 0.65

    cap = max(1, max_parallelism or independent_work)
    preconditions: list[str] = []
    reasons: list[str] = ["recommendation is advisory; caller retains execution authority"]

    if destructive or risk == "high":
        strategy = "stage_with_preconditions"
        parallelism = 1
        verification = "deep"
        preconditions.extend(("validate_target", "capture_pre_state", "verify_after_each_stage"))
        escalation = "human_review"
        reasons.append("high-risk/destructive work requires staged execution")
    elif network_signal > 0:
        strategy = "isolate_network_dependencies"
        parallelism = 1 if independent_work <= 1 else min(cap, max(1, independent_work // 2))
        verification = "deep"
        preconditions.extend(("validate_network_policy", "record_blocked_dependencies"))
        escalation = "network_review"
        reasons.append("network failures indicate dependency isolation/preflight value")
    elif failure_signal >= 2 or low_confidence:
        strategy = "split_and_verify"
        parallelism = 1 if independent_work <= 1 else min(cap, max(1, independent_work // 2))
        verification = "deep"
        preconditions.extend(("define_step_boundaries", "verify_each_partition"))
        escalation = "alternate_provider"
        reasons.append("repeated failures or weak evidence favor smaller verified partitions")
    elif independent_work > 1:
        strategy = "parallel_independent"
        parallelism = min(cap, independent_work)
        verification = "standard"
        preconditions.append("confirm_work_units_are_independent")
        escalation = "review"
        reasons.append("independent work can remain parallel")
    else:
        strategy = "single_unit"
        parallelism = 1
        verification = "standard"
        preconditions.append("validate_task_preconditions")
        escalation = "review"
        reasons.append("single work unit does not benefit from decomposition")

    confidence = max(0.0, min(1.0, 0.5 + min(0.4, len(observations) * 0.05)))
    return DecompositionDecision(
        strategy=strategy,
        suggested_parallelism=parallelism,
        verification_depth=verification,
        preconditions=tuple(preconditions),
        escalation=escalation,
        confidence=confidence,
        reasons=tuple(reasons),
    )


def decomposition_telemetry(
    decision: DecompositionDecision,
    *,
    task_id: str | None = None,
    execution_id: str | None = None,
) -> dict[str, object]:
    """Return a stable record for caller evidence/learning ingestion."""
    return {
        "task_id": task_id,
        "execution_id": execution_id,
        "strategy": decision.strategy,
        "suggested_parallelism": decision.suggested_parallelism,
        "verification_depth": decision.verification_depth,
        "preconditions": list(decision.preconditions),
        "escalation": decision.escalation,
        "predicted_confidence": decision.confidence,
        "reasons": list(decision.reasons),
    }
