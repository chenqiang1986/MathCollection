"""Main loop for the offline worker.

Scans every user directory under `data/`. For each user with a pending
raw file, claims the oldest one, runs the agent on it, and marks the
row done or failed. Between files, sleeps `IDLE_SLEEP_SECONDS` then
rescans.

Quota handling is out-of-band: after each file we probe Anthropic's
rate-limit headers (`quota.probe_quota`) and sleep until reset if any
dimension is exhausted. This is deliberate — the orchestrator swallows
per-problem/per-file exceptions internally, so a 429 never bubbles up
to `_process_one`, so we couldn't classify it inline even if we tried.
"""

import time
from pathlib import Path

from common import storage
from common.db_setup.setup import init_user

from . import agent
from .quota import probe_quota

IDLE_SLEEP_SECONDS = 60


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


def _process_one(filename: str, with_solution: bool) -> None:
    raw_path = storage.raw_upload_path(filename)
    if not raw_path.exists():
        raise FileNotFoundError(f"raw file missing on disk: {raw_path}")
    agent.process_image(
        raw_path,
        source_image=filename,
        with_solution=with_solution,
    )


def _drain_user(email: str) -> bool:
    """Claim and process at most one pending row for this user. Returns
    True if we did work, False if there was nothing pending. Quota
    blocking is handled by the caller via `probe_quota()` after each
    file, not here — see module docstring."""
    token = storage.set_current_user(email)
    try:
        init_user()
        item = storage.claim_next()
        if item is None:
            return False
        print(
            f"[worker] {email} processing {item.filename} "
            f"(attempt {item.attempts}, with_solution={item.with_solution})",
            flush=True,
        )
        try:
            _process_one(item.filename, item.with_solution)
        except Exception as exc:
            storage.mark_failed(item.filename, error=repr(exc))
            print(
                f"[worker] {email} FAILED {item.filename}: {exc!r}",
                flush=True,
            )
            return True
        storage.mark_done(item.filename)
        print(f"[worker] {email} done {item.filename}", flush=True)
        return True
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
        quota_blocked = False
        for email in _iter_user_emails():
            try:
                if not _drain_user(email):
                    continue
                did_work = True
            except Exception as exc:
                print(
                    f"[worker] {email} unexpected error draining: {exc!r}",
                    flush=True,
                )
                continue
            signal = probe_quota()
            if signal.is_quota:
                print(
                    f"[worker] quota blocked: {signal.detail}",
                    flush=True,
                )
                time.sleep(signal.sleep_seconds)
                # After waking, restart the user scan so newly-pending
                # work elsewhere gets a fair shot.
                quota_blocked = True
                break
        if quota_blocked:
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
            did = _drain_user(email)
            if not did:
                break
            processed += 1
            signal = probe_quota()
            if signal.is_quota:
                print(
                    f"[worker] quota blocked during --once: {signal.detail}; "
                    "aborting drain",
                    flush=True,
                )
                return processed
    return processed
