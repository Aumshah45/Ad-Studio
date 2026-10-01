"""ev-0.8 is frozen (evaluator/FROZEN.md): a threshold, judge prompt or judge-cache change without
an `evaluator_version` bump (and a new FROZEN.md record) fails here."""

import hashlib
import json
import re

import yaml

from backend.domain.adstudio.evaluator.config import CONFIG_PATH, load_config
from backend.llm.prompts.ad_inspect import AD_INSPECT
from backend.llm.prompts.composition_judge import COMPOSITION_JUDGE
from backend.llm.prompts.context_judge import CONTEXT_JUDGE

FROZEN_MD = CONFIG_PATH.parent / "FROZEN.md"


def _record() -> dict[str, object]:
    block = re.search(r"```json\n(.*?)\n```", FROZEN_MD.read_text(encoding="utf-8"), re.DOTALL)
    assert block, "FROZEN.md has no machine-readable json block"
    return json.loads(block.group(1))


def config_fingerprint() -> str:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def test_frozen_evaluator_is_unchanged() -> None:
    record = _record()
    cfg = load_config()
    hint = (
        "the evaluator is frozen (evaluator/FROZEN.md): bump evaluator_version and write a new "
        "FROZEN.md record for any change"
    )
    assert cfg.evaluator_version == record["evaluator_version"], hint
    assert config_fingerprint() == record["config_sha256"], f"a threshold changed: {hint}"
    assert cfg.judge_cache_version == record["judge_cache_version"], hint
    assert {
        "ad_inspect": AD_INSPECT.version,
        "context_judge": CONTEXT_JUDGE.version,
        "composition_judge": COMPOSITION_JUDGE.version,
    } == record["judge_prompts"], f"a judge prompt changed: {hint}"
