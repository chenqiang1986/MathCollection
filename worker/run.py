"""Main loop for the offline worker.

Scans every user directory under `data/`. For each user, drains both
queue stages — image_scan first (so newly uploaded files turn into
partials quickly), then problem_solve (so the solver backlog catches
up). Each claimed row runs through one stage and is either advanced,
reverted, or failed.

If a run reports `hit_quota_limit=True`, the row is reverted to its
pending state (quota isn't the file's fault, so it doesn't count toward
`MAX_ATTEMPTS`) and the whole scan loop sleeps until the reported reset
timestamp before trying again.
"""

import time
from datetime import datetime, timezone

from common import storage
from common.db_setup.setup import init_user

from worker import agent

IDLE_SLEEP_SECONDS = 60
# Cap how many times a single stage can be reverted-and-retried on a
# partial result. After this many `claim_next_*` cycles produce an
# incomplete result, give up and mark the file failed so a deterministic
# per-problem error doesn't loop forever.
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


def _run_problem_solve(item: storage.QueueItem) -> agent.StageResult:
    return agent.solve_pending_problems(
        source_image=item.filename, with_solution=item.with_solution
    )


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


def _drain_user(email: str) -> agent.StageResult | None:
    """Claim and process at most one pending row for this user — image
    scan first, then problem solve. Returns the run's `StageResult` if
    work was done, or `None` if there was nothing pending. On a quota hit
    the row is reverted to pending; the caller is expected to sleep until
    `result.quota_reset_at`."""
    token = storage.set_current_user(email)
    try:
        init_user()

        item = storage.claim_next_image_scan()
        if item is not None:
            print(
                f"[worker] {email} scan {item.filename} "
                f"(attempt {item.attempts}, with_solution={item.with_solution})",
                flush=True,
            )
            return _drive_stage(
                item,
                stage="image_scan",
                run_fn=_run_image_scan,
                advance_fn=storage.advance_to_problem_solve,
                revert_fn=storage.revert_image_scan,
            )

        item = storage.claim_next_problem_solve()
        if item is not None:
            print(
                f"[worker] {email} solve {item.filename} "
                f"(attempt {item.attempts}, with_solution={item.with_solution})",
                flush=True,
            )
            return _drive_stage(
                item,
                stage="problem_solve",
                run_fn=_run_problem_solve,
                advance_fn=storage.mark_done,
                revert_fn=storage.revert_problem_solve,
            )
        return None
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
        storage.mark_failed(item.filename, error=f"{stage}: {exc!r}")
        print(
            f"[worker] FAILED {item.filename} in {stage}: {exc!r}",
            flush=True,
        )
        return agent.StageResult(
            saved=[], complete=False, summary=f"error: {exc!r}"
        )
    _handle_result(
        item, stage, result, advance_fn=advance_fn, revert_fn=revert_fn
    )
    return result


def _reclaim_all_stale() -> None:
    """At startup, flip any in-flight `processing_*` rows from a prior
    killed run back to the matching `pending_*` so they get retried."""
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
    """Drain every user's queue once and exit. Returns the count of
    stage results processed. Used by `--once` for testing."""
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
