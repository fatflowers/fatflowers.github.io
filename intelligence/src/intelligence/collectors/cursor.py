"""Two-phase cursor checkpoint: persistence must succeed before commit."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CursorCheckpoint:
    committed: dict[str, Any] = field(default_factory=dict)
    _candidate: dict[str, Any] | None = None

    def stage(self, candidate: Mapping[str, Any]) -> None:
        self._candidate = dict(candidate)

    @property
    def candidate(self) -> Mapping[str, Any] | None:
        return dict(self._candidate) if self._candidate is not None else None

    def commit(self) -> dict[str, Any]:
        if self._candidate is None:
            raise RuntimeError("no cursor candidate is staged")
        self.committed = self._candidate
        self._candidate = None
        return dict(self.committed)

    def rollback(self) -> dict[str, Any]:
        self._candidate = None
        return dict(self.committed)
