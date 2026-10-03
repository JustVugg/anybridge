"""Evidence-driven provider routing primitives for AnyBridge and caller.

The router consumes historical observations supplied by the caller. It owns no
persistent storage, so caller remains the source of truth for long-lived learning.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping

from .providers import ProviderProfile, available_providers, provider_profiles


@dataclass(frozen=True)
class ProviderObservation:
    """Historical outcome for one provider under a task/capability profile."""

    provider: str
    success: bool
    evidence_confidence: float = 0.0
    latency_ms: float | None = None
    sample_weight: float = 1.0


@dataclass(frozen=True)
class ProviderScore:
    provider: str
    score: float
    samples: float
    success_rate: float
    evidence_confidence: float
    latency_ms: float | None
    reason: tuple[str, ...]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _history_for(
    history: Iterable[ProviderObservation | Mapping[str, object]],
    provider: str,
) -> list[ProviderObservation]:
    observations: list[ProviderObservation] = []
    for item in history:
        if isinstance(item, ProviderObservation):
            observation = item
        else:
            try:
                observation = ProviderObservation(
                    provider=str(item["provider"]),
                    success=bool(item["success"]),
                    evidence_confidence=float(item.get("evidence_confidence", 0.0)),
                    latency_ms=(
                        None
                        if item.get("latency_ms") is None
                        else float(item["latency_ms"])
                    ),
                    sample_weight=max(0.0, float(item.get("sample_weight", 1.0))),
                )
            except (KeyError, TypeError, ValueError):
                continue
        if observation.provider == provider and observation.sample_weight > 0:
            observations.append(observation)
    return observations


def score_provider(
    profile: ProviderProfile,
    history: Iterable[ProviderObservation | Mapping[str, object]] = (),
) -> ProviderScore:
    observations = _history_for(history, profile.name)
    if not observations:
        return ProviderScore(
            profile.name, 0.5, 0.0, 0.5, 0.5, None,
            ("no historical evidence; neutral prior",),
        )

    total = sum(o.sample_weight for o in observations)
    success_rate = sum(
        o.sample_weight * (1.0 if o.success else 0.0) for o in observations
    ) / total
    confidence = sum(
        o.sample_weight * _clamp(o.evidence_confidence) for o in observations
    ) / total
    latencies = [
        o.latency_ms
        for o in observations
        if o.latency_ms is not None and isfinite(o.latency_ms) and o.latency_ms >= 0
    ]
    latency = sum(latencies) / len(latencies) if latencies else None

    # Reliability dominates; evidence quality is the second signal. Latency is
    # deliberately bounded so a single slow run cannot erase a reliable route.
    latency_score = 0.5 if latency is None else 1.0 / (1.0 + latency / 5000.0)
    score = 0.55 * success_rate + 0.30 * confidence + 0.15 * latency_score
    return ProviderScore(
        profile.name,
        score,
        total,
        success_rate,
        confidence,
        latency,
        ("historical outcomes applied",),
    )


def rank_providers(
    required: Iterable[str] = (),
    browser: str | None = None,
    available: Iterable[str] | None = None,
    history: Iterable[ProviderObservation | Mapping[str, object]] = (),
    preferred: str | None = None,
) -> tuple[ProviderScore, ...]:
    """Return compatible providers ordered by learned evidence score."""
    required = frozenset(required)
    available_set = (
        frozenset(available)
        if available is not None
        else frozenset(available_providers())
    )
    candidates = [
        p for p in provider_profiles()
        if p.name in available_set and required <= p.capabilities
        and (browser is None or browser in p.browsers)
    ]
    scores = [score_provider(p, history) for p in candidates]
    scores.sort(
        key=lambda s: (s.score, s.provider == preferred, s.samples),
        reverse=True,
    )
    return tuple(scores)


def select_learned_provider(
    required: Iterable[str] = (),
    browser: str | None = None,
    available: Iterable[str] | None = None,
    history: Iterable[ProviderObservation | Mapping[str, object]] = (),
    preferred: str | None = None,
) -> str:
    ranked = rank_providers(required, browser, available, history, preferred)
    if not ranked:
        raise ValueError(
            f"No provider satisfies capabilities={sorted(frozenset(required))!r}, "
            f"browser={browser!r}, available={sorted(frozenset(available or ()))!r}"
        )
    return ranked[0].provider
