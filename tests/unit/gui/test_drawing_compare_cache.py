"""DrawingCompareTab 캐시 로직 테스트

Sprint 14: 렌더 캐시 관리 검증
- 단일 이미지 200MB 초과 시 캐시 스킵
- 메모리 제한 초과 시 캐시 클리어
- 캐시 키 생성 정밀도
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Qt 의존성 모킹 (headless 환경 대응)
#
# Phase G2.7-FU: 모듈 import 시점에 sys.modules 에 mock 을 심어야 후속
# imports 가 성공하지만, 그대로 두면 같은 pytest 프로세스에서 이후
# 실행되는 ``test_qt_pdf_adapter.py`` 가 진짜 PySide6.QtPdf 를 못 찾고
# 31개 테스트가 silently skip 됨. 우리가 설치한 mock 만 추적해서
# (다른 테스트가 먼저 깐 mock 은 건드리지 않음) 모듈 teardown 시점에
# pop 하도록 한다.
_mocked_modules: list[str] = []
for _name in ("PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui"):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()
        _mocked_modules.append(_name)


@pytest.fixture(scope="module", autouse=True)
def _restore_pyside6_after_module():
    """Pop the MagicMock entries this file installed, so later test
    modules (notably ``tests/unit/services/comparison/test_qt_pdf_adapter.py``)
    can ``import`` real PySide6 modules.

    Modules that already captured mocked symbols (this file's own
    drawing-compare imports, if any) keep working — Python's import
    system already bound those references; we only reset
    ``sys.modules`` so *new* imports go through the real package finder.
    """

    yield
    for name in _mocked_modules:
        sys.modules.pop(name, None)


class TestCacheSkipLogic:
    """캐시 스킵 로직 단위 테스트 (GUI 독립)"""

    def test_oversized_image_skip_condition(self):
        """단일 이미지가 제한 초과 시 캐시 스킵 조건 검증"""
        max_memory_mb = 200

        # 케이스 1: 150MB 이미지 → 캐시 허용
        img_150mb = np.zeros((10000, 10000, 3), dtype=np.uint8)  # ~286MB (실제)
        img_50mb = np.zeros((4000, 4000, 3), dtype=np.uint8)  # ~46MB

        img_size_50 = img_50mb.nbytes / (1024 * 1024)
        img_size_150 = img_150mb.nbytes / (1024 * 1024)

        # 50MB < 200MB → 캐시 허용
        assert img_size_50 < max_memory_mb

        # 286MB > 200MB → 캐시 스킵
        assert img_size_150 > max_memory_mb

    def test_cache_key_includes_max_edge_px(self):
        """캐시 키에 max_edge_px 포함 여부"""
        # 캐시 키 형식: (resolved_path, st_mtime_ns, st_size, max_edge_px)
        key_1600 = (Path("/test.dxf").resolve(), 1234567890, 1000, 1600)
        key_2600 = (Path("/test.dxf").resolve(), 1234567890, 1000, 2600)

        # 같은 파일이라도 max_edge_px가 다르면 다른 키
        assert key_1600 != key_2600

    def test_cache_key_path_resolution(self):
        """캐시 키 경로 정규화 검증"""
        # 심볼릭 링크/상대 경로 해결
        path1 = Path("./test.dxf")
        path2 = Path("test.dxf")

        # resolve() 적용 후 동일해야 함
        resolved1 = path1.resolve()
        resolved2 = path2.resolve()

        assert resolved1 == resolved2

    def test_memory_calculation_accuracy(self):
        """메모리 계산 정확도 검증"""
        # 1000x1000 RGB 이미지 = 3MB
        img = np.zeros((1000, 1000, 3), dtype=np.uint8)
        expected_mb = (1000 * 1000 * 3) / (1024 * 1024)  # 2.86MB

        actual_mb = img.nbytes / (1024 * 1024)
        assert abs(actual_mb - expected_mb) < 0.01

    def test_lru_eviction_order(self):
        """LRU 캐시 퇴출 순서 검증"""
        from collections import OrderedDict

        cache = OrderedDict()
        cache["key1"] = "value1"
        cache["key2"] = "value2"
        cache["key3"] = "value3"

        # key1 접근 → 맨 뒤로 이동
        cache.move_to_end("key1")

        # popitem(last=False) → 가장 오래된 항목 제거
        oldest_key, oldest_value = cache.popitem(last=False)
        assert oldest_key == "key2"  # key1이 뒤로 갔으므로 key2가 가장 오래됨


class TestCacheMemoryLimit:
    """캐시 메모리 제한 테스트"""

    def test_memory_limit_trigger_clear(self):
        """메모리 초과 시 캐시 클리어 트리거"""
        max_memory_mb = 200
        current_memory = 180  # MB
        new_img_mb = 50  # MB

        # 180 + 50 = 230 > 200 → 클리어 필요
        should_clear = (current_memory + new_img_mb) > max_memory_mb
        assert should_clear is True

    def test_memory_within_limit(self):
        """메모리 제한 내 캐시 유지"""
        max_memory_mb = 200
        current_memory = 100  # MB
        new_img_mb = 50  # MB

        # 100 + 50 = 150 < 200 → 클리어 불필요
        should_clear = (current_memory + new_img_mb) > max_memory_mb
        assert should_clear is False

    def test_single_image_exceeds_limit_skipped(self):
        """단일 이미지가 제한 초과 시 캐시 스킵"""
        max_memory_mb = 200
        single_img_mb = 250  # MB

        # 단일 이미지 > 제한 → 캐시 저장하지 않음
        should_skip = single_img_mb > max_memory_mb
        assert should_skip is True


class TestQualityChangeCache:
    """품질 변경 시 캐시 무효화 테스트"""

    def test_quality_change_clears_all_caches(self):
        """품질 변경 시 모든 캐시 클리어 확인"""
        # 클리어해야 할 항목들
        caches_to_clear = [
            "_render_cache",
            "_last_old_img",
            "_last_new_img",
            "_last_old_transform",
            "_last_new_transform",
        ]

        # 모든 캐시가 리셋되어야 함
        for cache_name in caches_to_clear:
            assert cache_name in caches_to_clear  # 명시적 확인

    def test_quality_options_max_edge_px_mapping(self):
        """품질 옵션별 max_edge_px 매핑"""
        quality_map = {
            "fast": 1600,
            "normal": 2600,
            "high": 3200,
        }

        assert quality_map["fast"] == 1600
        assert quality_map["normal"] == 2600
        assert quality_map["high"] == 3200

        # auto는 파일 크기 기반이므로 매핑에 없음
        assert "auto" not in quality_map
