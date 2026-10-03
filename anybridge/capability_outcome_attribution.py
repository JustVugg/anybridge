"""Closed-loop attribution from realized capability outcomes to calibration and curriculum.

This module is report-only: it consumes caller-supplied outcome evidence, attributes
failures across capability/domain/provider/cohort dimensions, and optionally feeds the
existing calibration and curriculum stores. It never executes, authorizes, or persists
external actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _b(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class CapabilityOutcome:
    capability_id: str
    predicted_confidence: float
    passed: bool
    evidence_confidence: float = 0.0
    holdout: bool = False
    independent: bool = False
    domain: str = ""
    provider: str = ""
    cohort_id: str = ""
    benchmark_family: str = ""
    independence_key: str = ""
    candidate_id: str = ""
    execution_id: str = ""
    details: str = ""

    def provenance_key(self) -> str:
        return self.independence_key or self.cohort_id


@dataclass(frozen=True)
class OutcomeAttribution:
    capability_id: str
    outcome: str
    predicted_confidence: float
    realized_success: float
    calibration_error: float
    dimensions: tuple[str, ...]
    provenance: str
    details: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class OutcomeAttributionReport:
    attributions: tuple[OutcomeAttribution, ...]
    calibration: tuple[object, ...]
    curriculum: tuple[object, ...]
    rejected: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "attributions": [x.to_dict() for x in self.attributions],
            "calibration": [x.to_dict() if hasattr(x, "to_dict") else x for x in self.calibration],
            "curriculum": [x.to_dict() if hasattr(x, "to_dict") else x for x in self.curriculum],
            "rejected": list(self.rejected),
        }


class CapabilityOutcomeAttributionStore:
    """Bounded deterministic outcome attribution and learning handoff."""

    def __init__(self, *, max_outcomes: int = 4096):
        self.max_outcomes = max(1, int(max_outcomes))
        self._outcomes: list[CapabilityOutcome] = []

    def observe(
        self,
        outcome: CapabilityOutcome,
        *,
        calibration_store=None,
        curriculum_store=None,
    ) -> OutcomeAttribution:
        self._validate(outcome)
        self._outcomes.append(outcome)
        if len(self._outcomes) > self.max_outcomes:
            del self._outcomes[:-self.max_outcomes]

        predicted = _b(outcome.predicted_confidence)
        realized = float(outcome.passed)
        dimensions = (
            f"capability:{outcome.capability_id}",
            f"domain:{outcome.domain}",
            f"provider:{outcome.provider}",
            f"cohort:{outcome.provenance_key()}",
            f"benchmark_family:{outcome.benchmark_family}",
        )
        attribution = OutcomeAttribution(
            capability_id=outcome.capability_id,
            outcome="passed" if outcome.passed else "failed",
            predicted_confidence=predicted,
            realized_success=realized,
            calibration_error=abs(predicted - realized),
            dimensions=dimensions,
            provenance=outcome.provenance_key(),
            details=outcome.details,
        )

        if calibration_store is not None:
            from .capability_calibration import CalibrationObservation
            calibration_store.observe(CalibrationObservation(
                outcome.capability_id,
                predicted,
                outcome.passed,
                holdout=outcome.holdout,
                evidence_confidence=_b(outcome.evidence_confidence),
                eligible=True,
            ))

        if curriculum_store is not None and outcome.candidate_id:
            from .capability_curriculum import CurriculumObservation
            curriculum_store.observe(CurriculumObservation(
                outcome.candidate_id,
                outcome.passed,
                holdout=outcome.holdout,
                evidence_confidence=_b(outcome.evidence_confidence),
                details=outcome.details,
            ))
        return attribution

    def report(
        self,
        outcomes: Iterable[CapabilityOutcome],
        *,
        calibration_store=None,
        curriculum_store=None,
    ) -> OutcomeAttributionReport:
        attributions = []
        rejected = []
        for outcome in outcomes:
            try:
                attributions.append(self.observe(
                    outcome,
                    calibration_store=calibration_store,
                    curriculum_store=curriculum_store,
                ))
            except (ValueError, KeyError) as exc:
                rejected.append(str(exc))
        calibration = tuple(calibration_store.results()) if calibration_store is not None else ()
        curriculum = tuple(curriculum_store.rank()) if curriculum_store is not None else ()
        return OutcomeAttributionReport(tuple(attributions), calibration, curriculum, tuple(rejected))

    def outcomes(self) -> tuple[CapabilityOutcome, ...]:
        return tuple(self._outcomes)

    def export(self) -> dict:
        return {
            "version": 1,
            "outcomes": [x.__dict__.copy() for x in self._outcomes],
        }

    def reset(self) -> None:
        self._outcomes.clear()

    @staticmethod
    def _validate(outcome: CapabilityOutcome) -> None:
        if not outcome.capability_id:
            raise ValueError("capability_id is required")
        if not outcome.provider:
            raise ValueError("provider provenance is required")
        if not outcome.domain:
            raise ValueError("domain provenance is required")
        if not outcome.benchmark_family:
            raise ValueError("benchmark_family provenance is required")
        if not outcome.cohort_id and not outcome.independence_key:
            raise ValueError("cohort_id or independence_key provenance is required")
        if outcome.holdout and not outcome.independent:
            raise ValueError("holdout outcome must explicitly declare independence")
