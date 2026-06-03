"""Generate release-readiness performance evidence from DXF stress pairs.

The report is consumed by ``build_dwg_release_baseline_metrics.py`` through its
``--large-dwg-probe`` option.  It records only measurements obtained from actual
generated files and compare runs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_differ import DwgDiffer  # noqa: E402


SCHEMA_VERSION = "dwg-release-performance-probe/v1"
DEFAULT_OUT = Path("build/reports/dwg-release-performance-probe.json")
DEFAULT_WORK_DIR = Path("build/reports/dwg-release-performance-probe")


@dataclass(frozen=True)
class CompareExecution:
    exit_code: int | None
    elapsed_s: float
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""


CompareRunner = Callable[[Sequence[str], float], CompareExecution]
DirectProbeRunner = Callable[[Path, Path, int, int, float], dict[str, Any]]


def build_probe(
    *,
    out: Path = DEFAULT_OUT,
    work_dir: Path = DEFAULT_WORK_DIR,
    python_executable: str = sys.executable,
    medium_line_count: int = 2_000,
    large_line_count: int = 8_000,
    large_pair_count: int = 3,
    pair_timeout_seconds: float = 120.0,
    max_entities: int = 200_000,
    max_dxf_tokens: int = 30_000_000,
    compare_runner: CompareRunner | None = None,
    direct_probe_runner: DirectProbeRunner | None = None,
) -> dict[str, Any]:
    out = _resolve(out)
    work_dir = _resolve(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    runner = compare_runner or _run_compare_command

    medium_before, medium_after = _write_pair(work_dir / "medium", medium_line_count, shift_every=250)
    medium = _run_pair(
        "medium",
        medium_before,
        medium_after,
        work_dir / "medium-result.json",
        python_executable=python_executable,
        pair_timeout_seconds=pair_timeout_seconds,
        max_entities=max_entities,
        max_dxf_tokens=max_dxf_tokens,
        compare_runner=runner,
    )

    large_pairs: list[dict[str, Any]] = []
    for index in range(large_pair_count):
        before, after = _write_pair(work_dir / f"large-{index + 1:02d}", large_line_count, shift_every=500 + index)
        large_pairs.append(
            _run_pair(
                f"large-{index + 1:02d}",
                before,
                after,
                work_dir / f"large-{index + 1:02d}-result.json",
                python_executable=python_executable,
                pair_timeout_seconds=pair_timeout_seconds,
                max_entities=max_entities,
                max_dxf_tokens=max_dxf_tokens,
                compare_runner=runner,
            )
        )

    direct_runner = direct_probe_runner or _run_direct_probe
    direct = direct_runner(medium_before, medium_after, max_entities, max_dxf_tokens, pair_timeout_seconds)

    successful_large = [item for item in large_pairs if item.get("status") == "passed"]
    large_seconds = max([_as_float(item.get("elapsed_s")) or 0.0 for item in successful_large] or [0.0])
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "source_policy": "synthetic DXF stress pairs generated locally and compared through cad_compare/DwgDiffer",
        "work_dir": str(work_dir),
        "medium": medium,
        "large_pairs": large_pairs,
        "large_cad_dxf_pairs": len(successful_large),
        "medium_drawing_seconds": round(_as_float(medium.get("elapsed_s")) or 0.0, 6),
        "large_drawing_seconds": round(large_seconds, 6),
        "elapsed_s": round(large_seconds, 6),
        "progress_max_gap_s": direct.get("progress_max_gap_s"),
        "cancel_probe": direct.get("cancel_probe") or {},
        "direct_probe": direct,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return report


def _run_pair(
    label: str,
    before: Path,
    after: Path,
    result_json: Path,
    *,
    python_executable: str,
    pair_timeout_seconds: float,
    max_entities: int,
    max_dxf_tokens: int,
    compare_runner: CompareRunner,
) -> dict[str, Any]:
    command = [
        python_executable,
        "-m",
        "src.cli.cad_compare",
        "file",
        str(before),
        str(after),
        "--output",
        str(result_json),
        "--max-entities",
        str(max_entities),
        "--max-dxf-tokens",
        str(max_dxf_tokens),
    ]
    execution = compare_runner(command, pair_timeout_seconds)
    payload = _load_json(result_json)
    summary = {}
    if isinstance(payload, dict):
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "label": label,
        "status": "passed" if execution.exit_code == 0 and not execution.timed_out and result_json.exists() else "failed",
        "before_path": str(before),
        "after_path": str(after),
        "result_json": str(result_json),
        "exit_code": execution.exit_code,
        "timed_out": execution.timed_out,
        "elapsed_s": round(float(execution.elapsed_s), 6),
        "summary": summary,
        "stdout_tail": execution.stdout_tail,
        "stderr_tail": execution.stderr_tail,
    }


def _run_direct_probe(
    before: Path,
    after: Path,
    max_entities: int,
    max_dxf_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    progress_times: list[float] = []
    started = time.perf_counter()

    def progress_callback(_current: int, _total: int, _message: str) -> None:
        progress_times.append(time.perf_counter())

    DwgDiffer(config={"max_entities": max_entities, "max_dxf_tokens": max_dxf_tokens}).compare(
        before,
        after,
        progress_callback=progress_callback,
    )
    ended = time.perf_counter()
    progress_gap = _max_gap([started, *progress_times, ended])

    cancel_started = time.perf_counter()
    cancel_at = cancel_started + min(0.05, max(timeout_seconds / 1000.0, 0.001))

    def is_cancelled() -> bool:
        return time.perf_counter() >= cancel_at

    DwgDiffer(config={"max_entities": max_entities, "max_dxf_tokens": max_dxf_tokens}).compare(
        before,
        after,
        is_cancelled=is_cancelled,
    )
    cancel_ended = time.perf_counter()
    return {
        "status": "passed",
        "progress_event_count": len(progress_times),
        "progress_max_gap_s": round(progress_gap, 6),
        "cancel_probe": {
            "status": "passed",
            "cancel_to_idle_s": round(max(0.0, cancel_ended - cancel_at), 6),
        },
    }


def _write_pair(pair_dir: Path, line_count: int, *, shift_every: int) -> tuple[Path, Path]:
    pair_dir.mkdir(parents=True, exist_ok=True)
    before = pair_dir / "before.dxf"
    after = pair_dir / "after.dxf"
    before.write_text(_dxf_for_lines(line_count), encoding="utf-8")
    after.write_text(_dxf_for_lines(line_count, shift_every=shift_every), encoding="utf-8")
    return before, after


def _dxf_for_lines(line_count: int, *, shift_every: int | None = None) -> str:
    parts = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "GRID", "70", "0", "62", "8", "6", "Continuous",
        "0", "LAYER", "2", "BEAM", "70", "0", "62", "7", "6", "Continuous",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for index in range(max(0, line_count)):
        y = float(index * 5)
        if shift_every and index and index % shift_every == 0:
            y += 2.0
        layer = "BEAM" if index % 5 else "GRID"
        parts.extend(
            [
                "0", "LINE",
                "5", f"P{index:07X}",
                "8", layer,
                "10", "0",
                "20", _num(y),
                "11", "1000",
                "21", _num(y),
            ]
        )
    parts.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(parts) + "\n"


def _run_compare_command(command: Sequence[str], timeout_seconds: float) -> CompareExecution:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return CompareExecution(
            exit_code=completed.returncode,
            elapsed_s=time.perf_counter() - started,
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return CompareExecution(
            exit_code=None,
            elapsed_s=time.perf_counter() - started,
            timed_out=True,
            stdout_tail=_tail(exc.stdout),
            stderr_tail=_tail(exc.stderr),
        )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _max_gap(points: Sequence[float]) -> float:
    if len(points) < 2:
        return 0.0
    return max(max(0.0, right - left) for left, right in zip(points, points[1:]))


def _num(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.6f}".rstrip("0").rstrip(".")


def _tail(value: Any, *, max_chars: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    return text[-max_chars:]


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve(path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else (ROOT / path).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--medium-line-count", type=int, default=2_000)
    parser.add_argument("--large-line-count", type=int, default=8_000)
    parser.add_argument("--large-pair-count", type=int, default=3)
    parser.add_argument("--pair-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-entities", type=int, default=200_000)
    parser.add_argument("--max-dxf-tokens", type=int, default=30_000_000)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_probe(
        out=args.out,
        work_dir=args.work_dir,
        medium_line_count=args.medium_line_count,
        large_line_count=args.large_line_count,
        large_pair_count=args.large_pair_count,
        pair_timeout_seconds=args.pair_timeout_seconds,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_dxf_tokens,
    )
    print(json.dumps({"status": "written", "out": str(args.out), "large_cad_dxf_pairs": report["large_cad_dxf_pairs"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
