"""Product profile -> `products.reference_facts` (versioned by `facts_version`).

Computed at upload when a vision judge is configured, otherwise lazily at the first run that has
one. Without a judge (or when it fails) the facts are stored as `status: unverified`, which the
planner treats as "no facts" and the evaluator as "no reference box" (the product dimension is then
unverified anyway, so nothing auto-passes on missing facts).

Label text read off the product is **data**: it is stored, screened with `injection_signals`
(flagged, never obeyed) and only ever quoted as "label text to preserve".
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import models_adstudio as m
from backend.domain.adstudio.vision import ProductProfile, VisionClient
from backend.guardrails.input import injection_signals
from backend.llm.cache import CacheMissError
from backend.llm.prompts.product_profile import PRODUCT_PROFILE

PROFILE_VERSION = "pp-2"  # pp-2: real-world size (ADR-007)
FACTS_VERSION = f"{PROFILE_VERSION}+{PRODUCT_PROFILE.name}@{PRODUCT_PROFILE.version}"
log = structlog.get_logger(__name__)


def facts_from_profile(profile: ProductProfile, *, model: str) -> dict[str, Any]:
    flags = injection_signals("\n".join(profile.visible_text))
    return {
        "status": "verified" if profile.is_product else "not_product",
        "profile_version": PROFILE_VERSION,
        "prompt": f"{PRODUCT_PROFILE.name}@{PRODUCT_PROFILE.version}",
        "model": model,
        **profile.model_dump(mode="json"),
        "label_injection_flags": flags,
    }


def unverified_facts(reason: str) -> dict[str, Any]:
    return {"status": "unverified", "profile_version": PROFILE_VERSION, "reason": reason}


def _is_offline_model(model: object) -> bool:
    return isinstance(model, str) and model.startswith("test:")


def needs_profile(facts: dict[str, Any] | None, judge_model: str | None = None) -> bool:
    """Missing or unverified facts need a profile; so do facts written by an offline (`test:`)
    judge once a real judge is configured (a keyless session's fake facts must never feed a live
    run: they say "no label, no colours" and a near-full-frame box), and facts from an older
    profile version once any judge is configured (pp-1 has no real-world size)."""
    if not facts or facts.get("status") not in ("verified", "not_product"):
        return True
    if judge_model is None:
        return False
    if facts.get("profile_version") != PROFILE_VERSION:
        return True
    return not _is_offline_model(judge_model) and _is_offline_model(facts.get("model"))


async def compute_facts(vision: VisionClient | None, reference: bytes) -> dict[str, Any]:
    """The facts record for a reference photo; never raises except on a stale replay snapshot."""
    if vision is None or not vision.configured():
        return unverified_facts("no vision judge configured")
    try:
        profile = await vision.profile(reference)
    except CacheMissError:
        raise
    except Exception as exc:  # noqa: BLE001 - a judge outage leaves the facts unverified
        log.warning("product_profile_failed", error_type=type(exc).__name__)
        return unverified_facts(f"vision judge unavailable ({type(exc).__name__})")
    return facts_from_profile(profile.sanitized(), model=vision.model)


async def ensure_reference_facts(
    session: AsyncSession,
    product: m.Product,
    reference: bytes,
    vision: VisionClient | None,
) -> dict[str, Any] | None:
    """Fill `reference_facts` if it is missing or unverified and a judge is now available."""
    judge_model = vision.model if vision is not None and vision.configured() else None
    if not needs_profile(product.reference_facts, judge_model):
        return product.reference_facts
    if vision is None or not vision.configured():
        if product.reference_facts is None:
            product.reference_facts = unverified_facts("no vision judge configured")
            product.facts_version = FACTS_VERSION
            await session.commit()
        return product.reference_facts
    product.reference_facts = await compute_facts(vision, reference)
    product.facts_version = FACTS_VERSION
    await session.commit()
    return product.reference_facts
