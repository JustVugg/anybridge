"""Verified security capability graduation and rollback.

A capability is a named defense strategy for an attack family. Graduation is
evidence-gated: repeated successful outcomes establish a candidate, independent
holdout outcomes can canary it, and subsequent regressions roll it back.
No attacks or tools are executed by this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
from .webmcp_security_evolution import DefenseObservation

@dataclass(frozen=True)
class CapabilityState:
    capability_id: str
    attack_class: str
    defense: str
    status: str
    evidence_attempts: int
    evidence_block_rate: float
    holdout_attempts: int
    holdout_block_rate: float
    canary_attempts: int
    canary_block_rate: float
    confidence: float
    version: int
    reason: str
    def to_dict(self): return self.__dict__.copy()

@dataclass(frozen=True)
class CapabilityDecision:
    capability_id: str
    action: str
    status: str
    confidence: float
    reason: str
    state: CapabilityState
    def to_dict(self): return {**self.__dict__, "state": self.state.to_dict()}

class CapabilityGraduationStore:
    def __init__(self, *, graduation_min_attempts=5, graduation_min_rate=.9,
                 holdout_min_attempts=3, holdout_min_rate=.9, rollback_rate=.75):
        self.graduation_min_attempts=max(1,int(graduation_min_attempts))
        self.graduation_min_rate=max(0.,min(1.,float(graduation_min_rate)))
        self.holdout_min_attempts=max(1,int(holdout_min_attempts))
        self.holdout_min_rate=max(0.,min(1.,float(holdout_min_rate)))
        self.rollback_rate=max(0.,min(1.,float(rollback_rate)))
        self._states={}
        self._evidence={}
        self._holdout={}
        self._canary={}
        self._versions={}

    @staticmethod
    def _key(attack_class,defense): return f"{attack_class}::{defense}"

    def _aggregate(self,bucket):
        attempts=len(bucket); blocked=sum(int(x.blocked) for x in bucket)
        rate=blocked/attempts if attempts else 0.
        confidence=sum(max(0.,min(1.,float(x.evidence_confidence))) for x in bucket)/attempts if attempts else 0.
        return attempts,rate,confidence

    def _state(self,key,attack_class,defense,status,reason):
        e,er,ec=self._aggregate(self._evidence.get(key,[]))
        h,hr,_=self._aggregate(self._holdout.get(key,[]))
        c,cr,_=self._aggregate(self._canary.get(key,[]))
        confidence=(ec+ (hr if h else 0.) + (cr if c else 0.))/(1+int(bool(h))+int(bool(c)))
        version=self._versions.get(key,0)
        return CapabilityState(key,attack_class,defense,status,e,er,h,hr,c,cr,confidence,version,reason)

    def observe(self, observation: DefenseObservation, *, cohort="evidence", capability_id=None):
        key=capability_id or self._key(observation.attack_class,observation.defense)
        bucket={"evidence":self._evidence,"holdout":self._holdout,"canary":self._canary}.get(cohort)
        if bucket is None: raise ValueError("cohort must be evidence, holdout, or canary")
        bucket.setdefault(key,[]).append(observation)
        prior=self._states.get(key)
        status=prior.status if prior else "candidate"
        reason="awaiting evidence"
        if cohort=="evidence":
            e,rate,_=self._aggregate(bucket[key])
            if e>=self.graduation_min_attempts and rate>=self.graduation_min_rate: status="holdout"; reason="evidence threshold reached; holdout required"
        if cohort=="holdout":
            h,rate,_=self._aggregate(bucket[key])
            if h>=self.holdout_min_attempts and rate>=self.holdout_min_rate:
                status="canary"; reason="independent holdout threshold reached; canary permitted"
        if cohort=="canary":
            c,rate,_=self._aggregate(bucket[key])
            if status=="graduated" and c>=1 and rate<self.rollback_rate:
                status="rolled_back"; self._versions[key]=self._versions.get(key,0)+1; reason="canary regression triggered rollback"
        self._states[key]=self._state(key,observation.attack_class,observation.defense,status,reason)
        return self.decision(key)

    def canary_result(self, capability_id, *, attack_class, defense, observations: Iterable[DefenseObservation]):
        key=str(capability_id)
        for o in observations: self.observe(o,cohort="canary",capability_id=key)
        state=self._states.get(key)
        if state is None: raise KeyError(key)
        c,rate,_=self._aggregate(self._canary.get(key,[]))
        if state.status=="canary" and c>=1 and rate>=self.holdout_min_rate:
            self._versions[key]=self._versions.get(key,0)+1
            state=self._state(key,attack_class,defense,"graduated","canary passed; capability promoted")
            self._states[key]=state
        elif state.status=="canary" and c>=1 and rate<self.rollback_rate:
            self._versions[key]=self._versions.get(key,0)+1
            state=self._state(key,attack_class,defense,"rolled_back","canary failed; capability rolled back")
            self._states[key]=state
        return self.decision(key)

    def decision(self,key):
        state=self._states[key]
        action={"candidate":"collect_evidence","holdout":"run_holdout","canary":"run_canary","graduated":"use_capability","rolled_back":"quarantine"}.get(state.status,"collect_evidence")
        return CapabilityDecision(key,action,state.status,state.confidence,state.reason,state)

    def states(self): return tuple(sorted(self._states.values(),key=lambda x:x.capability_id))
    def active(self): return tuple(x for x in self.states() if x.status=="graduated")
    def export(self): return {"version":1,"states":[x.to_dict() for x in self.states()]}
    def reset(self): self._states.clear();self._evidence.clear();self._holdout.clear();self._canary.clear();self._versions.clear()
