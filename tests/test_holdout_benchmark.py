from anybridge.holdout_generation import HoldoutGenerator, BenchmarkObservation
from anybridge.capability_composition import CapabilityPrimitive
from anybridge.holdout_benchmark import HoldoutBenchmark


def test_benchmark_requires_independent_outcomes():
    g=HoldoutGenerator()
    c=g.generate([CapabilityPrimitive("cap","content","boundary",confidence=.9)],target_domains=("extension",))
    case=c[0]
    b=HoldoutBenchmark(min_attempts=2)
    b.ingest(c,[
        BenchmarkObservation(case.case_id,True,1.0,True),
        BenchmarkObservation(case.case_id,True,1.0,True),
    ])
    assert b.results()[0].status=="validated_holdout"


def test_non_independent_outcomes_do_not_become_evidence():
    g=HoldoutGenerator()
    c=g.generate([CapabilityPrimitive("cap","content","boundary",confidence=.9)],target_domains=("extension",))
    case=c[0]
    b=HoldoutBenchmark(min_attempts=1)
    b.ingest(c,[BenchmarkObservation(case.case_id,True,1.0,False)])
    assert not b.results()


def test_failed_and_insufficient_benchmarks_are_distinct():
    g=HoldoutGenerator()
    c=g.generate([CapabilityPrimitive("cap","content","boundary",confidence=.9)],target_domains=("extension",))
    case=c[0]
    b=HoldoutBenchmark(min_attempts=2)
    b.ingest(c,[BenchmarkObservation(case.case_id,False,1.0,True)])
    assert b.results()[0].status=="insufficient_evidence"
    b.ingest(c,[BenchmarkObservation(case.case_id,False,1.0,True)])
    assert b.results()[0].status=="failed_holdout"


def test_benchmark_preserves_independence_cohort_for_promotion():
    g=HoldoutGenerator()
    cases=g.generate(
        [CapabilityPrimitive("cap","content","boundary",confidence=.9)],
        target_domains=("extension","service"),
    )
    b=HoldoutBenchmark(min_attempts=1)
    b.ingest(cases, [
        BenchmarkObservation(cases[0].case_id,True,1.0,True),
        BenchmarkObservation(cases[1].case_id,True,1.0,True),
    ])
    evidence=b.promotion_evidence()
    assert len(evidence) == 2
    assert all(x.independence_key and x.cohort_id for x in evidence)


def test_missing_independence_key_is_not_promotion_evidence():
    from anybridge.holdout_generation import HoldoutCase
    case = HoldoutCase("case", "cap", "domain", "variation", .5, "")
    b = HoldoutBenchmark(min_attempts=1)
    b.ingest([case], [BenchmarkObservation("case", True, 1.0, True)])
    assert not b.results()
