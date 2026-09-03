from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch

from textual.widgets import OptionList

from anybridge.browser import BrowserDependencyError, prepare_browser
from anybridge.engines import AdaptiveReader
from anybridge.launcher import (
    SESSION_INSTRUCTIONS,
    LaunchError,
    _run_child,
    build_launch_plan,
    build_terminal_plan,
)
from anybridge.repositories import (
    PreparedRepository,
    RepositoryError,
    RepositoryManager,
    RepositoryStore,
    normalize_repository_url,
)
from anybridge.server import BridgeRuntime
from anybridge.sites import SiteStore, SiteStoreError, normalize_url
from anybridge.tui import AnyBridgeTUI, SavedSitesScreen


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


class SiteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SiteStore(Path(self.temporary.name) / "sites.json")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_save_lookup_list_and_remove(self) -> None:
        saved = self.store.save("  My   Site ", "example.com")
        self.assertEqual(saved.name, "My Site")
        self.assertEqual(saved.url, "https://example.com")
        self.assertEqual(self.store.get("my site"), saved)
        self.assertEqual(self.store.list(), [saved])
        self.assertEqual(self.store.remove("MY SITE"), saved)
        self.assertEqual(self.store.list(), [])

    def test_rejects_unsafe_url_schemes(self) -> None:
        with self.assertRaises(SiteStoreError):
            normalize_url("javascript:alert(1)")

    def test_file_is_private_json(self) -> None:
        self.store.save("Docs", "https://example.com/docs")
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["sites"][0]["name"], "Docs")


class BrowserSetupTests(unittest.TestCase):
    def test_existing_marker_skips_browser_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "ready"
            marker.touch()
            with patch("anybridge.browser._probe_browser", new=AsyncMock()) as probe:
                self.assertFalse(prepare_browser(marker))
            probe.assert_not_awaited()

    def test_linux_missing_dependencies_are_installed_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "ready"
            probe = AsyncMock(
                side_effect=[BrowserDependencyError("missing libraries"), None]
            )
            completed = CompletedProcess([], 0, "", "")
            with (
                patch("anybridge.browser._probe_browser", new=probe),
                patch("anybridge.browser.subprocess.run", return_value=completed) as run,
                patch("anybridge.browser.sys.platform", "linux"),
            ):
                self.assertTrue(prepare_browser(marker))
            self.assertEqual(probe.await_count, 2)
            self.assertTrue(marker.exists())
            command = run.call_args.args[0]
            self.assertEqual(command[-3:], ["install", "--with-deps", "chromium"])


