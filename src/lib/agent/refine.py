"""Refine an existing saved problem: re-run the solver against the stored
problem text (plus an optional user hint) and update the same record
in-place, keeping its id."""

import time

import figures
from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)
from jinja2 import Template
from lib import storage

from .util import MODEL, PROMPTS_DIR, log_message

REFINE_MAX_TURNS = 6

_SOLVER_TEMPLATE = Template((PROMPTS_DIR / "solver.md").read_text())


def _build_update_store(
    problem_id: str,
    updated: list[storage.Problem],
):
    """MCP server that exposes `save_problem` but routes to update_problem,
    so the solver's existing tool-calling contract is preserved while the
    target record keeps its id and created_at."""

    @tool(
        "save_problem",
        (
            "Persist the refined solution for the math problem currently "
            "under review. Call exactly once."
        ),
        {
            "problem_text": str,
            "category": str,
            "subcategory": str,
            "solution": str,
        },
    )
    async def save_problem(args: dict) -> dict:
        problem = storage.update_problem(
            problem_id,
            category=args["category"],
            subcategory=args.get("subcategory", ""),
            solution=args.get("solution", ""),
        )
        updated.append(problem)
        return {
            "content": [
                {"type": "text", "text": f"Updated problem {problem.id}."}
            ]
        }

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_problem],
    )


async def _refine_async(problem: storage.Problem, hint: str) -> storage.Problem:
    figure_image = problem.figure_image
    # `solve_time_seconds` is the difficulty signal. A hint or an earlier
    # solution attempt both compromise that signal, so only treat this run as
    # an unaided first solve (and overwrite the time) when neither is present.
    update_solve_time = not problem.solution and not hint
    updated: list[storage.Problem] = []
    server = _build_update_store(problem.id, updated)

    allowed_tools = ["mcp__problem_store__save_problem"]
    if figure_image:
        allowed_tools.append("Read")

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_SOLVER_TEMPLATE.render(with_solution=True),
        mcp_servers={"problem_store": server},
        allowed_tools=allowed_tools,
        max_turns=REFINE_MAX_TURNS,
    )

    prompt_parts = [
        "Analyze and solve the following math problem, then call "
        "`mcp__problem_store__save_problem` exactly once with the refined "
        "`solution`. Use the original `problem_text` verbatim.",
        f"Problem:\n{problem.problem_text}",
    ]
    if problem.solution:
        prompt_parts.append(
            "An earlier solution attempt is provided below. The user found "
            "it sub-optimal or unsatisfactory; produce a better solution "
            "(shorter, clearer, or more elegant) rather than restating it.\n"
            f"Earlier solution:\n{problem.solution}"
        )
    if hint:
        prompt_parts.append(
            "The user provided the following hint to guide your reasoning. "
            "Treat it as a strong steer but verify it against the problem.\n"
            f"User hint:\n{hint}"
        )
    if figure_image:
        fig_path = storage.figure_path(figure_image)
        prompt_parts.append(
            f"An accompanying figure is at {fig_path}. Read it with the "
            "`Read` tool for spatial relationships (incidence, ordering of "
            "points, which lines are parallel, etc.). The problem text is "
            "authoritative for any numeric values."
        )
    prompt = "\n\n".join(prompt_parts)

    print(f"[refine] start problem={problem.id}", flush=True)
    started = time.monotonic()
    async for message in query(prompt=prompt, options=options):
        log_message(message)
    elapsed = time.monotonic() - started
    print(f"[refine] done in {elapsed:.2f}s", flush=True)

    if len(updated) != 1:
        raise ValueError(
            f"Refine expected exactly one update, got {len(updated)} "
            f"for problem {problem.id}"
        )

    if update_solve_time:
        return storage.update_problem(
            problem.id,
            solve_time_seconds=round(elapsed, 2),
            solve_time_estimated=False,
        )
    return updated[0]


def refine_problem(problem: storage.Problem, hint: str = "") -> storage.Problem:
    import asyncio

    return asyncio.run(_refine_async(problem, hint))
