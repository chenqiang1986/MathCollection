"""Storage record types. Declarations only — no runtime dependencies so any
consumer can import these without pulling in sqlite3 or filesystem helpers."""

import dataclasses
import re
from dataclasses import asdict, dataclass, field
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
    subexam: str = ""
    year: str = "Unknown"
    figure_image: str | None = None
    figure_bbox: list[float] | None = None
    figure_page: int | None = None
    # Free-form, customer-defined tags. Stored normalized (see normalize_tags).
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Problem":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_tag(raw: str | None) -> str:
    """Canonical form for a single tag: trimmed, lowercased, internal
    whitespace collapsed. Returns "" for empty input."""
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def normalize_tags(raw: object) -> list[str]:
    """Normalize an iterable of tags: drop empties and duplicates while
    preserving first-seen order. Non-list input yields an empty list."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = normalize_tag(item)
        if tag and tag not in out:
            out.append(tag)
    return out


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


CANONICAL_SOURCE_EXAMS: tuple[str, ...] = (
    "AMC8",
    "AMC10A",
    "AMC10B",
    "AMC12A",
    "AMC12B",
    "AIME",
    "BMT",
    "HMMT",
    "ARML",
    "MathCounts",
    "PiMathContest",
    "Putnam",
)

# Aliases the model has been observed to emit (or that a human might type)
# mapped to their canonical form. Keep keys human-readable here; lookup
# normalizes (lowercase, alphanumeric-only) so spacing/punctuation/case
# variants all collapse to the same key.
_SOURCE_EXAM_ALIAS_PAIRS: tuple[tuple[str, str], ...] = (
    ("MATHCOUNTS", "MathCounts"),
    ("Mathcounts", "MathCounts"),
    ("Math Counts", "MathCounts"),
    ("MathCount", "MathCounts"),
    ("PiMC", "PiMathContest"),
    ("Pi MC", "PiMathContest"),
    ("PMC", "PiMathContest"),
    ("Pi Math Contest", "PiMathContest"),
    ("Pi Math", "PiMathContest"),
)


def _exam_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


_SOURCE_EXAM_BY_KEY: dict[str, str] = {
    _exam_key(name): name for name in CANONICAL_SOURCE_EXAMS
}
for _alias, _canonical in _SOURCE_EXAM_ALIAS_PAIRS:
    _SOURCE_EXAM_BY_KEY.setdefault(_exam_key(_alias), _canonical)


def canonicalize_source_exam(raw: str | None) -> str:
    """Map a model-emitted exam name to its canonical form.

    Returns ``"Unknown"`` for empty input. If the value (after
    case/whitespace/punctuation normalization) matches a canonical exam
    or a known alias, returns the canonical name. Otherwise returns the
    input trimmed unchanged — new contests can still enter the system,
    they just won't be auto-renamed."""
    text = (raw or "").strip()
    if not text:
        return "Unknown"
    return _SOURCE_EXAM_BY_KEY.get(_exam_key(text), text)
