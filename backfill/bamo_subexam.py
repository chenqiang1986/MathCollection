"""Normalize the `subexam` field on existing BAMO problems to one of the
three canonical divisions: `8`, `12`, or `8/12`.

Bay Area Math Olympiad problems accumulated ~14 different spellings of the
same two divisions (`bamo-8`, `8`, `BAMO8`, `bamo8-12`, `8/12`, …). This is
a pure string remap over what's already stored — it does NOT re-read the
original exam images. The division is inferred from the digits present in
the current value:

    contains 8 and 12  -> "8/12"
    contains 12 only   -> "12"
    contains 8 only    -> "8"
    no 8 and no 12     -> left unchanged (e.g. empty string — no info)

Each match is written via `storage.update_problem`, which updates the
per-problem JSON and the Postgres index row in lockstep.
"""

import re

from common import storage

BAMO = "BAMO"


def normalize_bamo_subexam(raw: str | None) -> str | None:
    """Map any existing BAMO subexam spelling to `8`, `12`, or `8/12`.

    Returns ``None`` when the value carries no division info (e.g. an empty
    string) — the caller leaves such problems untouched."""
    nums = set(re.findall(r"\d+", raw or ""))
    has_8, has_12 = "8" in nums, "12" in nums
    if has_8 and has_12:
        return "8/12"
    if has_12:
        return "12"
    if has_8:
        return "8"
    return None


def backfill_bamo_subexams(dry_run: bool = False) -> tuple[int, int, int]:
    """Returns (bamo_problems, updated, unchanged) for the active user.

    `unchanged` counts BAMO problems left as-is — either already in
    canonical form or carrying no division info (empty subexam)."""
    bamo = [
        p
        for p in storage.list_problems()
        if (p.source_exam or "").strip() == BAMO
    ]
    updated = 0
    for p in bamo:
        current = (p.subexam or "").strip()
        target = normalize_bamo_subexam(current)
        if target is None or target == current:
            continue
        print(
            f"[backfill-bamo] {p.id}: subexam {current!r} -> {target!r}"
            + (" [dry-run]" if dry_run else ""),
            flush=True,
        )
        if not dry_run:
            storage.update_problem(p.id, subexam=target)
        updated += 1
    return len(bamo), updated, len(bamo) - updated
