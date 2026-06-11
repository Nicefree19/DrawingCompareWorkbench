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


def test_document_cache_scope_reuses_parsed_doc(tmp_path):
    # Issue-1 lever #2: within a scope the same on-disk DXF parses ONCE and
    # read-only consumers share the document object.
    import ezdxf

    from src.services.comparison.dxf_read import (
        dxf_document_cache_scope,
        read_dxf_document_result,
    )

    p = tmp_path / "a.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.saveas(p)

    with dxf_document_cache_scope() as scope:
        r1 = read_dxf_document_result(p)
        r2 = read_dxf_document_result(p)
        assert r1.doc is r2.doc, "second read must reuse the cached document"
        assert scope["hits"] == 1 and scope["misses"] == 1

        # mutable consumers must get a PRIVATE parse (cloud marker contract)
        r3 = read_dxf_document_result(p, mutable=True)
        assert r3.doc is not r1.doc

    # outside the scope: no caching (default behavior unchanged)
    r4 = read_dxf_document_result(p)
    assert r4.doc is not r1.doc


def test_document_cache_invalidates_when_file_changes(tmp_path):
    import ezdxf

    from src.services.comparison.dxf_read import (
        dxf_document_cache_scope,
        read_dxf_document_result,
    )

    p = tmp_path / "a.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.saveas(p)

    with dxf_document_cache_scope():
        r1 = read_dxf_document_result(p)
        doc2 = ezdxf.new()
        doc2.modelspace().add_line((0, 0), (99, 0))
        doc2.saveas(p)  # mtime/size change -> new key
        import os
        os.utime(p)  # ensure mtime tick even on coarse filesystems
        r2 = read_dxf_document_result(p)
        assert r2.doc is not r1.doc


def test_document_cache_lru_evicts_oldest(tmp_path):
    import ezdxf

    from src.services.comparison.dxf_read import (
        dxf_document_cache_scope,
        read_dxf_document_result,
    )

    paths = []
    for i in range(3):
        p = tmp_path / f"f{i}.dxf"
        d = ezdxf.new()
        d.modelspace().add_line((0, 0), (i + 1, 0))
        d.saveas(p)
        paths.append(p)

    with dxf_document_cache_scope(maxsize=2) as scope:
        first = read_dxf_document_result(paths[0])
        read_dxf_document_result(paths[1])
        read_dxf_document_result(paths[2])  # evicts paths[0]
        assert len(scope["entries"]) == 2
        again = read_dxf_document_result(paths[0])
        assert again.doc is not first.doc  # was evicted -> fresh parse
