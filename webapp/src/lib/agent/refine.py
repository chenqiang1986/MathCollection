"""Refine an existing saved problem. The agent reads the user's free-form
request and picks exactly one of three actions: re-solve with a hint,
re-crop the figure with a corrected bbox, or re-transcribe the problem
text from the source. Updates the existing record in-place."""

import asyncio
import time
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)
from common import figures, storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, PROMPTS_DIR, log_message
from jinja2 import Environment, FileSystemLoader

REFINE_MAX_TURNS = 8

_JINJA = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    keep_trailing_newline=True,
)
_REFINE_TEMPLATE = _JINJA.get_template("refine.md")


def _build_refine_store(
    problem: storage.Problem,
    chosen: list[storage.Problem],
):
    """MCP server with three structured-output tools — the agent calls
    exactly one to declare its chosen refine action. Each tool persists
    the corresponding change and appends the updated record."""

    @tool(
        "resolve_with_hint",
        (
            "Action 1: produce a better solution informed by the user's "
            "hint. Provide the final `solution` plus `category` and "
            "`subcategory` (which may also change). Call this when the "
            "user is asking for a different solution approach, a "
            "corrected answer, or a clearer explanation."
        ),
        {
            "category": str,
            "subcategory": str,
            "solution": str,
        },
    )
    async def resolve_with_hint(args: dict) -> dict:
        updated = storage.update_problem(
            problem.id,
            category=args["category"],
            subcategory=args.get("subcategory", ""),
            solution=args.get("solution", ""),
        )
        chosen.append(updated)
        return {
            "content": [
                {"type": "text", "text": f"Re-solved problem {updated.id}."}
            ]
        }

    @tool(
        "update_figure_bbox",
        (
            "Action 2: re-crop the figure with a corrected bounding box. "
            "Provide `figure_bbox` as normalized [x0, y0, x1, y1] in "
            "[0, 1] tightly around just the figure, `figure_rotation` "
            "(clockwise degrees to upright the crop: 0/90/180/270), and "
            "`figure_page` (1-indexed source page; 1 for non-PDF "
            "sources). Read the original source image/PDF first. Does "
            "not modify the solution."
        ),
        {
            "figure_bbox": list,
            "figure_rotation": int,
            "figure_page": int,
        },
    )
    async def update_figure_bbox(args: dict) -> dict:
        if not problem.source_image:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Error: this problem has no source_image, so "
                            "the figure cannot be re-cropped."
                        ),
                    }
                ],
                "is_error": True,
            }
        bbox = [float(v) for v in args["figure_bbox"]]
        rotation = int(args["figure_rotation"])
        page = int(args["figure_page"])
        new_figure = figures.save_figure(
            problem.source_image, bbox, rotation=rotation, page=page
        )
        if problem.figure_image:
            old = storage.figure_path(problem.figure_image)
            if old.exists():
                old.unlink()
        updated = storage.update_problem(
            problem.id,
            figure_image=new_figure,
            figure_bbox=bbox,
            figure_page=page,
        )
        chosen.append(updated)
        return {
            "content": [
                {"type": "text", "text": f"Re-cropped figure for {updated.id}."}
            ]
        }

    @tool(
        "update_problem_text",
        (
            "Action 3: replace the stored problem text with a re-read "
            "transcription from the source image. Provide the corrected "
            "`problem_text` verbatim (math wrapped in `$...$` or "
            "`$$...$$`; literal currency `$` escaped as `\\$`). Read the "
            "original source first. Does not modify the solution."
        ),
        {"problem_text": str},
    )
    async def update_problem_text(args: dict) -> dict:
        if not problem.source_image:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Error: this problem has no source_image, so "
                            "the problem text cannot be re-read."
                        ),
                    }
                ],
                "is_error": True,
            }
        new_text = args["problem_text"].strip()
        if not new_text:
            return {
                "content": [
                    {"type": "text", "text": "Error: problem_text is empty."}
                ],
                "is_error": True,
            }
        updated = storage.update_problem(problem.id, problem_text=new_text)
        chosen.append(updated)
        return {
            "content": [
                {"type": "text", "text": f"Re-read text for {updated.id}."}
            ]
        }

    return create_sdk_mcp_server(
        name="refine_store",
        version="1.0.0",
        tools=[resolve_with_hint, update_figure_bbox, update_problem_text],
    )


