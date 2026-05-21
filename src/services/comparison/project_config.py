# -*- coding: utf-8 -*-
"""프로젝트 설정 저장/로드 모듈

Phase 3+ QW-4: Project Config Save/Load

프로젝트별 비교 설정을 저장하고 로드하는 기능을 제공합니다.

기능:
    - 통합 프로젝트 설정 (ComparisonConfig + ColorConfig + 메타데이터)
    - JSON/YAML 파일 저장/로드
    - 최근 프로젝트 목록 관리
    - 자동 백업 및 복구

Author: TEKLA_MCP Team
Date: 2025-12-23
Sprint: Phase 3+ QW-4
"""

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .comparison_config import (
    ComparisonConfig,
    SensitivityConfig,
    SensitivityPreset,
    LayerPriorityConfig,
)
from .visualization_service import ColorConfig

logger = logging.getLogger(__name__)


# 기본 설정 디렉토리
DEFAULT_CONFIG_DIR = Path.home() / ".tekla_mcp" / "comparison"
RECENT_PROJECTS_FILE = "recent_projects.json"
MAX_RECENT_PROJECTS = 10


@dataclass
class ProjectMetadata:
    """프로젝트 메타데이터

    Attributes:
        name: 프로젝트 이름
        description: 프로젝트 설명
        created_at: 생성 시간 (ISO 형식)
        updated_at: 수정 시간 (ISO 형식)
        version: 설정 파일 버전
        old_file_path: 비교 대상 이전 파일 경로
        new_file_path: 비교 대상 새 파일 경로
    """

    name: str = "Untitled Project"
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: str = "1.0.0"
    old_file_path: str = ""
    new_file_path: str = ""

    def __post_init__(self):
        """생성/수정 시간 자동 설정"""
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "old_file_path": self.old_file_path,
            "new_file_path": self.new_file_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectMetadata":
        """딕셔너리에서 생성"""
        return cls(
            name=data.get("name", "Untitled Project"),
            description=data.get("description", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            version=data.get("version", "1.0.0"),
            old_file_path=data.get("old_file_path", ""),
            new_file_path=data.get("new_file_path", ""),
        )

    def touch(self) -> None:
        """수정 시간 업데이트"""
        self.updated_at = datetime.now().isoformat()


@dataclass
class ProjectConfig:
    """통합 프로젝트 설정

    ComparisonConfig, ColorConfig, 메타데이터를 통합 관리합니다.

    Attributes:
        metadata: 프로젝트 메타데이터
        comparison: 비교 설정 (민감도, 레이어 우선순위 등)
        color: 색상 및 표시 설정
        sensitivity_preset: 현재 민감도 프리셋 (UI 표시용)
        top_n_filter: 상위 N개 필터 설정 (0=전체 표시)
        dwg_render_quality: DWG 상세 뷰 렌더 품질 (auto/fast/normal/high)

    Examples:
        >>> config = ProjectConfig.create_new("My Project")
        >>> config.save("my_project.json")
        >>> loaded = ProjectConfig.load("my_project.json")
    """

    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    comparison: ComparisonConfig = field(default_factory=ComparisonConfig)
    color: ColorConfig = field(default_factory=ColorConfig)
    sensitivity_preset: str = "normal"
    top_n_filter: int = 0  # 0 = 전체 표시
    dwg_render_quality: str = "auto"

    @classmethod
    def create_new(
        cls,
        name: str = "Untitled Project",
        description: str = "",
        preset: SensitivityPreset = SensitivityPreset.NORMAL,
    ) -> "ProjectConfig":
        """새 프로젝트 설정 생성

        Args:
            name: 프로젝트 이름
            description: 프로젝트 설명
            preset: 민감도 프리셋 (기본: NORMAL)

        Returns:
            새 ProjectConfig 인스턴스
        """
        return cls(
            metadata=ProjectMetadata(name=name, description=description),
            comparison=ComparisonConfig.from_preset(preset),
            color=ColorConfig.get_default(),
            sensitivity_preset=preset.value,
        )

    @classmethod
    def from_preset(cls, preset: SensitivityPreset, name: str = "") -> "ProjectConfig":
        """프리셋에서 프로젝트 설정 생성

        Args:
            preset: 민감도 프리셋
            name: 프로젝트 이름 (기본: 프리셋 이름)

        Returns:
            프리셋 기반 ProjectConfig 인스턴스
        """
        preset_name = name or f"{preset.to_korean()} 모드 프로젝트"
        return cls.create_new(name=preset_name, preset=preset)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "metadata": self.metadata.to_dict(),
            "comparison": self.comparison.to_dict(),
            "color": self.color.to_dict(),
            "sensitivity_preset": self.sensitivity_preset,
            "top_n_filter": self.top_n_filter,
            "dwg_render_quality": self.dwg_render_quality,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectConfig":
        """딕셔너리에서 생성"""
        metadata = ProjectMetadata.from_dict(data.get("metadata", {}))
        comparison = ComparisonConfig.from_dict(data.get("comparison", {}))
        color = ColorConfig.from_dict(data.get("color", {}))

        return cls(
            metadata=metadata,
            comparison=comparison,
            color=color,
            sensitivity_preset=data.get("sensitivity_preset", "normal"),
            top_n_filter=data.get("top_n_filter", 0),
            dwg_render_quality=data.get("dwg_render_quality", "auto"),
        )

    def save(self, file_path: str | Path, format: str = "auto") -> None:
        """설정 파일 저장

        Args:
            file_path: 저장할 파일 경로
            format: 파일 형식 ("json", "yaml", "auto")
                    "auto"는 확장자에서 자동 감지

        Raises:
            ValueError: 지원하지 않는 형식
            IOError: 파일 저장 실패
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 형식 결정
        if format == "auto":
            suffix = path.suffix.lower()
            if suffix in (".yaml", ".yml"):
                format = "yaml"
            else:
                format = "json"

        # 수정 시간 업데이트
        self.metadata.touch()

        # 백업 생성 (기존 파일 있으면)
        if path.exists():
            backup_path = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, backup_path)
            except Exception as e:
                logger.warning(f"백업 생성 실패: {e}")

        # 저장
        data = self.to_dict()

        try:
            with open(path, "w", encoding="utf-8") as f:
                if format == "yaml":
                    yaml.dump(
                        data, f,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                    )
                else:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"프로젝트 설정 저장 완료: {path}")

        except Exception as e:
            logger.error(f"프로젝트 설정 저장 실패: {e}")
            raise IOError(f"설정 저장 실패: {e}") from e

    @classmethod
    def load(cls, file_path: str | Path) -> "ProjectConfig":
        """설정 파일 로드

        Args:
            file_path: 로드할 파일 경로

        Returns:
            로드된 ProjectConfig 인스턴스

        Raises:
            FileNotFoundError: 파일 없음
            ValueError: 파싱 오류
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"설정 파일 없음: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                suffix = path.suffix.lower()
                if suffix in (".yaml", ".yml"):
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)

            if data is None:
                data = {}

            config = cls.from_dict(data)
            logger.info(f"프로젝트 설정 로드 완료: {path}")
            return config

        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error(f"설정 파일 파싱 오류: {e}")
            raise ValueError(f"설정 파일 파싱 오류: {e}") from e

    @classmethod
    def load_or_create(
        cls,
        file_path: str | Path,
        name: str = "Untitled Project",
    ) -> "ProjectConfig":
        """설정 파일 로드 또는 새로 생성

        파일이 없으면 기본 설정으로 새로 생성합니다.

        Args:
            file_path: 파일 경로
            name: 새로 생성 시 프로젝트 이름

        Returns:
            ProjectConfig 인스턴스
        """
        path = Path(file_path)

        if path.exists():
            try:
                return cls.load(path)
            except Exception as e:
                logger.warning(f"설정 로드 실패, 새로 생성: {e}")

        return cls.create_new(name=name)

    def apply_preset(self, preset: SensitivityPreset) -> None:
        """민감도 프리셋 적용

        Args:
            preset: 적용할 프리셋
        """
        self.comparison = ComparisonConfig.from_preset(preset)
        self.sensitivity_preset = preset.value
        self.metadata.touch()

    def set_files(self, old_path: str, new_path: str) -> None:
        """비교 파일 경로 설정

        Args:
            old_path: 이전 파일 경로
            new_path: 새 파일 경로
        """
        self.metadata.old_file_path = str(old_path)
        self.metadata.new_file_path = str(new_path)
        self.metadata.touch()

    def get_summary(self) -> Dict[str, Any]:
        """설정 요약 정보 반환"""
        return {
            "name": self.metadata.name,
            "preset": self.sensitivity_preset,
            "position_threshold": self.comparison.sensitivity.position_threshold,
            "show_added": self.color.show_added,
            "show_deleted": self.color.show_deleted,
            "show_modified": self.color.show_modified,
            "top_n_filter": self.top_n_filter,
            "dwg_render_quality": self.dwg_render_quality,
            "updated_at": self.metadata.updated_at,
        }


