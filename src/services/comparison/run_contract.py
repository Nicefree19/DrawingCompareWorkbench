# -*- coding: utf-8 -*-
"""Run manifest and completion sentinel helpers for drawing compare pipelines."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

RUN_MANIFEST_SCHEMA_VERSION = 1


class RunManifestWriter:
    """Small durable run-status writer used by CLI and Workbench pipelines."""

    def __init__(self, output_dir: Path, *, run_id: Optional[str] = None):
        self.output_dir = Path(output_dir).resolve()
        self.path = self.output_dir / "run_manifest.json"
        self.success_path = self.output_dir / "_SUCCESS"
        self.failed_path = self.output_dir / "_FAILED"
        # Optional progress hook (stage name, status) — the pipeline's
        # hang watchdog pets through this so every transition counts as
        # progress without the manifest knowing about watchdogs.
        self.on_stage: Optional[Any] = None
        self.payload: dict[str, Any] = self._load_existing_payload() or {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "run_id": run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "status": "running",
            "inputs": {},
            "paths": {},
            "stages": {},
            "counts": {},
            "outputs": {},
            "warnings": [],
            "failures": [],
        }

    def start(
        self,
        *,
        inputs: Optional[dict[str, Any]] = None,
        paths: Optional[dict[str, Any]] = None,
        preflight: Optional[dict[str, Any]] = None,
    ) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.success_path.unlink(missing_ok=True)
        self.failed_path.unlink(missing_ok=True)
        if inputs:
            self.payload["inputs"] = _json_safe(inputs)
        if paths:
            self.payload["paths"] = _json_safe(paths)
        if preflight is not None:
            self.payload["preflight_result"] = _json_safe(preflight)
        self.write()

    def stage(self, name: str, status: str, **extra: Any) -> None:
        self.payload.setdefault("stages", {})[name] = {
            "status": status,
            "updated_at": datetime.now().isoformat(),
            **_json_safe(extra),
        }
        self.write()
        hook = self.on_stage
        if hook is not None:
            try:
                hook(name, status)
            except Exception:  # noqa: BLE001 - progress hook must stay non-fatal
                logger.debug("run manifest on_stage hook failed", exc_info=True)

    def complete(
        self,
        *,
        counts: Optional[dict[str, Any]] = None,
        outputs: Optional[dict[str, Any]] = None,
        warnings: Optional[list[str]] = None,
        failures: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._finalize_running_stages()
        self.payload["status"] = "completed"
        self.payload["finished_at"] = datetime.now().isoformat()
        if counts:
            self.payload["counts"] = _json_safe(counts)
        if outputs:
            self.payload["outputs"] = _json_safe(outputs)
        if warnings:
            self.payload["warnings"] = list(self.payload.get("warnings", [])) + list(warnings)
        if failures:
            self.payload["failures"] = list(self.payload.get("failures", [])) + _json_safe(
                failures
            )
        self.write()
        self.failed_path.unlink(missing_ok=True)
        self.success_path.write_text(
            json.dumps(
                {
                    "run_id": self.payload["run_id"],
                    "completed_at": self.payload["finished_at"],
                    "run_manifest": str(self.path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _finalize_running_stages(self) -> None:
        stages = self.payload.setdefault("stages", {})
        if not isinstance(stages, dict):
            self.payload["stages"] = {}
            return
        finalized_at = datetime.now().isoformat()
        for stage in stages.values():
            if not isinstance(stage, dict):
                continue
            if stage.get("status") == "running":
                stage["status"] = "completed"
                stage["updated_at"] = finalized_at
                stage["auto_finalized"] = True

    def fail(self, stage: str, error: BaseException | str) -> None:
        message = str(error)
        self.payload["status"] = "failed"
        self.payload["finished_at"] = datetime.now().isoformat()
        self.payload.setdefault("failures", []).append(
            {
                "stage": stage,
                "error": message,
                "failed_at": datetime.now().isoformat(),
            }
        )
        self.stage(stage, "failed", error=message)
        self.write()
        self.success_path.unlink(missing_ok=True)
        self.failed_path.write_text(
            json.dumps(
                {
                    "run_id": self.payload["run_id"],
                    "failed_at": self.payload["finished_at"],
                    "stage": stage,
                    "error": message,
                    "run_manifest": str(self.path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def write(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            temp_path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _load_existing_payload(self) -> Optional[dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None


def validate_run_completion(
    run_manifest_path: Optional[str],
    success_sentinel_path: Optional[str],
) -> dict[str, Any]:
    """Cross-check the run manifest against the ``_SUCCESS`` sentinel.

    Used by the Workbench when loading a finished result to make sure we are not
    showing partial output as completed. Returns a dict with:

    - ``valid`` (bool) — True when the sentinel exists and run_id matches manifest
    - ``status`` (str) — ``ok`` | ``missing_sentinel`` | ``run_id_mismatch`` |
      ``manifest_unreadable`` | ``manifest_missing``
    - ``message`` (str) — human-readable reason in Korean for direct UI display
    - ``run_id`` (str) — sentinel run_id when available, else empty
    """

    sentinel_path_str = str(success_sentinel_path or "")
    manifest_path_str = str(run_manifest_path or "")
    sentinel_path = Path(sentinel_path_str) if sentinel_path_str else None
    manifest_path = Path(manifest_path_str) if manifest_path_str else None

    if sentinel_path is None or not sentinel_path.exists():
        return {
            "valid": False,
            "status": "missing_sentinel",
            "message": "_SUCCESS 미생성 - 부분 결과일 수 있습니다.",
            "run_id": "",
        }
    try:
        sentinel_payload = json.loads(sentinel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "valid": False,
            "status": "missing_sentinel",
            "message": "_SUCCESS 파일을 읽을 수 없어 완료 여부를 확인할 수 없습니다.",
            "run_id": "",
        }
    sentinel_run_id = str(sentinel_payload.get("run_id") or "")
    if not manifest_path or not manifest_path.exists():
        return {
            "valid": False,
            "status": "manifest_missing",
            "message": "run_manifest.json이 없어 완료 여부를 확인할 수 없습니다.",
            "run_id": sentinel_run_id,
        }
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "valid": False,
            "status": "manifest_unreadable",
            "message": "run_manifest.json 파싱 실패 - 결과 무결성을 확인할 수 없습니다.",
            "run_id": sentinel_run_id,
        }
    manifest_run_id = str(manifest_payload.get("run_id") or "")
    if sentinel_run_id and manifest_run_id and sentinel_run_id != manifest_run_id:
        return {
            "valid": False,
            "status": "run_id_mismatch",
            "message": "_SUCCESS run_id가 manifest와 다릅니다 - 다른 실행의 결과가 섞여 있을 수 있습니다.",
            "run_id": sentinel_run_id,
        }
    return {
        "valid": True,
        "status": "ok",
        "message": "정상 완료",
        "run_id": sentinel_run_id or manifest_run_id,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
