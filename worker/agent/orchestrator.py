"""Two independent worker jobs.

Image scan — `scan_image`: one LLM call extracts every problem out of the
source image/PDF and persists each as a partial record (category
`unclassified`, no solution) via `build_scan_store`. Tracked per file in
`raw_files` (`common.storage.queue`).

Batch classify — `classify_pending_problems`: claims a flat backlog of
`unclassified` problems (regardless of source file, see
`common.storage.classify_tasks`), fans them out to a bounded number of
concurrent batch-classify sessions (`worker.agent.classifier.classify_batch`),
and updates each problem's category/subcategory in place.

The runner in `worker/run.py` drives image scan as a queue-row transition
(so a crash can't lose scan work) and classify as one bounded sweep of the
backlog each loop pass, so quota hits in one job don't roll back the other.
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from common import storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, log_message

from worker.quota import detect_in_message as detect_quota_in_message, later_reset
from worker.agent.classifier import classify_batch
from worker.agent.problem_store import UNCLASSIFIED_CATEGORY, build_scan_store
from worker.agent.results import StageResult

ORCHESTRATOR_MAX_TURNS = 20

# Batch-classify tunables. One call to `classify_pending_problems` claims
# and processes at most CLASSIFY_CONCURRENCY * CLASSIFY_BATCH_SIZE
# problems, so a single user's backlog can't monopolize the worker's
# per-round fairness across users (see worker/AGENTS.md).
CLASSIFY_BATCH_SIZE = 15
CLASSIFY_CONCURRENCY = 3
# Retry budget per problem, independent of the file-scan retry budget.
CLASSIFY_MAX_ATTEMPTS = 3
# Sentinel summary `classify_pending_problems` returns when there was no
# backlog at all — lets `worker/run.py` distinguish "ran and did nothing"
# from "found and processed some problems" without a real claim.
NO_CLASSIFY_WORK_SUMMARY = "No problems to classify."

# Orchestrator prompt is worker-local — only this orchestrator reads it.
WORKER_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
ORCHESTRATOR_SYSTEM_PROMPT = (WORKER_PROMPTS_DIR / "orchestrator.md").read_text()


async def _scan_image_async(
    image_path: Path,
    source_image: str | None,
) -> StageResult:
    saved: list[storage.Problem] = []
    server = build_scan_store(source_image, saved)
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        mcp_servers={"problem_store": server},
        allowed_tools=[
            "Read",
            "mcp__problem_store__list_subexams",
            "mcp__problem_store__save_parsed_problem",
        ],
        max_turns=ORCHESTRATOR_MAX_TURNS,
        max_buffer_size=MAX_BUFFER_SIZE,
    )
    prompt = (
        f"Read the file at {image_path} (image or PDF). Extract every "
        "distinct math problem and call "
        "`mcp__problem_store__save_parsed_problem` once for each problem, "
        "in source order, with its full per-problem metadata."
    )

    print("[scan_image] start", flush=True)
    quota = None
    async for message in query(prompt=prompt, options=options):
        log_message(message)
        q = detect_quota_in_message(message)
        if q is not None:
            print(f"[scan_image] quota hit during scan: {q.detail}", flush=True)
            quota = q
            break
    print(f"[scan_image] saved {len(saved)} partial problem(s)", flush=True)

    if quota is not None:
        return StageResult(
            saved=saved,
            complete=False,
            summary=(
                f"Saved {len(saved)} partial(s) before quota hit: {quota.detail}"
            ),
            hit_quota_limit=True,
            quota_reset_at=quota.reset_at,
        )
    return StageResult(
        saved=saved,
        complete=True,
        summary=f"Scan saved {len(saved)} partial problem(s).",
    )


def scan_image(
    image_path: Path,
    source_image: str | None = None,
) -> StageResult:
    """Persists each extracted problem as a partial record
    (category=`unclassified`)."""
    return asyncio.run(
        _scan_image_async(Path(image_path), source_image)
    )


async def _classify_pending_async(
    batch_size: int,
    concurrency: int,
) -> StageResult:
    storage.classify_tasks.seed_pending()

    claimed_batches: list[list[storage.ClassifyTask]] = []
    for _ in range(concurrency):
        chunk = storage.classify_tasks.claim_batch(batch_size)
        if not chunk:
            break
        claimed_batches.append(chunk)
    if not claimed_batches:
        return StageResult(saved=[], complete=True, summary=NO_CLASSIFY_WORK_SUMMARY)

    attempts_by_id = {
        task.problem_id: task.attempts
        for batch in claimed_batches
        for task in batch
    }

    async def _run_batch(batch: list[storage.ClassifyTask]) -> StageResult:
        try:
            return await classify_batch([t.problem_id for t in batch])
        except Exception as exc:
            print(f"[classifier] ERROR: {exc!r}", flush=True)
            return StageResult(saved=[], complete=False, summary=f"error: {exc!r}")

    results = await asyncio.gather(
        *(_run_batch(batch) for batch in claimed_batches)
    )

    for batch, result in zip(claimed_batches, results):
        saved_ids = {p.id for p in result.saved}
        for problem_id in saved_ids:
            storage.classify_tasks.mark_done(problem_id)
        for task in batch:
            if task.problem_id in saved_ids:
                continue
            if result.hit_quota_limit:
                storage.classify_tasks.revert_to_pending(
                    task.problem_id,
                    error=(
                        f"quota hit; will retry after "
                        f"{result.quota_reset_at}: {result.summary}"
                    ),
                )
            elif attempts_by_id[task.problem_id] >= CLASSIFY_MAX_ATTEMPTS:
                storage.classify_tasks.mark_failed(
                    task.problem_id,
                    error=(
                        f"incomplete after {attempts_by_id[task.problem_id]} "
                        f"attempts: {result.summary}"
                    ),
                )
            else:
                storage.classify_tasks.revert_to_pending(
                    task.problem_id,
                    error=(
                        f"partial result on attempt "
                        f"{attempts_by_id[task.problem_id]}; will retry: "
                        f"{result.summary}"
                    ),
                )

    saved = [p for r in results for p in r.saved]
    hit_quota_limit = any(r.hit_quota_limit for r in results)
    quota_reset_at = None
    for r in results:
        quota_reset_at = later_reset(quota_reset_at, r.quota_reset_at)
    complete = all(r.complete for r in results)
    summary = (
        f"Classified {len(saved)} of {len(attempts_by_id)} problem(s) "
        f"across {len(results)} batch(es)."
    )
    if hit_quota_limit:
        summary += f" (quota hit; resets_at={quota_reset_at})"
    return StageResult(
        saved=saved,
        complete=complete,
        summary=summary,
        hit_quota_limit=hit_quota_limit,
        quota_reset_at=quota_reset_at,
    )


def classify_pending_problems(
    batch_size: int = CLASSIFY_BATCH_SIZE,
    concurrency: int = CLASSIFY_CONCURRENCY,
) -> StageResult:
    """Claim up to `concurrency * batch_size` `unclassified` problems
    (regardless of source file) and classify them in `concurrency`
    concurrent batch sessions. Bookkeeping (done/retry/failed) is applied
    directly to `classify_tasks` per problem, independent of any
    `raw_files` row."""
    return asyncio.run(_classify_pending_async(batch_size, concurrency))
