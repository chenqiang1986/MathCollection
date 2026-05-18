"""Re-classify category and subcategory on existing problems via a
single-turn call against the closed vocabulary in
`prompts/math_category.md`. Cheap relative to `process_image` — no
solution generation, no tool calls."""

import asyncio
import json
import re
from typing import Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from common import storage
from common.agent_util import MODEL, PROMPTS_DIR

Mode = Literal["missing", "all"]

_MATH_CATEGORY_MD = (PROMPTS_DIR / "math_category.md").read_text()

_SYSTEM = (
    "You classify a math problem into exactly one (category, subcategory) "
    "pair drawn from the closed list below. Reply with ONLY a single JSON "
    'object: `{"category": "...", "subcategory": "..."}`. Use the exact '
    "strings from the list — lowercase, no rephrasing, no synonyms. If no "
    "subcategory fits cleanly, use `other` within the best-matching "
    "category. No prose, no markdown fences.\n\n"
) + _MATH_CATEGORY_MD


def _parse_allowed_pairs(md: str) -> dict[str, set[str]]:
    pairs: dict[str, set[str]] = {}
    current: str | None = None
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip().lower()
            pairs.setdefault(current, set())
            continue
        m = re.match(r"^-\s+(.+?)\s*$", line)
        if m and current:
            pairs[current].add(m.group(1).strip().lower())
    return pairs


_ALLOWED = _parse_allowed_pairs(_MATH_CATEGORY_MD)


def _parse_response(raw: str) -> tuple[str, str] | None:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    cat = str(data.get("category", "")).strip().lower()
    sub = str(data.get("subcategory", "")).strip().lower()
    if not cat:
        return None
    return cat, sub


def _is_allowed(cat: str, sub: str) -> bool:
    if cat not in _ALLOWED:
        return False
    return sub in _ALLOWED[cat]


async def _classify_one(problem_text: str) -> tuple[str, str] | None:
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_SYSTEM,
        allowed_tools=[],
        max_turns=1,
    )
    prompt = f"Problem:\n{problem_text}\n\nClassification (JSON):"
    raw = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    raw += block.text
    return _parse_response(raw)


async def _backfill_async(mode: Mode, dry_run: bool) -> tuple[int, int]:
    problems = storage.list_problems()
    if mode == "missing":
        targets = [
            p
            for p in problems
            if not (p.category or "").strip()
            or not (p.subcategory or "").strip()
        ]
    else:
        targets = list(problems)
    print(
        f"[backfill] mode={mode}: {len(targets)} of {len(problems)} targeted.",
        flush=True,
    )
    updated = 0
    for i, p in enumerate(targets, 1):
        try:
            result = await _classify_one(p.problem_text)
        except Exception as e:
            print(f"[backfill] {i}/{len(targets)} {p.id}: ERROR {e}", flush=True)
            continue
        if not result:
            print(
                f"[backfill] {i}/{len(targets)} {p.id}: "
                "empty/unparseable, skipping",
                flush=True,
            )
            continue
        new_cat, new_sub = result
        if not _is_allowed(new_cat, new_sub):
            print(
                f"[backfill] {i}/{len(targets)} {p.id}: REJECTED out-of-vocab "
                f"({new_cat!r}, {new_sub!r}), skipping",
                flush=True,
            )
            continue
        old_cat = (p.category or "").strip().lower()
        old_sub = (p.subcategory or "").strip().lower()
        if new_cat == old_cat and new_sub == old_sub:
            print(
                f"[backfill] {i}/{len(targets)} {p.id}: unchanged "
                f"({old_cat}/{old_sub})",
                flush=True,
            )
            continue
        print(
            f"[backfill] {i}/{len(targets)} {p.id}: "
            f"({old_cat}/{old_sub}) -> ({new_cat}/{new_sub})"
            + (" [dry-run]" if dry_run else ""),
            flush=True,
        )
        if not dry_run:
            storage.update_problem(p.id, category=new_cat, subcategory=new_sub)
            updated += 1
    return len(targets), updated


def classify_problems(
    mode: Mode = "missing", dry_run: bool = False
) -> tuple[int, int]:
    return asyncio.run(_backfill_async(mode, dry_run))
