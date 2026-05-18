"""Storage record types. Declarations only — no runtime dependencies so any
consumer can import these without pulling in sqlite3 or filesystem helpers."""

import dataclasses
from dataclasses import asdict, dataclass
from typing import NamedTuple


@dataclass
class Problem:
    id: str
    created_at: str
    problem_text: str
    category: str
    subcategory: str = ""
    solve_time_seconds: float | None = None
    solve_time_estimated: int = 0
    solution: str = ""
    source_image: str | None = None
    source_page: int | None = None
    seq_no: int | None = None
    source_exam: str = "Unknown"
    year: str = "Unknown"
    figure_image: str | None = None
    figure_bbox: list[float] | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Problem":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


class Bucket(NamedTuple):
    label: str
    lo: float
    hi: float


DIFFICULTY_BUCKETS: list[Bucket] = [
    Bucket("Easy (<60s)", 0.0, 60.0),
    Bucket("Medium (1–3m)", 60.0, 180.0),
    Bucket("Hard (3–10m)", 180.0, 600.0),
    Bucket("Very Hard (>10m)", 600.0, float("inf")),
]
