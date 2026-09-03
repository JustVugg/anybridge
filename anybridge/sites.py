"""Persistent saved-site aliases shared by every AnyBridge agent."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


class SiteStoreError(ValueError):
    """Raised when a saved site or the saved-sites file is invalid."""


@dataclass(frozen=True, slots=True)
class SavedSite:
    name: str
    url: str


def normalize_url(url: str) -> str:
    """Return a safe browser URL, adding https:// when the scheme is omitted."""
    value = str(url).strip()
    if not value:
        raise SiteStoreError("The site URL cannot be empty.")
    explicit_scheme = re.match(r"^([a-z][a-z0-9+.-]*):", value, flags=re.IGNORECASE)
    if explicit_scheme and explicit_scheme.group(1).lower() not in {"http", "https", "file"}:
        raise SiteStoreError("Only http://, https://, and file:// URLs are supported.")
    if not explicit_scheme:
        value = f"https://{value}"

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "file"}:
        raise SiteStoreError("Only http://, https://, and file:// URLs are supported.")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise SiteStoreError("Enter a complete website URL.")
    if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password):
        raise SiteStoreError(
            "Do not put usernames, passwords, or tokens in a website URL."
        )
    if parsed.scheme == "file" and not parsed.path:
        raise SiteStoreError("Enter a complete file:// URL.")
    return value


def default_config_dir() -> Path:
    """Return the platform-appropriate AnyBridge configuration directory."""
    override = os.environ.get("ANYBRIDGE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "anybridge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "anybridge"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "anybridge"


class SiteStore:
    """Small JSON-backed site registry with case-insensitive aliases."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_dir() / "sites.json"

    def list(self) -> list[SavedSite]:
        sites = self._read()
        return sorted(sites.values(), key=lambda site: site.name.casefold())

    def get(self, name: str) -> SavedSite:
        key = self._key(name)
        try:
            return self._read()[key]
        except KeyError as error:
            raise SiteStoreError(f'No saved site named "{name}".') from error

    def save(self, name: str, url: str) -> SavedSite:
        clean_name = self._clean_name(name)
        site = SavedSite(clean_name, normalize_url(url))
        sites = self._read()
        sites[self._key(clean_name)] = site
        self._write(sites)
        return site

    def remove(self, name: str) -> SavedSite:
        key = self._key(name)
        sites = self._read()
        try:
            removed = sites.pop(key)
        except KeyError as error:
            raise SiteStoreError(f'No saved site named "{name}".') from error
        self._write(sites)
        return removed

    @staticmethod
    def _clean_name(name: str) -> str:
        value = " ".join(str(name).split())
        if not value:
            raise SiteStoreError("The site name cannot be empty.")
        if len(value) > 80:
            raise SiteStoreError("The site name must be 80 characters or fewer.")
        return value

    @classmethod
    def _key(cls, name: str) -> str:
        return cls._clean_name(name).casefold()

    def _read(self) -> dict[str, SavedSite]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("sites", [])
            if not isinstance(entries, list):
                raise TypeError
            sites = {}
            for entry in entries:
                site = SavedSite(
                    self._clean_name(entry["name"]),
                    normalize_url(entry["url"]),
                )
                sites[self._key(site.name)] = site
            return sites
        except (OSError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise SiteStoreError(f"Cannot read saved sites from {self.path}.") from error

    def _write(self, sites: dict[str, SavedSite]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sites": [
                asdict(site)
                for site in sorted(sites.values(), key=lambda item: item.name.casefold())
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
            raise SiteStoreError(f"Cannot save sites to {self.path}.") from error
