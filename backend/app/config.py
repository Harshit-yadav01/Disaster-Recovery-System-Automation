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
    # Optional path to the array's self-signed certificate (PEM). When set, TLS
    # is verified against this cert instead of being disabled (more secure).
    alletra_ca_cert: str = ""
    alletra_timeout: int = 15
    # SSH port for the CLI-based DR automation (read/verify + failover/failback).
    # Reuses ALLETRA_USERNAME / ALLETRA_PASSWORD for the SSH login.
    alletra_ssh_port: int = 22
    # Deprecated single-array alias (used as primary if the primary URL is blank).
    alletra_base_url: str = ""

    # --- Present-to-host (after failover, export DR volumes to DR ESXi) ---
    # Target the DR array exports the failed-over group's volumes to. Use a host
    # set with the "set:" prefix (e.g. "set:DR_Intern_Automation") or a single
    # host name. Leave blank until configured; the UI can also override per-run.
    dr_host_target: str = ""
    # LUN assignment strategy when presenting: "match" = reuse each volume's
    # primary-side LUN (falls back to auto if unknown); "auto" = let the array pick.
    dr_present_lun: str = "match"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a clean list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
