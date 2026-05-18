"""CLI for backfill maintenance scripts.

Usage:
    python -m backfill classify --email <email> [--mode missing|all] [--dry-run]
"""

import argparse
import sys

from common import storage
from common.db_setup.setup import init_user

from backfill.classify import classify_problems


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
    return 1


if __name__ == "__main__":
    sys.exit(main())