@dataclass
class RecentProject:
    """최근 프로젝트 항목

    Attributes:
        path: 프로젝트 설정 파일 경로
        name: 프로젝트 이름
        last_opened: 마지막 열람 시간 (ISO 형식)
        preset: 사용된 프리셋
    """

    path: str
    name: str
    last_opened: str = ""
    preset: str = "normal"

    def __post_init__(self):
        if not self.last_opened:
            self.last_opened = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "path": self.path,
            "name": self.name,
            "last_opened": self.last_opened,
            "preset": self.preset,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecentProject":
        """딕셔너리에서 생성"""
        return cls(
            path=data.get("path", ""),
            name=data.get("name", "Unknown"),
            last_opened=data.get("last_opened", ""),
            preset=data.get("preset", "normal"),
        )

    def touch(self) -> None:
        """열람 시간 업데이트"""
        self.last_opened = datetime.now().isoformat()


class RecentProjectsManager:
    """최근 프로젝트 관리자

    최근 열어본 프로젝트 목록을 관리합니다.

    Examples:
        >>> manager = RecentProjectsManager()
        >>> manager.add_project("/path/to/project.json", "My Project")
        >>> recent = manager.get_recent_projects()
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """초기화

        Args:
            config_dir: 설정 디렉토리 (기본: ~/.tekla_mcp/comparison)
        """
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.recent_file = self.config_dir / RECENT_PROJECTS_FILE
        self._projects: List[RecentProject] = []
        self._load()

    def _load(self) -> None:
        """최근 프로젝트 목록 로드"""
        if not self.recent_file.exists():
            self._projects = []
            return

        try:
            with open(self.recent_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._projects = [
                RecentProject.from_dict(item)
                for item in data.get("projects", [])
            ]

        except Exception as e:
            logger.warning(f"최근 프로젝트 로드 실패: {e}")
            self._projects = []

    def _save(self) -> None:
        """최근 프로젝트 목록 저장"""
        try:
            data = {
                "projects": [p.to_dict() for p in self._projects],
                "updated_at": datetime.now().isoformat(),
            }

            with open(self.recent_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"최근 프로젝트 저장 실패: {e}")

    def add_project(
        self,
        path: str | Path,
        name: str,
        preset: str = "normal",
    ) -> None:
        """프로젝트 추가

        Args:
            path: 프로젝트 파일 경로
            name: 프로젝트 이름
            preset: 민감도 프리셋
        """
        path_str = str(Path(path).resolve())

        # 기존 항목 제거 (중복 방지)
        self._projects = [p for p in self._projects if p.path != path_str]

        # 새 항목 추가 (맨 앞에)
        self._projects.insert(0, RecentProject(
            path=path_str,
            name=name,
            preset=preset,
        ))

        # 최대 개수 유지
        self._projects = self._projects[:MAX_RECENT_PROJECTS]

        self._save()

    def remove_project(self, path: str | Path) -> bool:
        """프로젝트 제거

        Args:
            path: 제거할 프로젝트 경로

        Returns:
            제거 성공 여부
        """
        path_str = str(Path(path).resolve())
        original_len = len(self._projects)
        self._projects = [p for p in self._projects if p.path != path_str]

        if len(self._projects) < original_len:
            self._save()
            return True
        return False

    def get_recent_projects(self, limit: int = MAX_RECENT_PROJECTS) -> List[RecentProject]:
        """최근 프로젝트 목록 반환

        Args:
            limit: 최대 반환 개수

        Returns:
            최근 프로젝트 목록
        """
        return self._projects[:limit]

    def clear(self) -> None:
        """모든 최근 프로젝트 삭제"""
        self._projects = []
        self._save()

    def update_last_opened(self, path: str | Path) -> None:
        """마지막 열람 시간 업데이트

        Args:
            path: 프로젝트 경로
        """
        path_str = str(Path(path).resolve())

        for project in self._projects:
            if project.path == path_str:
                project.touch()
                # 맨 앞으로 이동
                self._projects.remove(project)
                self._projects.insert(0, project)
                self._save()
                return

    def get_project_by_path(self, path: str | Path) -> Optional[RecentProject]:
        """경로로 프로젝트 조회

        Args:
            path: 프로젝트 경로

        Returns:
            RecentProject 또는 None
        """
        path_str = str(Path(path).resolve())
        for project in self._projects:
            if project.path == path_str:
                return project
        return None


# 전역 매니저 인스턴스
_recent_manager: Optional[RecentProjectsManager] = None


def get_recent_projects_manager() -> RecentProjectsManager:
    """전역 최근 프로젝트 관리자 반환"""
    global _recent_manager
    if _recent_manager is None:
        _recent_manager = RecentProjectsManager()
    return _recent_manager


def save_project_config(
    config: ProjectConfig,
    file_path: str | Path,
    add_to_recent: bool = True,
) -> None:
    """프로젝트 설정 저장 (헬퍼 함수)

    Args:
        config: 저장할 설정
        file_path: 저장 경로
        add_to_recent: 최근 프로젝트에 추가 여부
    """
    config.save(file_path)

    if add_to_recent:
        manager = get_recent_projects_manager()
        manager.add_project(
            file_path,
            config.metadata.name,
            config.sensitivity_preset,
        )


def load_project_config(
    file_path: str | Path,
    update_recent: bool = True,
) -> ProjectConfig:
    """프로젝트 설정 로드 (헬퍼 함수)

    Args:
        file_path: 로드할 파일 경로
        update_recent: 최근 프로젝트 업데이트 여부

    Returns:
        로드된 ProjectConfig
    """
    config = ProjectConfig.load(file_path)

    if update_recent:
        manager = get_recent_projects_manager()
        manager.update_last_opened(file_path)

    return config
