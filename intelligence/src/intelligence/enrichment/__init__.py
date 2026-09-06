"""Evidence-preserving article hydration; retrieval time is never publication time."""

from .article import discover_links, enrich_article, fetch_article

__all__ = ["discover_links", "enrich_article", "fetch_article"]
