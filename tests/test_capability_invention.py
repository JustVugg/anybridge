from anybridge.capability_invention import CapabilityInventionStore, InventionObservation
from anybridge.capability_composition import CapabilityPrimitive


def test_invention_is_cross_family_and_requires_holdout():
    s=CapabilityInventionStore(min_holdout=2)
    ps=[
        CapabilityPrimitive("a","origin","x",confidence=.95),
        CapabilityPrimitive("b","schema","y",confidence=.90),
        CapabilityPrimitive("c","output","z",confidence=.85),
    ]
    hs=s.propose(ps,max_length=2)
    assert hs
    h=hs[0]
    assert len(h.attack_classes)>=2
    assert s.decision(h.hypothesis_id).action=="run_independent_holdout"
    s.observe(InventionObservation(h.hypothesis_id,True,True,.95))
    s.observe(InventionObservation(h.hypothesis_id,True,True,.95))
    assert s.decision(h.hypothesis_id).status=="validated"


def test_invention_rejects_incompatible_primitives():
    s=CapabilityInventionStore()
    ps=[
        CapabilityPrimitive("a","origin","x",confidence=1,incompatible_with=("b",)),
        CapabilityPrimitive("b","schema","y",confidence=1),
    ]
    assert not s.propose(ps)


def test_validated_hypothesis_exposes_safe_composition_handoff():
    s=CapabilityInventionStore(min_holdout=1)
    ps=[CapabilityPrimitive("a","origin","x",confidence=1), CapabilityPrimitive("b","schema","y",confidence=1)]
    h=s.propose(ps,max_length=1)[0]
    s.observe(InventionObservation(h.hypothesis_id,True,True,1.0))
    assert s.validated_capability_ids(h.hypothesis_id)==("a","b")
    assert len(s.validated_hypotheses())==1
