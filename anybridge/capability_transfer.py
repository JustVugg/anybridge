"""Deterministic cross-domain transfer of verified capabilities.

Transfer abstracts reusable defensive structure from graduated capabilities and
tests it against an explicitly different target domain. It is advisory only:
callers own holdout execution, authorization, persistence, canaries, and rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable


def _bounded(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class TransferPattern:
    pattern_id: str
    source_capability_ids: tuple[str, ...]
    source_domain: str
    abstract_strategy: str
    prerequisites: tuple[str, ...] = ()
    confidence: float = 0.0

    def to_dict(self):
        return self.__dict__.copy()


@dataclass(frozen=True)
class TransferHypothesis:
    transfer_id: str
    pattern_id: str
    target_domain: str
    target_capability_class: str
    structural_match: float
    confidence: float
    rationale: str
    status: str = "candidate"

    def to_dict(self):
        return self.__dict__.copy()


@dataclass(frozen=True)
class TransferObservation:
    transfer_id: str
    passed: bool
    holdout: bool
    evidence_confidence: float = 0.0
    source_domain: str = ""
    target_domain: str = ""
    execution_id: str = ""
    details: str = ""

    def to_dict(self):
        return self.__dict__.copy()


@dataclass(frozen=True)
class TransferDecision:
    transfer_id: str
    action: str
    status: str
    confidence: float
    reason: str
    hypothesis: TransferHypothesis

    def to_dict(self):
        return {**self.__dict__, "hypothesis": self.hypothesis.to_dict()}


class CapabilityTransferStore:
    """Bounded, deterministic transfer learning state.

    A transfer is validated only on target-domain holdout evidence. A validated
    transfer may be canaried by the caller; failed canaries roll it back.
    """

    def __init__(
        self,
        *,
        max_patterns=512,
        max_hypotheses=1024,
        max_observations=4096,
        min_holdout=3,
        min_holdout_rate=0.9,
        canary_min_attempts=2,
        canary_min_rate=0.9,
        rollback_rate=0.75,
    ):
        self.max_patterns = max(1, int(max_patterns))
        self.max_hypotheses = max(1, int(max_hypotheses))
        self.max_observations = max(1, int(max_observations))
        self.min_holdout = max(1, int(min_holdout))
        self.min_holdout_rate = _bounded(min_holdout_rate)
        self.canary_min_attempts = max(1, int(canary_min_attempts))
        self.canary_min_rate = _bounded(canary_min_rate)
        self.rollback_rate = _bounded(rollback_rate)
        self._patterns = {}
        self._hypotheses = {}
        self._observations = []
        self._canary = {}

    @staticmethod
    def _pattern_id(source_ids, source_domain):
        ids = "::".join(sorted(dict.fromkeys(str(x) for x in source_ids)))
        return f"transfer-pattern::{source_domain}::{ids}"

    @staticmethod
    def _transfer_id(pattern_id, target_domain, target_class):
        return f"transfer::{pattern_id}::{target_domain}::{target_class}"

    def abstract_patterns(self, primitives: Iterable, *, source_domain="webmcp"):
        """Abstract verified primitives into reusable strategy patterns."""
        ps = tuple(sorted(
            (p for p in primitives if getattr(p, "confidence", 0.0) > 0),
            key=lambda x: x.capability_id,
        ))
        out = []
        for length in range(1, min(3, len(ps)) + 1):
            for combo in combinations(ps, length):
                ids = tuple(x.capability_id for x in combo)
                strategies = tuple(sorted(dict.fromkeys(str(x.defense) for x in combo)))
                prerequisites = tuple(sorted({
                    dep for x in combo for dep in getattr(x, "dependencies", ())
                    if dep not in ids
                }))
                confidence = sum(_bounded(x.confidence) for x in combo) / length
                pattern = TransferPattern(
                    self._pattern_id(ids, source_domain),
                    ids,
                    str(source_domain),
                    " + ".join(strategies) or "verified defensive boundary",
                    prerequisites,
                    confidence,
                )
                self._patterns[pattern.pattern_id] = pattern
                out.append(pattern)
        self._trim(self._patterns, self.max_patterns)
        return tuple(sorted(out, key=lambda x: (-x.confidence, x.pattern_id)))

    def propose(
        self,
        *,
        target_domain,
        target_capability_class,
        source_domain=None,
        max_results=16,
    ):
        """Propose transfers only when target domain differs from the source."""
        target_domain = str(target_domain)
        target_class = str(target_capability_class)
        out = []
        for pattern in sorted(self._patterns.values(), key=lambda x: x.pattern_id):
            if source_domain is not None and pattern.source_domain != str(source_domain):
                continue
            if pattern.source_domain == target_domain:
                continue
            class_match = self._class_match(pattern, target_class)
            structural_match = _bounded(
                0.6 * class_match + 0.4 * (1.0 if pattern.prerequisites else 0.75)
            )
            confidence = _bounded(pattern.confidence * structural_match)
            transfer_id = self._transfer_id(pattern.pattern_id, target_domain, target_class)
            hypothesis = TransferHypothesis(
                transfer_id,
                pattern.pattern_id,
                target_domain,
                target_class,
                structural_match,
                confidence,
                "transfer verified defensive structure to an unseen domain; validate independently",
            )
            self._hypotheses[transfer_id] = hypothesis
            out.append(hypothesis)
        self._trim(self._hypotheses, self.max_hypotheses)
        return tuple(sorted(
            out, key=lambda x: (-x.confidence, -x.structural_match, x.transfer_id)
        )[:max(1, int(max_results))])

    @staticmethod
    def _class_match(pattern, target_class):
        text = f"{pattern.abstract_strategy} {pattern.source_domain}".casefold()
        target = str(target_class).casefold()
        tokens = [t for t in target.replace("-", " ").replace("_", " ").split() if len(t) > 2]
        if not tokens:
            return 0.5
        return sum(t in text for t in tokens) / len(tokens)

    def observe(self, observation: TransferObservation):
        if observation.transfer_id not in self._hypotheses:
            raise KeyError(observation.transfer_id)
        self._observations.append(observation)
        if len(self._observations) > self.max_observations:
            self._observations = self._observations[-self.max_observations:]
        return self.decision(observation.transfer_id)

    def _holdout_stats(self, transfer_id):
        xs = [
            x for x in self._observations
            if x.transfer_id == transfer_id and x.holdout
        ]
        rate = sum(int(x.passed) for x in xs) / len(xs) if xs else 0.0
        confidence = (
            sum(_bounded(x.evidence_confidence) for x in xs) / len(xs)
            if xs else 0.0
        )
        return len(xs), rate, confidence

    def decision(self, transfer_id):
        h = self._hypotheses[transfer_id]
        n, rate, evidence = self._holdout_stats(transfer_id)
        state = h.status
        if n >= self.min_holdout and rate >= self.min_holdout_rate:
            state = "validated"
            action = "run_canary"
            reason = "unseen-domain holdout threshold reached"
        elif n >= self.min_holdout:
            state = "rejected"
            action = "quarantine"
            reason = "unseen-domain holdout failed"
        else:
            state = "candidate"
            action = "run_unseen_domain_holdout"
            reason = "awaiting independent unseen-domain evidence"
        confidence = max(h.confidence, evidence)
        updated = TransferHypothesis(
            h.transfer_id, h.pattern_id, h.target_domain, h.target_capability_class,
            h.structural_match, confidence, h.rationale, state,
        )
        self._hypotheses[transfer_id] = updated
        return TransferDecision(transfer_id, action, state, confidence, reason, updated)

    def canary_result(self, transfer_id, observations: Iterable[TransferObservation]):
        """Record caller-executed canary outcomes and graduate or roll back."""
        if transfer_id not in self._hypotheses:
            raise KeyError(transfer_id)
        for observation in observations:
            if observation.transfer_id != transfer_id:
                raise ValueError("canary observation transfer_id mismatch")
            if observation.holdout:
                raise ValueError("canary observations must not be holdout observations")
            self._observations.append(observation)
        xs = [x for x in self._observations if x.transfer_id == transfer_id and not x.holdout]
        if len(xs) > self.max_observations:
            self._observations = self._observations[-self.max_observations:]
            xs = [x for x in self._observations if x.transfer_id == transfer_id and not x.holdout]
        passed = sum(int(x.passed) for x in xs)
        rate = passed / len(xs) if xs else 0.0
        h = self._hypotheses[transfer_id]
        if h.status in ("validated", "graduated") and len(xs) >= self.canary_min_attempts and rate < self.rollback_rate:
            status, action, reason = "rolled_back", "rollback_transfer", "canary regression detected"
        elif h.status in ("validated", "graduated") and len(xs) >= self.canary_min_attempts and rate >= self.canary_min_rate:
            status, action, reason = "graduated", "promote_transfer", "canary threshold passed"
        else:
            status, action, reason = h.status, "continue_canary", "awaiting canary threshold"
        updated = TransferHypothesis(
            h.transfer_id, h.pattern_id, h.target_domain, h.target_capability_class,
            h.structural_match, max(h.confidence, rate), h.rationale, status,
        )
        self._hypotheses[transfer_id] = updated
        self._canary[transfer_id] = {"attempts": len(xs), "pass_rate": rate}
        return TransferDecision(transfer_id, action, status, updated.confidence, reason, updated)

    def active(self):
        return tuple(
            self._hypotheses[k] for k in sorted(self._hypotheses)
            if self._hypotheses[k].status == "graduated"
        )

    def patterns(self):
        return tuple(self._patterns[k] for k in sorted(self._patterns))

    def hypotheses(self):
        return tuple(self._hypotheses[k] for k in sorted(self._hypotheses))

    def export(self):
        return {
            "version": 1,
            "patterns": [x.to_dict() for x in self.patterns()],
            "hypotheses": [x.to_dict() for x in self.hypotheses()],
            "observations": [x.to_dict() for x in self._observations],
            "canary": {k: dict(v) for k, v in sorted(self._canary.items())},
        }

    def reset(self):
        self._patterns.clear()
        self._hypotheses.clear()
        self._observations.clear()
        self._canary.clear()

    @staticmethod
    def _trim(mapping, limit):
        while len(mapping) > limit:
            del mapping[sorted(mapping)[0]]
