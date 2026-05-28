"""Safety checks for MCP-facing structural drawing outputs.

The checks are intentionally conservative and deterministic. They are meant to
guard compact evidence packets and draft packets from raw CAD payload leakage,
secret-like text, and wording that could be read as an automatic approval or
release.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


RAW_PAYLOAD_KEYS = frozenset({
    "canonical_drawing",
    "normalized_drawing",
    "raw_drawing",
    "raw_payload",
    "raw_entities",
    "raw_entity",
    "modelspace",
    "paperspace",
})
SECRET_LIKE_RE = re.compile(
    r"\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}",
    re.IGNORECASE,
)
POSITIVE_ACTION_PHRASES = (
    "approved",
    "released",
    "send now",
    "submit now",
    "can proceed",
    "approval granted",
    "release is allowed",
)
MISLEADING_ALL_CLEAR_PHRASES = (
    "no issues found",
    "no issue found",
    "no problems found",
    "all clear",
    "nothing to review",
)
BAD_SOURCE_HEALTH = frozenset({"partial", "unsupported", "failed", "missing", "timeout", "error"})


def find_structural_output_safety_findings(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return safety findings for a compact structural output payload."""

    findings: list[dict[str, str]] = []
    findings.extend(_raw_key_findings(payload))
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    findings.extend(_secret_findings(text))
    findings.extend(_positive_action_language_findings(payload))
    findings.extend(_misleading_source_health_findings(payload))
    return findings


def assert_structural_output_safe(payload: Mapping[str, Any]) -> None:
    """Raise ValueError if a compact structural output fails the safety scan."""

    findings = find_structural_output_safety_findings(payload)
    if findings:
        details = "; ".join(f"{finding['code']} at {finding['path']}" for finding in findings)
        raise ValueError(f"structural output safety findings: {details}")


def _raw_key_findings(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path, key in _walk_keys(payload):
        if key.casefold() in RAW_PAYLOAD_KEYS:
            findings.append(
                {
                    "code": "raw_payload_key",
                    "path": path,
                    "message": f"Forbidden raw payload key: {key}",
                }
            )
    return findings


def _secret_findings(text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for match in SECRET_LIKE_RE.finditer(text):
        findings.append(
            {
                "code": "secret_like_marker",
                "path": "$",
                "message": f"Secret-like marker: {match.group(1)}",
            }
        )
    return findings


def _positive_action_language_findings(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    checked_text = "\n".join(_user_facing_texts(payload)).casefold()
    findings: list[dict[str, str]] = []
    for phrase in POSITIVE_ACTION_PHRASES:
        if phrase in checked_text:
            findings.append(
                {
                    "code": "positive_action_language",
                    "path": "$.summary|$.draft|$.issue_suggestions",
                    "message": f"Forbidden positive action phrase: {phrase}",
                }
            )
    return findings


def _misleading_source_health_findings(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    source_health = str(
        _path_get(payload, ("source", "source_health"))
        or _path_get(payload, ("basis", "source_health"))
        or ""
    ).casefold()
    if source_health not in BAD_SOURCE_HEALTH:
        return []
    checked_text = " ".join(
        str(value)
        for value in (
            _path_get(payload, ("summary", "answer")),
            _path_get(payload, ("draft", "body")),
        )
        if value is not None
    ).casefold()
    if not checked_text:
        return []
    for phrase in MISLEADING_ALL_CLEAR_PHRASES:
        if phrase in checked_text:
            return [
                {
                    "code": "source_health_misleading_answer",
                    "path": "$.summary.answer",
                    "message": f"Bad source health cannot be summarized as {phrase!r}.",
                }
            ]
    return []


def _walk_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            keys.append((child_path, key_text))
            keys.extend(_walk_keys(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            keys.extend(_walk_keys(child, f"{path}[{index}]"))
    return keys


def _user_facing_texts(payload: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for path in (
        ("summary", "answer"),
        ("draft", "subject"),
        ("draft", "body"),
    ):
        value = _path_get(payload, path)
        if value is not None:
            values.append(str(value))
    values.extend(str(item) for item in _path_get(payload, ("draft", "review_checklist")) or [])
    values.extend(str(item) for item in _path_get(payload, ("draft", "limitations")) or [])
    for suggestion in _path_get(payload, ("issue_suggestions",)) or []:
        if isinstance(suggestion, Mapping):
            values.extend(
                str(suggestion.get(key) or "")
                for key in ("title", "rationale", "next_action")
            )
    return values


def _path_get(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


__all__ = [
    "assert_structural_output_safe",
    "find_structural_output_safety_findings",
]
