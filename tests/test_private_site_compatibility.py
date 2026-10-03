import asyncio

from anybridge.security import NetworkGuard, UnsafeTargetError


def test_private_target_enters_isolation_and_blocks_other_hosts(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        if host == "analytics.example.com":
            return {"93.184.216.34"}
        return set()

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True)

    asyncio.run(guard.assert_url("https://qa.internal/"))
    assert guard.private_isolation is True
    asyncio.run(guard.assert_url("https://qa.internal/api"))

    try:
        asyncio.run(guard.assert_url("https://analytics.example.com/collect"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("private-site isolation allowed an unrelated public host")


def test_private_target_can_use_explicit_private_host_allowlist(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        if host == "api.qa.internal":
            return {"10.20.30.41"}
        return set()

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True, allowed_hosts=("api.qa.internal",))

    asyncio.run(guard.assert_url("https://qa.internal/"))
    asyncio.run(guard.assert_url("https://api.qa.internal/data"))


def test_remote_mode_still_blocks_private_targets(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        return {"93.184.216.34"}

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=False)

    try:
        asyncio.run(guard.assert_url("https://qa.internal/"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("remote mode allowed a private target")


def test_wildcard_dependency_allowlist(monkeypatch):
    def resolve(host, port):
        if host in {"qa.planfuldev.com", "api.planfuldev.com"}:
            return {"10.20.30.40"}
        return {"93.184.216.34"}

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True, allowed_hosts=("*.planfuldev.com",))

    asyncio.run(guard.assert_url("https://qa.planfuldev.com/"))
    asyncio.run(guard.assert_url("https://api.planfuldev.com/data"))


def test_trusted_third_party_dependency_is_allowed(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        if host in {"cdn.example.com", "analytics.example.com", "telemetry.example.com", "auth.example.com"}:
            return {"93.184.216.34"}
        return set()

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(
        allow_private=True,
        isolate_private=True,
        allowed_hosts=(
            "cdn.example.com",
            "analytics.example.com",
            "telemetry.example.com",
            "auth.example.com",
        ),
    )

    asyncio.run(guard.assert_url("https://qa.internal/"))
    for host in ("cdn.example.com", "analytics.example.com", "telemetry.example.com", "auth.example.com"):
        asyncio.run(guard.assert_url(f"https://{host}/"))


def test_untrusted_third_party_dependency_remains_blocked(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        return {"93.184.216.34"}

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True, allowed_hosts=("cdn.example.com",))

    asyncio.run(guard.assert_url("https://qa.internal/"))
    try:
        asyncio.run(guard.assert_url("https://untrusted.example.com/"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("untrusted third-party dependency was allowed")


def test_blocked_dependency_is_reported_and_can_be_trusted(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        if host == "cdn.example.com":
            return {"93.184.216.34"}
        return {"93.184.216.35"}

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True)

    asyncio.run(guard.assert_url("https://qa.internal/"))
    try:
        asyncio.run(guard.assert_url("https://cdn.example.com/app.js"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("untrusted dependency was not blocked")

    assert guard.blocked_hosts[0]["host"] == "cdn.example.com"
    guard.allow_hosts(("cdn.example.com",))
    asyncio.run(guard.assert_url("https://cdn.example.com/app.js"))
    assert guard.blocked_hosts == ()


def test_public_host_seen_before_private_isolation_is_not_implicitly_trusted(monkeypatch):
    def resolve(host, port):
        if host == "public.example.com":
            return {"93.184.216.34"}
        if host == "qa.internal":
            return {"10.20.30.40"}
        return set()

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True)

    asyncio.run(guard.assert_url("https://public.example.com/"))
    asyncio.run(guard.assert_url("https://qa.internal/"))

    try:
        asyncio.run(guard.assert_url("https://public.example.com/exfil"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("a public host visited before private isolation became implicitly trusted")


def test_blocked_dependency_report_does_not_store_sensitive_path(monkeypatch):
    def resolve(host, port):
        if host == "qa.internal":
            return {"10.20.30.40"}
        if host == "cdn.example.com":
            return {"93.184.216.34"}
        return set()

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(allow_private=True, isolate_private=True)

    asyncio.run(guard.assert_url("https://qa.internal/"))
    try:
        asyncio.run(guard.assert_url("https://cdn.example.com/private/token/123"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("dependency was not blocked")

    report = guard.blocked_hosts[0]
    assert report["host"] == "cdn.example.com"
    assert report["scheme"] == "https"
    assert "token" not in str(report)
    assert "123" not in str(report)
    assert "url" not in report


def test_explicit_host_can_be_revoked_without_revoking_private_origin(monkeypatch):
    def resolve(host, port):
        if host in {"qa.internal"}:
            return {"10.20.30.40"}
        if host == "cdn.example.com":
            return {"93.184.216.34"}
        return set()

    monkeypatch.setattr(NetworkGuard, "_resolve", staticmethod(resolve))
    guard = NetworkGuard(
        allow_private=True,
        isolate_private=True,
        allowed_hosts=("cdn.example.com",),
    )

    asyncio.run(guard.assert_url("https://qa.internal/"))
    asyncio.run(guard.assert_url("https://cdn.example.com/app.js"))
    guard.revoke_host("cdn.example.com")

    try:
        asyncio.run(guard.assert_url("https://cdn.example.com/app.js"))
    except UnsafeTargetError:
        pass
    else:
        raise AssertionError("revoked host remained trusted")

    asyncio.run(guard.assert_url("https://qa.internal/api"))
    try:
        guard.revoke_host("qa.internal")
    except ValueError:
        pass
    else:
        raise AssertionError("private origin was revocable during its active session")
