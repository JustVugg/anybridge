"""Provider-neutral evidence envelopes for browser execution and QA validation."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass
class EvidenceEnvelope:
    test_id: str | None = None
    execution_id: str | None = None
    url: str | None = None
    action: str | None = None
    expected: Any = None
    observed: Any = None
    assertion: str | None = None
    status: str = "observed"
    screenshot: str | None = None
    dom_snapshot: str | None = None
    console_errors: list[str] = field(default_factory=list)
    network_errors: list[dict[str, Any]] = field(default_factory=list)
    timing_ms: float | None = None
    browser: str | None = None
    provider: str | None = None
    environment: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def evidence_status(assertion: bool | None) -> str:
    if assertion is True:
        return "passed"
    if assertion is False:
        return "failed"
    return "observed"
