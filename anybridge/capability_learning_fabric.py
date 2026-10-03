"""Unified capability learning fabric.

Composes outcome attribution, calibration, curriculum, benchmark evidence, and
canonical promotion into one bounded deterministic report. It never executes
capabilities, authorizes actions, or persists externally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class LearningFabricReport:
    outcome_report: object
    next_candidates: tuple
    promotion_ready: tuple
    promotion_decision: object | None
    rejected: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "outcome_report": self.outcome_report.to_dict(),
            "next_candidates": [x.to_dict() for x in self.next_candidates],
            "promotion_ready": [
                x.to_dict() if hasattr(x, "to_dict") else x for x in self.promotion_ready
            ],
            "promotion_decision": (
                self.promotion_decision.to_dict()
                if self.promotion_decision is not None else None
            ),
            "rejected": list(self.rejected),
        }


class CapabilityLearningFabric:
    """Bounded orchestration over canonical capability-learning components."""

    def __init__(self, *, max_outcomes: int = 4096, curriculum_budget: int = 3):
        from .capability_calibration import CapabilityCalibrationStore
        from .capability_curriculum import CapabilityCurriculumStore
        from .capability_outcome_attribution import CapabilityOutcomeAttributionStore
        from .capability_promotion import CapabilityPromotionStore

        self.calibration = CapabilityCalibrationStore()
        self.curriculum = CapabilityCurriculumStore()
        self.attribution = CapabilityOutcomeAttributionStore(max_outcomes=max_outcomes)
        self.promotion = CapabilityPromotionStore()
        self.curriculum_budget = max(1, int(curriculum_budget))

    def register_candidates(self, candidates: Iterable[object]) -> None:
        for candidate in candidates:
            self.curriculum.register(candidate)

    def ingest_outcomes(self, outcomes: Iterable[object]):
        return self.attribution.report(
            outcomes,
            calibration_store=self.calibration,
            curriculum_store=self.curriculum,
        )

    def evaluate(
        self,
        outcomes: Iterable[object],
        *,
        benchmark_results: Iterable[object] = (),
    ) -> LearningFabricReport:
        outcome_report = self.ingest_outcomes(outcomes)
        next_candidates = self.curriculum.rank(limit=self.curriculum_budget)
        promotion_ready = tuple(
            result for result in benchmark_results
            if getattr(result, "status", "") == "validated_holdout"
        )
        promotion_decision = None
        rejected = list(outcome_report.rejected)
        if promotion_ready:
            try:
                promotion_decision = self.promotion.evaluate_benchmark_results(
                    promotion_ready
                )
            except ValueError as exc:
                rejected.append(str(exc))
        return LearningFabricReport(
            outcome_report=outcome_report,
            next_candidates=next_candidates,
            promotion_ready=promotion_ready,
            promotion_decision=promotion_decision,
            rejected=tuple(rejected),
        )

    def export(self) -> dict:
        return {
            "version": 1,
            "attribution": self.attribution.export(),
            "calibration": self.calibration.export(),
            "curriculum": self.curriculum.export(),
            "promotion": self.promotion.export(),
        }

    def reset(self) -> None:
        self.attribution.reset()
        self.calibration.reset()
        self.curriculum.reset()
        self.promotion.reset()
