# -*- coding: utf-8 -*-
"""Priority Calculator 단위 테스트

Phase 3+ 확장: LayerProfile, PriorityCalculator 테스트
"""

import pytest
from src.services.comparison.priority_score import (
    PriorityLevel,
    ReviewReason,
    ConfidenceFactors,
    PriorityScore,
)
from src.services.comparison.priority_calculator import (
    LayerProfile,
    PriorityCalculator,
    DEFAULT_LAYER_PROFILES,
    get_default_calculator,
    calculate_priority,
)


class TestLayerProfile:
    """LayerProfile 데이터클래스 테스트"""

    def test_basic_creation(self):
        """기본 생성"""
        profile = LayerProfile(
            name="test",
            priority=PriorityLevel.HIGH,
            keywords=["TEST", "SAMPLE"],
        )
        assert profile.name == "test"
        assert profile.priority == PriorityLevel.HIGH
        assert "TEST" in profile.keywords

    def test_matches_keyword(self):
        """키워드 매칭"""
        profile = LayerProfile(
            name="structural",
            priority=PriorityLevel.CRITICAL,
            keywords=["BEAM", "COLUMN"],
        )

        assert profile.matches("S-BEAM") is True
        assert profile.matches("COLUMN-1") is True
        assert profile.matches("MAIN-BEAM-A") is True
        assert profile.matches("TEXT-LAYER") is False

    def test_matches_pattern(self):
        """패턴 매칭"""
        profile = LayerProfile(
            name="dimension",
            priority=PriorityLevel.HIGH,
            patterns=["*DIM*", "DIM_*"],
        )

        assert profile.matches("DIMENSION") is True
        assert profile.matches("A-DIM-1") is True
        assert profile.matches("DIM_MAIN") is True
        assert profile.matches("TEXT") is False

    def test_matches_case_insensitive(self):
        """대소문자 구분 없는 매칭"""
        profile = LayerProfile(
            name="test",
            priority=PriorityLevel.MEDIUM,
            keywords=["BEAM"],
            patterns=["*GRID*"],
        )

        assert profile.matches("beam") is True
        assert profile.matches("Beam") is True
        assert profile.matches("BEAM") is True
        assert profile.matches("grid-line") is True

    def test_to_dict(self):
        """딕셔너리 변환"""
        profile = LayerProfile(
            name="test",
            priority=PriorityLevel.HIGH,
            keywords=["A", "B"],
            weight=1.5,
            description="테스트 프로파일",
        )
        data = profile.to_dict()

        assert data["name"] == "test"
        assert data["priority"] == "HIGH"
        assert data["keywords"] == ["A", "B"]
        assert data["weight"] == 1.5
        assert data["description"] == "테스트 프로파일"

    def test_from_dict(self):
        """딕셔너리에서 생성"""
        data = {
            "name": "custom",
            "priority": "CRITICAL",
            "keywords": ["STEEL", "METAL"],
            "patterns": ["*STRUCT*"],
            "weight": 2.0,
        }
        profile = LayerProfile.from_dict(data)

        assert profile.name == "custom"
        assert profile.priority == PriorityLevel.CRITICAL
        assert "STEEL" in profile.keywords
        assert profile.weight == 2.0


class TestDefaultLayerProfiles:
    """기본 레이어 프로파일 테스트"""

    def test_structural_profile(self):
        """구조 프로파일 존재 확인"""
        structural = next(
            (p for p in DEFAULT_LAYER_PROFILES if p.name == "structural"),
            None,
        )
        assert structural is not None
        assert structural.priority == PriorityLevel.CRITICAL
        assert "BEAM" in structural.keywords
        assert structural.weight == 2.0

    def test_dimension_profile(self):
        """치수 프로파일 존재 확인"""
        dimension = next(
            (p for p in DEFAULT_LAYER_PROFILES if p.name == "dimension"),
            None,
        )
        assert dimension is not None
        assert dimension.priority == PriorityLevel.HIGH

    def test_grid_profile(self):
        """그리드 프로파일 존재 확인"""
        grid = next(
            (p for p in DEFAULT_LAYER_PROFILES if p.name == "grid"),
            None,
        )
        assert grid is not None
        assert grid.priority == PriorityLevel.HIGH

    def test_ignore_profile(self):
        """무시 프로파일 존재 확인"""
        ignore = next(
            (p for p in DEFAULT_LAYER_PROFILES if p.name == "ignore"),
            None,
        )
        assert ignore is not None
        assert ignore.priority == PriorityLevel.TRIVIAL
        assert ignore.weight < 1.0

    def test_profile_count(self):
        """프로파일 개수 확인"""
        assert len(DEFAULT_LAYER_PROFILES) >= 5


