"""HTTP layer only: parse input, call services, return the right status codes."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import (
    CertificateListOut,
    CertificateOut,
    HealthOut,
    JobCreateRequest,
    JobOut,
)
from app.services.job_processor import create_job, process_job_sync
from app.services.storage import resolve_safe_path

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=202,
    tags=["Jobs"],
    summary="Create a bulk certificate generation job",
    responses={422: {"description": "Invalid request (empty list, blank event name, too many recipients...)"}},
)
def create_generation_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    db: DbSession,
):
    """Accepts many recipients at once and returns immediately with a job ID.

    Certificates are generated in the background. Poll `GET /jobs/{job_id}`.
    Recipients with invalid data are recorded as failed; the rest proceed.
    """
    maximum = request.app.state.max_recipients
    if len(payload.recipients) > maximum:
        raise HTTPException(
            status_code=422,
            detail=f"A job may contain at most {maximum} recipients (received {len(payload.recipients)}).",
        )

    job = create_job(db, payload)
    if job.status == JobStatus.PENDING:  # at least one valid recipient
        background_tasks.add_task(
            process_job_sync, request.app.state.session_factory, job.id, request.app.state.output_dir
        )
    response.headers["Location"] = f"/jobs/{job.id}"
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    tags=["Jobs"],
    summary="Get job status and progress",
    responses={404: {"description": "Job not found"}, 422: {"description": "job_id is not a valid UUID"}},
)
def get_job(job_id: UUID, db: DbSession):
    job = db.get(GenerationJob, str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateListOut,
    tags=["Jobs"],
    summary="List per-recipient results of a job",
    responses={404: {"description": "Job not found"}, 422: {"description": "Invalid job_id or query parameter"}},
)
def list_job_certificates(
    job_id: UUID,
    db: DbSession,
    status: Annotated[CertificateStatus | None, Query(description="Filter by certificate status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    job_key = str(job_id)
    if db.get(GenerationJob, job_key) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    conditions = [Certificate.job_id == job_key]
    if status is not None:
        conditions.append(Certificate.status == status)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions))
    items = db.scalars(
        select(Certificate)
        .where(*conditions)
        .order_by(Certificate.position)
        .limit(limit)
        .offset(offset)
    ).all()
    return CertificateListOut(
        job_id=job_key,
        total=total,
        limit=limit,
        offset=offset,
        items=[CertificateOut.model_validate(item) for item in items],
    )


@router.get(
    "/certificates/{certificate_id}/download",
    tags=["Certificates"],
    summary="Download a generated certificate PDF",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The PDF file"},
        404: {"description": "Certificate not found, or its file is missing on disk"},
        409: {"description": "Certificate has not been generated (pending, processing or failed)"},
        422: {"description": "certificate_id is not a valid UUID"},
    },
)
def download_certificate(certificate_id: UUID, request: Request, db: DbSession):
    certificate = db.get(Certificate, str(certificate_id))
    if certificate is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if certificate.status != CertificateStatus.GENERATED:
        raise HTTPException(
            status_code=409,
            detail=f"Certificate is not available for download (status: {certificate.status.value})",
        )
    # The file location comes ONLY from our database, and is re-validated to be
    # inside the output directory. The client never supplies a path.
    path = resolve_safe_path(request.app.state.output_dir, certificate.file_path or "")
    if path is None:
        raise HTTPException(status_code=404, detail="Certificate file not found on server")
    return FileResponse(
        path, media_type="application/pdf", filename=f"certificate-{certificate.id}.pdf"
    )


@router.get("/health", response_model=HealthOut, tags=["System"], summary="Health check")
def health(db: DbSession):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "down"})
    return HealthOut(status="ok", database="up")
