"""CLI to apply the Postgres schema and sync every user's problems from JSON.

Run once at deploy time (see docker_run.sh) so live request handling never
triggers a DB sync. Idempotent and version-gated, so re-running it when nothing
changed is cheap.

    PYTHONPATH=. python -m common.db_setup
"""

import sys

from dotenv import load_dotenv

from common.db_setup.setup import sync_all_users


def main() -> int:
    load_dotenv()
    counts = sync_all_users()
    for user, n in counts.items():
        print(f"[db_setup] {user}: {n} problem(s)")
    total = sum(counts.values())
    print(
        f"[db_setup] schema ready; synced {len(counts)} user(s), "
        f"{total} problem(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
