from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.cad_group_c_release_gate import (
    JSON_REPORT,
    MARKDOWN_REPORT,
    GateCheck,
    build_checks,
    run_gate,
)


def test_build_checks_keeps_performance_optional() -> None:
    default_names = [check.name for check in build_checks()]
    full_names = [check.name for check in build_checks(include_performance=True)]

    assert "cad-performance-smoke" not in default_names
    assert "cad-performance-smoke" in full_names
    assert default_names[:2] == ["git-diff-check", "cad-policy-gate"]


def test_run_gate_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    status, results = run_gate(
        [
            GateCheck("first", ("tool", "one")),
            GateCheck("second", ("tool", "two")),
        ],
        root=tmp_path,
        report_dir=tmp_path / "reports",
        runner=fake_runner,
    )

    assert status == 0
    assert [result.name for result in results] == ["first", "second"]
    assert calls == [("tool", "one"), ("tool", "two")]

    payload = json.loads((tmp_path / "reports" / JSON_REPORT).read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert [check["name"] for check in payload["checks"]] == ["first", "second"]

    markdown = (tmp_path / "reports" / MARKDOWN_REPORT).read_text(encoding="utf-8")
    assert "Group C Release Gate Report" in markdown
    assert "`first`" in markdown
