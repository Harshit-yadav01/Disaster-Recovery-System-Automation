"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed settings sourced from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "development"

    # Auth
    secret_key: str = "change-me-to-a-long-random-secret"
    access_token_expire_minutes: int = 60
    dashboard_username: str = "admin"
    dashboard_password: str = "admin"

    # CORS
    cors_origins: str = (
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://localhost:5500,http://127.0.0.1:5500"
    )

    # Storage backend selection: "simulated" or "alletra"
    storage_provider: str = "simulated"

    # Real HPE Alletra Storage MP (WSAPI) connection.
    # Provide management IPs/hostnames only; the provider adds https:// and :443.
    alletra_primary_base_url: str = ""
    alletra_recovery_base_url: str = ""
    # Shared login used for both arrays.
    alletra_username: str = ""
    alletra_password: str = ""
    alletra_verify_ssl: bool = False
    alletra_timeout: int = 15
    # Deprecated single-array alias (used as primary if the primary URL is blank).
    alletra_base_url: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a clean list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
