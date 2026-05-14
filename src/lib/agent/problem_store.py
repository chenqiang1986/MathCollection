"""In-process MCP server exposing `save_problem` and `lookup_category_edits`
to the inner solver.

`lookup_category_edits` lets the solver consult prior user corrections that
moved problems AWAY from a candidate category, and switch its own choice in
the same agent context — replacing the older two-pass design where a
separate reviewer agent ran after save. `save_problem` refuses the first
call until `lookup_category_edits` has been invoked at least once, so the
solver cannot skip the check on a confident classification."""

from claude_agent_sdk import create_sdk_mcp_server, tool

from lib import storage

CATEGORY_EDIT_EXAMPLES_LIMIT = 5


def build_problem_store(
    source_image: str | None,
    saved: list[storage.Problem],
    figure_image: str | None = None,
    with_solution: bool = True,
):
    lookup_called = {"value": False}

    if with_solution:
        save_description = (
            "Persist a single solved math problem to storage. Call once per "
            "distinct problem, AFTER `lookup_category_edits` has been called "
            "for your chosen category."
        )
        save_schema = {
            "problem_text": str,
            "category": str,
            "solution": str,
        }
    else:
        save_description = (
            "Persist a single math problem to storage without a solution. "
            "Call once per distinct problem, AFTER `lookup_category_edits` "
            "has been called for your chosen category. `solve_time_seconds` "
            "is your own estimate of how long you would take to solve the "
            "problem step by step (in seconds, calibrated to typical Sonnet "
            "response time)."
        )
        save_schema = {
            "problem_text": str,
            "category": str,
            "solve_time_seconds": float,
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
                            "with your chosen category first to check for "
                            "prior user corrections, then call `save_problem`."
                        ),
                    }
                ],
                "is_error": True,
            }
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

    @tool(
        "lookup_category_edits",
        (
            "Look up past user corrections that moved problems AWAY from a "
            "candidate category. Call this EXACTLY ONCE with your "
            "tentatively chosen category BEFORE `save_problem`. If the "
            "returned examples reveal a consistent correction pattern that "
            "matches the new problem, switch to the user-picked category in "
            "`save_problem`; otherwise keep your category. An empty result "
            "means no prior edits — keep your category and proceed."
        ),
        {"category": str},
    )
    async def lookup_category_edits(args: dict) -> dict:
        lookup_called["value"] = True
        category = args.get("category", "")
        examples = storage.category_edit_examples(
            category, limit=CATEGORY_EDIT_EXAMPLES_LIMIT
        )
        if not examples:
            text = (
                f"No prior user edits away from '{category}'. Keep your "
                "category and proceed to `save_problem`."
            )
        else:
            lines = [
                f"{len(examples)} past user correction(s) away from "
                f"'{category}':"
            ]
            for ex in examples:
                lines.append(
                    f"- moved '{ex['from_category']}' -> "
                    f"'{ex['to_category']}': {ex['problem_text']}"
                )
            lines.append(
                "If the new problem fits this pattern, save with the "
                "corrected category; otherwise keep yours."
            )
            text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_problem, lookup_category_edits],
    )
