"""Bearer-token providers. Interactive OAuth remains outside unattended runs."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol


class TokenProvider(Protocol):
    def get_token(self) -> str: ...


@dataclass(frozen=True, slots=True)
class StaticTokenProvider:
    token: str

    def get_token(self) -> str:
        if not self.token:
            raise RuntimeError("MCP bearer token is empty")
        return self.token


@dataclass(frozen=True, slots=True)
class EnvironmentTokenProvider:
    variable: str = "AISA_MCP_TOKEN"

    def get_token(self) -> str:
        token = os.environ.get(self.variable, "").strip()
        if not token:
            raise RuntimeError(f"MCP bearer token is unavailable in {self.variable}")
        return token


class RefreshingOAuthTokenProvider:
    """Thread-safe adapter for a host-managed OAuth refresh callback.

    The callback returns ``(access_token, expires_at_unix)``. Credentials are never
    serialized by this package; Codex/Multica or a Keychain helper owns them.
    """

    def __init__(self, refresh: Callable[[], tuple[str, float]], *, skew_seconds: int = 60):
        self._refresh = refresh
        self._skew = skew_seconds
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get_token(self) -> str:
        with self._lock:
            if not self._token or time.time() >= self._expires_at - self._skew:
                token, expires_at = self._refresh()
                if not token:
                    raise RuntimeError("OAuth refresh returned an empty token")
                self._token, self._expires_at = token, expires_at
            return self._token
