"""File-system helpers. The ONLY place that turns a stored path into a real file."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def relative_pdf_path(job_id: str, certificate_id: str) -> str:
    """Server-generated path: built only from UUIDs, never from user input."""
    return f"{job_id}/{certificate_id}.pdf"


def resolve_safe_path(output_dir: Path, relative_path: str) -> Path | None:
    """Return the absolute file path, or None if it is unsafe or missing.

    Even though paths come from our own database, we re-check that the resolved
    path is inside the output directory (defence in depth against '..',
    absolute paths, and symlink tricks).
    """
    root = output_dir.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        logger.warning("Rejected path outside output directory: %s", relative_path)
        return None
    return candidate if candidate.is_file() else None


def delete_quietly(path: Path) -> None:
    """Best-effort delete used when cleaning up after a failure."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete %s", path, exc_info=True)
