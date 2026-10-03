from anybridge.capability_composition import CapabilityCompositionStore, CapabilityPrimitive, CompositionObservation

def test_composition_requires_holdout():
    s=CapabilityCompositionStore(min_holdout=2)
    s.register(CapabilityPrimitive("a","origin","boundary",confidence=.95))
    s.register(CapabilityPrimitive("b","schema","boundary",confidence=.90))
    assert s.decision("a::b").action=="run_independent_holdout"
    s.observe(CompositionObservation("a::b",("a","b"),True,.95,True))
    s.observe(CompositionObservation("a::b",("a","b"),True,.95,True))
    assert s.decision("a::b").chain.status=="graduated"

def test_missing_dependency_and_conflict_are_blocked():
    s=CapabilityCompositionStore()
    s.register(CapabilityPrimitive("a","origin","x",confidence=1.0,dependencies=("root",)))
    s.register(CapabilityPrimitive("b","schema","y",confidence=1.0,incompatible_with=("a",)))
    d=s.analyze(("a","b"))
    assert d.status=="blocked" and "root" in d.missing_dependencies and "a" in d.conflicts

def test_propose_is_deterministic_and_bounded():
    s=CapabilityCompositionStore()
    for x in ("a","b","c"): s.register(CapabilityPrimitive(x,x,"d",confidence=.9))
    assert s.propose(max_length=2)==s.propose(max_length=2)
    assert len(s.propose(max_length=2))==2

def test_non_graduated_state_cannot_register():
    class State: status="candidate"; capability_id="x"; attack_class="a"; defense="d"; version=1; confidence=.5
    s=CapabilityCompositionStore()
    try: s.register_graduated(State())
    except ValueError: pass
    else: assert False


def test_dependency_graph_and_regression_propagation():
    s=CapabilityCompositionStore()
    s.register(CapabilityPrimitive("a","origin","x",confidence=1.0))
    s.register(CapabilityPrimitive("b","schema","y",confidence=1.0,dependencies=("a",)))
    s.observe(CompositionObservation("a::b",("a","b"),True,.95,True))
    assert s.dependency_graph()["b"] == ("a",)
    assert s.quarantine_dependents("a") == ("a::b",)
