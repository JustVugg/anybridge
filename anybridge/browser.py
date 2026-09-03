"""Headless page session: captures WebMCP tools and offers universal page operations."""

import asyncio
import base64
import io
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import Error
from playwright.async_api import TimeoutError as PWTimeoutError
from playwright.async_api import async_playwright

from .sites import normalize_url
from .security import NetworkGuard

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

_SHOPIFY_WEBMCP_FALLBACK = (
    "https://cdn.shopify.com/storefront/webmcp/webmcp-0.1.1.js"
)


class BrowserDependencyError(RuntimeError):
    """Raised when Chromium exists but required OS libraries are unavailable."""


class BrowserInstallError(RuntimeError):
    """Raised when AnyBridge cannot complete its one-time browser setup."""


def _playwright_version() -> str:
    try:
        return version("playwright")
    except PackageNotFoundError:
        return "unknown"


def browser_ready_marker() -> Path:
    """Return a marker tied to the current OS, architecture, and Playwright release."""
    from .sites import default_config_dir

    system = platform.system().lower() or sys.platform
    machine = platform.machine().lower() or "unknown"
    return default_config_dir() / f"browser-ready-{system}-{machine}-{_playwright_version()}"


async def _probe_browser() -> None:
    bridge = PageBridge()
    try:
        await bridge.start(settle=0)
    finally:
        await bridge.close()


def prepare_browser(marker: Path | None = None) -> bool:
    """Prepare Chromium before an agent starts; return whether setup ran.

    Browser binaries are downloaded automatically on every platform. Linux and
    WSL may additionally need OS packages; Playwright installs those in the
    agent's new interactive terminal, before the agent itself starts.
    """
    ready = marker or browser_ready_marker()
    if ready.exists():
        return False

    try:
        asyncio.run(_probe_browser())
    except BrowserDependencyError:
        if not sys.platform.startswith("linux"):
            raise
        print(
            "AnyBridge first-time browser setup\n"
            "Installing Chromium system libraries before the agent starts.\n"
            "Linux/WSL may ask for your system password once.\n",
            file=sys.stderr,
        )
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "playwright",
                    "install",
                    "--with-deps",
                    "chromium",
                ],
                check=False,
                timeout=180,
            )
        except subprocess.TimeoutExpired as error:
            raise BrowserInstallError(
                "Browser setup exceeded its three-minute deadline."
            ) from error
        if result.returncode != 0:
            raise BrowserInstallError(
                "AnyBridge could not install Chromium's required system libraries."
            )
        try:
            asyncio.run(_probe_browser())
        except Exception as error:
            raise BrowserInstallError(
                "Chromium still could not start after AnyBridge completed browser setup."
            ) from error
    except Exception as error:
        raise BrowserInstallError(
            f"AnyBridge could not verify Chromium before starting the agent: {error}"
        ) from error

    ready.parent.mkdir(parents=True, exist_ok=True)
    ready.touch(mode=0o600, exist_ok=True)
    return True


