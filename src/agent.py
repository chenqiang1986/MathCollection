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


def log_message(message) -> None:
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


def build_problem_store(
    source_image: str | None,
    saved: list[storage.Problem],
    figure_image: str | None = None,
    with_solution: bool = True,
):
    if with_solution:
        description = (
            "Persist a single solved math problem to storage. Call once per "
            "distinct problem."
        )
        schema = {
            "problem_text": str,
            "category": str,
            "solution": str,
        }
    else:
        description = (
            "Persist a single math problem to storage without a solution. "
            "Call once per distinct problem. `solve_time_seconds` is your "
            "own estimate of how long you would take to solve the problem "
            "step by step (in seconds, calibrated to typical Sonnet "
            "response time)."
        )
        schema = {
            "problem_text": str,
            "category": str,
            "solve_time_seconds": float,
        }

    @tool("save_problem", description, schema)
    async def save_problem(args: dict) -> dict:
        estimated_time = args.get("solve_time_seconds")
        problem = storage.save_problem(
            problem_text=args["problem_text"],
            category=args["category"],
            solution=args.get("solution", ""),
            source_image=source_image,
            figure_image=figure_image,
            solve_time_seconds=(
                float(estimated_time) if estimated_time is not None else None
            ),
            solve_time_estimated=not with_solution,
        )
        saved.append(problem)
        return {
            "content": [
                {"type": "text", "text": f"Saved problem {problem.id}."}
            ]
        }

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_problem],
    )
