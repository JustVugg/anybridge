"""Resource-aware, counterfactual extensions for the execution decision fabric.

The fabric remains declarative: it returns a plan and alternatives, while caller
owns execution, persistence, scheduling and learning.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .execution_fabric import ExecutionDecision, ExecutionObservation, decide_execution
from .provider_learning import rank_providers


@dataclass(frozen=True)
class ResourceObservation:
    """Optional runtime capacity signals. Missing values are intentionally safe."""

    context_budget: float | None = None
    estimated_duration_ms: float | None = None
    cpu_available: float | None = None
    memory_available_mb: float | None = None
    queue_depth: int = 0
    concurrency_capacity: int = 1
    evidence_value: float = 0.5

    def normalized(self) -> "ResourceObservation":
        return replace(
            self,
            context_budget=None if self.context_budget is None else max(0.0, self.context_budget),
            estimated_duration_ms=None
            if self.estimated_duration_ms is None
            else max(0.0, self.estimated_duration_ms),
            cpu_available=None if self.cpu_available is None else max(0.0, min(1.0, self.cpu_available)),
            memory_available_mb=None
            if self.memory_available_mb is None
            else max(0.0, self.memory_available_mb),
            queue_depth=max(0, self.queue_depth),
            concurrency_capacity=max(1, self.concurrency_capacity),
            evidence_value=max(0.0, min(1.0, self.evidence_value)),
        )


@dataclass(frozen=True)
class CounterfactualPlan:
    """A safe alternative considered before execution."""

    name: str
    decision: ExecutionDecision
    rationale: str


def _resource_mode(
    decision: ExecutionDecision,
    resources: ResourceObservation | None,
    *,
    independent_work: int,
    destructive: bool,
    risk: str,
) -> tuple[str, str]:
    if resources is None:
        return decision.execution_mode, "no resource telemetry supplied"

    r = resources.normalized()
    if destructive or risk == "high":
        return "serial", "safety gate overrides resource pressure"

    if independent_work <= 1:
        return "serial", "only one independent work unit"

    constrained = (
        r.queue_depth >= r.concurrency_capacity
        or (r.cpu_available is not None and r.cpu_available < 0.20)
        or (r.memory_available_mb is not None and r.memory_available_mb < 512)
    )
    if constrained:
        return "serial", "resource pressure limits safe concurrency"

    if r.evidence_value >= 0.8 and r.context_budget is not None and r.context_budget < 2000:
        return "serial", "high-value evidence with constrained context budget"

    return "parallel", "resources support independent parallel work"


def decide_execution_adaptive(
    *,
    required: Iterable[str] = (),
    browser: str | None = None,
    available: Iterable[str] | None = None,
    history: Iterable[ExecutionObservation | dict[str, object]] = (),
    preferred_provider: str | None = None,
    independent_work: int = 1,
    risk: str = "medium",
    destructive: bool = False,
    task_class: str = "default",
    resources: ResourceObservation | None = None,
) -> tuple[ExecutionDecision, tuple[CounterfactualPlan, ...]]:
    """Choose a primary plan and expose safe alternatives for caller evaluation."""
    primary = decide_execution(
        required=required,
        browser=browser,
        available=available,
        history=history,
        preferred_provider=preferred_provider,
        independent_work=independent_work,
        risk=risk,
        destructive=destructive,
        task_class=task_class,
    )
    mode, mode_reason = _resource_mode(
        primary,
        resources,
        independent_work=independent_work,
        destructive=destructive,
        risk=risk,
    )
    if mode != primary.execution_mode:
        primary = replace(
            primary,
            execution_mode=mode,
            reasons=primary.reasons + (f"resource_mode={mode}: {mode_reason}",),
        )

    alternatives: list[CounterfactualPlan] = []
    if independent_work > 1 and not destructive and risk != "high":
        alternate_mode = "serial" if primary.execution_mode == "parallel" else "parallel"
        alternatives.append(
            CounterfactualPlan(
                name=f"{alternate_mode}_execution",
                decision=replace(primary, execution_mode=alternate_mode),
                rationale="counterfactual concurrency branch retained for outcome comparison",
            )
        )

    if primary.verification_depth == "standard":
        alternatives.append(
            CounterfactualPlan(
                name="deep_verification",
                decision=replace(primary, verification_depth="deep", max_retries=max(1, primary.max_retries)),
                rationale="counterfactual higher-evidence branch for low-confidence outcomes",
            )
        )

    if primary.escalation != "alternate_provider":
        candidates = rank_providers(
            required=required,
            browser=browser,
            available=available,
            history=(),
            preferred=preferred_provider,
        )
        alternate = next((c for c in candidates if c.provider != primary.provider), None)
        if alternate is not None:
            alternatives.append(
                CounterfactualPlan(
                    name="alternate_provider",
                    decision=replace(
                        primary,
                        provider=alternate.provider,
                        browser=("chrome" if alternate.provider == "selenium" else "chromium")
                        if browser in (None, "auto")
                        else browser,
                        escalation="alternate_provider",
                        max_retries=max(1, primary.max_retries),
                    ),
                    rationale="counterfactual compatible provider fallback after execution failure",
                )
            )

    return primary, tuple(alternatives)


def decision_telemetry(
    decision: ExecutionDecision,
    *,
    execution_id: str | None = None,
    test_id: str | None = None,
    resources: ResourceObservation | None = None,
) -> dict[str, object]:
    """Emit a stable telemetry shape that caller can merge into its evidence envelope."""
    payload: dict[str, object] = {
        "execution_id": execution_id,
        "test_id": test_id,
        "provider": decision.provider,
        "browser": decision.browser,
        "verification_depth": decision.verification_depth,
        "execution_mode": decision.execution_mode,
        "max_retries": decision.max_retries,
        "escalation": decision.escalation,
        "predicted_confidence": decision.confidence,
        "reasons": list(decision.reasons),
    }
    if resources is not None:
        r = resources.normalized()
        payload["resources"] = {
            "context_budget": r.context_budget,
            "estimated_duration_ms": r.estimated_duration_ms,
            "cpu_available": r.cpu_available,
            "memory_available_mb": r.memory_available_mb,
            "queue_depth": r.queue_depth,
            "concurrency_capacity": r.concurrency_capacity,
            "evidence_value": r.evidence_value,
        }
    return payload
