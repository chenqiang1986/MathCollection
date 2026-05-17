"""Public surface for the agent package — re-exports the orchestrator and
shared helpers used by callers."""

from .orchestrator import (
    ProcessImageInput,
    ProcessImageResult,
    process_image,
    process_images,
)
from .problem_store import build_problem_store
from .refine import refine_problem
from .util import MODEL, log_message

__all__ = [
    "MODEL",
    "ProcessImageInput",
    "ProcessImageResult",
    "build_problem_store",
    "log_message",
    "process_image",
    "process_images",
    "refine_problem",
]
