"""Deterministic composition of verified security capabilities.

Composes already-graduated primitives into bounded capability chains. Composition
is advisory: it never executes tools, attacks, or side effects.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class CapabilityPrimitive:
    capability_id: str
    attack_class: str
    defense: str
    version: int = 1
    confidence: float = 0.0
    dependencies: tuple[str, ...] = ()
    compatible_with: tuple[str, ...] = ()
    incompatible_with: tuple[str, ...] = ()
    def to_dict(self): return self.__dict__.copy()

@dataclass(frozen=True)
class CompositionObservation:
    chain_id: str
    capability_ids: tuple[str, ...]
    passed: bool
    evidence_confidence: float = 0.0
    holdout: bool = False
    execution_id: str = ""
    details: str = ""
    def to_dict(self): return self.__dict__.copy()

@dataclass(frozen=True)
class CapabilityChain:
    chain_id: str
    capability_ids: tuple[str, ...]
    attack_classes: tuple[str, ...]
    score: float
    status: str
    missing_dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    reason: str = ""
    def to_dict(self): return self.__dict__.copy()

@dataclass(frozen=True)
class CompositionDecision:
    chain: CapabilityChain
    action: str
    confidence: float
    counterfactuals: tuple[str, ...] = ()
    def to_dict(self): return {**self.__dict__, "chain": self.chain.to_dict()}

class CapabilityCompositionStore:
    def __init__(self, *, max_primitives=512, max_observations=4096,
                 min_holdout=3, min_holdout_rate=.9):
        self.max_primitives=max(1,int(max_primitives)); self.max_observations=max(1,int(max_observations))
        self.min_holdout=max(1,int(min_holdout)); self.min_holdout_rate=max(0.,min(1.,float(min_holdout_rate)))
        self._primitives={}; self._observations=[]

    def register(self, primitive):
        if not primitive.capability_id: raise ValueError("capability_id is required")
        self._primitives[primitive.capability_id]=primitive
        if len(self._primitives)>self.max_primitives: del self._primitives[sorted(self._primitives)[0]]
        return primitive

    def register_graduated(self, state):
        if getattr(state,"status",None)!="graduated": raise ValueError("only graduated capabilities may be composed")
        return self.register(CapabilityPrimitive(state.capability_id,state.attack_class,state.defense,
                                                  state.version,state.confidence))

    def primitives(self): return tuple(self._primitives[k] for k in sorted(self._primitives))

    def analyze(self, capability_ids: Iterable[str], *, chain_id=None):
        ids=tuple(dict.fromkeys(str(x) for x in capability_ids)); ps=[self._primitives[x] for x in ids if x in self._primitives]
        missing=set(ids)-{p.capability_id for p in ps}; conflicts=set(); missing_deps=set(missing)
        for p in ps:
            missing_deps.update(x for x in p.dependencies if x not in ids)
            conflicts.update(x for x in p.incompatible_with if x in ids)
        attacks=tuple(sorted({p.attack_class for p in ps}))
        score=sum(max(0.,min(1.,p.confidence)) for p in ps)/len(ps) if ps else 0.
        status,reason="candidate","candidate composition"
        if not ids: status,reason="invalid","empty capability chain"
        elif missing: status,reason="blocked","unknown capability primitive"
        elif missing_deps: status,reason="blocked","prerequisite capability missing"
        elif conflicts: status,reason="blocked","capability conflict detected"
        elif len(ids)<2: status,reason="redundant","composition requires at least two primitives"
        elif not all(p.confidence>0 for p in ps): reason="awaiting verified primitive confidence"
        return CapabilityChain(chain_id or "::".join(ids),ids,attacks,score,status,
                               tuple(sorted(missing_deps)),tuple(sorted(conflicts)),reason)

    def observe(self, observation):
        self._observations.append(observation)
        if len(self._observations)>self.max_observations: self._observations=self._observations[-self.max_observations:]
        return self.decision(observation.chain_id, observation.capability_ids)

    def _stats(self, chain_id):
        xs=[x for x in self._observations if x.chain_id==chain_id and x.holdout]
        return len(xs),sum(int(x.passed) for x in xs)/len(xs) if xs else 0.,sum(max(0.,min(1.,x.evidence_confidence)) for x in xs)/len(xs) if xs else 0.

    def decision(self, chain_id, capability_ids=None):
        ids=tuple(capability_ids) if capability_ids is not None else tuple(chain_id.split("::")) if chain_id else ()
        chain=self.analyze(ids,chain_id=chain_id); n,rate,conf=self._stats(chain_id)
        if chain.status=="candidate" and n>=self.min_holdout and rate>=self.min_holdout_rate:
            chain=CapabilityChain(chain.chain_id,chain.capability_ids,chain.attack_classes,chain.score,"graduated",
                                  chain.missing_dependencies,chain.conflicts,"independent holdout passed")
            action="use_composed_capability"
        elif chain.status=="candidate": action="run_independent_holdout"
        elif chain.status in ("blocked","invalid"): action="quarantine"
        else: action="do_not_compose"
        return CompositionDecision(chain,action,max(chain.score,conf),
                                   ("drop_last","run_primitives_independently","reverse_order"))

    def propose(self, *, max_length=3):
        ps=self.primitives(); out=[]
        for i,a in enumerate(ps):
            for b in ps[i+1:]:
                if a.attack_class==b.attack_class or a.capability_id in b.dependencies or b.capability_id in a.dependencies: continue
                if a.capability_id in b.incompatible_with or b.capability_id in a.incompatible_with: continue
                out.append(self.analyze((a.capability_id,b.capability_id)))
        return tuple(sorted(out,key=lambda x:(-x.score,x.chain_id))[:max(1,int(max_length))])


    def dependency_graph(self):
        """Return deterministic prerequisite and dependent edges."""
        return {p.capability_id: tuple(sorted(p.dependencies))
                for p in self.primitives()}

    def quarantine_dependents(self, capability_id):
        """Return chains that must be revalidated when a primitive regresses."""
        target=str(capability_id)
        affected=set()
        for observation in self._observations:
            if target not in observation.capability_ids:
                continue
            affected.add(observation.chain_id)
        changed=True
        while changed:
            changed=False
            for p in self.primitives():
                if p.capability_id in affected:
                    for observation in self._observations:
                        if p.capability_id in observation.capability_ids and observation.chain_id not in affected:
                            affected.add(observation.chain_id); changed=True
        return tuple(sorted(affected))

    def export(self):
        return {"version":1,"primitives":[p.to_dict() for p in self.primitives()],
                "observations":[o.to_dict() for o in self._observations]}

    def reset(self): self._primitives.clear(); self._observations.clear()
