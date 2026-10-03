from anybridge.capability_curriculum import CurriculumCandidate
from anybridge.capability_learning_fabric import CapabilityLearningFabric
from anybridge.capability_outcome_attribution import CapabilityOutcome
from anybridge.holdout_benchmark import BenchmarkResult


def outcome(passed, *, candidate_id="c1", execution_id="e1"):
    return CapabilityOutcome(
        capability_id="cap", predicted_confidence=0.9, passed=passed,
        evidence_confidence=0.9, domain="payments", provider="playwright",
        cohort_id="canary", benchmark_family="canary",
        candidate_id=candidate_id, execution_id=execution_id,
    )


def test_fabric_closes_outcome_to_calibration_and_curriculum():
    fabric = CapabilityLearningFabric(curriculum_budget=1)
    fabric.register_candidates([
        CurriculumCandidate("c1", "cap", "payments", novelty=0.3, uncertainty=0.4,
                             failure_risk=0.2, evidence_value=0.9)
    ])
    report = fabric.evaluate([outcome(True), outcome(False, execution_id="e2")])
    assert len(report.outcome_report.attributions) == 2
    assert report.outcome_report.calibration[0].samples == 2
    assert report.next_candidates[0].candidate_id == "c1"
    assert fabric.calibration.result("cap").empirical_rate == 0.5
    assert len(fabric.curriculum.observations()) == 2


def test_fabric_feeds_only_validated_benchmark_evidence_to_promotion():
    fabric = CapabilityLearningFabric()
    valid = BenchmarkResult("cap", "payments", 3, 1.0, 0.95, 3, "validated_holdout",
                            cohort_id="c1", independence_key="c1",
                            benchmark_family="generated_holdout")
    failed = BenchmarkResult("cap", "payments", 3, 0.5, 0.6, 3, "failed_holdout",
                             cohort_id="bad", independence_key="bad",
                             benchmark_family="generated_holdout")
    report = fabric.evaluate([], benchmark_results=[failed, valid])
    assert report.promotion_ready == (valid,)
    assert report.promotion_decision.state.status == "candidate"


def test_fabric_preserves_rejected_outcomes_and_is_bounded():
    fabric = CapabilityLearningFabric(max_outcomes=1)
    report = fabric.evaluate([
        outcome(True), outcome(False, execution_id="e2"),
        CapabilityOutcome(
            capability_id="cap", predicted_confidence=0.9, passed=True,
            domain="payments", provider="", cohort_id="c",
            benchmark_family="canary",
        ),
    ])
    assert len(report.rejected) == 1
    assert [x.execution_id for x in fabric.attribution.outcomes()] == ["e2"]
    assert fabric.export()["version"] == 1
    assert fabric.export()["promotion"]["version"] == 1


def test_fabric_reset_clears_all_learning_state():
    fabric = CapabilityLearningFabric()
    fabric.register_candidates([CurriculumCandidate("c1", "cap", "payments")])
    fabric.evaluate([outcome(True)])
    fabric.reset()
    assert fabric.attribution.outcomes() == ()
    assert fabric.calibration.results() == ()
    assert fabric.curriculum.candidates() == ()
