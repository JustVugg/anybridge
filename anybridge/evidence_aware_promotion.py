"""Evidence-aware capability lifecycle across independent benchmark cohorts.

The lifecycle is report-only. Callers provide the complete evidence history,
including round identifiers, and remain responsible for execution,
authorization, persistence, and reactivation.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict


def _b(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class CohortEvidence:
    capability_id: str
    cohort_id: str
    round_id: str
    passed: bool
    confidence: float = 0.0
    domain: str = ""
    benchmark_family: str = ""
    independence_key: str = ""
    execution_id: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class EvidencePromotionDecision:
    capability_id: str
    status: str
    cohorts: int
    attempts: int
    pass_rate: float
    confidence: float
    decay_rounds: int
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class EvidenceAwarePromotionStore:
    def __init__(
        self,
        *,
        min_cohorts: int = 3,
        min_attempts_per_cohort: int = 1,
        promotion_rate: float = 0.90,
        promotion_confidence: float = 0.80,
        canary_rate: float = 0.90,
        decay_rate: float = 0.75,
        retirement_failures: int = 3,
        max_observations: int = 4096,
    ):
        self.min_cohorts = max(1, int(min_cohorts))
        self.min_attempts_per_cohort = max(1, int(min_attempts_per_cohort))
        self.promotion_rate = _b(promotion_rate)
        self.promotion_confidence = _b(promotion_confidence)
        self.canary_rate = _b(canary_rate)
        self.decay_rate = _b(decay_rate)
        self.retirement_failures = max(1, int(retirement_failures))
        self.max_observations = max(1, int(max_observations))
        self._observations: list[CohortEvidence] = []

    def ingest(self, observations):
        self._observations.extend(
            o for o in observations if o.capability_id and o.cohort_id and o.round_id
        )
        if len(self._observations) > self.max_observations:
            self._observations = self._observations[-self.max_observations:]
        return self.decisions()

    def _groups(self, capability_id):
        groups = defaultdict(list)
        for observation in self._observations:
            if observation.capability_id != capability_id:
                continue
            key = observation.independence_key or (
                f"{observation.domain}|{observation.benchmark_family}|{observation.cohort_id}"
            )
            groups[key].append(observation)
        return groups

    def _round_metrics(self, capability_id):
        rounds = defaultdict(list)
        for observation in self._observations:
            if observation.capability_id == capability_id:
                rounds[observation.round_id].append(observation)
        metrics = []
        for round_id, observations in sorted(rounds.items()):
            passed = sum(int(x.passed) for x in observations)
            rate = passed / len(observations)
            confidence = sum(_b(x.confidence) for x in observations) / len(observations)
            metrics.append((round_id, rate, confidence))
        return metrics

    def decide(self, capability_id: str) -> EvidencePromotionDecision:
        groups = self._groups(capability_id)
        eligible = []
        for key, observations in sorted(groups.items()):
            if len(observations) < self.min_attempts_per_cohort:
                continue
            rate = sum(int(x.passed) for x in observations) / len(observations)
            confidence = sum(_b(x.confidence) for x in observations) / len(observations)
            eligible.append((key, rate, confidence, len(observations)))

        cohorts = len(eligible)
        attempts = sum(x[3] for x in eligible)
        rate = sum(x[1] for x in eligible) / cohorts if cohorts else 0.0
        confidence = sum(x[2] for x in eligible) / cohorts if cohorts else 0.0

        if cohorts < self.min_cohorts:
            return EvidencePromotionDecision(
                capability_id, "candidate", cohorts, attempts, rate, confidence, 0,
                "insufficient independent cohorts",
            )

        weak = [x for x in eligible if x[1] < self.promotion_rate or x[2] < self.promotion_confidence]
        if weak:
            return EvidencePromotionDecision(
                capability_id, "candidate", cohorts, attempts, rate, confidence, 0,
                "weak independent cohort blocks promotion",
            )

        rounds = self._round_metrics(capability_id)
        latest_rate = rounds[-1][1] if rounds else rate
        latest_confidence = rounds[-1][2] if rounds else confidence
        decay_rounds = 0
        for _, round_rate, round_confidence in reversed(rounds):
            if round_rate < self.decay_rate or round_confidence < self.decay_rate:
                decay_rounds += 1
            else:
                break

        if decay_rounds >= self.retirement_failures:
            return EvidencePromotionDecision(
                capability_id, "retired", cohorts, attempts, latest_rate,
                latest_confidence, decay_rounds, "repeated performance decay",
            )
        if latest_rate < self.decay_rate or latest_confidence < self.decay_rate:
            return EvidencePromotionDecision(
                capability_id, "quarantined", cohorts, attempts, latest_rate,
                latest_confidence, decay_rounds, "performance decay detected",
            )
        if len(rounds) >= 2 and latest_rate >= self.canary_rate and latest_confidence >= self.promotion_confidence:
            return EvidencePromotionDecision(
                capability_id, "graduated", cohorts, attempts, latest_rate,
                latest_confidence, decay_rounds, "latest canary round passed",
            )
        return EvidencePromotionDecision(
            capability_id, "canary", cohorts, attempts, latest_rate,
            latest_confidence, decay_rounds, "independent evidence passed; canary evidence required",
        )

    def decisions(self):
        return tuple(
            self.decide(capability_id)
            for capability_id in sorted({x.capability_id for x in self._observations})
        )

    def export(self):
        return {
            "version": 1,
            "observations": [x.to_dict() for x in self._observations],
            "decisions": [x.to_dict() for x in self.decisions()],
        }

    def reset(self):
        self._observations.clear()
