"""Detect Claude rate-limit / quota errors and figure out how long to wait
before retrying. Best-effort: the `claude-agent-sdk` wraps the underlying
Anthropic exceptions in ways we can't always introspect, so we inspect a
mix of exception class names, attributes, and the stringified message.

Also exposes `probe_quota()`, which makes a minimal `messages.create` call
purely to read fresh `anthropic-ratelimit-*` response headers. The worker
calls this between files to decide whether to pause — necessary because
the orchestrator swallows per-problem/per-file exceptions internally, so a
quota error inside a file never bubbles up on its own."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

from common.agent_util import MODEL

# Substrings in the exception class name or str(exc) that flag rate limiting.
_RATE_LIMIT_MARKERS = (
    "ratelimit",
    "rate_limit",
    "rate limit",
    "429",
    "too many requests",
    "quota",
    "usage limit",
    "overloaded",
)

# Reset header keys we may see embedded in error messages.
_RESET_HEADER_KEYS = (
    "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-tokens-reset",
    "anthropic-ratelimit-input-tokens-reset",
    "anthropic-ratelimit-output-tokens-reset",
)

# Default fallback: when we know it's a rate limit but can't extract a
# reset time, wait this many seconds. Matches the user's "hourly quota"
# expectation.
DEFAULT_QUOTA_SLEEP_SECONDS = 60 * 60


@dataclass
class QuotaSignal:
    is_quota: bool
    sleep_seconds: int
    detail: str


def classify_error(exc: BaseException) -> QuotaSignal:
    """Return a QuotaSignal saying whether this exception is a rate-limit
    block and, if so, how long to wait before resuming."""
    class_name = type(exc).__name__.lower()
    text = str(exc)
    haystack = f"{class_name} {text}".lower()

    if not any(marker in haystack for marker in _RATE_LIMIT_MARKERS):
        return QuotaSignal(is_quota=False, sleep_seconds=0, detail="")

    sleep = _extract_retry_after(text)
    if sleep is None:
        sleep = _extract_reset_timestamp(text)
    if sleep is None:
        sleep = DEFAULT_QUOTA_SLEEP_SECONDS
        detail = f"rate limit hit; no reset hint, waiting {sleep}s"
    else:
        detail = f"rate limit hit; waiting {sleep}s for reset"
    return QuotaSignal(is_quota=True, sleep_seconds=sleep, detail=detail)


def _extract_retry_after(text: str) -> int | None:
    """Look for a `retry-after: NN` style hint (seconds)."""
    m = re.search(r"retry[-_ ]after['\"\s:=]+([0-9]+)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return max(1, int(m.group(1)))
    except ValueError:
        return None


# Each "remaining" header has a matching "reset" header (ISO 8601 UTC).
_REMAINING_TO_RESET = {
    "anthropic-ratelimit-requests-remaining": "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-tokens-remaining": "anthropic-ratelimit-tokens-reset",
    "anthropic-ratelimit-input-tokens-remaining": "anthropic-ratelimit-input-tokens-reset",
    "anthropic-ratelimit-output-tokens-remaining": "anthropic-ratelimit-output-tokens-reset",
}


def probe_quota() -> QuotaSignal:
    """Make a 1-token `messages.create` call to read live rate-limit
    headers. Returns a blocking signal if we're throttled (probe 429'd)
    or if any `*-remaining` header is 0. Returns a no-op signal on
    success-with-headroom, or on transient/non-quota errors (we don't
    want a network blip to halt the worker)."""
    try:
        client = anthropic.Anthropic()
        raw = client.messages.with_raw_response.create(
            model=MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "."}],
        )
    except anthropic.RateLimitError as exc:
        return classify_error(exc)
    except Exception as exc:
        print(f"[quota] probe failed (non-quota): {exc!r}", flush=True)
        return QuotaSignal(is_quota=False, sleep_seconds=0, detail="")
    return _signal_from_headers(raw.headers)


def _signal_from_headers(headers) -> QuotaSignal:
    for remaining_key, reset_key in _REMAINING_TO_RESET.items():
        raw_val = headers.get(remaining_key)
        if raw_val is None:
            continue
        try:
            remaining = int(raw_val)
        except (TypeError, ValueError):
            continue
        if remaining > 0:
            continue
        sleep = _seconds_until_reset(headers.get(reset_key))
        return QuotaSignal(
            is_quota=True,
            sleep_seconds=sleep,
            detail=f"{remaining_key}=0; sleeping {sleep}s for {reset_key}",
        )
    return QuotaSignal(is_quota=False, sleep_seconds=0, detail="")


def _seconds_until_reset(ts_str: str | None) -> int:
    if not ts_str:
        return DEFAULT_QUOTA_SLEEP_SECONDS
    ts_raw = ts_str.rstrip("Z")
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return DEFAULT_QUOTA_SLEEP_SECONDS
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = (ts - datetime.now(timezone.utc)).total_seconds()
    return max(60, int(delta) + 5)


def _extract_reset_timestamp(text: str) -> int | None:
    """Look for an `anthropic-ratelimit-*-reset: 2026-05-18T13:00:00Z` style
    hint and return seconds-from-now to that timestamp."""
    for key in _RESET_HEADER_KEYS:
        m = re.search(
            rf"{re.escape(key)}['\"\s:=]+([0-9T\-:Z+\.]+)",
            text,
            re.IGNORECASE,
        )
        if not m:
            continue
        ts_raw = m.group(1).rstrip("Z")
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        delta = (ts - datetime.now(timezone.utc)).total_seconds()
        if delta > 0:
            return int(delta) + 5
    return None
