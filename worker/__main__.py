"""CLI entrypoint for the offline worker.

Usage:
    python -m worker            # long-running daemon (default)
    python -m worker --once     # drain every user's queue once and exit

Requires the repo root on PYTHONPATH so `from common import ...`
resolves. Run from the repo root, e.g.:

    PYTHONPATH=. python -m worker
"""

import argparse
import sys

from dotenv import load_dotenv

from worker.run import run_forever, run_once


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="worker", description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Drain every user's pending queue once, then exit. Otherwise "
            "run as a daemon that polls forever."
        ),
    )
    args = parser.parse_args()
    if args.once:
        n = run_once()
        print(f"[worker] --once done; processed {n} file(s).")
        return 0
    run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
