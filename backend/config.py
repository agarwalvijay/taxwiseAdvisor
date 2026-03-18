from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = Field(default="")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://user:password@localhost:5432/taxwise"
    )

    # S3
    aws_access_key_id: str = Field(default="")
    aws_secret_access_key: str = Field(default="")
    aws_region: str = Field(default="us-east-1")
    s3_bucket_name: str = Field(default="taxwise-documents")

    # Auth (Clerk)
    clerk_secret_key: str = Field(default="")
    next_public_clerk_publishable_key: str = Field(default="")

    # App
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Confidence thresholds — configurable without code changes
    confidence_threshold_classification: float = Field(default=0.90)
    confidence_threshold_hard_required: float = Field(default=0.85)
    confidence_threshold_soft_required: float = Field(default=0.75)
    confidence_threshold_optional: float = Field(default=0.60)

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
