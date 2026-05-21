# -*- coding: utf-8 -*-
"""
비교 설정 관리자 (Comparison Settings Manager)
===============================================

도면 비교 설정을 JSON 파일로 저장하고 불러옵니다.

Sprint 13-B: 비교 설정 저장/불러오기 기능 구현
Author: TEKLA_MCP Team
Date: 2025-12-19
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ComparisonSettingsManager:
    """도면 비교 설정 관리자

    설정을 JSON 파일로 저장하고 불러옵니다.
    기본 저장 위치: 사용자 홈/.tekla_mcp/comparison_settings.json
    """

    # Phase Q3 Codex round-2 follow-up [P2] (RV-20260509-002):
    # SCHEMA_VERSION 2 — expand_blocks default flipped False → True. legacy
    # version 1 (또는 미명시) 의 stored expand_blocks=False 는 user override
    # 가 아닌 silent default 이므로 migration 시 default 로 reset.
    SCHEMA_VERSION = 2

    DEFAULT_SETTINGS = {
        "schema_version": SCHEMA_VERSION,
        "auto_align": True,
        "text_compare": True,
        "layout_analysis": False,
        "compare_layouts": False,
        "cloud_mark": False,
        "expand_blocks": True,  # Phase Q3 (RV-20260509-002) default flipped
        "page": 0,
        "last_old_folder": "",
        "last_new_folder": "",
        "layer_filter": None,
    }

    # Keys whose default changed in each schema bump. Loader resets these
    # when legacy version detected (user might have actively overridden,
    # but for default-flip cases the reset is the safer call — they were
    # picked silently by the old UI).
    LEGACY_DEFAULT_KEYS = {
        2: ("expand_blocks",),
    }

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Args:
            config_dir: 설정 파일 저장 디렉토리 (기본: ~/.tekla_mcp)
        """
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path.home() / ".tekla_mcp"

        self.config_file = self.config_dir / "comparison_settings.json"
        self._settings: Dict[str, Any] = self.DEFAULT_SETTINGS.copy()

        # 디렉토리 생성
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 기존 설정 로드
        self.load()

    def load(self) -> Dict[str, Any]:
        """설정 파일에서 불러오기

        Returns:
            로드된 설정 딕셔너리
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # Phase Q3 Codex round-2 [P2]: schema_version 을 raw
                    # loaded 에서 먼저 확인 (DEFAULT_SETTINGS 와 merge 하면
                    # 항상 v2 가 되어 migration trigger 안 됨).
                    stored_version = int(loaded.get("schema_version", 1))
                    # 기본값과 병합 (새 키 추가 시 호환성)
                    self._settings = {**self.DEFAULT_SETTINGS, **loaded}
                    logger.info(f"설정 로드 완료: {self.config_file}")
                    # Schema migration — legacy default 였던 키를 새 default
                    # 로 reset (user override 가 아닌 silent default 였으므로
                    # 새 기본을 사용하는 게 안전).
                    self._migrate_schema(stored_version)
            except Exception as e:
                logger.warning(f"설정 로드 실패: {e}")
                self._settings = self.DEFAULT_SETTINGS.copy()
        else:
            self._settings = self.DEFAULT_SETTINGS.copy()

        return self._settings

    def _migrate_schema(self, stored_version: int) -> None:
        """Schema version 비교 후 legacy default 키들을 새 default 로 reset.

        Args:
            stored_version: 저장된 파일에서 직접 읽은 schema version
                (기본 1 — pre-Q3 파일은 schema_version 키 자체가 없음).
        """
        if stored_version >= self.SCHEMA_VERSION:
            return
        for v in range(stored_version + 1, self.SCHEMA_VERSION + 1):
            for key in self.LEGACY_DEFAULT_KEYS.get(v, ()):
                if key in self.DEFAULT_SETTINGS:
                    self._settings[key] = self.DEFAULT_SETTINGS[key]
                    logger.info(
                        f"settings migration v{stored_version}→v{v}: "
                        f"{key} reset to {self.DEFAULT_SETTINGS[key]}"
                    )
        self._settings["schema_version"] = self.SCHEMA_VERSION
        try:
            self.save()
        except Exception:
            logger.exception("settings migration save failed (non-fatal)")

    def save(self) -> bool:
        """설정 파일로 저장

        Returns:
            성공 여부
        """
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
            logger.info(f"설정 저장 완료: {self.config_file}")
            return True
        except Exception as e:
            logger.exception(f"설정 저장 실패: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """설정 값 조회

        Args:
            key: 설정 키
            default: 기본값

        Returns:
            설정 값
        """
        return self._settings.get(key, default)

    def set(self, key: str, value: Any):
        """설정 값 저장 (메모리)

        Args:
            key: 설정 키
            value: 설정 값
        """
        self._settings[key] = value

    def update(self, settings: Dict[str, Any]):
        """여러 설정 한번에 업데이트 (메모리)

        Args:
            settings: 업데이트할 설정 딕셔너리
        """
        self._settings.update(settings)

    def get_all(self) -> Dict[str, Any]:
        """전체 설정 반환

        Returns:
            전체 설정 딕셔너리 복사본
        """
        return self._settings.copy()

    def reset(self):
        """기본 설정으로 초기화"""
        self._settings = self.DEFAULT_SETTINGS.copy()
        self.save()
        logger.info("설정 초기화됨")


# 싱글톤 인스턴스
_settings_instance: Optional[ComparisonSettingsManager] = None


def get_settings_manager() -> ComparisonSettingsManager:
    """설정 관리자 싱글톤 인스턴스 반환

    Returns:
        ComparisonSettingsManager 인스턴스
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = ComparisonSettingsManager()
    return _settings_instance
