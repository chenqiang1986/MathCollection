"""Shared result type returned by the worker's independent stages (image
scan, batch classify) — kept out of `orchestrator.py` so `classifier.py`
can return it without an import cycle."""

from datetime import datetime
from typing import NamedTuple

from common import storage


class StageResult(NamedTuple):
    # Number of records produced (partials saved by scan, problems updated
    # by classify). `complete` is False when we know some intended records
    # were not persisted (a tool error, a swallowed failure, a parse
    # abort) so the caller should revert and retry instead of advancing.
    saved: list[storage.Problem]
    complete: bool
    summary: str
    # `hit_quota_limit` is True if the SDK saw a rejected `RateLimitEvent`
    # in this stage. `quota_reset_at` is the furthest-out reset timestamp
    # we saw (UTC).
    hit_quota_limit: bool = False
    quota_reset_at: datetime | None = None
