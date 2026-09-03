"""Deterministic workflow cache shared by every connected agent."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .sites import default_config_dir


class WorkflowError(ValueError):
    """Raised when a saved workflow is invalid."""


@dataclass(frozen=True, slots=True)
class SavedWorkflow:
    name: str
    origin: str
    start_url: str
    steps: tuple[dict, ...]

    @property
    def variables(self) -> list[str]:
        return sorted(
            {
                str(step["variable"])
                for step in self.steps
                if step.get("variable")
            }
        )


class WorkflowStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_dir() / "workflows.json"

    def list(self) -> list[SavedWorkflow]:
        return sorted(self._read().values(), key=lambda item: item.name.casefold())

    def get(self, name: str) -> SavedWorkflow:
        try:
            return self._read()[self._key(name)]
        except KeyError as error:
            raise WorkflowError(f'No workflow named "{name}".') from error

    def save(self, name: str, origin: str, start_url: str, steps: list[dict]) -> SavedWorkflow:
        clean = self._clean_name(name)
        if not steps:
            raise WorkflowError("Record at least one action before saving a workflow.")
        if len(steps) > 100:
            raise WorkflowError("A workflow can contain at most 100 actions.")
        workflow = SavedWorkflow(clean, str(origin), str(start_url), tuple(steps))
        entries = self._read()
        entries[self._key(clean)] = workflow
        self._write(entries)
        return workflow

    def remove(self, name: str) -> SavedWorkflow:
        entries = self._read()
        try:
            removed = entries.pop(self._key(name))
        except KeyError as error:
            raise WorkflowError(f'No workflow named "{name}".') from error
        self._write(entries)
        return removed

    @staticmethod
    def _clean_name(name: str) -> str:
        value = " ".join(str(name).split())
        if not value:
            raise WorkflowError("The workflow name cannot be empty.")
        if len(value) > 80:
            raise WorkflowError("The workflow name must be 80 characters or fewer.")
        return value

    @classmethod
    def _key(cls, name: str) -> str:
        return cls._clean_name(name).casefold()

    def _read(self) -> dict[str, SavedWorkflow]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            result = {}
            for entry in raw.get("workflows", []):
                workflow = SavedWorkflow(
                    self._clean_name(entry["name"]),
                    str(entry["origin"]),
                    str(entry["start_url"]),
                    tuple(entry["steps"]),
                )
                result[self._key(workflow.name)] = workflow
            return result
        except (OSError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise WorkflowError(f"Cannot read workflows from {self.path}.") from error

    def _write(self, entries: dict[str, SavedWorkflow]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "workflows": [
                asdict(item)
                for item in sorted(entries.values(), key=lambda value: value.name.casefold())
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
            raise WorkflowError(f"Cannot save workflows to {self.path}.") from error
