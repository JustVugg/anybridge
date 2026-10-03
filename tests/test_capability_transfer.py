from anybridge.capability_composition import CapabilityPrimitive
from anybridge.capability_transfer import (
    CapabilityTransferStore,
    TransferObservation,
)


def _store():
    store = CapabilityTransferStore(min_holdout=2, canary_min_attempts=2)
    primitives = [
        CapabilityPrimitive("origin-bound", "provenance", "origin_binding", confidence=.98),
        CapabilityPrimitive("quarantine", "content", "content_boundary", confidence=.96),
        CapabilityPrimitive("confirm", "action", "consequential_confirmation", confidence=.94),
    ]
    store.abstract_patterns(primitives, source_domain="webmcp")
    return store


def test_transfer_requires_a_different_domain_and_unseen_holdout():
    store = _store()
    assert not store.propose(
        target_domain="webmcp",
        target_capability_class="content-security",
    )
    proposals = store.propose(
        target_domain="browser-extension",
        target_capability_class="content-security",
    )
    assert proposals
    h = proposals[0]
    assert store.decision(h.transfer_id).action == "run_unseen_domain_holdout"

    store.observe(TransferObservation(h.transfer_id, True, True, .95, "webmcp", "browser-extension"))
    store.observe(TransferObservation(h.transfer_id, True, True, .95, "webmcp", "browser-extension"))
    decision = store.decision(h.transfer_id)
    assert decision.status == "validated"
    assert decision.action == "run_canary"


def test_canary_graduates_and_regresses():
    store = _store()
    h = store.propose(
        target_domain="private-app",
        target_capability_class="content-security",
    )[0]
    for _ in range(2):
        store.observe(TransferObservation(h.transfer_id, True, True, 1.0))
    assert store.decision(h.transfer_id).status == "validated"

    decision = store.canary_result(h.transfer_id, [
        TransferObservation(h.transfer_id, True, False, 1.0),
        TransferObservation(h.transfer_id, True, False, 1.0),
    ])
    assert decision.status == "graduated"
    assert decision.action == "promote_transfer"

    # A later failing canary is a regression and must roll back.
    decision = store.canary_result(h.transfer_id, [
        TransferObservation(h.transfer_id, False, False, 1.0),
        TransferObservation(h.transfer_id, False, False, 1.0),
    ])
    assert decision.status == "rolled_back"
    assert decision.action == "rollback_transfer"


def test_incomplete_or_failed_holdout_is_not_promoted():
    store = _store()
    h = store.propose(
        target_domain="agent-ui",
        target_capability_class="content-security",
    )[0]
    store.observe(TransferObservation(h.transfer_id, False, True, 1.0))
    store.observe(TransferObservation(h.transfer_id, False, True, 1.0))
    decision = store.decision(h.transfer_id)
    assert decision.status == "rejected"
    assert decision.action == "quarantine"


def test_transfer_state_is_bounded_and_exportable():
    store = CapabilityTransferStore(max_patterns=2, max_hypotheses=2, max_observations=3)
    primitives = [
        CapabilityPrimitive(str(i), f"class-{i}", f"defense-{i}", confidence=1.0)
        for i in range(4)
    ]
    store.abstract_patterns(primitives, source_domain="domain-a")
    assert len(store.patterns()) <= 2
    proposals = store.propose(target_domain="domain-b", target_capability_class="generic")
    for h in proposals:
        store.observe(TransferObservation(h.transfer_id, True, True, 1.0))
    assert len(store.export()["observations"]) <= 3
