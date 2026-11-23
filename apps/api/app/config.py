"""Runtime configuration (environment variables, see infra/docker-compose.yml)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    env: Literal["dev", "test", "prod"] = "dev"

    # Postgres: the API runs as the restricted app role, Alembic as the schema owner.
    database_url: str = "postgresql+asyncpg://revgate_app:revgate_app_dev@localhost:5432/revgate"
    migration_database_url: str = "postgresql+asyncpg://revgate:revgate@localhost:5432/revgate"
    app_db_role: str = "revgate_app"
    app_db_password: str = "revgate_app_dev"

    redis_url: str = "redis://localhost:6379/0"

    # S3 / R2 / MinIO. Presigned URLs are signed against the *public* endpoint (what browsers reach).
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "revgate"
    s3_secret_access_key: str = "revgate-dev-secret"
    s3_region: str = "us-east-1"
    s3_bucket_raw: str = "revgate-raw-cad"
    s3_bucket_viewer: str = "revgate-viewer-derived"
    s3_bucket_audit: str = "revgate-audit-proofs"

    web_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    # Cookies (blueprint §4): rg_sid 7 d, rg_ext_sid 24 h, HttpOnly + Secure + SameSite=Lax.
    cookie_secure: bool = True
    session_ttl_sec: int = 7 * 24 * 3600
    ext_session_ttl_sec: int = 24 * 3600
    magic_link_ttl_sec: int = 15 * 60

    # External 6-digit OTP (blueprint §3 otp_sessions / §4).
    otp_ttl_sec: int = 300
    otp_max_attempts: int = 5
    otp_rate_limit: int = 3
    otp_rate_window_sec: int = 600
    otp_hmac_secret: str = "dev-only-otp-hmac-secret-change-me"
    ip_hash_salt: str = "dev-only-ip-salt"

    # Uploads / signed URLs.
    max_upload_bytes: int = 50 * 1024 * 1024
    put_url_ttl_sec: int = 600
    get_url_ttl_sec: int = 900

    # Approval proofs (WORM bucket, S3 Object Lock).
    audit_lock_mode: Literal["GOVERNANCE", "COMPLIANCE"] = "GOVERNANCE"
    audit_retention_days: int = 1825

    # Local development hook: every outgoing mail/SMS is also pushed to the Redis list `rg:dev:outbox`.
    dev_outbox: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