class PageBridge:
    """Opens `url` in headless Chromium with the modelContext shim injected.

    Exposes both the page's own WebMCP tools and universal operations
    (read/navigate/links/forms/click) that work on any site.
    """

    def __init__(
        self,
        url: str | None = None,
        headless: bool = True,
        *,
        allow_private_network: bool = True,
        storage_state: dict | None = None,
    ):
        self.url = normalize_url(url) if url else "about:blank"
        self.headless = headless
        self.allow_private_network = allow_private_network
        self.storage_state = storage_state
        self._guard = NetworkGuard(allow_private=allow_private_network)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._installed = False
        self._record_actions = False
        self._recorded_actions: list[dict] = []
        self._recording_start_url: str | None = None
        self._pdf_cache: tuple[str, list[str]] | None = None   # (url, text per page)

    async def start(self, settle: float = 1.0):
        if self._page is not None:
            return self
        self._pw = await async_playwright().start()
        self._browser = await self._launch_chromium()
        # Some sites refuse the default HeadlessChrome user agent.
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{self._browser.version.split('.')[0]}.0.0.0 Safari/537.36"
        )
        context_options = {
            "user_agent": ua,
            "viewport": {"width": 1280, "height": 900},
            "locale": "en-US",
        }
        if self.storage_state:
            context_options["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**context_options)
        if not self.allow_private_network:
            await self._context.route("**/*", self._guard.route)
        await self._context.add_init_script(SHIM)
        await self._context.add_init_script(PAGETOOLS)
        # Links with target=_blank open a new tab; follow it as the current page.
        self._context.on("page", self._on_new_page)
        self._page = await self._context.new_page()
        if self.url != "about:blank":
            await self._goto(self.url)
            await asyncio.sleep(settle)
        return self

    @property
    def started(self) -> bool:
        """Whether the browser session has been started."""
        return self._page is not None

    @property
    def current_url(self) -> str:
        """Return the browser's current URL without exposing Playwright internals."""
        if self._page is not None:
            return self._page.url
        return self.url

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
                lowered = message.lower()
                if any(
                    marker in lowered
                    for marker in (
                        "shared libraries",
                        "error while loading shared object",
                        "host system is missing dependencies",
                        "missing libraries",
                    )
                ):
                    raise BrowserDependencyError(
                        "AnyBridge browser setup is incomplete. Close this agent and relaunch it "
                        "from AnyBridge so the one-time setup can finish."
                    ) from exc
                if browser_args:
                    continue  # fall back to the bundled headless shell
                raise
        raise RuntimeError("Could not launch Chromium.")

    def _install_browser(self):
        """Run `playwright install chromium` so first use needs no manual setup."""
        print("anybridge: downloading Chromium (first run only)...", file=sys.stderr)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=False,
                stdout=sys.stderr,
                timeout=180,
            )
        except subprocess.TimeoutExpired as error:
            raise BrowserInstallError(
                "Chromium download exceeded its three-minute deadline."
            ) from error
        if result.returncode != 0:
            raise BrowserInstallError("AnyBridge could not download Chromium.")

    def _on_new_page(self, page):
        self._page = page

    async def _goto(self, url: str):
        url = await self._guard.assert_url(url)
        last_error = None
        for attempt in range(3):
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if self._page.url.startswith("chrome-error://"):
                    # Chromium rendered its own error page (ERR_ADDRESS_UNREACHABLE and
                    # friends on a flaky link) instead of raising: retry like a net:: error.
                    raise Error("net::ERR_FAILED (navigation landed on chrome-error://)")
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
        tools = await self.list_tools()
        if tools:
            return tools
        try:
            await self._page.wait_for_function(
                "() => window.__anybridge__?.listTools().length > 0",
                timeout=max(1, float(timeout)) * 1000,
                polling=max(50, int(poll * 1000)),
            )
        except PWTimeoutError:
            pass
        return await self.list_tools()

    async def discover_tools(
        self, timeout: float = 30.0, *, reload_on_failure: bool = False
    ) -> list[dict]:
        """Wait for late WebMCP registration only when the page shows a capability hint."""
        async def discover_once() -> list[dict]:
            tools = await self.list_tools()
            if tools:
                return tools
            hinted = await self._eval_read(
            r"""() => Boolean(
              window.Shopify ||
              [...document.scripts].some((script) => /webmcp|modelcontext|standard-actions/i.test(script.src || script.textContent || '')) ||
              /modelContext\s*\./i.test(document.documentElement?.innerHTML || '')
            )"""
            )
            if not hinted:
                return []

            # Give native page loaders first chance. Shopify deliberately loads
            # WebMCP asynchronously and occasionally its inline loader is skipped
            # even though the official script URL remains in the page source.
            native_wait = min(max(float(timeout), 0.0), 8.0)
            tools = await self.wait_for_tools(timeout=native_wait)
            if tools:
                return tools

            bootstrapped = await self._bootstrap_shopify_webmcp()
            remaining = max(2.0, float(timeout) - native_wait) if bootstrapped else max(
                0.0, float(timeout) - native_wait
            )
            return await self.wait_for_tools(timeout=remaining) if remaining else []

        tools = await discover_once()
        if tools or not reload_on_failure:
            return tools
        # Shopify and other capability loaders are often feature-gated scripts.
        # A transient CDN miss can leave the standard-actions hint present but
        # never register tools. One clean reload repairs that state; never loop.
        try:
            await self._page.reload(wait_until="domcontentloaded", timeout=30000)
            await self._settle(1.0)
            return await discover_once()
        except (PWTimeoutError, Error):
            return await self.list_tools()

    async def _bootstrap_shopify_webmcp(self) -> bool:
        """Finish Shopify's own WebMCP bootstrap when its async loader was missed.

        AnyBridge only does this on a Shopify page that already advertises its
        official WebMCP loader. The URL is read from that page's source; the
        versioned fallback is used only for Liquid storefronts that expose
        Shopify's standard actions but stripped the inline loader URL.
        """
        result = await self._page.evaluate(
            r"""async ({fallback}) => {
              if (!window.Shopify || window.__anybridgeShopifyWebMcpAttempted) return false;
              if (window.__anybridge__?.listTools().length) return false;

              const source = [
                ...[...document.scripts].map((script) => `${script.src || ''}\n${script.textContent || ''}`),
                document.documentElement?.innerHTML || '',
              ].join('\n').replaceAll('\\/', '/');
              const match = source.match(/https:\/\/cdn\.shopify\.com\/storefront\/webmcp\/webmcp-[0-9.]+\.js/i);
              const standardActions = [...document.scripts].some((script) =>
                /cdn\.shopify\.com\/storefront\/standard-actions\.js/i.test(script.src || '')
              );
              const src = match?.[0] || (standardActions ? fallback : null);
              if (!src) return false;

              window.__anybridgeShopifyWebMcpAttempted = true;
              if ([...document.scripts].some((script) => script.src === src)) {
                // An existing async script may still be downloading. Replacing a
                // failed tag with a fresh tag is safe: Shopify's runtime guards
                // duplicate registration with its own global symbol.
                await new Promise((resolve) => setTimeout(resolve, 750));
                if (window.__anybridge__?.listTools().length) return true;
              }
              await new Promise((resolve) => {
                const script = document.createElement('script');
                script.src = src;
                script.dataset.sourceAttribution = 'anybridge.shopify_webmcp_recovery';
                script.onload = resolve;
                script.onerror = resolve;
                (document.head || document.documentElement).appendChild(script);
              });
              return true;
            }""",
            {"fallback": _SHOPIFY_WEBMCP_FALLBACK},
        )
        return bool(result)

    async def list_tools(self) -> list[dict]:
        return await self._eval_read("() => window.__anybridge__.listTools()")

    async def call_tool(self, name: str, args: dict | None = None):
        return await self._page.evaluate(_CALL_JS, {"name": name, "args": args or {}})

    # ---- Universal operations, work on any page ----

    async def is_pdf_document(self) -> bool:
        """Whether the current page is Chromium's PDF viewer rather than a document tree."""
        if not self.started or self._page.url == "about:blank":
            return False
        try:
            content_type = await self._eval_read("() => document.contentType || ''")
        except Error:
            content_type = ""
        return content_type == "application/pdf" or self._page.url.split("?", 1)[0].split("#", 1)[0].casefold().endswith(".pdf")

    async def fetch_bytes(self, url: str, timeout_ms: int = 45000) -> tuple[bytes, str]:
        """Download a URL through the browser context: same cookies, same network path."""
        if not self.started:
            raise RuntimeError("Browser session is not started.")
        # The context route guard only sees page requests, not this API client,
        # and a public site may redirect into localhost or a private network:
        # follow redirects by hand and check every hop.
        for _ in range(6):
            url = await self._guard.assert_url(url)
            response = await self._context.request.get(url, timeout=timeout_ms, max_redirects=0)
            if response.status not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location:
                break
            url = urljoin(url, location)
        else:
            raise RuntimeError(f"too many redirects fetching {url}")
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status} fetching {url}")
        media_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        return await response.body(), media_type

    async def pdf_document_text(self, max_chars: int = 20000, pages: str | None = None) -> str:
        """Extract the text of the PDF the browser is currently displaying."""
        from .engines import AdaptiveReader  # local: engines does not import browser

        url = self._page.url.split("#", 1)[0]
        if not (self._pdf_cache and self._pdf_cache[0] == url):
            # Extract every page once: snapshot, read_page and wait_for on the same
            # document must not each re-download and re-parse a 60-page bando.
            data, _ = await self.fetch_bytes(url)
            self._pdf_cache = (url, await asyncio.to_thread(self._pdf_pages, data))
        page_texts = self._pdf_cache[1]
        total = len(page_texts)
        if not "".join(page_texts).strip():
            return f"PDF document at {url} contains no extractable text; it may require OCR."
        first, last = AdaptiveReader.parse_pages(pages) or (1, total)
        if first > total:
            raise ValueError(f"the document has {total} pages; page {first} does not exist.")
        last = min(last, total)
        body = AdaptiveReader.assemble_pdf_pages(
            ((n, page_texts[n - 1]) for n in range(first, last + 1)), total, last, max_chars
        )
        return (
            f"# PDF document\nURL: {url}\n"
            f"({total} pages. Chromium's PDF viewer has no DOM: this is the extracted, "
            'page-marked text. Use read_page with pages="12-15" for a specific range.)\n\n'
            + body
        )

    @staticmethod
    def _pdf_pages(data: bytes) -> list[str]:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [(page.extract_text() or "").strip() for page in reader.pages]

    async def read_page(
        self, selector: str | None = None, max_chars: int = 20000, pages: str | None = None
    ) -> str:
        if await self.is_pdf_document():
            return await self.pdf_document_text(max_chars, pages)
        if pages:
            raise ValueError("pages applies only when the current page is a PDF document.")
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
        target = normalize_url(url)
        await self._goto(target)
        self.url = target
        await self._settle()
        await self.discover_tools(reload_on_failure=True)
        return await self.read_page()

    async def snapshot(
        self,
        *,
        interactive_only: bool = True,
        compact: bool = True,
        selector: str | None = None,
        max_chars: int = 12000,
    ) -> str:
        """Return a compact semantic page tree with deterministic element refs."""
        if await self.is_pdf_document():
            return await self.pdf_document_text(max_chars)
        return await self._eval_read(
            "options => window.__anybridge_pt__.snapshot(options)",
            {
                "interactiveOnly": interactive_only,
                "compact": compact,
                "selector": selector,
                "maxChars": max_chars,
            },
        )

    async def _element_for_ref(self, ref: str):
        handle = await self._page.evaluate_handle(
            "ref => window.__anybridge_pt__.resolveRef(ref)", ref
        )
        element = handle.as_element()
        if element is None:
            await handle.dispose()
            raise ValueError(f'Unknown element ref "{ref}". Take a new snapshot.')
        return element

    async def _describe_ref(self, ref: str) -> dict:
        return await self._eval_read(
            "ref => window.__anybridge_pt__.describeRef(ref)", ref
        )

    def begin_recording(self) -> None:
        self._recorded_actions = []
        self._recording_start_url = (
            self._page.url if self._page and self._page.url != "about:blank" else self.url
        )
        self._record_actions = True

    def end_recording(self) -> list[dict]:
        self._record_actions = False
        return list(self._recorded_actions)

    @property
    def recording_start_url(self) -> str | None:
        return self._recording_start_url

    def _record(self, action: str, target: dict | None = None, **values) -> None:
        if self._record_actions:
            self._recorded_actions.append(
                {"action": action, "target": target or {}, **values}
            )

    async def _after_ref_action(self) -> str:
        await self._settle(0.35)
        self.url = self._page.url
        return await self.snapshot(interactive_only=True, compact=True)

    async def click_ref(self, ref: str) -> str:
        target = await self._describe_ref(ref)
        element = await self._element_for_ref(ref)
        try:
            try:
                async with self._page.expect_navigation(
                    wait_until="domcontentloaded", timeout=6000
                ):
                    await element.click(timeout=8000)
            except PWTimeoutError:
                pass
        finally:
            await element.dispose()
        self._record("click", target)
        return await self._after_ref_action()

    async def fill_ref(self, ref: str, value: str, *, press_enter: bool = False) -> str:
        target = await self._describe_ref(ref)
        element = await self._element_for_ref(ref)
        try:
            await element.fill(value, timeout=8000)
            if press_enter:
                try:
                    async with self._page.expect_navigation(
                        wait_until="domcontentloaded", timeout=6000
                    ):
                        await element.press("Enter")
                except PWTimeoutError:
                    pass
        finally:
            await element.dispose()
        variable = "value_" + str(
            len(
                [
                    step
                    for step in self._recorded_actions
                    if step.get("action") in {"fill", "select"}
                ]
            )
            + 1
        )
        self._record("fill", target, variable=variable, press_enter=bool(press_enter))
        return await self._after_ref_action()

    async def select_ref(self, ref: str, value: str) -> str:
        target = await self._describe_ref(ref)
        element = await self._element_for_ref(ref)
        try:
            await element.select_option(value=value, timeout=8000)
        finally:
            await element.dispose()
        variable = "value_" + str(
            len(
                [
                    step
                    for step in self._recorded_actions
                    if step.get("action") in {"fill", "select"}
                ]
            )
            + 1
        )
        self._record("select", target, variable=variable)
        return await self._after_ref_action()

    async def press_key(self, key: str, ref: str | None = None) -> str:
        target = await self._describe_ref(ref) if ref else {}
        if ref:
            element = await self._element_for_ref(ref)
            try:
                await element.press(key)
            finally:
                await element.dispose()
        else:
            await self._page.keyboard.press(key)
        self._record("press", target, key=key)
        return await self._after_ref_action()

    async def run_recorded_step(self, step: dict, variables: dict) -> None:
        """Replay one deterministic workflow step with resilient locator fallback."""
        target = step.get("target") or {}
        locator = None
        selector = target.get("selector")
        if selector:
            candidate = self._page.locator(selector)
            if await candidate.count():
                locator = candidate.first
        if locator is None and target.get("role") and target.get("name"):
            candidate = self._page.get_by_role(
                target["role"], name=target["name"], exact=True
            )
            if await candidate.count():
                locator = candidate.first
        action = step.get("action")
        if action != "press" and locator is None:
            raise ValueError(
                f'Workflow target not found: {target.get("name") or selector or "unknown"}'
            )
        if action == "click":
            await locator.click(timeout=8000)
        elif action == "fill":
            key = step.get("variable")
            if key not in variables:
                raise ValueError(f'Workflow variable "{key}" is required.')
            await locator.fill(str(variables[key]), timeout=8000)
            if step.get("press_enter"):
                await locator.press("Enter")
        elif action == "select":
            key = step.get("variable")
            if key not in variables:
                raise ValueError(f'Workflow variable "{key}" is required.')
            await locator.select_option(value=str(variables[key]), timeout=8000)
        elif action == "press":
            if locator is not None:
                await locator.press(str(step.get("key") or "Enter"))
            else:
                await self._page.keyboard.press(str(step.get("key") or "Enter"))
        else:
            raise ValueError(f'Unsupported workflow action: "{action}".')
        await self._settle(0.25)

    async def wait_for(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int = 10000,
    ) -> str:
        timeout_ms = max(1, min(int(timeout_ms), 60000))
        if await self.is_pdf_document():
            # Nothing in a PDF viewer ever becomes "visible" to a locator: answer now.
            return await self.pdf_document_text()
        if selector:
            await self._page.locator(selector).first.wait_for(
                state="visible", timeout=timeout_ms
            )
        elif text:
            await self._page.get_by_text(text, exact=False).first.wait_for(
                state="visible", timeout=timeout_ms
            )
        else:
            await self._page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        return await self.snapshot(interactive_only=True, compact=True)

    async def extract_structured(
        self, schema: dict, selector: str | None = None
    ) -> dict:
        if not isinstance(schema, dict):
            raise ValueError("extract schema must be a JSON object.")
        return await self._eval_read(
            "({schema, selector}) => window.__anybridge_pt__.structuredExtract(schema, selector)",
            {"schema": schema, "selector": selector},
        )

    async def screenshot(self, *, full_page: bool = False) -> dict:
        data = await self._page.screenshot(type="png", full_page=bool(full_page))
        return {
            "content": [
                {
                    "type": "image",
                    "data": base64.b64encode(data).decode("ascii"),
                    "mimeType": "image/png",
                }
            ]
        }

    async def storage_snapshot(self, origin: str | None = None) -> dict:
        """Export explicitly scoped cookies and web storage for persistence."""
        state = await self._context.storage_state()
        if not origin:
            return state
        from urllib.parse import urlsplit

        parsed = urlsplit(origin)
        host = (parsed.hostname or "").casefold()
        normalized_origin = f"{parsed.scheme}://{parsed.netloc}".casefold()

        def cookie_matches(cookie: dict) -> bool:
            domain = str(cookie.get("domain") or "").lstrip(".").casefold()
            return bool(host and domain and (host == domain or host.endswith(f".{domain}")))

        return {
            "cookies": [cookie for cookie in state.get("cookies", []) if cookie_matches(cookie)],
            "origins": [
                entry
                for entry in state.get("origins", [])
                if str(entry.get("origin") or "").casefold() == normalized_origin
            ],
        }

    async def load_storage_snapshot(self, state: dict, url: str) -> str:
        await self.close()
        self.storage_state = state
        self.url = normalize_url(url)
        await self.start(settle=0.5)
        return await self.snapshot(interactive_only=True, compact=True)

    async def reset(self) -> str:
        """Recover a failed page without requiring the MCP client to restart."""
        target = self._page.url if self._page and self._page.url != "about:blank" else self.url
        await self.close()
        self.url = target
        await self.start(settle=0.25)
        return await self.snapshot(interactive_only=True, compact=True)

    async def access_status(self) -> dict:
        """Detect an interstitial without attempting to defeat it.

        This deliberately uses strong page signals instead of treating every
        mention of "captcha" as a block. The continuity engine can then switch
        to a cached, HTTP, or archived read while keeping live actions honest.
        """
        if not self.started:
            return {"blocked": False, "kind": None}
        return await self._eval_read(
            r"""() => {
              const title = (document.title || '').toLowerCase();
              const body = (document.body?.innerText || '').slice(0, 8000).toLowerCase();
              const challengeFrame = [...document.querySelectorAll('iframe')].some((frame) =>
                /recaptcha|hcaptcha|challenges\.cloudflare\.com/i.test(frame.src || '')
              );
              const challengeWidget = Boolean(document.querySelector(
                '[data-sitekey], .g-recaptcha, .h-captcha, .cf-turnstile, #challenge-form'
              ));
              const humanText = /verify (that )?you are human|checking (if )?you are human|robot or human/.test(body);
              const blockedTitle = /just a moment|attention required|access denied|security check/.test(title);
              const captchaText = /complete the captcha|captcha verification|verify captcha/.test(body);
              const blocked = challengeFrame || challengeWidget || humanText || blockedTitle || captchaText;
              let kind = null;
              if (challengeFrame || challengeWidget || captchaText) kind = 'captcha';
              else if (blocked) kind = 'access-challenge';
              return {blocked, kind, title: document.title || ''};
            }"""
        )

    async def current_site(self) -> dict:
        """Describe the current page without exposing browser internals."""
        if not self.started:
            return {"browser_started": False, "url": None, "title": None, "webmcp_tools": 0}
        return {
            "browser_started": True,
            "url": self._page.url if self._page.url != "about:blank" else None,
            "title": await self._eval_read("() => document.title || ''"),
            "webmcp_tools": len(await self.list_tools()),
        }

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
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()
