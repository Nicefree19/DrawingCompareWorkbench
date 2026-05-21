"""ProgressTracker 단위 테스트

Sprint 9 Phase 2: P1-1 ProgressTracker 클래스 테스트
"""

import pytest

from src.services.comparison.progress_tracker import (
    COMPARISON_STAGES,
    ProgressStage,
    ProgressTracker,
    create_comparison_tracker,
    create_tracker,
)


class TestProgressStage:
    """ProgressStage 데이터클래스 테스트"""

    def test_basic_creation(self):
        """기본 생성 테스트"""
        stage = ProgressStage("테스트", 20, 50, "Prefix: ")

        assert stage.name == "테스트"
        assert stage.start_percent == 20
        assert stage.end_percent == 50
        assert stage.message_prefix == "Prefix: "

    def test_default_prefix(self):
        """기본 접두사 (빈 문자열) 테스트"""
        stage = ProgressStage("테스트", 0, 100)

        assert stage.message_prefix == ""

    def test_map_percent_ratio_zero(self):
        """내부 진행률 0% 매핑 테스트"""
        stage = ProgressStage("테스트", 20, 50)

        # 0% → 시작 퍼센트
        assert stage.map_percent(0.0) == 20

    def test_map_percent_ratio_half(self):
        """내부 진행률 50% 매핑 테스트"""
        stage = ProgressStage("테스트", 20, 50)

        # 50% → 중간 (20 + 30*0.5 = 35)
        assert stage.map_percent(0.5) == 35

    def test_map_percent_ratio_full(self):
        """내부 진행률 100% 매핑 테스트"""
        stage = ProgressStage("테스트", 20, 50)

        # 100% → 끝 퍼센트
        assert stage.map_percent(1.0) == 50

    def test_map_percent_integer_range(self):
        """정수 범위 (0-100) 매핑 테스트"""
        stage = ProgressStage("테스트", 20, 50)

        # 50 → 0.5 → 35%
        assert stage.map_percent(50) == 35

        # 100 → 1.0 → 50%
        assert stage.map_percent(100) == 50

    def test_get_midpoint(self):
        """중간점 계산 테스트"""
        stage = ProgressStage("테스트", 20, 50)

        assert stage.get_midpoint() == 35

    def test_get_midpoint_odd_range(self):
        """홀수 범위 중간점 테스트 (정수 나눗셈)"""
        stage = ProgressStage("테스트", 20, 51)

        # (20 + 51) // 2 = 35
        assert stage.get_midpoint() == 35


