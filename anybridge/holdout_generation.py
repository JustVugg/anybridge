"""Deterministic, provenance-aware benchmark and holdout generation.

Generated cases are validation data only. They are derived from capability
metadata and observed gaps, marked as generated, and must not be treated as
trusted execution instructions. Callers own execution and independent scoring.
"""
from __future__ import annotations
from dataclasses import dataclass


def _b(x): return max(0.0,min(1.0,float(x)))


@dataclass(frozen=True)
class HoldoutCase:
    case_id: str
    source_capability_id: str
    target_domain: str
    variation: str
    difficulty: float
    independence_key: str
    generated: bool = True
    executable: bool = False
    provenance: str = "capability_metadata"
    def to_dict(self): return self.__dict__.copy()


@dataclass(frozen=True)
class BenchmarkObservation:
    case_id: str
    passed: bool
    evidence_confidence: float = 0.0
    independent: bool = True
    execution_id: str = ""
    details: str = ""
    def to_dict(self): return self.__dict__.copy()


class HoldoutGenerator:
    """Bounded deterministic generator; generated cases never become commands."""

    VARIATIONS=("field_order","nested_shape","boundary_value","whitespace","unseen_domain")

    def __init__(self, *, max_cases=2048):
        self.max_cases=max(1,int(max_cases))
        self._cases={}
        self._observations=[]

    @staticmethod
    def _id(capability_id,target_domain,variation):
        return f"holdout::{capability_id}::{target_domain}::{variation}"

    def generate(self, capabilities, *, target_domains, max_per_capability=5):
        out=[]
        for capability in sorted(capabilities,key=lambda x:x.capability_id):
            for domain in sorted(set(map(str,target_domains))):
                if not domain or domain == str(getattr(capability,"attack_class","")):
                    continue
                for variation in self.VARIATIONS[:max(1,int(max_per_capability))]:
                    cid=self._id(capability.capability_id,domain,variation)
                    case=HoldoutCase(
                        cid,capability.capability_id,domain,variation,
                        _b(.35+.1*self.VARIATIONS.index(variation)),
                        f"{capability.capability_id}|{domain}|{variation}",
                    )
                    self._cases[cid]=case
                    out.append(case)
        while len(self._cases)>self.max_cases:
            del self._cases[sorted(self._cases)[0]]
        return tuple(sorted(out,key=lambda x:x.case_id)[:self.max_cases])

    def observe(self, observation):
        if observation.case_id not in self._cases:
            raise KeyError(observation.case_id)
        if not observation.independent:
            raise ValueError("holdout observations must be independently scored")
        self._observations.append(observation)
        if len(self._observations)>self.max_cases*2:
            self._observations=self._observations[-self.max_cases*2:]
        return observation

    def corpus(self):
        return tuple(self._cases[k] for k in sorted(self._cases))

    def observations(self):
        return tuple(self._observations)

    def export(self):
        return {"version":1,"cases":[x.to_dict() for x in self.corpus()],
                "observations":[x.to_dict() for x in self.observations()]}

    def reset(self):
        self._cases.clear(); self._observations.clear()
