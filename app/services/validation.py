"""Recipient-level validation.

Request-level problems (empty list, missing event name...) are rejected by
Pydantic with HTTP 422. Problems with ONE recipient are different: that row is
recorded as a failed certificate while the others still proceed. This module
decides whether a single recipient is acceptable.
"""
import re
import unicodedata
from dataclasses import dataclass

MAX_NAME_LENGTH = 150
MAX_EMAIL_LENGTH = 254
# Deliberately simple: one '@', no spaces, a dot in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Control (Cc), surrogate (Cs), private-use (Co) and unassigned (Cn) characters
_BAD_CATEGORIES = {"Cc", "Cs", "Co", "Cn"}


def clean_text(value: str) -> str:
    """Normalise Unicode (NFC) and collapse every whitespace run to one space."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def has_bad_characters(value: str) -> bool:
    return any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in value)


@dataclass(frozen=True)
class RecipientCheck:
    name: str
    email: str | None
    error: str | None  # None means the recipient is valid


def validate_recipient(name: str | None, email: str | None) -> RecipientCheck:
    """Validate one recipient. Never raises; reports problems in `error`."""
    cleaned_name = clean_text(name) if name else ""
    cleaned_email = email.strip() if email and email.strip() else None

    problems: list[str] = []
    if not cleaned_name:
        problems.append("name is required")
    elif len(cleaned_name) > MAX_NAME_LENGTH:
        problems.append(f"name must be at most {MAX_NAME_LENGTH} characters")
    elif has_bad_characters(cleaned_name):
        problems.append("name contains invalid control characters")

    if cleaned_email is not None:
        if len(cleaned_email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(cleaned_email):
            problems.append("email is not a valid address")

    return RecipientCheck(
        name=cleaned_name,
        email=cleaned_email,
        error="; ".join(problems) if problems else None,
    )
