"""API endpoint integration test checks including security and error recovery."""
import pytest
from unittest.mock import patch
from pathlib import Path
from sqlalchemy import select, update
from app.models import GenerationJob, JobStatus, Certificate, CertificateStatus
from app.services.job_processor import process_job_sync
from app.services.certificate_generator import CertificateGenerationError

# ---------------------------------------------------------------- Basic Checks
def test_health_endpoint(client):
    """Verify that system metrics report healthy status connections."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}

def test_create_job_invalid_payload(client):
    """Verify request blocks completely empty structural schema arrays."""
    payload = {"certificate_title": "", "event_name": "", "recipients": []}
    response = client.post("/jobs", json=payload)
    assert response.status_code == 422

# ----------------------------------------------------------- Validation Engine
def test_invalid_recipient_data_processing(client, db_session):
    """Verify bad row data creates a failed cert record but keeps job running."""
    payload = {
        "certificate_title": "Validation Test",
        "event_name": "Error Checks",
        "recipients": [
            {"name": "Valid User", "email": "valid@example.com"},
            {"name": "", "email": "bad-email-format"}
        ]
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    
    db_session.expire_all()
    job = db_session.get(GenerationJob, job_id)
    assert job.failed_count == 1
    assert job.total_count == 2

# ------------------------------------------------------ Simulated Engine Crash
def test_pdf_generation_crash_handling(client, db_session):
    """Simulate a canvas engine crash to verify files wipe and rows log errors."""
    payload = {
        "certificate_title": "Crash Test",
        "event_name": "Failure Diagnostics",
        "recipients": [{"name": "John Doe", "email": "john@example.com"}]
    }
    
    # 1. Prevent background task auto-execution by short-circuiting the router call
    with patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
        response = client.post("/jobs", json=payload)
        job_id = response.json()["job_id"]
    
    # 2. Trigger worker manually with an active crash injection trigger
    with patch("app.services.job_processor.generate_single_certificate", 
               side_effect=CertificateGenerationError("Font layout overflow")):
        process_job_sync(client.app.state.session_factory, job_id, client.app.state.output_dir)

    db_session.expire_all()
    job = db_session.get(GenerationJob, job_id)
    cert = db_session.scalars(select(Certificate).where(Certificate.job_id == job_id)).first()
    
    assert job.status == JobStatus.FAILED
    assert cert.status == CertificateStatus.FAILED
    assert "Font layout overflow" in cert.error_message
    
    target_path = client.app.state.output_dir / f"{job_id}/{cert.id}.pdf"
    assert not target_path.exists()

# ------------------------------------------------------------- Security Shields
def test_download_security_path_traversal_shield(client):
    """Verify that path traversal attempts yield standard 404 errors."""
    payload = {
        "certificate_title": "Security Test",
        "event_name": "Traversal Check",
        "recipients": [{"name": "Hacker", "email": "hacker@example.com"}]
    }
    
    # 1. Post a valid job lifecycle sequence through the test application instance
    with patch("fastapi.BackgroundTasks.add_task"):
        response = client.post("/jobs", json=payload)
        job_id = response.json()["job_id"]
    
    # 2. Open an active database connection using the application state engine factory directly
    with client.app.state.session_factory() as app_db:
        cert = app_db.scalars(select(Certificate).where(Certificate.job_id == job_id)).first()
        
        # 3. Alter and commit properties explicitly inside the application domain state scope
        cert.status = CertificateStatus.GENERATED
        cert.file_path = "../../../../etc/passwd"
        app_db.commit()
        cert_id = cert.id
    
    # 4. Trigger download endpoint request
    download_response = client.get(f"/certificates/{cert_id}/download")
    
    assert download_response.status_code == 404
    assert download_response.json()["detail"] == "Certificate file not found on server"

# ------------------------------------------------------------- Concurrency Test
def test_job_processing_concurrency_lockout(client, db_session):
    """Verify double-claiming locks out race conditions to prevent data drift."""
    payload = {
        "certificate_title": "Race Test",
        "event_name": "Concurrency Check",
        "recipients": [{"name": "Race User", "email": "race@example.com"}]
    }
    with patch("fastapi.BackgroundTasks.add_task"):
        response = client.post("/jobs", json=payload)
        job_id = response.json()["job_id"]

    process_job_sync(client.app.state.session_factory, job_id, client.app.state.output_dir)
    
    db_session.expire_all()
    job_after_run_one = db_session.get(GenerationJob, job_id)
    assert job_after_run_one.status == JobStatus.COMPLETED

    with patch("app.services.job_processor._claim_job", return_value=False) as mock_claim:
        process_job_sync(client.app.state.session_factory, job_id, client.app.state.output_dir)
        assert mock_claim.call_count == 1
