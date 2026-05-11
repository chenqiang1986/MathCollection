"""In-process MCP server exposing `save_problem` to the inner solver."""

from claude_agent_sdk import create_sdk_mcp_server, tool

from lib import storage


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
