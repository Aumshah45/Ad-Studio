import pytest

from backend.core.settings import Settings
from backend.http.app import create_app
from backend.llm.config import ModelRoleError, provider_statuses, validate_model_roles
from tests.conftest import make_settings


def test_vision_role_rejects_text_model() -> None:
    with pytest.raises(ModelRoleError, match="VISION_JUDGE"):
        validate_model_roles(make_settings(vision_judge="groq:openai/gpt-oss-120b"))
    with pytest.raises(ModelRoleError, match="VISION_JUDGE"):
        validate_model_roles(make_settings(vision_judge="groq:llama-3.3-70b-versatile"))
    with pytest.raises(ModelRoleError):
        create_app(make_settings(vision_judge="groq:openai/gpt-oss-120b"))


def test_image_role_rejects_non_image_model() -> None:
    with pytest.raises(ModelRoleError, match="IMAGE_MODEL_REPAIR"):
        validate_model_roles(make_settings(image_model_repair="google:gemini-3.8-flash"))
    with pytest.raises(ModelRoleError, match="VISION_JUDGE"):
        validate_model_roles(make_settings(vision_judge="google:gemini-3.1-flash-image"))


def test_default_roles_are_valid_and_reported() -> None:
    settings = make_settings()
    validate_model_roles(settings)
    validate_model_roles(make_settings(vision_judge=""))
    validate_model_roles(make_settings(vision_judge="test:judge", image_model_candidate="test:x"))
    roles = {p.role for p in provider_statuses(settings)}
    assert {"chain", "vision_judge", "image_candidate", "image_repair"} <= roles


def test_new_settings_load_from_env_example() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[4] / ".env.example"
    values = {
        k.lower(): v
        for k, _, v in (
            line.partition("=") for line in example.read_text().splitlines() if "=" in line
        )
        if not k.startswith("#")
    }
    s = Settings(_env_file=None, **values)  # type: ignore[call-arg]  # init kwargs beat the process env
    assert (s.n_candidates, s.max_repairs) == (2, 2)
    assert s.ad_budget_usd == 0.25 and s.daily_budget_usd == 3 and s.ad_wallclock_s == 150
    assert s.image_timeout_s == 60
    assert s.image_model_candidate.endswith("-image") and s.image_model_repair.endswith("-image")
    assert s.vision_judge.startswith("google:gemini-")
