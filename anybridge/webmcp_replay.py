"""Replay feedback primitives for WebMCP security capability evolution.

AnyBridge never executes adversarial cases here. A caller supplies realized replay
outcomes; this module normalizes them and feeds the same learning/evolution stores
used by the deterministic security pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .webmcp_learning import SecurityLearningStore, SecurityObservation, schema_fingerprint
from .webmcp_security_evolution import DefenseObservation, SecurityEvolutionStore, SecurityEvolutionReport

@dataclass(frozen=True)
class SecurityReplayCase:
    case_id: str
    attack_id: str
    attack_class: str
    provider: str
    origin: str
    schema_hash: str
    defense: str
    payload: Mapping[str, Any]
    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "payload": dict(self.payload)}

@dataclass(frozen=True)
class SecurityReplayOutcome:
    case_id: str
    blocked: bool
    evidence_confidence: float = 0.5
    latency_ms: float | None = None
    execution_id: str | None = None
    details: Mapping[str, Any] | None = None
    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "details": dict(self.details or {})}

@dataclass(frozen=True)
class SecurityReplayReport:
    cases: int
    outcomes: int
    ignored_outcomes: int
    observations: int
    learning: Mapping[str, Any]
    evolution: SecurityEvolutionReport
    active_corpus: tuple[Mapping[str, Any], ...]
    def to_dict(self) -> dict[str, Any]:
        return {"cases": self.cases, "outcomes": self.outcomes, "ignored_outcomes": self.ignored_outcomes,
                "observations": self.observations, "learning": dict(self.learning),
                "evolution": self.evolution.to_dict(), "active_corpus": [dict(x) for x in self.active_corpus]}

def _case_from_mapping(item: Mapping[str, Any]) -> SecurityReplayCase:
    payload = dict(item.get("payload") or {})
    schema_hash = str(item.get("schema_hash") or schema_fingerprint(item.get("schema") or {"type": "object"}))
    return SecurityReplayCase(case_id=str(item.get("case_id") or item.get("id") or ""),
        attack_id=str(item.get("attack_id") or item.get("id") or item.get("case_id") or ""),
        attack_class=str(item.get("attack_class") or item.get("category") or "unknown"),
        provider=str(item.get("provider") or "unknown"), origin=str(item.get("origin") or ""),
        schema_hash=schema_hash, defense=str(item.get("defense") or "unknown"), payload=payload)

def replay_security_corpus(cases: Iterable[SecurityReplayCase | Mapping[str, Any]],
    outcomes: Iterable[SecurityReplayOutcome | Mapping[str, Any]], *,
    learning_store: SecurityLearningStore | None = None,
    evolution_store: SecurityEvolutionStore | None = None,
    generate_counter_cases: bool = True) -> SecurityReplayReport:
    """Feed caller-realized replay outcomes back into security learning/evolution.

    Missing or unknown case IDs are ignored rather than guessed.
    """
    normalized_cases = tuple(c if isinstance(c, SecurityReplayCase) else _case_from_mapping(c) for c in cases)
    by_id = {c.case_id: c for c in normalized_cases if c.case_id}
    learner = learning_store or SecurityLearningStore()
    evolver = evolution_store or SecurityEvolutionStore()
    ignored = outcome_count = 0
    for raw in outcomes:
        outcome = raw if isinstance(raw, SecurityReplayOutcome) else SecurityReplayOutcome(
            case_id=str(raw.get("case_id") or ""), blocked=bool(raw.get("blocked")),
            evidence_confidence=float(raw.get("evidence_confidence", 0.5)), latency_ms=raw.get("latency_ms"),
            execution_id=raw.get("execution_id"), details=raw.get("details"))
        case = by_id.get(outcome.case_id)
        if case is None:
            ignored += 1
            continue
        outcome_count += 1
        details = dict(outcome.details or {})
        details.setdefault("replay_case_id", case.case_id)
        learner.observe(SecurityObservation(case.attack_id, case.attack_class, case.provider, case.origin,
            case.schema_hash, case.defense, outcome.blocked, outcome.evidence_confidence, outcome.latency_ms,
            outcome.execution_id, details), regression_payload=case.payload)
        evolver.observe(DefenseObservation(case.attack_id, case.attack_class, case.provider, case.origin,
            case.schema_hash, case.defense, outcome.blocked, outcome.evidence_confidence, outcome.latency_ms,
            outcome.execution_id, details), regression_payload=case.payload)
        for promoted in learner.promoted_cases():
            if evolver.seed(promoted.case_id) is None:
                evolver.promote_seed(case_id=promoted.case_id, attack_class=promoted.attack_class,
                    defense=promoted.defense, payload=promoted.regression_payload, source=promoted.source)
    if generate_counter_cases:
        evolver.evolve_counter_cases()
    return SecurityReplayReport(cases=len(normalized_cases), outcomes=outcome_count, ignored_outcomes=ignored,
        observations=outcome_count, learning={**learner.report().to_dict(), "replay_ready": bool(evolver.corpus())},
        evolution=evolver.report(), active_corpus=evolver.corpus())