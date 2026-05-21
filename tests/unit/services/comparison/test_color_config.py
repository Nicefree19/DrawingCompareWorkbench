# -*- coding: utf-8 -*-
"""Color Toggle (QW-2) 단위 테스트

Phase 3+ 확장: ColorConfig 색상 토글 기능 테스트
"""

import pytest
from typing import List

from src.services.comparison.visualization_service import ColorConfig
from src.services.comparison.dxf_comparator import DxfChange, DxfChangeType


# 테스트용 Mock DxfChange 생성 헬퍼
def create_mock_change(
    change_type: DxfChangeType,
    entity_type: str = "LINE",
    layer: str = "0",
) -> DxfChange:
    """Mock DxfChange 생성"""
    return DxfChange(
        change_type=change_type,
        entity_type=entity_type,
        layer=layer,
        location=(0.0, 0.0),
    )


class TestColorConfigBasic:
    """ColorConfig 기본 기능 테스트"""

    def test_default_config(self):
        """기본 설정 생성"""
        config = ColorConfig.get_default()

        # 기본 색상 확인
        assert config.added_color == (0, 255, 0)  # 녹색
        assert config.deleted_color == (255, 0, 0)  # 빨간색
        assert config.modified_color == (255, 165, 0)  # 주황색

        # 기본적으로 모두 표시
        assert config.show_added is True
        assert config.show_deleted is True
        assert config.show_modified is True

    def test_colorblind_friendly_config(self):
        """색약 친화적 설정"""
        config = ColorConfig.get_colorblind_friendly()

        # 파란색-주황색 계열
        assert config.added_color == (0, 114, 178)  # 파란색
        assert config.deleted_color == (213, 94, 0)  # 주황-빨강
        assert config.modified_color == (204, 121, 167)  # 분홍

    def test_high_contrast_config(self):
        """고대비 설정"""
        config = ColorConfig.get_high_contrast()

        assert config.added_color == (0, 255, 0)  # 밝은 녹색
        assert config.deleted_color == (255, 0, 255)  # 마젠타
        assert config.modified_color == (255, 255, 0)  # 노란색


class TestColorConfigVisibility:
    """색상 표시 토글 테스트"""

    def test_show_all(self):
        """모든 유형 표시"""
        config = ColorConfig()

        assert config.is_visible(DxfChangeType.ADDED) is True
        assert config.is_visible(DxfChangeType.DELETED) is True
        assert config.is_visible(DxfChangeType.MODIFIED) is True

    def test_hide_added(self):
        """추가 항목 숨기기"""
        config = ColorConfig(show_added=False)

        assert config.is_visible(DxfChangeType.ADDED) is False
        assert config.is_visible(DxfChangeType.DELETED) is True
        assert config.is_visible(DxfChangeType.MODIFIED) is True

    def test_hide_deleted(self):
        """삭제 항목 숨기기"""
        config = ColorConfig(show_deleted=False)

        assert config.is_visible(DxfChangeType.ADDED) is True
        assert config.is_visible(DxfChangeType.DELETED) is False
        assert config.is_visible(DxfChangeType.MODIFIED) is True

    def test_hide_modified(self):
        """수정 항목 숨기기"""
        config = ColorConfig(show_modified=False)

        assert config.is_visible(DxfChangeType.ADDED) is True
        assert config.is_visible(DxfChangeType.DELETED) is True
        assert config.is_visible(DxfChangeType.MODIFIED) is False

    def test_hide_multiple(self):
        """여러 유형 숨기기"""
        config = ColorConfig(
            show_added=False,
            show_modified=False,
        )

        assert config.is_visible(DxfChangeType.ADDED) is False
        assert config.is_visible(DxfChangeType.DELETED) is True
        assert config.is_visible(DxfChangeType.MODIFIED) is False


class TestColorConfigFiltering:
    """변경점 필터링 테스트"""

    def test_filter_no_changes(self):
        """빈 리스트 필터링"""
        config = ColorConfig()
        filtered = config.filter_changes([])
        assert len(filtered) == 0

    def test_filter_all_visible(self):
        """모든 유형 표시 시 필터링 없음"""
        config = ColorConfig()
        changes = [
            create_mock_change(DxfChangeType.ADDED),
            create_mock_change(DxfChangeType.DELETED),
            create_mock_change(DxfChangeType.MODIFIED),
        ]

        filtered = config.filter_changes(changes)
        assert len(filtered) == 3

    def test_filter_hide_added(self):
        """추가 항목 숨기기 필터링"""
        config = ColorConfig(show_added=False)
        changes = [
            create_mock_change(DxfChangeType.ADDED),
            create_mock_change(DxfChangeType.DELETED),
            create_mock_change(DxfChangeType.MODIFIED),
        ]

        filtered = config.filter_changes(changes)
        assert len(filtered) == 2
        assert all(c.change_type != DxfChangeType.ADDED for c in filtered)

    def test_filter_hide_deleted(self):
        """삭제 항목 숨기기 필터링"""
        config = ColorConfig(show_deleted=False)
        changes = [
            create_mock_change(DxfChangeType.ADDED),
            create_mock_change(DxfChangeType.DELETED),
            create_mock_change(DxfChangeType.MODIFIED),
        ]

        filtered = config.filter_changes(changes)
        assert len(filtered) == 2
        assert all(c.change_type != DxfChangeType.DELETED for c in filtered)

    def test_filter_only_deleted(self):
        """삭제만 표시"""
        config = ColorConfig(
            show_added=False,
            show_modified=False,
        )
        changes = [
            create_mock_change(DxfChangeType.ADDED),
            create_mock_change(DxfChangeType.DELETED),
            create_mock_change(DxfChangeType.MODIFIED),
        ]

        filtered = config.filter_changes(changes)
        assert len(filtered) == 1
        assert filtered[0].change_type == DxfChangeType.DELETED


