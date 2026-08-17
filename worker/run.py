"""Main loop for the offline worker.

Scans every user directory under `data/`. For each user, does at most one
unit of image-scan work (claims and drives the oldest `pending_image_scan`
row) and one round of batch-classify work (`agent.classify_pending_problems`,
a flat sweep over unclassified problems bounded to
`CLASSIFY_CONCURRENCY * CLASSIFY_BATCH_SIZE` problems per call — see
`worker/agent/orchestrator.py`) — then moves to the next user.

If a run reports `hit_quota_limit=True`, image-scan rows are reverted to
`pending_image_scan` (quota isn't the file's fault, so it doesn't count
toward `MAX_ATTEMPTS`); classify quota hits are handled internally by
`classify_pending_problems` (each unsaved problem is reverted to
`pending`). Either way the whole loop sleeps until the reported reset
timestamp before trying again.
"""

import time
from datetime import datetime, timezone

from common import storage
from common.db_setup.setup import init_user

from worker import agent
from worker.quota import later_reset

IDLE_SLEEP_SECONDS = 60
# Cap how many times image scan can be reverted-and-retried on a partial
# result. After this many `claim_next_image_scan` cycles produce an
# incomplete result, give up and mark the file failed so a deterministic
# per-problem error doesn't loop forever. (Classify has its own retry
# budget, `CLASSIFY_MAX_ATTEMPTS` in orchestrator.py, applied per problem.)
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


def _run_image_scan(item: storage.QueueItem) -> agent.StageResult:
    raw_path = storage.raw_upload_path(item.filename)
    if not raw_path.exists():
        raise FileNotFoundError(f"raw file missing on disk: {raw_path}")
    return agent.scan_image(raw_path, source_image=item.filename)


def _handle_result(
    item: storage.QueueItem,
    stage: str,
    result: agent.StageResult,
    *,
    advance_fn,
    revert_fn,
) -> None:
    """Apply the queue transition appropriate to `result`. `advance_fn`
    moves to the next state on success; `revert_fn` puts the row back to
    its pending state on quota / partial result."""
    if result.hit_quota_limit:
        revert_fn(
            item.filename,
            error=(
                f"{stage}: quota hit; will retry after "
                f"{result.quota_reset_at}: {result.summary}"
            ),
        )
        print(
            f"[worker] QUOTA HIT on {item.filename} during {stage}; "
            f"reverting (resets_at={result.quota_reset_at})",
            flush=True,
        )
    elif result.complete:
        advance_fn(item.filename)
        print(
            f"[worker] {stage} OK on {item.filename}: {result.summary}",
            flush=True,
        )
    elif item.attempts >= MAX_ATTEMPTS:
        storage.mark_failed(
            item.filename,
            error=(
                f"{stage}: incomplete after {item.attempts} attempts: "
                f"{result.summary}"
            ),
        )
        print(
            f"[worker] GIVING UP on {item.filename} after {item.attempts} "
            f"attempts in {stage}: {result.summary}",
            flush=True,
        )
    else:
        revert_fn(
            item.filename,
            error=(
                f"{stage}: partial result on attempt {item.attempts}; "
                f"will retry: {result.summary}"
            ),
        )
        print(
            f"[worker] INCOMPLETE {item.filename} in {stage} "
            f"(attempt {item.attempts}/{MAX_ATTEMPTS}), reverting: "
            f"{result.summary}",
            flush=True,
        )


def _drain_user(email: str) -> list[agent.StageResult]:
    """Do at most one unit of image-scan work and one round of
    batch-classify work for this user. Returns the StageResult(s)
    produced; empty if there was nothing to do in either job."""
    token = storage.set_current_user(email)
    try:
        init_user()
        results: list[agent.StageResult] = []

        item = storage.claim_next_image_scan()
        if item is not None:
            print(
                f"[worker] {email} scan {item.filename} "
                f"(attempt {item.attempts})",
                flush=True,
            )
            results.append(
                _drive_stage(
                    item,
                    stage="image_scan",
                    run_fn=_run_image_scan,
                    advance_fn=storage.mark_done,
                    revert_fn=storage.revert_image_scan,
                )
            )

        classify_result = agent.classify_pending_problems()
        if classify_result.summary != agent.NO_CLASSIFY_WORK_SUMMARY:
            print(
                f"[worker] {email} classify: {classify_result.summary}",
                flush=True,
            )
            results.append(classify_result)

        return results
    finally:
        storage.reset_current_user(token)


def _drive_stage(
    item: storage.QueueItem,
    *,
    stage: str,
    run_fn,
    advance_fn,
    revert_fn,
) -> agent.StageResult:
    try:
        result = run_fn(item)
    except Exception as exc:
        print(
            f"[worker] ERROR {item.filename} in {stage}: {exc!r}",
            flush=True,
        )
        result = agent.StageResult(
            saved=[], complete=False, summary=f"error: {exc!r}"
        )
    _handle_result(
        item, stage, result, advance_fn=advance_fn, revert_fn=revert_fn
    )
    return result


def _reclaim_all_stale() -> None:
    """At startup, flip any in-flight `processing_image_scan` rows and any
    `processing` classify_tasks rows from a prior killed run back to
    pending so they get retried."""
    for email in _iter_user_emails():
        token = storage.set_current_user(email)
        try:
            init_user()
            n = storage.reclaim_stale_processing()
            m = storage.classify_tasks.reclaim_stale_processing()
            if n or m:
                print(
                    f"[worker] {email}: reclaimed {n} stale scan row(s), "
                    f"{m} stale classify task(s)",
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
                results = _drain_user(email)
            except Exception as exc:
                print(
                    f"[worker] {email} unexpected error draining: {exc!r}",
                    flush=True,
                )
                continue
            if not results:
                continue
            did_work = True
            quota_results = [r for r in results if r.hit_quota_limit]
            if quota_results:
                # Quota is global per account, not per-user — no point
                # draining the next user, they'll just hit it too.
                for r in quota_results:
                    quota_reset_at = later_reset(quota_reset_at, r.quota_reset_at)
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
    """Drain every user's queue once and exit. Returns the count of
    stage results processed. Used by `--once` for testing."""
    processed = 0
    for email in _iter_user_emails():
        while True:
            results = _drain_user(email)
            if not results:
                break
            processed += len(results)
            if any(r.hit_quota_limit for r in results):
                print(
                    f"[worker] quota hit during --once "
                    f"(user={email}); aborting drain",
                    flush=True,
                )
                return processed
    return processed
