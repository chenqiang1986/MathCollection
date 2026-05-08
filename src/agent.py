import asyncio
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

import storage

MODEL = "claude-opus-4-7"
MAX_TURNS = 12

SYSTEM_PROMPT = """You are a math problem extraction and analysis agent.

You receive an image that may contain one OR multiple math problems. For each
distinct problem in the image:

1. Transcribe the problem text exactly. Wrap every math formula or symbol in
   LaTeX delimiters: inline math in `$...$`, display math in `$$...$$`.
   Plain prose stays outside the delimiters. Example:
   "Find all real $x$ such that $$x^2 - 5x + 6 = 0.$$"
2. Identify the math category (e.g. "algebra", "calculus", "geometry",
   "number theory", "combinatorics", "linear algebra", "probability", etc.).
3. Estimate the difficulty as one of: "elementary", "middle school",
   "high school", "undergraduate", "graduate", "olympiad".
4. Solve the problem and write a clear, step-by-step `solution`, using the
   same `$...$` / `$$...$$` convention for any math.
5. Call the `save_problem` tool ONCE per distinct problem with the structured
   fields. Do NOT batch multiple problems into one call.

After every problem in the image has been saved with `save_problem`, reply
with a short plain-text summary of how many problems you saved and their
categories. Do not call `save_problem` again in the summary turn."""


def _truncate(text: str, limit: int = 300) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _log_message(message) -> None:
    if isinstance(message, SystemMessage):
        subtype = getattr(message, "subtype", "")
        print(f"[agent] system{f' ({subtype})' if subtype else ''}", flush=True)
        return

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(f"[agent] assistant text: {_truncate(block.text)}", flush=True)
            elif isinstance(block, ThinkingBlock):
                print(f"[agent] assistant thinking: {_truncate(block.thinking)}", flush=True)
            elif isinstance(block, ToolUseBlock):
                print(
                    f"[agent] tool_use {block.name} (id={block.id}) "
                    f"input={_truncate(str(block.input))}",
                    flush=True,
                )
        return

    if isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, str):
            print(f"[agent] user: {_truncate(content)}", flush=True)
            return
        for block in content:
            if isinstance(block, ToolResultBlock):
                status = "error" if getattr(block, "is_error", False) else "ok"
                print(
                    f"[agent] tool_result ({status}) for {block.tool_use_id}: "
                    f"{_truncate(str(block.content))}",
                    flush=True,
                )
            elif isinstance(block, TextBlock):
                print(f"[agent] user text: {_truncate(block.text)}", flush=True)
        return

    if isinstance(message, ResultMessage):
        usage = getattr(message, "usage", None)
        cost = getattr(message, "total_cost_usd", None)
        duration_ms = getattr(message, "duration_ms", None)
        print(
            f"[agent] result: duration={duration_ms}ms cost={cost} usage={usage}",
            flush=True,
        )
        return

    print(f"[agent] {type(message).__name__}: {message!r}", flush=True)


def _build_problem_store(source_image: str | None, saved: list[dict]):
    @tool(
        "save_problem",
        (
            "Persist a single math problem (with category, difficulty, and "
            "solution) to storage. Call once per distinct problem. "
            "`difficulty` must be one of: elementary, middle school, "
            "high school, undergraduate, graduate, olympiad."
        ),
        {
            "problem_text": str,
            "category": str,
            "difficulty": str,
            "solution": str,
        },
    )
    async def save_problem(args: dict) -> dict:
        record = storage.save_problem(
            problem_text=args["problem_text"],
            category=args["category"],
            difficulty=args["difficulty"],
            solution=args["solution"],
            source_image=source_image,
        )
        saved.append(record)
        return {
            "content": [
                {"type": "text", "text": f"Saved problem {record['id']}."}
            ]
        }

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_problem],
    )


async def _process_image_async(
    image_path: Path, source_image: str | None
) -> dict:
    saved: list[dict] = []
    server = _build_problem_store(source_image, saved)

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"problem_store": server},
        allowed_tools=["Read", "mcp__problem_store__save_problem"],
        max_turns=MAX_TURNS,
    )

    prompt = (
        f"Read the image at {image_path} and extract every math problem in "
        "it. For each distinct problem, call the "
        "`mcp__problem_store__save_problem` tool with the structured fields. "
        "When done, reply with a short summary of what you saved."
    )

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

    return {"saved": saved, "summary": final_text or "[no summary returned]"}


def process_image(
    image_path: Path,
    source_image: str | None = None,
) -> dict:
    """Run the Agent SDK on a local image file. Returns saved problems + summary."""
    return asyncio.run(_process_image_async(Path(image_path), source_image))