class TestProgressTracker:
    """ProgressTracker 클래스 테스트"""

    def test_basic_creation(self):
        """기본 생성 테스트"""
        tracker = ProgressTracker()

        assert tracker.callback is None
        assert tracker.is_cancelled_fn is None
        assert tracker.total_percent == 100
        assert tracker._current_stage is None
        assert tracker._stages == {}

    def test_create_with_callback(self):
        """콜백과 함께 생성 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        tracker.report_simple(50, "테스트")

        assert len(calls) == 1
        assert calls[0] == (50, 100, "테스트")

    def test_set_stage_chaining(self):
        """set_stage() 체이닝 테스트"""
        tracker = ProgressTracker()

        result = tracker.set_stage("단계1", 0, 50)

        # 체이닝 지원
        assert result is tracker
        assert tracker._current_stage is not None
        assert tracker._current_stage.name == "단계1"
        assert "단계1" in tracker._stages

    def test_set_stage_multiple(self):
        """여러 단계 설정 테스트"""
        tracker = ProgressTracker()

        tracker.set_stage("단계1", 0, 50)
        tracker.set_stage("단계2", 50, 100)

        assert len(tracker._stages) == 2
        assert tracker._current_stage.name == "단계2"

    def test_report_mapping(self):
        """report() 진행률 매핑 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        tracker.set_stage("테스트", 20, 50, "Prefix: ")

        # 내부 50% → 전체 35%
        tracker.report(50, 100, "진행 중")

        assert len(calls) == 1
        assert calls[0][0] == 35  # 매핑된 퍼센트
        assert calls[0][1] == 100
        assert calls[0][2] == "Prefix: 진행 중"

    def test_report_zero_total(self):
        """report() total=0일 때 중간점 사용 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        tracker.set_stage("테스트", 20, 50, "Prefix: ")

        tracker.report(0, 0, "메시지")

        assert len(calls) == 1
        assert calls[0][0] == 35  # 중간점

    def test_report_cancelled(self):
        """report() 취소 시 False 반환 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        def is_cancelled():
            return True

        tracker = ProgressTracker(callback=callback, is_cancelled_fn=is_cancelled)

        result = tracker.report(50, 100, "테스트")

        # 취소되면 False 반환, 콜백 호출 안 됨
        assert result is False
        assert len(calls) == 0

    def test_report_not_cancelled(self):
        """report() 취소 안 됐을 때 True 반환 테스트"""
        def is_cancelled():
            return False

        tracker = ProgressTracker(is_cancelled_fn=is_cancelled)

        result = tracker.report(50, 100, "테스트")

        assert result is True

    def test_report_no_stage(self):
        """report() 단계 설정 없이 호출 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)

        tracker.report(50, 100, "테스트")

        # 단계 없으면 직접 계산 (50%)
        assert len(calls) == 1
        assert calls[0][0] == 50

    def test_report_simple(self):
        """report_simple() 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        tracker.set_stage("테스트", 0, 50)  # 단계 설정해도 무시됨

        tracker.report_simple(75, "직접 지정")

        assert len(calls) == 1
        assert calls[0][0] == 75  # 직접 지정된 값

    def test_is_cancelled_true(self):
        """is_cancelled() True 테스트"""
        def is_cancelled():
            return True

        tracker = ProgressTracker(is_cancelled_fn=is_cancelled)

        assert tracker.is_cancelled() is True

    def test_is_cancelled_false(self):
        """is_cancelled() False 테스트"""
        def is_cancelled():
            return False

        tracker = ProgressTracker(is_cancelled_fn=is_cancelled)

        assert tracker.is_cancelled() is False

    def test_is_cancelled_no_fn(self):
        """is_cancelled() 함수 없을 때 False 테스트"""
        tracker = ProgressTracker()

        assert tracker.is_cancelled() is False

    def test_create_sub_tracker(self):
        """create_sub_tracker() 콜백 생성 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        sub_callback = tracker.create_sub_tracker(20, 50, "Sub: ")

        # 서브 콜백 호출
        sub_callback(50, 100, "진행 중")

        assert len(calls) == 1
        assert calls[0][0] == 35  # 20 + 30*0.5 = 35
        assert calls[0][2] == "Sub: 진행 중"

    def test_sub_tracker_callback(self):
        """create_sub_tracker() 콜백이 올바르게 매핑되는지 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        sub_callback = tracker.create_sub_tracker(0, 100, "")

        # 0% → 0%
        sub_callback(0, 100, "시작")
        assert calls[-1][0] == 0

        # 100% → 100%
        sub_callback(100, 100, "끝")
        assert calls[-1][0] == 100

    def test_sub_tracker_zero_total(self):
        """create_sub_tracker() total=0일 때 중간점 사용 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        sub_callback = tracker.create_sub_tracker(20, 50, "")

        sub_callback(0, 0, "메시지")

        assert calls[0][0] == 35  # 중간점

    def test_no_callback(self):
        """콜백 없을 때 정상 동작 테스트"""
        tracker = ProgressTracker()  # 콜백 없음

        # 예외 없이 동작해야 함
        result = tracker.report(50, 100, "테스트")
        assert result is True

        result = tracker.report_simple(50, "테스트")
        assert result is True

    def test_get_stage(self):
        """get_stage() 조회 테스트"""
        tracker = ProgressTracker()
        tracker.set_stage("단계1", 0, 50)

        stage = tracker.get_stage("단계1")

        assert stage is not None
        assert stage.name == "단계1"
        assert stage.start_percent == 0
        assert stage.end_percent == 50

    def test_get_stage_not_found(self):
        """get_stage() 없는 단계 조회 테스트"""
        tracker = ProgressTracker()

        stage = tracker.get_stage("없는단계")

        assert stage is None

    def test_get_current_stage(self):
        """get_current_stage() 테스트"""
        tracker = ProgressTracker()

        assert tracker.get_current_stage() is None

        tracker.set_stage("단계1", 0, 50)
        assert tracker.get_current_stage().name == "단계1"

    def test_multiple_stages(self):
        """여러 단계 순차 처리 테스트"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)

        # 단계 1: 0% ~ 50%
        tracker.set_stage("단계1", 0, 50)
        tracker.report(0, 100, "시작")
        tracker.report(100, 100, "완료")

        # 단계 2: 50% ~ 100%
        tracker.set_stage("단계2", 50, 100)
        tracker.report(0, 100, "시작")
        tracker.report(100, 100, "완료")

        assert calls[0][0] == 0   # 단계1 시작
        assert calls[1][0] == 50  # 단계1 완료
        assert calls[2][0] == 50  # 단계2 시작
        assert calls[3][0] == 100 # 단계2 완료


class TestCreateTracker:
    """create_tracker() 팩토리 함수 테스트"""

    def test_create_empty(self):
        """빈 트래커 생성 테스트"""
        tracker = create_tracker()

        assert isinstance(tracker, ProgressTracker)
        assert tracker.callback is None

    def test_create_with_callback(self):
        """콜백과 함께 생성 테스트"""
        def callback(c, t, m):
            pass

        tracker = create_tracker(callback=callback)

        assert tracker.callback is callback

    def test_create_with_is_cancelled(self):
        """취소 함수와 함께 생성 테스트"""
        def is_cancelled():
            return False

        tracker = create_tracker(is_cancelled_fn=is_cancelled)

        assert tracker.is_cancelled_fn is is_cancelled

    def test_create_with_total_percent(self):
        """total_percent 지정 생성 테스트"""
        tracker = create_tracker(total_percent=200)

        assert tracker.total_percent == 200


