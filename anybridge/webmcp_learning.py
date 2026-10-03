"""Adaptive, caller-owned learning loop for WebMCP security regressions.

The loop records security outcomes, correlates failures by attack class, provider,
origin, schema fingerprint and defense, and promotes repeatedly observed failures
into a bounded regression corpus. Promotion is deterministic and local: AnyBridge
does not call an LLM, persist externally, execute remediation, or depend on HWS.

A promoted case is a regression seed, not an instruction. The caller can export
the corpus to durable storage or a source-controlled fixture when desired.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .webmcp_security import ATTACK_CORPUS, SecurityEvaluation


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return repr(value)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SecurityObservation:
    """One realized adversarial/security outcome."""

    attack_id: str
    attack_class: str
    provider: str
    origin: str
    schema_hash: str
    defense: str
    passed: bool
    evidence_confidence: float = 0.5
    latency_ms: float | None = None
    execution_id: str | None = None
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["details"] = dict(self.details or {})
        return data


@dataclass(frozen=True)
class SecurityFailurePattern:
    """Aggregated failure signature used to decide regression promotion."""

    attack_class: str
    provider: str
    origin: str
    schema_hash: str
    defense: str
    failures: int
    samples: int
    failure_rate: float
    confidence: float
    first_seen: int
    last_seen: int

    @property
    def signature(self) -> str:
        return _hash({
            "attack_class": self.attack_class,
            "provider": self.provider,
            "origin": self.origin,
            "schema_hash": self.schema_hash,
            "defense": self.defense,
        })

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["signature"] = self.signature
        return data


@dataclass(frozen=True)
class PromotedSecurityCase:
    """A bounded regression seed promoted from observed security failures."""

    case_id: str
    attack_id: str
    attack_class: str
    provider: str
    origin: str
    schema_hash: str
    defense: str
    source: str
    reason: str
    promoted_at: int
    regression_payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["regression_payload"] = dict(self.regression_payload)
        return data


@dataclass(frozen=True)
class SecurityLearningReport:
    observations: int
    patterns: tuple[SecurityFailurePattern, ...]
    promoted: tuple[PromotedSecurityCase, ...]
    corpus_size: int
    newly_promoted: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "patterns": [p.to_dict() for p in self.patterns],
            "promoted": [p.to_dict() for p in self.promoted],
            "corpus_size": self.corpus_size,
            "newly_promoted": self.newly_promoted,
        }


def schema_fingerprint(schema: object) -> str:
    return _hash(schema)


class SecurityLearningStore:
    """Bounded in-memory security learning store.

    Persistence is intentionally caller-owned. The store can be exported and
    replayed elsewhere without coupling AnyBridge to a database, orchestrator,
    model provider, or project-specific memory system.
    """

    def __init__(
        self,
        *,
        max_observations: int = 2048,
        max_promoted_cases: int = 512,
        promotion_min_failures: int = 2,
        promotion_min_failure_rate: float = 0.5,
    ) -> None:
        self.max_observations = max(1, int(max_observations))
        self.max_promoted_cases = max(1, int(max_promoted_cases))
        self.promotion_min_failures = max(1, int(promotion_min_failures))
        self.promotion_min_failure_rate = max(0.0, min(1.0, float(promotion_min_failure_rate)))
        self._observations: list[tuple[int, SecurityObservation]] = []
        self._patterns: dict[str, SecurityFailurePattern] = {}
        self._promoted: dict[str, PromotedSecurityCase] = {}
        self._clock = 0

    @staticmethod
    def _signature(observation: SecurityObservation) -> str:
        return _hash({
            "attack_class": observation.attack_class,
            "provider": observation.provider,
            "origin": observation.origin,
            "schema_hash": observation.schema_hash,
            "defense": observation.defense,
        })

    def _promotable(
        self,
        pattern: SecurityFailurePattern,
    ) -> bool:
        return (
            pattern.failures >= self.promotion_min_failures
            and pattern.failure_rate >= self.promotion_min_failure_rate
        )

    def observe(
        self,
        observation: SecurityObservation,
        *,
        regression_payload: Mapping[str, Any] | None = None,
    ) -> SecurityLearningReport:
        self._clock += 1
        confidence = max(0.0, min(1.0, float(observation.evidence_confidence)))
        normalized = SecurityObservation(
            attack_id=str(observation.attack_id),
            attack_class=str(observation.attack_class),
            provider=str(observation.provider),
            origin=str(observation.origin),
            schema_hash=str(observation.schema_hash),
            defense=str(observation.defense),
            passed=bool(observation.passed),
            evidence_confidence=confidence,
            latency_ms=None if observation.latency_ms is None else max(0.0, float(observation.latency_ms)),
            execution_id=observation.execution_id,
            details=dict(observation.details or {}),
        )
        self._observations.append((self._clock, normalized))
        if len(self._observations) > self.max_observations:
            self._observations = self._observations[-self.max_observations:]

        signature = self._signature(normalized)
        prior = self._patterns.get(signature)
        failures = (prior.failures if prior else 0) + (0 if normalized.passed else 1)
        samples = (prior.samples if prior else 0) + 1
        pattern = SecurityFailurePattern(
            attack_class=normalized.attack_class,
            provider=normalized.provider,
            origin=normalized.origin,
            schema_hash=normalized.schema_hash,
            defense=normalized.defense,
            failures=failures,
            samples=samples,
            failure_rate=failures / samples,
            confidence=(
                (prior.confidence * (prior.samples or 0) + confidence) / samples
                if prior else confidence
            ),
            first_seen=prior.first_seen if prior else self._clock,
            last_seen=self._clock,
        )
        self._patterns[signature] = pattern

        if self._promotable(pattern):
            case_id = f"sec-{pattern.signature}"
            if case_id not in self._promoted:
                payload = dict(regression_payload or {})
                payload.setdefault("attack_id", normalized.attack_id)
                payload.setdefault("attack_class", normalized.attack_class)
                payload.setdefault("provider", normalized.provider)
                payload.setdefault("origin", normalized.origin)
                payload.setdefault("schema_hash", normalized.schema_hash)
                payload.setdefault("defense", normalized.defense)
                promoted = PromotedSecurityCase(
                    case_id=case_id,
                    attack_id=normalized.attack_id,
                    attack_class=normalized.attack_class,
                    provider=normalized.provider,
                    origin=normalized.origin,
                    schema_hash=normalized.schema_hash,
                    defense=normalized.defense,
                    source="observed_failure",
                    reason=(
                        f"promoted after {pattern.failures}/{pattern.samples} failures "
                        f"for stable security signature {pattern.signature}"
                    ),
                    promoted_at=self._clock,
                    regression_payload=payload,
                )
                self._promoted[case_id] = promoted
                while len(self._promoted) > self.max_promoted_cases:
                    oldest = min(self._promoted, key=lambda k: self._promoted[k].promoted_at)
                    del self._promoted[oldest]

        return self.report(newly_promoted=1 if any(
            p.promoted_at == self._clock for p in self._promoted.values()
        ) else 0)

    def patterns(self) -> tuple[SecurityFailurePattern, ...]:
        return tuple(sorted(self._patterns.values(), key=lambda p: p.signature))

    def promoted_cases(self) -> tuple[PromotedSecurityCase, ...]:
        return tuple(sorted(self._promoted.values(), key=lambda p: p.case_id))

    def corpus(self) -> tuple[dict[str, Any], ...]:
        """Return built-in cases plus promoted regression seeds, deduplicated by case id."""
        result: list[dict[str, Any]] = [dict(case) for case in ATTACK_CORPUS]
        existing = {str(case.get("id")) for case in result}
        for case in self.promoted_cases():
            if case.case_id in existing:
                continue
            payload = dict(case.regression_payload)
            payload["id"] = case.case_id
            payload.setdefault("category", case.attack_class)
            payload["source"] = case.source
            payload["promotion_reason"] = case.reason
            result.append(payload)
        return tuple(result)

    def report(self, *, newly_promoted: int = 0) -> SecurityLearningReport:
        return SecurityLearningReport(
            observations=len(self._observations),
            patterns=self.patterns(),
            promoted=self.promoted_cases(),
            corpus_size=len(self.corpus()),
            newly_promoted=newly_promoted,
        )

    def export(self) -> dict[str, Any]:
        return {
            "version": 1,
            "observations": [
                {"sequence": sequence, **observation.to_dict()}
                for sequence, observation in self._observations
            ],
            "patterns": [pattern.to_dict() for pattern in self.patterns()],
            "promoted": [case.to_dict() for case in self.promoted_cases()],
            "corpus": list(self.corpus()),
        }

    def reset(self) -> None:
        self._observations.clear()
        self._patterns.clear()
        self._promoted.clear()
        self._clock = 0


def observation_from_evaluation(
    evaluation: SecurityEvaluation,
    *,
    provider: str,
    origin: str,
    schema: object,
    defense: str,
    attack_id: str | None = None,
) -> tuple[SecurityObservation, ...]:
    """Convert an evaluation into caller-ready learning observations."""
    failed_ids = {finding.attack_id: finding for finding in evaluation.failures}
    observations: list[SecurityObservation] = []
    for case in ATTACK_CORPUS:
        current_id = str(case["id"])
        if attack_id is not None and current_id != attack_id:
            continue
        finding = failed_ids.get(current_id)
        passed = finding is None
        observations.append(
            SecurityObservation(
                attack_id=current_id,
                attack_class=str(case["category"]),
                provider=provider,
                origin=origin,
                schema_hash=schema_fingerprint(schema),
                defense=defense,
                passed=passed,
                evidence_confidence=1.0 if passed else 0.25,
                details={"reason": finding.reason if finding else "security regression passed"},
            )
        )
    return tuple(observations)
