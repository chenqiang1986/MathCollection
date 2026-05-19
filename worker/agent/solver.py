"""Inner solver: classifies/solves a single partial problem in a fresh
agent context.

Exposes `solve_problem`, a plain async function that takes one partial
`storage.Problem` saved by the scan stage and updates it in place with
category/subcategory/solution via `build_problem_store(mode="solved")`.
"""

import time

from claude_agent_sdk import ClaudeAgentOptions, query
from common import storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, PROMPTS_DIR, log_message
from jinja2 import Environment, FileSystemLoader

from ..quota import detect_in_message as detect_quota_in_message
from .problem_store import build_problem_store

SOLVER_MAX_TURNS = 7

_SOLVER_TEMPLATE = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    keep_trailing_newline=True,
).get_template("solver.md")


async def solve_problem(
    partial: storage.Problem,
    with_solution: bool = True,
) -> storage.Problem:
    """Run the inner solver against `partial` (a record saved by the scan
    stage with category=`unclassified`) and update it in place."""
    saved: list[storage.Problem] = []
    server = build_problem_store(
        partial.source_image,
        saved,
        mode="solved",
        existing_problem_id=partial.id,
        with_solution=with_solution,
    )

    allowed_tools = [
        "mcp__problem_store__save_problem",
        "mcp__problem_store__lookup_category_edits",
    ]
    if partial.figure_image:
        allowed_tools.append("Read")

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_SOLVER_TEMPLATE.render(with_solution=with_solution),
        mcp_servers={"problem_store": server},
        allowed_tools=allowed_tools,
        max_turns=SOLVER_MAX_TURNS,
        max_buffer_size=MAX_BUFFER_SIZE,
    )

    user_action = (
        "Analyze and solve the following math problem"
        if with_solution
        else "Analyze and rate the difficulty of the following math problem"
    )
    prompt_parts = [
        f"{user_action}, then call `mcp__problem_store__save_problem` "
        "exactly once.",
        f"Problem:\n{partial.problem_text}",
    ]
    if partial.figure_image:
        fig_path = storage.figure_path(partial.figure_image)
        prompt_parts.append(
            f"An accompanying figure is at {fig_path}. Read it with the "
            "`Read` tool for spatial relationships (incidence, ordering of "
            "points, which lines are parallel, etc.). The problem text is "
            "authoritative for any numeric values."
        )
    prompt = "\n\n".join(prompt_parts)

    print(f"[solver] start id={partial.id}", flush=True)
    started = time.monotonic()
    async for message in query(prompt=prompt, options=options):
        log_message(message)
        quota = detect_quota_in_message(message)
        if quota is not None:
            print(f"[solver] quota hit: {quota.detail}", flush=True)
            raise quota
    elapsed = time.monotonic() - started
    print(f"[solver] done in {elapsed:.2f}s", flush=True)

    if len(saved) != 1:
        raise ValueError(
            f"Inner solver expected exactly one updated record, got "
            f"{len(saved)} for problem id={partial.id}"
        )

    problem = saved[0]
    if with_solution:
        problem = storage.update_problem(
            problem.id,
            solve_time_seconds=round(elapsed, 2),
        )
    return problem
