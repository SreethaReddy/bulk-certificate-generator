"""Job orchestration: create jobs, process them one certificate at a time.

Design rules (see README):
* The background worker opens its OWN database session; it never reuses the
  request's session (which is closed when the response is sent).
* Every state change is claimed with a conditional UPDATE ("... WHERE status =
  'pending'"). If 0 rows change, someone else got there first, so we skip. That
  prevents duplicate processing.
* Each certificate is processed and committed independently, so one failure
  cannot roll back or block the others.
* Counters are changed with SQL "x = x + 1" in the SAME transaction as the
  certificate's status, so they can never drift from the real rows.
"""
import logging
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Certificate,
    CertificateStatus,
    GenerationJob,
    JobStatus,
    utcnow,
)
from app.schemas import JobCreateRequest
from app.services.certificate_generator import (
    CertificateData,
    CertificateGenerationError,
    generate_single_certificate,
)
from app.services.storage import delete_quietly, relative_pdf_path
from app.services.validation import validate_recipient

logger = logging.getLogger(__name__)

# Shown to clients when something unexpected happens. The real exception (which
# may contain paths or SQL) goes to the server log only.
GENERIC_ERROR = "Unexpected error while generating the certificate"


def derive_final_status(success_count: int, failed_count: int) -> JobStatus:
    """The single rule that decides a finished job's status."""
    if success_count == 0:
        return JobStatus.FAILED
    if failed_count == 0:
        return JobStatus.COMPLETED
    return JobStatus.PARTIALLY_FAILED


