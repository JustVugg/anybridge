from anybridge.capability_composition import CapabilityPrimitive
from anybridge.holdout_generation import HoldoutGenerator, BenchmarkObservation


def test_generation_is_deterministic_and_non_executable():
    g=HoldoutGenerator(max_cases=20)
    caps=[CapabilityPrimitive("cap-a","content","boundary",confidence=.9)]
    cases=g.generate(caps,target_domains=("extension","private-app"))
    assert cases
    assert all(x.generated and not x.executable for x in cases)
    assert cases[0].provenance=="capability_metadata"


def test_independent_scoring_is_required():
    g=HoldoutGenerator()
    cap=CapabilityPrimitive("cap-a","content","boundary",confidence=.9)
    case=g.generate([cap],target_domains=("extension",))[0]
    try:
        g.observe(BenchmarkObservation(case.case_id,True,1.0,False))
        assert False
    except ValueError:
        pass
    g.observe(BenchmarkObservation(case.case_id,True,1.0,True))


def test_unknown_cases_cannot_be_scored_and_storage_is_bounded():
    g=HoldoutGenerator(max_cases=2)
    cap=CapabilityPrimitive("cap","content","boundary",confidence=.9)
    g.generate([cap],target_domains=("a","b","c"))
    assert len(g.corpus())<=2
    try:
        g.observe(BenchmarkObservation("unknown",True))
        assert False
    except KeyError:
        pass
