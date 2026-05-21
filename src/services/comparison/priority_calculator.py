# -*- coding: utf-8 -*-
"""우선순위 계산기

Phase 3+ 확장: 레이어 프로파일 기반 우선순위 자동 계산

핵심 기능:
- LayerProfile: 레이어별 우선순위 프로파일
- DEFAULT_LAYER_PROFILES: 기본 레이어 프로파일 설정
- PriorityCalculator: 통합 우선순위 계산
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
import fnmatch
import logging

from .priority_score import (
    PriorityLevel,
    PriorityScore,
    ReviewReason,
    ConfidenceFactors,
)

logger = logging.getLogger(__name__)


@dataclass
class LayerProfile:
    """레이어 프로파일 설정

    특정 레이어 패턴에 대한 우선순위와 가중치를 정의합니다.

    Attributes:
        name: 프로파일 이름 (예: "structural", "dimension")
        priority: 기본 우선순위 레벨
        keywords: 레이어명에 포함된 키워드 목록
        patterns: fnmatch 스타일 패턴 목록
        weight: 점수 가중치 (기본 1.0)
        description: 프로파일 설명
    """
    name: str
    priority: PriorityLevel
    keywords: List[str] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    weight: float = 1.0
    description: str = ""

    def matches(self, layer_name: str) -> bool:
        """레이어명이 이 프로파일에 매칭되는지 확인

        Args:
            layer_name: 검사할 레이어명

        Returns:
            bool: 매칭 여부
        """
        upper_name = layer_name.upper()

        # 키워드 매칭
        for keyword in self.keywords:
            if keyword.upper() in upper_name:
                return True

        # 패턴 매칭
        for pattern in self.patterns:
            if fnmatch.fnmatch(upper_name, pattern.upper()):
                return True

        return False

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "name": self.name,
            "priority": self.priority.name,
            "keywords": self.keywords,
            "patterns": self.patterns,
            "weight": self.weight,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LayerProfile":
        """딕셔너리에서 생성"""
        return cls(
            name=data.get("name", "unknown"),
            priority=PriorityLevel[data.get("priority", "MEDIUM")],
            keywords=data.get("keywords", []),
            patterns=data.get("patterns", []),
            weight=data.get("weight", 1.0),
            description=data.get("description", ""),
        )


# 기본 레이어 프로파일 설정
DEFAULT_LAYER_PROFILES: List[LayerProfile] = [
    LayerProfile(
        name="structural",
        priority=PriorityLevel.CRITICAL,
        keywords=["BEAM", "COLUMN", "WALL", "SLAB", "FOUNDATION", "STEEL", "STRUCT"],
        patterns=["*BEAM*", "*COLUMN*", "*WALL*", "*SLAB*", "*FOUND*", "*STEEL*"],
        weight=2.0,
        description="구조 요소 레이어 (보, 기둥, 벽체, 슬래브, 기초)",
    ),
    LayerProfile(
        name="dimension",
        priority=PriorityLevel.HIGH,
        keywords=["DIM", "DIMENSION", "MEASURE", "SIZE", "ANNO"],
        patterns=["*DIM*", "*DIMENSION*", "*MEAS*"],
        weight=1.5,
        description="치수 및 측정 레이어",
    ),
    LayerProfile(
        name="grid",
        priority=PriorityLevel.HIGH,
        keywords=["GRID", "AXIS", "CENTERLINE", "CL", "BASELINE"],
        patterns=["*GRID*", "*AXIS*", "*CENTER*"],
        weight=1.5,
        description="그리드 및 축선 레이어",
    ),
    LayerProfile(
        name="annotation",
        priority=PriorityLevel.MEDIUM,
        keywords=["TEXT", "NOTE", "LABEL", "TAG", "MARK", "TITLE"],
        patterns=["*TEXT*", "*NOTE*", "*LABEL*", "*TAG*"],
        weight=1.0,
        description="주석 및 텍스트 레이어",
    ),
    LayerProfile(
        name="reference",
        priority=PriorityLevel.LOW,
        keywords=["REF", "XREF", "REFERENCE", "BACKGROUND"],
        patterns=["*REF*", "*XREF*", "*BG*"],
        weight=0.7,
        description="참조 및 배경 레이어",
    ),
    LayerProfile(
        name="ignore",
        priority=PriorityLevel.TRIVIAL,
        keywords=["DEFPOINTS", "TEMP", "HIDDEN", "OLD", "BACKUP"],
        patterns=["0", "DEFPOINTS", "*TEMP*", "*HIDDEN*", "*_OLD*", "*BACKUP*"],
        weight=0.3,
        description="무시 가능한 레이어",
    ),
]


class PriorityCalculator:
    """우선순위 계산기

    레이어 프로파일과 신뢰도 요소를 기반으로 변경 사항의
    우선순위를 자동으로 계산합니다.

    Attributes:
        profiles: 레이어 프로파일 목록
        default_priority: 매칭되지 않는 레이어의 기본 우선순위
        confidence_threshold: 신뢰도 검토 임계값

    Examples:
        >>> calculator = PriorityCalculator()
        >>> factors = ConfidenceFactors(ocr_confidence=0.8)
        >>> score = calculator.calculate("ADDED", "S-BEAM", factors)
        >>> print(score.priority_level)
        PriorityLevel.CRITICAL
    """

    def __init__(
        self,
        profiles: List[LayerProfile] = None,
        default_priority: PriorityLevel = PriorityLevel.MEDIUM,
        confidence_threshold: float = 0.7,
    ):
        """초기화

        Args:
            profiles: 사용자 정의 레이어 프로파일 목록
            default_priority: 기본 우선순위 레벨
            confidence_threshold: 검토 필요 판단 임계값
        """
        self.profiles = profiles if profiles is not None else DEFAULT_LAYER_PROFILES
        self.default_priority = default_priority
        self.confidence_threshold = confidence_threshold

        # 프로파일 캐시 (레이어명 → 프로파일)
        self._profile_cache: Dict[str, Optional[LayerProfile]] = {}

    def calculate(
        self,
        change_type: str,
        layer_name: str,
        confidence_factors: ConfidenceFactors = None,
    ) -> PriorityScore:
        """우선순위 점수 계산

        Args:
            change_type: 변경 유형 (ADDED, DELETED, MODIFIED)
            layer_name: 레이어명
            confidence_factors: 신뢰도 요소 (None이면 기본값 사용)

        Returns:
            PriorityScore: 계산된 우선순위 점수
        """
        if confidence_factors is None:
            confidence_factors = ConfidenceFactors()

        # 1. 레이어 기반 기본 우선순위
        profile = self._get_matching_profile(layer_name)
        base_priority = profile.priority if profile else self.default_priority
        weight = profile.weight if profile else 1.0

        # 2. 변경 유형에 따른 조정
        type_weight = self._get_change_type_weight(change_type)

        # 3. 신뢰도 기반 검토 필요 여부
        confidence_score = confidence_factors.calculate_overall()
        review_needed, reasons = self._evaluate_confidence(
            confidence_factors, base_priority
        )

        # 4. 최종 점수 계산
        # 기본 점수: 우선순위 레벨 × 20 (CRITICAL=100, TRIVIAL=20)
        base_score = base_priority.value * 20

        # 가중치 적용
        priority_score = base_score * type_weight * weight

        # 신뢰도가 낮으면 검토 필요 플래그
        if confidence_score < self.confidence_threshold:
            review_needed = True
            if ReviewReason.OCR_LOW_CONFIDENCE not in reasons:
                reasons.append(ReviewReason.OCR_LOW_CONFIDENCE)

        # 구조 변경이면 항상 검토 필요
        if base_priority == PriorityLevel.CRITICAL:
            review_needed = True
            if ReviewReason.STRUCTURAL_CHANGE not in reasons:
                reasons.append(ReviewReason.STRUCTURAL_CHANGE)

        return PriorityScore(
            priority_level=base_priority,
            priority_score=min(100.0, priority_score),
            confidence_score=confidence_score,
            review_needed=review_needed,
            review_reasons=reasons,
            source_layer=layer_name,
            change_type=change_type,
        )

    def calculate_batch(
        self,
        changes: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], PriorityScore]]:
        """여러 변경 사항의 우선순위 일괄 계산

        Args:
            changes: 변경 사항 딕셔너리 목록
                각 딕셔너리는 'change_type', 'layer' 키 필요

        Returns:
            List: (원본 변경사항, PriorityScore) 튜플 목록, 우선순위 순 정렬
        """
        results = []

        for change in changes:
            change_type = change.get("change_type", "MODIFIED")
            layer = change.get("layer", "")

            # 신뢰도 요소 추출
            factors = ConfidenceFactors(
                ocr_confidence=change.get("ocr_confidence", 1.0),
                match_distance=change.get("match_distance", 0.0),
                ssim_score=change.get("ssim_score", 1.0),
            )

            score = self.calculate(change_type, layer, factors)
            results.append((change, score))

        # 우선순위 순 정렬 (높은 순)
        # PriorityScore.__lt__가 이미 높은 순으로 정렬되도록 정의됨
        results.sort(key=lambda x: x[1])

        return results

    def get_top_changes(
        self,
        changes: List[Dict[str, Any]],
        top_n: int = 10,
        min_priority: PriorityLevel = PriorityLevel.LOW,
    ) -> List[Tuple[Dict[str, Any], PriorityScore]]:
        """상위 N개 중요 변경 사항 반환

        Args:
            changes: 변경 사항 목록
            top_n: 반환할 최대 개수
            min_priority: 최소 우선순위 레벨

        Returns:
            List: 상위 변경 사항 목록
        """
        all_scored = self.calculate_batch(changes)

        # 최소 우선순위 필터링
        filtered = [
            (c, s) for c, s in all_scored
            if s.priority_level.value >= min_priority.value
        ]

        return filtered[:top_n]

    def get_review_needed(
        self,
        changes: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], PriorityScore]]:
        """검토 필요 변경 사항만 반환

        Args:
            changes: 변경 사항 목록

        Returns:
            List: 검토 필요 변경 사항 목록
        """
        all_scored = self.calculate_batch(changes)
        return [(c, s) for c, s in all_scored if s.review_needed]

    def get_statistics(
        self,
        changes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """우선순위 통계 반환

        Args:
            changes: 변경 사항 목록

        Returns:
            Dict: 우선순위별 통계
        """
        all_scored = self.calculate_batch(changes)

        stats = {
            "total": len(all_scored),
            "by_priority": {level.name: 0 for level in PriorityLevel},
            "review_needed": 0,
            "avg_confidence": 0.0,
            "low_confidence_count": 0,
        }

        total_confidence = 0.0

        for _, score in all_scored:
            stats["by_priority"][score.priority_level.name] += 1
            if score.review_needed:
                stats["review_needed"] += 1
            total_confidence += score.confidence_score
            if score.confidence_score < self.confidence_threshold:
                stats["low_confidence_count"] += 1

        if all_scored:
            stats["avg_confidence"] = round(total_confidence / len(all_scored), 4)

        return stats

    def _get_matching_profile(self, layer_name: str) -> Optional[LayerProfile]:
        """레이어명에 매칭되는 프로파일 찾기 (캐시 사용)"""
        if layer_name in self._profile_cache:
            return self._profile_cache[layer_name]

        for profile in self.profiles:
            if profile.matches(layer_name):
                self._profile_cache[layer_name] = profile
                return profile

        self._profile_cache[layer_name] = None
        return None

    def _get_change_type_weight(self, change_type: str) -> float:
        """변경 유형별 가중치 반환"""
        weights = {
            "ADDED": 1.0,
            "DELETED": 1.2,  # 삭제는 더 중요
            "MODIFIED": 0.9,
        }
        return weights.get(change_type.upper(), 1.0)

    def _evaluate_confidence(
        self,
        factors: ConfidenceFactors,
        base_priority: PriorityLevel,
    ) -> Tuple[bool, List[ReviewReason]]:
        """신뢰도 기반 검토 필요 여부 평가"""
        reasons: List[ReviewReason] = []
        review_needed = False

        # OCR 신뢰도 체크
        if factors.ocr_confidence < 0.7:
            reasons.append(ReviewReason.OCR_LOW_CONFIDENCE)
            review_needed = True

        # Near-match 거리 체크
        if factors.match_distance > 5.0:
            reasons.append(ReviewReason.NEAR_MATCH_DETECTED)
            review_needed = True

        # SSIM 경계 영역 체크
        if 0.85 < factors.ssim_score < 0.95:
            reasons.append(ReviewReason.SSIM_BOUNDARY)
            review_needed = True

        # 엔티티 개수 차이 체크
        if factors.entity_count_diff > 0.1:
            reasons.append(ReviewReason.MULTIPLE_CHANGES)
            review_needed = True

        # 우선순위 기반 추가 사유
        if base_priority == PriorityLevel.CRITICAL:
            reasons.append(ReviewReason.STRUCTURAL_CHANGE)
        elif base_priority == PriorityLevel.HIGH:
            # 치수/그리드 레이어면 해당 사유 추가
            pass  # 추후 레이어명 기반 분류 추가 가능

        return review_needed, reasons

    def clear_cache(self) -> None:
        """프로파일 캐시 초기화"""
        self._profile_cache.clear()

    def add_profile(self, profile: LayerProfile) -> None:
        """프로파일 추가"""
        self.profiles.append(profile)
        self.clear_cache()

    def remove_profile(self, name: str) -> bool:
        """프로파일 제거"""
        for i, profile in enumerate(self.profiles):
            if profile.name == name:
                del self.profiles[i]
                self.clear_cache()
                return True
        return False


# 전역 기본 계산기 인스턴스
_DEFAULT_CALCULATOR: Optional[PriorityCalculator] = None


def get_default_calculator() -> PriorityCalculator:
    """전역 기본 계산기 반환 (싱글턴)"""
    global _DEFAULT_CALCULATOR
    if _DEFAULT_CALCULATOR is None:
        _DEFAULT_CALCULATOR = PriorityCalculator()
    return _DEFAULT_CALCULATOR


def calculate_priority(
    change_type: str,
    layer_name: str,
    confidence_factors: ConfidenceFactors = None,
) -> PriorityScore:
    """편의 함수: 우선순위 계산"""
    return get_default_calculator().calculate(
        change_type, layer_name, confidence_factors
    )
