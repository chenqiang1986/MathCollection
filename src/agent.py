from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

import storage


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


def _build_problem_store(
    source_image: str | None,
    saved: list[dict],
    diagram_svg: str | None = None,
):
    @tool(
        "save_problem",
        (
            "Persist a single math problem (with category, difficulty, and "
            "solution) to storage. Call once per distinct problem. "
            "`difficulty` must be one of: elementary, middle school, "
            "high school, undergraduate, graduate, olympiad. Pass "
            "`solution_svg` (an inline SVG showing the original figure plus "
            "any auxiliary constructions you drew) only when your solution "
            "introduces new points/lines/circles; otherwise pass an empty "
            "string."
        ),
        {
            "problem_text": str,
            "category": str,
            "difficulty": str,
            "solution": str,
            "solution_svg": str,
        },
    )
    async def save_problem(args: dict) -> dict:
        sol_svg = (args.get("solution_svg") or "").strip() or None
        record = storage.save_problem(
            problem_text=args["problem_text"],
            category=args["category"],
            difficulty=args["difficulty"],
            solution=args["solution"],
            source_image=source_image,
            diagram_svg=diagram_svg,
            solution_svg=sol_svg,
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
