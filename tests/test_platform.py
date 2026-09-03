from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp.shared.memory import create_connected_server_and_client_session
from httpx import ASGITransport, AsyncClient, Request, Response

from anybridge.browser import PageBridge
from anybridge.engines import AdaptiveReader, EngineResult, _Budget
from anybridge.profiles import ProfileStore
from anybridge.remote import create_remote_app
from anybridge.security import NetworkGuard, UnsafeTargetError
from anybridge.server import BridgeRuntime, create_server
from anybridge.webmcp import publish_tools
from anybridge.workflows import WorkflowStore


FIXTURE = Path(__file__).parent / "fixtures" / "interactive.html"


_CONFIG_SANDBOX: tempfile.TemporaryDirectory | None = None
_CONFIG_PREVIOUS: str | None = None


def setUpModule() -> None:
    """Keep the suite out of the user's real AnyBridge configuration.

    Without this, a test that writes to the persistent cache leaves an entry
    behind and the same test fails the next time the suite runs inside the
    cache window.
    """
    global _CONFIG_SANDBOX, _CONFIG_PREVIOUS
    _CONFIG_SANDBOX = tempfile.TemporaryDirectory()
    _CONFIG_PREVIOUS = os.environ.get("ANYBRIDGE_CONFIG_DIR")
    os.environ["ANYBRIDGE_CONFIG_DIR"] = _CONFIG_SANDBOX.name


def tearDownModule() -> None:
    if _CONFIG_PREVIOUS is None:
        os.environ.pop("ANYBRIDGE_CONFIG_DIR", None)
    else:
        os.environ["ANYBRIDGE_CONFIG_DIR"] = _CONFIG_PREVIOUS
    if _CONFIG_SANDBOX is not None:
        _CONFIG_SANDBOX.cleanup()