class LauncherTests(unittest.TestCase):
    def test_claude_uses_inline_session_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_launch_plan(
                "claude",
                executable="/tools/claude",
                python_executable="/venv/bin/python",
                repository_directory=temporary,
            )
            self.assertEqual(plan.argv[:2], ("/tools/claude", "--add-dir"))
            self.assertEqual(plan.argv[2], str(Path(temporary).resolve()))
            config_index = plan.argv.index("--mcp-config") + 1
            config = json.loads(plan.argv[config_index])
            server = config["mcpServers"]["anybridge"]
            self.assertEqual(server["command"], "/venv/bin/python")
            self.assertEqual(server["args"], ["-m", "anybridge.cli", "serve"])
            self.assertEqual(
                plan.argv[plan.argv.index("--append-system-prompt") + 1],
                SESSION_INSTRUCTIONS,
            )

    def test_codex_uses_command_line_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_launch_plan(
                "codex",
                executable="/tools/codex",
                python_executable="/venv/bin/python",
                repository_directory=temporary,
            )
            joined = "\n".join(plan.argv)
            self.assertIn("--add-dir", plan.argv)
            self.assertIn(str(Path(temporary).resolve()), plan.argv)
            self.assertIn('mcp_servers.anybridge.command="/venv/bin/python"', joined)
            self.assertIn(
                'mcp_servers.anybridge.args=["-m", "anybridge.cli", "serve"]',
                joined,
            )
            self.assertIn("mcp_servers.anybridge.required=true", joined)
            self.assertIn("mcp_servers.anybridge.startup_timeout_sec=30", joined)
            self.assertIn("developer_instructions=", joined)
            self.assertIn("Do not search the web", joined)

    def test_wsl_opens_a_new_windows_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_launch_plan(
                "codex",
                executable="/tools/codex",
                python_executable="/venv/bin/python",
                repository_directory=temporary,
            )
            commands = {
                "wt.exe": "/windows/wt.exe",
                "wsl.exe": "/windows/wsl.exe",
            }
            with patch(
                "anybridge.launcher.shutil.which",
                side_effect=lambda command: commands.get(command),
            ):
                terminal = build_terminal_plan(
                    plan,
                    cwd=temporary,
                    environ={"WSL_DISTRO_NAME": "Ubuntu"},
                )
            self.assertEqual(terminal.terminal, "/windows/wt.exe")
            self.assertEqual(terminal.argv[1:3], ("-w", "new"))
            self.assertIn("new-tab", terminal.argv)
            self.assertIn("wsl.exe", terminal.argv)
            self.assertNotIn("/windows/wsl.exe", terminal.argv)
            self.assertIn("--exec", terminal.argv)
            self.assertIn("anybridge.launcher", terminal.argv)

    def test_windows_has_native_console_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_launch_plan(
                "claude",
                executable="C:\\Tools\\claude.exe",
                python_executable="C:\\Python\\python.exe",
                repository_directory=temporary,
            )
            with patch("anybridge.launcher.shutil.which", return_value=None):
                terminal = build_terminal_plan(
                    plan,
                    cwd=temporary,
                    environ={},
                    platform="win32",
                    python_executable="C:\\Python\\python.exe",
                )
            self.assertEqual(terminal.terminal, "C:\\Python\\python.exe")
            self.assertNotEqual(terminal.creationflags, 0)
            self.assertIn("anybridge.launcher", terminal.argv)

    def test_macos_uses_terminal_applescript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_launch_plan(
                "claude",
                executable="/tools/claude",
                python_executable="/venv/bin/python",
                repository_directory=temporary,
            )
            with patch(
                "anybridge.launcher.shutil.which",
                side_effect=lambda command: (
                    "/usr/bin/osascript" if command == "osascript" else None
                ),
            ):
                terminal = build_terminal_plan(
                    plan,
                    cwd=temporary,
                    environ={},
                    platform="darwin",
                    python_executable="/venv/bin/python",
                )
            self.assertEqual(terminal.terminal, "/usr/bin/osascript")
            self.assertIn('tell application "Terminal"', terminal.argv)
            self.assertTrue(any("anybridge.launcher" in item for item in terminal.argv))

    def test_linux_uses_available_terminal_emulator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_launch_plan(
                "codex",
                executable="/tools/codex",
                python_executable="/venv/bin/python",
                repository_directory=temporary,
            )
            with patch(
                "anybridge.launcher.shutil.which",
                side_effect=lambda command: (
                    "/usr/bin/gnome-terminal" if command == "gnome-terminal" else None
                ),
            ):
                terminal = build_terminal_plan(
                    plan,
                    cwd=temporary,
                    environ={},
                    platform="linux",
                    python_executable="/venv/bin/python",
                )
            self.assertEqual(terminal.argv[:2], ("/usr/bin/gnome-terminal", "--"))
            self.assertIn("anybridge.launcher", terminal.argv)

    def test_unknown_agent_is_rejected(self) -> None:
        with self.assertRaises(LaunchError):
            build_launch_plan("unknown", executable="/tools/unknown")

    def test_browser_setup_failure_still_starts_agent_in_continuity_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = CompletedProcess([], 0, "", "")
            with (
                patch(
                    "anybridge.browser.prepare_browser",
                    side_effect=BrowserDependencyError("missing browser"),
                ),
                patch("anybridge.launcher.subprocess.run", return_value=completed) as run,
            ):
                code = _run_child(
                    "codex",
                    executable="/tools/codex",
                    repository_directory=temporary,
                )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 1)


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = RepositoryStore(root / "repositories.json")
        self.manager = RepositoryManager(root / "clones")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_save_lookup_list_and_remove(self) -> None:
        saved = self.store.save("  My   Repo ", "https://github.com/acme/demo.git")
        self.assertEqual(saved.name, "My Repo")
        self.assertEqual(self.store.get("my repo"), saved)
        self.assertEqual(self.store.list(), [saved])
        self.assertEqual(self.store.remove("MY REPO"), saved)

    def test_accepts_common_git_remotes_and_rejects_options(self) -> None:
        self.assertEqual(
            normalize_repository_url("git@github.com:acme/demo.git"),
            "git@github.com:acme/demo.git",
        )
        with self.assertRaises(RepositoryError):
            normalize_repository_url("--upload-pack=bad")

    def test_clone_uses_argument_vector_and_reuses_existing_clone(self) -> None:
        def fake_clone(argv, **kwargs):
            target = Path(argv[-1])
            (target / ".git").mkdir(parents=True)
            return CompletedProcess(argv, 0, "", "")

        with (
            patch("anybridge.repositories.shutil.which", return_value="/usr/bin/git"),
            patch("anybridge.repositories.subprocess.run", side_effect=fake_clone) as run,
        ):
            first = self.manager.prepare("https://github.com/acme/demo.git")
            second = self.manager.prepare("https://github.com/acme/demo.git")
        self.assertTrue(first.cloned)
        self.assertFalse(second.cloned)
        self.assertEqual(first.path, second.path)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/git", "clone", "--"])


