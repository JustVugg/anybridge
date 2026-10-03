"""Local self-healing target resolution and execution journal.

This module is deliberately orchestrator-agnostic. It provides bounded,
confidence-gated recovery primitives; callers retain execution authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping
import time


@dataclass(frozen=True)
class TargetFingerprint:
    role: str | None = None
    name: str | None = None
    text: str | None = None
    label: str | None = None
    tag: str | None = None
    href: str | None = None

    def score(self, candidate: "TargetFingerprint") -> float:
        weighted = (
            (self.role, candidate.role, 0.20),
            (self.name, candidate.name, 0.25),
            (self.text, candidate.text, 0.20),
            (self.label, candidate.label, 0.20),
            (self.tag, candidate.tag, 0.05),
            (self.href, candidate.href, 0.10),
        )
        total = 0.0
        weight = 0.0
        for left, right, w in weighted:
            if not left or not right:
                continue
            ratio = SequenceMatcher(None, str(left).strip().lower(), str(right).strip().lower()).ratio()
            total += ratio * w
            weight += w
        return total / weight if weight else 0.0


@dataclass(frozen=True)
class TargetCandidate:
    ref: str
    fingerprint: TargetFingerprint
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    target_ref: str | None
    confidence: float
    threshold: float
    requires_verification: bool
    reasons: tuple[str, ...] = ()


def rank_target_candidates(
    expected: TargetFingerprint,
    candidates: Iterable[tuple[str, TargetFingerprint]],
    *,
    minimum_score: float = 0.55,
) -> tuple[TargetCandidate, ...]:
    ranked = []
    for ref, fingerprint in candidates:
        score = expected.score(fingerprint)
        if score >= minimum_score:
            reasons = []
            for field in ("role", "name", "text", "label", "tag", "href"):
                if getattr(expected, field) and getattr(fingerprint, field):
                    reasons.append(f"{field}_match")
            ranked.append(TargetCandidate(ref, fingerprint, score, tuple(reasons)))
    return tuple(sorted(ranked, key=lambda item: (-item.score, item.ref)))


def decide_target_recovery(
    expected: TargetFingerprint,
    candidates: Iterable[tuple[str, TargetFingerprint]],
    *,
    confidence_threshold: float = 0.88,
) -> RecoveryDecision:
    ranked = rank_target_candidates(expected, candidates)
    if not ranked:
        return RecoveryDecision(
            action="refresh_snapshot",
            target_ref=None,
            confidence=0.0,
            threshold=confidence_threshold,
            requires_verification=True,
            reasons=("no_semantically_similar_target",),
        )
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    margin = best.score - second.score if second else best.score
    if best.score >= confidence_threshold and margin >= 0.08:
        return RecoveryDecision(
            action="retry_with_reidentified_target",
            target_ref=best.ref,
            confidence=best.score,
            threshold=confidence_threshold,
            requires_verification=True,
            reasons=best.reasons + ("clear_candidate_margin",),
        )
    return RecoveryDecision(
        action="refresh_snapshot",
        target_ref=None,
        confidence=best.score,
        threshold=confidence_threshold,
        requires_verification=True,
        reasons=best.reasons + ("ambiguous_or_low_confidence",),
    )


@dataclass(frozen=True)
class JournalEvent:
    event_id: str
    execution_id: str
    kind: str
    timestamp_ms: int
    action: str | None = None
    target: str | None = None
    status: str = "observed"
    observed: Any = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    remediation_id: str | None = None


class ExecutionJournal:
    """Bounded in-memory journal; callers can persist exported records if desired."""

    def __init__(self, *, max_events: int = 1000) -> None:
        self.max_events = max(1, int(max_events))
        self._events: list[JournalEvent] = []

    def append(
        self,
        *,
        event_id: str,
        execution_id: str,
        kind: str,
        action: str | None = None,
        target: str | None = None,
        status: str = "observed",
        observed: Any = None,
        evidence: Mapping[str, Any] | None = None,
        remediation_id: str | None = None,
        timestamp_ms: int | None = None,
    ) -> JournalEvent:
        event = JournalEvent(
            event_id=event_id,
            execution_id=execution_id,
            kind=kind,
            timestamp_ms=int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms),
            action=action,
            target=target,
            status=status,
            observed=observed,
            evidence=dict(evidence or {}),
            remediation_id=remediation_id,
        )
        self._events.append(event)
        if len(self._events) > self.max_events:
            del self._events[: len(self._events) - self.max_events]
        return event

    def events(self, execution_id: str | None = None) -> tuple[JournalEvent, ...]:
        if execution_id is None:
            return tuple(self._events)
        return tuple(e for e in self._events if e.execution_id == execution_id)

    def last(self, execution_id: str | None = None) -> JournalEvent | None:
        events = self.events(execution_id)
        return events[-1] if events else None

    def export(self, execution_id: str | None = None) -> list[dict[str, Any]]:
        return [asdict(e) for e in self.events(execution_id)]
