"""In-process MCP server backing both stages of the agent pipeline.

Two modes:

- `mode="parsed"` (stage 1, image scan): exposes `save_parsed_problem`.
  The orchestrator calls it once per problem extracted from the source.
  Each call crops the figure (if any) and persists a partial problem JSON
  with placeholder category `unclassified` and empty solution. Stage 2
  finds these by `(source_image, category='unclassified')` and fills them
  in. Duplicate seq_no calls for the same source_image are skipped so the
  scan stage can safely retry.

- `mode="solved"` (stage 2, problem solve): exposes `save_problem` and
  `lookup_category_edits`. Like the original solver flow, but updates the
  existing partial problem (identified by `existing_problem_id`) instead
  of inserting a new one. `lookup_category_edits` must be called first to
  pull in any prior user corrections, replacing the older two-pass
  reviewer agent.
"""

from typing import Literal

from claude_agent_sdk import create_sdk_mcp_server, tool
from common import figures, storage

CATEGORY_EDIT_EXAMPLES_LIMIT = 5
UNCLASSIFIED_CATEGORY = "unclassified"


def build_problem_store(
    source_image: str | None,
    saved: list[storage.Problem],
    *,
    mode: Literal["parsed", "solved"] = "solved",
    # solved-mode inputs:
    existing_problem_id: str | None = None,
    with_solution: bool = True,
):
    """Return an MCP server bound to one source image (parsed mode) or one
    partial problem record (solved mode). `saved` is the out-param the
    caller reads after the agent finishes."""
    if mode == "parsed":
        return _build_parsed_server(source_image, saved)
    if mode == "solved":
        if existing_problem_id is None:
            raise ValueError(
                "solved mode requires existing_problem_id (the partial "
                "saved by the scan stage)"
            )
        return _build_solved_server(
            saved=saved,
            existing_problem_id=existing_problem_id,
            with_solution=with_solution,
        )
    raise ValueError(f"unknown mode: {mode!r}")


def _build_parsed_server(
    source_image: str | None,
    saved: list[storage.Problem],
):
    save_parsed_description = (
        "Persist one extracted math problem (text + optional figure) as "
        "a partial record. Call once per distinct problem found in the "
        "source. Do NOT solve or classify — that runs in a later stage. "
        "Provide `figure_bbox` as `[x0, y0, x1, y1]` normalized to [0, 1] "
        "tightly enclosing just the figure, or `[]` if none. "
        "`figure_rotation` is 0/90/180/270 clockwise degrees to upright "
        "the crop (0 if no figure). `figure_page` is the 1-indexed page "
        "the figure lives on (1 if no figure)."
    )
    save_parsed_schema = {
        "problem_text": str,
        "source_exam": str,
        "subexam": str,
        "year": str,
        "source_page": int,
        "seq_no": int,
        "figure_bbox": list,
        "figure_rotation": int,
        "figure_page": int,
    }

    @tool("save_parsed_problem", save_parsed_description, save_parsed_schema)
    async def save_parsed_problem(args: dict) -> dict:
        seq_no_raw = args.get("seq_no")
        seq_no = int(seq_no_raw) if seq_no_raw is not None else None
        if seq_no is not None and source_image:
            # The scan stage may retry; skip seq_nos already persisted for
            # this source_image so we don't pile up duplicates.
            if seq_no in storage.existing_seq_nos(source_image):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Skipped seq_no={seq_no}: already saved for "
                                f"source_image={source_image!r}."
                            ),
                        }
                    ]
                }

        bbox = args.get("figure_bbox") or []
        rotation = int(args.get("figure_rotation") or 0)
        figure_page = int(args.get("figure_page") or 1)
        figure_image: str | None = None
        saved_bbox: list[float] | None = None
        saved_figure_page: int | None = None
        if bbox and source_image:
            figure_image = figures.save_figure(
                source_image, bbox, rotation=rotation, page=figure_page
            )
            saved_bbox = [float(v) for v in bbox]
            saved_figure_page = figure_page

        source_exam = (args.get("source_exam") or "Unknown").strip() or "Unknown"
        subexam = (args.get("subexam") or "").strip()
        year = str(args.get("year") or "Unknown").strip() or "Unknown"
        source_page_raw = args.get("source_page")
        source_page = int(source_page_raw) if source_page_raw is not None else None

        problem = storage.save_problem(
            problem_text=args["problem_text"],
            category=UNCLASSIFIED_CATEGORY,
            subcategory="",
            solution="",
            source_image=source_image,
            source_page=source_page,
            seq_no=seq_no,
            source_exam=source_exam,
            subexam=subexam,
            year=year,
            figure_image=figure_image,
            figure_bbox=saved_bbox,
            figure_page=saved_figure_page,
        )
        saved.append(problem)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Saved partial problem {problem.id} "
                        f"(seq_no={seq_no})."
                    ),
                }
            ]
        }

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_parsed_problem],
    )


