"""Pydantic models: the API's request and response contracts.

Request-level problems (wrong types, empty recipient list, blank event name)
fail here with HTTP 422. Problems with an individual recipient's VALUES (blank
name, bad email) are deliberately NOT rejected here: they are recorded as failed
certificates so valid recipients in the same job still succeed.
"""
from datetime import date, datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

from app.models import CertificateStatus, JobStatus
from app.services.certificate_generator import find_unsupported_characters
from app.services.validation import clean_text, has_bad_characters


# ----------------------------------------------------------------- requests
class RecipientIn(BaseModel):
    # max_length is only a safety cap against absurd payloads. The real,
    # per-recipient business rules live in services/validation.py.
    name: str | None = Field(None, max_length=1000, examples=["Asha Verma"])
    email: str | None = Field(None, max_length=500, examples=["asha@example.com"])


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "certificate_title": "Certificate of Completion",
                    "event_name": "Python Backend Bootcamp 2026",
                    "issue_date": "2026-10-11",
                    "recipients": [
                        {"name": "Asha Verma", "email": "asha@example.com"},
                        {"name": "José Álvarez"},
                        {"name": "", "email": "not-an-email"},
                    ],
                }
            ]
        },
    )

    certificate_title: str = Field("Certificate of Completion", min_length=1, max_length=100)
    event_name: str = Field(..., min_length=1, max_length=200)
    issue_date: date = Field(default_factory=date.today)
    recipients: list[RecipientIn] = Field(..., min_length=1)

    @field_validator("certificate_title", "event_name")
    @classmethod
    def _check_shared_text(cls, value: str) -> str:
        value = clean_text(value)
        if not value:
            raise ValueError("must not be blank")
        if has_bad_characters(value):
            raise ValueError("contains invalid control characters")
        bad = find_unsupported_characters(value)
        if bad:
            raise ValueError(
                "contains characters not supported by the certificate font: "
                + " ".join(repr(ch) for ch in bad[:5])
            )
        return value


# ---------------------------------------------------------------- responses
class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str = Field(validation_alias="id")
    status: JobStatus
    total_count: int
    success_count: int
    failed_count: int
    certificate_title: str
    event_name: str
    issue_date: date
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @computed_field
    @property
    def processed_count(self) -> int:
        return self.success_count + self.failed_count

    @computed_field
    @property
    def progress_percent(self) -> float:
        if self.total_count == 0:
            return 0.0
        return round(self.processed_count / self.total_count * 100, 1)


class CertificateOut(BaseModel):
    """Note: the server-side file path is intentionally NOT exposed."""

    model_config = ConfigDict(from_attributes=True)

    certificate_id: str = Field(validation_alias="id")
    job_id: str
    position: int
    recipient_name: str
    recipient_email: str | None
    status: CertificateStatus
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    @computed_field
    @property
    def download_url(self) -> str | None:
        if self.status == CertificateStatus.GENERATED:
            return f"/certificates/{self.certificate_id}/download"
        return None


class CertificateListOut(BaseModel):
    job_id: str
    total: int  # rows matching the filter (not just this page)
    limit: int
    offset: int
    items: list[CertificateOut]


class HealthOut(BaseModel):
    status: str
    database: str
