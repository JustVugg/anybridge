"""Evidence-driven execution decision fabric.

This module turns execution telemetry into a provider/browser plan plus
verification, concurrency, retry and escalation decisions. It deliberately
does not execute work or persist learning; caller owns those responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .provider_learning import ProviderObservation, rank_providers
from .providers import available_providers


@dataclass(frozen=True)
class ExecutionObservation:
    provider: str
    browser: str | None = None
    success: bool = True
    evidence_confidence: float = 0.5
    latency_ms: float | None = None
    verification_failures: int = 0
    retries: int = 0
    sample_weight: float = 1.0
    task_class: str = "default"


@dataclass(frozen=True)
class ExecutionDecision:
    provider: str
    browser: str
    verification_depth: str
    execution_mode: str
    max_retries: int
    escalation: str
    confidence: float
    reasons: tuple[str, ...]


def _normalize_history(
    history: Iterable[ExecutionObservation | Mapping[str, object]],
) -> tuple[ExecutionObservation, ...]:
    result = []
    for item in history:
        if isinstance(item, ExecutionObservation):
            result.append(item)
            continue
        try:
            result.append(
                ExecutionObservation(
                    provider=str(item["provider"]),
                    browser=None if item.get("browser") is None else str(item["browser"]),
                    success=bool(item.get("success", True)),
                    evidence_confidence=max(0.0, min(1.0, float(item.get("evidence_confidence", 0.5)))),
                    latency_ms=None if item.get("latency_ms") is None else max(0.0, float(item["latency_ms"])),
                    verification_failures=max(0, int(item.get("verification_failures", 0))),
                    retries=max(0, int(item.get("retries", 0))),
                    sample_weight=max(0.0, float(item.get("sample_weight", 1.0))),
                    task_class=str(item.get("task_class", "default")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(result)


def _provider_history(
    observations: Iterable[ExecutionObservation],
) -> tuple[ProviderObservation, ...]:
    return tuple(
        ProviderObservation(
            provider=o.provider,
            success=o.success,
            evidence_confidence=o.evidence_confidence,
            latency_ms=o.latency_ms,
            sample_weight=o.sample_weight,
        )
        for o in observations
    )


def decide_execution(
    *,
    required: Iterable[str] = (),
    browser: str | None = None,
    available: Iterable[str] | None = None,
    history: Iterable[ExecutionObservation | Mapping[str, object]] = (),
    preferred_provider: str | None = None,
    independent_work: int = 1,
    risk: str = "medium",
    destructive: bool = False,
    task_class: str = "default",
) -> ExecutionDecision:
    """Produce a deterministic plan from hard constraints and evidence."""
    observations = _normalize_history(history)
    candidates = rank_providers(
        required=required,
        browser=browser,
        available=available if available is not None else available_providers(),
        history=_provider_history(o for o in observations if o.task_class in {task_class, "default"}),
        preferred=preferred_provider,
    )
    if not candidates:
        raise ValueError("No compatible provider is available")

    selected = candidates[0].provider
    profile_browser = browser
    if profile_browser is None or profile_browser == "auto":
        profile_browser = "chrome" if selected == "selenium" else "chromium"

    task_obs = [o for o in observations if o.task_class in {task_class, "default"}]
    selected_obs = [o for o in task_obs if o.provider == selected]
    avg_conf = (
        sum(o.evidence_confidence * o.sample_weight for o in selected_obs)
        / sum(o.sample_weight for o in selected_obs)
        if selected_obs else candidates[0].evidence_confidence
    )
    failures = sum(o.verification_failures * o.sample_weight for o in selected_obs)
    prior_retries = sum(o.retries * o.sample_weight for o in selected_obs)
    effective_failure_signal = failures + prior_retries

    if destructive or risk == "high" or avg_conf < 0.65 or effective_failure_signal > 0:
        verification = "deep"
    elif risk == "low" and avg_conf >= 0.85:
        verification = "standard"
    else:
        verification = "standard"

    # Parallelize only independent, non-destructive work. Risky browser flows
    # remain serial unless caller explicitly decomposes them into safe units.
    if independent_work > 1 and not destructive and risk != "high":
        execution_mode = "parallel"
    else:
        execution_mode = "serial"

    if effective_failure_signal >= 2 or avg_conf < 0.5:
        max_retries = 2
        escalation = "alternate_provider"
    elif effective_failure_signal > 0:
        max_retries = 1
        escalation = "deeper_verification"
    else:
        max_retries = 0 if avg_conf >= 0.85 else 1
        escalation = "review"

    reasons = [
        "provider selected from capability constraints and historical evidence",
        f"verification={verification} from risk/confidence/failure evidence",
        f"execution={execution_mode} from independence and safety constraints",
        f"retries={max_retries}, escalation={escalation} from observed failure signal",
    ]
    return ExecutionDecision(
        provider=selected,
        browser=profile_browser,
        verification_depth=verification,
        execution_mode=execution_mode,
        max_retries=max_retries,
        escalation=escalation,
        confidence=max(0.0, min(1.0, candidates[0].score)),
        reasons=tuple(reasons),
    )
