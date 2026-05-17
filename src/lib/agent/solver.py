"""Inner solver: classifies/solves a single problem in a fresh agent context.

Exposes `solve_problem`, a plain async function that takes one parsed
problem dict from the orchestrator, crops its figure if any, and runs the
solver agent against it."""

import time

import figures
from claude_agent_sdk import ClaudeAgentOptions, query
from jinja2 import Environment, FileSystemLoader
from lib import storage

from .problem_store import build_problem_store
from .util import MODEL, PROMPTS_DIR, log_message

SOLVER_MAX_TURNS = 7

_SOLVER_TEMPLATE = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    keep_trailing_newline=True,
).get_template("solver.md")


async def _run_inner_solver(
    problem_text: str,
    source_image: str | None,
    source_page: int | None = None,
    source_exam: str = "Unknown",
    year: str = "Unknown",
    figure_image: str | None = None,
    figure_bbox: list[float] | None = None,
    with_solution: bool = True,
) -> storage.Problem:
    saved: list[storage.Problem] = []
    server = build_problem_store(
        source_image,
        saved,
        source_page=source_page,
        source_exam=source_exam,
        year=year,
        figure_image=figure_image,
        figure_bbox=figure_bbox,
        with_solution=with_solution,
    )

    allowed_tools = [
        "mcp__problem_store__save_problem",
        "mcp__problem_store__lookup_category_edits",
    ]
    if figure_image:
        allowed_tools.append("Read")

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_SOLVER_TEMPLATE.render(with_solution=with_solution),
        mcp_servers={"problem_store": server},
        allowed_tools=allowed_tools,
        max_turns=SOLVER_MAX_TURNS,
    )

    user_action = (
        "Analyze and solve the following math problem"
        if with_solution
        else "Analyze and rate the difficulty of the following math problem"
    )
    prompt_parts = [
        f"{user_action}, then call `mcp__problem_store__save_problem` "
        "exactly once.",
        f"Problem:\n{problem_text}",
    ]
    if figure_image:
        fig_path = storage.figure_path(figure_image)
        prompt_parts.append(
            f"An accompanying figure is at {fig_path}. Read it with the "
            "`Read` tool for spatial relationships (incidence, ordering of "
            "points, which lines are parallel, etc.). The problem text is "
            "authoritative for any numeric values."
        )
    prompt = "\n\n".join(prompt_parts)

    print("[solver] start", flush=True)
    started = time.monotonic()
    async for message in query(prompt=prompt, options=options):
        log_message(message)
    elapsed = time.monotonic() - started
    print(f"[solver] done in {elapsed:.2f}s", flush=True)

    if len(saved) != 1:
        raise ValueError(
            f"Inner solver expected exactly one saved record, got "
            f"{len(saved)} for problem: {problem_text!r}"
        )

    problem = saved[0]
    if with_solution:
        problem = storage.update_problem(
            problem.id,
            solve_time_seconds=round(elapsed, 2),
            solve_time_estimated=False,
        )
    return problem


async def solve_problem(
    parsed: dict,
    source_image: str | None,
    with_solution: bool = True,
) -> storage.Problem:
    """Crop the figure (if any) and run the inner solver for one parsed
    problem dict produced by the orchestrator."""
    bbox = parsed.get("figure_bbox") or []
    rotation = int(parsed.get("figure_rotation") or 0)
    page = int(parsed.get("figure_page") or 1)
    source_page = int(parsed.get("source_page") or 1)
    source_exam = (parsed.get("source_exam") or "Unknown").strip() or "Unknown"
    year = str(parsed.get("year") or "Unknown").strip() or "Unknown"

    figure_image: str | None = None
    saved_bbox: list[float] | None = None
    if bbox and source_image:
        figure_image = figures.save_figure(
            source_image, bbox, rotation=rotation, page=page
        )
        saved_bbox = [float(v) for v in bbox]

    return await _run_inner_solver(
        parsed["problem_text"],
        source_image,
        source_page=source_page,
        source_exam=source_exam,
        year=year,
        figure_image=figure_image,
        figure_bbox=saved_bbox,
        with_solution=with_solution,
    )
