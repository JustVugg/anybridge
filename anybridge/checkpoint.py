"""Crash-safe execution checkpoints.

The checkpoint is a declarative resume boundary, not an instruction to
re-execute side effects. Callers must verify the browser state before resuming.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
import json
import os
import tempfile
import time


@dataclass(frozen=True)
class ExecutionCheckpoint:
    execution_id: str
    checkpoint_id: str
    step_index: int
    action: str
    target: str | None
    url: str | None
    expected: Any = None
    observed: Any = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    safe_to_resume: bool = False
    resume_strategy: str = "revalidate_then_resume"
    created_at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionCheckpoint":
        return cls(
            execution_id=str(value["execution_id"]),
            checkpoint_id=str(value["checkpoint_id"]),
            step_index=int(value["step_index"]),
            action=str(value["action"]),
            target=value.get("target"),
            url=value.get("url"),
            expected=value.get("expected"),
            observed=value.get("observed"),
            evidence=dict(value.get("evidence") or {}),
            safe_to_resume=bool(value.get("safe_to_resume", False)),
            resume_strategy=str(value.get("resume_strategy", "revalidate_then_resume")),
            created_at_ms=int(value.get("created_at_ms") or 0),
        )


class CheckpointStore:
    """Small atomic JSON checkpoint store with no third-party dependency."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)

    def save(self, checkpoint: ExecutionCheckpoint) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = json.dumps(checkpoint.to_dict(), ensure_ascii=False, sort_keys=True)
        fd, temporary = tempfile.mkstemp(prefix=".anybridge-checkpoint-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> ExecutionCheckpoint | None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                return ExecutionCheckpoint.from_dict(json.load(handle))
        except FileNotFoundError:
            return None

    def clear(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class ResumeDecision:
    action: str
    checkpoint_id: str | None
    step_index: int | None
    confidence: float
    requires_revalidation: bool
    reasons: tuple[str, ...] = ()


def decide_resume(
    checkpoint: ExecutionCheckpoint | None,
    *,
    current_url: str | None = None,
    current_target_exists: bool | None = None,
    recovery_confidence: float = 0.0,
) -> ResumeDecision:
    if checkpoint is None:
        return ResumeDecision("start_new", None, None, 0.0, False, ("no_checkpoint",))
    if not checkpoint.safe_to_resume:
        return ResumeDecision(
            "revalidate_before_resume",
            checkpoint.checkpoint_id,
            checkpoint.step_index,
            recovery_confidence,
            True,
            ("checkpoint_requires_revalidation",),
        )
    reasons = []
    confidence = 1.0
    if checkpoint.url and current_url:
        if checkpoint.url != current_url:
            return ResumeDecision(
                "revalidate_before_resume",
                checkpoint.checkpoint_id,
                checkpoint.step_index,
                recovery_confidence,
                True,
                ("url_changed",),
            )
        reasons.append("url_matches")
    if current_target_exists is False:
        return ResumeDecision(
            "reidentify_target_before_resume",
            checkpoint.checkpoint_id,
            checkpoint.step_index,
            recovery_confidence,
            True,
            ("target_missing",),
        )
    if recovery_confidence:
        confidence = min(confidence, recovery_confidence)
    if confidence < 0.88:
        return ResumeDecision(
            "revalidate_before_resume",
            checkpoint.checkpoint_id,
            checkpoint.step_index,
            confidence,
            True,
            tuple(reasons) + ("recovery_confidence_below_threshold",),
        )
    return ResumeDecision(
        "resume_from_checkpoint",
        checkpoint.checkpoint_id,
        checkpoint.step_index,
        confidence,
        True,
        tuple(reasons) + ("checkpoint_verified",),
    )


def make_checkpoint(
    *,
    execution_id: str,
    checkpoint_id: str,
    step_index: int,
    action: str,
    target: str | None,
    url: str | None,
    expected: Any = None,
    observed: Any = None,
    evidence: Mapping[str, Any] | None = None,
    safe_to_resume: bool = False,
    resume_strategy: str = "revalidate_then_resume",
) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        execution_id=execution_id,
        checkpoint_id=checkpoint_id,
        step_index=step_index,
        action=action,
        target=target,
        url=url,
        expected=expected,
        observed=observed,
        evidence=dict(evidence or {}),
        safe_to_resume=safe_to_resume,
        resume_strategy=resume_strategy,
        created_at_ms=int(time.time() * 1000),
    )
