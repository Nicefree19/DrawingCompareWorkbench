"""Run the Group C CAD-core release gate and write machine/human reports."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "build" / "reports"
JSON_REPORT = "group-c-release-gate.json"
MARKDOWN_REPORT = "group-c-release-gate.md"


@dataclass(frozen=True)
class GateCheck:
    name: str
    command: tuple[str, ...]

    @property
    def display_command(self) -> str:
        return " ".join(self.command)


@dataclass(frozen=True)
class GateResult:
    name: str
    command: str
    returncode: int
    duration_s: float
    stdout: str
    stderr: str

    @property
    def status(self) -> str:
        return "passed" if self.returncode == 0 else "failed"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": self.command,
            "status": self.status,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 3),
            "stdout_tail": _tail(self.stdout),
            "stderr_tail": _tail(self.stderr),
        }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_checks(*, include_performance: bool = False) -> list[GateCheck]:
    checks = [
        GateCheck("git-diff-check", ("git", "diff", "--check")),
        GateCheck("cad-policy-gate", (sys.executable, "scripts\\cad_policy_gate.py")),
        GateCheck(
            "cad-format-regression",
            (
                sys.executable,
                "scripts\\cad_format_regression.py",
                "--check",
                "--report",
                "build\\reports\\cad-format-regression-report.md",
            ),
        ),
        GateCheck("dwg-native-diagnostics", (sys.executable, "scripts\\dwg_native_diagnostics.py")),
        GateCheck(
            "real-world-dwg-validation",
            (sys.executable, "scripts\\validate_real_world_dwg_samples.py"),
        ),
        GateCheck(
            "comparison-service-tests",
            (
                sys.executable,
                "-m",
                "pytest",
                "tests\\unit\\services\\comparison",
                "-q",
                "--tb=short",
                "--disable-warnings",
                "-o",
                "log_cli=false",
                "-o",
                "addopts=",
            ),
        ),
        GateCheck(
            "script-tests",
            (
                sys.executable,
                "-m",
                "pytest",
                "tests\\unit\\scripts",
                "-q",
                "--tb=short",
                "-o",
                "log_cli=false",
            ),
        ),
    ]
    if include_performance:
        checks.append(
            GateCheck(
                "cad-performance-smoke",
                (
                    sys.executable,
                    "scripts\\cad_performance_benchmark.py",
                    "--line-counts",
                    "1000,10000,100000",
                    "--target-mb",
                    "10,50",
                    "--size-case-lines",
                    "100000",
                    "--timeout",
                    "300",
                    "--max-entities",
                    "120000",
                    "--max-tokens",
                    "30000000",
                    "--output",
                    "build\\reports\\cad-performance-smoke.json",
                ),
            )
        )
    return checks


def run_gate(
    checks: Sequence[GateCheck],
    *,
    root: Path = ROOT,
    report_dir: Path = DEFAULT_REPORT_DIR,
    runner: Runner = subprocess.run,
) -> tuple[int, list[GateResult]]:
    report_dir.mkdir(parents=True, exist_ok=True)
    results: list[GateResult] = []
    for check in checks:
        started = time.perf_counter()
        completed = runner(
            check.command,
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        results.append(
            GateResult(
                name=check.name,
                command=check.display_command,
                returncode=int(completed.returncode),
                duration_s=time.perf_counter() - started,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        )

    status = 0 if all(result.returncode == 0 for result in results) else 1
    _write_reports(status, results, report_dir)
    return status, results


def _write_reports(status: int, results: Sequence[GateResult], report_dir: Path) -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": "group-c-release-gate.v1",
        "generated_at": generated_at,
        "status": "passed" if status == 0 else "failed",
        "checks": [result.to_dict() for result in results],
    }
    (report_dir / JSON_REPORT).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Group C Release Gate Report",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Status: `{payload['status']}`",
        "",
        "| Check | Status | Duration | Command |",
        "| --- | --- | ---: | --- |",
    ]
    for result in results:
        lines.append(
            f"| `{result.name}` | `{result.status}` | {result.duration_s:.3f}s | "
            f"`{result.command}` |"
        )
    lines.append("")
    (report_dir / MARKDOWN_REPORT).write_text("\n".join(lines), encoding="utf-8")


def _tail(text: str, *, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--include-performance",
        action="store_true",
        help="Also run the 10MB/50MB/100k entity CAD performance smoke benchmark.",
    )
    args = parser.parse_args(argv)

    status, results = run_gate(
        build_checks(include_performance=args.include_performance),
        root=args.root,
        report_dir=args.report_dir,
    )
    for result in results:
        print(f"[{result.status}] {result.name} ({result.duration_s:.1f}s)")
    print(f"Group C release gate report: {args.report_dir / MARKDOWN_REPORT}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
