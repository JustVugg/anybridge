"""Adaptive, bounded content access with durable continuity fallbacks."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from pypdf import PdfReader

from .security import NetworkGuard
from .sites import default_config_dir, normalize_url


@dataclass(frozen=True, slots=True)
class EngineResult:
    url: str
    content: str
    engine: str
    cached: bool = False
    stale: bool = False
    captured_at: str | None = None

    def as_text(self) -> str:
        fields = [
            f"engine={self.engine}",
            f"cached={str(self.cached).lower()}",
            f"stale={str(self.stale).lower()}",
            f"url={self.url}",
        ]
        if self.captured_at:
            fields.append(f"captured_at={self.captured_at}")
        return " ".join(fields) + f"\n\n{self.content}"


class _Budget:
    """Wall-clock budget shared by every route of one read."""

    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + max(0.0, float(seconds))

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def slice(self, wanted: float, minimum: float = 4.0) -> float | None:
        """Time a route may take: its own limit, capped by what is left.

        Returns None when the leftover is too short for a meaningful attempt,
        so the route is skipped and reported instead of timing out mid-flight.
        """
        available = min(float(wanted), self.remaining - 1.0)
        return available if available >= minimum else None


class PersistentContentCache:
    """Atomic, per-URL files shared safely by local AnyBridge processes."""

    def __init__(self, path: Path | None = None, *, max_entries: int = 500) -> None:
        self.path = path or default_config_dir() / "content-cache"
        self.max_entries = max(10, int(max_entries))

    def get(self, url: str, *, max_age: float | None = None) -> EngineResult | None:
        entry_path = self._entry_path(url)
        if not entry_path.exists():
            return None
        try:
            row = json.loads(entry_path.read_text(encoding="utf-8"))
            if row.get("lookup_url") != url:
                return None
            final_url = row["final_url"]
            content = row["content"]
            engine = row["engine"]
            captured_at = float(row["captured_at"])
        except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError):
            return None
        age = max(0.0, time.time() - captured_at)
        if max_age is not None and age > max(0.0, float(max_age)):
            return None
        timestamp = datetime.fromtimestamp(captured_at, timezone.utc).isoformat()
        return EngineResult(
            str(final_url),
            str(content),
            str(engine),
            cached=True,
            stale=max_age is None,
            captured_at=timestamp,
        )

    def put(self, lookup_url: str, result: EngineResult) -> None:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            entry_path = self._entry_path(lookup_url)
            temporary = entry_path.with_name(
                f".{entry_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
            )
            payload = {
                "version": 1,
                "lookup_url": lookup_url,
                "final_url": result.url,
                "content": result.content,
                "engine": result.engine,
                "captured_at": time.time(),
            }
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            if os.name != "nt":
                temporary.chmod(0o600)
            temporary.replace(entry_path)
            self._prune()
        except OSError:
            # Availability must never depend on the cache being writable.
            return

    def _entry_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.path / f"{key}.json"

    def _prune(self) -> None:
        entries = sorted(
            self.path.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for entry in entries[self.max_entries :]:
            try:
                entry.unlink()
            except OSError:
                pass


class AdaptiveReader:
    """Choose a bounded live engine, then degrade to durable or archived content."""

    def __init__(
        self,
        *,
        allow_private_network: bool = True,
        cache_seconds: float = 300,
        cache_path: Path | None = None,
        archive_fallback: bool = True,
        browser_timeout: float = 45,
        total_timeout: float = 68,
    ) -> None:
        self.guard = NetworkGuard(allow_private=allow_private_network)
        # Under the 75-second MCP call deadline in server.py, with margin.
        self.total_timeout = max(5.0, float(total_timeout))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.archive_fallback = bool(archive_fallback)
        self.browser_timeout = max(5.0, float(browser_timeout))
        self._cache: dict[str, tuple[float, EngineResult]] = {}
        self._persistent = (
            PersistentContentCache(cache_path) if self.cache_seconds > 0 else None
        )
        self._browser_retry_at = 0.0
        self._browser_failure = ""
        self._background_tasks: set[asyncio.Task] = set()

    @property
    def browser_retry_seconds(self) -> float:
        """Seconds before a failed browser route should be attempted again."""
        return max(0.0, self._browser_retry_at - time.monotonic())

    @property
    def browser_failure(self) -> str:
        return self._browser_failure

    def note_browser_failure(self, error: Exception) -> None:
        self._browser_failure = f"{type(error).__name__}: {str(error)[:160]}"
        self._browser_retry_at = time.monotonic() + 30

    async def close(self) -> None:
        """Cancel optional cache warmers without delaying session shutdown."""
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def read(
        self,
        url: str,
        *,
        bridge=None,
        max_chars: int = 20000,
        prefer: str = "auto",
        pages: str | None = None,
        total_timeout: float | None = None,
    ) -> EngineResult:
        target = normalize_url(url)
        document = self._is_pdf(target)
        max_chars = max(500, min(int(max_chars), 100000))
        page_range = self.parse_pages(pages)
        if page_range and not document:
            raise ValueError("pages applies only to PDF documents.")
        if prefer not in {"auto", "http", "lightpanda", "chromium", "archive"}:
            raise ValueError(
                "prefer must be auto, http, lightpanda, chromium, or archive."
            )
        # One deadline for the whole cascade, kept under the MCP call limit.
        # Competing PDF routes run concurrently below so neither can starve the other.
        budget = _Budget(self.total_timeout if total_timeout is None else total_timeout)
        if prefer == "auto" and not page_range:
            cached = await self._fresh_cached(target)
            if cached:
                return cached

        errors: list[str] = []
        partial: EngineResult | None = None
        parallel_pdf = (
            document
            and prefer in {"auto", "chromium"}
            and getattr(bridge, "started", False)
        )
        if parallel_pdf:
            # The direct request and the authenticated browser-context request are
            # independent routes to the same bytes. Run them together so a slow
            # server cannot spend most of the MCP deadline before the fallback
            # even starts. The first complete PDF wins; a partial HTTP shell is
            # retained only if both document routes fail.
            timeout = budget.slice(max(45, self.browser_timeout + 15))
            if timeout:
                result, partial, route_errors = await self._race_pdf_reads(
                    target,
                    bridge=bridge,
                    max_chars=max_chars,
                    pages=page_range,
                    timeout=timeout,
                )
                errors.extend(route_errors)
                if result:
                    if not page_range:
                        await self._store(target, result)
                    return result
            else:
                errors.append("pdf routes: skipped, read budget exhausted")

        if not parallel_pdf and (
            prefer in {"auto", "http"} or (document and prefer == "chromium")
        ):
            timeout = budget.slice(45 if document else 18)
            if timeout:
                try:
                    result, complete = await asyncio.wait_for(
                        self._http_read(
                            target, max_chars=max_chars, document=document, pages=page_range
                        ),
                        timeout=timeout,
                    )
                    if complete:
                        if not page_range:
                            await self._store(target, result)
                        return result
                    partial = replace(result, engine="http-partial")
                    errors.append("http: page returned an incomplete application shell")
                except Exception as error:
                    errors.append(self._error("http", error))
            else:
                errors.append("http: skipped, read budget exhausted")

        # Lightpanda renders JavaScript; a PDF has none to render.
        if prefer in {"auto", "lightpanda"} and not document:
            timeout = budget.slice(25)
            if timeout:
                try:
                    result = await asyncio.wait_for(
                        self._lightpanda_read(target, max_chars=max_chars), timeout=timeout
                    )
                    await self._store(target, result)
                    return result
                except Exception as error:
                    errors.append(self._error("lightpanda", error))
            else:
                errors.append("lightpanda: skipped, read budget exhausted")

        if prefer in {"auto", "chromium"} and bridge is not None and not document:
            timeout = budget.slice(self.browser_timeout + 10)
            if timeout:
                try:
                    result = await asyncio.wait_for(
                        self._chromium_read(target, bridge=bridge, max_chars=max_chars),
                        timeout=timeout,
                    )
                    await self._store(target, result)
                    return result
                except Exception as error:
                    errors.append(self._error("chromium", error))
            else:
                errors.append("chromium: skipped, read budget exhausted")

        if prefer != "archive":
            stale = await self._stale_cached(target)
            if stale:
                return stale

        if self.archive_fallback and prefer in {"auto", "archive", "chromium"}:
            timeout = budget.slice(18)
            if timeout:
                try:
                    result = await asyncio.wait_for(
                        self._wayback_read(target, max_chars=max_chars), timeout=timeout
                    )
                    # Archive content is useful as a fallback but must not replace the
                    # last known live copy in the persistent cache.
                    return result
                except Exception as error:
                    errors.append(self._error("wayback", error))
            else:
                errors.append("wayback: skipped, read budget exhausted")

        if partial:
            return partial
        detail = "; ".join(errors) or "no compatible content route"
        return EngineResult(
            target,
            "Live and historical content is temporarily unavailable. "
            "AnyBridge stayed available and the request can be retried.\n\n"
            f"Routes attempted: {detail}",
            "unavailable",
        )

    async def navigate(self, url: str, *, bridge, max_chars: int = 20000) -> str:
        """Prefer an interactive page but always return a bounded continuity result."""
        target = normalize_url(url)
        if self._is_pdf(target):
            # Chromium's built-in PDF viewer exposes an empty DOM. Documents
            # belong to the extraction route, not the interactive page route.
            # The bridge is passed along but never started for a document.
            result = await self.read(
                target,
                bridge=bridge,
                max_chars=max_chars,
                prefer="auto",
            )
            return result.as_text()
        budget = _Budget(self.total_timeout)
        public_warm = None
        if self.cache_seconds and not urlsplit(target).query:
            public_warm = asyncio.create_task(
                self._warm_public(target, max_chars=max_chars),
                name="anybridge-public-cache-warm",
            )
            self._background_tasks.add(public_warm)
            public_warm.add_done_callback(self._background_tasks.discard)
        try:
            result = await self._chromium_read(
                target, bridge=bridge, max_chars=max_chars
            )
            await self._store(target, result)
            return result.content
        except Exception as browser_error:
            if public_warm:
                try:
                    await public_warm
                except Exception:
                    pass
            fallback = await self.read(
                target,
                bridge=None,
                max_chars=max_chars,
                prefer="auto",
                total_timeout=max(12.0, budget.remaining),
            )
            reason = self._error("live browser", browser_error)
            return (
                "AnyBridge continuity mode (read-only). "
                f"{reason}. Interactive actions are not reported as completed.\n\n"
                + fallback.as_text()
            )

    async def _warm_public(self, url: str, *, max_chars: int) -> None:
        """Populate durable public content while Chromium opens independently."""
        try:
            if await self._fresh_cached(url):
                return
            result, complete = await asyncio.wait_for(
                self._http_read(url, max_chars=max_chars), timeout=18
            )
            if complete:
                await self._store(url, result)
        except Exception:
            return

    async def _chromium_read(self, url: str, *, bridge, max_chars: int) -> EngineResult:
        if time.monotonic() < self._browser_retry_at and not bridge.started:
            raise RuntimeError(
                f"browser circuit open after a recent failure: {self._browser_failure}"
            )
        try:
            if not bridge.started:
                await asyncio.wait_for(bridge.start(), timeout=min(25, self.browser_timeout))
            content = await asyncio.wait_for(
                bridge.navigate(url), timeout=self.browser_timeout
            )
            if hasattr(bridge, "access_status"):
                status = await asyncio.wait_for(bridge.access_status(), timeout=3)
                if status.get("blocked"):
                    raise RuntimeError(
                        f"access challenge detected ({status.get('kind') or 'challenge'})"
                    )
            self._browser_retry_at = 0.0
            self._browser_failure = ""
            return EngineResult(
                getattr(bridge, "current_url", url), content[:max_chars], "chromium"
            )
        except Exception as error:
            self.note_browser_failure(error)
            if hasattr(bridge, "close"):
                try:
                    await asyncio.wait_for(bridge.close(), timeout=5)
                except Exception:
                    pass
            raise

    async def _browser_pdf_read(
        self, url: str, *, bridge, max_chars: int, pages: tuple[int, int] | None = None
    ) -> EngineResult:
        data, media_type = await bridge.fetch_bytes(url, timeout_ms=int(self.browser_timeout * 1000))
        if media_type != "application/pdf" and not data.startswith(b"%PDF-"):
            raise RuntimeError(f"browser fetched {media_type or 'unknown content'}, not a PDF")
        text = await asyncio.to_thread(self._pdf_text, data, max_chars, pages)
        if not text.strip():
            raise RuntimeError("PDF contains no extractable text; it may require OCR")
        return EngineResult(url, text, "chromium-pdf")

    async def _race_pdf_reads(
        self,
        url: str,
        *,
        bridge,
        max_chars: int,
        pages: tuple[int, int] | None,
        timeout: float,
    ) -> tuple[EngineResult | None, EngineResult | None, list[str]]:
        """Race public HTTP and browser-context PDF reads within one deadline."""

        async def http_route() -> tuple[EngineResult, bool]:
            return await self._http_read(
                url, max_chars=max_chars, document=True, pages=pages
            )

        async def browser_route() -> tuple[EngineResult, bool]:
            return (
                await self._browser_pdf_read(
                    url, bridge=bridge, max_chars=max_chars, pages=pages
                ),
                True,
            )

        tasks = {
            asyncio.create_task(http_route(), name="anybridge-pdf-http"): "http",
            asyncio.create_task(browser_route(), name="anybridge-pdf-browser"): "chromium-pdf",
        }
        pending = set(tasks)
        errors: list[str] = []
        partial: EngineResult | None = None
        deadline = time.monotonic() + max(0.0, float(timeout))
        try:
            while pending:
                remaining = max(0.0, deadline - time.monotonic())
                if not remaining:
                    break
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                complete_results: list[EngineResult] = []
                for task in done:
                    route = tasks[task]
                    try:
                        result, complete = task.result()
                    except Exception as error:
                        errors.append(self._error(route, error))
                        continue
                    if complete:
                        complete_results.append(result)
                    elif route == "http":
                        partial = replace(result, engine="http-partial")
                        errors.append(
                            "http: page returned an incomplete application shell"
                        )
                if complete_results:
                    # Prefer the public result when both finish in the same event-loop
                    # turn; it is eligible for the durable cache, unlike session data.
                    winner = next(
                        (
                            result
                            for result in complete_results
                            if result.engine == "http-pdf"
                        ),
                        complete_results[0],
                    )
                    return winner, partial, errors
            for task in pending:
                errors.append(f"{tasks[task]}: timed out within the shared PDF budget")
            return None, partial, errors
        finally:
            unfinished = [task for task in tasks if not task.done()]
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)

    async def _fresh_cached(self, url: str) -> EngineResult | None:
        entry = self._cache.get(url)
        if entry:
            created, result = entry
            if time.monotonic() - created <= self.cache_seconds:
                return replace(result, cached=True, stale=False)
        if self._persistent:
            return self._persistent.get(url, max_age=self.cache_seconds)
        return None

    async def _stale_cached(self, url: str) -> EngineResult | None:
        if self._persistent:
            return self._persistent.get(url)
        entry = self._cache.get(url)
        if entry:
            return replace(entry[1], cached=True, stale=True)
        return None

    async def _store(self, url: str, result: EngineResult) -> None:
        if not self.cache_seconds or result.engine in {"wayback", "unavailable"}:
            return
        self._cache[url] = (time.monotonic(), result)
        # Never persist a rendered authenticated page or a URL that may carry a
        # reset/session token. Durable continuity is for public reads only.
        if (
            self._persistent
            and not result.engine.startswith("chromium")
            and not urlsplit(url).query
        ):
            self._persistent.put(url, result)

    async def _http_read(
        self,
        url: str,
        *,
        max_chars: int,
        document: bool = False,
        pages: tuple[int, int] | None = None,
    ) -> tuple[EngineResult, bool]:
        target = await self.guard.assert_url(url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json,application/pdf;q=0.9,*/*;q=0.8",
        }
        # A document is one large body from a possibly slow server: give the
        # read phase room without loosening the connect deadline.
        timeout = httpx.Timeout(30, connect=10) if document else httpx.Timeout(12)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, headers=headers
        ) as client:
            response = None
            for _ in range(6):
                target = await self.guard.assert_url(target)
                response = await client.get(target)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                target = urljoin(str(response.url), location)
            if response is None:
                raise RuntimeError("No HTTP response.")
            response.raise_for_status()
            final_url = str(response.url)
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if media_type == "application/pdf" or response.content.startswith(b"%PDF-"):
                text = await asyncio.to_thread(
                    self._pdf_text, response.content, max_chars, pages
                )
                if not text.strip():
                    raise RuntimeError(
                        "PDF contains no extractable text; it may require OCR"
                    )
                return EngineResult(final_url, text, "http-pdf"), True
            if media_type == "application/json":
                content = json.dumps(response.json(), indent=2, ensure_ascii=False)
                return EngineResult(final_url, content[:max_chars], "http-json"), True
            if "html" not in media_type and not response.text.lstrip().lower().startswith("<!doctype"):
                return EngineResult(final_url, response.text[:max_chars], "http"), True
            markdown, complete = self._html_markdown(response.text, final_url)
            return EngineResult(final_url, markdown[:max_chars], "http"), complete

    async def _wayback_read(self, url: str, *, max_chars: int) -> EngineResult:
        target = await self.guard.assert_url(url)
        parsed_target = urlsplit(target)
        if parsed_target.scheme not in {"http", "https"}:
            raise RuntimeError("Wayback supports only public HTTP(S) pages")
        # Query strings frequently contain reset, preview, and session tokens.
        # A historical fallback uses the public base page and never discloses
        # those values to a third-party archive.
        archive_target = parsed_target._replace(query="", fragment="").geturl()
        headers = {"User-Agent": "AnyBridge/1.0 (+https://github.com/JustVugg/anybridge)"}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10), follow_redirects=False, headers=headers
        ) as client:
            availability = await client.get(
                "https://archive.org/wayback/available", params={"url": archive_target}
            )
            availability.raise_for_status()
            closest = (
                availability.json().get("archived_snapshots", {}).get("closest", {})
            )
            timestamp = str(closest.get("timestamp") or "")
            if not closest.get("available") or not timestamp.isdigit():
                raise RuntimeError("no archived snapshot is available")
            snapshot_url = f"https://web.archive.org/web/{timestamp}id_/{archive_target}"
            response = None
            for _ in range(4):
                parsed = urlsplit(snapshot_url)
                if parsed.scheme != "https" or parsed.hostname != "web.archive.org":
                    raise RuntimeError("Wayback returned an unsafe snapshot location")
                response = await client.get(snapshot_url)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                snapshot_url = urljoin(snapshot_url, location)
            if response is None:
                raise RuntimeError("Wayback returned no snapshot response")
            response.raise_for_status()
            captured = self._wayback_timestamp(timestamp)
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if media_type == "application/pdf" or response.content.startswith(b"%PDF-"):
                rendered = await asyncio.to_thread(
                    self._pdf_text, response.content, max_chars
                )
            else:
                rendered, _ = self._html_markdown(response.text, target)
            content = (
                f"> Historical fallback from the Internet Archive Wayback Machine.\n"
                f"> Snapshot: {snapshot_url}\n> Captured: {captured}\n\n{rendered}"
            )
            return EngineResult(
                target,
                content[:max_chars],
                "wayback",
                cached=True,
                stale=True,
                captured_at=captured,
            )

    @staticmethod
    def _wayback_timestamp(value: str) -> str:
        padded = (value + "00000000000000")[:14]
        try:
            parsed = datetime.strptime(padded, "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            )
            return parsed.isoformat()
        except ValueError:
            return value

    @staticmethod
    def _error(route: str, error: Exception) -> str:
        detail = " ".join(str(error).split())[:240] or type(error).__name__
        return f"{route}: {detail}"

    @staticmethod
    def _html_markdown(html: str, url: str) -> tuple[str, bool]:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup.select("script, style, noscript, template, svg"):
            element.decompose()
        root = soup.select_one("main, article") or soup.body or soup
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        body_text = root.get_text(" ", strip=True)
        script_heavy = html.lower().count("<script") >= 8
        shell_markers = any(
            marker in html.lower()
            for marker in ('id="root"></div>', 'id="app"></div>', "enable javascript")
        )
        complete = len(body_text) >= 280 and not (shell_markers and len(body_text) < 1000)
        if script_heavy and len(body_text) < 500:
            complete = False
        rendered = markdownify(str(root), heading_style="ATX", bullets="-").strip()
        header = f"# {title or '(no title)'}\nURL: {url}\n\n"
        return header + rendered, complete

    @staticmethod
    def parse_pages(value: str | int | None) -> tuple[int, int] | None:
        """Turn "12", "12-15" or 12 into an inclusive 1-based (first, last) range."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", text)
        if not match:
            raise ValueError('pages must look like "12" or "12-15".')
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1 or last < first:
            raise ValueError('pages must be a 1-based range like "12-15".')
        return first, last

    @staticmethod
    def _pdf_text(
        data: bytes, max_chars: int = 100000, pages: tuple[int, int] | None = None
    ) -> str:
        reader = PdfReader(io.BytesIO(data))
        total = len(reader.pages)
        first, last = pages or (1, total)
        if first > total:
            raise ValueError(f"the document has {total} pages; page {first} does not exist.")
        last = min(last, total)

        def page_texts():
            for number in range(first, last + 1):
                yield number, (reader.pages[number - 1].extract_text() or "").strip()

        return AdaptiveReader.assemble_pdf_pages(page_texts(), total, last, max_chars)

    @staticmethod
    def assemble_pdf_pages(page_texts, total: int, last: int, max_chars: int) -> str:
        """Join page-marked texts within `max_chars`, then say where to resume.

        Pages are consumed lazily so a budget of a few thousand characters never
        extracts a 200-page document. The resume hint is appended *after* the
        cut, so it is never lost to the budget it describes.
        """
        emitted = ""
        cut_at: int | None = None
        for number, text in page_texts:
            part = f"--- page {number} of {total} ---\n{text}"
            candidate = part if not emitted else f"{emitted}\n\n{part}"
            if len(candidate) > max_chars:
                emitted = candidate[:max_chars].rstrip()
                cut_at = number
                break
            emitted = candidate
        if cut_at is not None and cut_at <= last:
            emitted += (
                f"\n\n[... budget reached inside page {cut_at} of {total}: "
                f'ask again with pages="{cut_at}-{last}" and a larger max_chars]'
            )
        return emitted

    @staticmethod
    def _is_pdf(url: str) -> bool:
        return urlsplit(url).path.casefold().endswith(".pdf")

    @staticmethod
    async def _lightpanda_read(url: str, *, max_chars: int) -> EngineResult:
        try:
            import lightpanda
        except ImportError as error:
            raise RuntimeError("Lightpanda is not installed") from error
        response = await asyncio.to_thread(
            lightpanda.fetch,
            url,
            dump="markdown",
            wait_until="networkidle",
        )
        body = str(response.text or "").strip()
        if len(body) < 100:
            raise RuntimeError("Lightpanda returned incomplete content")
        content = f"URL: {url}\n\n{body}"[:max_chars]
        return EngineResult(url, content, "lightpanda")
