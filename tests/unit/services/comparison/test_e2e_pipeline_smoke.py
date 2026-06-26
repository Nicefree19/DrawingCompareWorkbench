"""End-to-end stability smoke — the REAL folder-compare pipeline on a golden pair.

Every other ``*_pipeline`` test stubs the heavy stages (fake artifacts/preview/
viewer/compare), so none proves the actual producer chain runs. This test runs
the real `FolderComparePipeline` — import → compare → change zones → artifacts →
preview/viewer manifests + cloud-marking — on a real golden DXF pair, asserting
it completes without crash or stall and emits a structurally valid artifact set.

This is the "does the program actually run end-to-end" guard the suite lacked,
and a regression net for the documented compare-hang / preview-failure incidents
(gui_compare_hang_incident, scene_pack_prewarm). It is headless (no Qt event
loop) so it avoids the PySide6 native-AV flakiness of GUI-driven tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
)

_GOLDEN = Path(__file__).resolve().parents[4] / "tests/data/comparison/golden/dxf"
_PAIR = _GOLDEN / "02_single_modification"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not (_PAIR / "before.dxf").exists() or not (_PAIR / "after.dxf").exists(),
    reason="golden pair 02_single_modification not present",
)
def test_real_pipeline_completes_end_to_end_on_golden_pair(tmp_path: Path) -> None:
    out = tmp_path / "run"
    request = FolderCompareRunRequest(_PAIR / "before.dxf", _PAIR / "after.dxf", out)

    # Real producers, NO stubs. If this hangs, CI/pytest-timeout catches it; if it
    # crashes, run() raises. Either way the program's core path is not stable.
    result = FolderComparePipeline(request).run()

    od = Path(result.output_dir)

    # 1) The pipeline's own success marker was emitted.
    assert (od / "_SUCCESS").exists(), "pipeline did not emit _SUCCESS marker"

    # 2) The stage-hang watchdog did NOT fire (no stall dump in the run dir).
    hang_dumps = list(od.glob("hang_stacks_*.log"))
    assert not hang_dumps, f"stage hang watchdog fired: {hang_dumps}"

    # 3) The producer chain emitted structurally valid artifacts (each parses).
    required = (
        "artifacts/artifact_manifest.json",
        "artifacts/change_zones.json",
        "artifacts/review_dashboard.json",
        "preview/preview_manifest.json",
    )
    for rel in required:
        path = od / rel
        assert path.exists(), f"missing producer artifact: {rel}"
        _load_json(path)  # raises if not valid JSON

    # 4) Compare actually processed the change — the cloud-marking producer wrote
    #    a marked DXF (proves it is not a vacuous 'success' on an empty diff).
    marked = list((od / "artifacts" / "cloud_marked").glob("*.dxf"))
    assert marked, "no cloud-marked DXF emitted — change-marking producer did not run"

    # 5) change_zones.json carries change content (shape-tolerant).
    zones = _load_json(od / "artifacts" / "change_zones.json")
    if isinstance(zones, dict):
        zone_list = zones.get("zones") or zones.get("change_zones") or []
    else:
        zone_list = zones
    assert zone_list, "change_zones.json is empty — compare detected nothing"
