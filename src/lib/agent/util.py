"""Shared agent helpers: model id, prompts path, message truncation/logging."""

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

MODEL = "claude-sonnet-4-6"
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# SDK stdio JSON buffer cap. The default (1 MiB) overflows on multi-page PDFs
# whose Read tool results come back as a single base64-bearing JSON message.
MAX_BUFFER_SIZE = 32 * 1024 * 1024


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
