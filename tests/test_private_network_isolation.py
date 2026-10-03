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
