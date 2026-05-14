"""Second-pass category reviewer.

After the solver classifies a freshly extracted problem, if past users have
edited the category AWAY from the same label, ask a small no-tools agent
whether the same correction should apply here. Switches the category in
place (via storage.update_problem) when the model says so."""

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from jinja2 import Template
from lib import storage

from .util import MODEL, PROMPTS_DIR, log_message

RECATEGORIZE_MAX_TURNS = 1
EXAMPLES_LIMIT = 5

_RECATEGORIZE_TEMPLATE = Template((PROMPTS_DIR / "recategorize.md").read_text())


def _parse_decision(text: str) -> str | None:
    """Return the new category if the model said `SWITCH: <cat>`, else None."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "KEEP":
            return None
        if upper.startswith("SWITCH:"):
            candidate = line.split(":", 1)[1].strip().strip("`'\"").lower()
            return candidate or None
    return None


async def _ask_async(problem: storage.Problem, examples: list[dict]) -> str | None:
    system_prompt = _RECATEGORIZE_TEMPLATE.render(
        ai_category=problem.category, examples=examples
    )
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=system_prompt,
        allowed_tools=[],
        max_turns=RECATEGORIZE_MAX_TURNS,
    )
    user_prompt = f"New problem:\n{problem.problem_text}"

    decision_text = ""
    async for message in query(prompt=user_prompt, options=options):
        log_message(message)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    decision_text += block.text + "\n"
    return _parse_decision(decision_text)


async def maybe_recategorize_async(problem: storage.Problem) -> storage.Problem:
    examples = storage.category_edit_examples(problem.category, limit=EXAMPLES_LIMIT)
    if not examples:
        return problem
    print(
        f"[recategorize] {len(examples)} prior edits away from "
        f"'{problem.category}'; consulting reviewer",
        flush=True,
    )
    try:
        new_category = await _ask_async(problem, examples)
    except Exception as e:
        print(f"[recategorize] reviewer error: {e}", flush=True)
        return problem
    if not new_category or new_category == (problem.category or "").lower():
        print("[recategorize] KEEP", flush=True)
        return problem
    print(
        f"[recategorize] SWITCH '{problem.category}' -> '{new_category}'",
        flush=True,
    )
    return storage.update_problem(problem.id, category=new_category)
