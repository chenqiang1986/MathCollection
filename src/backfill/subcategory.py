"""Backfill missing subcategories on existing problems via a single-turn
classification call. Cheap relative to `process_image` — no solution
generation, no tool calls."""

import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from lib import storage
from lib.agent.util import MODEL

_SYSTEM = (
    "You assign a single short subcategory label to a math problem given its "
    "top-level category. Reply with ONLY the lowercase subcategory string "
    "(one to three words, no punctuation, no quotes, no explanation). "
    "Examples: for category 'algebra' → 'binomial', 'polynomial', "
    "'inequalities'; for 'geometry' → 'analytical geometry', "
    "'triangles', 'circles'; for 'calculus' → 'limits', 'derivatives', "
    "'integration'."
)


async def _classify_one(problem_text: str, category: str) -> str:
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_SYSTEM,
        allowed_tools=[],
        max_turns=1,
    )
    prompt = (
        f"Category: {category}\n\nProblem:\n{problem_text}\n\n"
        "Subcategory:"
    )
    subcategory = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    subcategory += block.text
    return _normalize(subcategory)


def _normalize(raw: str) -> str:
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    return line.strip().strip("'\"`").lower()


async def _backfill_async(dry_run: bool) -> tuple[int, int]:
    problems = storage.list_problems()
    targets = [p for p in problems if not (p.subcategory or "").strip()]
    print(
        f"[backfill] {len(targets)} of {len(problems)} problems need a subcategory.",
        flush=True,
    )
    updated = 0
    for i, p in enumerate(targets, 1):
        try:
            sub = await _classify_one(p.problem_text, p.category)
        except Exception as e:
            print(f"[backfill] {i}/{len(targets)} {p.id}: ERROR {e}", flush=True)
            continue
        if not sub:
            print(
                f"[backfill] {i}/{len(targets)} {p.id}: empty response, skipping",
                flush=True,
            )
            continue
        print(
            f"[backfill] {i}/{len(targets)} {p.id} ({p.category}) -> {sub}"
            + (" [dry-run]" if dry_run else ""),
            flush=True,
        )
        if not dry_run:
            storage.update_problem(p.id, subcategory=sub)
            updated += 1
    return len(targets), updated


def backfill_subcategory(dry_run: bool = False) -> tuple[int, int]:
    return asyncio.run(_backfill_async(dry_run))
