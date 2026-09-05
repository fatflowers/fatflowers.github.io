"""Deterministic URL and content normalization."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "ref_url",
}
_TRACKING_PREFIXES = ("utm_",)
_HORIZONTAL_SPACE = re.compile(r"[\t\f\v ]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(value: str | None) -> str:
    """Normalize Unicode, newlines and incidental whitespace without losing paragraphs."""

    if not value:
        return ""
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    return _EXCESS_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def canonicalize_url(value: str | None) -> str:
    """Return a stable public HTTP(S) URL with tracking parameters removed."""

    if not value:
        return ""
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return raw

    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS
        and not any(key.lower().startswith(prefix) for prefix in _TRACKING_PREFIXES)
    ]
    query.sort()
    return urlunsplit((scheme, host, path, urlencode(query, doseq=True), ""))
