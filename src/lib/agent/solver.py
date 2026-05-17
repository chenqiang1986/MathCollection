"""Inner solver: classifies/solves a single problem in a fresh agent context,
and the MCP `solve_and_save` tool that the orchestrator calls to spawn one."""

import time

import figures
from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)
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


def build_solver_tool(
    source_image: str | None,
    all_saved: list[storage.Problem],
    with_solution: bool = True,
):
    @tool(
        "solve_and_save",
        (
            "Spawn a fresh sub-agent that classifies, solves, and persists a "
            "single math problem. Pass the verbatim problem text with math "
            "wrapped in `$...$` (inline) or `$$...$$` (display). Escape any "
            "literal dollar-sign currency (USD) as `\\$` (e.g. `\\$5` for "
            "five dollars) so it is not parsed as a math delimiter. Also "
            "pass `source_exam` (math competition name such as 'AMC10', "
            "'AIME', 'BMT', 'ARML' — use 'Unknown' if absent), `year` (the "
            "4-digit year of the competition as a string, or 'Unknown' if "
            "absent), and `source_page` (1-indexed page number the problem "
            "appears on; pass 1 for single-image sources). If the problem "
            "has an accompanying figure in the source image, pass "
            "`figure_bbox` as a list [x0, y0, x1, y1] of normalized "
            "coordinates in [0, 1] tightly enclosing just the figure (no "
            "surrounding problem text), and `figure_rotation` as the "
            "clockwise rotation in degrees (0, 90, 180, or 270) needed to "
            "make the cropped figure appear upright. When the source is a "
            "multi-page PDF, also pass `figure_page` as the 1-indexed page "
            "number the figure appears on; `figure_bbox` is relative to that "
            "page. For single-image sources or problems with no figure, "
            "`figure_page` is ignored (pass 1). When there is no figure, "
            "pass an empty list for `figure_bbox` and 0 for "
            "`figure_rotation`. Returns a short confirmation string with the "
            "saved record id, category, and difficulty. Call once per "
            "distinct problem."
        ),
        {
            "problem_text": str,
            "source_exam": str,
            "year": str,
            "source_page": int,
            "figure_bbox": list,
            "figure_rotation": int,
            "figure_page": int,
        },
    )
    async def solve_and_save(args: dict) -> dict:
        bbox = args.get("figure_bbox") or []
        rotation = int(args.get("figure_rotation") or 0)
        page = int(args.get("figure_page") or 1)
        source_page = int(args.get("source_page") or 1)
        source_exam = (args.get("source_exam") or "Unknown").strip() or "Unknown"
        year = str(args.get("year") or "Unknown").strip() or "Unknown"
        figure_image: str | None = None
        saved_bbox: list[float] | None = None
        if bbox and source_image:
            try:
                figure_image = figures.save_figure(
                    source_image, bbox, rotation=rotation, page=page
                )
                saved_bbox = [float(v) for v in bbox]
            except Exception as e:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"figure_bbox crop failed: {e}. Re-call with "
                                "a valid normalized bbox or an empty list."
                            ),
                        }
                    ],
                    "is_error": True,
                }
        problem = await _run_inner_solver(
            args["problem_text"],
            source_image,
            source_page=source_page,
            source_exam=source_exam,
            year=year,
            figure_image=figure_image,
            figure_bbox=saved_bbox,
            with_solution=with_solution,
        )
        all_saved.append(problem)
        secs = problem.solve_time_seconds
        if secs is None:
            tail = "solve_time=unknown"
        else:
            qual = "est." if problem.solve_time_estimated else "measured"
            tail = f"solve_time={secs}s ({qual})"
        cat_str = problem.category
        if problem.subcategory:
            cat_str = f"{problem.category} / {problem.subcategory}"
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Saved {problem.id} "
                        f"(category={cat_str}, {tail})"
                    ),
                }
            ]
        }

    return create_sdk_mcp_server(
        name="solver",
        version="1.0.0",
        tools=[solve_and_save],
    )
