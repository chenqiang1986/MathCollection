"""In-process MCP server exposing `save_problem` and `lookup_category_edits`
to the inner solver.

`lookup_category_edits` lets the solver consult prior user corrections that
moved problems AWAY from a candidate (category, subcategory) pair, and
switch its own choice in the same agent context — replacing the older
two-pass design where a separate reviewer agent ran after save.
`save_problem` refuses the first call until `lookup_category_edits` has
been invoked at least once, so the solver cannot skip the check on a
confident classification."""

from claude_agent_sdk import create_sdk_mcp_server, tool

from lib import storage

CATEGORY_EDIT_EXAMPLES_LIMIT = 5


def build_problem_store(
    source_image: str | None,
    saved: list[storage.Problem],
    source_page: int | None = None,
    source_exam: str = "Unknown",
    year: str = "Unknown",
    figure_image: str | None = None,
    figure_bbox: list[float] | None = None,
    with_solution: bool = True,
):
    lookup_called = {"value": False}

    if with_solution:
        save_description = (
            "Persist a single solved math problem to storage. Call once per "
            "distinct problem, AFTER `lookup_category_edits` has been called "
            "for your chosen category/subcategory."
        )
        save_schema = {
            "problem_text": str,
            "category": str,
            "subcategory": str,
            "solution": str,
        }
    else:
        save_description = (
            "Persist a single math problem to storage without a solution. "
            "Call once per distinct problem, AFTER `lookup_category_edits` "
            "has been called for your chosen category/subcategory. "
            "`solve_time_estimated` is your own estimate of how long you "
            "would take to solve the problem step by step (integer "
            "seconds, calibrated to typical Sonnet response time)."
        )
        save_schema = {
            "problem_text": str,
            "category": str,
            "subcategory": str,
            "solve_time_estimated": int,
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
        estimated_time = args.get("solve_time_estimated")
        problem = storage.save_problem(
            problem_text=args["problem_text"],
            category=args["category"],
            subcategory=args.get("subcategory", ""),
            solution=args.get("solution", ""),
            source_image=source_image,
            source_page=source_page,
            source_exam=source_exam,
            year=year,
            figure_image=figure_image,
            figure_bbox=figure_bbox,
            solve_time_estimated=(
                int(round(float(estimated_time))) if estimated_time is not None else 0
            ),
        )
        saved.append(problem)
        return {
            "content": [
                {"type": "text", "text": f"Saved problem {problem.id}."}
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
