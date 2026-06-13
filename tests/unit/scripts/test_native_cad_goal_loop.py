from pathlib import Path

from scripts import native_cad_goal_loop as loop


def test_parse_porcelain_paths_handles_untracked_and_renames() -> None:
    output = "\n".join(
        [
            " M src/foo.py",
            "?? docs/local.md",
            "R  old/name.py -> new/name.py",
        ]
    )

    assert loop.parse_porcelain_paths(output) == {
        "src/foo.py",
        "docs/local.md",
        "new/name.py",
    }


def test_parse_numstat_added_sums_text_files_and_ignores_binary() -> None:
    output = "\n".join(
        [
            "12\t0\tsrc/a.py",
            "-\t-\timage.png",
            "3\t7\tdocs/b.md",
        ]
    )

    assert loop.parse_numstat_added(output) == 15


def test_find_forbidden_claims_uses_runtime_policy_without_literal_claims() -> None:
    claim = loop.forbidden_claims()[0]

    assert loop.find_forbidden_claims("+ " + claim) == [claim]
    assert loop.find_forbidden_claims("+ Native CAD bridge architecture is implemented.") == []


def test_append_checkpoint_writes_resume_ready_block(tmp_path: Path) -> None:
    ledger = Path("docs/collab/native_slice_ledger.md")

    loop.append_checkpoint(
        stage="G1",
        goal="slice contract locked",
        actions="wrote oracle table",
        evidence="oracle rows=12",
        verdict="PASS",
        next_stage="G2",
        ledger_path=ledger,
        repo_root=tmp_path,
    )

    text = (tmp_path / ledger).read_text(encoding="utf-8")
    assert "GOAL: slice contract locked" in text
    assert "ACTIONS: wrote oracle table" in text
    assert "EVIDENCE: oracle rows=12" in text
    assert "VERDICT: PASS" in text
    assert "NEXT: G2" in text


def test_create_state_captures_baseline_and_initializes_ledger(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/existing.py").write_text("print('baseline')\n", encoding="utf-8")
    (tmp_path / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    def fake_runner(command, cwd):
        assert cwd == tmp_path
        if tuple(command) == ("git", "status", "--porcelain"):
            return loop.CommandResult(tuple(command), 0, " M src/existing.py\n?? scratch.txt\n", "")
        raise AssertionError(f"unexpected command: {command}")

    payload = loop.create_state(
        repo_root=tmp_path,
        state_path=Path(".local/state.json"),
        ledger_path=Path("docs/collab/native_slice_ledger.md"),
        runner=fake_runner,
    )

    assert payload["schema_version"] == 1
    assert payload["baseline_paths"] == ["scratch.txt", "src/existing.py"]
    assert len(payload["baseline_snapshot"]) == 2
    assert (tmp_path / ".local/state.json").exists()
    ledger_text = (tmp_path / "docs/collab/native_slice_ledger.md").read_text(encoding="utf-8")
    assert "context lock initialized" in ledger_text


def test_check_dirty_preservation_fails_without_state() -> None:
    result = loop.check_dirty_preservation(None, loop.run_command)

    assert result.status == "FAIL"
    assert "run init first" in result.detail


def test_check_dirty_preservation_detects_baseline_content_changes(tmp_path: Path) -> None:
    tracked = tmp_path / "src/existing.py"
    tracked.parent.mkdir()
    tracked.write_text("print('baseline')\n", encoding="utf-8")

    state = {
        "baseline_paths": ["src/existing.py"],
        "baseline_snapshot": [
            {
                "path": "src/existing.py",
                "status": " M",
                "sha256": loop.file_sha256(tracked),
            }
        ],
    }
    tracked.write_text("print('changed')\n", encoding="utf-8")

    def fake_runner(command, cwd):
        if tuple(command) == ("git", "status", "--porcelain"):
            return loop.CommandResult(tuple(command), 0, " M src/existing.py\n", "")
        raise AssertionError(f"unexpected command: {command}")

    result = loop.check_dirty_preservation(state, fake_runner, repo_root=tmp_path)

    assert result.status == "FAIL"
    assert "content changed" in result.detail


def test_check_policy_gate_runs_repo_scanner_not_only_policy_tests() -> None:
    seen = []

    def fake_runner(command, cwd):
        seen.append(tuple(command))
        return loop.CommandResult(tuple(command), 0, "ok\n", "")

    result = loop.check_policy_gate(fake_runner, quick=False)

    assert result.status == "PASS"
    assert seen == [(loop.sys.executable, "scripts/cad_policy_gate.py", "--root", ".")]


def test_check_version_matrix_runs_matrix_validator() -> None:
    seen = []

    def fake_runner(command, cwd):
        seen.append(tuple(command))
        return loop.CommandResult(tuple(command), 0, "Native CAD version matrix passed.\n", "")

    result = loop.check_version_matrix(fake_runner)

    assert result.status == "PASS"
    assert seen == [(loop.sys.executable, "scripts/native_cad_version_matrix.py", "validate")]


def test_untracked_text_payload_scans_untracked_text_files(tmp_path: Path) -> None:
    untracked = tmp_path / "docs/new.md"
    untracked.parent.mkdir()
    claim = loop.forbidden_claims()[0]
    untracked.write_text(claim + "\n", encoding="utf-8")

    def fake_runner(command, cwd):
        if tuple(command) == ("git", "ls-files", "--others", "--exclude-standard"):
            return loop.CommandResult(tuple(command), 0, "docs/new.md\n", "")
        raise AssertionError(f"unexpected command: {command}")

    payload = loop.untracked_text_payload(tmp_path, fake_runner)

    assert claim in payload
    assert loop.find_forbidden_claims(payload) == [claim]


def test_untracked_whitespace_issues_reports_trailing_spaces(tmp_path: Path) -> None:
    untracked = tmp_path / "scripts/new.py"
    untracked.parent.mkdir()
    untracked.write_text("x = 1  \n", encoding="utf-8")

    def fake_runner(command, cwd):
        if tuple(command) == ("git", "ls-files", "--others", "--exclude-standard"):
            return loop.CommandResult(tuple(command), 0, "scripts/new.py\n", "")
        raise AssertionError(f"unexpected command: {command}")

    assert loop.untracked_whitespace_issues(tmp_path, fake_runner) == [
        "scripts/new.py:1: trailing whitespace"
    ]


def test_check_ledger_requires_resume_fields(tmp_path: Path) -> None:
    ledger = tmp_path / "docs/collab/native_slice_ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("GOAL: x\nVERDICT: PASS\nNEXT: G1\n", encoding="utf-8")
    state = {"ledger_path": "docs/collab/native_slice_ledger.md"}

    assert loop.check_ledger(state, repo_root=tmp_path).status == "PASS"

    ledger.write_text("not a checkpoint\n", encoding="utf-8")
    result = loop.check_ledger(state, repo_root=tmp_path)
    assert result.status == "FAIL"
