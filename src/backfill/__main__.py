"""CLI for backfill maintenance scripts.

Usage:
    python -m backfill subcategory <email> [--dry-run]
"""

import argparse
import sys

from db_setup.setup import init_user
from lib import storage

from backfill.subcategory import backfill_subcategory


def main() -> int:
    parser = argparse.ArgumentParser(prog="backfill", description=__doc__)
    sub = parser.add_subparsers(dest="task", required=True)

    sub_p = sub.add_parser(
        "subcategory",
        help="Fill in missing subcategories on existing problems.",
    )
    sub_p.add_argument("--email", help="The user email whose problems to backfill.")
    sub_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed subcategories without writing them.",
    )

    args = parser.parse_args()
    storage.set_current_user(args.email)
    init_user()

    if args.task == "subcategory":
        targeted, updated = backfill_subcategory(dry_run=args.dry_run)
        suffix = " (dry run, no writes)" if args.dry_run else ""
        print(f"[backfill] done: {updated} of {targeted} updated{suffix}.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
