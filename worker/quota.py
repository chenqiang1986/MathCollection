"""Detect rate-limit signals in the `claude-agent-sdk` message stream.

The SDK yields a `RateLimitEvent` message whenever the upstream service
reports rate-limit state. Status `"rejected"` means the request was
blocked — that is our quota signal, and `resets_at` (unix epoch seconds)
tells us when to retry. `"allowed_warning"` is informational only; we
let it pass.
"""

from datetime import datetime, timezone

from claude_agent_sdk import RateLimitEvent


class QuotaHit(Exception):
    """Raised when the agent run was blocked by a rate / quota limit."""

    def __init__(self, reset_at: datetime | None, detail: str):
        super().__init__(detail)
        self.reset_at = reset_at
        self.detail = detail


def detect_in_message(message) -> QuotaHit | None:
    """Return a `QuotaHit` if `message` is a rejected `RateLimitEvent`,
    else `None`. Caller is responsible for raising it (or aggregating)."""
    if not isinstance(message, RateLimitEvent):
        return None
    info = message.rate_limit_info
    if info.status != "rejected":
        return None
    reset_at = (
        datetime.fromtimestamp(info.resets_at, tz=timezone.utc)
        if info.resets_at
        else None
    )
    detail = (
        f"rate limit rejected (type={info.rate_limit_type}, "
        f"utilization={info.utilization}, resets_at={reset_at})"
    )
    return QuotaHit(reset_at=reset_at, detail=detail)


def later_reset(
    a: datetime | None, b: datetime | None
) -> datetime | None:
    """Return whichever reset timestamp is further in the future."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a > b else b
