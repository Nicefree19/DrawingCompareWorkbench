from __future__ import annotations

from src.services.comparison.cad_stability import CadStabilityLimits
from scripts.cad_performance_benchmark import _measure_stability_cases


def test_performance_benchmark_records_malformed_and_unsupported_cases() -> None:
    cases = {case["label"]: case for case in _measure_stability_cases(CadStabilityLimits())}

    assert cases["malformed-dxf"]["status"] == "explicit_error"
    assert cases["malformed-dxf"]["error_class"] in {"DxfParseError", "ValueError"}
    assert cases["unsupported-heavy-dxf"]["status"] == "partial"
    assert cases["unsupported-heavy-dxf"]["unsupported_entity_count"] >= 100
