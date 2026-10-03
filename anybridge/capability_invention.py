"""Deterministic capability invention from verified primitives.

Invention proposes novel bounded capability chains from already-verified
primitives. It never executes a tool or action; callers own holdout execution,
authorization, persistence, and promotion.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable


@dataclass(frozen=True)
class InventionHypothesis:
    hypothesis_id: str
    capability_ids: tuple[str, ...]
    attack_classes: tuple[str, ...]
    novelty: float
    confidence: float
    rationale: str
    status: str = "candidate"

    def to_dict(self):
        return self.__dict__.copy()


@dataclass(frozen=True)
class InventionObservation:
    hypothesis_id: str
    passed: bool
    holdout: bool
    evidence_confidence: float = 0.0
    execution_id: str = ""
    details: str = ""

    def to_dict(self):
        return self.__dict__.copy()


@dataclass(frozen=True)
class InventionDecision:
    hypothesis_id: str
    action: str
    status: str
    confidence: float
    reason: str
    hypothesis: InventionHypothesis

    def to_dict(self):
        return {**self.__dict__, "hypothesis": self.hypothesis.to_dict()}


class CapabilityInventionStore:
    def __init__(self, *, max_hypotheses=512, max_observations=4096,
                 min_holdout=3, min_holdout_rate=.9):
        self.max_hypotheses=max(1,int(max_hypotheses))
        self.max_observations=max(1,int(max_observations))
        self.min_holdout=max(1,int(min_holdout))
        self.min_holdout_rate=max(0.,min(1.,float(min_holdout_rate)))
        self._hypotheses={}
        self._observations=[]

    @staticmethod
    def _id(ids):
        return "invented::" + "::".join(sorted(dict.fromkeys(ids)))

    def propose(self, primitives: Iterable, *, max_length=3):
        ps=tuple(sorted(primitives,key=lambda x:x.capability_id))
        out=[]
        for length in range(2,min(int(max_length),len(ps))+1):
            for combo in combinations(ps,length):
                ids=tuple(x.capability_id for x in combo)
                attacks=tuple(sorted({x.attack_class for x in combo}))
                if len(attacks)<2:
                    continue
                incompatible=set()
                for p in combo:
                    incompatible.update(x for x in p.incompatible_with if x in ids)
                missing=set()
                for p in combo:
                    missing.update(x for x in p.dependencies if x not in ids)
                if incompatible or missing:
                    continue
                confidence=sum(max(0.,min(1.,x.confidence)) for x in combo)/length
                novelty=min(1.,(len(attacks)-1)/max(1,len(attacks)) + .1)
                hid=self._id(ids)
                h=InventionHypothesis(hid,ids,attacks,novelty,confidence,
                    "cross-family composition of verified primitives")
                self._hypotheses[hid]=h
                out.append(h)
        for hid in sorted(self._hypotheses):
            if len(out)>=max(1,int(max_length)): break
        return tuple(sorted(out,key=lambda x:(-(x.novelty*x.confidence),x.hypothesis_id))[:max(1,int(max_length))])

    def observe(self, observation: InventionObservation):
        if observation.hypothesis_id not in self._hypotheses:
            raise KeyError(observation.hypothesis_id)
        self._observations.append(observation)
        if len(self._observations)>self.max_observations:
            self._observations=self._observations[-self.max_observations:]
        return self.decision(observation.hypothesis_id)

    def decision(self, hypothesis_id):
        h=self._hypotheses[hypothesis_id]
        xs=[x for x in self._observations if x.hypothesis_id==hypothesis_id and x.holdout]
        passed=sum(int(x.passed) for x in xs)
        rate=passed/len(xs) if xs else 0.
        evidence=sum(max(0.,min(1.,x.evidence_confidence)) for x in xs)/len(xs) if xs else 0.
        if len(xs)>=self.min_holdout and rate>=self.min_holdout_rate:
            status="validated"; action="promote_to_composition"
            reason="independent holdout threshold reached"
        else:
            status="candidate"; action="run_independent_holdout"
            reason="awaiting independent holdout evidence"
        return InventionDecision(hypothesis_id,action,status,max(h.confidence,evidence),reason,
                                 InventionHypothesis(h.hypothesis_id,h.capability_ids,h.attack_classes,
                                                     h.novelty,h.confidence,h.rationale,status))


    def validated_capability_ids(self, hypothesis_id):
        """Return a validated hypothesis's primitive IDs for composition handoff."""
        decision = self.decision(hypothesis_id)
        if decision.status != "validated":
            return ()
        return decision.hypothesis.capability_ids

    def validated_hypotheses(self):
        return tuple(
            self._hypotheses[k] for k in sorted(self._hypotheses)
            if self.decision(k).status == "validated"
        )

    def hypotheses(self):
        return tuple(self._hypotheses[k] for k in sorted(self._hypotheses))

    def export(self):
        return {"version":1,"hypotheses":[x.to_dict() for x in self.hypotheses()],
                "observations":[x.to_dict() for x in self._observations]}

    def reset(self):
        self._hypotheses.clear()
        self._observations.clear()
