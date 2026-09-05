"""Pure channel-health state transitions for storage by the caller."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ChannelHealth:
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    consecutive_failures: int = 0
    error_code: str | None = None
    error_summary: str | None = None

    @property
    def state(self) -> str:
        if self.consecutive_failures == 0:
            return "healthy" if self.last_success_at else "unknown"
        if self.consecutive_failures >= 3:
            return "failing"
        return "degraded"

    def success(self, *, at: str | None = None) -> "ChannelHealth":
        instant = at or _now()
        return replace(
            self,
            last_checked_at=instant,
            last_success_at=instant,
            consecutive_failures=0,
            error_code=None,
            error_summary=None,
        )

    def failure(self, error: Exception, *, code: str | None = None, at: str | None = None) -> "ChannelHealth":
        instant = at or _now()
        return replace(
            self,
            last_checked_at=instant,
            last_error_at=instant,
            consecutive_failures=self.consecutive_failures + 1,
            error_code=code or type(error).__name__,
            error_summary=str(error)[:500],
        )
