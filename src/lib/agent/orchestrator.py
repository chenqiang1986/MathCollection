"""Top-level orchestrator: one LLM call parses every problem out of the
source image/PDF into a structured list, then a plain Python loop fans the
list out to the solver with bounded concurrency."""

import asyncio
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)
from lib import storage

from .solver import solve_problem
from .util import MODEL, PROMPTS_DIR, log_message

ORCHESTRATOR_MAX_TURNS = 4
# Concurrency cap for fan-out. Each inner solver is its own SDK session, so
# this also caps simultaneous API calls — keep modest to stay under the
# five-hour rate limit on Sonnet.
SOLVER_CONCURRENCY = 4

ORCHESTRATOR_SYSTEM_PROMPT = (PROMPTS_DIR / "orchestrator.md").read_text()


class ProcessImageResult(NamedTuple):
    saved: list[storage.Problem]
    summary: str


def _build_report_tool(parsed: list[dict]):
    @tool(
        "report_problems",
        (
            "Report every distinct math problem extracted from the source. "
            "Call exactly once with the full list. Each item is a dict with "
            "keys: `problem_text` (verbatim, math wrapped in `$...$` or "
            "`$$...$$`; literal currency `$` escaped as `\\$`), "
            "`source_exam` (competition short form like `AMC10`, `AIME`, "
            "`BMT`, or `Unknown`), `year` (4-digit string or `Unknown`), "
            "`source_page` (1-indexed page in the source; 1 for single "
            "images), `figure_bbox` (`[x0, y0, x1, y1]` normalized to "
            "[0, 1] tightly enclosing just the figure, or `[]` if none), "
            "`figure_rotation` (clockwise degrees needed to upright the "
            "crop: 0/90/180/270; 0 if no figure), `figure_page` (1-indexed "
            "page the figure is on; 1 if no figure)."
        ),
        {"problems": list},
    )
    async def report_problems(args: dict) -> dict:
        problems = args.get("problems") or []
        parsed.extend(problems)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Recorded {len(problems)} problem(s).",
                }
            ]
        }

    return create_sdk_mcp_server(
        name="orchestrator",
        version="1.0.0",
        tools=[report_problems],
    )


async def _parse_problems(image_path: Path) -> list[dict]:
    parsed: list[dict] = []
    server = _build_report_tool(parsed)
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        mcp_servers={"orchestrator": server},
        allowed_tools=["Read", "mcp__orchestrator__report_problems"],
        max_turns=ORCHESTRATOR_MAX_TURNS,
    )
    prompt = (
        f"Read the file at {image_path} (image or PDF). Extract every "
        "distinct math problem and call "
        "`mcp__orchestrator__report_problems` exactly once with the full "
        "list."
    )

    print("[orchestrator] start", flush=True)
    async for message in query(prompt=prompt, options=options):
        log_message(message)
    print(f"[orchestrator] parsed {len(parsed)} problem(s)", flush=True)
    return parsed


async def _process_image_async(
    image_path: Path,
    source_image: str | None,
    with_solution: bool = True,
) -> ProcessImageResult:
    parsed = await _parse_problems(image_path)
    if not parsed:
        return ProcessImageResult(saved=[], summary="No problems found.")

    sem = asyncio.Semaphore(SOLVER_CONCURRENCY)

    async def run_one(p: dict) -> storage.Problem | None:
        async with sem:
            try:
                return await solve_problem(
                    p, source_image, with_solution=with_solution
                )
            except Exception as e:
                print(
                    f"[orchestrator] solver failed for "
                    f"{p.get('problem_text', '')[:80]!r}: {e}",
                    flush=True,
                )
                return None

    results = await asyncio.gather(*(run_one(p) for p in parsed))
    saved = [r for r in results if r is not None]
    summary = f"Saved {len(saved)} of {len(parsed)} problem(s)."
    return ProcessImageResult(saved=saved, summary=summary)


def process_image(
    image_path: Path,
    source_image: str | None = None,
    with_solution: bool = True,
) -> ProcessImageResult:
    return asyncio.run(
        _process_image_async(
            Path(image_path), source_image, with_solution=with_solution
        )
    )
