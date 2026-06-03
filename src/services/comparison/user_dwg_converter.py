"""Customer-provided DWG converter boundary.

This module does not bundle or discover any DWG converter. It only runs a
user-configured executable when the caller explicitly selects the
``user_converter`` backend and provides a path.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence


class UserDwgConverterError(RuntimeError):
    """Raised when a user-provided DWG converter cannot produce a DXF."""


class UserDwgConverter:
    """Run a caller-supplied converter using a small placeholder contract.

    Args templates may include ``{input}``, ``{output_dir}``, ``{output}``, and
    ``{stem}``.  When no args template is supplied, the default command is:
    ``converter input.dwg output_dir``.
    """

    def __init__(self, converter_path: str | Path, *, args_template: Sequence[str] = ()):
        self.converter_path = Path(converter_path).resolve()
        self.args_template = tuple(str(item) for item in (args_template or ()))
        if not self.converter_path.exists() or not self.converter_path.is_file():
            raise UserDwgConverterError(f"User converter executable not found: {self.converter_path}")
        self._temp_roots: set[Path] = set()

    def convert(self, dwg_path: str | Path, *, timeout: int = 120) -> Path:
        source = Path(dwg_path).resolve()
        if not source.exists() or not source.is_file():
            raise UserDwgConverterError(f"DWG input not found: {source}")
        if source.suffix.lower() != ".dwg":
            raise UserDwgConverterError(f"User converter input must be a DWG: {source}")

        output_dir = Path(tempfile.mkdtemp(prefix="dwg_user_out_")).resolve()
        self._temp_roots.add(output_dir)
        expected_output = output_dir / source.with_suffix(".dxf").name
        command = self._command(source, output_dir, expected_output)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout or 120)),
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "no converter output")[:400]
                raise UserDwgConverterError(f"User DWG converter failed with exit code {completed.returncode}: {detail}")

            converted = self._find_converted_dxf(output_dir, expected_output)
            if converted is None:
                raise UserDwgConverterError(f"User DWG converter did not produce a DXF in {output_dir}")
            return converted
        except subprocess.TimeoutExpired as exc:
            self._cleanup_temp_root(output_dir)
            raise TimeoutError(f"User DWG converter timed out after {timeout}s.") from exc
        except Exception:
            self._cleanup_temp_root(output_dir)
            raise

    def cleanup_converted_output(self, converted: str | Path) -> None:
        path = Path(converted)
        output_dir = path.parent.resolve()
        self._cleanup_temp_root(output_dir)

    def _cleanup_temp_root(self, output_dir: Path) -> None:
        if output_dir not in self._temp_roots:
            return
        try:
            output_dir.relative_to(Path(tempfile.gettempdir()).resolve())
        except ValueError:
            return
        shutil.rmtree(output_dir, ignore_errors=True)
        self._temp_roots.discard(output_dir)

    def _command(self, source: Path, output_dir: Path, expected_output: Path) -> list[str]:
        replacements = {
            "input": str(source),
            "output_dir": str(output_dir),
            "output": str(expected_output),
            "stem": source.stem,
        }
        if self.args_template:
            args = [item.format(**replacements) for item in self.args_template]
        else:
            args = [str(source), str(output_dir)]
        return [str(self.converter_path), *args]

    @staticmethod
    def _find_converted_dxf(output_dir: Path, expected_output: Path) -> Path | None:
        if expected_output.exists() and expected_output.is_file():
            return expected_output
        matches = sorted(
            child
            for child in output_dir.iterdir()
            if child.is_file() and child.suffix.lower() == ".dxf"
        )
        return matches[0] if matches else None
