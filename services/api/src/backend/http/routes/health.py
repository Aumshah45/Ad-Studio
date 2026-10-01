"""Liveness and readiness."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.settings import Settings
from backend.db.session import Database
from backend.domain.adstudio.evaluator.ocr import TesseractOcr
from backend.domain.adstudio.overlay import raqm_available
from backend.domain.adstudio.pipeline import PipelineDeps
from backend.http.deps import get_app_settings
from backend.llm.config import ProviderStatus, provider_statuses

router = APIRouter(tags=["health"])
REQUIRED_EXTENSIONS = ("vector", "pg_trgm", "fuzzystrmatch")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ModelRow(BaseModel):
    role: Literal["image_candidate", "image_repair", "vision_judge"]
    spec: str = Field(description="The configured model (IMAGE_MODEL_* / VISION_JUDGE)")
    client: str = Field(description="gemini | fake")
    recorded_as: str = Field(description="The model id calls are recorded under in the ledger")
    configured: bool
    breaker: str = "closed"


class OcrStatus(BaseModel):
    engine: str
    available: bool
    version: str
    languages: int = Field(description="Installed Tesseract language packs")
    scripts: dict[str, bool] = Field(
        description="Scripts with an OCR pack (others are verified by construction after overlay)"
    )
    shaping: bool = Field(description="libraqm available for complex-script overlay")


class AdStudioStatus(BaseModel):
    status: Literal["ok", "degraded"]
    models: list[ModelRow]
    ocr: OcrStatus
    detail: str | None = None


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    db: bool
    extensions: dict[str, bool]
    providers: list[ProviderStatus]
    detail: str | None = None
    adstudio: AdStudioStatus | None = Field(
        default=None, description="Image-model, vision-judge and OCR rows for the status page"
    )


OCR_SCRIPTS = ("Latn", "Deva", "Jpan", "Hang", "Hans", "Thai", "Arab")


def adstudio_status(
    settings: Settings, pipeline: PipelineDeps, breakers: dict[str, str]
) -> AdStudioStatus:
    image = pipeline.image_client
    vision = pipeline.evaluator.vision
    rows: list[ModelRow] = []
    image_roles: tuple[tuple[Literal["image_candidate", "image_repair"], str], ...] = (
        ("image_candidate", settings.image_model_candidate),
        ("image_repair", settings.image_model_repair),
    )
    for role, spec in image_roles:
        recorded = image.model_for(spec)
        rows.append(
            ModelRow(
                role=role,
                spec=spec,
                client=image.name,
                recorded_as=recorded,
                configured=image.configured(),
                breaker=breakers.get(recorded, "closed"),
            )
        )
    judge_model = vision.model if vision is not None else settings.vision_judge
    rows.append(
        ModelRow(
            role="vision_judge",
            spec=settings.vision_judge,
            client=vision.name if vision is not None else "none",
            recorded_as=judge_model,
            configured=bool(vision is not None and vision.configured()),
            breaker=breakers.get(judge_model, "closed"),
        )
    )
    ocr = pipeline.evaluator.ocr
    available = bool(ocr is not None and ocr.available())
    langs = ocr.languages() if isinstance(ocr, TesseractOcr) else frozenset[str]()
    ocr2 = pipeline.evaluator.ocr2
    second_name = ocr2.name if ocr2 is not None and ocr2.available() else ""
    ocr_status = OcrStatus(
        # ev-0.8: "tesseract+apple_vision" when the second engine of the ensemble is on.
        engine=(ocr.name if ocr is not None else "none")
        + (f"+{second_name}" if second_name else ""),
        available=available,
        version=str(getattr(ocr, "version", "unknown"))
        + (f" / {getattr(ocr2, 'version', '?')}" if second_name else ""),
        languages=len(langs),
        scripts={s: bool(ocr is not None and ocr.lang_for(s) is not None) for s in OCR_SCRIPTS},
        shaping=raqm_available(),
    )
    problems = [f"{r.role} not configured" for r in rows if not r.configured]
    if not available:
        problems.append("OCR unavailable: text can't be verified, every run needs review")
    return AdStudioStatus(
        status="degraded" if problems else "ok",
        models=rows,
        ocr=ocr_status,
        detail="; ".join(problems) or None,
    )


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready", responses={503: {"model": ReadyResponse}})
async def ready(
    request: Request, response: Response, settings: Annotated[Settings, Depends(get_app_settings)]
) -> ReadyResponse:
    db: Database = request.app.state.db
    extensions: dict[str, bool] = dict.fromkeys(REQUIRED_EXTENSIONS, False)
    db_ok, detail = False, None
    try:
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            rows = await conn.execute(text("SELECT extname FROM pg_extension"))
            installed = {r[0] for r in rows}
            extensions = {name: name in installed for name in REQUIRED_EXTENSIONS}
            db_ok = True
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        detail = f"database error: {type(exc).__name__}"

    breakers = request.app.state.runtime.breakers.states()
    providers = [
        p.model_copy(update={"breaker": breakers.get(p.spec, "closed")})
        for p in provider_statuses(settings)
    ]
    if not db_ok or not all(extensions.values()):
        response.status_code = 503
        status: Literal["ok", "degraded", "unavailable"] = "unavailable"
        detail = detail or "missing extensions"
    elif not any(p.configured and p.role == "chain" for p in providers):
        status, detail = "degraded", "no model provider configured"
    else:
        status = "ok"
    return ReadyResponse(
        status=status,
        db=db_ok,
        extensions=extensions,
        providers=providers,
        detail=detail,
        adstudio=adstudio_status(settings, request.app.state.pipeline, breakers),
    )
