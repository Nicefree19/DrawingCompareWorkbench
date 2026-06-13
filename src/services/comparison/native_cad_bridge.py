"""Subprocess bridge runner for native CAD scene-pack contracts."""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dwg_importer import DwgVersionInfo
from .native_scene_pack import (
    BRIDGE_RESULT_SCHEMA_VERSION,
    NATIVE_SCENE_PACK_SCHEMA_VERSION,
    NativeScenePack,
)


class NativeCadBridgeCode:
    SDK_UNAVAILABLE = "SDK_UNAVAILABLE"
    LICENSE_NOT_ALLOWED = "LICENSE_NOT_ALLOWED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    CORRUPTED_INPUT = "CORRUPTED_INPUT"
    ENCRYPTED_INPUT = "ENCRYPTED_INPUT"
    ADAPTER_FAILED = "ADAPTER_FAILED"
    CONTRACT_INVALID = "CONTRACT_INVALID"


BRIDGE_FAILURE_CODES = frozenset(
    {
        NativeCadBridgeCode.SDK_UNAVAILABLE,
        NativeCadBridgeCode.LICENSE_NOT_ALLOWED,
        NativeCadBridgeCode.UNSUPPORTED_VERSION,
        NativeCadBridgeCode.TIMEOUT,
        NativeCadBridgeCode.CANCELLED,
        NativeCadBridgeCode.CORRUPTED_INPUT,
        NativeCadBridgeCode.ENCRYPTED_INPUT,
        NativeCadBridgeCode.ADAPTER_FAILED,
        NativeCadBridgeCode.CONTRACT_INVALID,
    }
)


@dataclass(frozen=True)
class NativeCadBridgeFailure:
    code: str
    message: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class NativeCadBridgeResult:
    scene_pack: NativeScenePack | None = None
    drawing: dict[str, Any] | None = None
    failure: NativeCadBridgeFailure | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.scene_pack is not None and self.failure is None


