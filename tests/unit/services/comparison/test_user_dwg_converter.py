from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.services.comparison import user_dwg_converter as converter_module
from src.services.comparison.user_dwg_converter import (
    UserDwgConverter,
    UserDwgConverterError,
)


def test_user_dwg_converter_runs_template_and_cleans_temp_output(tmp_path: Path) -> None:
    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032" + b"\0" * 32)
    script = (
        "import pathlib, sys; "
        "pathlib.Path(sys.argv[2]).write_text('0\\nEOF\\n', encoding='utf-8')"
    )
    converter = UserDwgConverter(
        sys.executable,
        args_template=("-c", script, "{input}", "{output}"),
    )

    converted = converter.convert(source, timeout=10)
    output_dir = converted.parent

    assert converted.exists()
    assert converted.name == "detail.dxf"

    converter.cleanup_converted_output(converted)

    assert not output_dir.exists()


def test_user_dwg_converter_surfaces_nonzero_exit(tmp_path: Path) -> None:
    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032" + b"\0" * 32)
    converter = UserDwgConverter(
        sys.executable,
        args_template=("-c", "import sys; print('bad conversion'); sys.exit(3)"),
    )

    with pytest.raises(UserDwgConverterError, match="exit code 3"):
        converter.convert(source, timeout=10)


def test_user_dwg_converter_cleans_temp_output_after_failed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032" + b"\0" * 32)
    output_dir = tmp_path / "converter-output"

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix == "dwg_user_out_"
        output_dir.mkdir()
        return str(output_dir)

    monkeypatch.setattr(converter_module.tempfile, "mkdtemp", fake_mkdtemp)
    converter = UserDwgConverter(
        sys.executable,
        args_template=("-c", "import sys; sys.exit(3)"),
    )

    with pytest.raises(UserDwgConverterError, match="exit code 3"):
        converter.convert(source, timeout=10)

    assert not output_dir.exists()
    assert not converter._temp_roots
