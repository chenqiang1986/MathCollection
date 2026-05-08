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

from agent import _build_problem_store, _log_message

MODEL = "claude-sonnet-4-6"
ORCHESTRATOR_MAX_TURNS = 20
SOLVER_MAX_TURNS = 6

_PROMPTS_DIR = Path(__file__).parent / "prompts"
ORCHESTRATOR_SYSTEM_PROMPT = (_PROMPTS_DIR / "orchestrator.md").read_text()
SOLVER_SYSTEM_PROMPT = (_PROMPTS_DIR / "solver.md").read_text()


async def _run_inner_solver(
    problem_text: str,
    source_image: str | None,
    diagram_svg: str | None = None,
    with_solution: bool = True,
) -> dict:
    saved: list[dict] = []
    server = _build_problem_store(source_image, saved, diagram_svg=diagram_svg)

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SOLVER_SYSTEM_PROMPT,
        mcp_servers={"problem_store": server},
        allowed_tools=["mcp__problem_store__save_problem"],
        max_turns=SOLVER_MAX_TURNS,
    )

    prompt_parts = [
        "Analyze and solve the following math problem, then call "
        "`mcp__problem_store__save_problem` exactly once.",
        f"Problem:\n{problem_text}",
    ]
    if diagram_svg:
        prompt_parts.append(
            "An accompanying figure is provided below as inline SVG. Use it "
            "for spatial relationships (incidence, ordering of points, which "
            "lines are parallel, etc.). Treat coordinates as approximate; "
            "the problem text is authoritative for any numeric values.\n\n"
            f"diagram_svg:\n{diagram_svg}"
        )
    if not with_solution:
        prompt_parts.append(
            "OVERRIDE: The user has opted out of step-by-step solutions for "
            "this run. Still identify `category` and `difficulty`, but pass "
            "an empty string for `solution` and an empty string for "
            "`solution_svg`. Do not perform or write up the solution."
        )
    prompt = "\n\n".join(prompt_parts)

    print("[solver] start", flush=True)
    async for message in query(prompt=prompt, options=options):
        _log_message(message)
    print("[solver] done", flush=True)

    if len(saved) != 1:
        raise ValueError(
            f"Inner solver expected exactly one saved record, got "
            f"{len(saved)} for problem: {problem_text!r}"
        )
    return saved[0]


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
            "problem has a geometric figure, pass an inline SVG of just the "
            "figure as `diagram_svg`; otherwise pass an empty string. "
            "Returns a short confirmation string with the saved record id, "
            "category, and difficulty. Call once per distinct problem."
        ),
        {"problem_text": str, "diagram_svg": str},
    )
    async def solve_and_save(args: dict) -> dict:
        svg = args.get("diagram_svg") or ""
        record = await _run_inner_solver(
            args["problem_text"],
            source_image,
            diagram_svg=svg.strip() or None,
            with_solution=with_solution,
        )
        all_saved.append(record)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Saved {record['id']} "
                        f"(category={record['category']}, "
                        f"difficulty={record['difficulty']})"
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
