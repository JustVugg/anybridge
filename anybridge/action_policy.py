"""Deterministic action risk and retry policy.

AnyBridge classifies browser actions without executing them. Consequential or
non-idempotent actions are never silently approved for automatic replay.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable


class ActionRisk(str, Enum):
    READ = "read"
    NAVIGATE = "navigate"
    MUTATE = "mutate"
    CONSEQUENTIAL = "consequential"
    UNKNOWN = "unknown"


class RetryPolicy(str, Enum):
    AUTOMATIC = "automatic"
    REVALIDATE = "revalidate"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class ActionAssessment:
    action: str
    risk: ActionRisk
    idempotent: bool
    policy: RetryPolicy
    max_retries: int
    requires_confirmation: bool
    reasons: tuple[str, ...] = ()


_READ = {"read", "read_page", "snapshot", "observe", "extract", "screenshot", "list_links", "list_forms", "current_site", "network_policy", "list_webmcp_tools"}
_NAV = {"navigate", "open_saved_site", "wait_for"}
_SAFE_MUTATE = {"fill_ref", "type_text", "select_ref", "press_key", "save_checkpoint"}
_HIGH_RISK = {"submit_form", "click", "click_ref", "run_workflow", "run_bdd", "call_webmcp_tool", "save_profile", "remove_profile", "remove_saved_site", "remove_workflow", "remove_saved_repository", "remove_saved_site"}
_TERMS = re.compile(r"\b(delete|remove|purchase|buy|pay|checkout|transfer|send|submit|cancel|publish|deploy|destroy|drop|withdraw)\b", re.I)


def assess_action(action: str, *, target: str | None = None, consequential: bool | None = None) -> ActionAssessment:
    normalized = action.strip().lower()
    reasons: list[str] = []
    if consequential is True or _TERMS.search(normalized) or _TERMS.search(target or ""):
        return ActionAssessment(normalized, ActionRisk.CONSEQUENTIAL, False, RetryPolicy.CONFIRM, 0, True, ("consequential_or_non_reversible_signal",))
    if normalized in _READ:
        return ActionAssessment(normalized, ActionRisk.READ, True, RetryPolicy.AUTOMATIC, 2, False, ("read_only",))
    if normalized in _NAV:
        return ActionAssessment(normalized, ActionRisk.NAVIGATE, True, RetryPolicy.AUTOMATIC, 2, False, ("navigation_is_replayable",))
    if normalized in _SAFE_MUTATE:
        return ActionAssessment(normalized, ActionRisk.MUTATE, False, RetryPolicy.REVALIDATE, 1, False, ("state_mutation_requires_current_target",))
    if normalized in _HIGH_RISK:
        return ActionAssessment(normalized, ActionRisk.CONSEQUENTIAL, False, RetryPolicy.CONFIRM, 0, True, ("action_may_have_external_side_effects",))
    return ActionAssessment(normalized, ActionRisk.UNKNOWN, False, RetryPolicy.REVALIDATE, 0, False, ("unknown_action_defaults_to_revalidation",))


def assess_sequence(actions: Iterable[str]) -> tuple[ActionAssessment, ...]:
    return tuple(assess_action(action) for action in actions)
