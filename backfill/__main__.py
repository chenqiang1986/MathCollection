"""CLI for backfill maintenance scripts.

Usage:
    python -m backfill classify --email <email> [--mode missing|all] [--dry-run]
    python -m backfill subexam --email <email> [--mode missing|all]
        [--dry-run] [--update-exam]
    python -m backfill bamo-subexam --email <email> [--dry-run]
"""

import argparse
import sys

from common import storage
from common.db_setup.setup import init_user

from backfill.bamo_subexam import backfill_bamo_subexams
from backfill.classify import classify_problems
from backfill.subexam import backfill_subexams


def main() -> int:
    parser = argparse.ArgumentParser(prog="backfill", description=__doc__)
    sub = parser.add_subparsers(dest="task", required=True)

    sub_p = sub.add_parser(
        "classify",
        help=(
            "Re-classify category and subcategory against the closed list "
            "in prompts/math_category.md."
        ),
    )
    sub_p.add_argument(
        "--email", help="The user email whose problems to backfill."
    )
    sub_p.add_argument(
        "--mode",
        choices=["missing", "all"],
        default="missing",
        help=(
            "missing (default): only problems with empty category or "
            "subcategory. all: re-evaluate every problem; writes only when "
            "the new pair differs from the stored one."
        ),
    )
    sub_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed (category, subcategory) without writing.",
    )

    sub_x = sub.add_parser(
        "subexam",
        help=(
            "Scan raw source files to identify each one's sub-round "
            "(BMT algebra/discrete/..., MathCounts sprint/target/team, "
            "etc.) and apply it to every problem extracted from that "
            "file."
        ),
    )
    sub_x.add_argument(
        "--email", help="The user email whose problems to backfill."
    )
    sub_x.add_argument(
        "--mode",
        choices=["missing", "all"],
        default="missing",
        help=(
            "missing (default): only update problems with empty subexam. "
            "all: also overwrite existing non-empty subexams when the "
            "scan disagrees."
        ),
    )
    sub_x.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed updates without writing.",
    )
    sub_x.add_argument(
        "--update-exam",
        action="store_true",
        help=(
            "Also fix source_exam when the existing value is empty or "
            "'Unknown'. Off by default — the orchestrator already "
            "captures source_exam at scan time."
        ),
    )

    sub_b = sub.add_parser(
        "bamo-subexam",
        help=(
            "Normalize existing BAMO subexam spellings (bamo-8, BAMO8, "
            "bamo8-12, …) to one of '8', '12', '8/12'. Pure string remap; "
            "does not re-read the exam images."
        ),
    )
    sub_b.add_argument(
        "--email", help="The user email whose problems to backfill."
    )
    sub_b.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed updates without writing.",
    )

    args = parser.parse_args()
    storage.set_current_user(args.email)
    init_user()

    if args.task == "classify":
        targeted, updated = classify_problems(
            mode=args.mode, dry_run=args.dry_run
        )
        suffix = " (dry run, no writes)" if args.dry_run else ""
        print(f"[backfill] done: {updated} of {targeted} updated{suffix}.")
        return 0
    if args.task == "subexam":
        files, targeted, updated, skipped = backfill_subexams(
            mode=args.mode,
            dry_run=args.dry_run,
            update_exam=args.update_exam,
        )
        suffix = " (dry run, no writes)" if args.dry_run else ""
        print(
            f"[backfill] done: scanned {files} file(s); "
            f"{updated} of {targeted} matched problem(s) updated; "
            f"{skipped} problem(s) had no matching raw file{suffix}."
        )
        return 0
    if args.task == "bamo-subexam":
        total, updated, unchanged = backfill_bamo_subexams(
            dry_run=args.dry_run
        )
        suffix = " (dry run, no writes)" if args.dry_run else ""
        print(
            f"[backfill] done: {total} BAMO problem(s); "
            f"{updated} normalized, {unchanged} unchanged{suffix}."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
