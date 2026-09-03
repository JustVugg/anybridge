"""Launch native coding-agent TUIs with AnyBridge attached for one session."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class LaunchError(RuntimeError):
    """Raised when an agent cannot be launched."""


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    agent: str
    executable: str
    repository_directory: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TerminalPlan:
    """A command that opens an agent in a separate terminal window."""

    terminal: str
    argv: tuple[str, ...]
    creationflags: int = 0


AGENT_COMMANDS = {
    "claude": "claude",
    "codex": "codex",
}


SESSION_INSTRUCTIONS = """You are running inside an AnyBridge session. AnyBridge is the required
path for user-supplied websites and Git repositories.

When the user sends only a normal website URL, immediately use the AnyBridge navigate tool,
then inspect a live result with snapshot, discover namespaced WebMCP actions, and return a concise
overview of the site and the useful actions available. If navigate reports "continuity mode",
use the returned read-only content directly: do not call browser-only tools and never claim a live
action completed. Do not search the web, use WebFetch, or ask what the user wants before inspecting
it. When the URL is accompanied by a request, navigate with AnyBridge and carry it out immediately
when the requested live capability is available.

When the user sends only a GitHub, GitLab, Bitbucket, or other Git remote, immediately use the
AnyBridge open_repository tool, inspect the returned local repository with native file tools, and
summarize its purpose and structure. When the remote is accompanied by a request, open it with
AnyBridge and carry out that request immediately.

