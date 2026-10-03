from anybridge.provider_learning import (
    ProviderObservation,
    rank_providers,
    select_learned_provider,
)


def test_learning_prefers_reliable_provider():
    history = [
        ProviderObservation("playwright", success=False, evidence_confidence=0.1, latency_ms=8000),
        ProviderObservation("playwright", success=False, evidence_confidence=0.2, latency_ms=7000),
        ProviderObservation("selenium", success=True, evidence_confidence=0.95, latency_ms=1200),
        ProviderObservation("selenium", success=True, evidence_confidence=0.9, latency_ms=1400),
    ]
    ranked = rank_providers(
        required=["bdd"],
        available=["playwright", "selenium"],
        history=history,
    )
    assert ranked[0].provider == "selenium"
    assert ranked[0].success_rate > ranked[1].success_rate


def test_learning_never_breaks_capability_constraints():
    history = [
        {"provider": "selenium", "success": True, "evidence_confidence": 1.0},
    ]
    assert select_learned_provider(
        required=["webmcp"],
        browser="webkit",
        available=["playwright", "selenium"],
        history=history,
    ) == "playwright"


def test_learning_handles_malformed_observations():
    ranked = rank_providers(
        available=["playwright"],
        history=[{"provider": "playwright", "success": "bad", "latency_ms": "bad"}],
    )
    assert len(ranked) == 1
    assert 0 <= ranked[0].score <= 1
