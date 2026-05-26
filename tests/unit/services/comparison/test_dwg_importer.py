from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.dwg_importer import (
    DwgFailureCode,
    DwgImportError,
    DwgImporter,
    DwgImporterAdapter,
    DwgJsonFixtureAdapter,
    DwgVersionDetector,
)


def _schema() -> dict:
    return json.loads(Path("docs/canonical-drawing.schema.json").read_text(encoding="utf-8"))


def _validate_schema(doc: dict) -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:10])


def _write_fixture(path: Path, version: str = "AC1015", payload: dict | None = None) -> Path:
    default_payload = {
        "header": {"$INSUNITS": 4},
        "layers": [
            {"name": "BEAM", "color": 3, "linetype": "Continuous", "lineweight": 25},
            {"name": "ANNO", "color": 7, "linetype": "Continuous"},
        ],
        "blocks": [
            {
                "name": "B1",
                "origin": {"x": 0, "y": 0, "z": 0},
                "entities": [
                    {
                        "type": "LINE",
                        "handle": "B10",
                        "layer": "BEAM",
                        "geometry": {
                            "start": {"x": 0, "y": 0, "z": 0},
                            "end": {"x": 10, "y": 0, "z": 0},
                        },
                    }
                ],
            }
        ],
        "model_space": [
            {
                "type": "LINE",
                "handle": "10",
                "layer": "BEAM",
                "style": {"color": 256, "linetype": "BYLAYER", "lineweight": -1},
                "geometry": {
                    "start": {"x": 0, "y": 0, "z": 0},
                    "end": {"x": 100, "y": 0, "z": 0},
                },
            },
            {
                "type": "CIRCLE",
                "handle": "11",
                "layer": "BEAM",
                "geometry": {"center": {"x": 50, "y": 50, "z": 0}, "radius": 10},
            },
            {
                "type": "TEXT",
                "handle": "12",
                "layer": "ANNO",
                "geometry": {
                    "insert": {"x": 0, "y": 20, "z": 0},
                    "height": 2.5,
                    "text": " H-400 ",
                },
            },
            {
                "type": "INSERT",
                "handle": "13",
                "layer": "BEAM",
                "geometry": {
                    "block_name": "B1",
                    "insert": {"x": 10, "y": 10, "z": 0},
                    "scale": {"x": 1, "y": 1, "z": 1},
                    "rotation_deg": 0,
                    "attributes": [{"tag": "MARK", "text": "B1"}],
                },
            },
        ],
        "metadata": {"fixture": True},
    }
    data = version.encode("ascii") + DwgJsonFixtureAdapter.MARKER + json.dumps(
        payload or default_payload,
        ensure_ascii=False,
    ).encode("utf-8")
    path.write_bytes(data)
    return path


def test_version_detector_identifies_supported_dwg_versions() -> None:
    expected = {
        "AC1015": "AutoCAD 2000/2000i/2002",
    }

    for code, release in expected.items():
        info = DwgVersionDetector.detect_bytes(code.encode("ascii"))
        assert info.code == code
        assert info.release == release
        assert info.supported is True


def test_version_detector_identifies_known_planned_versions_as_unsupported() -> None:
    expected = {
        "AC1018": "AutoCAD 2004/2005/2006",
        "AC1021": "AutoCAD 2007/2008/2009",
        "AC1024": "AutoCAD 2010/2011/2012",
        "AC1027": "AutoCAD 2013/2014/2015/2016/2017",
        "AC1032": "AutoCAD 2018+",
    }

    for code, release in expected.items():
        info = DwgVersionDetector.detect_bytes(code.encode("ascii"))
        assert info.code == code
        assert info.release == release
        assert info.supported is False


def test_version_detector_rejects_corrupted_and_marks_unsupported() -> None:
    unsupported = DwgVersionDetector.detect_bytes(b"AC1014")
    assert unsupported.supported is False
    assert unsupported.code == "AC1014"

    try:
        DwgVersionDetector.detect_bytes(b"DWG")
    except DwgImportError as exc:
        assert exc.code == DwgFailureCode.CORRUPTED
    else:
        raise AssertionError("short DWG header must fail")

    try:
        DwgVersionDetector.detect_bytes(b"XXXXXX")
    except DwgImportError as exc:
        assert exc.code == DwgFailureCode.CORRUPTED
    else:
        raise AssertionError("non-AC header must fail")


