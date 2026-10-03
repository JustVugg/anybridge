"""Security boundaries for untrusted websites and private AnyBridge sessions."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from .sites import SiteStoreError, normalize_url


class UnsafeTargetError(SiteStoreError):
    """Raised when a browser target violates the active network policy."""


class NetworkGuard:
    """Validate browser destinations and subresources against network policy.

    Local AnyBridge sessions may reach development/private sites. When the first
    target resolves to a non-public address, the guard automatically enters
    private-site isolation: browser requests are restricted to the target host
    unless an explicit extra host allowlist is configured.

    This is an egress boundary for page data. It does not prevent the target
    website itself from receiving requests needed to render that website.
    """

    def __init__(
        self,
        *,
        allow_private: bool = True,
        isolate_private: bool = True,
        allowed_hosts: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.allow_private = allow_private
        self.isolate_private = isolate_private
        configured_hosts = os.getenv("ANYBRIDGE_PRIVATE_ALLOWED_HOSTS", "")
        configured = tuple(item.strip() for item in configured_hosts.split(",") if item.strip())
        self._approved_hosts: set[str] = set()
        self._explicit_hosts: set[str] = set()
        self._private_hosts: set[str] = set()
        self._private_isolation = False
        self._blocked_hosts: dict[str, dict] = {}
        self._configure_explicit_hosts((*allowed_hosts, *configured))

    @property
    def private_isolation(self) -> bool:
        return self._private_isolation

    @property
    def approved_hosts(self) -> tuple[str, ...]:
        return tuple(sorted(self._approved_hosts))

    @staticmethod
    def _normalize_allowed_host(host: str) -> str:
        value = str(host).strip().rstrip(".").casefold()
        if not value or "/" in value or ":" in value or any(ch.isspace() for ch in value):
            raise ValueError("Allowed network entries must be hostnames, not URLs, paths, ports, or whitespace.")
        if value == "*" or (value.startswith("*.") and len(value) <= 2):
            raise ValueError("Wildcard allowlist entries must include a domain.")
        return value

    def _configure_explicit_hosts(self, hosts) -> None:
        normalized = {self._normalize_allowed_host(host) for host in hosts if str(host).strip()}
        self._explicit_hosts.update(normalized)
        self._approved_hosts.update(normalized)

    def _host_is_approved(self, host: str) -> bool:
        if host in self._approved_hosts:
            return True
        return any(
            entry.startswith("*.") and host.endswith(entry[1:])
            for entry in self._approved_hosts
        )

    def check_url_sync(self, url: str) -> str:
        """Synchronous policy check for WebDriver BiDi request interception."""
        target = normalize_url(url)
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeTargetError("AnyBridge only allows http(s) website requests.")
        if parsed.username or parsed.password:
            raise UnsafeTargetError("Credentials must not be embedded in a website URL.")
        host = (parsed.hostname or "").rstrip(".").casefold()
        if not host:
            raise UnsafeTargetError("The target URL has no hostname.")
        if not self.allow_private:
            self._validate_public(host, parsed.port)
            return target
        addresses = self._resolve(host, parsed.port)
        if not addresses:
            raise UnsafeTargetError(f'Could not resolve target host "{host}".')
        is_private = any(not self._is_public(address) for address in addresses)
        if is_private and not self.isolate_private:
            return target
        if is_private:
            if not self._private_isolation:
                self._private_isolation = True
                self._private_hosts.add(host)
                self._approved_hosts.add(host)
            elif not self._host_is_approved(host):
                raise UnsafeTargetError(f'Private-site isolation blocked network access to host "{host}".')
            return target
        if self._private_isolation and not self._host_is_approved(host):
            raise UnsafeTargetError(f'Private-site isolation blocked network access to public host "{host}".')
        self._approved_hosts.add(host)
        return target

    async def assert_url(self, url: str) -> str:
        target = normalize_url(url)
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeTargetError("AnyBridge only allows http(s) website requests.")
        if parsed.username or parsed.password:
            raise UnsafeTargetError("Credentials must not be embedded in a website URL.")
        host = (parsed.hostname or "").rstrip(".").casefold()
        if not host:
            raise UnsafeTargetError("The target URL has no hostname.")

        if not self.allow_private:
            self._validate_public(host, parsed.port)
            return target

        addresses = self._resolve(host, parsed.port)
        if not addresses:
            raise UnsafeTargetError(f'Could not resolve target host "{host}".')

        is_private = any(not self._is_public(address) for address in addresses)
        if is_private and not self.isolate_private:
            return target

        if is_private:
            if not self._private_isolation:
                self._private_isolation = True
                self._private_hosts.add(host)
                self._approved_hosts.add(host)
            elif not self._host_is_approved(host):
                raise UnsafeTargetError(
                    f'Private-site isolation blocked network access to host "{host}". '
                    "Add the host explicitly to AnyBridge's private-site allowlist."
                )
            return target

        if self._private_isolation and not self._host_is_approved(host):
            raise UnsafeTargetError(
                f'Private-site isolation blocked network access to public host "{host}".'
            )
        # A public host observed before private-site isolation must not become
        # implicitly trusted for later private-site traffic.
        return target

    def _validate_public(self, host: str, port: int | None) -> None:
        addresses = self._resolve(host, port)
        if not addresses:
            raise UnsafeTargetError(f'Could not resolve target host "{host}".')
        for address in addresses:
            if not self._is_public(address):
                raise UnsafeTargetError(
                    f'Remote access to non-public address "{address}" is blocked.'
                )

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
        """Apply the same egress policy to every Chromium request."""
        url = route.request.url
        scheme = urlsplit(url).scheme.casefold()
        if scheme in {"data", "blob", "about", "chrome-extension"}:
            await route.continue_()
            return
        try:
            await self.assert_url(url)
        except (SiteStoreError, ValueError) as error:
            host = (urlsplit(url).hostname or "").rstrip(".").casefold()
            if host:
                self._blocked_hosts[host] = {
                    "host": host,
                    "scheme": scheme or "unknown",
                    "reason": "network_policy_block",
                }
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    @property
    def blocked_hosts(self) -> tuple[dict, ...]:
        return tuple(self._blocked_hosts[host] for host in sorted(self._blocked_hosts))

    def allow_hosts(self, hosts: tuple[str, ...] | list[str]) -> None:
        """Add explicitly trusted dependency hosts for this browser session."""
        self._configure_explicit_hosts(hosts)
        for host in hosts:
            normalized = self._normalize_allowed_host(host)
            self._blocked_hosts.pop(normalized, None)

    def revoke_host(self, host: str) -> dict:
        """Revoke an explicitly trusted host for this browser session."""
        normalized = self._normalize_allowed_host(host)
        if normalized in self._private_hosts:
            raise ValueError("The active private-site origin cannot be revoked during its session.")
        self._explicit_hosts.discard(normalized)
        self._approved_hosts.discard(normalized)
        return self.policy()

    def policy(self) -> dict:
        """Return a safe, non-secret description of the active network policy."""
        return {
            "allow_private_network": self.allow_private,
            "private_site_isolation": self._private_isolation,
            "approved_hosts": list(self.approved_hosts),
            "private_hosts": sorted(self._private_hosts),
            "blocked_hosts": list(self.blocked_hosts),
            "explicit_hosts": sorted(self._explicit_hosts),
            "approval_required": bool(self._blocked_hosts),
        }
