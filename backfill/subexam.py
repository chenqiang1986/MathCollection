"""Backfill the `subexam` field (BMT algebra/discrete/calculus/..., MathCounts
sprint/target/team, etc.) on existing problems.

Two phases:

1. Scan every raw source file under `data/<user>/raw/` with a one-shot
   agent that reads the file (first page is enough for PDFs) and returns
   `{"source_exam": ..., "subexam": ...}`. The result is collected into a
   `{raw_filename: subexam}` map.
2. Walk every problem under the current user and update those whose
   `source_image` is in the map AND whose stored `subexam` is empty
   (or `--mode all` to overwrite). Per-problem JSON + SQL index get
   updated in lockstep via `storage.update_problem`.

Source-exam is only used during scanning as context for the LLM — we
intentionally don't overwrite the existing `source_exam` on problems
here, since the orchestrator already captured it. Add `--update-exam`
if you want to fix legacy `Unknown` source_exam values too.
"""

import asyncio
from pathlib import Path
from typing import Literal

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)
from common import storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, log_message

Mode = Literal["missing", "all"]

# Cap concurrent scans — each is its own SDK session reading a PDF.
SCAN_CONCURRENCY = 4

_SYSTEM = (
    "You identify which math competition and which sub-event/round a "
    "source document belongs to. Read the file with the `Read` tool "
    "(first page is sufficient for PDFs — pass `pages=\"1\"`), then "
    "call `mcp__subexam__save_subexam_info` EXACTLY ONCE with the "
    "result. Do not reply with prose.\n\n"
    "Field rules:\n"
    "- `source_exam`: use EXACTLY one of these canonical values "
    "(case-sensitive): AMC8, AMC10A, AMC10B, AMC12A, AMC12B, AIME, BMT, "
    "HMMT, ARML, MathCounts, PiMathContest, Putnam. Map variants to "
    "their canonical form: MATHCOUNTS / Mathcounts / Math Counts → "
    "MathCounts; PiMC / Pi Math Contest / PMC → PiMathContest. If the "
    "contest isn't in this list, use the short form without spaces as "
    "it appears in the document. Use `Unknown` if not indicated.\n"
    "- `subexam`: the named round/test within the competition, "
    "lowercase short form. Examples:\n"
    "  - BMT → general, algebra, discrete, calculus, geometry, team\n"
    "  - MathCounts → sprint, target, team, countdown\n"
    "  - HMMT → general, algebra, combinatorics, geometry, team, guts\n"
    "  - ARML → individual, relay, team, power, super-relay\n"
    "  Use empty string \"\" when the competition has no sub-rounds "
    "(AMC10/12, AIME) or when the document does not indicate one."
)


def _build_subexam_store(out: dict) -> object:
    """Returns an MCP server whose one tool stashes the agent's answer
    into `out` (a closure-bound dict the caller reads after the agent
    finishes)."""

    @tool(
        "save_subexam_info",
        (
            "Persist the identified competition (`source_exam`) and "
            "sub-round (`subexam`) for the source file you just read. "
            "Call exactly once per agent run."
        ),
        {"source_exam": str, "subexam": str},
    )
    async def save_subexam_info(args: dict) -> dict:
        exam = storage.canonicalize_source_exam(args.get("source_exam"))
        sub = (args.get("subexam") or "").strip().lower()
        out["source_exam"] = exam
        out["subexam"] = sub
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Saved source_exam={exam!r} subexam={sub!r}.",
                }
            ]
        }

    return create_sdk_mcp_server(
        name="subexam",
        version="1.0.0",
        tools=[save_subexam_info],
    )


