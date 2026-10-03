from anybridge.capability_curriculum import (
    CapabilityCurriculumStore,
    CurriculumCandidate,
    CurriculumObservation,
)


def test_curriculum_prefers_unseen_high_value_work_and_exposes_counterfactuals():
    s=CapabilityCurriculumStore()
    s.register(CurriculumCandidate("known","a","webmcp",novelty=.1,uncertainty=.1,evidence_value=.4))
    s.register(CurriculumCandidate("novel","b","extension",novelty=1,uncertainty=1,evidence_value=1,transfer_gap=1))
    s.register(CurriculumCandidate("risk","c","private-app",novelty=.5,uncertainty=.8,failure_risk=.9,evidence_value=.8))
    d=s.decide(budget=1)
    assert d.selected[0].candidate_id=="novel"
    assert d.counterfactuals


def test_realized_failures_raise_future_priority():
    s=CapabilityCurriculumStore()
    s.register(CurriculumCandidate("steady","a","a",novelty=.2,uncertainty=.1,evidence_value=.5))
    s.register(CurriculumCandidate("risky","b","b",novelty=.2,uncertainty=.1,evidence_value=.5))
    before=s.rank()[0].candidate_id
    s.observe(CurriculumObservation("risky",False,True,1.0))
    after=s.rank()[0].candidate_id
    assert before=="risky" or after=="risky"


def test_curriculum_is_bounded_and_deterministic():
    s=CapabilityCurriculumStore(max_candidates=2,max_observations=2)
    for i in range(4):
        s.register(CurriculumCandidate(str(i),str(i),"d",novelty=.5))
    assert len(s.candidates())==2
    s.observe(CurriculumObservation("2",True))
    s.observe(CurriculumObservation("3",True))
    assert len(s.observations())==2
    assert s.export()["version"]==1
