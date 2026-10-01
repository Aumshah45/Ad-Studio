"""FastAPI dependencies shared by routers."""

from fastapi import Request

from backend.core.settings import Settings
from backend.domain.adstudio.vision import VisionClient
from backend.llm.gateway import LlmGateway
from backend.storage.blobs import BlobStore


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_gateway(request: Request) -> LlmGateway:
    return request.app.state.gateway


def get_blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def get_tenant(request: Request) -> str:
    """The single demo tenant; swapping this for OIDC claims is the multi-tenant upgrade path."""
    settings: Settings = request.app.state.settings
    return settings.default_tenant


def get_vision(request: Request) -> VisionClient:
    return request.app.state.vision
