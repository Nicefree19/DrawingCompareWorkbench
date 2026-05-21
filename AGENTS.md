# TEKLA_MCP Codex Agent Rules

This file is for OpenAI Codex when it works in this repository.

## Role

Codex can implement changes when the user explicitly requests implementation. Claude Code may still be the primary implementer, but Codex is allowed to edit repository files for user-approved tasks.

When the user asks for a review only (no edits), Codex should follow the Review Output format below and describe mutating commands as recommendations instead of running them.

## Operating Rules

- Codex may read and edit files when the user explicitly asks for implementation.
- Keep edits narrowly scoped to the requested task.
- Do not run destructive git commands (e.g., `reset --hard`, `clean -fdx`, force-push, branch deletion) without explicit user approval.
- Do not stage, commit, push, tag, merge, or delete files unless the user explicitly asks.
- Preserve unrelated dirty worktree changes; do not revert in-flight work by the user or Claude Code.
- Run targeted tests for the touched module after edits; broaden the suite when changes cross module boundaries.
- Do not skip git hooks (`--no-verify`) or bypass signing without explicit user approval.
- Do not assume fictional MCP tool names. Use only tools that are actually listed by the active client.

## Review Output

When Codex produces review findings (whether in review-only mode or alongside an implementation task), lead with findings ordered by severity. Use this format for each issue:

- Severity: `CRITICAL` | `HIGH` | `MEDIUM` | `LOW`
- Impact: concrete runtime, security, data, performance, or maintainability risk
- Evidence: file path, line, diff hunk, command output, or reproducible scenario
- Recommendation: specific fix direction
- Tests: targeted test or validation scenario

If no issues are found, say so clearly and list any residual test gaps.

## TEKLA_MCP Context

- Primary project domain: MIDAS Gen MGT parsing, Tekla Structures integration, structural/BIM automation, drawing comparison, and Windows release tooling.
- Collaboration source of truth: `docs/collab/`.
- Review records belong in `docs/collab/REVIEWS.md` or a Sync Packet when requested by the user or by Claude.
- Implementation work that materially changes behavior should leave a 1-line entry in `docs/collab/WORKLOG.md` (append-only) and update `docs/collab/STATUS.md` if the active work item state changes.
- Commit gates should treat unresolved `CRITICAL` or `HIGH` Codex findings as blockers unless the user explicitly accepts the risk.
