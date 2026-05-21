# -*- coding: utf-8 -*-
"""Priority Score 단위 테스트

Phase 3+ 확장: PriorityLevel, ReviewReason, ConfidenceFactors, PriorityScore 테스트
"""

import pytest
from src.services.comparison.priority_score import (
    PriorityLevel,
    ReviewReason,
    ConfidenceFactors,
    PriorityScore,
    create_critical_score,
    create_high_score,
    create_medium_score,
)


class TestPriorityLevel:
    """PriorityLevel 열거형 테스트"""

    def test_priority_values(self):
        """우선순위 값 확인"""
        assert PriorityLevel.CRITICAL.value == 5
        assert PriorityLevel.HIGH.value == 4
        assert PriorityLevel.MEDIUM.value == 3
        assert PriorityLevel.LOW.value == 2
        assert PriorityLevel.TRIVIAL.value == 1

    def test_from_string_valid(self):
        """문자열에서 PriorityLevel 생성 - 유효한 입력"""
        assert PriorityLevel.from_string("critical") == PriorityLevel.CRITICAL
        assert PriorityLevel.from_string("HIGH") == PriorityLevel.HIGH
        assert PriorityLevel.from_string("Medium") == PriorityLevel.MEDIUM
        assert PriorityLevel.from_string("low") == PriorityLevel.LOW
        assert PriorityLevel.from_string("trivial") == PriorityLevel.TRIVIAL

    def test_from_string_invalid(self):
        """문자열에서 PriorityLevel 생성 - 유효하지 않은 입력"""
        assert PriorityLevel.from_string("unknown") == PriorityLevel.MEDIUM
        assert PriorityLevel.from_string("") == PriorityLevel.MEDIUM
        assert PriorityLevel.from_string("invalid") == PriorityLevel.MEDIUM

    def test_to_korean(self):
        """한국어 레이블 변환"""
        assert PriorityLevel.CRITICAL.to_korean() == "긴급"
        assert PriorityLevel.HIGH.to_korean() == "높음"
        assert PriorityLevel.MEDIUM.to_korean() == "보통"
        assert PriorityLevel.LOW.to_korean() == "낮음"
        assert PriorityLevel.TRIVIAL.to_korean() == "무시"


class TestReviewReason:
    """ReviewReason 열거형 테스트"""

    def test_ocr_reasons(self):
        """OCR 관련 사유"""
        assert ReviewReason.OCR_LOW_CONFIDENCE.value == "ocr_low_confidence"
        assert ReviewReason.OCR_PARTIAL_MATCH.value == "ocr_partial_match"

    def test_matching_reasons(self):
        """매칭 관련 사유"""
        assert ReviewReason.NEAR_MATCH_DETECTED.value == "near_match_detected"
        assert ReviewReason.AMBIGUOUS_MATCH.value == "ambiguous_match"

    def test_structural_reasons(self):
        """구조 변경 사유"""
        assert ReviewReason.STRUCTURAL_CHANGE.value == "structural_change"
        assert ReviewReason.DIMENSION_CHANGE.value == "dimension_change"
        assert ReviewReason.GRID_CHANGE.value == "grid_change"

    def test_to_korean(self):
        """한국어 설명 변환"""
        assert ReviewReason.OCR_LOW_CONFIDENCE.to_korean() == "OCR 신뢰도 낮음"
        assert ReviewReason.STRUCTURAL_CHANGE.to_korean() == "구조 변경"
        assert ReviewReason.NEAR_MATCH_DETECTED.to_korean() == "근접 매칭 감지"


class TestConfidenceFactors:
    """ConfidenceFactors 데이터클래스 테스트"""

    def test_default_values(self):
        """기본값 확인"""
        factors = ConfidenceFactors()
        assert factors.ocr_confidence == 1.0
        assert factors.match_distance == 0.0
        assert factors.ssim_score == 1.0
        assert factors.layer_reliability == 1.0
        assert factors.entity_count_diff == 0.0

    def test_custom_values(self):
        """사용자 정의 값"""
        factors = ConfidenceFactors(
            ocr_confidence=0.8,
            match_distance=5.0,
            ssim_score=0.9,
            layer_reliability=0.95,
        )
        assert factors.ocr_confidence == 0.8
        assert factors.match_distance == 5.0
        assert factors.ssim_score == 0.9
        assert factors.layer_reliability == 0.95

    def test_value_normalization(self):
        """값 범위 정규화"""
        # 범위를 초과하는 값
        factors = ConfidenceFactors(
            ocr_confidence=1.5,  # > 1.0
            match_distance=-5.0,  # < 0.0
            ssim_score=-0.5,  # < 0.0
        )
        assert factors.ocr_confidence == 1.0
        assert factors.match_distance == 0.0
        assert factors.ssim_score == 0.0

    def test_calculate_overall_perfect(self):
        """전체 신뢰도 계산 - 완벽한 점수"""
        factors = ConfidenceFactors()
        overall = factors.calculate_overall()
        assert overall == 1.0

    def test_calculate_overall_low_ocr(self):
        """전체 신뢰도 계산 - 낮은 OCR 신뢰도"""
        factors = ConfidenceFactors(ocr_confidence=0.5)
        overall = factors.calculate_overall()
        # 0.5 * 0.3 + 1.0 * 0.25 + 1.0 * 0.25 + 1.0 * 0.2 = 0.85
        assert overall == 0.85

    def test_calculate_overall_high_distance(self):
        """전체 신뢰도 계산 - 높은 매칭 거리"""
        factors = ConfidenceFactors(match_distance=10.0)
        overall = factors.calculate_overall()
        # distance_score = 1 - 10/20 = 0.5
        # 1.0 * 0.3 + 0.5 * 0.25 + 1.0 * 0.25 + 1.0 * 0.2 = 0.875
        assert abs(overall - 0.875) < 0.001

    def test_calculate_overall_mixed(self):
        """전체 신뢰도 계산 - 혼합"""
        factors = ConfidenceFactors(
            ocr_confidence=0.7,
            match_distance=5.0,
            ssim_score=0.85,
            layer_reliability=0.9,
        )
        overall = factors.calculate_overall()
        assert 0.7 < overall < 0.9

    def test_to_dict(self):
        """딕셔너리 변환"""
        factors = ConfidenceFactors(ocr_confidence=0.8)
        data = factors.to_dict()

        assert "ocr_confidence" in data
        assert data["ocr_confidence"] == 0.8
        assert "overall" in data


