"""Security boundaries for untrusted websites and remote AnyBridge sessions."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .sites import SiteStoreError, normalize_url


class UnsafeTargetError(SiteStoreError):
    """Raised when a remote browser target can reach a non-public network."""


class NetworkGuard:
    """Validate browser destinations and subresources against SSRF targets.

    Local AnyBridge sessions intentionally allow local development sites. Remote
    sessions use this guard so an MCP client cannot turn the hosted browser into
    a proxy for localhost, link-local metadata services, or private networks.
    """

    def __init__(self, *, allow_private: bool = True) -> None:
        self.allow_private = allow_private
        self._approved_hosts: set[str] = set()

    async def assert_url(self, url: str) -> str:
        target = normalize_url(url)
        if self.allow_private:
            return target
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeTargetError("Remote AnyBridge sessions only allow http(s) URLs.")
        if parsed.username or parsed.password:
            raise UnsafeTargetError("Credentials must not be embedded in a website URL.")
        host = (parsed.hostname or "").rstrip(".").casefold()
        if not host:
            raise UnsafeTargetError("The target URL has no hostname.")
        if host in self._approved_hosts:
            return target
        # Resolution is cached per host. A synchronous lookup also avoids leaving
        # executor threads behind in short-lived MCP/CLI processes on Windows.
        addresses = self._resolve(host, parsed.port)
        if not addresses:
            raise UnsafeTargetError(f'Could not resolve target host "{host}".')
        for address in addresses:
            if not self._is_public(address):
                raise UnsafeTargetError(
                    f'Remote access to non-public address "{address}" is blocked.'
                )
        self._approved_hosts.add(host)
        return target

    @staticmethod
    def _resolve(host: str, port: int | None) -> set[str]:
        try:
            return {
                item[4][0]
                for item in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
            }
        except socket.gaierror:
            return set()

    @staticmethod
    def _is_public(value: str) -> bool:
        address = ipaddress.ip_address(value)
        return bool(address.is_global)

    async def route(self, route) -> None:
        """Playwright route handler that applies the policy to every request."""
        url = route.request.url
        scheme = urlsplit(url).scheme.casefold()
        if scheme in {"data", "blob", "about"}:
            await route.continue_()
            return
        try:
            await self.assert_url(url)
        except (SiteStoreError, ValueError):
            await route.abort("blockedbyclient")
            return
        await route.continue_()
