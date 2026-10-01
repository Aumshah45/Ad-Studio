"""Batch checks over the committed golden outputs of every version (data/golden/v1, v2; ADR-007)."""

import pytest

from backend.golden.dataset import (
    GOLDEN_DIR,
    GOLDEN_VERSIONS,
    GoldenPaths,
    golden_out,
    read_labels,
    read_outputs,
)
from backend.golden.harness import image_size

VERSIONS = [v for v in GOLDEN_VERSIONS if list((golden_out(v) / "outputs").glob("*.png"))]


def test_v1_is_committed_in_the_versioned_layout() -> None:
    assert "v1" in VERSIONS
    assert not (GOLDEN_DIR / "outputs").exists() and not (GOLDEN_DIR / "labels.csv").exists()
    v1 = GoldenPaths(out=golden_out("v1"))
    assert v1.version == "v1" and v1.verdicts.exists() and v1.labels.exists()
    # The human baseline: 40 rubric-1 rows, frozen in labels_rubric1.csv.
    frozen = read_labels(v1.out / "labels_rubric1.csv")
    assert len(frozen) == 40 and {r.rubric_version for r in frozen.values()} == {"1"}
    assert all(r.composition is None for r in frozen.values())


@pytest.mark.parametrize("version", VERSIONS)
def test_resolution_cap(version: str) -> None:
    """C1 over every stored golden output: long edge <= 1024 px."""
    for path in sorted((golden_out(version) / "outputs").glob("*.png")):
        size = image_size(path.read_bytes())
        assert size is not None and max(size) <= 1024, path.name


@pytest.mark.parametrize("version", VERSIONS)
def test_committed_manifest_matches_files(version: str) -> None:
    paths = GoldenPaths(out=golden_out(version))
    items = read_outputs(paths)
    assert {i.file for i in items} == {p.name for p in paths.outputs.glob("*.png")}
