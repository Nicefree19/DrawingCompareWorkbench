# -*- coding: utf-8 -*-
"""우선순위 점수 데이터 모델

Phase 3+ 확장: 변경 우선순위 및 신뢰도 평가 시스템

핵심 기능:
- PriorityLevel: 5단계 우선순위 레벨 (CRITICAL ~ TRIVIAL)
- ReviewReason: 검토 필요 사유 열거
- ConfidenceFactors: 신뢰도 평가 요소
- PriorityScore: 통합 우선순위 점수
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class PriorityLevel(Enum):
    """변경 우선순위 레벨

    구조 변경 > 치수/그리드 > 일반 > 주석 > 무시 순서로 중요도 부여

    Attributes:
        CRITICAL (5): 구조 변경 - 즉시 검토 필수 (BEAM, COLUMN, WALL 등)
        HIGH (4): 치수/그리드 변경 - 주요 검토 대상
        MEDIUM (3): 일반 변경 - 표준 검토
        LOW (2): 주석/텍스트 변경 - 낮은 우선순위
        TRIVIAL (1): 무시 가능 - 검토 생략 가능
    """
    CRITICAL = 5
    HIGH = 4
    MEDIUM = 3
    LOW = 2
    TRIVIAL = 1

    @classmethod
    def from_string(cls, value: str) -> "PriorityLevel":
        """문자열에서 PriorityLevel 생성"""
        mapping = {
            "critical": cls.CRITICAL,
            "high": cls.HIGH,
            "medium": cls.MEDIUM,
            "low": cls.LOW,
            "trivial": cls.TRIVIAL,
        }
        return mapping.get(value.lower(), cls.MEDIUM)

    def to_korean(self) -> str:
        """한국어 레이블 반환"""
        labels = {
            PriorityLevel.CRITICAL: "긴급",
            PriorityLevel.HIGH: "높음",
            PriorityLevel.MEDIUM: "보통",
            PriorityLevel.LOW: "낮음",
            PriorityLevel.TRIVIAL: "무시",
        }
        return labels.get(self, "보통")


class ReviewReason(Enum):
    """검토 필요 사유

    변경 사항에 대해 수동 검토가 필요한 이유를 명시합니다.
    """
    # OCR 관련
    OCR_LOW_CONFIDENCE = "ocr_low_confidence"
    OCR_PARTIAL_MATCH = "ocr_partial_match"

    # 매칭 관련
    NEAR_MATCH_DETECTED = "near_match_detected"
    AMBIGUOUS_MATCH = "ambiguous_match"

    # 이미지 비교 관련
    SSIM_BOUNDARY = "ssim_boundary"
    VISUAL_DIFF_LARGE = "visual_diff_large"

    # 변경 유형 관련
    STRUCTURAL_CHANGE = "structural_change"
    DIMENSION_CHANGE = "dimension_change"
    GRID_CHANGE = "grid_change"
    LAYER_MOVED = "layer_moved"

    # 기타
    MANUAL_REVIEW_REQUESTED = "manual_review_requested"
    MULTIPLE_CHANGES = "multiple_changes"

    def to_korean(self) -> str:
        """한국어 설명 반환"""
        descriptions = {
            ReviewReason.OCR_LOW_CONFIDENCE: "OCR 신뢰도 낮음",
            ReviewReason.OCR_PARTIAL_MATCH: "OCR 부분 일치",
            ReviewReason.NEAR_MATCH_DETECTED: "근접 매칭 감지",
            ReviewReason.AMBIGUOUS_MATCH: "모호한 매칭",
            ReviewReason.SSIM_BOUNDARY: "SSIM 경계 영역",
            ReviewReason.VISUAL_DIFF_LARGE: "시각적 차이 큼",
            ReviewReason.STRUCTURAL_CHANGE: "구조 변경",
            ReviewReason.DIMENSION_CHANGE: "치수 변경",
            ReviewReason.GRID_CHANGE: "그리드 변경",
            ReviewReason.LAYER_MOVED: "레이어 이동",
            ReviewReason.MANUAL_REVIEW_REQUESTED: "수동 검토 요청됨",
            ReviewReason.MULTIPLE_CHANGES: "복합 변경",
        }
        return descriptions.get(self, self.value)


@dataclass
class ConfidenceFactors:
    """신뢰도 평가 요소

    다양한 소스의 신뢰도를 통합하여 전체 신뢰도를 계산합니다.

    Attributes:
        ocr_confidence: OCR 인식 신뢰도 (0.0 ~ 1.0)
        match_distance: near-match 거리 (mm), 낮을수록 좋음
        ssim_score: 구조적 유사도 점수 (0.0 ~ 1.0)
        layer_reliability: 레이어 신뢰도 (0.0 ~ 1.0)
        entity_count_diff: 엔티티 개수 차이 비율
    """
    ocr_confidence: float = 1.0
    match_distance: float = 0.0
    ssim_score: float = 1.0
    layer_reliability: float = 1.0
    entity_count_diff: float = 0.0

    def __post_init__(self):
        """값 범위 정규화"""
        self.ocr_confidence = max(0.0, min(1.0, self.ocr_confidence))
        self.match_distance = max(0.0, self.match_distance)
        self.ssim_score = max(0.0, min(1.0, self.ssim_score))
        self.layer_reliability = max(0.0, min(1.0, self.layer_reliability))
        self.entity_count_diff = max(0.0, self.entity_count_diff)

    def calculate_overall(self) -> float:
        """전체 신뢰도 계산 (가중 평균)

        Returns:
            float: 0.0 ~ 1.0 범위의 전체 신뢰도
        """
        # match_distance를 0~1 범위로 변환 (20mm 이상이면 0)
        distance_score = max(0.0, 1.0 - self.match_distance / 20.0)

        # 가중치 적용
        weights = {
            "ocr": 0.30,
            "distance": 0.25,
            "ssim": 0.25,
            "layer": 0.20,
        }

        overall = (
            self.ocr_confidence * weights["ocr"] +
            distance_score * weights["distance"] +
            self.ssim_score * weights["ssim"] +
            self.layer_reliability * weights["layer"]
        )

        return round(overall, 4)

    def to_dict(self) -> Dict[str, float]:
        """딕셔너리 변환"""
        return {
            "ocr_confidence": self.ocr_confidence,
            "match_distance": self.match_distance,
            "ssim_score": self.ssim_score,
            "layer_reliability": self.layer_reliability,
            "entity_count_diff": self.entity_count_diff,
            "overall": self.calculate_overall(),
        }


@dataclass
class PriorityScore:
    """통합 우선순위 점수

    변경 사항에 대한 최종 우선순위와 신뢰도를 종합합니다.

    Attributes:
        priority_level: 우선순위 레벨 (CRITICAL ~ TRIVIAL)
        priority_score: 세부 점수 (0.0 ~ 100.0)
        confidence_score: 신뢰도 점수 (0.0 ~ 1.0)
        review_needed: 수동 검토 필요 여부
        review_reasons: 검토 필요 사유 목록
        source_layer: 원본 레이어명
        change_type: 변경 유형 (ADDED, DELETED, MODIFIED)
    """
    priority_level: PriorityLevel
    priority_score: float = 50.0
    confidence_score: float = 1.0
    review_needed: bool = False
    review_reasons: List[ReviewReason] = field(default_factory=list)
    source_layer: str = ""
    change_type: str = ""

    def __post_init__(self):
        """값 범위 정규화"""
        self.priority_score = max(0.0, min(100.0, self.priority_score))
        self.confidence_score = max(0.0, min(1.0, self.confidence_score))

        # 신뢰도가 낮으면 자동으로 검토 필요 플래그 설정
        if self.confidence_score < 0.7 and not self.review_needed:
            self.review_needed = True

    @property
    def display_label(self) -> str:
        """표시용 레이블 반환"""
        icons = {
            PriorityLevel.CRITICAL: "CRITICAL",
            PriorityLevel.HIGH: "HIGH",
            PriorityLevel.MEDIUM: "MEDIUM",
            PriorityLevel.LOW: "LOW",
            PriorityLevel.TRIVIAL: "TRIVIAL",
        }
        return icons.get(self.priority_level, "UNKNOWN")

    @property
    def display_label_with_emoji(self) -> str:
        """이모지 포함 레이블 반환"""
        icons = {
            PriorityLevel.CRITICAL: "🔴 CRITICAL",
            PriorityLevel.HIGH: "🟠 HIGH",
            PriorityLevel.MEDIUM: "🟡 MEDIUM",
            PriorityLevel.LOW: "🟢 LOW",
            PriorityLevel.TRIVIAL: "⚪ TRIVIAL",
        }
        return icons.get(self.priority_level, "⚪ UNKNOWN")

    @property
    def confidence_label(self) -> str:
        """신뢰도 레이블 반환"""
        if self.confidence_score >= 0.9:
            return "높음"
        elif self.confidence_score >= 0.7:
            return "보통"
        elif self.confidence_score >= 0.5:
            return "낮음"
        else:
            return "매우 낮음"

    def get_review_reasons_korean(self) -> List[str]:
        """검토 사유 한국어 목록"""
        return [reason.to_korean() for reason in self.review_reasons]

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        result = {
            "priority_level": self.priority_level.name,
            "priority_level_value": self.priority_level.value,
            "priority_score": self.priority_score,
            "confidence_score": self.confidence_score,
            "display_label": self.display_label,
        }

        if self.review_needed:
            result["review_needed"] = True
            result["review_reasons"] = [r.value for r in self.review_reasons]
            result["review_reasons_korean"] = self.get_review_reasons_korean()

        if self.source_layer:
            result["source_layer"] = self.source_layer

        if self.change_type:
            result["change_type"] = self.change_type

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriorityScore":
        """딕셔너리에서 생성"""
        priority_level = PriorityLevel[data.get("priority_level", "MEDIUM")]

        review_reasons = []
        for reason_str in data.get("review_reasons", []):
            try:
                review_reasons.append(ReviewReason(reason_str))
            except ValueError:
                pass

        return cls(
            priority_level=priority_level,
            priority_score=data.get("priority_score", 50.0),
            confidence_score=data.get("confidence_score", 1.0),
            review_needed=data.get("review_needed", False),
            review_reasons=review_reasons,
            source_layer=data.get("source_layer", ""),
            change_type=data.get("change_type", ""),
        )

    def __lt__(self, other: "PriorityScore") -> bool:
        """우선순위 비교 (정렬용)"""
        if self.priority_level.value != other.priority_level.value:
            return self.priority_level.value > other.priority_level.value
        return self.priority_score > other.priority_score

    def __eq__(self, other: object) -> bool:
        """동등성 비교"""
        if not isinstance(other, PriorityScore):
            return False
        return (
            self.priority_level == other.priority_level and
            self.priority_score == other.priority_score
        )


# 편의 함수
def create_critical_score(
    confidence: float = 1.0,
    review_reasons: List[ReviewReason] = None,
    layer: str = "",
    change_type: str = "",
) -> PriorityScore:
    """CRITICAL 우선순위 점수 생성"""
    return PriorityScore(
        priority_level=PriorityLevel.CRITICAL,
        priority_score=100.0,
        confidence_score=confidence,
        review_needed=True,
        review_reasons=review_reasons or [ReviewReason.STRUCTURAL_CHANGE],
        source_layer=layer,
        change_type=change_type,
    )


def create_high_score(
    confidence: float = 1.0,
    review_reasons: List[ReviewReason] = None,
    layer: str = "",
    change_type: str = "",
) -> PriorityScore:
    """HIGH 우선순위 점수 생성"""
    return PriorityScore(
        priority_level=PriorityLevel.HIGH,
        priority_score=80.0,
        confidence_score=confidence,
        review_needed=confidence < 0.8,
        review_reasons=review_reasons or [],
        source_layer=layer,
        change_type=change_type,
    )


def create_medium_score(
    confidence: float = 1.0,
    review_reasons: List[ReviewReason] = None,
    layer: str = "",
    change_type: str = "",
) -> PriorityScore:
    """MEDIUM 우선순위 점수 생성"""
    return PriorityScore(
        priority_level=PriorityLevel.MEDIUM,
        priority_score=50.0,
        confidence_score=confidence,
        review_needed=confidence < 0.7,
        review_reasons=review_reasons or [],
        source_layer=layer,
        change_type=change_type,
    )
