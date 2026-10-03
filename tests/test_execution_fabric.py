from anybridge.execution_fabric import ExecutionObservation, decide_execution


def test_fabric_parallelizes_safe_independent_work():
    decision = decide_execution(
        required=["bdd"],
        available=["playwright", "selenium"],
        independent_work=3,
        risk="low",
        history=[
            ExecutionObservation("playwright", success=True, evidence_confidence=0.95, latency_ms=1000),
            ExecutionObservation("selenium", success=True, evidence_confidence=0.70, latency_ms=1500),
        ],
    )
    assert decision.execution_mode == "parallel"
    assert decision.verification_depth == "standard"


def test_fabric_deep_verification_for_risky_work():
    decision = decide_execution(
        required=["bdd"],
        available=["playwright"],
        independent_work=4,
        risk="high",
        destructive=True,
        history=[ExecutionObservation("playwright", success=True, evidence_confidence=0.9)],
    )
    assert decision.execution_mode == "serial"
    assert decision.verification_depth == "deep"


def test_fabric_escalates_after_repeated_failures():
    decision = decide_execution(
        available=["playwright", "selenium"],
        history=[
            ExecutionObservation("playwright", success=False, evidence_confidence=0.2, verification_failures=2, retries=1),
            ExecutionObservation("selenium", success=True, evidence_confidence=0.8),
        ],
    )
    assert decision.provider == "selenium"
    assert decision.escalation in {"review", "deeper_verification", "alternate_provider"}


def test_fabric_ignores_malformed_history():
    decision = decide_execution(
        available=["playwright"],
        history=[{"provider": "playwright", "success": "bad", "latency_ms": "bad"}],
    )
    assert decision.provider == "playwright"
