"""Encrypted, portable browser profiles for the AnyBridge wallet."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .sites import default_config_dir


class ProfileError(ValueError):
    """Raised when an encrypted browser profile cannot be managed."""


@dataclass(frozen=True, slots=True)
class SavedProfile:
    name: str
    origin: str
    state: dict


class ProfileStore:
    """Store cookies and web storage encrypted at rest with a local private key."""

    def __init__(self, path: Path | None = None, key_path: Path | None = None) -> None:
        self.path = path or default_config_dir() / "profiles.json"
        self.key_path = key_path or self.path.with_name("wallet.key")

    def list(self) -> list[dict]:
        return [
            {"name": entry["name"], "origin": entry["origin"]}
            for entry in sorted(
                self._read_entries().values(), key=lambda value: value["name"].casefold()
            )
        ]

    def get(self, name: str) -> SavedProfile:
        try:
            entry = self._read_entries()[self._key(name)]
        except KeyError as error:
            raise ProfileError(f'No browser profile named "{name}".') from error
        try:
            decrypted = self._cipher().decrypt(entry["encrypted"].encode("ascii"))
            state = json.loads(decrypted.decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ProfileError(f'Browser profile "{entry["name"]}" cannot be decrypted.') from error
        if not isinstance(state, dict):
            raise ProfileError(f'Browser profile "{entry["name"]}" is invalid.')
        return SavedProfile(entry["name"], entry["origin"], state)

    def save(self, name: str, origin: str, state: dict) -> SavedProfile:
        clean = self._clean_name(name)
        if not isinstance(state, dict):
            raise ProfileError("Browser profile state must be an object.")
        encrypted = self._cipher().encrypt(
            json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        entries = self._read_entries()
        entries[self._key(clean)] = {
            "name": clean,
            "origin": str(origin),
            "encrypted": encrypted,
        }
        self._write_entries(entries)
        return SavedProfile(clean, str(origin), state)

    def remove(self, name: str) -> dict:
        entries = self._read_entries()
        try:
            removed = entries.pop(self._key(name))
        except KeyError as error:
            raise ProfileError(f'No browser profile named "{name}".') from error
        self._write_entries(entries)
        return {"name": removed["name"], "origin": removed["origin"]}

    @staticmethod
    def _clean_name(name: str) -> str:
        value = " ".join(str(name).split())
        if not value:
            raise ProfileError("The profile name cannot be empty.")
        if len(value) > 80:
            raise ProfileError("The profile name must be 80 characters or fewer.")
        return value

    @classmethod
    def _key(cls, name: str) -> str:
        return cls._clean_name(name).casefold()

    def _cipher(self) -> Fernet:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            temporary = self.key_path.with_name(f".{self.key_path.name}.tmp")
            temporary.write_bytes(key + b"\n")
            if os.name != "nt":
                temporary.chmod(0o600)
            try:
                temporary.replace(self.key_path)
            except OSError:
                if self.key_path.exists():
                    temporary.unlink(missing_ok=True)
                    key = self.key_path.read_bytes().strip()
                else:
                    raise
        try:
            return Fernet(key)
        except (TypeError, ValueError) as error:
            raise ProfileError(f"Invalid AnyBridge wallet key: {self.key_path}") from error

    def _read_entries(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("profiles", [])
            if not isinstance(entries, list):
                raise TypeError
            result = {}
            for entry in entries:
                clean = self._clean_name(entry["name"])
                if not isinstance(entry["encrypted"], str):
                    raise TypeError
                result[self._key(clean)] = {
                    "name": clean,
                    "origin": str(entry.get("origin") or ""),
                    "encrypted": entry["encrypted"],
                }
            return result
        except (OSError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ProfileError(f"Cannot read browser profiles from {self.path}.") from error

    def _write_entries(self, entries: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "profiles": sorted(entries.values(), key=lambda value: value["name"].casefold()),
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
            raise ProfileError(f"Cannot save browser profiles to {self.path}.") from error
