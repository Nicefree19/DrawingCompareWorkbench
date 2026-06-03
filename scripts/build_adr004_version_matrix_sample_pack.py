"""Build an ADR-004 all-version DWG/DXF matrix sample pack.

This script is for local/internal evidence generation only. It invokes a local
ODA File Converter executable to down/up-convert one before/after CAD pair into
the DWG generations covered by ADR-004, then creates matching registered DXF
baselines. Product runtime behavior is unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONVERTER_PATHS = (
    Path(r"C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe"),
    Path(r"C:\Program Files\ODA\ODAFileConverter 25.12\ODAFileConverter.exe"),
    Path(r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe"),
    Path(r"C:\Program Files (x86)\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe"),
    Path(r"C:\Program Files (x86)\ODA\ODAFileConverter 25.12\ODAFileConverter.exe"),
    Path(r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe"),
)
TARGET_VERS = {
    "AC1009": "ACAD12",
    "AC1012": "ACAD13",
    "AC1014": "ACAD14",
    "AC1015": "ACAD2000",
    "AC1018": "ACAD2004",
    "AC1021": "ACAD2007",
    "AC1024": "ACAD201",
    "AC1027": "ACAD2013",
    "AC1032": "ACAD2018",
}
SIDES = ("before", "after")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_adr004_version_sample_pack import detect_dwg_header, detect_dxf_acadver  # noqa: E402


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


class VersionMatrixBuildError(RuntimeError):
    """Raised when the local version matrix cannot be built."""


def build_sample_pack(
    before_dwg: Path,
    after_dwg: Path,
    output_root: Path,
    *,
    converter_path: Path | None = None,
    versions: Sequence[str] | None = None,
    timeout_seconds: float = 180.0,
    runner: Runner | None = None,
) -> dict[str, Any]:
    before_dwg = before_dwg.resolve()
    after_dwg = after_dwg.resolve()
    output_root = output_root.resolve()
    converter = _resolve_converter(converter_path)
    selected_versions = [code.upper() for code in (versions or TARGET_VERS.keys())]
    _validate_inputs(before_dwg, after_dwg, selected_versions)

    if output_root.exists():
        raise VersionMatrixBuildError(f"output root already exists: {output_root}")
    output_root.mkdir(parents=True)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(),
        "oda_converter": str(converter),
        "output_root": str(output_root),
        "purpose": "ADR-004 all-version local DWG/DXF compatibility matrix",
        "source_policy": "local converter evidence only; source CAD files are referenced by provenance and not copied into source control",
        "source_before": _source_record(before_dwg),
        "source_after": _source_record(after_dwg),
        "versions": {},
    }

    run = runner or _run_converter
    for code in selected_versions:
        manifest["versions"][code] = _build_version(
            code,
            TARGET_VERS[code],
            before_dwg,
            after_dwg,
            output_root,
            converter,
            timeout_seconds=timeout_seconds,
            runner=run,
        )

    _write_json(output_root / "manifest.json", manifest)
    return manifest


def _build_version(
    code: str,
    output_version: str,
    before_dwg: Path,
    after_dwg: Path,
    output_root: Path,
    converter: Path,
    *,
    timeout_seconds: float,
    runner: Runner,
) -> dict[str, Any]:
    version_root = output_root / code
    scratch_root = output_root / "_scratch" / code
    conversions: dict[str, Any] = {}
    outputs: dict[str, list[dict[str, Any]]] = {}
    sample_dwgs: dict[str, Path] = {}

    for side, source in (("before", before_dwg), ("after", after_dwg)):
        side_input = scratch_root / side
        side_input.mkdir(parents=True)
        input_copy = side_input / f"{side}{source.suffix.lower()}"
        shutil.copy2(source, input_copy)

        dwg_dir = version_root / side
        dxf_dir = version_root / "dxf_registered" / side
        dwg_dir.mkdir(parents=True)
        dxf_dir.mkdir(parents=True)

        dwg_result = _convert_folder(
            converter,
            side_input,
            dwg_dir,
            output_version,
            "DWG",
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        sample_dwg = _single_output(dwg_dir, ".dwg", f"{code}.{side}.DWG")

        dxf_result = _convert_folder(
            converter,
            dwg_dir,
            dxf_dir,
            output_version,
            "DXF",
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        sample_dxf = _single_output(dxf_dir, ".dxf", f"{code}.{side}.DXF")

        sample_dwgs[side] = sample_dwg
        outputs[side] = [_dxf_output(sample_dxf, expected_acadver=code)]
        conversions[side] = {"dwg": dwg_result, "dxf": dxf_result}

    shutil.rmtree(scratch_root, ignore_errors=True)

    return {
        "dwg_code": code,
        "dxf_output_version": output_version,
        "pair_kind": _pair_kind(before_dwg, after_dwg),
        "source_before": str(before_dwg),
        "source_after": str(after_dwg),
        "sample_before_dwg": str(sample_dwgs["before"]),
        "sample_after_dwg": str(sample_dwgs["after"]),
        "dxf_before_dir": str(version_root / "dxf_registered" / "before"),
        "dxf_after_dir": str(version_root / "dxf_registered" / "after"),
        "conversion": conversions,
        "outputs": outputs,
    }


def _convert_folder(
    converter: Path,
    input_dir: Path,
    output_dir: Path,
    output_version: str,
    output_format: str,
    *,
    timeout_seconds: float,
    runner: Runner,
) -> dict[str, Any]:
    cmd = [
        str(converter),
        str(input_dir),
        str(output_dir),
        output_version,
        output_format,
        "0",
        "1",
    ]
    try:
        result = runner(cmd, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise VersionMatrixBuildError(
            f"conversion timeout after {timeout_seconds}s for {output_format} {output_version}: {input_dir}"
        ) from exc
    record = {
        "cmd": cmd,
        "returncode": int(result.returncode),
        "stdout": (result.stdout or "")[-4000:],
        "stderr": (result.stderr or "")[-4000:],
    }
    if result.returncode != 0:
        raise VersionMatrixBuildError(
            f"conversion failed for {output_format} {output_version}: returncode={result.returncode}"
        )
    return record


def _run_converter(cmd: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _single_output(folder: Path, suffix: str, label: str) -> Path:
    matches = sorted(path for path in folder.iterdir() if path.suffix.lower() == suffix.lower())
    if len(matches) != 1:
        raise VersionMatrixBuildError(f"expected exactly one {label} output in {folder}, found {len(matches)}")
    return matches[0]


def _dxf_output(path: Path, *, expected_acadver: str) -> dict[str, Any]:
    detected = detect_dxf_acadver(path)
    if detected != expected_acadver:
        raise VersionMatrixBuildError(
            f"DXF $ACADVER mismatch for {path}: expected {expected_acadver}, detected {detected or '(missing)'}"
        )
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
        "acadver": expected_acadver,
    }


def _source_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "format": path.suffix.lower().lstrip("."),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
        "dwg_header": detect_dwg_header(path),
        "dxf_acadver": detect_dxf_acadver(path) if path.suffix.lower() == ".dxf" else None,
    }


def _validate_inputs(before_dwg: Path, after_dwg: Path, versions: Sequence[str]) -> None:
    for label, path in (("before", before_dwg), ("after", after_dwg)):
        if not path.is_file():
            raise VersionMatrixBuildError(f"{label} CAD source not found: {path}")
        if path.suffix.lower() not in {".dwg", ".dxf"}:
            raise VersionMatrixBuildError(f"{label} input must be a DWG or DXF: {path}")
    unknown = sorted(set(versions) - set(TARGET_VERS))
    if unknown:
        raise VersionMatrixBuildError(f"unsupported target version(s): {', '.join(unknown)}")


def _resolve_converter(converter_path: Path | None) -> Path:
    if converter_path:
        converter = converter_path.resolve()
        if converter.is_file():
            return converter
        raise VersionMatrixBuildError(f"converter not found: {converter}")
    for path in DEFAULT_CONVERTER_PATHS:
        if path.is_file():
            return path
    found = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if found:
        return Path(found).resolve()
    raise VersionMatrixBuildError("ODA File Converter executable not found")


def _pair_kind(before_source: Path, after_source: Path) -> str:
    suffixes = {before_source.suffix.lower(), after_source.suffix.lower()}
    if suffixes == {".dwg"}:
        return "version_matrix_real_dwg_source_pair"
    return "version_matrix_fixture_dxf_source_pair"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before_dwg", type=Path)
    parser.add_argument("after_dwg", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--converter", type=Path)
    parser.add_argument("--version", action="append", choices=sorted(TARGET_VERS))
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = build_sample_pack(
            args.before_dwg,
            args.after_dwg,
            args.output_root,
            converter_path=args.converter,
            versions=args.version,
            timeout_seconds=args.timeout_seconds,
        )
    except VersionMatrixBuildError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    versions = ", ".join(manifest["versions"])
    print(f"version matrix sample pack: versions=[{versions}] manifest={Path(args.output_root).resolve() / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