class NativeCadBridgeRunner:
    """Run a local bridge command that emits native CAD bridge JSON."""

    def __init__(
        self,
        *,
        command: str | Path | None = None,
        args_template: Sequence[str] | None = None,
        timeout_seconds: float = 120.0,
        adapter_id: str = "native-cad-json-bridge",
    ) -> None:
        self.command = str(command or "").strip().strip('"')
        self.args_template = tuple(args_template or ("{input}", "{acadver}"))
        self.timeout_seconds = max(1.0, float(timeout_seconds or 120.0))
        self.adapter_id = adapter_id

    def diagnostics(self) -> dict[str, Any]:
        resolved = self.resolved_command()
        return {
            "kind": "native_cad_bridge_runner",
            "adapter_id": self.adapter_id,
            "command": self.command,
            "resolved_command": resolved,
            "command_exists": bool(resolved),
            "command_sha256": _file_sha256(resolved),
            "args_template": list(self.args_template),
            "args_template_file_sha256": _template_file_hashes(self.args_template),
            "timeout_seconds": self.timeout_seconds,
        }

    def resolved_command(self) -> str:
        if not self.command:
            return ""
        candidate = Path(self.command)
        if candidate.exists() and candidate.is_file():
            return str(candidate)
        return shutil.which(self.command) or ""

    def run(self, path: str | Path, version: DwgVersionInfo) -> NativeCadBridgeResult:
        command = self.resolved_command()
        if not command:
            return self._failure(
                NativeCadBridgeCode.SDK_UNAVAILABLE,
                "Native CAD bridge command is not configured or not found.",
                {"path": str(path), "dwg_version": version.to_dict()},
            )
        cmd = [command, *self._render_args(Path(path), version)]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._failure(
                NativeCadBridgeCode.TIMEOUT,
                f"Native CAD bridge timed out after {self.timeout_seconds:g}s.",
                {"path": str(path), "command": command, "dwg_version": version.to_dict()},
            )
        if completed.returncode != 0:
            payload_failure = _failure_from_json(completed.stdout or completed.stderr)
            if payload_failure is not None:
                return NativeCadBridgeResult(failure=payload_failure)
            return self._failure(
                NativeCadBridgeCode.ADAPTER_FAILED,
                f"Native CAD bridge failed with exit code {completed.returncode}.",
                {
                    "path": str(path),
                    "command": command,
                    "exit_code": completed.returncode,
                    "stderr": (completed.stderr or "")[:800],
                },
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return self._failure(
                NativeCadBridgeCode.CONTRACT_INVALID,
                f"Native CAD bridge did not emit valid JSON: {exc}",
                {"path": str(path), "command": command},
            )
        return parse_bridge_payload(payload)

    def _render_args(self, path: Path, version: DwgVersionInfo) -> list[str]:
        replacements = {
            "input": str(path),
            "path": str(path),
            "stem": path.stem,
            "version": version.code,
            "acadver": version.code,
            "family": version.family,
            "release": version.release,
        }
        return [str(item).format(**replacements) for item in self.args_template]

    def _failure(
        self,
        code: str,
        message: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> NativeCadBridgeResult:
        payload = dict(diagnostics or {})
        payload["bridge"] = self.diagnostics()
        return NativeCadBridgeResult(
            failure=NativeCadBridgeFailure(code=code, message=message, diagnostics=payload)
        )


def parse_bridge_payload(payload: object) -> NativeCadBridgeResult:
    if not isinstance(payload, dict):
        return NativeCadBridgeResult(
            failure=NativeCadBridgeFailure(
                code=NativeCadBridgeCode.CONTRACT_INVALID,
                message="Native CAD bridge payload must be a JSON object.",
            )
        )
    failure_payload = payload.get("failure") or payload.get("error")
    if isinstance(failure_payload, dict):
        return NativeCadBridgeResult(failure=_failure_from_mapping(failure_payload))
    if payload.get("schema_version") != BRIDGE_RESULT_SCHEMA_VERSION:
        return NativeCadBridgeResult(
            failure=NativeCadBridgeFailure(
                code=NativeCadBridgeCode.CONTRACT_INVALID,
                message="Native CAD bridge payload schema_version is unsupported.",
                diagnostics={"schema_version": payload.get("schema_version")},
            ),
            raw_payload=dict(payload),
        )
    scene_payload = payload.get("scene_pack")
    if not isinstance(scene_payload, dict):
        return NativeCadBridgeResult(
            failure=NativeCadBridgeFailure(
                code=NativeCadBridgeCode.CONTRACT_INVALID,
                message="Native CAD bridge payload is missing scene_pack.",
            ),
            raw_payload=dict(payload),
        )
    if scene_payload.get("schema_version") != NATIVE_SCENE_PACK_SCHEMA_VERSION:
        return NativeCadBridgeResult(
            failure=NativeCadBridgeFailure(
                code=NativeCadBridgeCode.CONTRACT_INVALID,
                message="Native CAD scene_pack schema_version is unsupported.",
                diagnostics={"schema_version": scene_payload.get("schema_version")},
            ),
            raw_payload=dict(payload),
        )
    scene_pack = NativeScenePack.from_dict(scene_payload)
    drawing = payload.get("drawing") if isinstance(payload.get("drawing"), dict) else None
    return NativeCadBridgeResult(scene_pack=scene_pack, drawing=drawing, raw_payload=dict(payload))


def _failure_from_json(text: str) -> NativeCadBridgeFailure | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        failure_payload = payload.get("failure") or payload.get("error") or payload
        if isinstance(failure_payload, dict) and failure_payload.get("code"):
            return _failure_from_mapping(failure_payload)
    return None


def _failure_from_mapping(payload: Mapping[str, Any]) -> NativeCadBridgeFailure:
    code = str(payload.get("code") or NativeCadBridgeCode.ADAPTER_FAILED)
    if code not in BRIDGE_FAILURE_CODES:
        code = NativeCadBridgeCode.ADAPTER_FAILED
    return NativeCadBridgeFailure(
        code=code,
        message=str(payload.get("message") or code),
        diagnostics=dict(payload.get("diagnostics") or {}),
    )


def _file_sha256(path: str | Path) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _template_file_hashes(args_template: Sequence[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in args_template:
        text = str(item)
        if "{" in text or "}" in text:
            continue
        digest = _file_sha256(text)
        if digest:
            hashes[text] = digest
    return hashes


__all__ = [
    "BRIDGE_FAILURE_CODES",
    "NativeCadBridgeCode",
    "NativeCadBridgeFailure",
    "NativeCadBridgeResult",
    "NativeCadBridgeRunner",
    "parse_bridge_payload",
]