def test_fixture_adapter_imports_dwg_sample_to_canonical_drawing(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path / "sample.dwg", version="AC1015")

    doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(path)

    _validate_schema(doc)
    assert doc["drawing"]["source"]["format"] == "dwg"
    assert doc["drawing"]["source"]["acad_version"] == "AC1015"
    assert doc["import_report"]["status"] == "ok"
    assert doc["import_report"]["error_code"] is None
    assert doc["import_report"]["adapter"]["license_id"] == "MIT"
    assert {layer["name"] for layer in doc["layers"]} >= {"0", "BEAM", "ANNO"}
    assert len(doc["blocks"]) == 1
    assert {entity["type"] for entity in doc["entities"]} >= {
        "line",
        "circle",
        "text",
        "block_reference",
    }
    text = next(entity for entity in doc["entities"] if entity["type"] == "text")
    assert text["geometry"]["canonical_text"] == "H-400"
    insert = next(entity for entity in doc["entities"] if entity["type"] == "block_reference")
    assert insert["geometry"]["block_name"] == "B1"


def test_unsupported_entity_is_partial_with_warning(tmp_path: Path) -> None:
    path = _write_fixture(
        tmp_path / "unsupported.dwg",
        payload={
            "model_space": [
                {"type": "3DSOLID", "handle": "3D1", "layer": "MODEL", "geometry": {}}
            ]
        },
    )

    doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(path)

    _validate_schema(doc)
    assert doc["import_report"]["status"] == "partial"
    assert doc["import_report"]["warnings"][0]["code"] == DwgFailureCode.UNSUPPORTED_ENTITY
    assert doc["import_report"]["unsupported_entities"][0]["raw_type"] == "3DSOLID"


def test_importer_rejects_known_planned_dwg_version_until_reader_expands(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path / "planned.dwg", version="AC1032")

    doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(path)

    _validate_schema(doc)
    assert doc["drawing"]["source"]["acad_version"] == "AC1032"
    assert doc["import_report"]["status"] == "failed"
    assert doc["import_report"]["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION
    assert doc["import_report"]["dwg_version"]["supported"] is False


def test_injected_adapter_can_claim_a_planned_version_without_changing_default_policy(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path / "planned.dwg", version="AC1032")

    class PlannedVersionAdapter(DwgJsonFixtureAdapter):
        name = "planned-version-fixture"

        def supports_version(self, version) -> bool:  # type: ignore[no-untyped-def]
            return version.code == "AC1032"

    doc = DwgImporter(adapter=PlannedVersionAdapter()).import_file(path)

    _validate_schema(doc)
    assert doc["drawing"]["source"]["acad_version"] == "AC1032"
    assert doc["drawing"]["importer"]["backend"] == "planned-version-fixture"
    assert doc["import_report"]["status"] == "ok"
    assert doc["import_report"]["error_code"] is None


def test_importer_returns_failed_document_for_unsupported_corrupted_and_encrypted(tmp_path: Path) -> None:
    unsupported = _write_fixture(tmp_path / "old.dwg", version="AC1014")
    corrupted = tmp_path / "corrupted.dwg"
    corrupted.write_bytes(b"DWG")
    encrypted = _write_fixture(tmp_path / "encrypted.dwg", payload={"encrypted": True})

    unsupported_doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(unsupported)
    corrupted_doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(corrupted)
    encrypted_doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(encrypted)

    for doc in (unsupported_doc, corrupted_doc, encrypted_doc):
        _validate_schema(doc)
        assert doc["import_report"]["status"] == "failed"
        assert doc["entities"] == []

    assert unsupported_doc["import_report"]["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION
    assert corrupted_doc["import_report"]["error_code"] == DwgFailureCode.CORRUPTED
    assert encrypted_doc["import_report"]["error_code"] == DwgFailureCode.ENCRYPTED


def test_adapter_availability_and_license_failures_are_explicit(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path / "sample.dwg")

    class UnavailableAdapter(DwgImporterAdapter):
        name = "unavailable"
        license_id = "MIT"

        def is_available(self) -> bool:
            return False

    class GplAdapter(DwgJsonFixtureAdapter):
        name = "gpl-adapter"
        license_id = "GPL-3.0-only"

    unavailable = DwgImporter(adapter=UnavailableAdapter()).import_file(path)
    forbidden = DwgImporter(adapter=GplAdapter()).import_file(path)

    _validate_schema(unavailable)
    _validate_schema(forbidden)
    assert unavailable["import_report"]["error_code"] == DwgFailureCode.ADAPTER_UNAVAILABLE
    assert forbidden["import_report"]["error_code"] == DwgFailureCode.FORBIDDEN_LICENSE
