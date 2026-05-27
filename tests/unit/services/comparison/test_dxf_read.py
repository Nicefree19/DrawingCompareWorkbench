from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.dxf_read import read_dxf_document_result

ezdxf = pytest.importorskip("ezdxf")


def _write_missing_lwpolyline_subclass(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "HEADER",
                "9",
                "$ACADVER",
                "1",
                "AC1024",
                "0",
                "ENDSEC",
                "0",
                "SECTION",
                "2",
                "ENTITIES",
                "0",
                "LWPOLYLINE",
                "5",
                "13",
                "8",
                "BEAM",
                "70",
                "1",
                "10",
                "0",
                "20",
                "80",
                "10",
                "30",
                "20",
                "80",
                "10",
                "30",
                "20",
                "110",
                "0",
                "ENDSEC",
                "0",
                "EOF",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_read_dxf_document_sanitizes_missing_lwpolyline_subclass(tmp_path: Path) -> None:
    malformed = _write_missing_lwpolyline_subclass(tmp_path / "missing_subclass.dxf")

    with pytest.raises(Exception, match="AcDbPolyline"):
        ezdxf.readfile(str(malformed))

    result = read_dxf_document_result(malformed, ezdxf_module=ezdxf)

    assert result.diagnostics.sanitized is True
    assert result.diagnostics.repair_count == 1
    entities = list(result.doc.modelspace())
    assert [entity.dxftype() for entity in entities] == ["LWPOLYLINE"]
    assert list(entities[0].get_points("xy")) == [(0.0, 80.0), (30.0, 80.0), (30.0, 110.0)]


def test_existing_cad_sample_loads_through_sanitized_reader() -> None:
    sample = Path("tests/data/comparison/cad_samples/dxf/simple_base.dxf")

    result = read_dxf_document_result(sample, ezdxf_module=ezdxf)

    assert result.diagnostics.sanitized is True
    assert any(entity.dxftype() == "LWPOLYLINE" for entity in result.doc.modelspace())


def test_entity_extractor_uses_sanitized_reader(tmp_path: Path) -> None:
    malformed = _write_missing_lwpolyline_subclass(tmp_path / "extractable.dxf")

    entities = DxfEntityExtractor().extract_from_file(malformed)

    assert len(entities["LWPOLYLINE"]) == 1