class TestPriorityCalculator:
    """PriorityCalculator 클래스 테스트"""

    @pytest.fixture
    def calculator(self):
        """기본 계산기 fixture"""
        return PriorityCalculator()

    def test_basic_creation(self, calculator):
        """기본 생성"""
        assert calculator is not None
        assert len(calculator.profiles) > 0
        assert calculator.default_priority == PriorityLevel.MEDIUM

    def test_custom_profiles(self):
        """사용자 정의 프로파일"""
        custom_profiles = [
            LayerProfile("custom", PriorityLevel.HIGH, keywords=["CUSTOM"]),
        ]
        calc = PriorityCalculator(profiles=custom_profiles)
        assert len(calc.profiles) == 1

    def test_calculate_structural_layer(self, calculator):
        """구조 레이어 우선순위 계산"""
        score = calculator.calculate("ADDED", "S-BEAM")

        assert score.priority_level == PriorityLevel.CRITICAL
        assert score.review_needed is True
        assert ReviewReason.STRUCTURAL_CHANGE in score.review_reasons

    def test_calculate_dimension_layer(self, calculator):
        """치수 레이어 우선순위 계산"""
        score = calculator.calculate("MODIFIED", "A-DIM-1")

        assert score.priority_level == PriorityLevel.HIGH

    def test_calculate_annotation_layer(self, calculator):
        """주석 레이어 우선순위 계산"""
        score = calculator.calculate("DELETED", "TEXT-NOTES")

        assert score.priority_level == PriorityLevel.MEDIUM

    def test_calculate_ignore_layer(self, calculator):
        """무시 레이어 우선순위 계산"""
        score = calculator.calculate("ADDED", "DEFPOINTS")

        assert score.priority_level == PriorityLevel.TRIVIAL

    def test_calculate_unknown_layer(self, calculator):
        """알 수 없는 레이어 기본 우선순위"""
        score = calculator.calculate("MODIFIED", "UNKNOWN-LAYER-XYZ")

        assert score.priority_level == PriorityLevel.MEDIUM

    def test_calculate_deleted_weight(self, calculator):
        """삭제 변경 유형 가중치"""
        added = calculator.calculate("ADDED", "S-BEAM")
        deleted = calculator.calculate("DELETED", "S-BEAM")

        # 삭제가 더 높은 점수
        assert deleted.priority_score >= added.priority_score

    def test_calculate_with_low_ocr_confidence(self, calculator):
        """낮은 OCR 신뢰도"""
        factors = ConfidenceFactors(ocr_confidence=0.5)
        score = calculator.calculate("ADDED", "TEXT", factors)

        assert score.review_needed is True
        assert ReviewReason.OCR_LOW_CONFIDENCE in score.review_reasons

    def test_calculate_with_high_match_distance(self, calculator):
        """높은 매칭 거리"""
        factors = ConfidenceFactors(match_distance=10.0)
        score = calculator.calculate("MODIFIED", "BEAM", factors)

        assert score.review_needed is True
        assert ReviewReason.NEAR_MATCH_DETECTED in score.review_reasons

    def test_calculate_with_ssim_boundary(self, calculator):
        """SSIM 경계 영역"""
        factors = ConfidenceFactors(ssim_score=0.9)
        score = calculator.calculate("MODIFIED", "TEXT", factors)

        assert score.review_needed is True
        assert ReviewReason.SSIM_BOUNDARY in score.review_reasons

    def test_calculate_batch(self, calculator):
        """배치 계산"""
        changes = [
            {"change_type": "ADDED", "layer": "S-BEAM"},
            {"change_type": "DELETED", "layer": "RANDOM-LAYER"},  # MEDIUM (no match)
            {"change_type": "MODIFIED", "layer": "DEFPOINTS"},
        ]

        results = calculator.calculate_batch(changes)

        assert len(results) == 3
        # 정렬 확인: CRITICAL (BEAM) > MEDIUM (RANDOM) > TRIVIAL (DEFPOINTS)
        assert results[0][1].priority_level == PriorityLevel.CRITICAL
        assert results[2][1].priority_level == PriorityLevel.TRIVIAL

    def test_get_top_changes(self, calculator):
        """상위 변경 사항 조회"""
        changes = [
            {"change_type": "ADDED", "layer": "BEAM-1"},
            {"change_type": "ADDED", "layer": "BEAM-2"},
            {"change_type": "ADDED", "layer": "DIM-1"},  # HIGH priority
            {"change_type": "ADDED", "layer": "DIM-2"},  # HIGH priority
            {"change_type": "ADDED", "layer": "DEFPOINTS"},
        ]

        top_3 = calculator.get_top_changes(changes, top_n=3)

        assert len(top_3) == 3
        # 상위 3개는 BEAM(2개, CRITICAL) + DIM(1개, HIGH)
        assert top_3[0][1].priority_level == PriorityLevel.CRITICAL
        assert top_3[1][1].priority_level == PriorityLevel.CRITICAL

    def test_get_top_changes_with_min_priority(self, calculator):
        """상위 변경 사항 - 최소 우선순위 필터"""
        changes = [
            {"change_type": "ADDED", "layer": "BEAM"},
            {"change_type": "ADDED", "layer": "TEXT"},
            {"change_type": "ADDED", "layer": "DEFPOINTS"},
        ]

        filtered = calculator.get_top_changes(
            changes, top_n=10, min_priority=PriorityLevel.MEDIUM
        )

        # DEFPOINTS(TRIVIAL)는 제외
        assert all(
            score.priority_level.value >= PriorityLevel.MEDIUM.value
            for _, score in filtered
        )

    def test_get_review_needed(self, calculator):
        """검토 필요 변경 사항 조회"""
        changes = [
            {"change_type": "ADDED", "layer": "BEAM"},  # CRITICAL → 검토 필요
            {"change_type": "ADDED", "layer": "TEXT", "ocr_confidence": 0.5},  # 낮은 신뢰도
            {"change_type": "ADDED", "layer": "LAYER-1"},  # 일반
        ]

        review_needed = calculator.get_review_needed(changes)

        # BEAM(구조)과 TEXT(낮은 신뢰도)만 검토 필요
        assert len(review_needed) == 2

    def test_get_statistics(self, calculator):
        """통계 조회"""
        changes = [
            {"change_type": "ADDED", "layer": "BEAM"},
            {"change_type": "ADDED", "layer": "DIM"},
            {"change_type": "ADDED", "layer": "TEXT"},
            {"change_type": "ADDED", "layer": "DEFPOINTS"},
        ]

        stats = calculator.get_statistics(changes)

        assert stats["total"] == 4
        assert stats["by_priority"]["CRITICAL"] == 1
        assert stats["by_priority"]["HIGH"] == 1
        assert stats["by_priority"]["MEDIUM"] == 1
        assert stats["by_priority"]["TRIVIAL"] == 1

    def test_cache_usage(self, calculator):
        """캐시 사용 확인"""
        # 첫 번째 호출
        calculator.calculate("ADDED", "S-BEAM")
        assert "S-BEAM" in calculator._profile_cache

        # 두 번째 호출 - 캐시 사용
        calculator.calculate("DELETED", "S-BEAM")
        assert "S-BEAM" in calculator._profile_cache

    def test_clear_cache(self, calculator):
        """캐시 초기화"""
        calculator.calculate("ADDED", "S-BEAM")
        assert len(calculator._profile_cache) > 0

        calculator.clear_cache()
        assert len(calculator._profile_cache) == 0

    def test_add_profile(self, calculator):
        """프로파일 추가"""
        initial_count = len(calculator.profiles)

        new_profile = LayerProfile(
            name="new",
            priority=PriorityLevel.HIGH,
            keywords=["NEW"],
        )
        calculator.add_profile(new_profile)

        assert len(calculator.profiles) == initial_count + 1

    def test_remove_profile(self, calculator):
        """프로파일 제거"""
        # 새 프로파일 추가
        new_profile = LayerProfile(
            name="removable",
            priority=PriorityLevel.LOW,
            keywords=["REMOVE"],
        )
        calculator.add_profile(new_profile)

        # 제거
        result = calculator.remove_profile("removable")
        assert result is True

        # 존재하지 않는 프로파일 제거 시도
        result = calculator.remove_profile("nonexistent")
        assert result is False


