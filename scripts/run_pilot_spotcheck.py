"""Lightweight pilot spotcheck CLI — a thin wrapper over FolderComparePipeline.

Given two drawings (or two folders), run the REAL detection pipeline and emit the
operator spotcheck sheet + ground-truth skeleton. The sheet rendering itself lives
in ``src/services/comparison/pilot_spotcheck_sheet.py`` so the GUI compare path and
this CLI share ONE producer (no re-implementation). This module adds the CLI front
door + the DWG on-ramp (single-file and folder).

DXF and other natively-supported formats run directly; DWG is converted via the
existing approved path when a local converter is installed, else it fails loud
with a pre-convert instruction. See ``docs/INTERNAL_PILOT_GUIDE.md``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_backend import DWG_BACKEND_ODA_CONVERTER
from src.services.comparison.dwg_converter import converter_installation_status
from src.services.comparison.dwg_dxf_fallback import auto_convert_unsupported_dwg
from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
)

# Re-export the shared sheet producer so existing callers/tests keep importing
# build_spotcheck_md / build_ground_truth_rows / emit_spotcheck_artifacts from here.
from src.services.comparison.pilot_spotcheck_sheet import (  # noqa: F401
    GROUND_TRUTH_HEADER,
    SKELETON_NOTE,
    build_ground_truth_rows,
    build_spotcheck_md,
    emit_spotcheck_artifacts,
    write_ground_truth_csv,
)


class PilotSpotcheckError(RuntimeError):
    """Fail-loud error for unusable pilot input (e.g. DWG with no converter)."""


def _iter_input_files(path: Path):
    if path.is_dir():
        yield from (child for child in path.iterdir() if child.is_file())
    elif path.is_file():
        yield path


def _inputs_include_dwg(before: Path, after: Path) -> bool:
    return any(
        file.suffix.lower() == ".dwg"
        for root in (before, after)
        for file in _iter_input_files(root)
    )


def _resolve_dwg_backend_mode(before: Path, after: Path) -> str | None:
    """Wire DWG inputs through the existing approved conversion path.

    Returns the ODA-converter backend mode when a DWG input is present and a
    local converter is installed, ``None`` for DXF-only input. Raises
    ``PilotSpotcheckError`` (fail-loud) when a DWG is given but no converter is
    installed — never a silent empty result or single-file fallback.
    """
    if not _inputs_include_dwg(before, after):
        return None
    status = converter_installation_status()
    if not status.get("installed"):
        raise PilotSpotcheckError(
            "DWG 입력이 감지됐으나 로컬 DWG 변환기가 설치되어 있지 않습니다.\n"
            f"  상태: {status.get('message', '(불명)')}\n"
            "  → DWG를 먼저 DXF로 변환한 뒤 입력하거나, 변환기를 설치한 뒤 다시 실행하세요."
        )
    return DWG_BACKEND_ODA_CONVERTER


def _convert_folder_dwgs(root: Path, staging: Path) -> Path:
    """Pre-convert each ``.dwg`` in a folder input to DXF.

    The pipeline auto-converts a DWG only on the explicit single-file path; its
    folder-scan path passes the directory to the converter (→ ``not_dwg``) and
    leaves DWGs unconverted, so an unsupported-version DWG inside a folder fails
    preflight. This pre-converts each DWG file via the existing approved path (no
    conversion logic re-implemented) into a staging folder of DXF (and pass-
    through) files. Non-folder inputs are returned unchanged (the single-file DWG
    path keeps the pipeline's own conversion). Fails loud if a DWG needs
    conversion but none is installed — never a silent drop.
    """
    if not root.is_dir():
        return root
    files = [child for child in root.iterdir() if child.is_file()]
    if not any(f.suffix.lower() == ".dwg" for f in files):
        return root
    if not converter_installation_status().get("installed"):
        raise PilotSpotcheckError(
            "DWG가 포함된 폴더 입력이 감지됐으나 로컬 DWG 변환기가 설치되어 있지 "
            "않습니다.\n  → DWG를 먼저 DXF로 변환한 뒤 입력하거나, 변환기를 설치한 "
            "뒤 다시 실행하세요."
        )
    staging.mkdir(parents=True, exist_ok=True)
    cache = staging / "_oda_cache"
    for child in files:
        if child.suffix.lower() == ".dwg":
            converted, _did, note = auto_convert_unsupported_dwg(child, cache)
            converted = Path(converted)
            if converted.suffix.lower() == ".dxf":
                shutil.copy(converted, staging / f"{child.stem}.dxf")
            elif note == "native_supported":
                shutil.copy(child, staging / child.name)  # native reader handles it
            else:
                raise PilotSpotcheckError(
                    f"DWG 변환 실패: {child.name} (사유 {note}) — 먼저 DXF로 변환한 " "뒤 입력하세요."
                )
        else:
            shutil.copy(child, staging / child.name)
    return staging


def run_pilot_spotcheck(before: Path, after: Path, output: Path) -> dict[str, Any]:
    """Run the real pipeline and emit the spotcheck sheet + ground-truth skeleton."""
    output.mkdir(parents=True, exist_ok=True)
    orig_before, orig_after = before, after
    staging = output / "_dwg_staging"
    before = _convert_folder_dwgs(before, staging / "before")
    after = _convert_folder_dwgs(after, staging / "after")
    dwg_backend_mode = _resolve_dwg_backend_mode(before, after)
    request = FolderCompareRunRequest(before, after, output, dwg_backend_mode=dwg_backend_mode)
    result = FolderComparePipeline(request).run()
    output_dir = Path(result.output_dir)

    if orig_before.is_dir() or orig_after.is_dir():
        pair_name = f"{orig_before.name}/ ↔ {orig_after.name}/ (폴더 배치)"
    else:
        pair_name = f"{orig_before.stem} → {orig_after.stem}"

    # The pipeline already emits a default-named sheet; re-emit with the nicer
    # CLI pair name (idempotent, last write wins).
    return emit_spotcheck_artifacts(output_dir, pair_name)


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp949; print Korean output without crashing."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        description=(
            "실 도면으로 무마찰 파일럿 dry-run을 돕는 경량 러너. 실제 비교"
            "(FolderComparePipeline)를 돌려 검출 변경 표와 ground-truth 스켈레톤을 "
            "산출한다. 단일 쌍(파일) 또는 폴더(다중 쌍, 파일명으로 자동 매칭)를 받는다. "
            "DXF 우선 — DWG는 로컬 변환기 설치 시 자동 변환, 없으면 사전 변환 안내."
        )
    )
    parser.add_argument("before", type=Path, help="이전(개정 전) 도면 파일 또는 폴더")
    parser.add_argument("after", type=Path, help="이후(개정 후) 도면 파일 또는 폴더")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="산출물 디렉터리 (pilot_spotcheck.md / review_ground_truth.csv)",
    )
    args = parser.parse_args(argv)

    for label, path in (("before", args.before), ("after", args.after)):
        if not path.exists():
            parser.error(f"{label} 경로를 찾을 수 없음: {path}")

    try:
        summary = run_pilot_spotcheck(args.before, args.after, args.output)
    except PilotSpotcheckError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 2

    print(f"검출 변경: {summary['detected_count']}건")
    print(f"스팟체크 시트: {summary['spotcheck_md']}")
    print(f"정답 스켈레톤: {summary['ground_truth_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
