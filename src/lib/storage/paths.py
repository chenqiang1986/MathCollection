"""User-context binding and per-user filesystem paths."""

import contextvars
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
UPLOADS_DIR = REPO_ROOT / "uploads"

_CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar("current_user")
_EMAIL_SAFE_RE = re.compile(r"[^a-z0-9@._\-+]")


def sanitize_email(email: str) -> str:
    """Lowercase and replace filesystem-unsafe characters."""
    if not email:
        raise ValueError("email is required")
    safe = _EMAIL_SAFE_RE.sub("_", email.strip().lower())
    if not safe:
        raise ValueError(f"email sanitizes to empty string: {email!r}")
    return safe


def set_current_user(email: str) -> contextvars.Token:
    """Bind the current user for subsequent storage calls in this context."""
    return _CURRENT_USER.set(sanitize_email(email))


def reset_current_user(token: contextvars.Token) -> None:
    _CURRENT_USER.reset(token)


def _user_slug() -> str:
    try:
        return _CURRENT_USER.get()
    except LookupError as e:
        raise RuntimeError(
            "storage called without an active user; call set_current_user first"
        ) from e


def user_dir() -> Path:
    return DATA_DIR / _user_slug()


def problems_dir() -> Path:
    return user_dir() / "problems"


def figures_dir() -> Path:
    return user_dir() / "figures"


def figure_path(filename: str) -> Path:
    return figures_dir() / filename


def index_path() -> Path:
    return user_dir() / "problems_index.db"
