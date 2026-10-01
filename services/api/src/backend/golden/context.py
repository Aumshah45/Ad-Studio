"""The golden CLI's process wiring: the same pipeline dependencies as `create_app`, without HTTP."""

from dataclasses import dataclass

from backend.core.settings import Settings
from backend.db.session import Database, create_database
from backend.domain.adstudio.evaluator.core import JUDGE_CACHE_VERSION, Evaluator
from backend.domain.adstudio.evaluator.ocr import TesseractOcr, second_engine
from backend.domain.adstudio.image_clients import build_image_client
from backend.domain.adstudio.pipeline import PipelineDeps
from backend.domain.adstudio.planner import Planner
from backend.domain.adstudio.vision import build_vision_client
from backend.llm.cache import DbCache
from backend.llm.calls import CallRuntime, set_runtime
from backend.llm.config import validate_model_roles
from backend.llm.gateway import LlmGateway
from backend.llm.ledger import DbRecorder
from backend.storage.blobs import BlobStore


@dataclass
class GoldenContext:
    settings: Settings
    db: Database
    deps: PipelineDeps

    @property
    def tenant(self) -> str:
        return self.settings.default_tenant

    async def close(self) -> None:
        await self.db.dispose()


def build_context(settings: Settings) -> GoldenContext:
    validate_model_roles(settings)
    db = create_database(settings.database_url)
    runtime = CallRuntime(
        recorder=DbRecorder(db.sessionmaker),
        cache=DbCache(db.sessionmaker),
        max_concurrency=settings.llm_max_concurrency,
        timeout_s=settings.llm_timeout_s,
    )
    set_runtime(runtime)
    gateway = LlmGateway(settings, runtime)
    vision = build_vision_client(settings, gateway, cache_version=JUDGE_CACHE_VERSION)
    deps = PipelineDeps(
        settings=settings,
        sessionmaker=db.sessionmaker,
        blobs=BlobStore(settings.blob_root),
        runtime=runtime,
        image_client=build_image_client(settings),
        gateway=gateway,
        evaluator=Evaluator(
            TesseractOcr(runtime),
            vision=vision,
            ocr2=second_engine(runtime, settings.ocr_apple_vision),
        ),
        planner=Planner(gateway),
    )
    return GoldenContext(settings, db, deps)
