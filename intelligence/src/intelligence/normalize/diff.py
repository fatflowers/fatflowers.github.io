"""Deterministic web-page change detection."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

from .text import normalize_text


@dataclass(frozen=True, slots=True)
class TextDiff:
    changed: bool
    previous_hash: str | None
    current_hash: str
    unified_diff: str


def compare_text(previous: str | None, current: str) -> TextDiff:
    before = normalize_text(previous)
    after = normalize_text(current)
    before_hash = hashlib.sha256(before.encode()).hexdigest() if previous is not None else None
    after_hash = hashlib.sha256(after.encode()).hexdigest()
    if previous is not None and before_hash == after_hash:
        return TextDiff(False, before_hash, after_hash, "")
    patch = "\n".join(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), fromfile="previous", tofile="current", lineterm=""
        )
    )
    return TextDiff(True, before_hash, after_hash, patch)
