"""Application factory and entry point.

Run with:  uvicorn app.main:app --reload
"""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine

from app import models  # noqa: F401  (registers the tables on Base.metadata)
from app.config import get_settings
from app.database import Base, create_db_engine, create_session_factory
from app.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app(engine: Engine | None = None, output_dir: Path | None = None) -> FastAPI:
    """Build the app. Tests pass their own engine/output_dir; production passes nothing."""
    settings = get_settings()
    engine = engine or create_db_engine()
    output_dir = Path(output_dir) if output_dir else settings.resolved_output_dir
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)  # creates missing tables only
        output_dir.mkdir(parents=True, exist_ok=True)
        # Placeholder for background job recovery routines if added later
        yield
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        description="Submit many recipients in one request; certificates are generated in the "
        "background as individual PDFs. Track progress per job and download each PDF.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.output_dir = output_dir
    app.state.max_recipients = settings.max_recipients_per_job
    app.include_router(router)
    return app


app = create_app()