class TestCreateComparisonTracker:
    """create_comparison_tracker() 테스트"""

    def test_create_comparison_tracker(self):
        """비교 트래커 생성 테스트"""
        tracker = create_comparison_tracker()

        assert isinstance(tracker, ProgressTracker)

    def test_comparison_stages_preset(self):
        """프리셋 단계 등록 확인 테스트"""
        tracker = create_comparison_tracker()

        # 모든 프리셋 단계가 등록되어야 함
        for name in COMPARISON_STAGES.keys():
            assert tracker.get_stage(name) is not None

    def test_comparison_stages_values(self):
        """프리셋 단계 값 확인 테스트"""
        tracker = create_comparison_tracker()

        convert_stage = tracker.get_stage("convert")
        assert convert_stage.start_percent == 0
        assert convert_stage.end_percent == 10

        extract_old = tracker.get_stage("extract_old")
        assert extract_old.start_percent == 20
        assert extract_old.end_percent == 50
        assert extract_old.message_prefix == "Old: "


class TestIntegration:
    """통합 테스트"""

    def test_typical_comparison_workflow(self):
        """일반적인 비교 워크플로우 시뮬레이션"""
        progress_log = []
        cancelled = False

        def callback(current, total, message):
            progress_log.append((current, message))

        def is_cancelled():
            return cancelled

        tracker = create_tracker(callback, is_cancelled)

        # DXF 변환 단계
        if not tracker.set_stage("변환", 0, 10).report_simple(0, "DXF 변환 시작"):
            return

        tracker.report_simple(5, "Old 파일 변환")
        tracker.report_simple(10, "New 파일 변환")

        # 엔티티 추출 단계 (서브 콜백 사용)
        extract_callback = tracker.create_sub_tracker(20, 50, "Old: ")
        extract_callback(0, 100, "시작")
        extract_callback(50, 100, "진행 중")
        extract_callback(100, 100, "완료")

        # 비교 단계
        tracker.set_stage("비교", 80, 100)
        tracker.report(0, 100, "비교 시작")
        tracker.report(100, 100, "비교 완료")

        # 진행률이 순차적으로 증가했는지 확인
        percentages = [p[0] for p in progress_log]
        assert percentages == [0, 5, 10, 20, 35, 50, 80, 100]

    def test_cancellation_during_workflow(self):
        """워크플로우 중 취소 시나리오"""
        progress_log = []
        should_cancel = [False]  # 리스트로 래핑하여 클로저에서 수정 가능

        def callback(current, total, message):
            progress_log.append((current, message))

        def is_cancelled():
            return should_cancel[0]

        tracker = create_tracker(callback, is_cancelled)

        # 첫 번째 단계 - 정상 진행
        tracker.set_stage("단계1", 0, 50)
        assert tracker.report(50, 100, "진행 중") is True

        # 취소 플래그 설정
        should_cancel[0] = True

        # 두 번째 단계 - 취소됨
        tracker.set_stage("단계2", 50, 100)
        assert tracker.report(0, 100, "시작") is False

        # 첫 번째 단계만 기록됨
        assert len(progress_log) == 1

    def test_dwg_differ_compatible_pattern(self):
        """DwgDiffer 기존 패턴과 호환성 테스트"""
        calls = []

        def progress_callback(current, total, message):
            calls.append((current, total, message))

        def is_cancelled():
            return False

        tracker = create_tracker(progress_callback, is_cancelled)

        # 기존 DwgDiffer 패턴 시뮬레이션
        # 1. DXF 변환
        if progress_callback:
            progress_callback(0, 100, "DXF 변환 중...")

        # 2. Old 파일 추출 (기존 패턴 - 내부 함수 정의)
        def progress_a(current, total, msg):
            if progress_callback:
                if total > 0:
                    pct = 20 + int(30 * current / total)
                else:
                    pct = 35
                progress_callback(pct, 100, f"Old 파일: {msg}")

        progress_a(50, 100, "엔티티 추출")

        # 새 패턴 - create_sub_tracker 사용
        sub_callback = tracker.create_sub_tracker(50, 80, "New 파일: ")
        sub_callback(50, 100, "엔티티 추출")

        # 결과 확인
        assert calls[0] == (0, 100, "DXF 변환 중...")
        assert calls[1] == (35, 100, "Old 파일: 엔티티 추출")  # 기존 패턴
        assert calls[2] == (65, 100, "New 파일: 엔티티 추출")  # 새 패턴


class TestZeroTotalHandling:
    """total=0 처리 테스트"""

    def test_report_zero_total_no_stage(self):
        """report() total=0, 단계 없음"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)

        # 단계 없이 total=0이면 current 값 직접 사용
        tracker.report(0, 0, "메시지")

        assert calls[0][0] == 0

    def test_sub_tracker_zero_total(self):
        """서브 트래커 total=0 처리"""
        calls = []

        def callback(current, total, message):
            calls.append((current, total, message))

        tracker = ProgressTracker(callback=callback)
        sub_callback = tracker.create_sub_tracker(40, 60, "")

        # total=0이면 중간점 사용
        sub_callback(0, 0, "메시지")

        assert calls[0][0] == 50  # (40 + 60) // 2
