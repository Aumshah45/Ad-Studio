"""Parse model specs from settings and build pydantic-ai models with explicit provider keys.

Hosted providers only (no local LLMs). `test:<text>` maps to `TestModel` (tests, key-less runs).
"""

from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from backend.core.errors import AppError
from backend.core.settings import Settings

SUPPORTED_PROVIDERS = ("google", "groq", "openrouter", "test")
_RATE_LIMIT_TEXT = (
    "rate limit",
    "rate_limit",
    "quota",
    "resource_exhausted",
    "429",
    "overloaded",
)


@dataclass(frozen=True)
class ModelSpec:
    raw: str
    provider: str
    name: str


class ProviderStatus(BaseModel):
    spec: str
    provider: str
    role: str
    configured: bool
    breaker: str = "closed"


def parse_spec(raw: str) -> ModelSpec:
    provider, sep, name = raw.strip().partition(":")
    if not sep or not name or provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported model spec {raw!r}; expected one of {SUPPORTED_PROVIDERS}")
    return ModelSpec(raw=raw.strip(), provider=provider, name=name)


def _key(settings: Settings, provider: str) -> str:
    keys = {
        "google": settings.google_api_key,
        "groq": settings.groq_api_key,
        "openrouter": settings.openrouter_api_key,
    }
    secret = keys.get(provider)
    return secret.get_secret_value() if secret is not None else ""


def is_configured(spec: ModelSpec, settings: Settings) -> bool:
    return spec.provider == "test" or bool(_key(settings, spec.provider))


def build_model(spec: ModelSpec, settings: Settings) -> Model | None:
    """The pydantic-ai model for a spec, or None when its provider key is missing."""
    if spec.provider == "test":
        return TestModel(custom_output_text=spec.name, model_name=spec.raw)
    key = _key(settings, spec.provider)
    if not key:
        return None
    if spec.provider == "google":
        return GoogleModel(spec.name, provider=GoogleProvider(api_key=key))
    if spec.provider == "groq":
        return GroqModel(spec.name, provider=GroqProvider(api_key=key))
    return OpenRouterModel(spec.name, provider=OpenRouterProvider(api_key=key))


def chain_specs(settings: Settings, *, judge: bool = False) -> list[ModelSpec]:
    raws = [settings.llm_judge] if judge else [settings.llm_primary, *settings.llm_fallbacks]
    return [parse_spec(r) for r in raws if r.strip()]


def _rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_TEXT)


def _bad_finish(response: ModelResponse) -> bool:
    return response.finish_reason in ("length", "content_filter", "error")


def build_chain(
    settings: Settings, *, exclude: tuple[str, ...] = (), judge: bool = False
) -> tuple[Model, list[ModelSpec]]:
    """Primary + configured fallbacks as one model; raises 503 `llm-unconfigured` if none."""
    pairs: list[tuple[Model, ModelSpec]] = []
    for spec in chain_specs(settings, judge=judge):
        if spec.raw in exclude:
            continue
        model = build_model(spec, settings)
        if model is not None:
            pairs.append((model, spec))
    if not pairs:
        raise AppError(
            503,
            "llm-unconfigured",
            "No model provider configured",
            "Set a provider API key (e.g. GOOGLE_API_KEY or GROQ_API_KEY) in .env.",
        )
    models = [m for m, _ in pairs]
    specs = [s for _, s in pairs]
    if len(models) == 1:
        return models[0], specs
    return FallbackModel(
        models[0], *models[1:], fallback_on=[ModelAPIError, _rate_limited, _bad_finish]
    ), specs


# Model-id prefixes known to accept image input and answer in text (vision roles), and prefixes
# known to generate images (image roles). Anything else is treated as text-only for that role.
VISION_INPUT_PREFIXES = (
    "google:gemini-",
    "groq:meta-llama/llama-4-",
    "openrouter:google/gemini-",
    "test:",
)
IMAGE_OUTPUT_PREFIXES = ("google:gemini-", "test:")
# Customer images (reference photos, generated ads) may only reach the billed Google project
# (production-readiness D9): paid-service terms. `test` is the offline TestModel (no network).
IMAGE_DATA_PROVIDERS = ("google", "test")


class ModelRoleError(ValueError):
    """A model role is mapped to a model that cannot do the job (raised at startup)."""


def _vision_capable(spec: ModelSpec) -> bool:
    return spec.raw.startswith(VISION_INPUT_PREFIXES) and not spec.name.endswith("-image")


def _image_capable(spec: ModelSpec) -> bool:
    return spec.provider == "test" or (
        spec.raw.startswith(IMAGE_OUTPUT_PREFIXES) and spec.name.endswith("-image")
    )


def image_data_roles(settings: Settings) -> dict[str, str]:
    """Every role whose calls carry customer images (env name -> spec)."""
    return {
        "VISION_JUDGE": settings.vision_judge,
        "IMAGE_MODEL_CANDIDATE": settings.image_model_candidate,
        "IMAGE_MODEL_REPAIR": settings.image_model_repair,
    }


def ensure_image_data_provider(spec: ModelSpec) -> None:
    """Runtime twin of the startup check: refuse to send images to any non-Google provider."""
    if spec.provider not in IMAGE_DATA_PROVIDERS:
        raise AppError(
            503,
            "image-provider-refused",
            "Image data provider refused",
            f"{spec.raw} is not on the billed Google project; images are never sent there.",
        )


def validate_model_roles(settings: Settings) -> None:
    """Fail fast when a role that receives images is not a `google:` model (D9), a vision role
    maps to a text-only model, or an image role to a non-image one.

    `LLM_JUDGE` stays a text role; `VISION_JUDGE` must see images (architecture reconciliation).
    Empty roles are allowed: the pipeline then fails closed at run time.
    """
    problems: list[str] = []
    for env, raw in image_data_roles(settings).items():
        if raw.strip() and parse_spec(raw).provider not in IMAGE_DATA_PROVIDERS:
            problems.append(
                f"{env}={raw!r} would send customer images to a non-Google provider; "
                "only the billed Google project may receive images"
            )
    vision_roles = {"VISION_JUDGE": settings.vision_judge}
    image_roles = {
        "IMAGE_MODEL_CANDIDATE": settings.image_model_candidate,
        "IMAGE_MODEL_REPAIR": settings.image_model_repair,
    }
    for env, raw in vision_roles.items():
        if raw.strip() and not _vision_capable(parse_spec(raw)):
            problems.append(f"{env}={raw!r} is not a vision (image-input) model")
    for env, raw in image_roles.items():
        if raw.strip() and not _image_capable(parse_spec(raw)):
            problems.append(f"{env}={raw!r} is not an image-generation model")
    if problems:
        raise ModelRoleError("; ".join(problems))


def role_specs(settings: Settings) -> list[tuple[str, ModelSpec]]:
    pairs: list[tuple[str, ModelSpec]] = [("chain", s) for s in chain_specs(settings)]
    pairs += [("judge", s) for s in chain_specs(settings, judge=True)]
    for role, raw in (
        ("vision_judge", settings.vision_judge),
        ("image_candidate", settings.image_model_candidate),
        ("image_repair", settings.image_model_repair),
    ):
        if raw.strip():
            pairs.append((role, parse_spec(raw)))
    return pairs


def provider_statuses(settings: Settings) -> list[ProviderStatus]:
    return [
        ProviderStatus(
            spec=spec.raw,
            provider=spec.provider,
            role=role,
            configured=is_configured(spec, settings),
        )
        for role, spec in role_specs(settings)
    ]
