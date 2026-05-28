from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.region_profile import RegionProfile, default_profile_path
from src.services.comparison.sheet_region_detector import detect_sheet_regions


ezdxf = pytest.importorskip("ezdxf")


def _write_custom_number_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (3000.0, 0.0), (3000.0, 1800.0), (0.0, 1800.0)],
        close=True,
        dxfattribs={"layer": "CUSTOM-PERIMETER"},
    )
    msp.add_line((300.0, 350.0), (2700.0, 350.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((300.0, 700.0), (2700.0, 700.0), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("ZONE/42", dxfattribs={"height": 120, "layer": "TITLE"})
    text.set_placement((120.0, 1500.0))
    doc.saveas(str(path))
    return path


def _write_custom_table_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (4000.0, 0.0), (4000.0, 2200.0), (0.0, 2200.0)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    for y in (400.0, 800.0, 1200.0, 1600.0):
        msp.add_line((350.0, y), (3600.0, y), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("D-101", dxfattribs={"height": 120, "layer": "TITLE"})
    text.set_placement((120.0, 1900.0))

    table_x = 4600.0
    table_y = 0.0
    msp.add_lwpolyline(
        [
            (table_x, table_y),
            (table_x + 1200.0, table_y),
            (table_x + 1200.0, table_y + 800.0),
            (table_x, table_y + 800.0),
        ],
        close=True,
        dxfattribs={"layer": "CUSTOMINFO"},
    )
    for row, label in enumerate(("QTYLIST", "COUNT", "MARK", "REF")):
        table_text = msp.add_text(label, dxfattribs={"height": 60, "layer": "CUSTOMINFO"})
        table_text.set_placement((table_x + 40.0, table_y + 80.0 + row * 180.0))
    doc.saveas(str(path))
    return path


def test_default_region_profile_loads_configured_defaults() -> None:
    profile = RegionProfile.default()

    assert default_profile_path().exists()
    assert profile.name == "default"
    assert "*FRAME*" in profile.frame_layer_patterns
    assert "SCHEDULE" in profile.table_reject_keywords
    assert profile.title_area_policy == "bottom_or_right_title_band"
    assert profile.drawing_number_patterns


def test_region_profile_from_yaml_accepts_project_specific_patterns(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        """
        name: project-a
        frame_layer_patterns:
          - "CUSTOM-PERIMETER"
        table_reject_keywords:
          - "QTYLIST"
        drawing_number_patterns:
          - "\\\\bZONE/\\\\d{2}\\\\b"
        title_area_policy: right_title_band
        """,
        encoding="utf-8",
    )

    profile = RegionProfile.from_yaml(profile_path)

    assert profile.name == "project-a"
    assert profile.frame_layer_patterns == ("CUSTOM-PERIMETER",)
    assert profile.table_reject_keywords == ("QTYLIST",)
    assert profile.drawing_number_patterns == (r"\bZONE/\d{2}\b",)
    assert profile.title_area_policy == "right_title_band"


def test_custom_profile_patterns_affect_frame_scoring_and_drawing_number(
    tmp_path: Path,
) -> None:
    source = _write_custom_number_dxf(tmp_path / "custom_number.dxf")
    custom_profile = RegionProfile.from_dict(
        {
            "frame_layer_patterns": ["CUSTOM-PERIMETER"],
            "drawing_number_patterns": [r"\bZONE/\d{2}\b"],
        }
    )

    default_result = detect_sheet_regions(source, side="before")
    custom_result = detect_sheet_regions(
        source,
        side="before",
        region_profile=custom_profile,
    )

    assert len(default_result.regions) == 1
    assert len(custom_result.regions) == 1
    assert default_result.regions[0].drawing_number == ""
    assert custom_result.regions[0].drawing_number == "ZONE/42"
    assert custom_result.regions[0].frame_score > default_result.regions[0].frame_score
    assert "frame layer profile match" in custom_result.regions[0].confidence_reasons


def test_custom_profile_table_keywords_reject_false_detail_frames(tmp_path: Path) -> None:
    source = _write_custom_table_dxf(tmp_path / "custom_table.dxf")
    custom_profile = RegionProfile.from_dict({"table_reject_keywords": ["QTYLIST"]})

    default_result = detect_sheet_regions(source, side="before")
    custom_result = detect_sheet_regions(
        source,
        side="before",
        region_profile=custom_profile,
    )

    assert len(default_result.regions) == 2
    assert len(custom_result.regions) == 1
    assert custom_result.regions[0].drawing_number == "D-101"
