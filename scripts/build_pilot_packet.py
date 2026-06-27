"""Assemble a reproducible internal-pilot packet from a built app directory.

Replaces the never-existed ``build_customer_pilot_*`` runbook scripts with a real,
one-command producer. It does NOT build the exe (that is a separate PyInstaller /
build-machine step) — it takes an already-built onedir app directory and assembles
the hand-overable packet around it:

    DrawingCompare_<version>_internal_pilot/
      ├─ DrawingCompare_실행.bat        (double-click launcher)
      ├─ 사용가이드.md / 스팟체크_기록양식.md   (version-controlled, from docs/pilot_packet/)
      ├─ 샘플도면/before.dxf, after.dxf  (a golden pair for a first compare)
      ├─ app/DrawingCompareWorkbench/    (copied build)
      └─ packet_manifest.json

The packet guide describes the auto-emitted ``pilot_spotcheck.md`` + fill-and-return
(the dry-run measurement artifact), so the engineer's feedback path is concrete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs" / "pilot_packet"
GUIDE_FILES = ["사용가이드.md", "스팟체크_기록양식.md"]
DEFAULT_SAMPLE_PAIR = ROOT / "tests/data/comparison/golden/dxf/02_single_modification"

_BAT_TEMPLATE = """@echo off
rem DrawingCompareWorkbench {version} launcher
rem DWG auto-conversion works by default when ODA File Converter is installed.
set DRAWING_COMPARE_DWG_BACKEND=oda_converter
start "" "%~dp0app\\DrawingCompareWorkbench\\{exe_name}"
"""


class PacketBuildError(RuntimeError):
    """Fail-loud error for an unusable build input."""


def _git_sha() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — sha is best-effort metadata
        pass
    return None


def _find_exe(app_dir: Path) -> str:
    exes = sorted(p.name for p in app_dir.glob("*.exe"))
    if not exes:
        raise PacketBuildError(
            f"app-dir에 .exe가 없습니다: {app_dir}\n"
            "  → PyInstaller로 빌드한 DrawingCompareWorkbench 디렉터리를 가리키세요 "
            "(exe 빌드는 이 스크립트 범위 밖)."
        )
    # Prefer the canonical name if present.
    if "DrawingCompareWorkbench.exe" in exes:
        return "DrawingCompareWorkbench.exe"
    return exes[0]


def build_pilot_packet(
    app_dir: Path,
    output: Path,
    *,
    version: str = "internal",
    sample_pair: Path = DEFAULT_SAMPLE_PAIR,
    make_zip: bool = False,
) -> dict[str, Any]:
    """Assemble the packet around a built ``app_dir`` and return a summary dict."""
    app_dir = Path(app_dir)
    if not app_dir.is_dir():
        raise PacketBuildError(f"app-dir가 디렉터리가 아닙니다: {app_dir}")
    exe_name = _find_exe(app_dir)

    packet_name = f"DrawingCompare_{version}_internal_pilot"
    packet_dir = Path(output) / packet_name
    if packet_dir.exists():
        shutil.rmtree(packet_dir)
    packet_dir.mkdir(parents=True)

    # 1) app build (copied verbatim into app/DrawingCompareWorkbench/)
    app_dest = packet_dir / "app" / "DrawingCompareWorkbench"
    shutil.copytree(app_dir, app_dest)

    # 2) launcher
    (packet_dir / "DrawingCompare_실행.bat").write_text(
        _BAT_TEMPLATE.format(version=version, exe_name=exe_name), encoding="utf-8"
    )

    # 3) version-controlled guides
    missing = [name for name in GUIDE_FILES if not (SOURCE_DIR / name).exists()]
    if missing:
        raise PacketBuildError(f"패킷 가이드 소스 누락 ({SOURCE_DIR}): {missing}")
    for name in GUIDE_FILES:
        shutil.copy(SOURCE_DIR / name, packet_dir / name)

    # 4) sample drawing pair (a first compare with no own data)
    sample_pair = Path(sample_pair)
    sample_dest = packet_dir / "샘플도면"
    sample_dest.mkdir()
    sample_files: list[str] = []
    for stem in ("before", "after"):
        src = sample_pair / f"{stem}.dxf"
        if src.exists():
            shutil.copy(src, sample_dest / f"{stem}.dxf")
            sample_files.append(f"샘플도면/{stem}.dxf")

    # 5) manifest (provenance — reproducible, not hand-assembled)
    contents = sorted(
        str(p.relative_to(packet_dir)).replace("\\", "/")
        for p in packet_dir.rglob("*")
        if p.is_file() and "app/DrawingCompareWorkbench" not in str(p).replace("\\", "/")
    )
    manifest = {
        "version": version,
        "git_sha": _git_sha(),
        "source_app_dir": str(app_dir),
        "exe": f"app/DrawingCompareWorkbench/{exe_name}",
        "sample_pair": [str(sample_pair)] if sample_files else [],
        "contents": contents,
    }
    (packet_dir / "packet_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path: Optional[Path] = None
    if make_zip:
        zip_path = Path(shutil.make_archive(str(packet_dir), "zip", root_dir=packet_dir))

    return {
        "packet_dir": packet_dir,
        "zip_path": zip_path,
        "exe_name": exe_name,
        "sample_files": sample_files,
        "manifest": manifest,
    }


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(
        description=(
            "빌드된 exe 디렉터리로부터 사내 파일럿 패킷을 한 명령으로 조립한다. "
            "exe 빌드 자체는 범위 밖 — PyInstaller로 만든 DrawingCompareWorkbench "
            "onedir 디렉터리를 --app-dir로 넘긴다. 가이드/샘플/런처/매니페스트를 조립."
        )
    )
    parser.add_argument(
        "--app-dir",
        type=Path,
        required=True,
        help="빌드된 DrawingCompareWorkbench onedir 디렉터리 (DrawingCompareWorkbench.exe 포함)",
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="패킷 출력 디렉터리")
    parser.add_argument("--version", default="internal", help="패킷 버전 라벨 (예: v0.9.3)")
    parser.add_argument("--zip", action="store_true", help="패킷을 zip으로도 압축")
    args = parser.parse_args(argv)

    try:
        summary = build_pilot_packet(
            args.app_dir, args.output, version=args.version, make_zip=args.zip
        )
    except PacketBuildError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 2

    print(f"패킷: {summary['packet_dir']}")
    if summary["zip_path"]:
        print(f"zip: {summary['zip_path']}")
    print(f"샘플 쌍: {len(summary['sample_files'])} 파일")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
