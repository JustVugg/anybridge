"""Deterministic calibration of capability confidence against outcomes.

Calibration is measurement, not authorization: callers provide predicted
confidence and realized holdout outcomes; the store reports reliability error
and a bounded correction for future decision layers.
"""
from __future__ import annotations
from dataclasses import dataclass


def _b(x): return max(0.0, min(1.0, float(x)))


@dataclass(frozen=True)
class CalibrationObservation:
    capability_id: str
    predicted_confidence: float
    passed: bool
    holdout: bool = True
    evidence_confidence: float = 0.0
    def to_dict(self): return self.__dict__.copy()


@dataclass(frozen=True)
class CalibrationResult:
    capability_id: str
    samples: int
    empirical_rate: float
    mean_predicted: float
    calibration_error: float
    corrected_confidence: float
    status: str
    def to_dict(self): return self.__dict__.copy()


class CapabilityCalibrationStore:
    def __init__(self, *, max_observations=4096, min_samples=3):
        self.max_observations=max(1,int(max_observations))
        self.min_samples=max(1,int(min_samples))
        self._observations=[]

    def observe(self, observation):
        self._observations.append(observation)
        if len(self._observations)>self.max_observations:
            self._observations=self._observations[-self.max_observations:]
        return self.result(observation.capability_id)

    def result(self, capability_id):
        xs=[x for x in self._observations if x.capability_id==capability_id and x.eligible]
        n=len(xs)
        rate=sum(int(x.passed) for x in xs)/n if n else 0.0
        predicted=sum(_b(x.predicted_confidence) for x in xs)/n if n else 0.0
        error=abs(predicted-rate)
        corrected=_b(.5*predicted+.5*rate) if n else predicted
        status="calibrated" if n>=self.min_samples else "insufficient_evidence"
        return CalibrationResult(capability_id,n,rate,predicted,error,corrected,status)

    def results(self):
        ids=sorted({x.capability_id for x in self._observations})
        return tuple(self.result(i) for i in ids)

    def export(self):
        return {"version":1,"observations":[x.to_dict() for x in self._observations],
                "results":[x.to_dict() for x in self.results()]}

    def reset(self): self._observations.clear()
