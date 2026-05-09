"""Alternative orchestration: a single top-level agent decides when to
transcribe and when to fan out to a `solve_and_save` tool that spawns a
fresh sub-agent per problem.

This is a sketch — not wired into app.py. To use it, swap the import in
src/app.py from `import agent` to `import agent_self_planning as agent`.

Design:
- One outer `query()` with system prompt that tells the model to read the
  image, identify each problem, and call `solve_and_save` per problem.
- `solve_and_save(problem_text)` is an MCP tool. Its body spawns a fresh
  inner `query()` with the solver system prompt and the existing
  `save_problem` MCP server. Each call is its own context window.
"""

import asyncio
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from jinja2 import Template

import storage
from agent import _build_problem_store, _log_message

MODEL = "claude-sonnet-4-6"
ORCHESTRATOR_MAX_TURNS = 20
SOLVER_MAX_TURNS = 6

_PROMPTS_DIR = Path(__file__).parent / "prompts"
ORCHESTRATOR_SYSTEM_PROMPT = (_PROMPTS_DIR / "orchestrator.md").read_text()
_SOLVER_TEMPLATE = Template((_PROMPTS_DIR / "solver.md").read_text())


async def _run_inner_solver(
    problem_text: str,
    source_image: str | None,
    figure_image: str | None = None,
    with_solution: bool = True,
) -> dict:
    saved: list[dict] = []
    server = _build_problem_store(
        source_image, saved, figure_image=figure_image, with_solution=with_solution
    )

    allowed_tools = ["mcp__problem_store__save_problem"]
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
        _log_message(message)
    elapsed = time.monotonic() - started
    print(f"[solver] done in {elapsed:.2f}s", flush=True)

    if len(saved) != 1:
        raise ValueError(
            f"Inner solver expected exactly one saved record, got "
            f"{len(saved)} for problem: {problem_text!r}"
        )

    record = saved[0]
    if with_solution:
        record = storage.update_problem(
            record["id"],
            solve_time_seconds=round(elapsed, 2),
            solve_time_estimated=False,
        )
    return record


def _build_solver_tool(
    source_image: str | None,
    all_saved: list[dict],
    with_solution: bool = True,
):
    @tool(
        "solve_and_save",
        (
            "Spawn a fresh sub-agent that classifies, solves, and persists a "
            "single math problem. Pass the verbatim problem text with math "
            "wrapped in `$...$` (inline) or `$$...$$` (display). If the "
            "problem has an accompanying figure in the source image, pass "
            "`figure_bbox` as a list [x0, y0, x1, y1] of normalized "
            "coordinates in [0, 1] tightly enclosing just the figure (no "
            "surrounding problem text), and `figure_rotation` as the "
            "clockwise rotation in degrees (0, 90, 180, or 270) needed to "
            "make the cropped figure appear upright. Otherwise pass an "
            "empty list and 0. Returns a short confirmation string with the "
            "saved record id, category, and difficulty. Call once per "
            "distinct problem."
        ),
        {"problem_text": str, "figure_bbox": list, "figure_rotation": int},
    )
    async def solve_and_save(args: dict) -> dict:
        bbox = args.get("figure_bbox") or []
        rotation = int(args.get("figure_rotation") or 0)
        figure_image: str | None = None
        if bbox and source_image:
            try:
                figure_image = storage.save_figure(
                    source_image, bbox, rotation=rotation
                )
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
        record = await _run_inner_solver(
            args["problem_text"],
            source_image,
            figure_image=figure_image,
            with_solution=with_solution,
        )
        all_saved.append(record)
        secs = record.get("solve_time_seconds")
        if secs is None:
            tail = "solve_time=unknown"
        else:
            qual = "est." if record.get("solve_time_estimated") else "measured"
            tail = f"solve_time={secs}s ({qual})"
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Saved {record['id']} "
                        f"(category={record['category']}, {tail})"
                    ),
                }
            ]
        }

    return create_sdk_mcp_server(
        name="solver",
        version="1.0.0",
        tools=[solve_and_save],
    )


async def _process_image_async(
    image_path: Path,
    source_image: str | None,
    with_solution: bool = True,
) -> dict:
    saved: list[dict] = []
    server = _build_solver_tool(source_image, saved, with_solution=with_solution)

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        mcp_servers={"solver": server},
        allowed_tools=["Read", "mcp__solver__solve_and_save"],
        max_turns=ORCHESTRATOR_MAX_TURNS,
    )

    prompt = (
        f"Read the image at {image_path}. Extract every distinct math problem "
        "and dispatch each one to `mcp__solver__solve_and_save`. When all "
        "problems have been dispatched, reply with a short summary."
    )

    print("[orchestrator] start", flush=True)
    final_text = ""
    async for message in query(prompt=prompt, options=options):
        _log_message(message)
        if isinstance(message, AssistantMessage):
            text_parts = [
                block.text
                for block in message.content
                if isinstance(block, TextBlock)
            ]
            if text_parts:
                final_text = "\n".join(text_parts)
        elif isinstance(message, ResultMessage):
            result_text = getattr(message, "result", None)
            if result_text:
                final_text = result_text
    print("[orchestrator] done", flush=True)

    return {"saved": saved, "summary": final_text or "[no summary returned]"}


def process_image(
    image_path: Path,
    source_image: str | None = None,
    with_solution: bool = True,
) -> dict:
    """Same signature as agent.process_image — drop-in alternative."""
    return asyncio.run(
        _process_image_async(
            Path(image_path), source_image, with_solution=with_solution
        )
    )
