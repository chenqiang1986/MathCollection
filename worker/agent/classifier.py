"""Batch classifier: classifies a batch of already-scanned problems in one
agent session, keyed by explicit `problem_id`s so a single system prompt
(including the whole category taxonomy) is paid for once per batch instead
of once per problem.

Exposes `classify_batch`, a plain async function that takes the list of
problem ids claimed by `storage.classify_tasks.claim_batch` and updates
each one's category/subcategory in place.
"""

import time

from claude_agent_sdk import ClaudeAgentOptions, query
from common import storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, PROMPTS_DIR, log_message
from jinja2 import Environment, FileSystemLoader

from worker.quota import QuotaHit, detect_in_message as detect_quota_in_message
from worker.agent.problem_store import build_classify_store
from worker.agent.results import StageResult

# Rough per-problem turn budget: one lookup_category_edits call, one
# save_classification call, plus slack for figure reads and the final
# summary reply.
TURNS_PER_PROBLEM = 4
MIN_MAX_TURNS = 10

_CLASSIFIER_TEMPLATE = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    keep_trailing_newline=True,
).get_template("classifier.md")


def _build_prompt(problems: list[storage.Problem]) -> str:
    parts = [
        f"Classify the following {len(problems)} math problem(s). Call "
        "`save_classification` exactly once per problem_id listed below."
    ]
    for i, problem in enumerate(problems, start=1):
        entry = [f"[{i}] problem_id={problem.id}", problem.problem_text]
        if problem.figure_image:
            fig_path = storage.figure_path(problem.figure_image)
            entry.append(
                f"An accompanying figure for problem_id={problem.id} is at "
                f"{fig_path}. Read it for spatial relationships (incidence, "
                "ordering of points, which lines are parallel, etc.). The "
                "problem text is authoritative for any numeric values."
            )
        parts.append("\n".join(entry))
    return "\n\n".join(parts)


async def classify_batch(problem_ids: list[str]) -> StageResult:
    """Run one batch-classify session against `problem_ids`. Missing
    problems (deleted since being claimed) are silently dropped."""
    problems = [
        p for p in (storage.get_problem(pid) for pid in problem_ids) if p is not None
    ]
    if not problems:
        return StageResult(saved=[], complete=True, summary="No problems to classify.")

    saved: list[storage.Problem] = []
    server = build_classify_store([p.id for p in problems], saved)

    allowed_tools = [
        "mcp__problem_store__save_classification",
        "mcp__problem_store__lookup_category_edits",
    ]
    if any(p.figure_image for p in problems):
        allowed_tools.append("Read")

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_CLASSIFIER_TEMPLATE.render(),
        mcp_servers={"problem_store": server},
        allowed_tools=allowed_tools,
        max_turns=max(MIN_MAX_TURNS, TURNS_PER_PROBLEM * len(problems)),
        max_buffer_size=MAX_BUFFER_SIZE,
    )
    prompt = _build_prompt(problems)

    print(f"[classifier] start batch of {len(problems)}", flush=True)
    started = time.monotonic()
    quota: QuotaHit | None = None
    async for message in query(prompt=prompt, options=options):
        log_message(message)
        q = detect_quota_in_message(message)
        if q is not None:
            print(f"[classifier] quota hit: {q.detail}", flush=True)
            quota = q
            break
    elapsed = time.monotonic() - started
    print(
        f"[classifier] done in {elapsed:.2f}s "
        f"({len(saved)}/{len(problems)} saved)",
        flush=True,
    )

    if quota is not None:
        return StageResult(
            saved=saved,
            complete=False,
            summary=(
                f"Classified {len(saved)} of {len(problems)} before quota "
                f"hit: {quota.detail}"
            ),
            hit_quota_limit=True,
            quota_reset_at=quota.reset_at,
        )
    complete = len(saved) == len(problems)
    return StageResult(
        saved=saved,
        complete=complete,
        summary=f"Classified {len(saved)} of {len(problems)} problem(s).",
    )
