"""Headless page session: captures WebMCP tools and offers universal page operations."""

import asyncio
import subprocess
import sys
from pathlib import Path

from playwright.async_api import Error
from playwright.async_api import TimeoutError as PWTimeoutError
from playwright.async_api import async_playwright

_HERE = Path(__file__).parent
SHIM = (_HERE / "shim.js").read_text()
PAGETOOLS = (_HERE / "pagetools.js").read_text()

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

    Exposes both the page's own WebMCP tools and universal operations
    (read/navigate/links/forms/click) that work on any site.
    """

    def __init__(self, url: str, headless: bool = True):
        self.url = url
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._installed = False

    async def start(self, settle: float = 1.0):
        self._pw = await async_playwright().start()
        self._browser = await self._launch_chromium()
        # Some sites refuse the default HeadlessChrome user agent.
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{self._browser.version.split('.')[0]}.0.0.0 Safari/537.36"
        )
        self._context = await self._browser.new_context(
            user_agent=ua, viewport={"width": 1280, "height": 900}, locale="en-US"
        )
        await self._context.add_init_script(SHIM)
        await self._context.add_init_script(PAGETOOLS)
        # Links with target=_blank open a new tab; follow it as the current page.
        self._context.on("page", self._on_new_page)
        self._page = await self._context.new_page()
        await self._goto(self.url)
        await asyncio.sleep(settle)
        return self

    async def _launch_chromium(self):
        """Launch Chromium, downloading it on first run if it isn't installed yet.

        Full Chromium ("chromium" channel) in new-headless mode is detected as a
        bot far less often than the bundled headless shell, so it is preferred.
        """
        for browser_args in ({"channel": "chromium"}, {}):
            try:
                return await self._pw.chromium.launch(headless=self.headless, **browser_args)
            except Error as exc:
                message = str(exc)
                if "playwright install" in message or "Executable doesn't exist" in message:
                    if not self._installed:
                        self._install_browser()
                        self._installed = True
                    try:
                        return await self._pw.chromium.launch(
                            headless=self.headless, **browser_args
                        )
                    except Error as retry_exc:
                        message = str(retry_exc)
                if "shared libraries" in message or "error while loading" in message:
                    raise RuntimeError(
                        "Chromium is installed but the system is missing libraries it needs.\n"
                        "On Debian/Ubuntu run:\n"
                        "    sudo playwright install-deps chromium\n"
                        "or, without sudo, install them for your user and set LD_LIBRARY_PATH."
                    ) from exc
                if browser_args:
                    continue  # fall back to the bundled headless shell
                raise
        raise RuntimeError("Could not launch Chromium.")

    def _install_browser(self):
        """Run `playwright install chromium` so first use needs no manual setup."""
        print("anybridge: downloading Chromium (first run only)...", file=sys.stderr)
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            stdout=sys.stderr,
        )

    def _on_new_page(self, page):
        self._page = page

    async def _goto(self, url: str):
        last_error = None
        for attempt in range(3):
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                last_error = None
                break
            except PWTimeoutError:
                last_error = None
                break  # keep whatever loaded; SPAs and slow trackers often never settle
            except Error as exc:
                # transient network errors (ERR_NETWORK_CHANGED, ERR_CONNECTION_RESET, ...)
                if "net::" not in str(exc):
                    raise
                last_error = exc
                await asyncio.sleep(1.0 + attempt)
        if last_error is not None:
            raise last_error
        try:
            await self._page.wait_for_load_state("load", timeout=8000)
        except PWTimeoutError:
            pass
        # Bot challenges sometimes resolve into a transient error page: reload once or twice.
        for _ in range(2):
            try:
                title = await self._page.title() or ""
            except Error:
                break
            if not any(
                marker in title
                for marker in ("502 Bad Gateway", "503 Service", "504 Gateway", "Just a moment")
            ):
                break
            await asyncio.sleep(3.0)
            try:
                await self._page.reload(wait_until="domcontentloaded", timeout=30000)
            except (PWTimeoutError, Error):
                break

    async def _settle(self, seconds: float = 1.0):
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PWTimeoutError:
            pass
        await asyncio.sleep(seconds)

    async def _eval_read(self, expression: str, arg=None):
        """Evaluate a read-only expression, retrying when a navigation races us.

        Only for side-effect-free reads: an action (tool call, form submit)
        must never be silently re-executed.
        """
        for _ in range(4):
            try:
                return await self._page.evaluate(expression, arg)
            except Error as exc:
                if "Execution context was destroyed" not in str(exc) and "Cannot find context" not in str(exc):
                    raise
                await self._settle(1.0)
        return await self._page.evaluate(expression, arg)

    # ---- WebMCP tools registered by the page ----

    async def wait_for_tools(self, timeout: float = 10.0, poll: float = 0.25):
        """Wait until at least one WebMCP tool is registered, or the timeout passes."""
        elapsed = 0.0
        while elapsed < timeout:
            tools = await self.list_tools()
            if tools:
                return tools
            await asyncio.sleep(poll)
            elapsed += poll
        return await self.list_tools()

    async def list_tools(self) -> list[dict]:
        return await self._eval_read("() => window.__anybridge__.listTools()")

    async def call_tool(self, name: str, args: dict | None = None):
        return await self._page.evaluate(_CALL_JS, {"name": name, "args": args or {}})

    # ---- Universal operations, work on any page ----

    async def read_page(self, selector: str | None = None, max_chars: int = 20000) -> str:
        # Pages mid-redirect (bot challenges, meta refreshes) briefly have no body:
        # retry instead of reporting an empty page.
        for _ in range(4):
            md = await self._eval_read(
                "({selector, maxChars}) => window.__anybridge_pt__.extract(selector, maxChars)",
                {"selector": selector, "maxChars": max_chars},
            )
            if md != "__anybridge_no_body__":
                return md
            await asyncio.sleep(1.5)
        return "The page has no readable content (empty document)."

    async def navigate(self, url: str) -> str:
        await self._goto(url)
        await self._settle()
        return await self.read_page()

    async def list_links(self, filter: str | None = None, limit: int = 100) -> list[dict]:
        return await self._eval_read(
            "({filter, limit}) => window.__anybridge_pt__.links(filter, limit)",
            {"filter": filter, "limit": limit},
        )

    async def list_forms(self) -> list[dict]:
        return await self._eval_read("() => window.__anybridge_pt__.forms()")

    async def submit_form(self, form: int, fields: dict) -> str:
        await self._page.evaluate(
            "({form, fields}) => window.__anybridge_pt__.fill(form, fields)",
            {"form": form, "fields": fields},
        )
        page = self._page
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=8000):
                await page.evaluate(
                    "form => window.__anybridge_pt__.submit(form)", form
                )
        except PWTimeoutError:
            pass  # no full navigation: SPA updated in place
        except Error as exc:
            if "net::" not in str(exc):
                raise
            await self._recover_navigation()
        await self._settle()
        return await self.read_page()

    async def _recover_navigation(self):
        """Retry a navigation that died on a transient network error."""
        await asyncio.sleep(2.0)
        try:
            await self._page.reload(wait_until="domcontentloaded", timeout=30000)
        except (PWTimeoutError, Error):
            pass

    async def click(self, target: str) -> str:
        page = self._page
        locator = None
        for candidate in (
            page.get_by_role("link", name=target),
            page.get_by_role("button", name=target),
            page.get_by_text(target, exact=False),
        ):
            try:
                if await candidate.count() > 0:
                    locator = candidate.first
                    break
            except Exception:
                continue
        if locator is None:
            try:
                css = page.locator(target)
                if await css.count() > 0:
                    locator = css.first
            except Exception:
                pass
        if locator is None:
            return f'Nothing found matching "{target}" (tried link/button text, page text, CSS selector).'
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=6000):
                await locator.click(timeout=8000)
        except PWTimeoutError:
            pass  # in-page update or new tab (handled by _on_new_page)
        except Error as exc:
            if "net::" not in str(exc):
                raise
            await self._recover_navigation()
        await self._settle()
        return await self.read_page()

    @staticmethod
    async def _editable(locator) -> bool:
        try:
            return await locator.evaluate(
                "el => ['INPUT','TEXTAREA'].includes(el.tagName) || el.isContentEditable"
            )
        except Exception:
            return False

    async def type_text(self, target: str, text: str, press_enter: bool = False) -> str:
        page = self._page
        attr = target.replace('"', '\\"')
        candidates = [
            page.get_by_placeholder(target),
            page.locator(f'input[name="{attr}"], textarea[name="{attr}"]'),
            page.locator(f'input[id="{attr}"], textarea[id="{attr}"]'),
            page.get_by_label(target),
            page.get_by_role("textbox", name=target),
            page.get_by_role("searchbox", name=target),
        ]
        try:
            candidates.append(page.locator(target))
        except Exception:
            pass
        locator = None
        for candidate in candidates:
            try:
                if await candidate.count() == 0:
                    continue
                first = candidate.first
                if await self._editable(first):
                    locator = first
                    break
            except Exception:
                continue
        if locator is None:
            return (
                f'No editable input matching "{target}" '
                "(tried placeholder, name/id attribute, label, textbox role, CSS selector)."
            )
        await locator.fill(text, timeout=8000)
        if press_enter:
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=6000):
                    await locator.press("Enter")
            except PWTimeoutError:
                pass
            except Error as exc:
                if "net::" not in str(exc):
                    raise
                await self._recover_navigation()
        await self._settle()
        return await self.read_page()

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()
