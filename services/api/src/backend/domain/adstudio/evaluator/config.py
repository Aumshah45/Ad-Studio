"""Typed evaluator thresholds loaded from `evaluator_config.yaml`."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel

CONFIG_PATH = Path(__file__).resolve().parent / "evaluator_config.yaml"


class TechnicalConfig(BaseModel):
    aspect_tolerance: float
    blank_std_min: float
    copy_ssim_max: float
    placeholder_tile_px: int
    placeholder_tile_std: float
    placeholder_fraction_max: float


class TextConfig(BaseModel):
    cer_max: float
    reread_upscale: int = 4
    zone_margin: float
    zone_upscale: int
    stray_min_conf: float
    stray_min_alnum: int
    stray_reference_ratio: float
    product_box_margin: float
    zone_extra_min_alnum: int
    zone_extra_min_sources: int


class ProductConfig(BaseModel):
    delta_e_max: float
    delta_e_kl: float
    cluster_delta_e_max: float
    cluster_min_weight: float
    kmeans_k: int
    kmeans_seed: int
    sample_px: int
    crop_max_px: int
    border_fraction: float
    background_delta: float
    mask_min_fraction: float
    max_count: int
    unsure_fails: bool
    note_checks: list[str]
    hue_chroma_min: float = 20.0
    hue_drift_note_deg: float = 10.0


class ContextConfig(BaseModel):
    unsure_must_fails: bool
    avoid_check_severity: Literal["must", "should"]
    people_check_severity: Literal["must", "should", "off"] = "off"


class CompositionConfig(BaseModel):
    unsure_fails: bool
    area_extreme_over: float
    area_extreme_under: float
    scale_ratio_min: float = 0.6
    scale_ratio_max: float = 1.6
    pasted_signs_max: int | None = None


class RepairConfig(BaseModel):
    stall_ssim: float


class EvaluatorConfig(BaseModel):
    evaluator_version: str
    judge_cache_version: str
    technical: TechnicalConfig
    text: TextConfig
    product: ProductConfig
    context: ContextConfig
    composition: CompositionConfig
    repair: RepairConfig


@lru_cache(maxsize=1)
def load_config(path: Path = CONFIG_PATH) -> EvaluatorConfig:
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    return EvaluatorConfig.model_validate(raw)
