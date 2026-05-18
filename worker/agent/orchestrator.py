"""Top-level orchestrator: one LLM call parses every problem out of the
source image/PDF into a structured list, then a plain Python loop fans the
list out to the solver with bounded concurrency."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)
from common import storage
from common.agent_util import MAX_BUFFER_SIZE, MODEL, log_message

from ..quota import QuotaHit, detect_in_message as detect_quota_in_message, later_reset
from .solver import solve_problem

ORCHESTRATOR_MAX_TURNS = 4
# Concurrency cap for fan-out. Each inner solver is its own SDK session, so
# this also caps simultaneous API calls — keep modest to stay under the
# five-hour rate limit on Sonnet.
SOLVER_CONCURRENCY = 4

# Orchestrator prompt is worker-local — only this orchestrator reads it.
# Solver prompt stays in webapp/src/prompts/ because refine.md includes it.
WORKER_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
ORCHESTRATOR_SYSTEM_PROMPT = (WORKER_PROMPTS_DIR / "orchestrator.md").read_text()


class ProcessImageInput(NamedTuple):
    image_path: Path
    source_image: str | None


class ProcessImageResult(NamedTuple):
    saved: list[storage.Problem]
    # `complete` is False when we know we didn't save every problem we
    # tried to (a solver swallowed a quota/network error, or the orchestrator
    # parse itself failed). The worker uses this to revert the row to
    # pending so it retries instead of being marked done with partial work.
    complete: bool
    summary: str
    # `hit_quota_limit` is True if any solver / the parse step saw a
    # rejected `RateLimitEvent`. `quota_reset_at` is the furthest-out
    # reset timestamp we saw (UTC). The worker uses these to sleep until
    # the quota window opens up before its next scan.
    hit_quota_limit: bool = False
    quota_reset_at: datetime | None = None


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
        max_buffer_size=MAX_BUFFER_SIZE,
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
        quota = detect_quota_in_message(message)
        if quota is not None:
            print(f"[orchestrator] quota hit during parse: {quota.detail}", flush=True)
            raise quota
    print(f"[orchestrator] parsed {len(parsed)} problem(s)", flush=True)
    return parsed


def _dedup_against_existing(
    parsed: list[dict], source_image: str | None
) -> tuple[list[dict], int]:
    # Stamp each parsed problem with its 1-indexed position in the source.
    # That position is its seq_no — the stable identity within source_image.
    for idx, p in enumerate(parsed, start=1):
        p["seq_no"] = idx

    if not source_image:
        return parsed, 0
    already = storage.existing_seq_nos(source_image)
    if not already:
        return parsed, 0
    kept = [p for p in parsed if p["seq_no"] not in already]
    skipped = len(parsed) - len(kept)
    if skipped:
        print(
            f"[orchestrator] dedup: skipping {skipped} problem(s) "
            f"already saved for source_image={source_image!r}",
            flush=True,
        )
    return kept, skipped


async def _process_image_async(
    image_path: Path,
    source_image: str | None,
    with_solution: bool = True,
) -> ProcessImageResult:
    try:
        parsed = await _parse_problems(image_path)
    except QuotaHit as q:
        return ProcessImageResult(
            saved=[],
            complete=False,
            summary=f"quota hit during parse: {q.detail}",
            hit_quota_limit=True,
            quota_reset_at=q.reset_at,
        )
    if not parsed:
        return ProcessImageResult(
            saved=[], complete=True, summary="No problems found."
        )

    parsed, skipped = _dedup_against_existing(parsed, source_image)

    if not parsed:
        return ProcessImageResult(
            saved=[],
            complete=True,
            summary=f"All {skipped} problem(s) already saved; nothing to do.",
        )

    expected = len(parsed)
    sem = asyncio.Semaphore(SOLVER_CONCURRENCY)

    async def run_one(p: dict) -> tuple[storage.Problem | None, QuotaHit | None]:
        async with sem:
            try:
                problem = await solve_problem(
                    p, source_image, with_solution=with_solution
                )
                return problem, None
            except QuotaHit as q:
                print(
                    f"[orchestrator] solver quota hit for "
                    f"{p.get('problem_text', '')[:80]!r}: {q.detail}",
                    flush=True,
                )
                return None, q
            except Exception as e:
                print(
                    f"[orchestrator] solver failed for "
                    f"{p.get('problem_text', '')[:80]!r}: {e}",
                    flush=True,
                )
                return None, None

    outcomes = await asyncio.gather(*(run_one(p) for p in parsed))
    saved = [problem for problem, _ in outcomes if problem is not None]
    quota_hits = [q for _, q in outcomes if q is not None]
    hit_quota_limit = bool(quota_hits)
    quota_reset_at: datetime | None = None
    for q in quota_hits:
        quota_reset_at = later_reset(quota_reset_at, q.reset_at)

    complete = len(saved) == expected
    summary = f"Saved {len(saved)} of {expected} problem(s)."
    if not complete:
        summary += f" (incomplete: {expected - len(saved)} failed)"
    if skipped:
        summary += f" (skipped {skipped} already-saved)"
    if hit_quota_limit:
        summary += (
            f" (quota hit on {len(quota_hits)} solver(s); "
            f"resets_at={quota_reset_at})"
        )
    return ProcessImageResult(
        saved=saved,
        complete=complete,
        summary=summary,
        hit_quota_limit=hit_quota_limit,
        quota_reset_at=quota_reset_at,
    )


async def _process_images_async(
    inputs: list[ProcessImageInput],
    with_solution: bool = True,
) -> ProcessImageResult:
    """Process each file sequentially in its own agent session. Files are
    unrelated, so we never share a parse/solve session across them; a
    failure on one file does not abort the others."""
    all_saved: list[storage.Problem] = []
    per_file_summaries: list[str] = []
    all_complete = True
    hit_quota_limit = False
    quota_reset_at: datetime | None = None
    for idx, inp in enumerate(inputs, start=1):
        label = inp.source_image or inp.image_path.name
        print(
            f"[orchestrator] file {idx}/{len(inputs)}: {label}",
            flush=True,
        )
        try:
            result = await _process_image_async(
                inp.image_path, inp.source_image, with_solution=with_solution
            )
        except Exception as e:
            print(
                f"[orchestrator] file {idx}/{len(inputs)} failed: {e}",
                flush=True,
            )
            per_file_summaries.append(f"{label}: error ({e})")
            all_complete = False
            continue
        all_saved.extend(result.saved)
        per_file_summaries.append(f"{label}: {result.summary}")
        if not result.complete:
            all_complete = False
        if result.hit_quota_limit:
            hit_quota_limit = True
            quota_reset_at = later_reset(quota_reset_at, result.quota_reset_at)

    summary = (
        f"Processed {len(inputs)} file(s); saved {len(all_saved)} "
        f"problem(s) total."
    )
    if per_file_summaries:
        summary += " | " + " | ".join(per_file_summaries)
    return ProcessImageResult(
        saved=all_saved,
        complete=all_complete,
        summary=summary,
        hit_quota_limit=hit_quota_limit,
        quota_reset_at=quota_reset_at,
    )


def process_images(
    inputs: list[ProcessImageInput],
    with_solution: bool = True,
) -> ProcessImageResult:
    return asyncio.run(
        _process_images_async(inputs, with_solution=with_solution)
    )


def process_image(
    image_path: Path,
    source_image: str | None = None,
    with_solution: bool = True,
) -> ProcessImageResult:
    return process_images(
        [ProcessImageInput(image_path=Path(image_path), source_image=source_image)],
        with_solution=with_solution,
    )
