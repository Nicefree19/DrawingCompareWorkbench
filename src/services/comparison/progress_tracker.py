"""진행률 추적 모듈

Sprint 9 Phase 2: ProgressTracker 클래스
DwgDiffer 등에서 중복되는 진행률 콜백 및 취소 확인 로직을 통합합니다.

기능:
    - 단계별 진행률 매핑 (0-100%)
    - 취소 확인 통합
    - 체이닝 API 지원
    - 서브 트래커 생성
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ProgressStage:
    """진행률 단계 정의

    전체 진행률의 특정 구간을 담당하는 단계를 정의합니다.

    Attributes:
        name: 단계 이름 (예: "DXF 변환", "엔티티 추출")
        start_percent: 전체 진행률에서 시작 퍼센트 (0-100)
        end_percent: 전체 진행률에서 끝 퍼센트 (0-100)
        message_prefix: 콜백 메시지 앞에 붙일 접두사

    Example:
        stage = ProgressStage("Old 파일 추출", 20, 50, "Old: ")
        # 내부 진행률 0% → 전체 20%
        # 내부 진행률 100% → 전체 50%
    """
    name: str
    start_percent: int
    end_percent: int
    message_prefix: str = ""

    def map_percent(self, internal_percent: float) -> int:
        """내부 진행률을 전체 진행률로 매핑

        Args:
            internal_percent: 현재 단계 내 진행률 (0.0 ~ 1.0 또는 0 ~ 100)

        Returns:
            전체 진행률 (0-100)
        """
        # 0-1 범위를 0-100으로 정규화
        if internal_percent <= 1.0 and internal_percent >= 0:
            # 0.0 ~ 1.0 범위로 간주
            normalized = internal_percent
        else:
            # 0 ~ 100 범위로 간주
            normalized = internal_percent / 100.0

        # 단계 범위 내에서 매핑
        range_size = self.end_percent - self.start_percent
        return int(self.start_percent + range_size * normalized)

    def get_midpoint(self) -> int:
        """단계의 중간점 반환 (total=0일 때 사용)"""
        return (self.start_percent + self.end_percent) // 2


@dataclass
class ProgressTracker:
    """진행률 추적기

    진행률 콜백과 취소 확인을 통합 관리합니다.
    체이닝 API를 통해 유연한 사용이 가능합니다.

    Attributes:
        callback: 진행률 콜백 함수 (current, total, message) -> None
        is_cancelled_fn: 취소 확인 함수 () -> bool
        total_percent: 전체 진행률 기준 (기본값 100)

    Example:
        tracker = create_tracker(progress_callback, is_cancelled)

        tracker.set_stage("변환", 0, 10).report(5, 10, "파일 변환 중...")
        if tracker.is_cancelled():
            return

        tracker.set_stage("추출", 10, 50, "Old: ").report(30, 100, "엔티티 추출")
    """
    callback: Optional[Callable[[int, int, str], None]] = None
    is_cancelled_fn: Optional[Callable[[], bool]] = None
    total_percent: int = 100

    # 내부 상태
    _current_stage: Optional[ProgressStage] = field(default=None, repr=False)
    _stages: Dict[str, ProgressStage] = field(default_factory=dict, repr=False)

    def set_stage(
        self,
        name: str,
        start_percent: int,
        end_percent: int,
        message_prefix: str = "",
    ) -> "ProgressTracker":
        """현재 진행 단계 설정 (체이닝 지원)

        Args:
            name: 단계 이름
            start_percent: 시작 퍼센트 (0-100)
            end_percent: 끝 퍼센트 (0-100)
            message_prefix: 메시지 접두사

        Returns:
            self (체이닝용)

        Example:
            tracker.set_stage("Old 파일", 20, 50, "Old: ").report(...)
        """
        stage = ProgressStage(name, start_percent, end_percent, message_prefix)
        self._current_stage = stage
        self._stages[name] = stage
        return self

    def report(
        self,
        current: int,
        total: int,
        message: str,
    ) -> bool:
        """진행률 보고 (취소 확인 포함)

        현재 단계 설정에 따라 진행률을 매핑하고 콜백을 호출합니다.
        취소되었으면 False를 반환합니다.

        Args:
            current: 현재 진행 값
            total: 전체 값 (0이면 메시지만 표시)
            message: 진행 메시지

        Returns:
            True if 계속 진행, False if 취소됨

        Example:
            if not tracker.report(50, 100, "처리 중..."):
                return  # 취소됨
        """
        # 취소 확인 먼저
        if self.is_cancelled():
            return False

        # 콜백이 없으면 바로 반환
        if self.callback is None:
            return True

        # 현재 단계 기반 퍼센트 계산
        if self._current_stage is not None:
            if total > 0:
                internal_ratio = current / total
                mapped_percent = self._current_stage.map_percent(internal_ratio)
            else:
                # total=0이면 단계 중간점 사용
                mapped_percent = self._current_stage.get_midpoint()

            # 메시지에 접두사 추가
            full_message = f"{self._current_stage.message_prefix}{message}"
        else:
            # 단계 설정 없으면 직접 사용
            mapped_percent = current if total == 0 else int(current * 100 / total)
            full_message = message

        # 콜백 호출
        self.callback(mapped_percent, self.total_percent, full_message)
        return True

    def report_simple(self, percent: int, message: str) -> bool:
        """단순 진행률 보고 (단계 매핑 없이)

        Args:
            percent: 직접 지정할 퍼센트 (0-100)
            message: 진행 메시지

        Returns:
            True if 계속 진행, False if 취소됨
        """
        if self.is_cancelled():
            return False

        if self.callback is not None:
            self.callback(percent, self.total_percent, message)

        return True

    def is_cancelled(self) -> bool:
        """취소 여부 확인

        Returns:
            True if 취소됨
        """
        if self.is_cancelled_fn is not None:
            return self.is_cancelled_fn()
        return False

    def create_sub_tracker(
        self,
        start_percent: int,
        end_percent: int,
        message_prefix: str = "",
    ) -> Callable[[int, int, str], None]:
        """서브 트래커용 콜백 함수 생성

        기존 API와 호환되는 콜백 함수를 반환합니다.
        이 콜백은 내부에서 자동으로 진행률을 매핑합니다.

        Args:
            start_percent: 서브 트래커의 시작 퍼센트
            end_percent: 서브 트래커의 끝 퍼센트
            message_prefix: 메시지 접두사

        Returns:
            (current, total, message) -> None 형태의 콜백 함수

        Example:
            # 기존 코드와 호환
            entities = extractor.extract(
                doc,
                progress_callback=tracker.create_sub_tracker(20, 50, "Old: "),
                is_cancelled=tracker.is_cancelled_fn,
            )
        """
        stage = ProgressStage("sub", start_percent, end_percent, message_prefix)

        def sub_callback(current: int, total: int, message: str) -> None:
            if self.callback is None:
                return

            if total > 0:
                mapped_percent = stage.map_percent(current / total)
            else:
                mapped_percent = stage.get_midpoint()

            full_message = f"{message_prefix}{message}"
            self.callback(mapped_percent, self.total_percent, full_message)

        return sub_callback

    def get_stage(self, name: str) -> Optional[ProgressStage]:
        """등록된 단계 조회

        Args:
            name: 단계 이름

        Returns:
            ProgressStage 또는 None
        """
        return self._stages.get(name)

    def get_current_stage(self) -> Optional[ProgressStage]:
        """현재 단계 반환"""
        return self._current_stage


def create_tracker(
    callback: Optional[Callable[[int, int, str], None]] = None,
    is_cancelled_fn: Optional[Callable[[], bool]] = None,
    total_percent: int = 100,
) -> ProgressTracker:
    """ProgressTracker 팩토리 함수

    Args:
        callback: 진행률 콜백 함수
        is_cancelled_fn: 취소 확인 함수
        total_percent: 전체 진행률 기준

    Returns:
        ProgressTracker 인스턴스

    Example:
        tracker = create_tracker(progress_callback, is_cancelled)

        if not tracker.report_simple(0, "시작..."):
            return

        tracker.set_stage("Phase1", 0, 50)
        tracker.report(25, 100, "진행 중...")
    """
    return ProgressTracker(
        callback=callback,
        is_cancelled_fn=is_cancelled_fn,
        total_percent=total_percent,
    )


# 프리셋 단계 정의 (일반적인 비교 워크플로우)
COMPARISON_STAGES = {
    "convert": ProgressStage("DXF 변환", 0, 10, ""),
    "load_old": ProgressStage("Old 파일 로드", 10, 15, "Old: "),
    "load_new": ProgressStage("New 파일 로드", 15, 20, "New: "),
    "extract_old": ProgressStage("Old 엔티티 추출", 20, 50, "Old: "),
    "extract_new": ProgressStage("New 엔티티 추출", 50, 80, "New: "),
    "compare": ProgressStage("비교 분석", 80, 95, ""),
    "finalize": ProgressStage("완료", 95, 100, ""),
}


def create_comparison_tracker(
    callback: Optional[Callable[[int, int, str], None]] = None,
    is_cancelled_fn: Optional[Callable[[], bool]] = None,
) -> ProgressTracker:
    """비교 워크플로우용 ProgressTracker 생성

    미리 정의된 비교 단계가 설정된 트래커를 반환합니다.

    Args:
        callback: 진행률 콜백 함수
        is_cancelled_fn: 취소 확인 함수

    Returns:
        프리셋 단계가 설정된 ProgressTracker

    Example:
        tracker = create_comparison_tracker(progress_callback, is_cancelled)

        tracker.set_stage("convert", 0, 10).report_simple(0, "DXF 변환 중...")
        tracker.set_stage("extract_old", 20, 50, "Old: ").report(50, 100, "추출 중...")
    """
    tracker = create_tracker(callback, is_cancelled_fn)

    # 프리셋 단계 등록
    for name, stage in COMPARISON_STAGES.items():
        tracker._stages[name] = stage

    return tracker