class WebMCPSecurityTests(unittest.TestCase):
    def test_site_tools_are_namespaced_and_cannot_shadow_builtins(self) -> None:
        tools, mapping = publish_tools(
            [
                {
                    "name": "navigate",
                    "description": "untrusted",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ],
            "https://shop.example/path",
        )
        self.assertEqual(len(tools), 1)
        self.assertTrue(tools[0]["name"].startswith("webmcp_shop_example_navigate"))
        self.assertEqual(mapping[tools[0]["name"]], "navigate")
        self.assertIn("untrusted page content", tools[0]["description"])

    def test_private_network_guard_blocks_local_addresses(self) -> None:
        guard = NetworkGuard(allow_private=False)
        with patch.object(guard, "_resolve", return_value={"127.0.0.1"}):
            with self.assertRaises(UnsafeTargetError):
                asyncio.run(guard.assert_url("http://internal.example"))


class WalletTests(unittest.TestCase):
    def test_profiles_are_encrypted_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ProfileStore(root / "profiles.json", root / "wallet.key")
            secret = "session-cookie-secret"
            state = {"cookies": [{"name": "session", "value": secret}], "origins": []}
            store.save("Shop", "https://shop.example", state)
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            self.assertEqual(store.get("shop").state, state)
            self.assertEqual(store.list(), [{"name": "Shop", "origin": "https://shop.example"}])
            store.remove("SHOP")
            self.assertEqual(store.list(), [])

    def test_workflow_never_contains_entered_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkflowStore(Path(temporary) / "workflows.json")
            workflow = store.save(
                "Book",
                "https://hotel.example",
                "https://hotel.example/start",
                [{"action": "fill", "target": {"name": "Email"}, "variable": "value_1"}],
            )
            self.assertEqual(workflow.variables, ["value_1"])
            self.assertNotIn("alice@example.com", store.path.read_text(encoding="utf-8"))
            self.assertEqual(store.get("book").steps[0]["variable"], "value_1")


class AdaptiveEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_cached_without_a_second_fetch(self) -> None:
        # An explicit cache directory, not just the sandboxed config dir: a test
        # that leaves an entry behind would otherwise fail its own next run,
        # because the second call would be served from the previous run's cache.
        with tempfile.TemporaryDirectory() as folder:
            reader = AdaptiveReader(cache_seconds=60, cache_path=Path(folder) / "cache")
            result = EngineResult("https://example.com", "content", "http")
            with patch.object(
                reader, "_http_read", new=AsyncMock(return_value=(result, True))
            ) as fetch:
                first = await reader.read("https://example.com")
                second = await reader.read("https://example.com")
            self.assertFalse(first.cached)
            self.assertTrue(second.cached)
            self.assertEqual(fetch.await_count, 1)

    async def test_incomplete_http_falls_back_to_chromium(self) -> None:
        reader = AdaptiveReader(cache_seconds=0)
        incomplete = EngineResult("https://app.example", "empty", "http")

        class Bridge:
            started = False
            current_url = "https://app.example"

            async def start(self):
                self.started = True

            async def navigate(self, url):
                return "rendered"

        with (
            patch.object(reader, "_http_read", new=AsyncMock(return_value=(incomplete, False))),
            patch.object(reader, "_lightpanda_read", new=AsyncMock(side_effect=RuntimeError("missing"))),
        ):
            result = await reader.read("https://app.example", bridge=Bridge())
        self.assertEqual(result.engine, "chromium")
        self.assertEqual(result.content, "rendered")

    async def test_stale_public_cache_survives_restart_and_live_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "content-cache"
            live = EngineResult("https://example.com", "last known content", "http")
            first_reader = AdaptiveReader(
                cache_seconds=0.001,
                cache_path=cache_path,
                archive_fallback=False,
            )
            with patch.object(
                first_reader,
                "_http_read",
                new=AsyncMock(return_value=(live, True)),
            ):
                await first_reader.read("https://example.com")
            await asyncio.sleep(0.01)

            restarted = AdaptiveReader(
                cache_seconds=0.001,
                cache_path=cache_path,
                archive_fallback=False,
            )
            with (
                patch.object(
                    restarted,
                    "_http_read",
                    new=AsyncMock(side_effect=RuntimeError("offline")),
                ),
                patch.object(
                    restarted,
                    "_lightpanda_read",
                    new=AsyncMock(side_effect=RuntimeError("offline")),
                ),
            ):
                result = await restarted.read("https://example.com")
            self.assertTrue(result.cached)
            self.assertTrue(result.stale)
            self.assertEqual(result.content, "last known content")

    async def test_wayback_is_a_labeled_historical_last_resort(self) -> None:
        availability = Response(
            200,
            json={
                "archived_snapshots": {
                    "closest": {
                        "available": True,
                        "timestamp": "20240102030405",
                        "status": "200",
                    }
                }
            },
            request=Request("GET", "https://archive.org/wayback/available"),
        )
        snapshot = Response(
            200,
            text="<html><title>Archived</title><body><main>" + "old " * 100 + "</main></body></html>",
            request=Request("GET", "https://web.archive.org/web/snapshot"),
        )

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return availability if "available" in url else snapshot

        client = FakeClient()
        reader = AdaptiveReader(cache_seconds=0)
        with patch("anybridge.engines.httpx.AsyncClient", return_value=client):
            result = await reader._wayback_read(
                "https://example.com/private?token=secret", max_chars=5000
            )
        self.assertEqual(result.engine, "wayback")
        self.assertTrue(result.stale)
        self.assertIn("Historical fallback", result.content)
        self.assertNotIn("token=secret", client.calls[0][1]["params"]["url"])

    async def test_total_route_failure_returns_availability_result_not_exception(self) -> None:
        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        with (
            patch.object(
                reader,
                "_http_read",
                new=AsyncMock(side_effect=RuntimeError("offline")),
            ),
            patch.object(
                reader,
                "_lightpanda_read",
                new=AsyncMock(side_effect=RuntimeError("offline")),
            ),
        ):
            result = await reader.read("https://example.com")
        self.assertEqual(result.engine, "unavailable")
        self.assertIn("stayed available", result.content)

    async def test_browser_challenge_switches_navigate_to_public_continuity(self) -> None:
        class ChallengedBridge:
            started = False
            current_url = "https://blocked.example"

            async def start(self):
                self.started = True

            async def navigate(self, url):
                return "Verify you are human"

            async def access_status(self):
                return {"blocked": True, "kind": "captcha"}

            async def close(self):
                self.started = False

        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        public = EngineResult(
            "https://blocked.example", "public fallback content", "http"
        )
        with patch.object(
            reader,
            "_http_read",
            new=AsyncMock(return_value=(public, True)),
        ):
            result = await reader.navigate(
                "https://blocked.example", bridge=ChallengedBridge()
            )
        self.assertIn("continuity mode", result)
        self.assertIn("public fallback content", result)
        self.assertIn("Interactive actions are not reported as completed", result)

    async def test_pdf_navigation_uses_document_engine_without_starting_browser(self) -> None:
        class BrowserThatMustNotStart:
            started = False
            start_count = 0

            async def start(self):
                self.start_count += 1
                raise AssertionError("PDF should not start Chromium")

        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        document = EngineResult(
            "https://example.com/bando.pdf", "extracted bando text", "http-pdf"
        )
        browser = BrowserThatMustNotStart()
        with patch.object(
            reader,
            "_http_read",
            new=AsyncMock(return_value=(document, True)),
        ):
            result = await reader.navigate(
                "https://example.com/bando.pdf", bridge=browser, max_chars=50000
            )
        self.assertIn("engine=http-pdf", result)
        self.assertIn("extracted bando text", result)
        self.assertEqual(browser.start_count, 0)

    async def test_pdf_falls_back_to_the_open_browser_context_when_http_fails(self) -> None:
        async def inline_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class OpenBrowser:
            started = True
            fetched: list[str] = []

            async def fetch_bytes(self, url, timeout_ms=45000):
                self.fetched.append(url)
                return b"%PDF-1.4 fake", "application/pdf"

        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        browser = OpenBrowser()
        with patch.object(
            reader, "_http_read", new=AsyncMock(side_effect=TimeoutError("slow server"))
        ), patch.object(
            reader, "_pdf_text", return_value="--- page 1 of 1 ---\nage limit 38"
        ), patch("anybridge.engines.asyncio.to_thread", new=inline_to_thread):
            result = await reader.read(
                "https://example.com/bando.pdf", bridge=browser, max_chars=5000
            )
        self.assertEqual(result.engine, "chromium-pdf")
        self.assertIn("age limit 38", result.content)
        self.assertEqual(browser.fetched, ["https://example.com/bando.pdf"])

    async def test_pdf_route_never_starts_a_closed_browser(self) -> None:
        class ClosedBrowser:
            started = False

            async def fetch_bytes(self, url, timeout_ms=45000):
                raise AssertionError("must not fetch through a browser that is not open")

            async def start(self):
                raise AssertionError("must not start Chromium for a document")

        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        with patch.object(
            reader, "_http_read", new=AsyncMock(side_effect=TimeoutError("slow server"))
        ):
            result = await reader.read(
                "https://example.com/bando.pdf", bridge=ClosedBrowser(), max_chars=5000
            )
        self.assertEqual(result.engine, "unavailable")
        self.assertIn("http: slow server", result.content)

    async def test_browser_sourced_content_is_never_persisted_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "content-cache"
            reader = AdaptiveReader(cache_seconds=600, cache_path=cache)
            # A PDF fetched through the browser carries the session's cookies:
            # it may be an authenticated document and must stay in memory only.
            await reader._store(
                "https://example.com/bando.pdf",
                EngineResult("https://example.com/bando.pdf", "private", "chromium-pdf"),
            )
            await reader._store(
                "https://example.com/page",
                EngineResult("https://example.com/page", "rendered", "chromium"),
            )
            await reader._store(
                "https://example.com/public.pdf",
                EngineResult("https://example.com/public.pdf", "public", "http-pdf"),
            )
            on_disk = sorted(
                json.loads(entry.read_text())["engine"]
                for entry in cache.glob("*.json")
            )
            self.assertEqual(on_disk, ["http-pdf"])

    def test_document_race_fits_under_the_mcp_budget(self) -> None:
        reader = AdaptiveReader()
        # server.py stops an MCP call at 75s; the cascade must finish before that.
        self.assertLessEqual(reader.total_timeout, 72)
        budget = _Budget(reader.total_timeout)
        race_slice = budget.slice(max(45, reader.browser_timeout + 15))
        self.assertEqual(race_slice, 60)
        self.assertGreaterEqual(reader.total_timeout - race_slice, 8)
        # A budget too short for a meaningful attempt reports instead of hanging.
        self.assertIsNone(_Budget(3).slice(45))
        self.assertAlmostEqual(_Budget(10).slice(45), 9.0, places=1)

    async def test_pdf_http_and_browser_routes_start_together(self) -> None:
        async def inline_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        http_started = asyncio.Event()
        http_cancelled = asyncio.Event()
        browser_started = asyncio.Event()
        never = asyncio.Event()

        async def stalled_http(*args, **kwargs):
            http_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                http_cancelled.set()
                raise

        class FastBrowser:
            started = True

            async def fetch_bytes(self, url, timeout_ms=45000):
                await http_started.wait()
                browser_started.set()
                return b"%PDF-1.4 fake", "application/pdf"

        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        with patch.object(reader, "_http_read", new=stalled_http), patch.object(
            reader, "_pdf_text", return_value="--- page 1 of 1 ---\nage limit 38"
        ), patch("anybridge.engines.asyncio.to_thread", new=inline_to_thread):
            result = await asyncio.wait_for(
                reader.read(
                    "https://example.com/bando.pdf",
                    bridge=FastBrowser(),
                    max_chars=5000,
                    total_timeout=6,
                ),
                timeout=2,
            )

        self.assertEqual(result.engine, "chromium-pdf")
        self.assertTrue(http_started.is_set())
        self.assertTrue(browser_started.is_set())
        self.assertTrue(http_cancelled.is_set())

    async def test_documents_skip_lightpanda_and_reach_the_browser_pdf_route(self) -> None:
        async def inline_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        class OpenBrowser:
            started = True

            async def fetch_bytes(self, url, timeout_ms=45000):
                return b"%PDF-1.4 fake", "application/pdf"

        reader = AdaptiveReader(cache_seconds=0, archive_fallback=False)
        lightpanda = AsyncMock(side_effect=AssertionError("a PDF has no JavaScript to render"))
        with patch.object(
            reader, "_http_read", new=AsyncMock(side_effect=TimeoutError("slow server"))
        ), patch.object(reader, "_lightpanda_read", new=lightpanda), patch.object(
            reader, "_pdf_text", return_value="--- page 1 of 1 ---\nage limit 38"
        ), patch("anybridge.engines.asyncio.to_thread", new=inline_to_thread):
            result = await reader.read(
                "https://example.com/bando.pdf", bridge=OpenBrowser(), max_chars=5000
            )
        self.assertEqual(result.engine, "chromium-pdf")
        lightpanda.assert_not_awaited()

    def test_resume_hint_survives_the_character_budget(self) -> None:
        pages = ((n, "x" * 400) for n in range(1, 21))
        text = AdaptiveReader.assemble_pdf_pages(pages, total=20, last=20, max_chars=900)
        self.assertIn('ask again with pages="', text)
        self.assertIn("-20", text)
        # The hint is appended after the cut, so the budget can never eat it.
        self.assertTrue(text.rstrip().endswith("]"))

    def test_pdf_page_ranges_are_one_based_and_bounded(self) -> None:
        pages = [SimpleNamespace(extract_text=lambda n=n: f"text of page {n}") for n in range(1, 8)]
        with patch("anybridge.engines.PdfReader", return_value=SimpleNamespace(pages=pages)):
            ranged = AdaptiveReader._pdf_text(b"pdf", max_chars=5000, pages=(3, 4))
            self.assertIn("--- page 3 of 7 ---\ntext of page 3", ranged)
            self.assertIn("page 4 of 7", ranged)
            self.assertNotIn("page 2 of", ranged)
            self.assertNotIn("page 5 of", ranged)
            with self.assertRaises(ValueError):
                AdaptiveReader._pdf_text(b"pdf", pages=(9, 9))
        self.assertEqual(AdaptiveReader.parse_pages("12-15"), (12, 15))
        self.assertEqual(AdaptiveReader.parse_pages(" 7 "), (7, 7))
        self.assertIsNone(AdaptiveReader.parse_pages(None))
        with self.assertRaises(ValueError):
            AdaptiveReader.parse_pages("15-12")

    def test_pdf_extraction_stops_after_requested_character_budget(self) -> None:
        class Page:
            def __init__(self):
                self.calls = 0

            def extract_text(self):
                self.calls += 1
                return "x" * 100

        pages = [Page() for _ in range(10)]
        with patch(
            "anybridge.engines.PdfReader",
            return_value=SimpleNamespace(pages=pages),
        ):
            content = AdaptiveReader._pdf_text(b"pdf", max_chars=250)
        self.assertGreaterEqual(len(content), 250)
        self.assertEqual(sum(page.calls for page in pages), 3)


class FetchBytesRedirectTests(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self, status: int, headers=None, body=b"") -> None:
            self.status = status
            self.headers = headers or {}
            self._body = body

        @property
        def ok(self) -> bool:
            return 200 <= self.status < 300

        async def body(self) -> bytes:
            return self._body

    class RequestAPI:
        def __init__(self, responses) -> None:
            self.responses = list(responses)
            self.seen: list[str] = []

        async def get(self, url, timeout=None, max_redirects=None):
            self.seen.append(url)
            assert max_redirects == 0, "redirects must be followed hop by hop"
            return self.responses.pop(0)

    def _bridge(self, responses):
        bridge = PageBridge(allow_private_network=False)
        bridge._page = object()          # `started` only checks for a page
        bridge._context = SimpleNamespace(request=self.RequestAPI(responses))
        return bridge

    async def test_a_redirect_into_a_private_network_is_blocked(self) -> None:
        bridge = self._bridge([
            self.Response(302, {"location": "http://127.0.0.1:9000/secret"}),
        ])
        with self.assertRaises(UnsafeTargetError):
            await bridge.fetch_bytes("https://93.184.216.34/bando.pdf")
        self.assertEqual(bridge._context.request.seen, ["https://93.184.216.34/bando.pdf"])

    async def test_a_public_redirect_chain_is_followed_and_returned(self) -> None:
        bridge = self._bridge([
            self.Response(302, {"location": "https://93.184.216.35/real.pdf"}),
            self.Response(200, {"content-type": "application/pdf; charset=binary"}, b"%PDF-1.7"),
        ])
        data, media_type = await bridge.fetch_bytes("https://93.184.216.34/bando.pdf")
        self.assertEqual(data, b"%PDF-1.7")
        self.assertEqual(media_type, "application/pdf")
        self.assertEqual(
            bridge._context.request.seen,
            ["https://93.184.216.34/bando.pdf", "https://93.184.216.35/real.pdf"],
        )


class MCPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_handshake_and_builtin_call(self) -> None:
        runtime = BridgeRuntime()
        try:
            async with create_connected_server_and_client_session(create_server(runtime)) as client:
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                self.assertIn("snapshot", names)
                self.assertIn("smart_read", names)
                result = await client.call_tool("engine_status", {})
                self.assertFalse(result.isError)
                self.assertIn("chromium", result.content[0].text)
        finally:
            await runtime.close()


class RemoteSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_health_and_bearer_boundary(self) -> None:
        app = create_remote_app(api_token="test-token")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get("/health")
            denied = await client.post("/mcp", json={})
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["service"], "anybridge")
        self.assertEqual(denied.status_code, 401)


