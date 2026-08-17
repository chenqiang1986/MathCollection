"""In-process MCP servers backing the worker's two independent jobs.

- `build_scan_store` (image scan): exposes `save_parsed_problem` plus
  `list_subexams` (a read-only lookup of subexam labels prior runs used
  under a given exam, so the orchestrator reuses an existing label
  instead of inventing a new spelling — see
  `worker/prompts/orchestrator.md`). The orchestrator calls
  `save_parsed_problem` once per problem extracted. Each call crops the
  figure (if any) and persists a partial problem JSON with placeholder
  category `unclassified` and empty solution. Duplicate seq_no calls for
  the same source_image are skipped so the scan stage can safely retry.

- `build_classify_store` (batch classify): exposes `save_classification`
  and `lookup_category_edits` for one classify session covering a batch
  of problems (identified by `problem_id`, not bound to a single record
  like the old per-problem solver was). `lookup_category_edits` pulls in
  prior user corrections; a lookup must precede each save.
"""

from claude_agent_sdk import create_sdk_mcp_server, tool
from common import figures, storage

CATEGORY_EDIT_EXAMPLES_LIMIT = 5
UNCLASSIFIED_CATEGORY = "unclassified"


def build_scan_store(source_image: str | None, saved: list[storage.Problem]):
    """Return an MCP server bound to one source image. `saved` is the
    out-param the caller reads after the agent finishes."""
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

        source_exam = storage.canonicalize_source_exam(args.get("source_exam"))
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

    @tool(
        "list_subexams",
        (
            "List the `subexam` labels already used under a given "
            "`source_exam` by prior runs, with a usage count for each. "
            "Call this BEFORE `save_parsed_problem` whenever the document "
            "has a sub-event/round, then reuse a returned label EXACTLY "
            "(verbatim — same spelling and case) when it denotes the same "
            "round. This keeps the value consistent across runs. Only "
            "invent a new label when none of the returned ones fit; if "
            "several returned labels mean the same round, prefer the one "
            "with the highest count."
        ),
        {"source_exam": str},
    )
    async def list_subexams(args: dict) -> dict:
        exam = storage.canonicalize_source_exam(args.get("source_exam"))
        rows = storage.distinct_subexams(exam)
        if not rows:
            text = (
                f"No subexam labels recorded yet for {exam!r}. If this "
                "document has a named round, create a concise lowercase "
                "label; future runs will reuse it."
            )
        else:
            listed = ", ".join(f"{sub!r} ({n})" for sub, n in rows)
            text = (
                f"Existing subexam labels for {exam!r} (label (count), "
                f"most-used first): {listed}. Reuse one verbatim if it "
                "denotes this document's round; otherwise create a new one."
            )
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_parsed_problem, list_subexams],
    )


def _category_edit_lookup_text(category: str, subcategory: str | None) -> str:
    examples = storage.category_edit_examples(
        category,
        limit=CATEGORY_EDIT_EXAMPLES_LIMIT,
        from_subcategory=subcategory,
    )
    pair = f"'{category}'"
    if subcategory:
        pair = f"'{category} / {subcategory}'"
    if not examples:
        return (
            f"No prior user edits away from {pair}. Keep your choice and "
            "proceed to save."
        )
    lines = [f"{len(examples)} past user correction(s) away from {pair}:"]
    for ex in examples:
        src = ex["from_category"]
        if ex.get("from_subcategory"):
            src += f" / {ex['from_subcategory']}"
        dst = ex["to_category"]
        if ex.get("to_subcategory"):
            dst += f" / {ex['to_subcategory']}"
        lines.append(f"- moved '{src}' -> '{dst}': {ex['problem_text']}")
    lines.append(
        "If the new problem fits this pattern, save with the corrected "
        "values; otherwise keep yours."
    )
    return "\n".join(lines)


def build_classify_store(
    problem_ids: list[str], saved: list[storage.Problem]
):
    """Return an MCP server for one batch-classify session covering
    `problem_ids`. `saved` is the out-param the caller reads after the
    agent finishes."""
    expected_ids = set(problem_ids)
    saved_ids: set[str] = set()
    lookups_so_far = {"value": 0}

    @tool(
        "save_classification",
        (
            "Finalize the category/subcategory for ONE problem in this "
            "batch, identified by `problem_id`. Call once per problem "
            "listed in the prompt — never skip one, never invent a "
            "problem_id that wasn't given to you. Must be called AFTER "
            "`lookup_category_edits` for that problem's tentative pair."
        ),
        {"problem_id": str, "category": str, "subcategory": str},
    )
    async def save_classification(args: dict) -> dict:
        problem_id = args.get("problem_id", "")
        if problem_id not in expected_ids:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Error: problem_id {problem_id!r} is not part "
                            "of this batch."
                        ),
                    }
                ],
                "is_error": True,
            }
        if problem_id in saved_ids:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Already saved {problem_id}; skip it.",
                    }
                ],
                "is_error": True,
            }
        if lookups_so_far["value"] <= len(saved_ids):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Refusing save: call `lookup_category_edits` "
                            "with your chosen category/subcategory for "
                            f"{problem_id} first."
                        ),
                    }
                ],
                "is_error": True,
            }
        updated = storage.update_problem(
            problem_id,
            category=args["category"],
            subcategory=args.get("subcategory", "") or "",
        )
        saved.append(updated)
        saved_ids.add(problem_id)
        return {
            "content": [
                {"type": "text", "text": f"Classified {problem_id}."}
            ]
        }

    @tool(
        "lookup_category_edits",
        (
            "Look up past user corrections that moved problems AWAY from a "
            "candidate (category, subcategory). Call this ONCE per problem "
            "in the batch, with your tentatively chosen pair, BEFORE "
            "`save_classification` for that problem. If the returned "
            "examples reveal a consistent correction pattern that matches "
            "the new problem, switch to the user-picked values when "
            "saving; otherwise keep yours. An empty result means no prior "
            "edits — keep your choice and proceed. Pass an empty string "
            "for `subcategory` if you have not chosen one."
        ),
        {"category": str, "subcategory": str},
    )
    async def lookup_category_edits(args: dict) -> dict:
        lookups_so_far["value"] += 1
        text = _category_edit_lookup_text(
            args.get("category", ""), args.get("subcategory", "") or None
        )
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name="problem_store",
        version="1.0.0",
        tools=[save_classification, lookup_category_edits],
    )
