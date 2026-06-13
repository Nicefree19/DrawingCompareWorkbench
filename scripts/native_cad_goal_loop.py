"""Native CAD goal-loop ledger and invariant runner.

This script does not implement the native CAD bridge itself. It gives agents a
small, repeatable control loop for that work: initialize a dirty-worktree
baseline, append ledger checkpoints, and run the cheap/global invariants that
protect the repository while the larger vertical slice is built.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = Path(".local/native_cad_goal_loop_state.json")
DEFAULT_LEDGER_PATH = Path("docs/collab/native_slice_ledger.md")
MONOLITH_PATH = Path("src/gui/drawing_compare_workbench.py")
MONOLITH_ADDED_LINE_LIMIT = 5
TEXT_SCAN_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".qml",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

# Stored as parts so the enforcement code can contain the claim policy without
# putting forbidden product claims verbatim into this source diff.
FORBIDDEN_CLAIM_PARTS: tuple[tuple[str, str], ...] = (
    ("All DWG", "versions are supported"),
    ("Modern DWG native", "support is complete"),
    ("AC1032 native DWG", "is supported by default"),
    ("customer default path automatically", "converts or imports all DWGs"),
)

POLICY_GATE_COMMAND = (
    sys.executable,
    "scripts/cad_policy_gate.py",
    "--root",
    ".",
)

FALLBACK_TEST_COMMAND = (
    sys.executable,
    "-m",
    "pytest",
    "tests/unit/services/comparison/test_dwg_importer.py",
    "tests/unit/services/comparison/test_dwg_native_reader.py",
    "tests/unit/services/comparison/test_import_compare_pipeline.py",
    "-p",
    "no:xdist",
    "-q",
)
VERSION_MATRIX_COMMAND = (
    sys.executable,
    "scripts/native_cad_version_matrix.py",
    "validate",
)


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str = ""
    command: tuple[str, ...] | None = None
    output_tail: str = ""

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "SKIP"}

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.command:
            payload["command"] = list(self.command)
        if self.output_tail:
            payload["output_tail"] = self.output_tail
        return payload


CommandRunner = Callable[[Sequence[str], Path], CommandResult]


def run_command(command: Sequence[str], cwd: Path = REPO_ROOT) -> CommandResult:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CommandResult(tuple(str(part) for part in command), completed.returncode, completed.stdout, completed.stderr)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tail_lines(text: str, limit: int = 12) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def parse_porcelain_paths(output: str) -> set[str]:
    return {path for _status, path in parse_porcelain_entries(output)}


def parse_porcelain_entries(output: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((status, path.replace("\\", "/")))
    return entries


def git(command: Sequence[str], runner: CommandRunner = run_command) -> CommandResult:
    return runner(("git", *command), REPO_ROOT)


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def baseline_snapshot(repo_root: Path, status_output: str) -> list[dict[str, str | None]]:
    snapshot: list[dict[str, str | None]] = []
    for status, path_text in parse_porcelain_entries(status_output):
        path = repo_root / path_text
        snapshot.append(
            {
                "path": path_text,
                "status": status,
                "sha256": file_sha256(path),
            }
        )
    return snapshot


def create_state(
    *,
    repo_root: Path = REPO_ROOT,
    state_path: Path = DEFAULT_STATE_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    runner: CommandRunner = run_command,
    overwrite: bool = False,
) -> dict[str, object]:
    resolved_state = repo_root / state_path
    if resolved_state.exists() and not overwrite:
        raise FileExistsError(f"loop state already exists: {resolved_state}")

    status = runner(("git", "status", "--porcelain"), repo_root)
    if status.returncode != 0:
        raise RuntimeError(status.combined_output or "git status failed")

    payload: dict[str, object] = {
        "schema_version": 1,
        "created_at": utc_timestamp(),
        "repo_root": str(repo_root),
        "ledger_path": str(ledger_path).replace("\\", "/"),
        "baseline_status": status.stdout.splitlines(),
        "baseline_paths": sorted(parse_porcelain_paths(status.stdout)),
        "baseline_snapshot": baseline_snapshot(repo_root, status.stdout),
    }
    resolved_state.parent.mkdir(parents=True, exist_ok=True)
    resolved_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    resolved_ledger = repo_root / ledger_path
    if not resolved_ledger.exists():
        resolved_ledger.parent.mkdir(parents=True, exist_ok=True)
        resolved_ledger.write_text(
            "# Native CAD Goal Loop Ledger\n\n"
            "Append-only working ledger for the native CAD vertical-slice loop.\n"
            "This file is a local execution artifact and should not be committed.\n\n",
            encoding="utf-8",
        )
    append_checkpoint(
        stage="G0",
        goal="context lock initialized",
        actions=f"captured git status baseline in {state_path}",
        evidence=f"baseline_paths={len(payload['baseline_paths'])}",
        verdict="PASS",
        next_stage="G1",
        ledger_path=ledger_path,
        repo_root=repo_root,
    )
    return payload


def load_state(repo_root: Path = REPO_ROOT, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, object] | None:
    resolved = repo_root / state_path
    if not resolved.exists():
        return None
    return json.loads(resolved.read_text(encoding="utf-8"))


def append_checkpoint(
    *,
    stage: str,
    goal: str,
    actions: str,
    evidence: str,
    verdict: str,
    next_stage: str,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    repo_root: Path = REPO_ROOT,
) -> None:
    resolved = repo_root / ledger_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    block = (
        f"[iter auto | {stage} | {utc_timestamp()}]\n"
        f"GOAL: {goal}\n"
        f"ACTIONS: {actions}\n"
        f"EVIDENCE: {evidence}\n"
        f"VERDICT: {verdict}\n"
        f"NEXT: {next_stage}\n\n"
    )
    with resolved.open("a", encoding="utf-8") as handle:
        handle.write(block)


def parse_numstat_added(output: str) -> int:
    total = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added = parts[0]
        if added == "-":
            continue
        try:
            total += int(added)
        except ValueError:
            continue
    return total


def forbidden_claims() -> list[str]:
    return [left + " " + right for left, right in FORBIDDEN_CLAIM_PARTS]


def find_forbidden_claims(diff_text: str) -> list[str]:
    lowered = (diff_text or "").lower()
    return [claim for claim in forbidden_claims() if claim.lower() in lowered]


def is_text_scan_path(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SCAN_SUFFIXES


def untracked_files(repo_root: Path, runner: CommandRunner = run_command) -> list[Path]:
    result = runner(("git", "ls-files", "--others", "--exclude-standard"), repo_root)
    if result.returncode != 0:
        return []
    files: list[Path] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path = repo_root / line.strip()
        if path.is_file():
            files.append(path)
    return files


def untracked_text_payload(repo_root: Path, runner: CommandRunner = run_command) -> str:
    chunks: list[str] = []
    for path in untracked_files(repo_root, runner):
        if not is_text_scan_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        chunks.append(f"\n--- untracked:{rel} ---\n{text}")
    return "".join(chunks)


def untracked_whitespace_issues(repo_root: Path, runner: CommandRunner = run_command) -> list[str]:
    issues: list[str] = []
    for path in untracked_files(repo_root, runner):
        if not is_text_scan_path(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        for lineno, line in enumerate(lines, start=1):
            if line.rstrip(" \t") != line:
                issues.append(f"{rel}:{lineno}: trailing whitespace")
    return issues


def changed_python_files(repo_root: Path, runner: CommandRunner = run_command) -> list[Path]:
    diff = runner(("git", "diff", "--name-only", "--", "*.py"), repo_root)
    staged = runner(("git", "diff", "--cached", "--name-only", "--", "*.py"), repo_root)
    status = runner(("git", "status", "--porcelain"), repo_root)
    names = set(diff.stdout.splitlines()) | set(staged.stdout.splitlines())
    for line in status.stdout.splitlines():
        if line.startswith("?? ") and line[3:].endswith(".py"):
            names.add(line[3:])
    paths = []
    for name in sorted(names):
        path = repo_root / name
        if path.exists() and path.suffix == ".py":
            paths.append(path)
    return paths


def check_nothing_staged(runner: CommandRunner) -> CheckResult:
    result = git(("diff", "--cached", "--stat"), runner)
    passed = result.returncode == 0 and not result.stdout.strip() and not result.stderr.strip()
    return CheckResult(
        "I1 nothing staged",
        "PASS" if passed else "FAIL",
        "staged diff is empty" if passed else "staged changes are present",
        result.command,
        tail_lines(result.combined_output),
    )


def check_dirty_preservation(
    state: dict[str, object] | None,
    runner: CommandRunner,
    *,
    repo_root: Path = REPO_ROOT,
) -> CheckResult:
    if state is None:
        return CheckResult("I2 dirty-file preservation", "FAIL", "missing state; run init first")
    baseline_paths = set(str(item) for item in state.get("baseline_paths", []))
    baseline_entries = {
        str(item.get("path")): item
        for item in state.get("baseline_snapshot", [])
        if isinstance(item, dict) and item.get("path")
    }
    status = git(("status", "--porcelain"), runner)
    current_entries = {path: status for status, path in parse_porcelain_entries(status.stdout)}
    current_paths = set(current_entries)
    missing = sorted(baseline_paths - current_paths)
    changed: list[str] = []
    for path_text, entry in baseline_entries.items():
        if path_text not in current_entries:
            continue
        expected_status = str(entry.get("status") or "")
        if expected_status and current_entries[path_text] != expected_status:
            changed.append(f"{path_text}: status {expected_status!r}->{current_entries[path_text]!r}")
            continue
        expected_sha = entry.get("sha256")
        if expected_sha:
            actual_sha = file_sha256(repo_root / path_text)
            if actual_sha != expected_sha:
                changed.append(f"{path_text}: content changed")
    passed = status.returncode == 0 and not missing and not changed
    if passed:
        detail = "baseline dirty paths and content preserved"
    else:
        parts = []
        if missing:
            parts.append("missing baseline dirty paths: " + ", ".join(missing))
        if changed:
            parts.append("changed baseline dirty entries: " + " | ".join(changed))
        detail = "; ".join(parts)
    return CheckResult("I2 dirty-file preservation", "PASS" if passed else "FAIL", detail, status.command, tail_lines(status.combined_output))


def check_ledger(state: dict[str, object] | None, *, repo_root: Path = REPO_ROOT) -> CheckResult:
    if state is None:
        return CheckResult("I0 ledger", "FAIL", "missing state; run init first")
    ledger_value = state.get("ledger_path")
    if not ledger_value:
        return CheckResult("I0 ledger", "FAIL", "state has no ledger_path")
    path = repo_root / str(ledger_value)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult("I0 ledger", "FAIL", f"ledger unreadable: {exc}")
    has_checkpoint = "GOAL:" in text and "NEXT:" in text and "VERDICT:" in text
    return CheckResult(
        "I0 ledger",
        "PASS" if has_checkpoint else "FAIL",
        "ledger readable with checkpoints" if has_checkpoint else "ledger missing checkpoint fields",
    )


def check_monolith_added_lines(runner: CommandRunner) -> CheckResult:
    result = git(("diff", "--numstat", "--", str(MONOLITH_PATH)), runner)
    added = parse_numstat_added(result.stdout)
    passed = result.returncode == 0 and added <= MONOLITH_ADDED_LINE_LIMIT
    detail = f"added_lines={added}, limit={MONOLITH_ADDED_LINE_LIMIT}"
    return CheckResult("I3 monolith freeze", "PASS" if passed else "FAIL", detail, result.command, tail_lines(result.combined_output))


def check_forbidden_wording(runner: CommandRunner) -> CheckResult:
    result = git(("diff", "--", "."), runner)
    payload = (result.stdout or "") + untracked_text_payload(REPO_ROOT, runner)
    hits = find_forbidden_claims(payload)
    passed = result.returncode == 0 and not hits
    detail = "no forbidden wording in diff" if passed else "forbidden wording: " + " | ".join(hits)
    return CheckResult("I5 forbidden wording", "PASS" if passed else "FAIL", detail, result.command, "")


def check_policy_gate(runner: CommandRunner, *, quick: bool) -> CheckResult:
    if quick:
        return CheckResult("I4 policy gate", "SKIP", "skipped by --quick")
    result = runner(POLICY_GATE_COMMAND, REPO_ROOT)
    passed = result.returncode == 0
    return CheckResult("I4 policy gate", "PASS" if passed else "FAIL", "policy gate passed" if passed else "policy gate failed", result.command, tail_lines(result.combined_output))


def check_fallback_tests(runner: CommandRunner, *, run_fallback_tests: bool) -> CheckResult:
    if not run_fallback_tests:
        return CheckResult("I6 fallback intact", "SKIP", "skipped unless --run-fallback-tests is set")
    result = runner(FALLBACK_TEST_COMMAND, REPO_ROOT)
    passed = result.returncode == 0
    return CheckResult("I6 fallback intact", "PASS" if passed else "FAIL", "fallback tests passed" if passed else "fallback tests failed", result.command, tail_lines(result.combined_output))


def check_py_compile(runner: CommandRunner) -> CheckResult:
    paths = changed_python_files(REPO_ROOT, runner)
    if not paths:
        return CheckResult("I7 compile", "PASS", "no changed Python files")
    command = (sys.executable, "-m", "py_compile", *[str(path.relative_to(REPO_ROOT)) for path in paths])
    result = runner(command, REPO_ROOT)
    passed = result.returncode == 0
    detail = f"compiled {len(paths)} changed Python file(s)" if passed else "py_compile failed"
    return CheckResult("I7 compile", "PASS" if passed else "FAIL", detail, result.command, tail_lines(result.combined_output))


def check_diff_check(runner: CommandRunner) -> CheckResult:
    result = git(("diff", "--check"), runner)
    untracked_issues = untracked_whitespace_issues(REPO_ROOT, runner)
    passed = result.returncode == 0 and not untracked_issues
    if passed:
        detail = "git diff --check clean"
    elif untracked_issues:
        detail = "untracked whitespace issues: " + " | ".join(untracked_issues[:10])
    else:
        detail = "git diff --check failed"
    return CheckResult("I8 whitespace", "PASS" if passed else "FAIL", detail, result.command, tail_lines(result.combined_output))


def check_version_matrix(runner: CommandRunner) -> CheckResult:
    result = runner(VERSION_MATRIX_COMMAND, REPO_ROOT)
    passed = result.returncode == 0
    return CheckResult(
        "I9 version matrix",
        "PASS" if passed else "FAIL",
        "native CAD version matrix passed" if passed else "native CAD version matrix failed",
        result.command,
        tail_lines(result.combined_output),
    )


def run_invariants(
    *,
    quick: bool = False,
    run_fallback_tests: bool = False,
    state_path: Path = DEFAULT_STATE_PATH,
    runner: CommandRunner = run_command,
) -> list[CheckResult]:
    state = load_state(REPO_ROOT, state_path)
    return [
        check_ledger(state),
        check_nothing_staged(runner),
        check_dirty_preservation(state, runner),
        check_monolith_added_lines(runner),
        check_policy_gate(runner, quick=quick),
        check_forbidden_wording(runner),
        check_fallback_tests(runner, run_fallback_tests=run_fallback_tests),
        check_py_compile(runner),
        check_diff_check(runner),
        check_version_matrix(runner),
    ]


def summarize(results: Iterable[CheckResult]) -> tuple[bool, list[dict[str, object]]]:
    payload = [result.to_dict() for result in results]
    return all(result["status"] in {"PASS", "SKIP"} for result in payload), payload


def command_init(args: argparse.Namespace) -> int:
    create_state(
        state_path=Path(args.state),
        ledger_path=Path(args.ledger),
        overwrite=args.overwrite,
    )
    print(f"initialized loop state: {args.state}")
    print(f"ledger: {args.ledger}")
    return 0


def command_checkpoint(args: argparse.Namespace) -> int:
    append_checkpoint(
        stage=args.stage,
        goal=args.goal,
        actions=args.actions,
        evidence=args.evidence,
        verdict=args.verdict,
        next_stage=args.next,
        ledger_path=Path(args.ledger),
    )
    print(f"checkpoint appended: {args.stage} -> {args.next}")
    return 0


def command_invariants(args: argparse.Namespace) -> int:
    results = run_invariants(
        quick=args.quick,
        run_fallback_tests=args.run_fallback_tests,
        state_path=Path(args.state),
    )
    passed, payload = summarize(results)
    output = {
        "schema_version": 1,
        "generated_at": utc_timestamp(),
        "status": "PASS" if passed else "FAIL",
        "checks": payload,
    }
    text = json.dumps(output, indent=2, ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create loop state and ledger.")
    init.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    init.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    init.add_argument("--overwrite", action="store_true")
    init.set_defaults(func=command_init)

    checkpoint = subparsers.add_parser("checkpoint", help="Append one ledger checkpoint.")
    checkpoint.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    checkpoint.add_argument("--stage", required=True)
    checkpoint.add_argument("--goal", required=True)
    checkpoint.add_argument("--actions", required=True)
    checkpoint.add_argument("--evidence", required=True)
    checkpoint.add_argument("--verdict", required=True, choices=("PASS", "FAIL", "PARTIAL"))
    checkpoint.add_argument("--next", required=True)
    checkpoint.set_defaults(func=command_checkpoint)

    invariants = subparsers.add_parser("invariants", help="Run global loop invariants.")
    invariants.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    invariants.add_argument("--quick", action="store_true", help="Skip expensive pytest checks.")
    invariants.add_argument("--run-fallback-tests", action="store_true")
    invariants.add_argument("--json-out")
    invariants.set_defaults(func=command_invariants)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
