"""Adaptive capability evolution for WebMCP security defenses.

Learns defense effectiveness, detects regressions, creates deterministic
counter-cases, and manages promoted regression-seed lifecycle. Model-free,
bounded, caller-owned, and independent of any orchestrator.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from typing import Any, Iterable, Mapping

from .webmcp_security import evaluate_webmcp_security

def _canonical(value: object) -> str:
    try: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, RecursionError): return repr(value)

def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]

def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))

@dataclass(frozen=True)
class DefenseObservation:
    attack_id: str
    attack_class: str
    provider: str
    origin: str
    schema_hash: str
    defense: str
    blocked: bool
    evidence_confidence: float = 0.5
    latency_ms: float | None = None
    execution_id: str | None = None
    details: Mapping[str, Any] | None = None
    def to_dict(self) -> dict[str, Any]:
        data=self.__dict__.copy(); data["details"]=dict(self.details or {}); return data

@dataclass(frozen=True)
class DefenseEffectiveness:
    attack_class: str
    defense: str
    attempts: int
    blocked: int
    block_rate: float
    confidence: float
    first_seen: int
    last_seen: int
    regression: bool
    @property
    def signature(self)->str: return _hash({"attack_class":self.attack_class,"defense":self.defense})
    def to_dict(self)->dict[str,Any]:
        data=self.__dict__.copy(); data["signature"]=self.signature; return data

@dataclass(frozen=True)
class DefenseRegression:
    attack_class: str
    defense: str
    block_rate: float
    baseline_rate: float
    attempts: int
    reason: str
    def to_dict(self): return self.__dict__.copy()

@dataclass(frozen=True)
class CounterCase:
    case_id: str
    parent_case_id: str
    attack_class: str
    variant: str
    payload: Mapping[str, Any]
    def to_dict(self):
        data=self.__dict__.copy(); data["payload"]=dict(self.payload); return data

@dataclass(frozen=True)
class RegressionSeed:
    case_id: str
    attack_class: str
    defense: str
    status: str
    consecutive_passes: int
    consecutive_failures: int
    last_seen: int
    source: str
    payload: Mapping[str, Any]
    def to_dict(self):
        data=self.__dict__.copy(); data["payload"]=dict(self.payload); return data

@dataclass(frozen=True)
class SecurityEvolutionReport:
    effectiveness: tuple[DefenseEffectiveness,...]
    regressions: tuple[DefenseRegression,...]
    counter_cases: tuple[CounterCase,...]
    seeds: tuple[RegressionSeed,...]
    newly_retired: int
    reactivated: int
    def to_dict(self):
        return {"effectiveness":[x.to_dict() for x in self.effectiveness],
                "regressions":[x.to_dict() for x in self.regressions],
                "counter_cases":[x.to_dict() for x in self.counter_cases],
                "seeds":[x.to_dict() for x in self.seeds],
                "newly_retired":self.newly_retired,"reactivated":self.reactivated}

class SecurityEvolutionStore:
    """Bounded local evolution store; generated cases are data, never executed."""
    def __init__(self, *, max_observations=4096, max_counter_cases=1024, max_seeds=512,
                 baseline_min_attempts=3, baseline_min_rate=.9, regression_rate=.75,
                 regression_min_attempts=3, retirement_passes=5, reactivation_failures=1):
        self.max_observations=max(1,int(max_observations)); self.max_counter_cases=max(1,int(max_counter_cases))
        self.max_seeds=max(1,int(max_seeds)); self.baseline_min_attempts=max(1,int(baseline_min_attempts))
        self.baseline_min_rate=_bounded(baseline_min_rate); self.regression_rate=_bounded(regression_rate)
        self.regression_min_attempts=max(1,int(regression_min_attempts)); self.retirement_passes=max(1,int(retirement_passes))
        self.reactivation_failures=max(1,int(reactivation_failures)); self._clock=0
        self._observations=[]; self._effectiveness={}; self._seeds={}; self._counter_cases={}

    @staticmethod
    def _signature(o): return _hash({"attack_class":o.attack_class,"defense":o.defense})

    def _rebuild_effectiveness(self,o):
        sig=self._signature(o); prior=self._effectiveness.get(sig); attempts=(prior.attempts if prior else 0)+1
        blocked=(prior.blocked if prior else 0)+(1 if o.blocked else 0)
        conf=((prior.confidence*(prior.attempts or 0))+_bounded(o.evidence_confidence))/attempts if prior else _bounded(o.evidence_confidence)
        rate=blocked/attempts
        # Establish the baseline only after enough observations; compare later observations
        # against the best established rate, not the immediately preceding sample.
        baseline=max(prior.block_rate if prior else 0.0, rate)
        regression=attempts>=self.regression_min_attempts and baseline>=self.baseline_min_rate and rate<self.regression_rate
        result=DefenseEffectiveness(o.attack_class,o.defense,attempts,blocked,rate,conf,prior.first_seen if prior else self._clock,self._clock,regression)
        self._effectiveness[sig]=result; return result

    def _update_seed(self,o,payload):
        existing=self._seeds.get(o.attack_id)
        if existing is None:
            if o.blocked: return False,False
            data=dict(payload or {}); data.setdefault("attack_id",o.attack_id); data.setdefault("attack_class",o.attack_class)
            data.setdefault("provider",o.provider); data.setdefault("origin",o.origin); data.setdefault("schema_hash",o.schema_hash); data.setdefault("defense",o.defense)
            self._seeds[o.attack_id]=RegressionSeed(o.attack_id,o.attack_class,o.defense,"active",0,1,self._clock,"observed_failure",data)
            return False,False
        passes=existing.consecutive_passes+1 if o.blocked else 0
        failures=existing.consecutive_failures+1 if not o.blocked else 0
        retired=existing.status!="retired" and passes>=self.retirement_passes
        reactivated=existing.status=="retired" and failures>=self.reactivation_failures
        status="active" if reactivated else ("retired" if retired or existing.status=="retired" else existing.status)
        self._seeds[o.attack_id]=RegressionSeed(existing.case_id,existing.attack_class,existing.defense,status,passes,failures,self._clock,existing.source,existing.payload)
        return retired,reactivated

    def observe(self, observation, *, regression_payload=None):
        self._clock+=1
        o=DefenseObservation(str(observation.attack_id),str(observation.attack_class),str(observation.provider),str(observation.origin),
                             str(observation.schema_hash),str(observation.defense),bool(observation.blocked),
                             _bounded(observation.evidence_confidence),None if observation.latency_ms is None else max(0.,float(observation.latency_ms)),
                             observation.execution_id,dict(observation.details or {}))
        self._observations.append((self._clock,o)); self._observations=self._observations[-self.max_observations:]
        self._rebuild_effectiveness(o); retired,reactivated=self._update_seed(o,regression_payload)
        return self.report(newly_retired=int(retired),reactivated=int(reactivated))

    def promote_seed(self, *, case_id, attack_class, defense, payload, source="promoted_regression"):
        self._clock+=1
        seed=RegressionSeed(str(case_id),str(attack_class),str(defense),"promoted",0,0,self._clock,str(source),dict(payload))
        self._seeds[seed.case_id]=seed
        if len(self._seeds)>self.max_seeds:
            oldest=min(self._seeds,key=lambda k:self._seeds[k].last_seen); del self._seeds[oldest]
        return seed

    def seed(self, case_id: str) -> RegressionSeed | None:
        """Return lifecycle state for a promoted case without exposing mutable state."""
        return self._seeds.get(str(case_id))

    def effectiveness(self): return tuple(sorted(self._effectiveness.values(),key=lambda x:x.signature))
    def regressions(self):
        return tuple(sorted((DefenseRegression(x.attack_class,x.defense,x.block_rate,self.baseline_min_rate,x.attempts,
            f"observed block rate {x.block_rate:.3f} fell below regression threshold {self.regression_rate:.3f} after an established baseline")
            for x in self._effectiveness.values() if x.regression),key=lambda x:(x.attack_class,x.defense)))

    @staticmethod
    def generate_counter_cases(seed, *, max_variants=4):
        if isinstance(seed,RegressionSeed): case_id,attack_class,payload=seed.case_id,seed.attack_class,dict(seed.payload)
        else:
            case_id=str(seed.get("case_id") or seed.get("id") or "seed"); attack_class=str(seed.get("attack_class") or seed.get("category") or "unknown"); payload=dict(seed.get("payload") or seed)
        variants=[("field_order",dict(sorted(payload.items(),reverse=True))),
                  ("case_variant",{k:(v.swapcase() if isinstance(v,str) else v) for k,v in payload.items()}),
                  ("whitespace_variant",{k:(f" {v} " if isinstance(v,str) else v) for k,v in payload.items()}),
                  ("nested_variant",{"nested":dict(payload)})]
        out=[]
        for variant,data in variants[:max(0,int(max_variants))]:
            out.append(CounterCase(f"{case_id}:{_hash({'variant':variant,'payload':data})}",case_id,attack_class,variant,data))
        return tuple(out)

    def counter_cases(self): return tuple(sorted(self._counter_cases.values(),key=lambda x:x.case_id))
    def evolve_counter_cases(self,*,max_variants_per_seed=4):
        generated=[]
        for seed in sorted(self._seeds.values(),key=lambda x:x.case_id):
            if seed.status not in {"active","promoted"}: continue
            for case in self.generate_counter_cases(seed,max_variants=max_variants_per_seed):
                if case.case_id not in self._counter_cases: self._counter_cases[case.case_id]=case; generated.append(case)
        while len(self._counter_cases)>self.max_counter_cases:
            del self._counter_cases[sorted(self._counter_cases)[0]]
        return tuple(generated)

    def corpus(self):
        active=[s for s in self._seeds.values() if s.status in {"active","promoted"}]
        out=[dict(s.payload,id=s.case_id) for s in active]
        out.extend(dict(c.payload,id=c.case_id,parent_case_id=c.parent_case_id,variant=c.variant,category=c.attack_class) for c in self._counter_cases.values()
                   if c.parent_case_id in {s.case_id for s in active})
        return tuple(out)

    def report(self,*,newly_retired=0,reactivated=0):
        return SecurityEvolutionReport(self.effectiveness(),self.regressions(),self.counter_cases(),
            tuple(sorted(self._seeds.values(),key=lambda x:x.case_id)),newly_retired,reactivated)

    def export(self):
        return {"version":1,"observations":[{"sequence":n,**o.to_dict()} for n,o in self._observations],
                "effectiveness":[x.to_dict() for x in self.effectiveness()],"regressions":[x.to_dict() for x in self.regressions()],
                "seeds":[x.to_dict() for x in self._seeds.values()],"counter_cases":[x.to_dict() for x in self.counter_cases()],
                "corpus":[dict(x) for x in self.corpus()]}

    def reset(self):
        self._clock=0; self._observations.clear(); self._effectiveness.clear(); self._seeds.clear(); self._counter_cases.clear()

def evolve_security_capabilities(observations: Iterable[DefenseObservation], *, store=None, generate_counter_cases=True):
    target=store or SecurityEvolutionStore()
    for observation in observations: target.observe(observation)
    if generate_counter_cases: target.evolve_counter_cases()
    return target.report()


@dataclass(frozen=True)
class SecurityEvolutionPipelineReport:
    """Unified deterministic evaluation → learning → evolution report."""

    evaluation: Mapping[str, Any]
    learning: Mapping[str, Any]
    evolution: SecurityEvolutionReport
    active_corpus: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation": dict(self.evaluation),
            "learning": dict(self.learning),
            "evolution": self.evolution.to_dict(),
            "active_corpus": [dict(item) for item in self.active_corpus],
        }


def evaluate_and_evolve_security(*, provider: str = "playwright", origin: str = "https://trusted.example", schema: object | None = None, defense: str = "content_boundary", learning_store: Any | None = None, evolution_store: SecurityEvolutionStore | None = None, generate_counter_cases: bool = True) -> SecurityEvolutionPipelineReport:
    """Run the deterministic evaluator through both security learning layers.

    No generated counter-case or website tool is executed here. The caller owns
    replay, persistence, authorization, and remediation.
    """
    from .webmcp_learning import SecurityLearningStore, observation_from_evaluation
    evaluation = evaluate_webmcp_security()
    learner = learning_store or SecurityLearningStore()
    evolver = evolution_store or SecurityEvolutionStore()
    schema_value = schema if schema is not None else {"type": "object"}
    observations = observation_from_evaluation(evaluation, provider=provider, origin=origin, schema=schema_value, defense=defense)
    newly_promoted = 0
    for observation in observations: newly_promoted += learner.observe(observation).newly_promoted
    for promoted in learner.promoted_cases():
        if evolver.seed(promoted.case_id) is not None:
            continue
        evolver.promote_seed(case_id=promoted.case_id, attack_class=promoted.attack_class, defense=promoted.defense, payload=promoted.regression_payload, source=promoted.source)
    if generate_counter_cases: evolver.evolve_counter_cases()
    return SecurityEvolutionPipelineReport(evaluation=evaluation.to_dict(), learning={**learner.report(newly_promoted=newly_promoted).to_dict(), "replay_ready": True}, evolution=evolver.report(), active_corpus=evolver.corpus())
