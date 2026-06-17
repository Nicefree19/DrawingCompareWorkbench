"""Characterization tests for the pure render-decision logic (MONO-4 #7 slice A).

SAFETY NET before extracting the render-decision helpers into
``src/gui/workbench_render_decisions.py``: pins the behavior of the V2 methods
``_is_usable_zone_render_source_v2`` (source-path validity) and the active
zone-render request-id matchers (``_active_zone_render_request_id_v2`` /
``_is_current_zone_render_request_v2``). These are the only cleanly-pure pieces
of the render-callback cluster; the worker-result + viewport bodies stay on V2.
Exercises a real ``DrawingCompareWorkbenchV2`` (no rebinding).
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


@pytest.fixture
def workbench(qapp):
    wb = DrawingCompareWorkbenchV2()
    try:
        yield wb
    finally:
        wb.deleteLater()


# --- _is_usable_zone_render_source_v2 (static, pure path validity) ---

def test_usable_source_accepts_existing_supported_file(workbench, tmp_path):
    dxf = tmp_path / "drawing.dxf"
    dxf.write_text("0\nSECTION\n", encoding="utf-8")
    assert workbench._is_usable_zone_render_source_v2(str(dxf)) is True


def test_usable_source_rejects_empty_redacted_and_unsupported(workbench, tmp_path):
    assert workbench._is_usable_zone_render_source_v2("") is False
    assert workbench._is_usable_zone_render_source_v2(None) is False
    assert workbench._is_usable_zone_render_source_v2("<redacted>/x.dxf") is False
    # exists but unsupported extension
    txt = tmp_path / "notes.txt"
    txt.write_text("x", encoding="utf-8")
    assert workbench._is_usable_zone_render_source_v2(str(txt)) is False
    # supported extension but the file does not exist
    assert workbench._is_usable_zone_render_source_v2(str(tmp_path / "absent.dwg")) is False


# --- request-id matching (active render request tuple) ---

def test_active_request_id_returns_id_only_for_matching_pair_zone(workbench):
    workbench._active_zone_render_request_v2 = ("pair_a", "C-001", "pair_a:C-001:3")
    assert workbench._active_zone_render_request_id_v2("pair_a", "C-001") == "pair_a:C-001:3"
    assert workbench._active_zone_render_request_id_v2("pair_a", "C-002") == ""
    assert workbench._active_zone_render_request_id_v2("other", "C-001") == ""
    workbench._active_zone_render_request_v2 = None
    assert workbench._active_zone_render_request_id_v2("pair_a", "C-001") == ""


def test_is_current_request_matches_active_selection_and_id(workbench):
    workbench._active_row = {"pair_id": "pair_a"}
    workbench._active_zone_id = "C-001"
    workbench._active_zone_render_request_v2 = ("pair_a", "C-001", "rid-9")

    # matching pair+zone, no request_id required -> True
    assert workbench._is_current_zone_render_request_v2("pair_a", "C-001") is True
    # wrong zone -> False
    assert workbench._is_current_zone_render_request_v2("pair_a", "C-999") is False
    # matching pair+zone AND matching request_id -> True
    assert workbench._is_current_zone_render_request_v2("pair_a", "C-001", "rid-9") is True
    # matching pair+zone but stale request_id -> False
    assert workbench._is_current_zone_render_request_v2("pair_a", "C-001", "rid-8") is False
