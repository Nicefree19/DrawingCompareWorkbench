"""Meta-guard: the per-PR CI must keep linting changed Python files.

Closes BDC-2 enforcement. Once black/isort gating exists in the workflow, this
guards it against being silently dropped — the CI-layer silent_fallback pattern
this whole reliability arc has been closing. Text-level (no YAML parser needed)
so it runs anywhere.
"""

from __future__ import annotations

from pathlib import Path

_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "cad-format-regression.yml"
)


def _workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_workflow_lints_changed_python_files() -> None:
    text = _workflow_text()
    assert "black --check" in text, "per-PR CI must run black --check on changed files"
    assert "isort --check-only" in text, "per-PR CI must run isort --check-only on changed files"


def test_changed_files_lint_is_pull_request_scoped() -> None:
    # A push to main has no base commit to diff against, so the changed-files
    # lint must be guarded to pull_request events and diff against the PR base.
    text = _workflow_text()
    assert (
        "github.event.pull_request.base.sha" in text
    ), "changed-files lint must diff against the PR base sha"
