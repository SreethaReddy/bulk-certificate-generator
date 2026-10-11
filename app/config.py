"""Application settings, loaded from environment variables / the .env file.

Nothing secret is hard-coded here: DB_USER and DB_PASSWORD have no defaults,
so the app refuses to start if they are not provided.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "certigen_user"
    db_password: str  = "BulkCertificateGenerator"
    db_name: str = "certigen"
    test_db_name: str = "certigen_test"

    output_dir: Path = Path("generated_certificates")
    max_recipients_per_job: int = 1000

    def database_url(self, database: str | None = None) -> URL:
        """Build the connection URL.

        URL.create() escapes special characters in the password (e.g. '@' or
        '/'), which naive string formatting would break on.
        """
        return URL.create(
            drivername="mysql+pymysql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=database or self.db_name,
            query={"charset": "utf8mb4"},
        )

    @property
    def resolved_output_dir(self) -> Path:
        """Absolute output directory (relative values are anchored to the project root)."""
        path = self.output_dir
        return path if path.is_absolute() else BASE_DIR / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