async def _scan_one(path: Path) -> tuple[str, str] | None:
    captured: dict = {}
    server = _build_subexam_store(captured)
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=_SYSTEM,
        mcp_servers={"subexam": server},
        allowed_tools=["Read", "mcp__subexam__save_subexam_info"],
        max_turns=10,
        max_buffer_size=MAX_BUFFER_SIZE,
    )
    prompt = (
        f"Read the file at {path}. If it is a PDF, read only the first "
        "page (pages=\"1\"). Then call "
        "`mcp__subexam__save_subexam_info` with the identified "
        "`source_exam` and `subexam`."
    )
    async for message in query(prompt=prompt, options=options):
        log_message(message)
    if "source_exam" not in captured:
        return None
    return captured["source_exam"], captured.get("subexam", "")


async def _scan_raw_files(
    raw_dir: Path,
) -> dict[str, tuple[str, str]]:
    files = sorted(p for p in raw_dir.iterdir() if p.is_file())
    print(
        f"[backfill-subexam] scanning {len(files)} raw file(s) under {raw_dir}",
        flush=True,
    )
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def run_one(path: Path) -> tuple[str, tuple[str, str] | None]:
        async with sem:
            try:
                result = await _scan_one(path)
            except Exception as e:
                print(
                    f"[backfill-subexam] {path.name}: ERROR {e}", flush=True
                )
                return path.name, None
            if result is None:
                print(
                    f"[backfill-subexam] {path.name}: unparseable response",
                    flush=True,
                )
            else:
                exam, sub = result
                print(
                    f"[backfill-subexam] {path.name}: "
                    f"exam={exam!r} subexam={sub!r}",
                    flush=True,
                )
            return path.name, result

    outcomes = await asyncio.gather(*(run_one(p) for p in files))
    return {name: result for name, result in outcomes if result is not None}


def _apply_to_problems(
    file_map: dict[str, tuple[str, str]],
    mode: Mode,
    dry_run: bool,
    update_exam: bool,
) -> tuple[int, int, int]:
    """Returns (targeted, updated, skipped_no_match)."""
    problems = storage.list_problems()
    targeted = 0
    updated = 0
    skipped_no_match = 0
    for p in problems:
        src = (p.source_image or "").strip()
        if not src:
            continue
        if src not in file_map:
            skipped_no_match += 1
            continue
        exam, sub = file_map[src]
        targeted += 1
        existing_sub = (p.subexam or "").strip()
        if mode == "missing" and existing_sub:
            continue
        updates: dict = {}
        if sub != existing_sub:
            updates["subexam"] = sub
        if update_exam:
            existing_exam = (p.source_exam or "").strip()
            if exam and exam != existing_exam and existing_exam in ("", "Unknown"):
                updates["source_exam"] = exam
        if not updates:
            continue
        print(
            f"[backfill-subexam] {p.id} ({src}): "
            f"{ {k: (getattr(p, k, None), v) for k, v in updates.items()} }"
            + (" [dry-run]" if dry_run else ""),
            flush=True,
        )
        if not dry_run:
            storage.update_problem(p.id, **updates)
            updated += 1
    return targeted, updated, skipped_no_match


async def _backfill_async(
    mode: Mode, dry_run: bool, update_exam: bool
) -> tuple[int, int, int, int]:
    raw_dir = storage.raw_uploads_dir()
    if not raw_dir.exists():
        print(f"[backfill-subexam] no raw dir at {raw_dir}", flush=True)
        return 0, 0, 0, 0
    file_map = await _scan_raw_files(raw_dir)
    print(
        f"[backfill-subexam] built map for {len(file_map)} file(s); "
        "applying to problems…",
        flush=True,
    )
    targeted, updated, skipped = _apply_to_problems(
        file_map, mode=mode, dry_run=dry_run, update_exam=update_exam
    )
    return len(file_map), targeted, updated, skipped


def backfill_subexams(
    mode: Mode = "missing",
    dry_run: bool = False,
    update_exam: bool = False,
) -> tuple[int, int, int, int]:
    """Returns (files_scanned, problems_targeted, problems_updated,
    problems_skipped_no_match_in_map)."""
    return asyncio.run(_backfill_async(mode, dry_run, update_exam))