class TestPriorityScore:
    """PriorityScore 데이터클래스 테스트"""

    def test_basic_creation(self):
        """기본 생성"""
        score = PriorityScore(priority_level=PriorityLevel.MEDIUM)
        assert score.priority_level == PriorityLevel.MEDIUM
        assert score.priority_score == 50.0
        assert score.confidence_score == 1.0
        assert score.review_needed is False

    def test_critical_score(self):
        """CRITICAL 점수 생성"""
        score = PriorityScore(
            priority_level=PriorityLevel.CRITICAL,
            priority_score=100.0,
            review_needed=True,
        )
        assert score.priority_level == PriorityLevel.CRITICAL
        assert score.priority_score == 100.0
        assert score.review_needed is True

    def test_low_confidence_auto_review(self):
        """낮은 신뢰도 시 자동 검토 플래그"""
        score = PriorityScore(
            priority_level=PriorityLevel.MEDIUM,
            confidence_score=0.5,
            review_needed=False,  # 명시적 False
        )
        # 신뢰도 < 0.7 이면 자동으로 review_needed = True
        assert score.review_needed is True

    def test_value_normalization(self):
        """값 범위 정규화"""
        score = PriorityScore(
            priority_level=PriorityLevel.HIGH,
            priority_score=150.0,  # > 100
            confidence_score=1.5,  # > 1.0
        )
        assert score.priority_score == 100.0
        assert score.confidence_score == 1.0

    def test_display_label(self):
        """표시 레이블"""
        score = PriorityScore(priority_level=PriorityLevel.HIGH)
        assert score.display_label == "HIGH"

    def test_display_label_with_emoji(self):
        """이모지 포함 레이블"""
        score = PriorityScore(priority_level=PriorityLevel.CRITICAL)
        assert "CRITICAL" in score.display_label_with_emoji

    def test_confidence_label(self):
        """신뢰도 레이블"""
        high = PriorityScore(priority_level=PriorityLevel.MEDIUM, confidence_score=0.95)
        assert high.confidence_label == "높음"

        medium = PriorityScore(priority_level=PriorityLevel.MEDIUM, confidence_score=0.75)
        assert medium.confidence_label == "보통"

        low = PriorityScore(priority_level=PriorityLevel.MEDIUM, confidence_score=0.55)
        assert low.confidence_label == "낮음"

        very_low = PriorityScore(priority_level=PriorityLevel.MEDIUM, confidence_score=0.3)
        assert very_low.confidence_label == "매우 낮음"

    def test_review_reasons_korean(self):
        """검토 사유 한국어 목록"""
        score = PriorityScore(
            priority_level=PriorityLevel.CRITICAL,
            review_needed=True,
            review_reasons=[
                ReviewReason.STRUCTURAL_CHANGE,
                ReviewReason.OCR_LOW_CONFIDENCE,
            ],
        )
        korean_reasons = score.get_review_reasons_korean()
        assert "구조 변경" in korean_reasons
        assert "OCR 신뢰도 낮음" in korean_reasons

    def test_to_dict_basic(self):
        """딕셔너리 변환 - 기본"""
        score = PriorityScore(priority_level=PriorityLevel.MEDIUM)
        data = score.to_dict()

        assert data["priority_level"] == "MEDIUM"
        assert data["priority_level_value"] == 3
        assert data["priority_score"] == 50.0
        assert data["display_label"] == "MEDIUM"

    def test_to_dict_with_review(self):
        """딕셔너리 변환 - 검토 필요"""
        score = PriorityScore(
            priority_level=PriorityLevel.HIGH,
            review_needed=True,
            review_reasons=[ReviewReason.DIMENSION_CHANGE],
        )
        data = score.to_dict()

        assert data["review_needed"] is True
        assert "review_reasons" in data
        assert "dimension_change" in data["review_reasons"]

    def test_from_dict(self):
        """딕셔너리에서 생성"""
        data = {
            "priority_level": "HIGH",
            "priority_score": 80.0,
            "confidence_score": 0.9,
            "review_needed": True,
            "review_reasons": ["structural_change"],
        }
        score = PriorityScore.from_dict(data)

        assert score.priority_level == PriorityLevel.HIGH
        assert score.priority_score == 80.0
        assert score.confidence_score == 0.9
        assert score.review_needed is True

    def test_comparison_lt(self):
        """비교 연산 - 작음"""
        critical = PriorityScore(priority_level=PriorityLevel.CRITICAL)
        high = PriorityScore(priority_level=PriorityLevel.HIGH)
        low = PriorityScore(priority_level=PriorityLevel.LOW)

        # 높은 우선순위가 "작음" (정렬 시 앞으로)
        assert critical < high  # CRITICAL이 앞으로
        assert high < low  # HIGH가 앞으로

    def test_comparison_eq(self):
        """비교 연산 - 같음"""
        score1 = PriorityScore(priority_level=PriorityLevel.MEDIUM, priority_score=50.0)
        score2 = PriorityScore(priority_level=PriorityLevel.MEDIUM, priority_score=50.0)
        score3 = PriorityScore(priority_level=PriorityLevel.HIGH, priority_score=50.0)

        assert score1 == score2
        assert score1 != score3

    def test_sorting(self):
        """정렬 테스트"""
        scores = [
            PriorityScore(priority_level=PriorityLevel.LOW),
            PriorityScore(priority_level=PriorityLevel.CRITICAL),
            PriorityScore(priority_level=PriorityLevel.MEDIUM),
        ]
        sorted_scores = sorted(scores)

        assert sorted_scores[0].priority_level == PriorityLevel.CRITICAL
        assert sorted_scores[1].priority_level == PriorityLevel.MEDIUM
        assert sorted_scores[2].priority_level == PriorityLevel.LOW