def _build_solved_server(
    *,
    saved: list[storage.Problem],
    existing_problem_id: str,
    with_solution: bool,
):
    lookup_called = {"value": False}

    if with_solution:
        save_description = (
            "Finalize the classification and solution for this problem on "
            "the existing partial record. Call exactly once, AFTER "
            "`lookup_category_edits` has been called for your chosen "
            "category/subcategory."
        )
        save_schema = {
            "problem_text": str,
            "category": str,
            "subcategory": str,
            "solution": str,
        }
    else:
        save_description = (
            "Finalize the classification on this problem's existing "
            "partial record (no solution requested). Call once, AFTER "
            "`lookup_category_edits`."
        )
        save_schema = {
            "problem_text": str,
            "category": str,
            "subcategory": str,
        }

    @tool("save_problem", save_description, save_schema)
    async def save_problem(args: dict) -> dict:
        if not lookup_called["value"]:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Refusing save: call `lookup_category_edits` "
                            "with your chosen category/subcategory first to "
                            "check for prior user corrections, then call "
                            "`save_problem`."
                        ),
                    }
                ],
                "is_error": True,
            }
        updates: dict = {
            "problem_text": args["problem_text"],
            "category": args["category"],
            "subcategory": args.get("subcategory", "") or "",
            "solution": args.get("solution", "") or "",
        }
        if not with_solution:
            # Backward-compat: schema no longer asks the LLM to estimate.
            updates["solve_time_estimated"] = 60
        problem = storage.update_problem(existing_problem_id, **updates)
        saved.append(problem)
        return {
            "content": [
                {"type": "text", "text": f"Updated problem {problem.id}."}
            ]
        }

    @tool(
        "lookup_category_edits",
        (
            "Look up past user corrections that moved problems AWAY from a "
            "candidate (category, subcategory). Call this EXACTLY ONCE with "
            "your tentatively chosen pair BEFORE `save_problem`. If the "
            "returned examples reveal a consistent correction pattern that "
            "matches the new problem, switch to the user-picked values in "
            "`save_problem`; otherwise keep yours. An empty result means no "
            "prior edits — keep your choice and proceed. Pass an empty "
            "string for `subcategory` if you have not chosen one."
        ),
        {"category": str, "subcategory": str},
    )
    async def lookup_category_edits(args: dict) -> dict:
        lookup_called["value"] = True
        category = args.get("category", "")
        subcategory = args.get("subcategory", "") or None
        examples = storage.category_edit_examples(
            category,
            limit=CATEGORY_EDIT_EXAMPLES_LIMIT,
            from_subcategory=subcategory,
        )
        pair = f"'{category}'"
        if subcategory:
            pair = f"'{category} / {subcategory}'"
        if not examples:
            text = (
                f"No prior user edits away from {pair}. Keep your choice "
                "and proceed to `save_problem`."
            )
        else:
            lines = [f"{len(examples)} past user correction(s) away from {pair}:"]
            for ex in examples:
                src = ex["from_category"]
                if ex.get("from_subcategory"):
                    src += f" / {ex['from_subcategory']}"
                dst = ex["to_category"]
                if ex.get("to_subcategory"):
                    dst += f" / {ex['to_subcategory']}"
                lines.append(
                    f"- moved '{src}' -> '{dst}': {ex['problem_text']}"
                )
            lines.append(
                "If the new problem fits this pattern, save with the "
                "corrected values; otherwise keep yours."
            )
            text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_problem, lookup_category_edits],
    )