class FakeBridge:
    def __init__(self) -> None:
        self.started = False
        self.start_count = 0

    async def start(self) -> None:
        self.started = True
        self.start_count += 1

    async def wait_for_tools(self, timeout: float) -> list[dict]:
        return []

    async def list_tools(self) -> list[dict]:
        return []

    async def navigate(self, url: str) -> str:
        return f"opened {url}"

    async def close(self) -> None:
        self.started = False


class BridgeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = SiteStore(root / "sites.json")
        self.repositories = RepositoryStore(root / "repositories.json")
        self.repository_manager = RepositoryManager(root / "clones")
        self.bridge = FakeBridge()
        self.runtime = BridgeRuntime(
            sites=self.store,
            repositories=self.repositories,
            repository_manager=self.repository_manager,
            bridge=self.bridge,
            adaptive=AdaptiveReader(cache_seconds=0, archive_fallback=False),
        )

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_saved_sites_do_not_start_browser(self) -> None:
        await self.runtime.call("save_site", {"name": "Docs", "url": "example.com"})
        listed = await self.runtime.call("list_saved_sites", {})
        self.assertIn("https://example.com", listed)
        self.assertEqual(self.bridge.start_count, 0)

    async def test_navigation_starts_browser_once(self) -> None:
        result = await self.runtime.call("navigate", {"url": "https://example.com"})
        self.assertEqual(result, "opened https://example.com")
        self.assertEqual(self.bridge.start_count, 1)

    async def test_initial_url_does_not_block_tool_listing_on_browser_start(self) -> None:
        runtime = BridgeRuntime(
            "https://example.com",
            sites=self.store,
            repositories=self.repositories,
            repository_manager=self.repository_manager,
            bridge=self.bridge,
        )
        tools = await runtime.tool_specs()
        self.assertIn("navigate", {tool["name"] for tool in tools})
        self.assertEqual(self.bridge.start_count, 0)

    async def test_saved_repositories_do_not_start_browser(self) -> None:
        await self.runtime.call(
            "save_repository",
            {"name": "Demo", "url": "https://github.com/acme/demo.git"},
        )
        listed = await self.runtime.call("list_saved_repositories", {})
        self.assertIn("https://github.com/acme/demo.git", listed)
        self.assertEqual(self.bridge.start_count, 0)

    async def test_open_repository_returns_native_working_path(self) -> None:
        prepared = PreparedRepository(
            "https://github.com/acme/demo.git",
            Path(self.temporary.name) / "clones" / "demo",
            True,
        )
        with patch(
            "anybridge.repositories.RepositoryManager.prepare_async",
            new=AsyncMock(return_value=prepared),
        ):
            result = await self.runtime.call(
                "open_repository",
                {"url": "https://github.com/acme/demo.git"},
            )
        # The result is JSON: on Windows the path arrives with escaped
        # backslashes, so compare the decoded field, not the raw string.
        payload = json.loads(result)
        self.assertEqual(payload["path"], str(prepared.path))
        self.assertIn("native file", result)
        self.assertEqual(self.bridge.start_count, 0)


class TUITests(unittest.IsolatedAsyncioTestCase):
    async def test_keyboard_selection_and_saved_sites_screen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            repositories = RepositoryStore(Path(temporary) / "repositories.json")
            with patch("anybridge.tui.which", side_effect=lambda name: f"/bin/{name}"):
                app = AnyBridgeTUI(store=store, repository_store=repositories)
                async with app.run_test(size=(80, 24)) as pilot:
                    options = app.query_one("#agent-list", OptionList)
                    self.assertTrue(options.has_focus)
                    self.assertEqual(app.selected_agent, "claude")
                    self.assertIn(
                        "any website, within reach of any agent",
                        str(app.query_one("#tagline").render()),
                    )
                    await pilot.press("down")
                    self.assertEqual(app.selected_agent, "codex")
                    await pilot.press("s")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, SavedSitesScreen)
                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertNotIsInstance(app.screen, SavedSitesScreen)

    async def test_enter_opens_agent_without_closing_tui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            repositories = RepositoryStore(Path(temporary) / "repositories.json")
            with (
                patch("anybridge.tui.which", side_effect=lambda name: f"/bin/{name}"),
                patch("anybridge.tui.launch_agent", return_value=1234) as launch,
            ):
                app = AnyBridgeTUI(store=store, repository_store=repositories)
                async with app.run_test(size=(80, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    launch.assert_called_once_with("claude")
                    self.assertTrue(app.is_running)
                    self.assertIn(
                        "new terminal",
                        str(app.query_one("#prompt").render()),
                    )


if __name__ == "__main__":
    unittest.main()
