from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.build_adr004_version_matrix_sample_pack import build_sample_pack
from scripts.validate_adr004_version_sample_pack import build_report


def test_build_version_matrix_pack_writes_validation_compatible_manifest(tmp_path: Path) -> None:
    before = _dwg(tmp_path / "source" / "before.dwg", "AC1032")
    after = _dwg(tmp_path / "source" / "after.dwg", "AC1032")
    converter = _exe(tmp_path / "ODAFileConverter.exe")
    output_root = tmp_path / "matrix-pack"

    manifest = build_sample_pack(
        before,
        after,
        output_root,
        converter_path=converter,
        versions=["AC1009", "AC1015"],
        runner=_fake_oda_runner,
    )

    assert list(manifest["versions"]) == ["AC1009", "AC1015"]
    assert manifest["versions"]["AC1009"]["dxf_output_version"] == "ACAD12"
    assert Path(manifest["versions"]["AC1009"]["sample_before_dwg"]).read_bytes().startswith(b"AC1009")
    assert Path(manifest["versions"]["AC1015"]["sample_after_dwg"]).read_bytes().startswith(b"AC1015")

    report = build_report(output_root, run_import=False, run_compare=False, root=Path.cwd())

    assert report["status"] == "ok"
    assert report["summary"]["version_count"] == 2
    assert report["summary"]["header_mismatch_count"] == 0


def test_build_version_matrix_pack_accepts_dxf_fixture_sources(tmp_path: Path) -> None:
    before = _dxf(tmp_path / "source" / "simple_base.dxf", "AC1024")
    after = _dxf(tmp_path / "source" / "simple_modified.dxf", "AC1024")
    converter = _exe(tmp_path / "ODAFileConverter.exe")
    output_root = tmp_path / "fixture-pack"

    manifest = build_sample_pack(
        before,
        after,
        output_root,
        converter_path=converter,
        versions=["AC1014"],
        runner=_fake_oda_runner,
    )

    item = manifest["versions"]["AC1014"]
    assert item["pair_kind"] == "version_matrix_fixture_dxf_source_pair"
    assert manifest["source_before"]["format"] == "dxf"
    assert Path(item["sample_before_dwg"]).read_bytes().startswith(b"AC1014")


def _fake_oda_runner(cmd: list[str] | tuple[str, ...], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    del timeout_seconds
    input_dir = Path(cmd[1])
    output_dir = Path(cmd[2])
    output_version = cmd[3]
    output_format = cmd[4]
    code = {
        "ACAD12": "AC1009",
        "ACAD14": "AC1014",
        "ACAD2000": "AC1015",
    }[output_version]
    source = next(path for path in input_dir.iterdir() if path.suffix.lower() in {".dwg", ".dxf"})
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_format == "DWG":
        _dwg(output_dir / source.with_suffix(".dwg").name, code)
    elif output_format == "DXF":
        _dxf(output_dir / source.with_suffix(".dxf").name, code)
    else:
        raise AssertionError(f"unexpected format: {output_format}")
    return subprocess.CompletedProcess(list(cmd), 0, "", "")


def _dwg(path: Path, code: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("ascii") + b"\0sample")
    return path


def _dxf(path: Path, acadver: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", acadver, "0", "ENDSEC", "0", "EOF"])
        + "\n",
        encoding="utf-8",
    )
    return path


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fake", encoding="utf-8")
    return path
