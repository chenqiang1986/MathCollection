"""Top-level agent: reads the source image and fans out one `solve_and_save`
call per distinct problem."""

import asyncio
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)
from lib import storage

from .solver import build_solver_tool
from .util import MODEL, PROMPTS_DIR, log_message

ORCHESTRATOR_MAX_TURNS = 20

ORCHESTRATOR_SYSTEM_PROMPT = (PROMPTS_DIR / "orchestrator.md").read_text()


class ProcessImageResult(NamedTuple):
    saved: list[storage.Problem]
    summary: str


async def _process_image_async(
    image_path: Path,
    source_image: str | None,
    with_solution: bool = True,
) -> ProcessImageResult:
    saved: list[storage.Problem] = []
    server = build_solver_tool(source_image, saved, with_solution=with_solution)

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
        log_message(message)
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

    return ProcessImageResult(
        saved=saved,
        summary=final_text or "[no summary returned]",
    )


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
