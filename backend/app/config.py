"""Configuration loaded from environment, with Secrets Manager fallback in AWS.

DATABASE_URL and JWT_SECRET are injected directly into the ECS task from Secrets
Manager (see infra/ecs.tf). For flexibility, if either is absent but a corresponding
secret ARN is provided, it is fetched here at startup. Nothing secret is ever logged.
"""

from __future__ import annotations

import functools
import json
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _fetch_secret(arn: str, region: str) -> str:
    import boto3  # imported lazily so local dev without AWS still works

    client = boto3.client("secretsmanager", region_name=region)
    resp = client.get_secret_value(SecretId=arn)
    return resp["SecretString"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    aws_region: str = Field(default="ap-south-1")
    resource_prefix: str = Field(default="blucheck")
    environment: str = Field(default="production")

    # Auth
    jwt_secret: str = Field(default="")
    jwt_alg: str = Field(default="HS256")
    jwt_ttl_minutes: int = Field(default=720)
    blucheck_jwt_secret_arn: str = Field(default="")

    # Database
    database_url: str = Field(default="")
    blucheck_db_secret_arn: str = Field(default="")

    # Storage / queue
    media_bucket: str = Field(default="")
    dashboard_bucket: str = Field(default="")
    extraction_queue_url: str = Field(default="")
    kms_key_id: str = Field(default="")

    # URL TTLs (seconds)
    upload_url_ttl: int = Field(default=3600)
    frame_url_ttl: int = Field(default=300)

    # Upload tuning
    multipart_part_size: int = Field(default=8 * 1024 * 1024)  # 8 MiB parts

    # CORS
    dashboard_origin: str = Field(default="http://localhost:3000")

    def resolve_secrets(self) -> None:
        """Best-effort: populate database_url / jwt_secret from Secrets Manager when only ARNs
        are set. Deliberately does NOT raise on missing values, so importing the app never
        requires AWS (unit tests, tooling). Presence is enforced at startup by validate_runtime.
        """
        if not self.database_url and self.blucheck_db_secret_arn:
            self.database_url = _fetch_secret(self.blucheck_db_secret_arn, self.aws_region)
        if not self.jwt_secret and self.blucheck_jwt_secret_arn:
            self.jwt_secret = _fetch_secret(self.blucheck_jwt_secret_arn, self.aws_region)

    def validate_runtime(self) -> None:
        """Fail fast at application startup if the runtime configuration is incomplete."""
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set and could not be resolved from Secrets Manager"
            )
        if not self.jwt_secret:
            raise RuntimeError(
                "JWT_SECRET is not set and could not be resolved from Secrets Manager"
            )


@functools.lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.resolve_secrets()
    return settings
