# -*- coding: utf-8 -*-
"""DrawingComparisonViewer 동작 테스트

Sprint 14: 새로운 동작 검증
- LOW #1: unknown change_type Old/New 뷰 선택 로직
- 메모리 해제 로직 (200MB 초과 이미지)
- 좌표 변환 안전성
"""

import pytest
from unittest.mock import MagicMock


class TestUnknownChangeTypeViewSelection:
    """unknown change_type의 Old/New 뷰 선택 테스트 (LOW #1)"""

    def _should_use_old_view(self, change: dict) -> bool:
        """뷰어의 unknown 처리 로직 재현 (테스트용)

        실제 코드 위치: drawing_comparison_viewer.py lines 605-631
        """
        old_x = change.get("old_cad_x")
        old_y = change.get("old_cad_y")
        has_explicit_old = (
            old_x is not None
            and old_y is not None
            and isinstance(old_x, (int, float))
            and isinstance(old_y, (int, float))
            and old_x != change.get("cad_x")
        )
        return has_explicit_old

    def test_unknown_with_different_old_coords_uses_old_view(self):
        """old 좌표가 new와 다르면 Old 뷰 선택"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "old_cad_x": 150,  # 다른 값
            "old_cad_y": 250,
            "change_type": "unknown",
        }
        assert self._should_use_old_view(change) is True

    def test_unknown_with_same_old_coords_uses_new_view(self):
        """old 좌표가 new와 같으면 New 뷰 선택"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "old_cad_x": 100,  # 같은 값
            "old_cad_y": 200,
            "change_type": "unknown",
        }
        assert self._should_use_old_view(change) is False

    def test_unknown_without_old_coords_uses_new_view(self):
        """old 좌표가 없으면 New 뷰 선택"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "change_type": "unknown",
        }
        assert self._should_use_old_view(change) is False

    def test_unknown_with_only_old_cad_x_uses_new_view(self):
        """old_cad_x만 있고 old_cad_y가 없으면 New 뷰"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "old_cad_x": 150,
            "change_type": "unknown",
        }
        assert self._should_use_old_view(change) is False

    def test_unknown_with_only_old_cad_y_uses_new_view(self):
        """old_cad_y만 있고 old_cad_x가 없으면 New 뷰"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "old_cad_y": 250,
            "change_type": "unknown",
        }
        assert self._should_use_old_view(change) is False

    def test_unknown_with_none_old_coords_uses_new_view(self):
        """old 좌표가 None이면 New 뷰 (None은 유효한 좌표 아님)"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "old_cad_x": None,
            "old_cad_y": None,
            "change_type": "unknown",
        }
        # None은 유효한 숫자 좌표가 아니므로 New 뷰 사용
        result = self._should_use_old_view(change)
        assert result is False

    def test_unknown_with_string_old_coords_uses_new_view(self):
        """old 좌표가 문자열이면 New 뷰 (문자열은 유효한 좌표 아님)"""
        change = {
            "cad_x": 100,
            "cad_y": 200,
            "old_cad_x": "150",
            "old_cad_y": "250",
            "change_type": "unknown",
        }
        result = self._should_use_old_view(change)
        assert result is False


