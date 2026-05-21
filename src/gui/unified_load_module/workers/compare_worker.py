# -*- coding: utf-8 -*-
"""
도면 비교 Worker (Drawing Compare Worker)
=========================================

UI 스레드를 차단하지 않고 백그라운드에서 도면 비교를 수행합니다.

주요 기능:
- DWG/DXF 파일 비교 (ezdxf 사용)
- PDF/이미지 비교 (OpenCV 사용)
- 진행률 시그널 제공
- 취소 기능

Author: TEKLA_MCP Team
Date: 2025-12-20
"""

import logging
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class CompareWorker(QThread):
    """백그라운드에서 도면 비교를 수행하는 Worker 스레드

    UI 스레드 차단 없이 대용량 DWG/DXF 파일 비교가 가능합니다.

    Signals:
        finished: 비교 완료 시 (result, differ, diff_img, (old_img, new_img))
        error: 오류 발생 시 (error_message)
        progress: 진행률 업데이트 시 (percent, message)

    Examples:
        >>> worker = CompareWorker("old.dwg", "new.dwg", {"cloud_mark": True})
        >>> worker.finished.connect(self._on_compare_finished)
        >>> worker.start()
    """

    # Signals
    finished = Signal(
        object, object, object, object
    )  # result, differ, diff_img, (old_img, new_img)
    error = Signal(str)
    progress = Signal(int, str)  # percent, message

    def __init__(
        self,
        old_path: str,
        new_path: str,
        options: Dict[str, Any],
        parent=None,
    ):
        """Worker 초기화

        Args:
            old_path: 기준(Old) 파일 경로
            new_path: 대상(New) 파일 경로
            options: 비교 옵션 딕셔너리
                - cloud_mark: 구름마크 DXF 생성 여부
                - include_layers: 포함할 레이어 목록
                - exclude_layers: 제외할 레이어 목록
                - compare_layouts: Paper Space 비교 여부
                - auto_align: 자동 정렬 여부
                - text_compare: 텍스트 비교 여부
                - page: PDF 페이지 번호
            parent: 부모 QObject
        """
        super().__init__(parent)
        self.old_path = old_path
        self.new_path = new_path
        self.options = options
        self._is_cancelled = False

    def cancel(self):
        """비교 작업 취소 요청"""
        self._is_cancelled = True

    def _create_comparison_config(self):
        """UI 옵션에서 ComparisonConfig 생성

        Phase 3 P3-3: UI 설정을 ComparisonConfig로 변환
        지원 옵션:
            - expand_blocks: 블록 내부 엔티티 확장 여부 (Phase Q3 기본: True)
            - use_spatial_index: R-tree 공간 인덱싱 사용 여부 (기본: True)
            - position_threshold: 위치 변경 임계값 (mm, 기본: 1.0)
            - near_match_radius: 근접 매칭 검색 반경 (mm, 기본: 10.0)

        Phase O dialog 적용 범위 (RV-20260508-001 #3 → 후속 보강):
        이 worker 는 dialog 의 O2 (global_alignment, hungarian) +
        O3 (cosmetic detection/suppress/attributes) +
        O5 (noise_filter_strength, _run_image_compare 의 PDF 경로
        한정) 를 적용합니다.

        **O4 (min_changes_per_zone, single_entity_noise_score_threshold)**
        는 이 worker 에 적용 surface 가 없습니다:
            - legacy DrawingCompareTab 은 zones 를 빌드하지 않고 raw
              change 리스트만 표시합니다 (v2 Workbench 의
              build_change_zones 후처리가 없음).
            - 따라서 O4 ("zone promote 차단") 가 작용할 대상이 부재.
            - 사용자는 dialog 에서 O4 를 조정해도 legacy 경로의 raw
              change 결과는 바뀌지 않습니다 (의도된 동작 — zone
              개념 자체가 없는 워크플로우).
        v2 Workbench (FolderComparePipeline) 는 모든 5개 그룹을
        완전 적용합니다.

        Returns:
            ComparisonConfig 인스턴스
        """
        from src.services.comparison.comparison_config import (
            ComparisonConfig,
            SensitivityConfig,
        )

        # UI 옵션에서 값 추출 (Phase Q3 RV-20260509-002 — default flipped
        # False → True 와 일치. block geometry 변경 검출 기본 활성)
        expand_blocks = self.options.get("expand_blocks", True)
        use_spatial_index = self.options.get("use_spatial_index", True)
        position_threshold = self.options.get("position_threshold", 1.0)
        near_match_radius = self.options.get("near_match_radius", 10.0)

        # SensitivityConfig 생성 (UI에서 설정된 값 사용)
        sensitivity = SensitivityConfig(
            position_threshold=position_threshold,
            near_match_radius=near_match_radius,
        )

        # Phase O — overlay the user's noise filter dialog choices on top
        # of the per-tab preset. The existing position/near-match preset
        # combo (Strict/Normal/Relaxed) keeps its meaning; the dialog
        # adds the orthogonal Phase O fields (global alignment,
        # Hungarian cap, cosmetic separation). Disk read is silent on
        # missing/invalid file → defaults preserve legacy behaviour.
        try:
            from src.services.comparison.noise_filter_io import (
                load_noise_filter_settings,
            )
            noise_filter = load_noise_filter_settings()
            sensitivity.global_alignment_enabled = (
                noise_filter.global_alignment_enabled
            )
            sensitivity.hungarian_max_subset = (
                noise_filter.hungarian_max_subset
            )
            sensitivity.cosmetic_detection_enabled = (
                noise_filter.cosmetic_detection_enabled
            )
            sensitivity.suppress_cosmetic_only = (
                noise_filter.suppress_cosmetic_only
            )
            sensitivity.cosmetic_attributes = tuple(
                noise_filter.cosmetic_attributes
            )
        except Exception:
            # Defensive — never let a config-load failure break the
            # legacy compare worker. Silent fallback to default
            # SensitivityConfig values is the same behaviour as before
            # this Phase O integration.
            pass

        # ComparisonConfig 생성
        config = ComparisonConfig(
            sensitivity=sensitivity,
            expand_blocks=expand_blocks,
            use_spatial_index=use_spatial_index,
        )

        return config

    def run(self):
        """백그라운드에서 비교 실행"""
        try:
            ext_old = Path(self.old_path).suffix.lower()
            ext_new = Path(self.new_path).suffix.lower()

            self.progress.emit(10, "엔진 초기화 중...")

            if ext_old in (".dwg", ".dxf") and ext_new in (".dwg", ".dxf"):
                self._run_dwg_compare()
            else:
                self._run_image_compare()

        except ImportError as e:
            self.error.emit(
                f"필요한 라이브러리가 설치되지 않았습니다:\n{e}\n\n"
                "pip install opencv-python PyMuPDF ezdxf"
            )
        except Exception as e:
            logger.exception("Worker comparison failed")
            self.error.emit(str(e))

    def _resolve_noise_filter_strength(self) -> str:
        """Read ``noise_filter_strength`` from the dialog-saved config.

        Returns the user's choice ("low" / "medium" / "high") for the
        PDF visual-diff strength preset, falling back to "medium" on
        any disk-load failure. Mirrors the defensive try/except
        pattern of ``_create_comparison_config`` so a corrupt or
        unreadable config never breaks the legacy compare flow.
        """
        try:
            from src.services.comparison.noise_filter_io import (
                load_noise_filter_settings,
            )
            return load_noise_filter_settings().noise_filter_strength
        except Exception:
            return "medium"

    def _run_dwg_compare(self):
        """DWG/DXF 파일 비교 실행"""
        from src.services.comparison.dwg_differ import DwgDiffer

        # Phase 3 P3-3: UI 옵션에서 ComparisonConfig 생성
        comparison_config = self._create_comparison_config()
        differ = DwgDiffer(comparison_config=comparison_config)
        logger.info(
            f"DWG/DXF 비교 엔진 사용 (Worker) - expand_blocks={comparison_config.expand_blocks}"
        )

        self.progress.emit(10, "DWG/DXF 비교 시작...")

        # 레이어 필터 옵션
        include_layers = self.options.get("include_layers")
        exclude_layers = self.options.get("exclude_layers")

        # 진행률 콜백 정의 (0-100% → 10-85% 매핑)
        def on_progress(pct, total, msg):
            mapped_pct = 10 + int(pct * 0.75)
            self.progress.emit(mapped_pct, msg)

        result = None

        # 구름마크 출력 (옵션) - 비교 결과도 함께 반환됨
        if self.options.get("cloud_mark", False):
            output_dxf = Path(self.new_path).with_name(f"{Path(self.new_path).stem}_MARKED.dxf")
            marked_path, result = differ.compare_and_mark(
                self.old_path,
                self.new_path,
                output_dxf,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                progress_callback=on_progress,
                is_cancelled=lambda: self._is_cancelled,
            )
            if marked_path:
                logger.info(f"구름마크 DXF 생성: {marked_path}")

        if self._is_cancelled:
            return

        # cloud_mark가 아니거나 결과가 없는 경우에만 compare() 호출
        if result is None:
            result = differ.compare(
                self.old_path,
                self.new_path,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                progress_callback=on_progress,
                is_cancelled=lambda: self._is_cancelled,
            )

        # Paper Space 비교
        if self.options.get("compare_layouts", False):
            self.progress.emit(75, "Paper Space 비교 중...")
            layout_results = differ.compare_layouts(self.old_path, self.new_path)
            layout_total = sum(
                lr.total_changes for lr in layout_results.values() if hasattr(lr, "total_changes")
            )
            result.metadata = result.metadata or {}
            result.metadata["layout_results"] = {
                name: lr.total_changes if hasattr(lr, "total_changes") else 0
                for name, lr in layout_results.items()
            }
            logger.info(
                f"Paper Space 비교 완료: {layout_total}개 변경 "
                f"(레이아웃 {len(layout_results)}개)"
            )

        self._emit_result(differ, result)

    def _run_image_compare(self):
        """PDF/이미지 파일 비교 실행"""
        from src.services.comparison.drawing_differ import DrawingDiffer

        # Phase O5 — pull the user's PDF noise-filter strength preset
        # from the noise filter dialog. RV-20260508-001 #3 follow-up:
        # legacy worker now applies O5 to image-compare paths (PDF
        # rasterised diff) so the dialog combo affects all entry
        # points uniformly. O4 (zone-level filter) still doesn't
        # apply here — see _create_comparison_config docstring.
        noise_strength = self._resolve_noise_filter_strength()

        differ = DrawingDiffer(
            config={
                "alignment_enabled": self.options.get("auto_align", True),
                "text_extraction": self.options.get("text_compare", True),
                "noise_filter_strength": noise_strength,
            }
        )
        page = self.options.get("page", 0)

        self.progress.emit(40, "이미지 변환 및 정렬 중...")

        if self._is_cancelled:
            return

        self.progress.emit(70, "픽셀 비교 중...")
        result = differ.compare(self.old_path, self.new_path, page=page)

        self._emit_result(differ, result)

    def _emit_result(self, differ, result):
        """비교 결과 시그널 발송"""
        if self._is_cancelled:
            return

        self.progress.emit(90, "결과 생성 중...")

        # 차이 이미지 및 원본 이미지 가져오기 (DrawingDiffer만 지원)
        diff_img = None
        old_img = None
        new_img = None

        if hasattr(differ, "get_diff_image"):
            try:
                diff_img = differ.get_diff_image()
            except Exception as e:
                logger.warning(f"차이 이미지 생성 실패: {e}")

        # Phase 4: 좌표 정합을 위해 aligned 이미지 우선 사용
        if hasattr(differ, "get_compare_images"):
            try:
                old_img, new_img = differ.get_compare_images()
            except Exception as e:
                logger.warning(f"정렬 이미지 가져오기 실패: {e}")

        # Fallback: aligned 이미지가 없으면 원본 사용
        if old_img is None and hasattr(differ, "get_original_images"):
            try:
                old_img, new_img = differ.get_original_images()
            except Exception as e:
                logger.warning(f"원본 이미지 가져오기 실패: {e}")

        self.progress.emit(100, "완료!")
        self.finished.emit(result, differ, diff_img, (old_img, new_img))
