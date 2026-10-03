"""Capability-aware browser provider selection for AnyBridge."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Iterable


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    browsers: tuple[str, ...]
    capabilities: frozenset[str]
    optional: bool = True


PROVIDERS = (
    ProviderProfile(
        "playwright",
        ("chromium", "firefox", "webkit"),
        frozenset({"webmcp", "network_interception", "screenshot", "storage_state", "bdd"}),
        False,
    ),
    ProviderProfile(
        "selenium",
        ("chrome", "firefox", "edge"),
        frozenset({"webmcp", "network_interception", "screenshot", "storage_state", "bdd"}),
        True,
    ),
)


def provider_profiles() -> tuple[ProviderProfile, ...]:
    return PROVIDERS


def _provider_available(name: str) -> bool:
    return find_spec("playwright") is not None if name == "playwright" else (
        find_spec("selenium") is not None if name == "selenium" else False
    )


def available_providers() -> tuple[str, ...]:
    return tuple(p.name for p in PROVIDERS if _provider_available(p.name))


def select_provider(
    required: Iterable[str] = (),
    preferred: str | None = None,
    browser: str | None = None,
    available: Iterable[str] | None = None,
) -> str:
    """Select a compatible, locally available provider deterministically."""
    required = frozenset(required)
    available_set = frozenset(
        available if available is not None else available_providers()
    )
    candidates = [
        p for p in PROVIDERS
        if p.name in available_set and required <= p.capabilities
    ]
    if browser:
        candidates = [p for p in candidates if browser in p.browsers]
    if preferred:
        for p in candidates:
            if p.name == preferred:
                return p.name
    if not candidates:
        raise ValueError(
            f"No provider satisfies capabilities={sorted(required)!r}, "
            f"browser={browser!r}, available={sorted(available_set)!r}"
        )
    return candidates[0].name


def select_provider_profile(
    required: Iterable[str] = (),
    preferred: str | None = None,
    browser: str | None = None,
    available: Iterable[str] | None = None,
) -> ProviderProfile:
    selected = select_provider(required, preferred, browser, available)
    return next(p for p in PROVIDERS if p.name == selected)


def default_browser(provider: str) -> str:
    profile = next((p for p in PROVIDERS if p.name == provider), None)
    if profile is None:
        raise ValueError(f"Unknown provider: {provider!r}")
    return profile.browsers[0]
