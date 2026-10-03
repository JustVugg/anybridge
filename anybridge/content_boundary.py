"""Structured trust boundaries for page-controlled WebMCP content.

Anything supplied by a web page is data, not an AnyBridge/system instruction.
This module keeps that distinction explicit in machine-readable envelopes and
optionally spotlighted/base64 encoded payloads. It does not attempt to prove
that arbitrary text is non-malicious.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping


_ALLOWED_SOURCES = {"webmcp_page", "webmcp_tool_output", "webmcp_error"}
_ALLOWED_ENCODINGS = {"identity", "spotlight", "base64"}
_ALLOWED_TRUST = {"untrusted", "page_controlled_data"}


@dataclass(frozen=True)
class ContentEnvelope:
    source: str
    content_type: str
    trust: str
    instruction_authority: str
    origin: str
    tool_name: str
    content: Any
    encoding: str = "identity"
    boundary: str | None = None
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        return data


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return repr(value)


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:16]


def _clean_boundary(value: str, boundary: str) -> str:
    return value.replace(boundary, "[BOUNDARY_REMOVED]")


def spotlight(value: Any, boundary: str | None = None) -> tuple[str, str]:
    """Return text with a per-envelope boundary that cannot be injected by payload text."""
    marker = boundary or secrets.token_hex(16)
    text = value if isinstance(value, str) else _stable_json(value)
    text = _clean_boundary(text, marker)
    return f"[UNTRUSTED_WEBMCP:{marker}]\n{text}\n[/UNTRUSTED_WEBMCP:{marker}]", marker


def encode_untrusted(value: Any) -> str:
    """Encode a payload for higher-risk contexts without executing or interpreting it."""
    raw = value if isinstance(value, str) else _stable_json(value)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _envelope_payload(envelope: ContentEnvelope) -> str:
    """Recover the serialized payload used to calculate the envelope hash."""
    if envelope.encoding == "identity":
        return _stable_json(envelope.content)
    if envelope.encoding == "base64":
        try:
            return base64.b64decode(envelope.content, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, TypeError):
            raise ValueError("invalid WebMCP Base64 quarantine payload") from None
    if envelope.encoding == "spotlight":
        marker = envelope.boundary
        if not marker:
            raise ValueError("spotlight envelope is missing its boundary")
        prefix = f"[UNTRUSTED_WEBMCP:{marker}]\n"
        suffix = f"\n[/UNTRUSTED_WEBMCP:{marker}]"
        if not (isinstance(envelope.content, str) and envelope.content.startswith(prefix)
                and envelope.content.endswith(suffix)):
            raise ValueError("invalid WebMCP spotlight envelope")
        return envelope.content[len(prefix):-len(suffix)] if suffix else envelope.content
    raise ValueError(f"unsupported WebMCP content encoding: {envelope.encoding}")


def enforce_quarantine(
    envelope: ContentEnvelope | Mapping[str, Any],
    *,
    expected_origin: str | None = None,
) -> ContentEnvelope:
    """Reject forged or structurally unsafe page-content envelopes.

    This is intentionally deterministic: it validates provenance, authority,
    encoding, boundary structure, and the content fingerprint. It is not a
    semantic prompt-injection detector.
    """
    if isinstance(envelope, ContentEnvelope):
        value = envelope
    elif isinstance(envelope, Mapping):
        try:
            value = ContentEnvelope(**dict(envelope))
        except TypeError as exc:
            raise ValueError("malformed WebMCP content envelope") from exc
    else:
        raise TypeError("WebMCP content must be a ContentEnvelope or mapping")

    if value.source not in _ALLOWED_SOURCES:
        raise ValueError("unrecognized WebMCP content source")
    if value.trust not in _ALLOWED_TRUST:
        raise ValueError("invalid WebMCP content trust")
    if value.instruction_authority != "none":
        raise PermissionError("page-controlled WebMCP content cannot have instruction authority")
    if value.encoding not in _ALLOWED_ENCODINGS:
        raise ValueError("unsupported WebMCP content encoding")
    if not isinstance(value.origin, str) or not value.origin:
        raise ValueError("WebMCP content is missing its origin")
    if expected_origin is not None and value.origin != expected_origin:
        raise PermissionError("WebMCP content origin does not match the active page origin")
    if not isinstance(value.tool_name, str) or not value.tool_name:
        raise ValueError("WebMCP content is missing its tool name")

    payload = _envelope_payload(value)
    if value.encoding == "identity":
        observed_hash = content_hash(value.content)
    else:
        observed_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    if observed_hash != value.content_hash:
        raise ValueError("WebMCP content fingerprint mismatch")

    if value.encoding == "spotlight":
        marker = value.boundary or ""
        if marker in payload:
            raise ValueError("WebMCP spotlight boundary token appears inside payload")

    return value


def wrap_tool_metadata(
    *,
    origin: str,
    name: str,
    description: str,
    schema: Mapping[str, Any],
    annotations: Mapping[str, Any] | None = None,
) -> ContentEnvelope:
    """Represent a page-provided tool definition as data with no instruction authority."""
    annotations = dict(annotations or {})
    payload = {
        "name": name,
        "description": description,
        "inputSchema": dict(schema),
        "annotations": annotations,
    }
    envelope = ContentEnvelope(
        source="webmcp_page",
        content_type="tool_definition",
        trust="untrusted",
        instruction_authority="none",
        origin=origin,
        tool_name=name,
        content=payload,
        content_hash=content_hash(payload),
    )
    return enforce_quarantine(envelope)


def wrap_tool_output(
    *,
    origin: str,
    name: str,
    value: Any,
    untrusted: bool,
    high_risk: bool = False,
) -> ContentEnvelope:
    """Wrap a WebMCP result so callers can distinguish data from instructions."""
    if untrusted or high_risk:
        encoded = encode_untrusted(value)
        envelope = ContentEnvelope(
            source="webmcp_tool_output",
            content_type="tool_result",
            trust="untrusted",
            instruction_authority="none",
            origin=origin,
            tool_name=name,
            content=encoded,
            encoding="base64",
            content_hash=content_hash(value),
        )
        return enforce_quarantine(envelope)

    marked, boundary = spotlight(value)
    envelope = ContentEnvelope(
        source="webmcp_tool_output",
        content_type="tool_result",
        trust="page_controlled_data",
        instruction_authority="none",
        origin=origin,
        tool_name=name,
        content=marked,
        encoding="spotlight",
        boundary=boundary,
        content_hash=content_hash(value),
    )
    return enforce_quarantine(envelope)


def wrap_webmcp_error(
    *,
    origin: str,
    name: str,
    error: str,
) -> ContentEnvelope:
    marked, boundary = spotlight(error)
    envelope = ContentEnvelope(
        source="webmcp_error",
        content_type="error",
        trust="untrusted",
        instruction_authority="none",
        origin=origin,
        tool_name=name,
        content=marked,
        encoding="spotlight",
        boundary=boundary,
        content_hash=content_hash(error),
    )
    return enforce_quarantine(envelope)
