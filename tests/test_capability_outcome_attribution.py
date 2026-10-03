from anybridge.capability_calibration import CapabilityCalibrationStore
from anybridge.capability_curriculum import CapabilityCurriculumStore, CurriculumCandidate
from anybridge.capability_outcome_attribution import (
    CapabilityOutcome,
    CapabilityOutcomeAttributionStore,
)


def _outcome(**overrides):
    data = {
        "capability_id": "cap",
        "predicted_confidence": 0.9,
        "passed": True,
        "evidence_confidence": 0.95,
        "holdout": False,
        "independent": False,
        "domain": "payments",
        "provider": "playwright",
        "cohort_id": "canary-1",
        "benchmark_family": "canary",
        "candidate_id": "candidate-1",
        "execution_id": "exec-1",
    }
    data.update(overrides)
    return CapabilityOutcome(**data)


def test_attribution_keeps_learning_dimensions_and_realized_error():
    store = CapabilityOutcomeAttributionStore()
    result = store.observe(_outcome(passed=False, predicted_confidence=0.9))
    assert result.outcome == "failed"
    assert result.realized_success == 0.0
    assert result.calibration_error == 0.9
    assert "capability:cap" in result.dimensions
    assert "domain:payments" in result.dimensions
    assert "provider:playwright" in result.dimensions
    assert "cohort:canary-1" in result.dimensions
    assert "benchmark_family:canary" in result.dimensions


def test_canary_outcomes_feed_calibration_without_being_called_holdout():
    calibration = CapabilityCalibrationStore(min_samples=1)
    store = CapabilityOutcomeAttributionStore()
    store.report([
        _outcome(passed=True, predicted_confidence=0.9),
        _outcome(passed=False, predicted_confidence=0.9, execution_id="exec-2"),
    ], calibration_store=calibration)
    result = calibration.result("cap")
    assert result.samples == 2
    assert result.empirical_rate == 0.5
    assert result.mean_predicted == 0.9
    assert result.corrected_confidence == 0.7


def test_outcome_feedback_updates_curriculum():
    curriculum = CapabilityCurriculumStore()
    curriculum.register(CurriculumCandidate(
        "candidate-1", "cap", "payments", novelty=0.2,
        uncertainty=0.3, failure_risk=0.2, evidence_value=0.8,
    ))
    store = CapabilityOutcomeAttributionStore()
    store.observe(_outcome(passed=False), curriculum_store=curriculum)
    assert len(curriculum.observations()) == 1
    assert curriculum.observations()[0].candidate_id == "candidate-1"
    assert curriculum.observations()[0].passed is False


def test_missing_provenance_is_rejected():
    store = CapabilityOutcomeAttributionStore()
    try:
        store.observe(_outcome(cohort_id="", independence_key=""))
    except ValueError as exc:
        assert "provenance" in str(exc)
    else:
        raise AssertionError("missing cohort provenance must be rejected")


def test_holdout_requires_explicit_independence():
    store = CapabilityOutcomeAttributionStore()
    try:
        store.observe(_outcome(holdout=True, independent=False))
    except ValueError as exc:
        assert "independence" in str(exc)
    else:
        raise AssertionError("non-independent holdout must be rejected")


def test_bounded_history_export_and_reset_are_deterministic():
    store = CapabilityOutcomeAttributionStore(max_outcomes=2)
    store.observe(_outcome(execution_id="1"))
    store.observe(_outcome(execution_id="2"))
    store.observe(_outcome(execution_id="3"))
    assert [x.execution_id for x in store.outcomes()] == ["2", "3"]
    assert store.export()["version"] == 1
    assert len(store.export()["outcomes"]) == 2
    store.reset()
    assert store.outcomes() == ()


def test_report_rejects_bad_outcomes_without_executing_them():
    store = CapabilityOutcomeAttributionStore()
    report = store.report([
        _outcome(provider=""),
        _outcome(execution_id="valid"),
    ])
    assert len(report.attributions) == 1
    assert len(report.rejected) == 1
    assert report.attributions[0].capability_id == "cap"