def _truncate(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


# --------------------------------------------------------------- job creation
def create_job(db: Session, payload: JobCreateRequest) -> GenerationJob:
    """Persist a job plus one certificate row per recipient, in ONE transaction.

    Invalid recipients are stored as already-failed rows with the reason, so
    they show up in results. If nothing is left to generate, the job is
    finalised immediately (status 'failed').
    """
    now = utcnow()
    job = GenerationJob(
        status=JobStatus.PENDING,
        total_count=len(payload.recipients),
        success_count=0,
        failed_count=0,
        certificate_title=payload.certificate_title,
        event_name=payload.event_name,
        issue_date=payload.issue_date,
    )

    for position, recipient in enumerate(payload.recipients, start=1):
        check = validate_recipient(recipient.name, recipient.email)
        certificate = Certificate(
            position=position,
            # Raw values are kept (truncated to the column size) so a failed
            # row can still be identified by the client.
            recipient_name=_truncate(check.name or (recipient.name or ""), 255),
            recipient_email=_truncate(check.email or recipient.email, 255),
        )
        if check.error:
            certificate.status = CertificateStatus.FAILED
            certificate.error_message = f"Invalid recipient: {check.error}"
            certificate.completed_at = now
            job.failed_count += 1
        else:
            certificate.status = CertificateStatus.PENDING
        job.certificates.append(certificate)

    if job.failed_count == job.total_count:  # nothing to generate
        job.status = JobStatus.FAILED
        job.completed_at = now

    try:
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return job


# ------------------------------------------------------------- job processing
def _claim_job(db: Session, job_id: str) -> bool:
    result = db.execute(
        update(GenerationJob)
        .where(GenerationJob.id == job_id, GenerationJob.status == JobStatus.PENDING)
        .values(status=JobStatus.PROCESSING, started_at=utcnow())
    )
    db.commit()
    return result.rowcount == 1


def _record_result(
    db: Session, job_id: str, certificate_id: str, file_path: str | None, error: str | None
) -> None:
    """Save one certificate's outcome AND bump the job counter, atomically."""
    succeeded = error is None
    result = db.execute(
        update(Certificate)
        .where(Certificate.id == certificate_id, Certificate.status == CertificateStatus.PROCESSING)
        .values(
            status=CertificateStatus.GENERATED if succeeded else CertificateStatus.FAILED,
            file_path=file_path,
            error_message=error,
            completed_at=utcnow(),
        )
    )
    if result.rowcount != 1:  # already recorded elsewhere: do not double count
        db.rollback()
        logger.warning("Certificate %s was not in 'processing'; result ignored", certificate_id)
        return
    if succeeded:
        counter = {"success_count": GenerationJob.success_count + 1}
    else:
        counter = {"failed_count": GenerationJob.failed_count + 1}
    db.execute(update(GenerationJob).where(GenerationJob.id == job_id).values(**counter))
    db.commit()


def _process_certificate(
    db: Session, job_id: str, certificate_id: str, job_info: dict, output_dir: Path
) -> None:
    # 1. Claim this certificate (pending -> processing)
    claimed = db.execute(
        update(Certificate)
        .where(Certificate.id == certificate_id, Certificate.status == CertificateStatus.PENDING)
        .values(status=CertificateStatus.PROCESSING)
    )
    db.commit()
    if claimed.rowcount != 1:
        return

    recipient_name = db.scalar(
        select(Certificate.recipient_name).where(Certificate.id == certificate_id)
    )
    relative_path = relative_pdf_path(job_id, certificate_id)
    destination = output_dir / relative_path

    # 2. Generate. Anything that goes wrong becomes a message, never an exception.
    error: str | None = None
    try:
        generate_single_certificate(
            destination,
            CertificateData(
                recipient_name=recipient_name,
                certificate_id=certificate_id,
                **job_info,
            ),
        )
    except CertificateGenerationError as exc:
        error = str(exc)  # written to be safe for clients
    except Exception:
        logger.exception("Unexpected failure generating certificate %s", certificate_id)
        error = GENERIC_ERROR

    # 3. Record the outcome (certificate row + job counter in one transaction)
    if error is None:
        try:
            _record_result(db, job_id, certificate_id, relative_path, None)
            return
        except Exception:
            logger.exception("Could not save result for certificate %s", certificate_id)
            db.rollback()
            error = GENERIC_ERROR

    delete_quietly(destination)  # never leave a PDF behind for a failed record
    try:
        _record_result(db, job_id, certificate_id, None, error)
    except Exception:
        # Database unavailable. The row stays 'processing'; startup recovery
        # will pick it up. Do not crash the rest of the job.
        logger.exception("Could not record failure for certificate %s", certificate_id)
        db.rollback()


def _finalize_job(db: Session, job_id: str) -> None:
    unfinished = db.scalar(
        select(func.count())
        .select_from(Certificate)
        .where(
            Certificate.job_id == job_id,
            Certificate.status.in_([CertificateStatus.PENDING, CertificateStatus.PROCESSING]),
        )
    )
    if unfinished:
        logger.warning("Job %s has %s unfinished certificates; not finalising", job_id, unfinished)
        return
    success, failed = db.execute(
        select(GenerationJob.success_count, GenerationJob.failed_count).where(
            GenerationJob.id == job_id
        )
    ).one()
    db.execute(
        update(GenerationJob)
        .where(GenerationJob.id == job_id, GenerationJob.status == JobStatus.PROCESSING)
        .values(
            status=derive_final_status(success, failed),
            completed_at=utcnow()
        )
    )
    db.commit()


def process_job_sync(session_factory: sessionmaker[Session], job_id: str, output_dir: Path) -> None:
    """Synchronous consumer entrypoint executed inside background threads."""
    with session_factory() as db:
        if not _claim_job(db, job_id):
            return  # Job already claimed or completed by another worker process

        # Load stable shared details needed by the drawing canvas engine
        job_data = db.execute(
            select(
                GenerationJob.certificate_title,
                GenerationJob.event_name,
                GenerationJob.issue_date
            ).where(GenerationJob.id == job_id)
        ).one()
        
        job_info = {
            "certificate_title": job_data.certificate_title,
            "event_name": job_data.event_name,
            "issue_date": job_data.issue_date,
        }

        # Fetch all pending certificate rows linked with this batch
        cert_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.position)
        ).all()

    # Process individual loops outside of global session isolation windows
    for certificate_id in cert_ids:
        with session_factory() as db:
            _process_certificate(db, job_id, certificate_id, job_info, output_dir)

    # Wrap up counter tracking metrics and close processing lifecycle window
    with session_factory() as db:
        _finalize_job(db, job_id)
