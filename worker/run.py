"""Main loop for the offline worker.

Scans every user directory under `data/`. For each user with a pending
raw file, claims the oldest one, runs the agent on it, and marks the
row done or failed. Between files, sleeps `IDLE_SLEEP_SECONDS` then
rescans.

If a run reports `hit_quota_limit=True`, the file is reverted to pending
(quota isn't the file's fault, so it doesn't count toward `MAX_ATTEMPTS`)
and the whole scan loop sleeps until the reported reset timestamp before
trying again.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from common import storage
from common.db_setup.setup import init_user

from . import agent

IDLE_SLEEP_SECONDS = 60
# Cap how many times a file can be reverted-and-retried on partial saves.
# After this many `claim_next` cycles produce an incomplete result, we
# give up and mark it failed so a deterministic per-problem error doesn't
# loop forever.
MAX_ATTEMPTS = 3
# Fallback wait when a quota hit is reported with no `resets_at`.
DEFAULT_QUOTA_SLEEP_SECONDS = 60 * 60


def _seconds_until(reset_at: datetime | None) -> int:
    if reset_at is None:
        return DEFAULT_QUOTA_SLEEP_SECONDS
    delta = (reset_at - datetime.now(timezone.utc)).total_seconds()
    # Clamp: don't busy-loop if the timestamp is already past, and add a
    # small safety pad to avoid waking just before the reset lands.
    return max(60, int(delta) + 5)


def _iter_user_emails() -> list[str]:
    """Return the sanitized-email dir names under data/. Each is a slug
    suitable to pass to `storage.set_current_user` (sanitize_email is
    idempotent on already-safe input)."""
    if not storage.DATA_DIR.exists():
        return []
    emails: list[str] = []
    for child in sorted(storage.DATA_DIR.iterdir()):
        if not child.is_dir():
            continue
        emails.append(child.name)
    return emails


def _process_one(
    filename: str, with_solution: bool
) -> agent.ProcessImageResult:
    raw_path = storage.raw_upload_path(filename)
    if not raw_path.exists():
        raise FileNotFoundError(f"raw file missing on disk: {raw_path}")
    return agent.process_image(
        raw_path,
        source_image=filename,
        with_solution=with_solution,
    )


def _drain_user(email: str) -> agent.ProcessImageResult | None:
    """Claim and process at most one pending row for this user. Returns
    the run's `ProcessImageResult` if work was done, or `None` if there
    was nothing pending. On a quota hit the file is reverted to pending
    without consulting `MAX_ATTEMPTS` — the caller is expected to sleep
    until `result.quota_reset_at`."""
    token = storage.set_current_user(email)
    try:
        init_user()
        item = storage.claim_next()
        if item is None:
            return None
        print(
            f"[worker] {email} processing {item.filename} "
            f"(attempt {item.attempts}, with_solution={item.with_solution})",
            flush=True,
        )
        try:
            result = _process_one(item.filename, item.with_solution)
        except Exception as exc:
            storage.mark_failed(item.filename, error=repr(exc))
            print(
                f"[worker] {email} FAILED {item.filename}: {exc!r}",
                flush=True,
            )
            return agent.ProcessImageResult(
                saved=[], complete=False, summary=f"error: {exc!r}"
            )
        if result.hit_quota_limit:
            storage.revert_to_pending(
                item.filename,
                error=(
                    f"quota hit; will retry after "
                    f"{result.quota_reset_at}: {result.summary}"
                ),
            )
            print(
                f"[worker] {email} QUOTA HIT on {item.filename}; reverting "
                f"to pending (resets_at={result.quota_reset_at})",
                flush=True,
            )
        elif result.complete:
            storage.mark_done(item.filename)
            print(
                f"[worker] {email} done {item.filename}: {result.summary}",
                flush=True,
            )
        elif item.attempts >= MAX_ATTEMPTS:
            storage.mark_failed(
                item.filename,
                error=(
                    f"incomplete after {item.attempts} attempts: "
                    f"{result.summary}"
                ),
            )
            print(
                f"[worker] {email} GIVING UP on {item.filename} "
                f"after {item.attempts} attempts: {result.summary}",
                flush=True,
            )
        else:
            storage.revert_to_pending(
                item.filename,
                error=(
                    f"partial save on attempt {item.attempts}; will retry: "
                    f"{result.summary}"
                ),
            )
            print(
                f"[worker] {email} INCOMPLETE {item.filename} "
                f"(attempt {item.attempts}/{MAX_ATTEMPTS}), reverting to "
                f"pending: {result.summary}",
                flush=True,
            )
        return result
    finally:
        storage.reset_current_user(token)


def _reclaim_all_stale() -> None:
    """At startup, flip any `processing` rows from a prior killed run back
    to `pending` so they get retried."""
    for email in _iter_user_emails():
        token = storage.set_current_user(email)
        try:
            init_user()
            n = storage.reclaim_stale_processing()
            if n:
                print(
                    f"[worker] {email}: reclaimed {n} stale processing row(s)",
                    flush=True,
                )
        finally:
            storage.reset_current_user(token)


def run_forever() -> None:
    print("[worker] starting; data dir =", storage.DATA_DIR, flush=True)
    _reclaim_all_stale()
    while True:
        did_work = False
        quota_reset_at: datetime | None = None
        for email in _iter_user_emails():
            try:
                result = _drain_user(email)
            except Exception as exc:
                print(
                    f"[worker] {email} unexpected error draining: {exc!r}",
                    flush=True,
                )
                continue
            if result is None:
                continue
            did_work = True
            if result.hit_quota_limit:
                # Quota is global per account, not per-user — no point
                # draining the next user, they'll just hit it too.
                quota_reset_at = result.quota_reset_at
                break
        if quota_reset_at is not None:
            sleep_s = _seconds_until(quota_reset_at)
            print(
                f"[worker] quota blocked; sleeping {sleep_s}s "
                f"(resets_at={quota_reset_at})",
                flush=True,
            )
            time.sleep(sleep_s)
            continue
        if did_work:
            print(
                f"[worker] sleeping {IDLE_SLEEP_SECONDS}s before next scan",
                flush=True,
            )
        time.sleep(IDLE_SLEEP_SECONDS)


def run_once() -> int:
    """Drain every user's queue once and exit. Returns the count of files
    processed. Used by `--once` for testing."""
    processed = 0
    for email in _iter_user_emails():
        while True:
            result = _drain_user(email)
            if result is None:
                break
            processed += 1
            if result.hit_quota_limit:
                print(
                    f"[worker] quota hit during --once "
                    f"(resets_at={result.quota_reset_at}); aborting drain",
                    flush=True,
                )
                return processed
    return processed