@unittest.skipIf(
    os.environ.get("ANYBRIDGE_SKIP_BROWSER_TESTS") == "1",
    "browser integration disabled",
)
class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_refs_actions_profiles_and_webmcp(self) -> None:
        bridge = PageBridge(FIXTURE.resolve().as_uri())
        try:
            await bridge.start(settle=0.1)
            raw_tools = await bridge.wait_for_tools(timeout=1)
            published, mapping = publish_tools(raw_tools, (await bridge.current_site())["url"])
            names = {tool["name"] for tool in published}
            self.assertTrue(any(name.endswith("_navigate") for name in names))
            quote = next(name for name in names if name.endswith("_quote_price"))
            self.assertEqual(mapping[quote], "quote_price")
            webmcp_result = await bridge.call_tool("quote_price", {"days": 3})
            self.assertEqual(webmcp_result["content"][0]["text"], "30")

            snapshot = await bridge.snapshot(interactive_only=True)
            self.assertIn("Guest name", snapshot)
            self.assertIn("Save booking", snapshot)
            input_ref = next(line.split()[0][1:] for line in snapshot.splitlines() if "Guest name" in line)
            select_ref = next(line.split()[0][1:] for line in snapshot.splitlines() if "Room" in line)
            button_ref = next(line.split()[0][1:] for line in snapshot.splitlines() if "Save booking" in line)
            bridge.begin_recording()
            await bridge.fill_ref(input_ref, "Vincenzo")

            # Every snapshot refreshes refs; locate the remaining controls again.
            snapshot = await bridge.snapshot(interactive_only=True)
            select_ref = next(line.split()[0][1:] for line in snapshot.splitlines() if "Room" in line)
            await bridge.select_ref(select_ref, "suite")
            snapshot = await bridge.snapshot(interactive_only=True)
            button_ref = next(line.split()[0][1:] for line in snapshot.splitlines() if "Save booking" in line)
            await bridge.click_ref(button_ref)
            data = await bridge.extract_structured({"result": "#result"})
            self.assertEqual(data["result"], "Saved Vincenzo suite")
            steps = bridge.end_recording()
            self.assertEqual([step["action"] for step in steps], ["fill", "select", "click"])
            self.assertNotIn("Vincenzo", json.dumps(steps))
            self.assertEqual(
                [step.get("variable") for step in steps[:2]], ["value_1", "value_2"]
            )

            await bridge.navigate(FIXTURE.resolve().as_uri())
            for step in steps:
                await bridge.run_recorded_step(
                    step, {"value_1": "Replay", "value_2": "suite"}
                )
            replayed = await bridge.extract_structured({"result": "#result"})
            self.assertEqual(replayed["result"], "Saved Replay suite")

            state = await bridge.storage_snapshot()
            self.assertIn("cookies", state)
        finally:
            await bridge.close()

    async def test_snapshot_collapses_a_mega_menu_but_keeps_its_refs(self) -> None:
        bridge = PageBridge((FIXTURE.parent / "menu.html").resolve().as_uri())
        try:
            await bridge.start(settle=0.1)
            snapshot = await bridge.snapshot(interactive_only=True, compact=True)
            printed_menu = [line for line in snapshot.splitlines() if "Section " in line]
            self.assertEqual(len(printed_menu), 8)
            self.assertIn("28 more menu links", snapshot)
            self.assertIn("Bando 1 (PDF)", snapshot)
            self.assertIn("Bando 2 (PDF)", snapshot)
            # A hidden menu link keeps a working ref.
            described = await bridge._describe_ref("e20")
            self.assertEqual(described.get("name"), "Section 20")
            # A structural snapshot collapses the menu too: the transcript that
            # motivated this used interactive_only=false.
            structural = await bridge.snapshot(interactive_only=False, compact=True)
            self.assertEqual(len([l for l in structural.splitlines() if "Section " in l]), 8)
            self.assertIn("more menu links", structural)
            # compact=false is the explicit way to see everything.
            full = await bridge.snapshot(interactive_only=False, compact=False)
            self.assertIn("Section 36", full)
        finally:
            await bridge.close()


if __name__ == "__main__":
    unittest.main()
