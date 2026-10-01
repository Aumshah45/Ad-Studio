"""Typed settings for every environment variable in `.env.example`."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[3]  # services/api


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "dev"
    project_name: str = "ad-studio"
    database_url: str = "postgresql+psycopg://localhost:5432/ad_studio"
    test_database_url: str = "postgresql+psycopg://localhost:5432/ad_studio_test"
    next_public_api_url: str = "http://localhost:8000"
    web_origin: str = "http://localhost:3000"

    llm_primary: str = ""
    llm_fallbacks: Annotated[list[str], NoDecode] = Field(default_factory=list[str])
    llm_judge: str = ""
    llm_timeout_s: float = 30.0
    llm_max_concurrency: int = 3

    # Ad studio model roles. The VISION_JUDGE default is a placeholder, to be confirmed by the
    # slice-1 image-model spike (docs/stack-lock.md).
    image_model_candidate: str = "google:gemini-3.1-flash-lite-image"
    image_model_repair: str = "google:gemini-3.1-flash-image"
    vision_judge: str = "google:gemini-3.8-flash"
    image_timeout_s: float = 60.0
    # `fake` composites the product photo into a template scene locally (no key, no network);
    # `gemini` calls the image models above and needs GOOGLE_API_KEY.
    image_client: Literal["gemini", "fake"] = "gemini"
    # `fake` answers vision checks locally with scripted verdicts (key-less dev and tests only);
    # `gemini` calls VISION_JUDGE through the gateway. No judge -> vision checks are unverified.
    vision_client: Literal["gemini", "fake"] = "gemini"
    # ev-0.8: Apple Vision as the second OCR engine of the text ensemble (macOS only; ignored
    # elsewhere). Local and deterministic, no network. Warmed in the background at startup.
    ocr_apple_vision: bool = True
    # google-genai surface for image calls; the slice-1 spike confirms which one works.
    image_api: Literal["interactions", "generate_content"] = "interactions"
    # Offline demo only (IMAGE_CLIENT=fake): scripted failures per request label, e.g.
    # "candidate=typo;repair_text=typo" (see `image_clients.parse_fake_script`). Empty = clean.
    fake_image_script: str = ""

    # Ad studio pipeline limits (architecture "Cross-doc reconciliation").
    n_candidates: int = Field(default=2, ge=1, le=4)
    max_repairs: int = Field(default=2, ge=0, le=4)
    # Native text edits before the deterministic overlay (ai-design §4.1; 0 = overlay at once).
    text_repair_attempts: int = Field(default=1, ge=0, le=2)
    ad_budget_usd: float = Field(default=0.25, gt=0)
    max_image_calls: int = Field(default=5, ge=1, le=10)
    daily_budget_usd: float = Field(default=3.0, gt=0)
    ad_wallclock_s: float = Field(default=150.0, gt=0)

    # In-process run runner (ADR-002): executing runs and the queue cap before 429.
    runs_max_concurrent: int = Field(default=4, ge=1, le=32)
    runs_queue_cap: int = Field(default=20, ge=1, le=1000)

    google_api_key: SecretStr = SecretStr("")
    groq_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:6006/v1/traces"

    # Content-addressed image store (ADR-003); relative paths resolve against services/api.
    blob_dir: Path = Path("var/blobs")
    default_tenant: str = "demo"

    rate_limit_per_min: int = 120
    # Per-IP admission limits (production-readiness T3); 0 disables a limit (dev/demo can raise).
    runs_per_hour_per_ip: int = Field(default=10, ge=0)
    runs_concurrent_per_ip: int = Field(default=2, ge=0)
    products_per_hour_per_ip: int = Field(default=30, ge=0)
    # The scaffold's open text proxies `/v1/llm/complete|stream`: None = on only when APP_ENV=dev.
    llm_dev_routes: bool | None = None
    max_body_bytes: int = 12_582_912  # 12 MiB: uploads are capped at 10 MB below it
    max_upload_bytes: int = 10_485_760
    max_input_chars: int = 20_000

    @field_validator("llm_fallbacks", mode="before")
    @classmethod
    def _split_fallbacks(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def blob_root(self) -> Path:
        return self.blob_dir if self.blob_dir.is_absolute() else API_DIR / self.blob_dir

    @property
    def llm_dev_routes_enabled(self) -> bool:
        if self.llm_dev_routes is not None:
            return self.llm_dev_routes
        return self.app_env == "dev"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
