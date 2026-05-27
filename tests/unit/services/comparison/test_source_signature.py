from __future__ import annotations

from pathlib import Path

from src.services.comparison.source_signature import (
    build_source_signature,
    source_cache_filename,
    source_cache_stem,
)
from src.services.comparison.viewer_tile_cache import file_signature as tile_file_signature
from src.services.comparison.zone_render_service import file_signature as zone_file_signature


def test_source_signature_is_stable_for_unchanged_file(tmp_path: Path) -> None:
    source = tmp_path / "A-101.dwg"
    source.write_bytes(b"dwg-content")

    first = build_source_signature(source, importer_version="ACAD2018")
    second = build_source_signature(source, importer_version="ACAD2018")

    assert first["source_hash"] == second["source_hash"]
    assert first["file_size"] == len(b"dwg-content")


def test_source_signature_changes_when_file_identity_changes(tmp_path: Path) -> None:
    source = tmp_path / "A-101.dwg"
    source.write_bytes(b"dwg-content")
    first = build_source_signature(source, importer_version="ACAD2018")

    source.write_bytes(b"dwg-content-updated")
    second = build_source_signature(source, importer_version="ACAD2018")

    assert second["source_hash"] != first["source_hash"]


def test_source_cache_filename_includes_namespace_and_safe_ascii_stem(tmp_path: Path) -> None:
    source = tmp_path / "구조 평면도.dwg"
    source.write_bytes(b"dwg")

    compare_name = source_cache_filename(
        source,
        namespace="compare_dxf",
        extension=".dxf",
        importer_version="ACAD2018",
    )
    preview_name = source_cache_filename(
        source,
        namespace="preview_dxf",
        extension=".dxf",
        importer_version="AC1015",
    )

    assert compare_name.endswith(".dxf")
    assert preview_name.endswith(".dxf")
    assert compare_name != preview_name
    assert source_cache_stem(source).isascii()
    assert compare_name.split(".")[0].isascii()


def test_viewer_and_zone_helpers_share_base_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "same-source.dxf"
    source.write_text("0\nEOF\n", encoding="utf-8")

    assert tile_file_signature(source)["source_hash"] == zone_file_signature(source)["source_hash"]
