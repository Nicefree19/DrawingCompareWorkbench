"""DWG/DXF 파일 비교 통합 인터페이스

Sprint 9 Phase 1.4: DwgDiffer
DWG/DXF 파일을 비교하고 변경 사항을 감지합니다.

기능:
    - CanonicalDrawing import/normalize/compare 기본 경로
    - Legacy DWG → DXF 자동 변환 (ODA Converter)은 명시적 fallback 전용
    - Legacy ezdxf 추출/비교는 config={"use_canonical_pipeline": False} 전용
    - ComparisonResult 통합
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .base import ChangeRecord, ChangeType, ComparisonResult
from .cad_stability import CadStabilityLimits
from .comparison_config import ComparisonConfig, get_default_config
from .drawing_compare_engine import CompareTolerance, DrawingCompareOptions
from .dxf_comparator import DxfChangeType, DxfComparator, DxfComparisonResult
from .dxf_entity_extractor import DxfEntityExtractor
from .dxf_read import read_dxf_document
from .dwg_converter import DwgConverter, ODAConverterNotFoundError
from .import_pipeline import (
    ComparePipeline,
    ComparePipelineOptions,
    ImportPipelineOptions,
)
from .progress_tracker import create_tracker
from .source_signature import source_cache_filename, source_cache_stem

logger = logging.getLogger(__name__)

# ezdxf 임포트 확인
try:
    import ezdxf

    # ezdxf의 FIELD 경고 억제 (로그 가독성 향상)
    logging.getLogger("ezdxf").setLevel(logging.ERROR)

    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False


class DwgDiffer:
    """DWG/DXF 파일 비교 통합 인터페이스

    DWG 또는 DXF 파일을 비교하고 변경 사항을 감지합니다.
    DWG 파일은 자동으로 DXF로 변환됩니다.

    사용 예시:
        differ = DwgDiffer()
        result = differ.compare("old.dwg", "new.dwg")
        print(f"변경점: {result.total_changes}개")
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        ignore_layers: Optional[List[str]] = None,
        comparison_config: Optional[ComparisonConfig] = None,
        dxf_cache_dir: Optional[Union[str, Path]] = None,
        change_zone_stream_path: Optional[Union[str, Path]] = None,
        change_zone_stream_pair_id: str = "",
        block_text_detection: bool = True,
    ):
        """
        Args:
            config: 설정 딕셔너리 (레거시)
            ignore_layers: 무시할 레이어 목록 (레거시, comparison_config가 우선)
            comparison_config: Phase 3 P3-2 ComparisonConfig 객체
            block_text_detection: Phase O Commit 3 [RV-20260508-009]
                — INSERT block-internal text fingerprint 활성. 기본
                True (사용자 사례 보호); False 시 legacy 회귀.
        """
        self._block_text_detection = bool(block_text_detection)
        self.config = config or {}
        if "use_canonical_pipeline" in self.config:
            self._use_canonical_pipeline = bool(self.config["use_canonical_pipeline"])
        elif self.config.get("use_legacy_ezdxf_pipeline", False):
            self._use_canonical_pipeline = False
        else:
            self._use_canonical_pipeline = True
        self._allow_oda_fallback = bool(
            self.config.get("allow_oda_fallback", False)
            or self.config.get("enable_oda_fallback", False)
        )
        cache_dir = dxf_cache_dir or self.config.get("dxf_cache_dir") or os.environ.get(
            "DRAWING_COMPARE_DXF_CACHE_DIR"
        )
        self._dxf_cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self._change_zone_stream_path = (
            Path(change_zone_stream_path).resolve() if change_zone_stream_path else None
        )
        self._change_zone_stream_pair_id = change_zone_stream_pair_id
        self._dxf_cache_resolution_notes: List[str] = []

        # Phase 3 P3-3: ComparisonConfig 통합
        self._comparison_config = comparison_config or get_default_config()

        # ignore_layers: comparison_config가 우선, 없으면 레거시 사용
        if comparison_config:
            # ComparisonConfig의 ignore_patterns 사용
            self.ignore_layers = []  # comparator에서 LayerPriorityConfig로 처리
        else:
            self.ignore_layers = ignore_layers or ["Defpoints"]

        # 컴포넌트 초기화 (Lazy)
        self._converter: Optional[DwgConverter] = None
        self._extractor: Optional[DxfEntityExtractor] = None
        self._comparator: Optional[DxfComparator] = None

        # 임시 파일 관리
        self._temp_dirs: List[str] = []

        # 기능 사용 가능 여부 확인
        if not EZDXF_AVAILABLE:
            logger.warning("ezdxf가 설치되지 않음 - DXF 비교 불가")

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료 - 예외 발생 시에도 임시 파일 정리"""
        self._cleanup_temp()
        return False  # 예외를 재발생시킴

    @property
    def converter(self) -> Optional[DwgConverter]:
        """DWG 변환기 (Lazy init)"""
        if not self._allow_oda_fallback and self._converter is None:
            logger.warning("Legacy ODA fallback is disabled.")
            return None
        if self._converter is None:
            try:
                self._converter = DwgConverter(self.config.get("oda_converter_path"))
            except ODAConverterNotFoundError:
                logger.warning("ODA Converter 없음 - DWG 비교 불가")
        return self._converter

    @property
    def extractor(self) -> DxfEntityExtractor:
        """엔티티 추출기 (Lazy init)"""
        if self._extractor is None:
            max_entities = self._comparison_config.max_entities or None
            self._extractor = DxfEntityExtractor(
                precision=self.config.get("precision", 2),
                max_entities=max_entities,
                block_text_detection=self._block_text_detection,
            )
        return self._extractor

    @property
    def comparator(self) -> DxfComparator:
        """비교 엔진 (Lazy init)"""
        if self._comparator is None:
            # Phase 3 P3-3: ComparisonConfig 전달
            self._comparator = DxfComparator(
                ignore_layers=self.ignore_layers,
                use_spatial_index=self._comparison_config.use_spatial_index,
                config=self._comparison_config,
            )
            self._comparator.configure_change_zone_stream(
                self._change_zone_stream_path,
                pair_id=self._change_zone_stream_pair_id,
            )
        return self._comparator

    @property
    def comparison_config(self) -> ComparisonConfig:
        """ComparisonConfig 객체 반환"""
        return self._comparison_config

    @classmethod
    def from_config(cls, comparison_config: ComparisonConfig) -> "DwgDiffer":
        """ComparisonConfig로부터 DwgDiffer 생성

        Args:
            comparison_config: ComparisonConfig 객체

        Returns:
            DwgDiffer 인스턴스
        """
        return cls(comparison_config=comparison_config)

    def compare(
        self,
        source_a: Union[str, Path],
        source_b: Union[str, Path],
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
        progress_callback: Optional[callable] = None,
        is_cancelled: Optional[callable] = None,
    ) -> ComparisonResult:
        """두 DWG/DXF 파일 비교

        Args:
            source_a: 기준(Old) 파일 경로
            source_b: 대상(New) 파일 경로
            include_layers: 포함할 레이어 목록 (None이면 전체)
            exclude_layers: 제외할 레이어 목록
            progress_callback: 진행률 콜백 (current, total, message) -> None
            is_cancelled: 취소 확인 함수 () -> bool

        Returns:
            ComparisonResult 객체
        """
        source_a = Path(source_a)
        source_b = Path(source_b)

        logger.info(f"DWG/DXF 비교 시작: {source_a.name} vs {source_b.name}")
        if include_layers:
            logger.info(f"  포함 레이어: {include_layers}")
        if exclude_layers:
            logger.info(f"  제외 레이어: {exclude_layers}")

        if self._use_canonical_pipeline:
            return self._compare_canonical_pipeline(
                source_a,
                source_b,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )

        result = ComparisonResult(
            source_a=str(source_a),
            source_b=str(source_b),
        )
        timing = {}
        start_time = time.perf_counter()

        try:
            # ProgressTracker 생성
            tracker = create_tracker(progress_callback, is_cancelled)

            # 1. DXF로 변환 (필요시)
            if not tracker.report_simple(0, "DXF 변환 중..."):
                logger.info("비교 취소됨 (DXF 변환 시작)")
                return result

            t0 = time.perf_counter()
            dxf_a = self._ensure_dxf(source_a)
            dxf_b = self._ensure_dxf(source_b)
            timing["convert"] = time.perf_counter() - t0

            # 취소 확인
            if tracker.is_cancelled():
                logger.info("비교 취소됨 (DXF 변환 후)")
                return result

            # 2. 엔티티 추출 (레이어 필터 적용)
            if not tracker.report_simple(10, "Old 파일 로드 중..."):
                return result
            t0 = time.perf_counter()
            doc_a = read_dxf_document(dxf_a, ezdxf_module=ezdxf)

            if not tracker.report_simple(15, "New 파일 로드 중..."):
                return result
            doc_b = read_dxf_document(dxf_b, ezdxf_module=ezdxf)
            timing["load"] = time.perf_counter() - t0

            # Phase 3 P3-3: expand_blocks 설정 적용
            expand_blocks = self._comparison_config.expand_blocks

            # Old 파일 엔티티 추출 (ProgressTracker 서브 콜백 사용)
            t0 = time.perf_counter()
            entities_a = self.extractor.extract(
                doc_a,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                expand_blocks=expand_blocks,
                progress_callback=tracker.create_sub_tracker(20, 50, "Old 파일: "),
                is_cancelled=is_cancelled,
            )
            extract_stats_a = dict(getattr(self.extractor, "last_stats", {}))
            timing["extract_old"] = time.perf_counter() - t0

            # 취소 확인
            if tracker.is_cancelled():
                logger.info("비교 취소됨 (Old 파일 추출 후)")
                return result

            # New 파일 엔티티 추출 (ProgressTracker 서브 콜백 사용)
            t0 = time.perf_counter()
            entities_b = self.extractor.extract(
                doc_b,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                expand_blocks=expand_blocks,
                progress_callback=tracker.create_sub_tracker(50, 80, "New 파일: "),
                is_cancelled=is_cancelled,
            )
            extract_stats_b = dict(getattr(self.extractor, "last_stats", {}))
            timing["extract_new"] = time.perf_counter() - t0

            # 3. 비교
            t0 = time.perf_counter()
            if not tracker.report_simple(80, "DXF 변경 분석 중..."):
                return result
            comparison = self._compare_entities(
                entities_a,
                entities_b,
                progress_callback=tracker.create_sub_tracker(80, 95, "DXF 비교: "),
                is_cancelled=is_cancelled,
            )
            if tracker.is_cancelled():
                logger.info("비교 취소됨 (DXF 변경 분석 중)")
                return result
            timing["compare"] = time.perf_counter() - t0

            # 4. ComparisonResult로 변환
            for change in comparison.changes:
                # [High] 좌표 정보 + old_location + change_type을 metadata에 추가
                metadata = {
                    "layer": change.layer,
                    "entity_type": change.entity_type,
                    "change_type": change.change_type.value,  # [Medium] 변경 타입 전달
                    "change_detail": getattr(change, "change_detail", None),
                    "change_category": getattr(change, "change_category", None),
                    "source_format": "cad",
                    "detection_source": "cad_entity",
                }
                # DxfChange.location은 Tuple[float, float] (new 위치)
                if change.location:
                    metadata["x"] = change.location[0]
                    metadata["y"] = change.location[1]
                    metadata["w"] = 50  # 기본 영역 크기
                    metadata["h"] = 30
                # [High] old_location 전달 (MODIFIED 이동 변경용)
                if hasattr(change, "old_location") and change.old_location:
                    metadata["old_x"] = change.old_location[0]
                    metadata["old_y"] = change.old_location[1]

                result.add_change(
                    ChangeRecord(
                        key=f"{change.entity_type}_{change.change_type.value}_{id(change)}",
                        change_type=self._map_change_type(change.change_type),
                        old_value=change.old_data,
                        new_value=change.new_data,
                        location=str(change.location) if change.location else None,
                        metadata=metadata,
                    )
                )

            change_counts = comparison.stats.get("change_counts") or {}
            if change_counts:
                result.added_count = int(change_counts.get("added", result.added_count) or 0)
                result.deleted_count = int(change_counts.get("deleted", result.deleted_count) or 0)
                result.modified_count = int(change_counts.get("modified", result.modified_count) or 0)

            # 메타데이터 추가
            result.metadata = {
                "comparison_type": "DWG/DXF",
                "entities_a": comparison.stats.get("entities_a", 0),
                "entities_b": comparison.stats.get("entities_b", 0),
                "by_type": comparison.stats.get("by_type", {}),
                "by_layer": comparison.stats.get("by_layer", {}),
                "layer_statistics": {
                    layer: stats.to_dict()
                    for layer, stats in getattr(comparison, "layer_statistics", {}).items()
                },
                "priority_summary": getattr(comparison, "priority_summary", {}),
                "modified_detected": comparison.stats.get("modified_detected", 0),
                "layer_moves_detected": comparison.stats.get("layer_moves_detected", 0),
                "change_counts": change_counts,
                "change_records_in_memory": len(result.changes),
                "index_backend": comparison.metadata.get(
                    "index_backend", comparison.stats.get("index_backend")
                ),
                "large_drawing_mode": comparison.metadata.get(
                    "large_drawing_mode", comparison.stats.get("large_drawing_mode")
                ),
                "truncated_changes": comparison.metadata.get(
                    "truncated_changes", comparison.stats.get("truncated_changes", False)
                ),
                "omitted_change_counts": comparison.metadata.get(
                    "omitted_change_counts",
                    comparison.stats.get(
                        "omitted_change_counts",
                        {"added": 0, "deleted": 0, "modified": 0},
                    ),
                ),
                "extraction_stats": {
                    "a": extract_stats_a,
                    "b": extract_stats_b,
                },
                # Phase Q2 Codex follow-up (RV-20260509-002) — surface
                # comparator-stage suppression counters that previously only
                # lived on the intermediate DxfComparisonResult.stats. Without
                # this, the suppression audit dialog reports zero comparison-
                # stage drops for real Workbench batch results.
                "comparison_suppression": {
                    "modified_ignored": comparison.stats.get(
                        "modified_ignored", 0
                    ),
                    "alignment_suppressed": comparison.stats.get(
                        "alignment_suppressed", 0
                    ),
                    "cosmetic_suppressed": comparison.stats.get(
                        "cosmetic_suppressed", 0
                    ),
                },
                # Plan §16 Phase C-2.2 — same propagation as the secondary
                # ``_dxf_to_comparison_result`` path. This is the primary
                # ``DwgDiffer.compare()`` route (used by BatchCompareJob) so
                # the pipeline harvester in validate_drawing_compare_realset
                # needs the metric on EVERY ComparisonResult, not just the
                # ones routed through ``_dxf_to_comparison_result``. Two
                # metadata-construction sites is a known gotcha (Plan §16 R1).
                "peak_changes_pre_truncate": comparison.stats.get(
                    "peak_changes_pre_truncate", 0
                ),
                "time_to_first_stream_record_ms": comparison.stats.get(
                    "time_to_first_stream_record_ms"
                ),
                "dxf_cache_resolution_notes": list(self._dxf_cache_resolution_notes),
            }
            self._copy_change_zone_stream_metadata(comparison, result)
            if result.metadata.get("truncated_changes"):
                result.warnings.append(
                    "Large drawing mode truncated in-memory change records; "
                    "full counts are available in metadata.change_counts."
                )

            timing["total"] = time.perf_counter() - start_time
            logger.info(
                "DWG/DXF 비교 타이밍: convert=%.2fs load=%.2fs extract_old=%.2fs "
                "extract_new=%.2fs compare=%.2fs total=%.2fs",
                timing.get("convert", 0.0),
                timing.get("load", 0.0),
                timing.get("extract_old", 0.0),
                timing.get("extract_new", 0.0),
                timing.get("compare", 0.0),
                timing.get("total", 0.0),
            )
            logger.info(f"DWG/DXF 비교 완료: {result.total_changes}개 변경")

        except Exception as e:
            logger.error(f"DWG/DXF 비교 실패: {e}")
            result.warnings.append(f"비교 중 오류: {e}")
            raise

        finally:
            # 임시 파일 정리
            self._cleanup_temp()

        return result

    def _compare_canonical_pipeline(
        self,
        source_a: Path,
        source_b: Path,
        *,
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
        progress_callback: Optional[callable] = None,
        is_cancelled: Optional[callable] = None,
    ) -> ComparisonResult:
        """Run the ODA-free CanonicalDrawing comparison path."""

        result = ComparisonResult(source_a=str(source_a), source_b=str(source_b))
        tracker = create_tracker(progress_callback, is_cancelled)
        started = time.perf_counter()
        try:
            if not tracker.report_simple(0, "CAD importer 선택 중..."):
                return result

            if tracker.is_cancelled():
                return result

            if not tracker.report_simple(10, "CanonicalDrawing 가져오는 중..."):
                return result

            pipeline = ComparePipeline(
                self._canonical_pipeline_options(
                    is_cancelled,
                    include_layers=include_layers,
                    exclude_layers=exclude_layers,
                )
            )
            pipeline_result = pipeline.compare(source_a, source_b)

            if tracker.is_cancelled():
                return result

            if not tracker.report_simple(90, "CanonicalDrawing 비교 결과 변환 중..."):
                return result

            result = pipeline_result.to_comparison_result()
            importer_a = getattr(pipeline_result.imports.get("a"), "importer", "")
            importer_b = getattr(pipeline_result.imports.get("b"), "importer", "")
            result.metadata.update(
                {
                    "comparison_type": "CAD_CANONICAL",
                    "canonical_pipeline": True,
                    "legacy_oda_converter_used": bool(
                        str(importer_a).endswith("oda-fallback")
                        or str(importer_b).endswith("oda-fallback")
                    ),
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            if pipeline_result.is_failed:
                result.warnings.append(
                    f"{pipeline_result.error_code}: {pipeline_result.message}"
                )
            elif pipeline_result.is_partial:
                result.warnings.append(
                    "부분 가져오기 - 일부 객체가 비교에서 제외되었습니다."
                )
            self._result = result
            return result
        except Exception as exc:
            logger.error("Canonical CAD 비교 실패: %s", exc)
            result.warnings.append(f"비교 중 오류: {exc}")
            raise
        finally:
            self._cleanup_temp()

    def _canonical_pipeline_options(
        self,
        is_cancelled: Optional[callable] = None,
        *,
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
    ) -> ComparePipelineOptions:
        sensitivity = self._comparison_config.sensitivity
        configured_limits = self.config.get("stability_limits")
        if isinstance(configured_limits, CadStabilityLimits):
            stability_limits = configured_limits
        elif isinstance(configured_limits, dict):
            stability_limits = CadStabilityLimits(**configured_limits)
        else:
            stability_limits = CadStabilityLimits(
                import_timeout_seconds=self.config.get("import_timeout_seconds", 30.0),
                max_entities=int(self._comparison_config.max_entities or 100_000),
                max_dxf_tokens=int(self.config.get("max_dxf_tokens", 2_500_000)),
                max_block_depth=int(self.config.get("max_block_depth", 4)),
            )
        tolerance = CompareTolerance(
            position_tolerance_mm=float(sensitivity.position_threshold),
            bbox_tolerance_mm=float(sensitivity.position_threshold),
            numeric_tolerance=float(sensitivity.dimension_abs_threshold),
            angle_tolerance_deg=float(sensitivity.rotation_threshold),
        )
        return ComparePipelineOptions(
            import_options=ImportPipelineOptions(
                expand_blocks=bool(self._comparison_config.expand_blocks),
                allow_oda_fallback=self._allow_oda_fallback,
                oda_converter_path=self.config.get("oda_converter_path"),
                stability_limits=stability_limits,
                cancel_callback=is_cancelled,
            ),
            compare_options=DrawingCompareOptions(
                tolerance=tolerance,
                search_radius_mm=float(sensitivity.near_match_radius),
                max_spatial_cells_per_entity=stability_limits.max_spatial_cells_per_entity,
                include_unchanged=True,
                include_entity_snapshots=True,
                include_match_candidates=False,
            ),
            include_layers=include_layers,
            exclude_layers=exclude_layers,
        )

    def compare_and_mark(
        self,
        source_a: Union[str, Path],
        source_b: Union[str, Path],
        output_path: Union[str, Path],
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
        progress_callback: Optional[callable] = None,
        is_cancelled: Optional[callable] = None,
    ) -> tuple:
        """비교 후 구름마크가 추가된 DXF 파일 생성

        Args:
            source_a: 기준(Old) 파일 경로
            source_b: 대상(New) 파일 경로
            output_path: 구름마크 DXF 출력 경로
            include_layers: 포함할 레이어 목록
            exclude_layers: 제외할 레이어 목록
            progress_callback: 진행률 콜백 (current, total, message) -> None
            is_cancelled: 취소 확인 함수 () -> bool

        Returns:
            (생성된 DXF 파일 경로 또는 None, ComparisonResult)
        """
        from .dxf_cloud_marker import DxfCloudMarker

        source_a = Path(source_a)
        source_b = Path(source_b)
        output_path = Path(output_path)

        logger.info(f"비교 및 구름마크 생성: {source_a.name} vs {source_b.name}")

        try:
            # ProgressTracker 생성
            tracker = create_tracker(progress_callback, is_cancelled)
            empty_result = ComparisonResult(source_a=str(source_a), source_b=str(source_b))

            # 1. DXF로 변환 (필요시)
            if not tracker.report_simple(0, "DXF 변환 중..."):
                return (None, empty_result)

            dxf_a = self._ensure_dxf(source_a)
            dxf_b = self._ensure_dxf(source_b)

            if tracker.is_cancelled():
                return (None, empty_result)

            # 2. 엔티티 추출 및 비교
            if not tracker.report_simple(10, "파일 로드 중..."):
                return (None, empty_result)

            doc_a = read_dxf_document(dxf_a, ezdxf_module=ezdxf)
            doc_b = read_dxf_document(dxf_b, ezdxf_module=ezdxf)

            if tracker.is_cancelled():
                return (None, empty_result)

            # Phase 3 P3-3: expand_blocks 설정 적용
            expand_blocks = self._comparison_config.expand_blocks

            # Old 파일 추출 (ProgressTracker 서브 콜백 사용)
            entities_a = self.extractor.extract(
                doc_a,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                expand_blocks=expand_blocks,
                progress_callback=tracker.create_sub_tracker(15, 40, "Old: "),
                is_cancelled=is_cancelled,
            )
            extract_stats_a = dict(getattr(self.extractor, "last_stats", {}))

            if tracker.is_cancelled():
                return (None, empty_result)

            # New 파일 추출 (ProgressTracker 서브 콜백 사용)
            entities_b = self.extractor.extract(
                doc_b,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
                expand_blocks=expand_blocks,
                progress_callback=tracker.create_sub_tracker(40, 65, "New: "),
                is_cancelled=is_cancelled,
            )
            extract_stats_b = dict(getattr(self.extractor, "last_stats", {}))

            if tracker.is_cancelled():
                return (None, empty_result)

            if not tracker.report_simple(70, "엔티티 비교 중..."):
                return (None, empty_result)

            comparison = self._compare_entities(entities_a, entities_b)

            # 3. 구름마크 추가
            if not tracker.report_simple(80, "구름마크 생성 중..."):
                return (None, empty_result)

            marker = DxfCloudMarker(
                segment_length=self.config.get("cloud_segment_length", 15.0),
                margin=self.config.get("cloud_margin", 30.0),
                add_labels=self.config.get("cloud_labels", True),
            )

            result_path = marker.create_marked_dxf(
                base_dxf_path=dxf_b,  # New 파일 기준
                changes=comparison.changes,
                output_path=output_path,
            )

            tracker.report_simple(100, "구름마크 완료!")
            logger.info(f"구름마크 DXF 생성 완료: {result_path}")

            # 비교 결과를 ComparisonResult로 변환하여 반환
            result = self._dxf_to_comparison_result(
                comparison,
                source_a,
                source_b,
                extraction_stats={"a": extract_stats_a, "b": extract_stats_b},
            )
            return (result_path, result)

        except Exception as e:
            logger.error(f"구름마크 생성 실패: {e}")
            raise

        finally:
            # 임시 파일 정리
            self._cleanup_temp()

    def compare_layouts(
        self,
        source_a: Union[str, Path],
        source_b: Union[str, Path],
    ) -> Dict[str, "ComparisonResult"]:
        """모든 레이아웃(Paper Space) 비교

        Args:
            source_a: 기준(Old) 파일 경로
            source_b: 대상(New) 파일 경로

        Returns:
            레이아웃별 비교 결과 {"Layout1": ComparisonResult, ...}
        """
        source_a = Path(source_a)
        source_b = Path(source_b)

        try:
            dxf_a = self._ensure_dxf(source_a)
            dxf_b = self._ensure_dxf(source_b)

            doc_a = read_dxf_document(dxf_a, ezdxf_module=ezdxf)
            doc_b = read_dxf_document(dxf_b, ezdxf_module=ezdxf)

            # 레이아웃 목록 (합집합)
            layouts_a = set(self.extractor.get_layouts(doc_a))
            layouts_b = set(self.extractor.get_layouts(doc_b))
            all_layouts = layouts_a | layouts_b

            results = {}

            for layout_name in sorted(all_layouts):
                logger.info(f"레이아웃 비교: {layout_name}")

                entities_a = self.extractor.extract_layout(doc_a, layout_name)
                entities_b = self.extractor.extract_layout(doc_b, layout_name)

                comparison = self._compare_entities(entities_a, entities_b)

                # ComparisonResult로 변환
                result = ComparisonResult(
                    source_a=f"{source_a}[{layout_name}]",
                    source_b=f"{source_b}[{layout_name}]",
                )

                for change in comparison.changes:
                    result.add_change(
                        ChangeRecord(
                            key=f"{layout_name}_{change.entity_type}_{id(change)}",
                            change_type=self._map_change_type(change.change_type),
                            old_value=change.old_data,
                            new_value=change.new_data,
                            location=str(change.location) if change.location else None,
                            metadata={
                                "layout": layout_name,
                                "layer": change.layer,
                                "entity_type": change.entity_type,
                                "change_type": change.change_type.value,
                                "change_detail": getattr(change, "change_detail", None),
                                "change_category": getattr(change, "change_category", None),
                                "source_format": "cad",
                                "detection_source": "cad_entity",
                            },
                        )
                    )

                results[layout_name] = result

            logger.info(f"레이아웃 비교 완료: {len(results)}개 레이아웃")
            return results

        except Exception as e:
            logger.error(f"레이아웃 비교 실패: {e}")
            raise

        finally:
            # 임시 파일 정리
            self._cleanup_temp()

    def export_excel(
        self,
        source_a: Union[str, Path],
        source_b: Union[str, Path],
        output_path: Union[str, Path],
    ) -> Path:
        """비교 결과를 Excel로 내보내기

        Args:
            source_a: 기준 파일 경로
            source_b: 대상 파일 경로
            output_path: Excel 출력 경로

        Returns:
            생성된 Excel 파일 경로
        """
        from .dwg_excel_reporter import DwgExcelReporter

        # 비교 수행 (내부 결과 사용)
        source_a = Path(source_a)
        source_b = Path(source_b)

        try:
            dxf_a = self._ensure_dxf(source_a)
            dxf_b = self._ensure_dxf(source_b)

            doc_a = read_dxf_document(dxf_a, ezdxf_module=ezdxf)
            doc_b = read_dxf_document(dxf_b, ezdxf_module=ezdxf)

            # Phase 3 P3-3: expand_blocks 설정 적용
            expand_blocks = self._comparison_config.expand_blocks
            entities_a = self.extractor.extract(doc_a, expand_blocks=expand_blocks)
            entities_b = self.extractor.extract(doc_b, expand_blocks=expand_blocks)

            comparison = self._compare_entities(entities_a, entities_b)

            # Excel 생성
            reporter = DwgExcelReporter()
            return reporter.generate(comparison, str(source_a), str(source_b), Path(output_path))

        except Exception as e:
            logger.error(f"Excel 내보내기 실패: {e}")
            raise

        finally:
            # 임시 파일 정리
            self._cleanup_temp()

    def _ensure_dxf(self, path: Path) -> Path:
        """Return a DXF path, converting DWG input when required."""

        if path.suffix.lower() == ".dxf":
            return path

        if path.suffix.lower() != ".dwg":
            raise ValueError(f"Unsupported file format: {path.suffix}")

        if self._dxf_cache_dir is not None:
            return self._ensure_cached_dxf(path)

        if self.converter is None:
            raise ODAConverterNotFoundError(
                "Legacy DWG-to-DXF fallback is disabled or not configured. "
                "Use the supported native DWG import path, compare DXF files, "
                "or convert DWG to DXF outside the customer build."
            )

        logger.info("DWG -> DXF converting: %s", path.name)
        dxf_path = self.converter.convert(path)
        self._temp_dirs.append(str(dxf_path.parent))
        return dxf_path

    def _ensure_cached_dxf(self, path: Path) -> Path:
        """Return a persistent cached DXF conversion for a DWG source."""

        cache_path = self._dxf_cache_path(path)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            logger.info("DWG DXF cache hit: %s -> %s", path.name, cache_path)
            return cache_path
        compatible_cache_path = self._compatible_dxf_cache_path(
            path,
            exact_path=cache_path,
        )
        if compatible_cache_path is not None:
            note = (
                "DWG DXF cache exact key missed; using compatible same-stem cache "
                f"for {path.name}: {compatible_cache_path.name}"
            )
            logger.warning(note)
            self._dxf_cache_resolution_notes.append(note)
            return compatible_cache_path

        if self.converter is None:
            raise ODAConverterNotFoundError(
                "Legacy DWG-to-DXF fallback is disabled or not configured. "
                "Use the supported native DWG import path, compare DXF files, "
                "or convert DWG to DXF outside the customer build."
            )

        logger.info("DWG DXF cache miss: %s -> %s", path.name, cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        converted_path: Optional[Path] = None
        temp_cache_path = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
        try:
            converted_path = self.converter.convert(path)
            shutil.copy2(converted_path, temp_cache_path)
            os.replace(temp_cache_path, cache_path)
            return cache_path
        finally:
            if temp_cache_path.exists():
                temp_cache_path.unlink(missing_ok=True)
            if converted_path is not None and converted_path.parent != cache_path.parent:
                shutil.rmtree(converted_path.parent, ignore_errors=True)

    def _dxf_cache_path(self, path: Path) -> Path:
        filename = source_cache_filename(
            path,
            namespace="compare_dxf",
            extension=".dxf",
            importer_version="ACAD2018",
            config_fingerprint="dwg_differ:v1",
            digest_length=16,
        )
        return self._dxf_cache_dir / filename

    def _compatible_dxf_cache_path(
        self,
        path: Path,
        *,
        exact_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """Find a same-stem cached DXF when the strict path/mtime key changed.

        The strict cache key includes absolute path, size, and mtime to avoid
        stale reuse. In the Workbench, however, users often reopen the same DWG
        from a copied folder while the already-generated DXF cache remains the
        only practical fallback for large ACAD files that exceed the canonical
        token budget. This fallback is intentionally limited to the sanitized
        exact filename stem and non-empty ``*.dxf`` files in the configured cache
        directory.
        """

        if self._dxf_cache_dir is None:
            return None
        cache_dir = self._dxf_cache_dir
        if not cache_dir.exists():
            return None
        if (
            exact_path is not None
            and exact_path.exists()
            and exact_path.stat().st_size > 0
        ):
            return exact_path
        safe_stem = "".join(
            ch if ch.isalnum() or ch in "._-" else "_" for ch in Path(path).stem
        )
        stems = [safe_stem, source_cache_stem(path)]
        candidates = []
        for stem in dict.fromkeys(stem for stem in stems if stem):
            candidates.extend(
                candidate
                for candidate in cache_dir.glob(f"{stem}.*.dxf")
                if candidate.is_file() and candidate.stat().st_size > 0
            )
        if exact_path is not None:
            candidates = [candidate for candidate in candidates if candidate != exact_path]
        if not candidates:
            return None
        candidates.sort(
            key=lambda candidate: (
                candidate.stat().st_mtime_ns,
                candidate.stat().st_size,
            ),
            reverse=True,
        )
        return candidates[0]

    def _map_change_type(self, dxf_change: DxfChangeType) -> ChangeType:
        """DxfChangeType → ChangeType 변환"""
        mapping = {
            DxfChangeType.ADDED: ChangeType.ADDED,
            DxfChangeType.DELETED: ChangeType.DELETED,
            DxfChangeType.MODIFIED: ChangeType.MODIFIED,
        }
        return mapping.get(dxf_change, ChangeType.MODIFIED)

    def _compare_entities(
        self,
        entities_a: Dict[str, List[Any]],
        entities_b: Dict[str, List[Any]],
        progress_callback: Optional[callable] = None,
        is_cancelled: Optional[callable] = None,
    ) -> DxfComparisonResult:
        """Run the richest available DXF comparison path."""
        comparator = self.comparator
        for method_name in (
            "compare_with_layer_statistics",
            "compare_with_modified_detection",
            "compare",
        ):
            method = getattr(comparator, method_name, None)
            if not callable(method):
                continue
            comparison = method(
                entities_a,
                entities_b,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )
            if isinstance(comparison, DxfComparisonResult):
                return comparison

        raise TypeError("DXF comparator did not return a comparison result")

    def _dxf_to_comparison_result(
        self,
        comparison: "DxfComparisonResult",
        source_a: Path,
        source_b: Path,
        extraction_stats: Optional[Dict[str, Any]] = None,
    ) -> ComparisonResult:
        """DxfComparisonResult를 ComparisonResult로 변환

        Args:
            comparison: DxfComparisonResult 객체
            source_a: 기준 파일 경로
            source_b: 대상 파일 경로

        Returns:
            ComparisonResult 객체
        """
        result = ComparisonResult(
            source_a=str(source_a),
            source_b=str(source_b),
        )

        for change in comparison.changes:
            result.add_change(
                ChangeRecord(
                    key=f"{change.entity_type}_{change.change_type.value}_{id(change)}",
                    change_type=self._map_change_type(change.change_type),
                    old_value=change.old_data,
                    new_value=change.new_data,
                    location=str(change.location) if change.location else None,
                    metadata={
                        "layer": change.layer,
                        "entity_type": change.entity_type,
                        "change_type": change.change_type.value,
                        "old_location": getattr(change, "old_location", None),
                        "change_detail": getattr(change, "change_detail", None),
                        "change_category": getattr(change, "change_category", None),
                        "source_format": "cad",
                        "detection_source": "cad_entity",
                    },
                )
            )

        change_counts = comparison.stats.get("change_counts") or {}
        if change_counts:
            result.added_count = int(change_counts.get("added", result.added_count) or 0)
            result.deleted_count = int(change_counts.get("deleted", result.deleted_count) or 0)
            result.modified_count = int(change_counts.get("modified", result.modified_count) or 0)

        result.metadata = {
            "comparison_type": "DWG/DXF",
            "entities_a": comparison.stats.get("entities_a", 0),
            "entities_b": comparison.stats.get("entities_b", 0),
            "by_type": comparison.stats.get("by_type", {}),
            "by_layer": comparison.stats.get("by_layer", {}),
            "layer_statistics": {
                layer: stats.to_dict()
                for layer, stats in getattr(comparison, "layer_statistics", {}).items()
            },
            "priority_summary": getattr(comparison, "priority_summary", {}),
            "modified_detected": comparison.stats.get("modified_detected", 0),
            "layer_moves_detected": comparison.stats.get("layer_moves_detected", 0),
            "change_counts": change_counts,
            "change_records_in_memory": len(result.changes),
            "index_backend": comparison.metadata.get(
                "index_backend", comparison.stats.get("index_backend")
            ),
            "large_drawing_mode": comparison.metadata.get(
                "large_drawing_mode", comparison.stats.get("large_drawing_mode")
            ),
            "truncated_changes": comparison.metadata.get(
                "truncated_changes", comparison.stats.get("truncated_changes", False)
            ),
            "omitted_change_counts": comparison.metadata.get(
                "omitted_change_counts",
                comparison.stats.get(
                    "omitted_change_counts",
                    {"added": 0, "deleted": 0, "modified": 0},
                ),
            ),
            "extraction_stats": extraction_stats or {},
            # Plan §16 Phase C-2.2 — propagate comparator-derived metrics so
            # ``validate_drawing_compare_realset.py`` can harvest them per pair
            # and the audit gate can enforce thresholds. ``peak_changes_pre_truncate``
            # is the in-band peak captured during ``DxfComparator.compare()``
            # (Plan §15 Phase C-1). ``time_to_first_stream_record_ms`` is the
            # streaming first-write latency added in Phase C-3.1. Both may be
            # 0 / None for non-streaming or short-circuit paths — harvesters
            # must tolerate that.
            "peak_changes_pre_truncate": comparison.stats.get(
                "peak_changes_pre_truncate", 0
            ),
            "time_to_first_stream_record_ms": comparison.stats.get(
                "time_to_first_stream_record_ms"
            ),
            "dxf_cache_resolution_notes": list(self._dxf_cache_resolution_notes),
        }
        self._copy_change_zone_stream_metadata(comparison, result)
        if result.metadata.get("truncated_changes"):
            result.warnings.append(
                "Large drawing mode truncated in-memory change records; "
                "full counts are available in metadata.change_counts."
            )

        return result

    @staticmethod
    def _copy_change_zone_stream_metadata(
        comparison: "DxfComparisonResult",
        result: ComparisonResult,
    ) -> None:
        for key in (
            "change_zone_stream_path",
            "change_zone_record_count",
            "change_zone_stream_complete",
            "change_zone_stream_schema_version",
            "change_zone_missing_bbox_count",
            "change_zone_stream_error",
        ):
            if key in comparison.metadata:
                result.metadata[key] = comparison.metadata[key]
            elif key in comparison.stats:
                result.metadata[key] = comparison.stats[key]

    def _cleanup_temp(self):
        """임시 파일 정리"""
        for temp_dir in self._temp_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"임시 폴더 정리 실패: {temp_dir} - {e}")

        self._temp_dirs.clear()

    @classmethod
    def is_available(cls) -> bool:
        """DWG/DXF 비교 사용 가능 여부"""
        return True

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """기능 상태 확인

        Returns:
            {
                "canonical_pipeline": bool,
                "ezdxf": bool,
                "oda_converter": bool,
                "dwg_support": bool,
                "dxf_support": bool,
            }
        """
        return {
            "canonical_pipeline": True,
            "ezdxf": EZDXF_AVAILABLE,
            "oda_converter": False,
            "oda_path": None,
            "oda_required": False,
            "dwg_support": True,
            "dwg_support_scope": "limited-read-only-adapter",
            "dwg_supported_versions": ["AC1015"],
            "dwg_detectable_versions": [
                "AC1009",
                "AC1012",
                "AC1014",
                "AC1015",
                "AC1018",
                "AC1021",
                "AC1024",
                "AC1027",
                "AC1032",
            ],
            "dwg_planned_versions": ["AC1018", "AC1021", "AC1024", "AC1027", "AC1032"],
            "dxf_support": True,
            "legacy_oda_required": False,
            "legacy_oda_fallback": "disabled_by_default",
        }

    def get_layers(self, source: Union[str, Path]) -> List[str]:
        """DWG/DXF 파일의 레이어 목록 반환

        Args:
            source: DWG 또는 DXF 파일 경로

        Returns:
            레이어 이름 목록
        """
        source = Path(source)
        dxf_path = self._ensure_dxf(source)
        doc = read_dxf_document(dxf_path, ezdxf_module=ezdxf)
        return self.extractor.get_entity_layers(doc)
