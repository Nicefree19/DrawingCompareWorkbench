"""Run native DWG reader diagnostics for local samples."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("tests/data/comparison/real_world/local-dwg-samples.manifest.json")
DEFAULT_JSON_REPORT = Path("build/reports/dwg-native-diagnostics.json")
DEFAULT_MD_REPORT = Path("build/reports/dwg-native-diagnostics.md")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_diagnostics import diagnose_dwg_file  # noqa: E402


def build_report(manifest_path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> dict[str, Any]:
    manifest_path = _resolve(root, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_root = Path(str(manifest.get("source_root") or ""))
    diagnostics = []
    for sample in manifest.get("samples") or []:
        path = source_root / str(sample.get("path") or "")
        item = diagnose_dwg_file(path).to_dict()
        item["sample_id"] = sample.get("id")
        item["expected_version"] = sample.get("expected_version")
        item["expected_size_bytes"] = sample.get("expected_size_bytes")
        diagnostics.append(item)

    status_counts: dict[str, int] = {}
    blocking_counts: dict[str, int] = {}
    for item in diagnostics:
        status_counts[str(item.get("status"))] = status_counts.get(str(item.get("status")), 0) + 1
        blocking = str(item.get("blocking_stage") or "none")
        blocking_counts[blocking] = blocking_counts.get(blocking, 0) + 1

    return {
        "schema_version": "dwg-native-diagnostics/v1",
        "generated_at": datetime.now().isoformat(),
        "manifest_path": str(manifest_path),
        "source_root": str(source_root),
        "source_root_available": source_root.exists(),
        "status": "ok" if source_root.exists() else "skipped",
        "summary": {
            "sample_count": len(manifest.get("samples") or []),
            "status_counts": dict(sorted(status_counts.items())),
            "blocking_stage_counts": dict(sorted(blocking_counts.items())),
        },
        "diagnostics": diagnostics,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DWG Native Diagnostics",
        "",
        f"Status: **{report['status']}**",
        "",
        "## Scope",
        "",
        f"- Source root: `{report['source_root']}`",
        f"- Source available: `{report['source_root_available']}`",
        f"- Samples: `{report['summary']['sample_count']}`",
        "",
        "## Samples",
        "",
        "| id | version | status | blocking stage | detail | message |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.get("diagnostics") or []:
        version = (item.get("version") or {}).get("code") or ""
        stage_detail = _blocking_stage_detail(item)
        lines.append(
            "| {id} | {version} | {status} | {blocking} | {detail} | {message} |".format(
                id=item.get("sample_id"),
                version=version,
                status=item.get("status"),
                blocking=item.get("blocking_stage") or "",
                detail=stage_detail,
                message=_md_cell(str(item.get("message") or "")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _blocking_stage_detail(item: dict[str, Any]) -> str:
    blocking = item.get("blocking_stage")
    for stage in item.get("stages") or []:
        if stage.get("name") == blocking:
            return _md_cell(str((stage.get("metrics") or {}).get("blocking_stage_detail") or ""))
    return ""


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--md-report", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args(argv)

    report = build_report(args.manifest)
    _write_json(_resolve(ROOT, args.json_report), report)
    _write_text(_resolve(ROOT, args.md_report), render_markdown(report))
    print(
        "dwg native diagnostics: "
        f"status={report['status']} "
        f"samples={report['summary']['sample_count']} "
        f"blocking={report['summary']['blocking_stage_counts']}"
    )
    return 0 if report["status"] in {"ok", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
