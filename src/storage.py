import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROBLEMS_DIR = Path(__file__).resolve().parent.parent / "data" / "problems"


def save_problem(
    problem_text: str,
    category: str,
    difficulty: str,
    solution: str,
    source_image: str | None = None,
    diagram_svg: str | None = None,
    solution_svg: str | None = None,
) -> dict:
    PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    problem_id = str(uuid.uuid4())
    record = {
        "id": problem_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "problem_text": problem_text,
        "category": category,
        "difficulty": difficulty,
        "solution": solution,
        "source_image": source_image,
        "diagram_svg": diagram_svg or None,
        "solution_svg": solution_svg or None,
    }
    path = PROBLEMS_DIR / f"{problem_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return record


def list_problems() -> list[dict]:
    if not PROBLEMS_DIR.exists():
        return []
    out = []
    for p in sorted(PROBLEMS_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out