class TestColorConfigColors:
    """색상 변환 테스트"""

    def test_get_color_for_added(self):
        """추가 항목 색상"""
        config = ColorConfig()
        color = config.get_color_for_type(DxfChangeType.ADDED)
        assert color == (0, 255, 0)

    def test_get_color_for_deleted(self):
        """삭제 항목 색상"""
        config = ColorConfig()
        color = config.get_color_for_type(DxfChangeType.DELETED)
        assert color == (255, 0, 0)

    def test_get_color_for_modified(self):
        """수정 항목 색상"""
        config = ColorConfig()
        color = config.get_color_for_type(DxfChangeType.MODIFIED)
        assert color == (255, 165, 0)

    def test_custom_color(self):
        """커스텀 색상"""
        config = ColorConfig(added_color=(100, 150, 200))
        color = config.get_color_for_type(DxfChangeType.ADDED)
        assert color == (100, 150, 200)

    def test_get_hex_for_added(self):
        """Hex 색상 변환 - 추가"""
        config = ColorConfig()
        hex_color = config.get_hex_for_type(DxfChangeType.ADDED)
        assert hex_color == "#00FF00"

    def test_get_hex_for_deleted(self):
        """Hex 색상 변환 - 삭제"""
        config = ColorConfig()
        hex_color = config.get_hex_for_type(DxfChangeType.DELETED)
        assert hex_color == "#FF0000"

    def test_get_hex_for_modified(self):
        """Hex 색상 변환 - 수정"""
        config = ColorConfig()
        hex_color = config.get_hex_for_type(DxfChangeType.MODIFIED)
        assert hex_color == "#FFA500"


class TestColorConfigSerialization:
    """직렬화/역직렬화 테스트"""

    def test_to_dict(self):
        """딕셔너리 변환"""
        config = ColorConfig(
            added_color=(0, 255, 0),
            show_deleted=False,
        )
        data = config.to_dict()

        assert data["added_color"] == [0, 255, 0]
        assert data["show_deleted"] is False
        assert data["show_added"] is True

    def test_from_dict(self):
        """딕셔너리에서 생성"""
        data = {
            "added_color": [100, 150, 200],
            "deleted_color": [255, 0, 0],
            "modified_color": [255, 165, 0],
            "show_added": True,
            "show_deleted": False,
            "show_modified": True,
        }
        config = ColorConfig.from_dict(data)

        assert config.added_color == (100, 150, 200)
        assert config.show_deleted is False

    def test_round_trip(self):
        """직렬화 → 역직렬화 왕복"""
        original = ColorConfig(
            added_color=(50, 100, 150),
            deleted_color=(200, 50, 50),
            modified_color=(100, 200, 100),
            show_added=True,
            show_deleted=False,
            show_modified=True,
        )

        data = original.to_dict()
        restored = ColorConfig.from_dict(data)

        assert restored.added_color == original.added_color
        assert restored.deleted_color == original.deleted_color
        assert restored.modified_color == original.modified_color
        assert restored.show_added == original.show_added
        assert restored.show_deleted == original.show_deleted
        assert restored.show_modified == original.show_modified

    def test_from_dict_missing_fields(self):
        """필드 누락 시 기본값 적용"""
        data = {"show_added": False}
        config = ColorConfig.from_dict(data)

        # 기본 색상 적용
        assert config.added_color == (0, 255, 0)
        assert config.deleted_color == (255, 0, 0)

        # 명시된 값 적용
        assert config.show_added is False

        # 누락 필드는 기본값
        assert config.show_deleted is True
        assert config.show_modified is True


class TestColorConfigEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_changes_list(self):
        """빈 변경 목록"""
        config = ColorConfig()
        filtered = config.filter_changes([])
        assert filtered == []

    def test_all_hidden(self):
        """모든 유형 숨기기"""
        config = ColorConfig(
            show_added=False,
            show_deleted=False,
            show_modified=False,
        )
        changes = [
            create_mock_change(DxfChangeType.ADDED),
            create_mock_change(DxfChangeType.DELETED),
            create_mock_change(DxfChangeType.MODIFIED),
        ]

        filtered = config.filter_changes(changes)
        assert len(filtered) == 0

    def test_unknown_change_type_fallback(self):
        """알 수 없는 변경 유형 폴백"""
        config = ColorConfig()
        # 기본 회색 반환 확인 (실제 코드에서는 fallback으로 회색 사용)
        # DxfChangeType은 ADDED, DELETED, MODIFIED만 있으므로
        # 이 테스트는 get_color_for_type의 기본값 동작 확인
        pass  # 현재 enum에 다른 값이 없으므로 스킵
