from anybridge.evidence_aware_promotion import CohortEvidence, EvidenceAwarePromotionStore


def obs(cap, cohort, round_id, passed=True, confidence=1.0, independence=""):
    return CohortEvidence(cap, cohort, round_id, passed, confidence, cohort, cohort, independence)


def test_one_cohort_cannot_promote():
    store = EvidenceAwarePromotionStore(min_cohorts=2)
    decision = store.ingest([obs("cap", "a", "r1"), obs("cap", "a", "r1")])[0]
    assert decision.status == "candidate"


def test_shared_independence_key_cannot_fake_cohorts():
    store = EvidenceAwarePromotionStore(min_cohorts=2)
    decision = store.ingest([
        obs("cap", "a", "r1", independence="same"),
        obs("cap", "b", "r1", independence="same"),
    ])[0]
    assert decision.status == "candidate"


def test_independent_cohorts_enter_canary():
    store = EvidenceAwarePromotionStore(min_cohorts=2)
    decision = store.ingest([obs("cap", "a", "r1"), obs("cap", "b", "r1")])[0]
    assert decision.status == "canary"


def test_canary_graduates_on_later_strong_round():
    store = EvidenceAwarePromotionStore(min_cohorts=2)
    decision = store.ingest([
        obs("cap", "a", "r1"), obs("cap", "b", "r1"),
        obs("cap", "a", "r2"), obs("cap", "b", "r2"),
    ])[0]
    assert decision.status == "graduated"


def test_decay_quarantines_then_retires_from_round_history():
    store = EvidenceAwarePromotionStore(min_cohorts=2, retirement_failures=2)
    decision = store.ingest([
        obs("cap", "a", "r1"), obs("cap", "b", "r1"),
        obs("cap", "a", "r2", False, .5), obs("cap", "b", "r2", False, .5),
    ])[0]
    assert decision.status == "quarantined"
    decision = store.ingest([
        obs("cap", "a", "r3", False, .5), obs("cap", "b", "r3", False, .5),
    ])[0]
    assert decision.status == "retired"


def test_bounded_export_is_deterministic():
    store = EvidenceAwarePromotionStore(max_observations=2)
    store.ingest([obs("b", "z", "r1"), obs("a", "x", "r1"), obs("c", "y", "r1")])
    assert len(store.export()["observations"]) == 2
    assert [x.capability_id for x in store.decisions()] == ["a", "c"]