async def _refine_async(problem: storage.Problem, hint: str) -> storage.Problem:
    hint = (hint or "").strip()
    if not hint:
        raise ValueError("Refine requires a non-empty user message.")

    chosen: list[storage.Problem] = []
    server = _build_refine_store(problem, chosen)

    allowed_tools = [
        "mcp__refine_store__resolve_with_hint",
        "mcp__refine_store__update_figure_bbox",
        "mcp__refine_store__update_problem_text",
    ]
    if problem.source_image or problem.figure_image:
        allowed_tools.append("Read")

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_REFINE_TEMPLATE.render(with_solution=True),
        mcp_servers={"refine_store": server},
        allowed_tools=allowed_tools,
        max_turns=REFINE_MAX_TURNS,
        max_buffer_size=MAX_BUFFER_SIZE,
    )

    prompt_parts = [
        "Decide which one of the three refine actions matches the user's "
        "request below, then call EXACTLY ONE corresponding tool. Do not "
        "call more than one.",
        f"User request:\n{hint}",
        f"Current problem text:\n{problem.problem_text}",
    ]
    if problem.solution:
        prompt_parts.append(f"Current solution:\n{problem.solution}")
    prompt_parts.append(
        f"Current category: {problem.category} / "
        f"{problem.subcategory or '(none)'}"
    )
    if problem.figure_image:
        fig_path = storage.figure_path(problem.figure_image)
        prompt_parts.append(
            f"Current cropped figure: {fig_path}. Read it if you need to "
            "judge whether the existing crop captured the right region."
        )
        if problem.figure_bbox:
            prompt_parts.append(
                f"Current figure_bbox (normalized): {problem.figure_bbox}"
            )
    rendered_page_path: Path | None = None
    if problem.source_image:
        source_page = problem.source_page or 1
        rendered_page_path = figures.render_source_page_to_temp_png(
            problem.source_image, page=source_page
        )
        prompt_parts.append(
            f"Source page {source_page} rendered as PNG: "
            f"{rendered_page_path}. This image IS the page (already "
            "rasterized for you) — Read it once for `update_figure_bbox` "
            "(bbox is normalized [0,1] over this page) or "
            "`update_problem_text`. Pass figure_page="
            f"{source_page} when calling `update_figure_bbox`."
        )
    else:
        prompt_parts.append(
            "(No source image is stored for this problem, so "
            "`update_figure_bbox` and `update_problem_text` are unavailable. "
            "Use `resolve_with_hint` and note the limitation if relevant.)"
        )

    prompt = "\n\n".join(prompt_parts)

    print(f"[refine] start problem={problem.id}", flush=True)
    started = time.monotonic()
    try:
        async for message in query(prompt=prompt, options=options):
            log_message(message)
    finally:
        if rendered_page_path is not None:
            rendered_page_path.unlink(missing_ok=True)
    elapsed = time.monotonic() - started
    print(f"[refine] done in {elapsed:.2f}s", flush=True)

    if len(chosen) != 1:
        raise ValueError(
            f"Refine expected exactly one action, got {len(chosen)} "
            f"for problem {problem.id}"
        )
    updated = chosen[0]
    # If the problem had no real solve time before this refine, treat the
    # refine elapsed as the first real solve (only meaningful when refine
    # actually produced a solution).
    if problem.solve_time_seconds is None and updated.solution:
        updated = storage.update_problem(
            updated.id, solve_time_seconds=round(elapsed, 2)
        )
    return updated


def refine_problem(problem: storage.Problem, hint: str = "") -> storage.Problem:
    return asyncio.run(_refine_async(problem, hint))