class TestGlobalFunctions:
    """전역 함수 테스트"""

    def test_get_default_calculator(self):
        """기본 계산기 싱글턴"""
        calc1 = get_default_calculator()
        calc2 = get_default_calculator()

        assert calc1 is calc2  # 동일 인스턴스

    def test_calculate_priority(self):
        """편의 함수 테스트"""
        score = calculate_priority("ADDED", "S-COLUMN")

        assert score.priority_level == PriorityLevel.CRITICAL
        assert score.source_layer == "S-COLUMN"
        assert score.change_type == "ADDED"

    def test_calculate_priority_with_factors(self):
        """편의 함수 - 신뢰도 요소 포함"""
        factors = ConfidenceFactors(ocr_confidence=0.6)
        score = calculate_priority("MODIFIED", "TEXT", factors)

        assert score.review_needed is True


class TestIntegration:
    """통합 테스트"""

    def test_full_workflow(self):
        """전체 워크플로우"""
        calculator = PriorityCalculator()

        # 1. 여러 변경 사항 생성
        changes = [
            {"change_type": "ADDED", "layer": "COLUMN-MAIN"},  # CRITICAL (COLUMN)
            {"change_type": "DELETED", "layer": "BEAM-SEC"},   # CRITICAL (BEAM)
            {"change_type": "MODIFIED", "layer": "DIM-DETAIL", "match_distance": 8.0},  # HIGH
            {"change_type": "ADDED", "layer": "NOTE-1", "ocr_confidence": 0.4},  # MEDIUM, low conf
            {"change_type": "MODIFIED", "layer": "GRID-LINE"},  # HIGH
            {"change_type": "ADDED", "layer": "DEFPOINTS"},  # TRIVIAL
        ]

        # 2. 배치 계산
        results = calculator.calculate_batch(changes)
        assert len(results) == 6

        # 3. 상위 3개 조회
        top_3 = calculator.get_top_changes(changes, top_n=3)
        assert len(top_3) == 3
        # 첫 번째는 CRITICAL이어야 함
        assert top_3[0][1].priority_level == PriorityLevel.CRITICAL

        # 4. 검토 필요 항목 조회
        review_needed = calculator.get_review_needed(changes)
        # 최소 구조 변경(2개) + near-match(1개) + low confidence(1개)
        assert len(review_needed) >= 3

        # 5. 통계 조회
        stats = calculator.get_statistics(changes)
        assert stats["total"] == 6
        assert stats["review_needed"] >= 3

    def test_custom_profile_workflow(self):
        """사용자 정의 프로파일 워크플로우"""
        # 사용자 정의 프로파일
        custom_profiles = [
            LayerProfile(
                name="equipment",
                priority=PriorityLevel.CRITICAL,
                keywords=["EQUIP", "MACHINE", "DEVICE"],
                weight=2.5,
            ),
            LayerProfile(
                name="piping",
                priority=PriorityLevel.HIGH,
                keywords=["PIPE", "DUCT", "LINE"],
                weight=1.8,
            ),
        ]

        calculator = PriorityCalculator(profiles=custom_profiles)

        # 테스트
        equip_score = calculator.calculate("ADDED", "EQUIPMENT-MAIN")
        assert equip_score.priority_level == PriorityLevel.CRITICAL

        pipe_score = calculator.calculate("MODIFIED", "PIPE-SECTION")
        assert pipe_score.priority_level == PriorityLevel.HIGH

        # 알 수 없는 레이어는 기본 우선순위
        unknown_score = calculator.calculate("DELETED", "UNKNOWN")
        assert unknown_score.priority_level == PriorityLevel.MEDIUM


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_changes_batch(self):
        """빈 변경 사항 배치"""
        calculator = PriorityCalculator()
        results = calculator.calculate_batch([])
        assert results == []

    def test_empty_layer_name(self):
        """빈 레이어명"""
        calculator = PriorityCalculator()
        score = calculator.calculate("ADDED", "")
        assert score.priority_level == PriorityLevel.MEDIUM

    def test_special_characters_in_layer(self):
        """레이어명 특수 문자"""
        calculator = PriorityCalculator()

        # 특수 문자 포함 레이어명
        score = calculator.calculate("ADDED", "S-BEAM#1@LEVEL-2")
        assert score.source_layer == "S-BEAM#1@LEVEL-2"

    def test_unicode_layer_name(self):
        """유니코드 레이어명"""
        calculator = PriorityCalculator()

        # 한글 레이어명
        score = calculator.calculate("ADDED", "보-메인")
        assert score.source_layer == "보-메인"

    def test_very_long_layer_name(self):
        """매우 긴 레이어명"""
        calculator = PriorityCalculator()
        long_name = "A" * 500 + "-BEAM"

        score = calculator.calculate("ADDED", long_name)
        assert score.priority_level == PriorityLevel.CRITICAL

    def test_none_confidence_factors(self):
        """None 신뢰도 요소"""
        calculator = PriorityCalculator()
        score = calculator.calculate("ADDED", "BEAM", None)
        assert score.confidence_score == 1.0

    def test_statistics_empty(self):
        """빈 통계"""
        calculator = PriorityCalculator()
        stats = calculator.get_statistics([])

        assert stats["total"] == 0
        assert stats["avg_confidence"] == 0.0
