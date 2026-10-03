"""Deterministic experience-driven capability curriculum selection.

Ranks caller-supplied validation candidates using novelty, uncertainty, failure
risk, evidence value, and observed cost. It never executes work or grants
authorization; callers own task execution, persistence, and promotion.
"""
from __future__ import annotations
from dataclasses import dataclass


def _b(value):
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class CurriculumCandidate:
    candidate_id: str
    capability_id: str
    domain: str
    novelty: float = 0.0
    uncertainty: float = 0.0
    failure_risk: float = 0.0
    evidence_value: float = 0.0
    estimated_cost: float = 0.0
    transfer_gap: float = 0.0
    def to_dict(self): return self.__dict__.copy()


@dataclass(frozen=True)
class CurriculumObservation:
    candidate_id: str
    passed: bool
    holdout: bool = False
    evidence_confidence: float = 0.0
    cost: float = 0.0
    duration_ms: float = 0.0
    details: str = ""
    def to_dict(self): return self.__dict__.copy()


@dataclass(frozen=True)
class CurriculumDecision:
    selected: tuple
    counterfactuals: tuple
    rationale: str
    confidence: float
    def to_dict(self):
        return {
            "selected": [x.to_dict() for x in self.selected],
            "counterfactuals": [x.to_dict() for x in self.counterfactuals],
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


class CapabilityCurriculumStore:
    def __init__(self, *, max_candidates=1024, max_observations=4096):
        self.max_candidates=max(1,int(max_candidates))
        self.max_observations=max(1,int(max_observations))
        self._candidates={}
        self._observations=[]

    @staticmethod
    def _score(c, history):
        xs=[x for x in history if x.candidate_id==c.candidate_id]
        failures=sum(not x.passed for x in xs)
        attempts=len(xs)
        realized_failure=failures/attempts if attempts else c.failure_risk
        uncertainty=max(c.uncertainty, 1.0-(attempts/5.0))
        cost_penalty=_b(c.estimated_cost/100.0)
        return _b(
            .24*c.novelty + .24*uncertainty + .20*max(c.failure_risk,realized_failure)
            + .18*c.evidence_value + .10*c.transfer_gap + .04*(1.0-cost_penalty)
        )

    def register(self, candidate):
        if not candidate.candidate_id: raise ValueError("candidate_id is required")
        self._candidates[candidate.candidate_id]=candidate
        while len(self._candidates)>self.max_candidates:
            del self._candidates[sorted(self._candidates)[0]]
        return candidate

    def observe(self, observation):
        if observation.candidate_id not in self._candidates:
            raise KeyError(observation.candidate_id)
        self._observations.append(observation)
        if len(self._observations)>self.max_observations:
            self._observations=self._observations[-self.max_observations:]
        return self.rank()

    def rank(self, *, limit=16):
        ranked=sorted(
            self._candidates.values(),
            key=lambda c:(-self._score(c,self._observations),c.candidate_id),
        )
        return tuple(ranked[:max(1,int(limit))])

    def decide(self, *, budget=3):
        ranked=self.rank(limit=max(1,len(self._candidates)))
        selected=ranked[:max(1,int(budget))]
        counterfactuals=ranked[max(1,int(budget)):max(1,int(budget))+3]
        confidence=(
            sum(_b(self._score(x,self._observations)) for x in selected)/len(selected)
            if selected else 0.0
        )
        return CurriculumDecision(
            selected, counterfactuals,
            "prioritize novel, uncertain, high-evidence candidates while retaining failure and transfer signals",
            confidence,
        )

    def candidates(self):
        return tuple(self._candidates[k] for k in sorted(self._candidates))

    def observations(self):
        return tuple(self._observations)

    def export(self):
        return {
            "version":1,
            "candidates":[x.to_dict() for x in self.candidates()],
            "observations":[x.to_dict() for x in self.observations()],
        }

    def reset(self):
        self._candidates.clear()
        self._observations.clear()
