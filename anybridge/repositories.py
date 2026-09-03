"""Saved Git repositories and local clones shared by AnyBridge agents."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


class RepositoryError(ValueError):
    """Raised when a repository reference or clone operation is invalid."""


@dataclass(frozen=True, slots=True)
class SavedRepository:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class PreparedRepository:
    url: str
    path: Path
    cloned: bool


def normalize_repository_url(url: str) -> str:
    """Validate a Git remote without passing it through a shell."""
    value = str(url).strip()
    if not value or value.startswith("-"):
        raise RepositoryError("Enter a repository URL.")
    if re.match(r"^[\w.-]+@[\w.-]+:[^\s]+$", value):
        return value

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ssh", "git", "file"}:
        raise RepositoryError(
            "Use an http(s), ssh, git, file, or git@host repository URL."
        )
    if parsed.scheme != "file" and not parsed.netloc:
        raise RepositoryError("Enter a complete repository URL.")
    if parsed.scheme == "file" and not parsed.path:
        raise RepositoryError("Enter a complete file:// repository URL.")
    if parsed.password:
        raise RepositoryError(
            "Do not put passwords or access tokens in a repository URL; use a Git "
            "credential helper or SSH agent instead."
        )
    return value


def default_data_dir() -> Path:
    override = os.environ.get("ANYBRIDGE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "anybridge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "anybridge"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "anybridge"


def repository_root(*, create: bool = True) -> Path:
    root = default_data_dir() / "repositories"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            root.chmod(0o700)
    return root


class RepositoryStore:
    """JSON-backed aliases for remote Git repositories."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from .sites import default_config_dir

            path = default_config_dir() / "repositories.json"
        self.path = path

    def list(self) -> list[SavedRepository]:
        return sorted(self._read().values(), key=lambda repository: repository.name.casefold())

    def get(self, name: str) -> SavedRepository:
        key = self._key(name)
        try:
            return self._read()[key]
        except KeyError as error:
            raise RepositoryError(f'No saved repository named "{name}".') from error

    def save(self, name: str, url: str) -> SavedRepository:
        clean_name = self._clean_name(name)
        repository = SavedRepository(clean_name, normalize_repository_url(url))
        repositories = self._read()
        repositories[self._key(clean_name)] = repository
        self._write(repositories)
        return repository

    def remove(self, name: str) -> SavedRepository:
        key = self._key(name)
        repositories = self._read()
        try:
            removed = repositories.pop(key)
        except KeyError as error:
            raise RepositoryError(f'No saved repository named "{name}".') from error
        self._write(repositories)
        return removed

    @staticmethod
    def _clean_name(name: str) -> str:
        value = " ".join(str(name).split())
        if not value:
            raise RepositoryError("The repository name cannot be empty.")
        if len(value) > 80:
            raise RepositoryError("The repository name must be 80 characters or fewer.")
        return value

    @classmethod
    def _key(cls, name: str) -> str:
        return cls._clean_name(name).casefold()

    def _read(self) -> dict[str, SavedRepository]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("repositories", [])
            if not isinstance(entries, list):
                raise TypeError
            repositories = {}
            for entry in entries:
                repository = SavedRepository(
                    self._clean_name(entry["name"]),
                    normalize_repository_url(entry["url"]),
                )
                repositories[self._key(repository.name)] = repository
            return repositories
        except (OSError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise RepositoryError(f"Cannot read saved repositories from {self.path}.") from error

    def _write(self, repositories: dict[str, SavedRepository]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "repositories": [
                asdict(repository)
                for repository in sorted(
                    repositories.values(), key=lambda item: item.name.casefold()
                )
            ],
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                temporary.chmod(0o600)
            temporary.replace(self.path)
        except OSError as error:
            raise RepositoryError(f"Cannot save repositories to {self.path}.") from error


class RepositoryManager:
    """Clone repositories into AnyBridge's dedicated agent-accessible directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else repository_root(create=False)

    def _clone_details(
        self,
        url: str,
    ) -> tuple[str, str, Path, PreparedRepository | None]:
        remote = normalize_repository_url(url)
        git = shutil.which("git")
        if not git:
            raise RepositoryError("git is not installed or is not in PATH.")

        target = self.root / self._directory_name(remote)
        if target.exists():
            if (target / ".git").is_dir():
                prepared = PreparedRepository(remote, target.resolve(), cloned=False)
                return remote, git, target, prepared
            raise RepositoryError(
                "Repository destination already exists and is not a Git clone: "
                f"{target}"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        return remote, git, target, None

    @staticmethod
    def _clone_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return environment

    def _temporary_target(self, target: Path) -> Path:
        suffix = f"{os.getpid()}-{secrets.token_hex(4)}"
        return self.root / f".{target.name}.clone-{suffix}"

    @staticmethod
    def _discard_temporary_clone(target: Path) -> None:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    def _publish_clone(
        self,
        remote: str,
        temporary: Path,
        target: Path,
    ) -> PreparedRepository:
        try:
            temporary.replace(target)
        except OSError as error:
            # Another AnyBridge agent may have completed the same clone first.
            if (target / ".git").is_dir():
                self._discard_temporary_clone(temporary)
                return PreparedRepository(remote, target.resolve(), cloned=False)
            self._discard_temporary_clone(temporary)
            raise RepositoryError(f"Could not store the cloned repository: {error}") from error
        return PreparedRepository(remote, target.resolve(), cloned=True)

    def prepare(self, url: str) -> PreparedRepository:
        """Synchronous clone helper for non-async integrations."""
        remote, git, target, prepared = self._clone_details(url)
        if prepared is not None:
            return prepared
        temporary = self._temporary_target(target)
        try:
            completed = subprocess.run(
                [git, "clone", "--", remote, str(temporary)],
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=self._clone_environment(),
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            self._discard_temporary_clone(temporary)
            raise RepositoryError("git clone timed out after 5 minutes.") from error
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "git clone failed"
            self._discard_temporary_clone(temporary)
            raise RepositoryError(message)
        return self._publish_clone(remote, temporary, target)

    async def prepare_async(self, url: str) -> PreparedRepository:
        """Clone without blocking the MCP server's event loop."""
        remote, git, target, prepared = self._clone_details(url)
        if prepared is not None:
            return prepared
        temporary = self._temporary_target(target)
        try:
            process = await asyncio.create_subprocess_exec(
                git,
                "clone",
                "--",
                remote,
                str(temporary),
                stdin=subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._clone_environment(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=300,
                )
            except asyncio.TimeoutError as error:
                process.kill()
                await process.wait()
                self._discard_temporary_clone(temporary)
                raise RepositoryError("git clone timed out after 5 minutes.") from error
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                self._discard_temporary_clone(temporary)
                raise
        except OSError as error:
            self._discard_temporary_clone(temporary)
            raise RepositoryError(f"Could not start git clone: {error}") from error

        if process.returncode != 0:
            message = (
                stderr.decode(errors="replace").strip()
                or stdout.decode(errors="replace").strip()
                or "git clone failed"
            )
            self._discard_temporary_clone(temporary)
            raise RepositoryError(message)
        return self._publish_clone(remote, temporary, target)

    @staticmethod
    def _directory_name(url: str) -> str:
        path = urlsplit(url).path if "://" in url else url.split(":", 1)[-1]
        parts = [part for part in path.strip("/").split("/") if part]
        readable = "-".join(parts[-2:]) if parts else "repository"
        if readable.endswith(".git"):
            readable = readable[:-4]
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", readable).strip("-._") or "repository"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        return f"{slug[:64]}-{digest}"
