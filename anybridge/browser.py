"""Headless page session that captures WebMCP tools and proxies calls into the page."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

SHIM = (Path(__file__).parent / "shim.js").read_text()

_CALL_JS = """async ({ name, args }) => {
  const result = await window.__anybridge__.callTool(name, args);
  try {
    return JSON.parse(JSON.stringify(result));
  } catch {
    return String(result);
  }
}"""


class PageBridge:
    """Opens `url` in headless Chromium with the modelContext shim injected.

    Pages register tools at unpredictable times (after fetches, hydration, ...),
    so `wait_for_tools` polls the registry instead of trusting a single load event.
    """

    def __init__(self, url: str, headless: bool = True):
        self.url = url
        self.headless = headless
        self._pw = None
        self._browser = None
        self._page = None

    async def start(self, settle: float = 1.0):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        context = await self._browser.new_context()
        await context.add_init_script(SHIM)
        self._page = await context.new_page()
        await self._page.goto(self.url, wait_until="load")
        await asyncio.sleep(settle)
        return self

    async def wait_for_tools(self, timeout: float = 10.0, poll: float = 0.25):
        """Wait until at least one tool is registered, or the timeout passes."""
        elapsed = 0.0
        while elapsed < timeout:
            tools = await self.list_tools()
            if tools:
                return tools
            await asyncio.sleep(poll)
            elapsed += poll
        return await self.list_tools()

    async def list_tools(self) -> list[dict]:
        return await self._page.evaluate("() => window.__anybridge__.listTools()")

    async def call_tool(self, name: str, args: dict | None = None):
        return await self._page.evaluate(_CALL_JS, {"name": name, "args": args or {}})

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()
