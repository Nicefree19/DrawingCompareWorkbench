"""Explicit commercial/internal DWG JSON bridge adapter.

This module does not bundle a DWG SDK. It lets an approved local wrapper
process convert a DWG file into the existing ``DwgAdapterDrawing`` JSON
contract, but only when selected explicitly through the commercial SDK backend.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from .dwg_backend import DWG_BACKEND_COMMERCIAL_SDK
from .dwg_importer import (
    DwgAdapterDrawing,
    DwgFailureCode,
    DwgImportError,
    DwgImporterAdapter,
    DwgVersionDetector,
    DwgVersionInfo,
    _adapter_drawing_from_dict,
)
from ._process_cleanup import (
    kill_process_tree as _kill_process_tree,
    process_ids_for_image as _process_ids_for_image,
    terminate_process as _terminate_process,
)


COMMAND_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_COMMAND"
ARGS_JSON_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_ARGS_JSON"
LICENSE_ID_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_LICENSE_ID"
SUPPORTED_VERSIONS_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_SUPPORTED_VERSIONS"
ADAPTER_NAME_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_ADAPTER_NAME"
ADAPTER_VERSION_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_ADAPTER_VERSION"
TIMEOUT_SECONDS_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_TIMEOUT_SECONDS"
TIMEOUT_CLEANUP_IMAGE_NAMES_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_TIMEOUT_CLEANUP_IMAGE_NAMES"
TIMEOUT_CLEANUP_GRACE_SECONDS_ENV = "DRAWING_COMPARE_COMMERCIAL_DWG_JSON_TIMEOUT_CLEANUP_GRACE_SECONDS"

# Exit code the bridge uses to signal a controlled timeout (GNU `timeout` convention).
# This is the wire contract between the bridge subprocess and this adapter; it must
# match tools/zwcad_dwg_json_bridge.py TIMEOUT_EXIT_CODE. Structured signalling is
# preferred over sniffing English text out of stderr.
BRIDGE_TIMEOUT_EXIT_CODE = 124


class CommercialDwgJsonBridgeAdapter(DwgImporterAdapter):
    """Run an approved wrapper that emits adapter-drawing JSON to stdout."""

    backend_mode = DWG_BACKEND_COMMERCIAL_SDK
    approval_required = True

    def __init__(
        self,
        *,
        command: str | Path | None = None,
        args_template: Sequence[str] | None = None,
        license_id: str | None = None,
        supported_versions: Sequence[str] | str | None = None,
        name: str | None = None,
        version: str | None = None,
        timeout_seconds: float | None = None,
        timeout_cleanup_image_names: Sequence[str] | str | None = None,
        timeout_cleanup_grace_seconds: float | None = None,
    ):
        self.command = str(command or os.environ.get(COMMAND_ENV) or "").strip().strip('"')
        self.args_template = tuple(args_template if args_template is not None else _args_template_from_env())
        self.license_id = str(license_id or os.environ.get(LICENSE_ID_ENV) or "COMMERCIAL-SDK-PENDING").strip()
        self.name = str(name or os.environ.get(ADAPTER_NAME_ENV) or "commercial-dwg-json-bridge").strip()
        self.version = str(version or os.environ.get(ADAPTER_VERSION_ENV) or "0").strip()
        self.supported_versions = _normalize_supported_versions(supported_versions)
        self.timeout_seconds = _timeout(timeout_seconds)
        self.timeout_cleanup_image_names = _normalize_image_names(timeout_cleanup_image_names)
        self.timeout_cleanup_grace_seconds = _timeout_cleanup_grace(timeout_cleanup_grace_seconds)
        self.implementation_status = (
            "json_bridge_configured" if self.command and self.license_id != "COMMERCIAL-SDK-PENDING" else "json_bridge_unconfigured"
        )

    def is_available(self) -> bool:
        return bool(self.command and self._resolved_command() and self.license_id != "COMMERCIAL-SDK-PENDING")

    def supports_version(self, version: DwgVersionInfo) -> bool:
        if not self.supported_versions:
            return False
        return "*" in self.supported_versions or version.code.upper() in self.supported_versions

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        command = self._resolved_command()
        if not command:
            raise DwgImportError(
                DwgFailureCode.ADAPTER_UNAVAILABLE,
                "Commercial DWG JSON bridge command is not configured or not found.",
                details={"env": COMMAND_ENV, "command": self.command},
            )
        cmd = [command, *self._render_args(Path(path), version)]
        timeout_cleanup_snapshot = _process_snapshot(self.timeout_cleanup_image_names)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_cleanup_pids = _cleanup_spawned_images(
                timeout_cleanup_snapshot,
                grace_seconds=self.timeout_cleanup_grace_seconds,
            )
            raise DwgImportError(
                DwgFailureCode.IMPORT_TIMEOUT,
                f"Commercial DWG JSON bridge timed out after {self.timeout_seconds:g}s.",
                details={
                    "command": command,
                    "timeout_seconds": self.timeout_seconds,
                    "timeout_cleanup_image_names": list(self.timeout_cleanup_image_names),
                    "timeout_cleanup_grace_seconds": self.timeout_cleanup_grace_seconds,
                    "timeout_cleanup_pids": timeout_cleanup_pids,
                },
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no bridge output")[:800]
            timed_out_by_code = completed.returncode == BRIDGE_TIMEOUT_EXIT_CODE
            if timed_out_by_code or _looks_like_timeout(detail):
                timeout_cleanup_pids = _cleanup_spawned_images(
                    timeout_cleanup_snapshot,
                    grace_seconds=self.timeout_cleanup_grace_seconds,
                )
                details = {
                    "command": command,
                    "exit_code": completed.returncode,
                    # Prefer the structured exit code; fall back to stderr text only
                    # when an older/foreign bridge does not use the timeout code.
                    "timeout_signal": "exit_code" if timed_out_by_code else "stderr",
                    "timeout_cleanup_image_names": list(self.timeout_cleanup_image_names),
                    "timeout_cleanup_grace_seconds": self.timeout_cleanup_grace_seconds,
                    "timeout_cleanup_pids": timeout_cleanup_pids,
                }
                timeout_stage = _timeout_stage(detail)
                if timeout_stage:
                    details["timeout_stage"] = timeout_stage
                raise DwgImportError(
                    DwgFailureCode.IMPORT_TIMEOUT,
                    f"Commercial DWG JSON bridge timed out: {detail}",
                    details=details,
                )
            raise DwgImportError(
                DwgFailureCode.ADAPTER_FAILED,
                f"Commercial DWG JSON bridge failed with exit code {completed.returncode}: {detail}",
                details={"command": command, "exit_code": completed.returncode},
            )
        payload = _decode_payload(completed.stdout)
        drawing = _adapter_drawing_from_dict(payload.get("drawing") if isinstance(payload.get("drawing"), dict) else payload)
        drawing.metadata.setdefault("commercial_dwg_json_bridge", {})
        drawing.metadata["commercial_dwg_json_bridge"].update(
            {
                "adapter": self.name,
                "adapter_version": self.version,
                "license_id": self.license_id,
                "backend_mode": self.backend_mode,
                "implementation_status": self.implementation_status,
                "approval_required": self.approval_required,
                "dwg_version": version.code,
                "diagnostics": self.diagnostics(),
            }
        )
        return drawing

    def diagnostics(self) -> dict[str, Any]:
        """Return bridge provenance suitable for validation artifacts."""

        resolved = self._resolved_command()
        return {
            "kind": "commercial_dwg_json_bridge",
            "command": self.command,
            "resolved_command": resolved,
            "command_exists": bool(resolved),
            "command_sha256": _file_sha256(resolved),
            "args_template": list(self.args_template or ("{input}", "{acadver}")),
            "license_id": self.license_id,
            "supported_versions": sorted(self.supported_versions),
            "timeout_seconds": self.timeout_seconds,
            "timeout_cleanup_image_names": list(self.timeout_cleanup_image_names),
            "timeout_cleanup_grace_seconds": self.timeout_cleanup_grace_seconds,
            "env": {
                "command": COMMAND_ENV,
                "args_json": ARGS_JSON_ENV,
                "license_id": LICENSE_ID_ENV,
                "supported_versions": SUPPORTED_VERSIONS_ENV,
                "adapter_name": ADAPTER_NAME_ENV,
                "adapter_version": ADAPTER_VERSION_ENV,
                "timeout_seconds": TIMEOUT_SECONDS_ENV,
                "timeout_cleanup_image_names": TIMEOUT_CLEANUP_IMAGE_NAMES_ENV,
                "timeout_cleanup_grace_seconds": TIMEOUT_CLEANUP_GRACE_SECONDS_ENV,
            },
        }

    def _resolved_command(self) -> str:
        if not self.command:
            return ""
        candidate = Path(self.command)
        if candidate.exists() and candidate.is_file():
            return str(candidate)
        return shutil.which(self.command) or ""

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
        template = self.args_template or ("{input}", "{acadver}")
        return [str(item).format(**replacements) for item in template]


def create_adapter() -> CommercialDwgJsonBridgeAdapter:
    """Factory used by DRAWING_COMPARE_COMMERCIAL_DWG_ADAPTER."""

    return CommercialDwgJsonBridgeAdapter()


def _args_template_from_env() -> tuple[str, ...]:
    raw = os.environ.get(ARGS_JSON_ENV)
    if not raw:
        return ("{input}", "{acadver}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ARGS_JSON_ENV} must be a JSON array of argument templates.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{ARGS_JSON_ENV} must be a JSON array of strings.")
    return tuple(parsed)


def _normalize_supported_versions(value: Sequence[str] | str | None) -> frozenset[str]:
    raw: Sequence[str]
    if value is None:
        env_value = os.environ.get(SUPPORTED_VERSIONS_ENV) or ""
        raw = env_value.replace(";", ",").split(",")
    elif isinstance(value, str):
        raw = value.replace(";", ",").split(",")
    else:
        raw = value
    codes = {str(item).strip().upper() for item in raw if str(item or "").strip()}
    if "*" in codes or "ALL" in codes:
        return frozenset({"*"})
    known = set(DwgVersionDetector.SUPPORTED_CODES) | set(DwgVersionDetector.KNOWN_UNSUPPORTED_CODES)
    return frozenset(code for code in codes if code in known)


def _timeout(value: float | None) -> float:
    if value is not None:
        return max(1.0, float(value))
    raw = os.environ.get(TIMEOUT_SECONDS_ENV)
    if not raw:
        return 120.0
    try:
        return max(1.0, float(raw))
    except ValueError as exc:
        raise ValueError(f"{TIMEOUT_SECONDS_ENV} must be numeric.") from exc


def _normalize_image_names(value: Sequence[str] | str | None) -> tuple[str, ...]:
    if value is None:
        raw_items = (os.environ.get(TIMEOUT_CLEANUP_IMAGE_NAMES_ENV) or "").replace(";", ",").split(",")
    elif isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    else:
        raw_items = value
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if not item.lower().endswith(".exe"):
            item = f"{item}.exe"
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _timeout_cleanup_grace(value: float | None) -> float:
    if value is not None:
        return max(0.0, float(value))
    raw = os.environ.get(TIMEOUT_CLEANUP_GRACE_SECONDS_ENV)
    if not raw:
        return 30.0
    try:
        return max(0.0, float(raw))
    except ValueError as exc:
        raise ValueError(f"{TIMEOUT_CLEANUP_GRACE_SECONDS_ENV} must be numeric.") from exc


def _process_snapshot(image_names: Sequence[str]) -> dict[str, set[int]]:
    return {image_name: _process_ids_for_image(image_name) for image_name in image_names}


def _cleanup_spawned_images(snapshot: dict[str, set[int]], *, grace_seconds: float = 0.0) -> dict[str, list[int]]:
    killed_by_image: dict[str, list[int]] = {image_name: [] for image_name in snapshot}
    if not snapshot:
        return killed_by_image
    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while True:
        killed_this_round = 0
        kill_pending = False  # a spawned PID we tried but failed to kill -> retry next poll
        for image_name, existing_pids in snapshot.items():
            already_killed = set(killed_by_image[image_name])
            spawned = _process_ids_for_image(image_name) - set(existing_pids)
            for pid in sorted(spawned - already_killed):
                if _kill_process_tree(pid):
                    killed_by_image[image_name].append(pid)
                    killed_this_round += 1
            if spawned - set(killed_by_image[image_name]):
                kill_pending = True
        if time.monotonic() >= deadline:
            return killed_by_image
        total_killed = sum(len(pids) for pids in killed_by_image.values())
        # Settled: we have already killed at least one spawn and this poll found
        # nothing new and nothing left to retry -> return without waiting the rest of
        # the grace window (finding 9). Do NOT return merely because the first poll
        # saw a spawn (finding 10a): keep polling so late/second-wave spawns and
        # kill-failure retries are still handled until settled or the deadline.
        if total_killed and killed_this_round == 0 and not kill_pending:
            return killed_by_image
        time.sleep(0.5)


# Windows process enumeration/termination live in the shared leaf module
# _process_cleanup (imported above as _process_ids_for_image / _kill_process_tree).


def _decode_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DwgImportError(
            DwgFailureCode.ADAPTER_FAILED,
            "Commercial DWG JSON bridge did not emit valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise DwgImportError(
            DwgFailureCode.ADAPTER_FAILED,
            "Commercial DWG JSON bridge JSON payload must be an object.",
        )
    return payload


def _looks_like_timeout(detail: str) -> bool:
    normalized = str(detail or "").lower()
    return "timed out" in normalized or "timeout" in normalized or "did not produce json within" in normalized


def _timeout_stage(detail: str) -> str:
    match = re.search(r"\bduring\s+([A-Za-z0-9_.-]+)", str(detail or ""))
    return match.group(1).strip(" .,:;") if match else ""


def _file_sha256(path: str) -> str:
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


__all__ = [
    "ADAPTER_NAME_ENV",
    "ADAPTER_VERSION_ENV",
    "ARGS_JSON_ENV",
    "COMMAND_ENV",
    "CommercialDwgJsonBridgeAdapter",
    "LICENSE_ID_ENV",
    "SUPPORTED_VERSIONS_ENV",
    "TIMEOUT_CLEANUP_GRACE_SECONDS_ENV",
    "TIMEOUT_CLEANUP_IMAGE_NAMES_ENV",
    "TIMEOUT_SECONDS_ENV",
    "create_adapter",
]
