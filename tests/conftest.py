"""Global configuration and fixtures for Pytest."""
import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import create_app
from app.config import get_settings
from app.database import Base

@pytest.fixture(scope="session")
def test_settings():
    """Load settings profile configuration."""
    return get_settings()

@pytest.fixture(scope="session")
def test_engine(test_settings):
    """Create an isolated database engine using the test database configuration."""
    # Use the test database instead of production
    engine = create_engine(
        test_settings.database_url(database=test_settings.test_db_name),
        pool_pre_ping=True
    )
    
    # Create clean tables on test execution startup
    Base.metadata.create_all(bind=engine)
    yield engine
    # Clean up and wipe schemas when done
    Base.metadata.drop_all(bind=engine)
    engine.dispose()

@pytest.fixture(scope="function")
def db_session(test_engine):
    """Provide an isolated database session per individual test function execution."""
    connection = test_engine.connect()
    transaction = connection.begin()
    
    SessionLocal = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture(scope="function")
def client(test_engine, test_settings):
    """Provide an initialized instance of FastAPI TestClient pointing to the isolated instance."""
    # Build a dedicated test storage folder target location paths
    test_output_dir = Path(__file__).parent / "test_certificates"
    
    # Instantiate the application running on top of test connection objects
    app = create_app(engine=test_engine, output_dir=test_output_dir)
    
    with TestClient(app) as test_client:
        yield test_client
        
    # Clean up test directories on hard terminations
    if test_output_dir.exists():
        import shutil
        shutil.rmtree(test_output_dir)
