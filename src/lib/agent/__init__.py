"""Public surface for the agent package — re-exports the orchestrator and
shared helpers used by callers."""

from .orchestrator import ProcessImageResult, process_image
from .problem_store import build_problem_store
from .util import MODEL, log_message

__all__ = [
    "MODEL",
    "ProcessImageResult",
    "build_problem_store",
    "log_message",
    "process_image",
]