Use AnyBridge saved-resource tools when the user asks to save or reopen a site or repository by
name. Do not submit forms, publish changes, or perform other consequential actions unless the user
requests them. Prefer namespaced WebMCP tools, then exact snapshot refs; use text and CSS tools only
as compatibility fallbacks. For every PDF URL, call smart_read directly; do not inspect the empty
Chromium PDF viewer, take screenshots, or use browser find shortcuts. A website with no native
WebMCP tools still has the AnyBridge-generated MCP tools and is not an unsupported website.
Profiles open read-only and must only be persisted when the user asks.
Never install browser binaries or operating-system packages yourself; AnyBridge
performs browser setup before this session starts."""


def available_agents() -> dict[str, str | None]:
    """Return supported agent keys and their resolved executable paths."""
    return {key: shutil.which(command) for key, command in AGENT_COMMANDS.items()}


def _mcp_command(python_executable: str) -> tuple[str, list[str]]:
    # Use this exact interpreter so pipx/venv installations keep working even
    # when the agent starts the MCP server from another working directory.
    return python_executable, ["-m", "anybridge.cli", "serve"]


def build_launch_plan(
    agent: str,
    *,
    executable: str | None = None,
    python_executable: str | None = None,
    repository_directory: str | os.PathLike[str] | None = None,
) -> LaunchPlan:
    """Build a shell-free, session-scoped launch command for an agent."""
    if agent not in AGENT_COMMANDS:
        raise LaunchError(f'Unsupported agent: "{agent}".')

    resolved = executable or shutil.which(AGENT_COMMANDS[agent])
    if not resolved:
        raise LaunchError(f'{AGENT_COMMANDS[agent]} is not installed or is not in PATH.')

    python = python_executable or sys.executable
    mcp_executable, mcp_args = _mcp_command(python)
    if repository_directory is None:
        from .repositories import repository_root

        repository_directory = repository_root()
    repository_path = str(Path(repository_directory).expanduser().resolve())

    if agent == "claude":
        config = {
            "mcpServers": {
                "anybridge": {
                    "type": "stdio",
                    "command": mcp_executable,
                    "args": mcp_args,
                }
            }
        }
        # --mcp-config accepts multiple values, so all other Claude flags must
        # precede it or they would be consumed as additional config values.
        argv = (
            resolved,
            "--add-dir",
            repository_path,
            "--append-system-prompt",
            SESSION_INSTRUCTIONS,
            "--mcp-config",
            json.dumps(config, separators=(",", ":"), ensure_ascii=False),
        )
    else:
        # Codex parses -c values as TOML. JSON string and array syntax are also
        # valid TOML here, and passing argv directly avoids shell interpolation.
        argv = (
            resolved,
            "--add-dir",
            repository_path,
            "-c",
            f"mcp_servers.anybridge.command={json.dumps(mcp_executable)}",
            "-c",
            f"mcp_servers.anybridge.args={json.dumps(mcp_args)}",
            "-c",
            "mcp_servers.anybridge.required=true",
            "-c",
            "mcp_servers.anybridge.startup_timeout_sec=30",
            "-c",
            f"developer_instructions={json.dumps(SESSION_INSTRUCTIONS)}",
        )

    return LaunchPlan(
        agent=agent,
        executable=resolved,
        repository_directory=repository_path,
        argv=argv,
    )


def build_terminal_plan(
    agent_plan: LaunchPlan,
    *,
    cwd: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
    python_executable: str | None = None,
) -> TerminalPlan:
    """Build a new-terminal command for WSL, Windows, macOS, or Linux."""
    environment = os.environ if environ is None else environ
    working_directory = str(Path(cwd or os.getcwd()).resolve())
    current_platform = sys.platform if platform is None else platform
    python = python_executable or sys.executable
    child = (
        python,
        "-m",
        "anybridge.launcher",
        "--child",
        agent_plan.agent,
        "--executable",
        agent_plan.executable,
        "--repository-directory",
        agent_plan.repository_directory,
    )
    distro = environment.get("WSL_DISTRO_NAME")
    windows_terminal = shutil.which("wt.exe")
    wsl = shutil.which("wsl.exe")
    if distro and windows_terminal and wsl:
        return TerminalPlan(
            terminal=windows_terminal,
            argv=(
                windows_terminal,
                "-w",
                "new",
                "new-tab",
                "--title",
                f"AnyBridge · {agent_plan.agent}",
                # This token is interpreted by Windows Terminal, so use the
                # Windows command name rather than its /mnt/c WSL path.
                "wsl.exe",
                "--distribution",
                distro,
                "--cd",
                working_directory,
                "--exec",
                *child,
            ),
        )

    if current_platform == "win32":
        if windows_terminal:
            return TerminalPlan(
                terminal=windows_terminal,
                argv=(
                    windows_terminal,
                    "-w",
                    "new",
                    "new-tab",
                    "--title",
                    f"AnyBridge · {agent_plan.agent}",
                    "--startingDirectory",
                    working_directory,
                    *child,
                ),
            )
        # Python is a native executable and can safely create a fresh console.
        # The child then launches .exe/.cmd agent shims inside that console.
        return TerminalPlan(
            terminal=python,
            argv=child,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
        )

    if current_platform == "darwin":
        osascript = shutil.which("osascript")
        if not osascript:
            raise LaunchError("macOS Terminal could not be opened: osascript is missing.")
        command = f"cd {shlex.quote(working_directory)} && exec {shlex.join(child)}"
        return TerminalPlan(
            terminal=osascript,
            argv=(
                osascript,
                "-e",
                'tell application "Terminal"',
                "-e",
                "activate",
                "-e",
                f"do script {json.dumps(command)}",
                "-e",
                "end tell",
            ),
        )

    candidates = (
        ("gnome-terminal", ("--",)),
        ("konsole", ("-e",)),
        ("xfce4-terminal", ("-x",)),
        ("mate-terminal", ("-x",)),
        ("kitty", ("--directory", working_directory)),
        ("alacritty", ("--working-directory", working_directory, "-e")),
        (
            "wezterm",
            ("start", "--always-new-process", "--cwd", working_directory, "--"),
        ),
        ("foot", (f"--working-directory={working_directory}",)),
        ("x-terminal-emulator", ("-e",)),
        ("xterm", ("-e",)),
    )
    for command, prefix in candidates:
        terminal = shutil.which(command)
        if terminal:
            return TerminalPlan(
                terminal=terminal,
                argv=(terminal, *prefix, *child),
            )
    raise LaunchError(
        "No supported terminal was found. Install Windows Terminal, "
        "GNOME Terminal, Konsole, XFCE Terminal, Kitty, Alacritty, "
        "WezTerm, foot, or xterm."
    )


def launch_agent(agent: str, *, cwd: str | os.PathLike[str] | None = None) -> int:
    """Open the selected agent in another terminal and return the process ID."""
    agent_plan = build_launch_plan(agent)
    terminal_plan = build_terminal_plan(agent_plan, cwd=cwd)
    try:
        popen_options = {
            "cwd": str(Path(cwd or os.getcwd()).resolve()),
            "creationflags": terminal_plan.creationflags,
        }
        if sys.platform != "win32":
            popen_options["start_new_session"] = True
        process = subprocess.Popen(terminal_plan.argv, **popen_options)
    except OSError as error:
        raise LaunchError(
            f"Could not open {agent_plan.executable} in {terminal_plan.terminal}: {error}"
        ) from error
    return process.pid


def _run_child(
    agent: str,
    *,
    executable: str,
    repository_directory: str,
) -> int:
    """Run an agent inside the newly-created terminal."""
    try:
        plan = build_launch_plan(
            agent,
            executable=executable,
            repository_directory=repository_directory,
        )
        from .browser import BrowserDependencyError, BrowserInstallError, prepare_browser

        try:
            prepare_browser()
        except (BrowserDependencyError, BrowserInstallError, OSError) as error:
            print(
                "AnyBridge warning: the interactive browser is not ready "
                f"({error}). Starting {agent} in continuity mode; HTTP, cache, "
                "repositories, and Wayback remain available.",
                file=sys.stderr,
            )
        return_code = subprocess.run(plan.argv, check=False).returncode
        if return_code != 0 and sys.stdin.isatty():
            try:
                input(f"{agent} exited with code {return_code}. Press Enter to close...")
            except (EOFError, KeyboardInterrupt):
                pass
        return return_code
    except (LaunchError, OSError) as error:
        print(f"AnyBridge could not start {agent}: {error}", file=sys.stderr)
        if sys.stdin.isatty():
            try:
                input("Press Enter to close this terminal...")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--child", choices=tuple(AGENT_COMMANDS), required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--repository-directory", required=True)
    args = parser.parse_args()
    return _run_child(
        args.child,
        executable=args.executable,
        repository_directory=args.repository_directory,
    )


if __name__ == "__main__":
    raise SystemExit(_main())
