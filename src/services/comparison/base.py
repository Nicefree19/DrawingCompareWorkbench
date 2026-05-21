# -*- coding: utf-8 -*-
"""
비교 모듈 기본 클래스 (Base Comparator)
=======================================

모든 비교 엔진의 공통 인터페이스를 정의합니다.

Author: TEKLA_MCP Team
Date: 2025-12-14
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """변경 유형"""

    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


@dataclass
class ChangeRecord:
    """단일 변경 레코드"""

    key: str
    change_type: ChangeType
    field_name: Optional[str] = None
    old_value: Any = None
    new_value: Any = None
    location: Optional[str] = None  # 위치 정보 (행 번호, 좌표 등)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "change_type": self.change_type.value,
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "location": self.location,
            "metadata": self.metadata,
        }


@dataclass
class ComparisonResult:
    """비교 결과"""

    source_a: str
    source_b: str
    compared_at: datetime = field(default_factory=datetime.now)

    added_count: int = 0
    deleted_count: int = 0
    modified_count: int = 0
    unchanged_count: int = 0

    changes: List[ChangeRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_changes(self) -> int:
        return self.added_count + self.deleted_count + self.modified_count

    @property
    def has_changes(self) -> bool:
        return self.total_changes > 0

    def add_change(self, change: ChangeRecord) -> None:
        """변경 레코드 추가"""
        self.changes.append(change)
        if change.change_type == ChangeType.ADDED:
            self.added_count += 1
        elif change.change_type == ChangeType.DELETED:
            self.deleted_count += 1
        elif change.change_type == ChangeType.MODIFIED:
            self.modified_count += 1
        else:
            self.unchanged_count += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_a": self.source_a,
            "source_b": self.source_b,
            "compared_at": self.compared_at.isoformat(),
            "summary": {
                "added": self.added_count,
                "deleted": self.deleted_count,
                "modified": self.modified_count,
                "unchanged": self.unchanged_count,
                "total_changes": self.total_changes,
            },
            "changes": [c.to_dict() for c in self.changes],
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


class BaseComparator(ABC):
    """비교기 추상 기본 클래스

    모든 비교 엔진(Excel, Drawing 등)이 구현해야 할 공통 인터페이스입니다.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: 비교 설정 (tolerance, ignore_columns 등)
        """
        self.config = config or {}
        self._result: Optional[ComparisonResult] = None

    @abstractmethod
    def compare(
        self,
        source_a: Union[str, Path],
        source_b: Union[str, Path],
    ) -> ComparisonResult:
        """두 소스를 비교합니다.

        Args:
            source_a: 비교 대상 A (기준, Old)
            source_b: 비교 대상 B (신규, New)

        Returns:
            ComparisonResult
        """
        pass

    @abstractmethod
    def export_report(
        self,
        output_path: Union[str, Path],
        format: str = "excel",
    ) -> Path:
        """비교 결과를 리포트로 내보냅니다.

        Args:
            output_path: 출력 경로
            format: 출력 형식 ("excel", "json", "html")

        Returns:
            생성된 파일 경로
        """
        pass

    @property
    def result(self) -> Optional[ComparisonResult]:
        """마지막 비교 결과"""
        return self._result

    def get_config(self, key: str, default: Any = None) -> Any:
        """설정 값 조회"""
        return self.config.get(key, default)
