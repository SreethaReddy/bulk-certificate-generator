"""Database plumbing: declarative base, engine/session factories, request dependency.

Engines and session factories are *created by functions* instead of at import
time. That lets the tests build the app against a separate test database.
"""
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Parent class of all ORM models."""


def create_db_engine(database: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url(database),
        pool_pre_ping=True,  # transparently replace connections MySQL has closed
        pool_recycle=1800,  # recycle before MySQL's wait_timeout can kill them
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # autoflush=False: writes happen only when we say so (explicit transactions).
    # expire_on_commit=False: objects stay readable after commit without a re-query.
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()
