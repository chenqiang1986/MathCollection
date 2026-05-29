"""Two-stage agent pipeline.

Stage 1 — `scan_image`: one LLM call extracts every problem out of the
source image/PDF and persists each as a partial record (category
`unclassified`, no solution) via `build_problem_store(mode="parsed")`.

Stage 2 — `solve_pending_problems`: looks up the partials saved for a
given source_image and fans them out to the solver with bounded
concurrency. Each solver classifies/solves one problem and updates its
partial record in place via `build_problem_store(mode="solved")`.

The runner in `worker/run.py` drives the two stages as separate queue
transitions so a long solver pass can't lose the parse work, and so
quota hits in one stage don't roll back the other.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import ClaudeAgentOptions, query
from common import storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, log_message

from worker.quota import QuotaHit, detect_in_message as detect_quota_in_message, later_reset
from worker.agent.problem_store import UNCLASSIFIED_CATEGORY, build_problem_store
from worker.agent.solver import solve_problem

ORCHESTRATOR_MAX_TURNS = 20
# Concurrency cap for solver fan-out. Each inner solver is its own SDK
# session, so this also caps simultaneous API calls — keep modest to stay
# under the five-hour rate limit on Sonnet.
SOLVER_CONCURRENCY = 4

# Orchestrator prompt is worker-local — only this orchestrator reads it.
WORKER_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
ORCHESTRATOR_SYSTEM_PROMPT = (WORKER_PROMPTS_DIR / "orchestrator.md").read_text()


class StageResult(NamedTuple):
    # Number of records produced (partials saved by scan, problems updated
    # by solve). `complete` is False when we know some intended records
    # were not persisted (a tool error, a swallowed solver failure, a
    # parse abort) so the runner should revert and retry instead of
    # advancing the file.
    saved: list[storage.Problem]
    complete: bool
    summary: str
    # `hit_quota_limit` is True if the SDK saw a rejected `RateLimitEvent`
    # in this stage. `quota_reset_at` is the furthest-out reset timestamp
    # we saw (UTC). The runner uses these to sleep until the quota window
    # opens up before its next scan.
    hit_quota_limit: bool = False
    quota_reset_at: datetime | None = None


# Backwards-compatible alias: callers outside the worker package import
# `ProcessImageResult` from `worker.agent`. Both stages now report through
# the same shape.
ProcessImageResult = StageResult


async def _scan_image_async(
    image_path: Path,
    source_image: str | None,
) -> StageResult:
    saved: list[storage.Problem] = []
    server = build_problem_store(source_image, saved, mode="parsed")
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
    quota: QuotaHit | None = None
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
    """Stage 1. Persists each extracted problem as a partial record
    (category=`unclassified`)."""
    return asyncio.run(
        _scan_image_async(Path(image_path), source_image)
    )


async def _solve_pending_async(
    source_image: str,
    with_solution: bool,
) -> StageResult:
    partial_ids = storage.problems_by_source_and_category(
        source_image, UNCLASSIFIED_CATEGORY
    )
    if not partial_ids:
        return StageResult(
            saved=[],
            complete=True,
            summary="No partials to solve.",
        )

    expected = len(partial_ids)
    sem = asyncio.Semaphore(SOLVER_CONCURRENCY)

    async def run_one(
        problem_id: str,
    ) -> tuple[storage.Problem | None, QuotaHit | None]:
        partial = storage.get_problem(problem_id)
        if partial is None:
            return None, None
        async with sem:
            try:
                problem = await solve_problem(
                    partial, with_solution=with_solution
                )
                return problem, None
            except QuotaHit as q:
                print(
                    f"[solve_pending] solver quota hit for {problem_id}: "
                    f"{q.detail}",
                    flush=True,
                )
                return None, q
            except Exception as e:
                print(
                    f"[solve_pending] solver failed for {problem_id}: {e}",
                    flush=True,
                )
                return None, None

    outcomes = await asyncio.gather(*(run_one(pid) for pid in partial_ids))
    saved = [problem for problem, _ in outcomes if problem is not None]
    quota_hits = [q for _, q in outcomes if q is not None]
    hit_quota_limit = bool(quota_hits)
    quota_reset_at: datetime | None = None
    for q in quota_hits:
        quota_reset_at = later_reset(quota_reset_at, q.reset_at)

    complete = len(saved) == expected
    summary = f"Solved {len(saved)} of {expected} partial(s)."
    if not complete:
        summary += f" (incomplete: {expected - len(saved)} failed)"
    if hit_quota_limit:
        summary += (
            f" (quota hit on {len(quota_hits)} solver(s); "
            f"resets_at={quota_reset_at})"
        )
    return StageResult(
        saved=saved,
        complete=complete,
        summary=summary,
        hit_quota_limit=hit_quota_limit,
        quota_reset_at=quota_reset_at,
    )


def solve_pending_problems(
    source_image: str,
    with_solution: bool = True,
) -> StageResult:
    """Stage 2. Walks every `unclassified` partial for `source_image` and
    runs the inner solver against it, updating the record in place."""
    return asyncio.run(
        _solve_pending_async(source_image, with_solution=with_solution)
    )
