"""Deterministic contextual defense selection for WebMCP security.

Turns observed defense outcomes into an auditable decision without executing
anything. Exact context matches are weighted most heavily, with bounded fallback
to attack-family history. Caller remains responsible for authorization/execution.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
from .webmcp_security_evolution import DefenseObservation

@dataclass(frozen=True)
class DefenseCandidate:
    defense: str
    score: float
    attempts: int
    block_rate: float
    confidence: float
    context_matches: int
    reason: str
    def to_dict(self): return self.__dict__.copy()

@dataclass(frozen=True)
class DefenseDecision:
    attack_class: str
    provider: str
    origin: str
    schema_hash: str
    selected: str | None
    confidence: float
    candidates: tuple[DefenseCandidate, ...]
    counterfactuals: tuple[str, ...]
    def to_dict(self):
        return {**self.__dict__, "candidates":[x.to_dict() for x in self.candidates],
                "counterfactuals":list(self.counterfactuals)}

def select_defense(observations: Iterable[DefenseObservation], *, attack_class: str,
                   provider: str = "", origin: str = "", schema_hash: str = "",
                   min_attempts: int = 1) -> DefenseDecision:
    grouped = {}
    for o in observations:
        if o.attack_class != attack_class: continue
        key = o.defense
        if not key: continue
        exact = int(bool(provider and o.provider == provider)) + int(bool(origin and o.origin == origin)) + int(bool(schema_hash and o.schema_hash == schema_hash))
        bucket = grouped.setdefault(key, [0, 0, 0.0, 0])
        bucket[0] += 1
        bucket[1] += int(o.blocked)
        bucket[2] += max(0.0, min(1.0, float(o.evidence_confidence)))
        bucket[3] += exact
    candidates = []
    for defense, (attempts, blocked, confidence_sum, matches) in grouped.items():
        if attempts < max(1, int(min_attempts)): continue
        rate = blocked / attempts
        confidence = confidence_sum / attempts
        context = matches / max(1, attempts * 3)
        score = 0.60 * rate + 0.25 * confidence + 0.15 * context
        candidates.append(DefenseCandidate(defense, score, attempts, rate, confidence, matches,
            "context-weighted block rate, evidence confidence, and exact-context match"))
    candidates = tuple(sorted(candidates, key=lambda x:(-x.score, -x.block_rate, x.defense)))
    selected = candidates[0].defense if candidates else None
    confidence = candidates[0].score if candidates else 0.0
    return DefenseDecision(attack_class, provider, origin, schema_hash, selected, confidence,
                           candidates, tuple(x.defense for x in candidates[1:]))
