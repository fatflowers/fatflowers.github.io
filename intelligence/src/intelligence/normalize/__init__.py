"""Canonical item models and deterministic normalization helpers."""

from .dedupe import DedupeIndex, content_hash, dedupe_key
from .models import NormalizedItem
from .text import canonicalize_url, normalize_text

__all__ = [
    "DedupeIndex",
    "NormalizedItem",
    "canonicalize_url",
    "content_hash",
    "dedupe_key",
    "normalize_text",
]