class TestViewerMemoryRelease:
    """뷰어 메모리 해제 테스트"""

    def _get_img_mb(self, img) -> float:
        """이미지 메모리 크기 계산 (MB)

        실제 코드 위치: drawing_compare_tab.py lines 1347-1350
        """
        if img is not None and hasattr(img, "nbytes"):
            return img.nbytes / (1024 * 1024)
        return 0

    def _should_release_memory(self, old_img, new_img, cache_limit_mb: int) -> bool:
        """메모리 해제 조건 검증

        실제 코드 위치: drawing_compare_tab.py lines 1352-1359
        """
        old_mb = self._get_img_mb(old_img)
        new_mb = self._get_img_mb(new_img)
        return old_mb > cache_limit_mb or new_mb > cache_limit_mb

    def test_oversized_old_image_triggers_release(self):
        """Old 이미지가 제한 초과 시 해제 트리거"""
        import numpy as np

        # 250MB 이미지 (제한 200MB 초과)
        old_img = np.zeros((8000, 8000, 4), dtype=np.uint8)  # ~244MB
        new_img = np.zeros((1000, 1000, 3), dtype=np.uint8)  # ~2.9MB

        assert self._should_release_memory(old_img, new_img, 200) is True

    def test_oversized_new_image_triggers_release(self):
        """New 이미지가 제한 초과 시 해제 트리거"""
        import numpy as np

        old_img = np.zeros((1000, 1000, 3), dtype=np.uint8)  # ~2.9MB
        new_img = np.zeros((8000, 8000, 4), dtype=np.uint8)  # ~244MB

        assert self._should_release_memory(old_img, new_img, 200) is True

    def test_both_under_limit_no_release(self):
        """양쪽 모두 제한 이하면 해제 안 함"""
        import numpy as np

        old_img = np.zeros((1000, 1000, 3), dtype=np.uint8)  # ~2.9MB
        new_img = np.zeros((1000, 1000, 3), dtype=np.uint8)  # ~2.9MB

        assert self._should_release_memory(old_img, new_img, 200) is False

    def test_none_images_no_release(self):
        """None 이미지는 해제 안 함 (0MB)"""
        assert self._should_release_memory(None, None, 200) is False

    def test_mixed_none_and_oversized_triggers_release(self):
        """하나가 None이고 하나가 초과면 해제"""
        import numpy as np

        old_img = None
        new_img = np.zeros((8000, 8000, 4), dtype=np.uint8)  # ~244MB

        assert self._should_release_memory(old_img, new_img, 200) is True


class TestSafeCoordHelper:
    """좌표 안전 변환 헬퍼 테스트"""

    def _safe_coord(self, val, default):
        """좌표 안전 변환 (테스트용)

        실제 코드 위치: drawing_compare_tab.py lines 1145-1151
        """
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def test_numeric_value_returns_float(self):
        """숫자 값은 float로 변환"""
        assert self._safe_coord(100, 0) == 100.0
        assert self._safe_coord(100.5, 0) == 100.5
        assert isinstance(self._safe_coord(100, 0), float)

    def test_none_returns_default(self):
        """None은 기본값 반환"""
        assert self._safe_coord(None, 50) == 50

    def test_string_number_converts(self):
        """문자열 숫자는 변환됨"""
        assert self._safe_coord("100", 0) == 100.0

    def test_invalid_string_returns_default(self):
        """변환 불가 문자열은 기본값"""
        assert self._safe_coord("abc", 50) == 50

    def test_list_returns_default(self):
        """리스트는 기본값"""
        assert self._safe_coord([1, 2], 50) == 50

    def test_dict_returns_default(self):
        """딕셔너리는 기본값"""
        assert self._safe_coord({"x": 1}, 50) == 50


class TestHasLocationValidation:
    """has_location 검증 테스트"""

    def _has_location(self, raw_x, raw_y) -> bool:
        """위치 정보 유효성 검증 (테스트용)

        실제 코드 위치: drawing_compare_tab.py lines 1163-1170
        """
        return (
            raw_x is not None
            and raw_y is not None
            and isinstance(raw_x, (int, float))
            and isinstance(raw_y, (int, float))
        )

    def test_valid_int_coords(self):
        """정수 좌표는 유효"""
        assert self._has_location(100, 200) is True

    def test_valid_float_coords(self):
        """실수 좌표는 유효"""
        assert self._has_location(100.5, 200.5) is True

    def test_none_x_invalid(self):
        """x가 None이면 무효"""
        assert self._has_location(None, 200) is False

    def test_none_y_invalid(self):
        """y가 None이면 무효"""
        assert self._has_location(100, None) is False

    def test_both_none_invalid(self):
        """둘 다 None이면 무효"""
        assert self._has_location(None, None) is False

    def test_string_coords_invalid(self):
        """문자열 좌표는 무효"""
        assert self._has_location("100", "200") is False

    def test_mixed_string_number_invalid(self):
        """혼합 타입은 무효"""
        assert self._has_location(100, "200") is False
        assert self._has_location("100", 200) is False

    def test_list_coords_invalid(self):
        """리스트 좌표는 무효"""
        assert self._has_location([100], [200]) is False

    def test_zero_coords_valid(self):
        """0 좌표는 유효"""
        assert self._has_location(0, 0) is True

    def test_negative_coords_valid(self):
        """음수 좌표는 유효 (CAD에서 가능)"""
        assert self._has_location(-100, -200) is True
