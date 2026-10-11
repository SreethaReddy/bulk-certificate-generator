"""SQLAlchemy ORM models: GenerationJob (1) ---< Certificate (many)."""
import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp. Every timestamp in the database is UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    GENERATED = "generated"
    FAILED = "failed"


def _enum_column(enum_cls: type[enum.Enum], name: str) -> Enum:
    """Store the enum as VARCHAR(20) + CHECK constraint (readable, easy to migrate)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=20,
        create_constraint=True,
        values_callable=lambda e: [member.value for member in e],
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint(
            "success_count >= 0 AND failed_count >= 0 "
            "AND success_count + failed_count <= total_count",
            name="ck_jobs_counts",
        ),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus, "ck_jobs_status"), default=JobStatus.PENDING
    )
    total_count: Mapped[int] = mapped_column(Integer)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)

    # Shared certificate information (same for every recipient in the job)
    certificate_title: Mapped[str] = mapped_column(String(100))
    event_name: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[Date] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Certificate.position",
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_certificates_job_position"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generation_jobs.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)  # 1-based order in the request

    recipient_name: Mapped[str] = mapped_column(String(255))
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[CertificateStatus] = mapped_column(
        _enum_column(CertificateStatus, "ck_certificates_status"),
        default=CertificateStatus.PENDING,
    )
    # Path RELATIVE to the output directory, e.g. "<job_id>/<certificate_id>.pdf"
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped[GenerationJob] = relationship(back_populates="certificates")