class TestFactoryFunctions:
    """편의 함수 테스트"""

    def test_create_critical_score(self):
        """CRITICAL 점수 생성 함수"""
        score = create_critical_score(
            confidence=0.9,
            layer="S-BEAM",
            change_type="ADDED",
        )
        assert score.priority_level == PriorityLevel.CRITICAL
        assert score.priority_score == 100.0
        assert score.review_needed is True
        assert score.source_layer == "S-BEAM"
        assert ReviewReason.STRUCTURAL_CHANGE in score.review_reasons

    def test_create_high_score(self):
        """HIGH 점수 생성 함수"""
        score = create_high_score(
            confidence=0.85,
            layer="DIM",
            change_type="MODIFIED",
        )
        assert score.priority_level == PriorityLevel.HIGH
        assert score.priority_score == 80.0
        assert score.source_layer == "DIM"

    def test_create_medium_score(self):
        """MEDIUM 점수 생성 함수"""
        score = create_medium_score(
            confidence=0.75,
            layer="TEXT",
            change_type="DELETED",
        )
        assert score.priority_level == PriorityLevel.MEDIUM
        assert score.priority_score == 50.0

    def test_create_high_score_low_confidence_review(self):
        """HIGH 점수 - 낮은 신뢰도 시 검토 필요"""
        score = create_high_score(confidence=0.6)
        assert score.review_needed is True

    def test_create_medium_score_low_confidence_review(self):
        """MEDIUM 점수 - 낮은 신뢰도 시 검토 필요"""
        score = create_medium_score(confidence=0.5)
        assert score.review_needed is True


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_review_reasons(self):
        """빈 검토 사유 목록"""
        score = PriorityScore(
            priority_level=PriorityLevel.MEDIUM,
            review_reasons=[],
        )
        assert score.get_review_reasons_korean() == []

    def test_from_dict_missing_fields(self):
        """딕셔너리 - 필드 누락"""
        data = {"priority_level": "LOW"}
        score = PriorityScore.from_dict(data)

        assert score.priority_level == PriorityLevel.LOW
        assert score.priority_score == 50.0  # 기본값
        assert score.confidence_score == 1.0  # 기본값

    def test_from_dict_invalid_review_reason(self):
        """딕셔너리 - 유효하지 않은 검토 사유"""
        data = {
            "priority_level": "MEDIUM",
            "review_reasons": ["invalid_reason", "structural_change"],
        }
        score = PriorityScore.from_dict(data)

        # invalid_reason은 무시되고 structural_change만 포함
        assert len(score.review_reasons) == 1
        assert ReviewReason.STRUCTURAL_CHANGE in score.review_reasons

    def test_comparison_with_non_priority_score(self):
        """다른 타입과 비교"""
        score = PriorityScore(priority_level=PriorityLevel.MEDIUM)
        assert score != "not a score"
        assert score != 50
        assert score != None
