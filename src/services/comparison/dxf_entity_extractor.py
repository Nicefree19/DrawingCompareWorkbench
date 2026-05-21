"""DXF 엔티티 추출 및 정규화

Sprint 9 Phase 1.2: DxfEntityExtractor
DXF 파일에서 엔티티를 추출하고 비교 가능한 형태로 정규화합니다.

지원 엔티티:
    - LINE: 선분
    - CIRCLE: 원
    - ARC: 호
    - LWPOLYLINE/POLYLINE: 폴리라인
    - TEXT/MTEXT: 텍스트
    - DIMENSION: 치수
    - INSERT: 블록 참조
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .entity_normalizers import NormalizerFactory

logger = logging.getLogger(__name__)

# ezdxf 임포트 (선택적)
try:
    import ezdxf
    from ezdxf.document import Drawing

    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    logger.warning("ezdxf가 설치되지 않았습니다: pip install ezdxf")


@dataclass(slots=True)
class NormalizedEntity:
    """정규화된 DXF 엔티티

    Attributes:
        hash: 엔티티 고유 해시 (비교용) — 좌표만 포함, cosmetic 제외
        entity_type: 엔티티 타입 (LINE, CIRCLE 등)
        layer: 레이어 이름
        data: 엔티티 속성 딕셔너리
        location: 대표 위치 (x, y)
        parent_block: 상위 블록 이름 (블록 확장 시)
        color: Phase O3 — ACI 색상 코드 (256=BYLAYER, 0=BYBLOCK)
        lineweight: Phase O3 — 선두께 (100ths of mm; -1=BYLAYER 등)
        linetype: Phase O3 — 선종류 이름 ("Continuous", "DASHED", ...)
    """

    hash: str
    entity_type: str
    layer: str
    data: Dict[str, Any]
    location: Tuple[float, float]
    parent_block: Optional[str] = None  # Sprint 10: 블록 내부 비교용
    # Phase O3 — cosmetic 속성 (None = 미설정 / cache 호환)
    color: Optional[int] = None
    lineweight: Optional[int] = None
    linetype: Optional[str] = None

    def __eq__(self, other):
        if not isinstance(other, NormalizedEntity):
            return False
        return self.hash == other.hash

    def __hash__(self):
        return hash(self.hash)


class DxfEntityExtractor:
    """DXF 엔티티 추출 및 정규화

    DXF 파일에서 엔티티를 추출하고 비교 가능한 형태로 정규화합니다.

    사용 예시:
        extractor = DxfEntityExtractor()
        doc = ezdxf.readfile("drawing.dxf")
        entities = extractor.extract(doc)
    """

    # 지원하는 엔티티 타입
    SUPPORTED_TYPES = {
        "LINE",
        "CIRCLE",
        "ARC",
        "LWPOLYLINE",
        "POLYLINE",
        "TEXT",
        "MTEXT",
        "DIMENSION",
        "INSERT",
        # RV-20260508-003 — 블록 attribute 텍스트 변경 (사용자 사례
        # ``DOWEL BAR ... @100 -> @200``) 검출. ATTRIB 는 INSERT 의
        # sub-entity 라 modelspace iterate 시 직접 노출되지 않으므로
        # ``_process_single_entity`` 가 INSERT 처리 후 명시적으로
        # ``insert.attribs`` 를 iterate 함. ATTDEF 는 BLOCK definition
        # 안에 살아 ``_expand_block`` 경로에서 자연스럽게 매칭됨.
        "ATTRIB",
        "ATTDEF",
        # Phase Q1 (RV-20260509-002) — 추출 단계에서 silent drop 되던
        # 6개 entity type 추가. 사용자 미탐지 사례 직접 해소 — HATCH
        # (단면 채움), SOLID (Tekla mass/plate), MULTILEADER (dowel
        # callout 일반 형식), LEADER (구식 화살표), SPLINE (곡선),
        # ELLIPSE (타원).
        "HATCH",
        "SOLID",
        "MULTILEADER",
        "LEADER",
        "SPLINE",
        "ELLIPSE",
    }

    # 좌표 정밀도 (소수점 자릿수)
    PRECISION = 2  # 0.01mm

    # Sprint 11 P2: 메모리 안전을 위한 엔티티 수 제한
    # 동적 제한: 시스템 메모리에 따라 조정
    DEFAULT_MAX_ENTITIES = 50000  # 기본값
    MIN_ENTITIES = 10000  # 최소 (저메모리 시스템)
    MAX_ENTITIES_CEILING = 500000  # 최대 상한 (고용량 시스템)

    # 엔티티당 예상 메모리 사용량 (bytes)
    BYTES_PER_ENTITY = 500  # 약 0.5KB/엔티티

    @staticmethod
    def calculate_dynamic_limit() -> int:
        """시스템 가용 메모리를 기준으로 동적 엔티티 제한 계산

        Returns:
            권장 최대 엔티티 수
        """
        available_mb = None

        # 방법 1: psutil 사용 (우선)
        try:
            import psutil

            available_mb = psutil.virtual_memory().available / (1024 * 1024)
        except ImportError:
            pass

        # 방법 2: Windows ctypes 사용 (psutil 없을 때)
        if available_mb is None:
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                c_ulonglong = ctypes.c_ulonglong

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", c_ulonglong),
                        ("ullAvailPhys", c_ulonglong),
                        ("ullTotalPageFile", c_ulonglong),
                        ("ullAvailPageFile", c_ulonglong),
                        ("ullTotalVirtual", c_ulonglong),
                        ("ullAvailVirtual", c_ulonglong),
                        ("ullAvailExtendedVirtual", c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(stat)
                kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                available_mb = stat.ullAvailPhys / (1024 * 1024)
            except Exception:
                pass

        # 메모리 정보 획득 실패 시 기본값
        if available_mb is None:
            logger.info("메모리 확인 불가, 기본값 200,000개 사용")
            return 200000  # psutil 없으면 넉넉하게 200,000개

        # 가용 메모리의 10%를 엔티티 처리에 할당
        allocate_mb = available_mb * 0.1
        max_entities = int((allocate_mb * 1024 * 1024) / DxfEntityExtractor.BYTES_PER_ENTITY)

        # 범위 내로 제한
        result = max(
            DxfEntityExtractor.MIN_ENTITIES,
            min(max_entities, DxfEntityExtractor.MAX_ENTITIES_CEILING),
        )
        logger.info(f"동적 엔티티 제한: {result:,}개 (가용 메모리: {available_mb:.0f}MB)")
        return result

    def __init__(
        self,
        precision: int = 2,
        max_entities: int = None,
        *,
        block_text_detection: bool = True,
    ):
        """
        Args:
            precision: 좌표 정밀도 (소수점 자릿수)
            max_entities: 최대 처리 엔티티 수 (None이면 동적 계산)
            block_text_detection: Phase O Commit 3 [RV-20260508-009]
                — INSERT block-internal text fingerprint 활성. False
                시 INSERT hash 가 Phase O Commit 1 이전 동작으로
                회귀하여 사용자가 "정밀 텍스트 감지" 를 비활성화
                할 수 있음. 기본값 True (사용자 사례 보호).
        """
        if not EZDXF_AVAILABLE:
            raise ImportError("ezdxf가 필요합니다: pip install ezdxf")
        self.precision = precision
        # None이면 동적으로 계산
        self.max_entities = max_entities or self.calculate_dynamic_limit()
        # Strategy Pattern: NormalizerFactory 인스턴스 생성
        self._normalizer_factory = NormalizerFactory(
            precision=self.precision,
            block_text_detection=block_text_detection,
        )
        self._block_text_detection = bool(block_text_detection)
        self.last_stats: Dict[str, Any] = {}

    def extract(
        self,
        doc: "Drawing",
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
        expand_blocks: bool = True,
        block_recursion_depth: int = 1,
        progress_callback: Optional[callable] = None,
        is_cancelled: Optional[callable] = None,
        extract_all_layouts: bool = True,
    ) -> Dict[str, List[NormalizedEntity]]:
        """DXF 문서에서 모든 엔티티 추출

        Args:
            doc: ezdxf Document 객체
            include_layers: 포함할 레이어 목록 (None이면 전체)
            exclude_layers: 제외할 레이어 목록
            expand_blocks: INSERT 블록 내부 엔티티 확장 여부.
                Phase Q-FU-1 (RV-20260510-001) — default 가 False → True 로
                변경. ``ComparisonConfig.expand_blocks=True`` (Q3 default)
                와 일관시키고 verify pipeline 의 ``extract_from_file()``
                + 기타 no-config caller 도 Q3 block-internal change detect
                혜택을 받도록 함. Phase Q3 까지 ChangeZoneOptions/GUI flow
                만 True 였고 extractor 자체 default 는 False 라 silent drop
                잔존. fixture 11 (block_geometry_change) 가 active 전환됨.
                False 로 명시 전달하면 기존 동작 유지 (backward-compat).
            block_recursion_depth: 블록 재귀 깊이
            progress_callback: 진행률 콜백 함수 (current, total, message) -> None
            is_cancelled: 취소 여부 확인 함수 () -> bool
            extract_all_layouts: Phase Q5 (RV-20260509-002) — paperspace
                레이아웃까지 모두 추출. default True (사용자가 paperspace
                도면을 사용하면 modelspace 만 보던 비교가 silent drop 됐음).
                False 면 modelspace 만 처리 + ``last_stats`` 에 paperspace
                entity 수를 ``paperspace_entities_skipped_count`` 로 surface.

        Returns:
            엔티티 타입별 정규화된 엔티티 목록
            {
                "LINE": [NormalizedEntity, ...],
                "CIRCLE": [...],
                ...
            }
        """
        # Codex P1 [RV-20260508-008] — 동일 extractor 가 여러 doc 에
        # 재사용될 때 InsertNormalizer 의 ``id(doc)`` 캐시가 GC 후
        # 메모리 주소 재사용으로 stale 데이터를 반환할 수 있음. 매
        # extraction 시작 직전에 normalizer 측 doc-scoped 캐시 무효화.
        try:
            self._normalizer_factory.reset_per_extraction_state()
        except Exception:
            # backward-compat: factory 가 hook 미구현이어도 (예: 외부
            # custom factory) 무시 — 캐시 없는 경우와 동등.
            pass
        result = {t: [] for t in self.SUPPORTED_TYPES}
        msp = doc.modelspace()
        total_entities = self._get_total_entities(msp)

        # 추출 컨텍스트 초기화
        ctx = {
            "entity_count": 0,
            "filtered_count": 0,
            "block_count": 0,
            "limit_exceeded": False,
            "loop_count": 0,
            "paperspace_entities_extracted_count": 0,
            "paperspace_entities_skipped_count": 0,
            "paperspace_layouts_processed": [],
        }

        for entity in msp:
            ctx["loop_count"] += 1

            # 진행률 및 취소 확인
            self._report_progress(progress_callback, ctx["loop_count"], total_entities)
            if self._check_cancellation(is_cancelled, ctx["loop_count"]):
                logger.info(f"엔티티 추출 취소됨 (처리: {ctx['loop_count']:,}개)")
                self._set_last_stats(ctx, total_entities, result, cancelled=True)
                return result

            # 엔티티 처리
            should_stop = self._process_single_entity(
                entity, doc, result, ctx,
                include_layers, exclude_layers,
                expand_blocks, block_recursion_depth, is_cancelled
            )
            if should_stop:
                break

        # Phase Q5 (RV-20260509-002) — paperspace 레이아웃 처리.
        # default True: paperspace entity 도 modelspace 결과에 merge 추가.
        # False 면 paperspace entity 수만 카운트해 audit 에 surface.
        self._process_paperspace_layouts(
            doc, result, ctx,
            include_layers=include_layers,
            exclude_layers=exclude_layers,
            expand_blocks=expand_blocks,
            block_recursion_depth=block_recursion_depth,
            is_cancelled=is_cancelled,
            extract_all_layouts=extract_all_layouts,
        )

        self._log_extraction_result(
            ctx["entity_count"], ctx["filtered_count"], ctx["block_count"],
            ctx["limit_exceeded"], result
        )
        self._set_last_stats(ctx, total_entities, result, cancelled=False)
        return result

    def _process_paperspace_layouts(
        self,
        doc: "Drawing",
        result: Dict[str, List[NormalizedEntity]],
        ctx: Dict[str, Any],
        *,
        include_layers: Optional[List[str]],
        exclude_layers: Optional[List[str]],
        expand_blocks: bool,
        block_recursion_depth: int,
        is_cancelled: Optional[callable],
        extract_all_layouts: bool,
    ) -> None:
        """Phase Q5 (RV-20260509-002) — paperspace 레이아웃 처리.

        ezdxf 의 ``doc.layouts`` 는 modelspace 와 paperspace layout 을
        모두 노출. ``doc.modelspace()`` 와 별도로 layout 들을 iterate
        해야 함. 각 paperspace layout 의 entity 를 수집:
        - extract_all_layouts=True: result 에 merge → modelspace 와
          함께 비교 가능.
        - extract_all_layouts=False: 카운터만 증가 (silent drop 가시화).
        """
        # ezdxf 의 layouts.names() 는 modelspace 포함; 'Model' 은 제외.
        try:
            layout_names = list(doc.layouts.names())
        except Exception:
            return
        for layout_name in layout_names:
            if layout_name.lower() in ("model", "*model_space"):
                continue
            try:
                layout = doc.layouts.get(layout_name)
            except Exception:
                continue

            # Phase Q5 Codex follow-up [P1] (RV-20260509-002): paperspace
            # entity 의 hash 가 layout_name 을 포함하지 않으면 같은 좌표
            # 의 entity 가 Layout1 → DETAIL_VIEW 로 이동해도 invisible.
            # ctx 에 _layout_namespace 를 setattr 한 뒤 normalize 후
            # 그 값으로 hash 를 re-key.
            ctx["_current_layout_name"] = layout_name
            try:
                for entity in layout:
                    # 빠른 type check — supported 가 아니면 미리 skip.
                    etype = entity.dxftype()
                    if not extract_all_layouts:
                        # 카운터만 — 실제 추출 안 함.
                        if etype in self.SUPPORTED_TYPES:
                            ctx["paperspace_entities_skipped_count"] = (
                                ctx.get("paperspace_entities_skipped_count", 0) + 1
                            )
                        continue

                    # extract_all_layouts=True — 정상 처리.
                    # _process_single_entity 가 기존 카운터/로직 재사용.
                    ctx["loop_count"] += 1
                    if self._check_cancellation(is_cancelled, ctx["loop_count"]):
                        return
                    # Snapshot per-type lengths BEFORE processing — entity
                    # could expand into many children (expand_blocks=True).
                    pre_lengths = {t: len(v) for t, v in result.items()}
                    pre_count = ctx["entity_count"]
                    # Phase Q5 Codex follow-up [P2]: _process_single_entity
                    # 가 True 반환 시 max_entities 한계/cancel — paperspace
                    # 루프도 즉시 중단해야 modelspace 와 동작 일치.
                    should_stop = self._process_single_entity(
                        entity, doc, result, ctx,
                        include_layers, exclude_layers,
                        expand_blocks, block_recursion_depth, is_cancelled,
                    )
                    if ctx["entity_count"] > pre_count:
                        ctx["paperspace_entities_extracted_count"] = (
                            ctx.get("paperspace_entities_extracted_count", 0) + 1
                        )
                    # P1 fix: 새로 추가된 모든 entity 의 hash 에 layout_name
                    # 을 namespace 로 추가해 layout 이동/추가/삭제 detect.
                    self._tag_paperspace_entities(
                        result, pre_lengths, layout_name,
                    )
                    if should_stop:
                        return
            finally:
                ctx.pop("_current_layout_name", None)

            if extract_all_layouts:
                ctx["paperspace_layouts_processed"].append(layout_name)

    def _tag_paperspace_entities(
        self,
        result: Dict[str, List[NormalizedEntity]],
        pre_lengths: Dict[str, int],
        layout_name: str,
    ) -> None:
        """Phase Q5 Codex follow-up [P1] (RV-20260509-002) — 직전
        ``_process_single_entity`` 호출로 추가된 entity 들의 hash 에
        ``layout_name`` 을 namespace 접미로 추가하고 ``data`` 에
        ``_paperspace_layout`` 키를 설정한다.

        ``pre_lengths`` 와 현재 길이 차이로 새 entity 의 정확한 범위 식별.
        ``NormalizedEntity`` 가 frozen 이면 ``dataclasses.replace`` 로
        교체, 아니면 in-place 수정.
        """
        import dataclasses

        for etype, entities in result.items():
            old_len = pre_lengths.get(etype, 0)
            new_len = len(entities)
            if new_len <= old_len:
                continue
            for i in range(old_len, new_len):
                ent = entities[i]
                # 이미 tagged 면 skip (방어적 — 정상 흐름에서는 fresh)
                if (getattr(ent, "data", {}) or {}).get(
                    "_paperspace_layout"
                ):
                    continue
                new_hash = (
                    f"{ent.hash}:PSL:{layout_name}"
                    if ent.hash else f"PSL:{layout_name}"
                )
                try:
                    new_data = dict(ent.data or {})
                    new_data["_paperspace_layout"] = layout_name
                    entities[i] = dataclasses.replace(
                        ent, hash=new_hash, data=new_data,
                    )
                except Exception:
                    try:
                        ent.hash = new_hash
                        if isinstance(ent.data, dict):
                            ent.data["_paperspace_layout"] = layout_name
                    except Exception:
                        pass

    def _get_total_entities(self, modelspace) -> Optional[int]:
        """ModelSpace의 총 엔티티 수 반환

        Args:
            modelspace: ezdxf ModelSpace 객체

        Returns:
            엔티티 수 또는 None
        """
        try:
            return len(modelspace)
        except (TypeError, AttributeError):
            return None

    def _set_last_stats(
        self,
        ctx: Dict[str, Any],
        total_entities: Optional[int],
        result: Dict[str, List[NormalizedEntity]],
        cancelled: bool = False,
    ) -> None:
        """Store lightweight extraction telemetry for UI/reporting."""
        extracted_count = sum(len(entities) for entities in result.values())
        # Phase Q1 (RV-20260509-002) — 미지원 entity 종류 카운트 가시화.
        # 사용자가 "왜 변경이 안 보이는지" 진단할 때 어느 종류가 silent
        # drop 됐는지 즉시 확인.
        unsupported_counts = dict(ctx.get("unsupported_counts", {}))
        unsupported_total = sum(unsupported_counts.values())
        self.last_stats = {
            "processed_count": ctx.get("loop_count", 0),
            "filtered_count": ctx.get("filtered_count", 0),
            "extracted_count": extracted_count,
            "direct_entity_count": ctx.get("entity_count", 0),
            "block_count": ctx.get("block_count", 0),
            "limit_exceeded": bool(ctx.get("limit_exceeded", False)),
            "cancelled": bool(cancelled),
            "max_entities": self.max_entities,
            "total_entities": total_entities,
            "memory_estimate": extracted_count * self.BYTES_PER_ENTITY,
            "unsupported_counts": unsupported_counts,
            "unsupported_total": unsupported_total,
            # Phase Q3 (RV-20260509-002) — INSERT 가 expand_blocks=False 로
            # 처리되어 block 정의 geometry (LINE/CIRCLE/...) 가 미추출된
            # 횟수. block_text fingerprint 만 비교에 반영되었음을 audit 가
            # surface 가능하도록.
            "block_geometry_skipped_count": int(
                ctx.get("block_geometry_skipped_count", 0)
            ),
            # Phase Q-FU-1 round-1 [Codex P2-3] — block 확장 중
            # max_entities 도달로 truncation 된 시점 카운터. ``limit_exceeded``
            # 와 함께 silent truncation 감지에 사용.
            "block_truncated_count": int(
                ctx.get("block_truncated_count", 0)
            ),
            # Phase Q5 (RV-20260509-002) — paperspace 추출 통계.
            # extracted_count: 사용자에게 보일 paperspace entity 수.
            # skipped_count: extract_all_layouts=False 일 때만 비-zero.
            "paperspace_entities_extracted_count": int(
                ctx.get("paperspace_entities_extracted_count", 0)
            ),
            "paperspace_entities_skipped_count": int(
                ctx.get("paperspace_entities_skipped_count", 0)
            ),
            "paperspace_layouts_processed": list(
                ctx.get("paperspace_layouts_processed", [])
            ),
        }

    def _process_single_entity(
        self,
        entity,
        doc: "Drawing",
        result: Dict[str, List[NormalizedEntity]],
        ctx: Dict[str, Any],
        include_layers: Optional[List[str]],
        exclude_layers: Optional[List[str]],
        expand_blocks: bool,
        block_recursion_depth: int,
        is_cancelled: Optional[callable],
    ) -> bool:
        """단일 엔티티 처리

        Args:
            entity: 처리할 엔티티
            doc: ezdxf Document
            result: 결과 딕셔너리
            ctx: 추출 컨텍스트 (카운터들)
            include_layers: 포함 레이어
            exclude_layers: 제외 레이어
            expand_blocks: 블록 확장 여부
            block_recursion_depth: 블록 재귀 깊이
            is_cancelled: 취소 확인 함수

        Returns:
            True if 루프 중단해야 함
        """
        # 제한 체크
        should_break, ctx["limit_exceeded"] = self._check_entity_limit(
            ctx["entity_count"], ctx["block_count"], ctx["limit_exceeded"]
        )
        if should_break:
            return True

        etype = entity.dxftype()
        layer = entity.dxf.layer

        # 레이어 필터링 — INSERT 는 sub-ATTRIB 가 별도 layer 일 수
        # 있어 (예: INSERT=BEAM_LAYER, ATTRIB=TEXT_LAYER) 부모 INSERT
        # 가 필터에 안 걸려도 ATTRIB 는 통과할 수 있어야 한다는 것이
        # Codex P2 finding [RV-20260508-004] 의 요지. 따라서 INSERT
        # 는 일찍 skip 하지 않고 sub-attribs 만 별도 처리한다.
        skip = self._should_skip_layer(layer, include_layers, exclude_layers)
        if skip and etype != "INSERT":
            ctx["filtered_count"] += 1
            return False
        if skip and etype == "INSERT":
            # 부모 INSERT 자체는 추출하지 않되 (filtered_count 증가),
            # ATTRIB 는 자체 layer 기준으로 다시 평가해서 추출.
            ctx["filtered_count"] += 1
            self._extract_insert_attribs(
                entity, result, ctx,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
            )
            return False

        # INSERT 블록 확장 처리 (expand_blocks=True 경로 — 블록 정의
        # geometry 까지 모두 펼침; 무거움)
        if etype == "INSERT" and expand_blocks:
            # Phase Q3 Codex follow-up [P1] (RV-20260509-002):
            # 이전에는 expand 경로에서 children 만 추가하고 parent INSERT
            # 자체는 result["INSERT"] 에 넣지 않았음. 결과: xscale/
            # yscale/rotation 만 변경된 케이스 — 예: xscale 1.0→2.0 —
            # 가 expand 경로에서 silent drop. children 의 hash 는
            # block 정의 좌표 + offset 으로 계산되어 transform 변화
            # 반영 안 됨. parent INSERT 도 함께 추가.
            #
            # Phase Q3 Codex round-2 follow-up [P2]: parent INSERT 는
            # ``fingerprint_mode="transform_only"`` 로 normalize. 그렇지
            # 않으면 block 정의 내 TEXT/ATTDEF 변경이 (a) 펼쳐진 children
            # entity 변경 + (b) parent INSERT block_text_fingerprint 차이
            # 로 double-count. transform-only mode 는 block_name +
            # insert_point + xscale + yscale + rotation 만 hash 해서
            # transform 변화는 detect 하면서 text 변경 중복 보고는 차단.
            #
            # Phase Q3 Codex round-3 follow-up [P2]: 단,
            # ``block_recursion_depth <= 0`` 케이스는 children 이
            # emit 되지 않으므로 transform_only mode 가 되면 block-
            # internal TEXT/ATTDEF 변경이 silent drop. 이 한 케이스에
            # 한해 full mode 사용 (legacy 동작 보존).
            insert_normalizer = self._normalizer_factory.get_normalizer("INSERT")
            fp_mode = "full" if block_recursion_depth <= 0 else "transform_only"
            try:
                normalized_parent = insert_normalizer.normalize(
                    entity, fingerprint_mode=fp_mode
                )
            except TypeError:
                # backward-compat — custom factory 가 fingerprint_mode 미지원
                normalized_parent = self._normalize(entity)
            if normalized_parent:
                result["INSERT"].append(normalized_parent)
                ctx["entity_count"] += 1
            new_block_count = self._process_block_expansion(
                doc, entity, block_recursion_depth, is_cancelled,
                result, ctx,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
            )
            if new_block_count < 0:
                return True  # 취소됨
            ctx["block_count"] = new_block_count
            # RV-20260508-003 — expand 경로에서도 INSERT 의 sub-ATTRIB
            # 을 추출. _expand_block 은 블록 정의 entity (TEXT/ATTDEF
            # 등) 만 펼치고, INSERT instance 가 가진 ATTRIB 는 별도.
            # Codex P2 — include/exclude 필터를 ATTRIB layer 에도 적용.
            self._extract_insert_attribs(
                entity, result, ctx,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
            )
            return False

        # 지원 타입 확인 및 정규화
        if etype in self.SUPPORTED_TYPES:
            normalized = self._normalize(entity)
            if normalized:
                result[etype].append(normalized)
                ctx["entity_count"] += 1

            # RV-20260508-003 — INSERT 면 sub-ATTRIB 도 함께 추출.
            # expand_blocks=False (default) 경로에서도 ATTRIB 를 놓치
            # 지 않게 하려는 핵심 hook. 사용자 사례 (DOWEL BAR @100 ->
            # @200) 가 이 경로에서 검출됨.
            if etype == "INSERT":
                self._extract_insert_attribs(
                    entity, result, ctx,
                    include_layers=include_layers,
                    exclude_layers=exclude_layers,
                )
                # Phase Q3 (RV-20260509-002) — expand_blocks=False 일 때
                # INSERT 의 block 정의 geometry (LINE/CIRCLE/POLYLINE 등)
                # 가 silent drop. text fingerprint 는 InsertNormalizer 가
                # 처리하지만 geometry 변경은 expand_blocks 경로 책임이라
                # caller 가 disable 했음을 audit 에 surface 한다.
                if not expand_blocks:
                    ctx["block_geometry_skipped_count"] = (
                        ctx.get("block_geometry_skipped_count", 0) + 1
                    )
        else:
            # Phase Q1 (RV-20260509-002) — 미지원 entity 종류 가시화.
            # 이전에는 silent drop 으로 사용자가 "왜 안 보이는지" 알
            # 수 없었음. unsupported_counts 카운터에 누적해 last_stats
            # / GUI 진단에서 surface 가능하도록.
            unsupported = ctx.setdefault("unsupported_counts", {})
            unsupported[etype] = unsupported.get(etype, 0) + 1

        return False

    def _extract_insert_attribs(
        self,
        insert_entity,
        result: Dict[str, List[NormalizedEntity]],
        ctx: Dict[str, Any],
        *,
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
    ) -> None:
        """RV-20260508-003 — INSERT 의 visible attribute (ATTRIB) 추출.

        ezdxf 의 ``INSERT.attribs`` 는 list-like; 각 ATTRIB 는 modelspace
        좌표 (INSERT 변환 적용 후) 와 (tag, text) 를 가짐. 사용자가
        본 ``@100 -> @200`` 케이스가 여기서 발생.

        ATTRIB 자체 layer 가 INSERT 의 layer 와 다를 수 있으므로 (예:
        INSERT 는 BEAM_LAYER, ATTRIB 는 TEXT_LAYER) include/exclude
        필터를 ATTRIB 의 layer 에 직접 적용해야 함 — Codex review
        finding P2 [RV-20260508-004].

        실패는 silent — 비정상 INSERT 라도 다른 추출은 계속.
        """
        try:
            attribs = getattr(insert_entity, "attribs", None)
            if not attribs:
                return
        except Exception:
            return

        block_name = ""
        try:
            block_name = insert_entity.dxf.name or ""
        except Exception:
            pass

        # CRITICAL [RV-20260508-011] — parent INSERT 의 modelspace 좌표
        # + scale/rotation 추출. ATTRIB hash 충돌 방지 + 정확한 modelspace
        # 위치 산출의 핵심 입력 (Codex P2 [RV-20260508-012]).
        parent_insert_point: Tuple[float, float] = (0.0, 0.0)
        parent_xscale = 1.0
        parent_yscale = 1.0
        parent_rotation_deg = 0.0
        try:
            raw_pt = insert_entity.dxf.insert
            if hasattr(raw_pt, "x"):
                parent_insert_point = (round(raw_pt.x, 1), round(raw_pt.y, 1))
            else:
                parent_insert_point = (round(raw_pt[0], 1), round(raw_pt[1], 1))
        except Exception:
            pass
        try:
            parent_xscale = float(insert_entity.dxf.xscale)
            parent_yscale = float(insert_entity.dxf.yscale)
            parent_rotation_deg = float(insert_entity.dxf.rotation) % 360.0
        except Exception:
            pass

        from .entity_normalizers import AttribNormalizer

        for attrib in attribs:
            # Codex P2 [RV-20260508-005] — entity cap 우회 방지. 한
            # INSERT 가 수십 개의 ATTRIB 를 가질 수 있어 (장비 리스트
            # 블록 등) 매 ATTRIB 마다 limit 체크 필수. 초과 시 즉시
            # 중단하고 limit_exceeded 플래그 set.
            should_break, ctx["limit_exceeded"] = self._check_entity_limit(
                ctx.get("entity_count", 0), ctx.get("block_count", 0),
                ctx.get("limit_exceeded", False),
            )
            if should_break:
                return

            # Per-attrib layer filter (Codex P2 finding)
            try:
                attrib_layer = attrib.dxf.layer
            except Exception:
                attrib_layer = ""
            if attrib_layer and self._should_skip_layer(
                attrib_layer, include_layers, exclude_layers
            ):
                ctx["filtered_count"] = ctx.get("filtered_count", 0) + 1
                continue

            try:
                normalized = self._normalize(attrib)
            except Exception as exc:
                logger.debug("ATTRIB 정규화 실패 (block=%s): %s", block_name, exc)
                continue
            if normalized is None:
                continue
            # CRITICAL [RV-20260508-011] — parent INSERT 의 modelspace
            # 좌표 + block 이름을 결합하여 hash 재계산. 동일 block 의
            # 여러 INSERT 인스턴스에서 ATTRIB hash 가 충돌하지 않도록.
            # 이 단계 없이는 사용자가 본 dowel callout 시나리오 (같은
            # 블록 다수 사용) 에서 변경이 잘못된 callout 에 attribute
            # 됨. Codex P2 [RV-20260508-012] — scale/rotation 도 전달
            # 하여 ATTRIB modelspace location 정확 산출.
            AttribNormalizer.rehash_with_parent_context(
                normalized, block_name or "", parent_insert_point,
                parent_xscale=parent_xscale,
                parent_yscale=parent_yscale,
                parent_rotation_deg=parent_rotation_deg,
            )
            result["ATTRIB"].append(normalized)
            ctx["entity_count"] += 1

    def _process_block_expansion(
        self,
        doc: "Drawing",
        entity,
        block_recursion_depth: int,
        is_cancelled: Optional[callable],
        result: Dict[str, List[NormalizedEntity]],
        ctx: Dict[str, Any],
        *,
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
    ) -> int:
        """INSERT 블록 확장 처리

        Phase Q-FU-1 round-1 [Codex P2-1/P2-2/P2-3] 수정:
        - include/exclude layer 필터를 block 내부 child entity layer 에
          적용 (P2-1) — 부모 INSERT layer 가 통과해도 block 내부의
          제외 layer entity 는 result 에 들어가지 않아야 함.
        - ``self._block_text_detection=False`` 일 때 TEXT/MTEXT/ATTDEF
          를 expansion 경로에서도 skip (P2-2) — block_text_detection
          opt-out 가 expand_blocks=True default 에서도 일관 동작.
        - block_count 가 max_entities 도달 시 즉시 ctx['limit_exceeded']
          set (P2-3) — silent truncation 방지.

        Returns:
            업데이트된 block_count (취소 시 -1 반환)
        """
        block_entities = self._expand_block(
            doc, entity, depth=block_recursion_depth, is_cancelled=is_cancelled,
            include_layers=include_layers,
            exclude_layers=exclude_layers,
            skip_text_types=not self._block_text_detection,
        )

        # 블록 확장 중 취소 감지
        if is_cancelled:
            try:
                if is_cancelled():
                    loop_count = ctx.get("loop_count", 0)
                    logger.info(f"엔티티 추출 취소됨 (블록 확장 중, 처리: {loop_count:,}개)")
                    return -1
            except Exception:
                pass

        entity_count = ctx.get("entity_count", 0)
        block_count = ctx.get("block_count", 0)
        for be in block_entities:
            if entity_count + block_count >= self.max_entities:
                # Codex P2-3 — block 내 truncation 발생 시 last_stats
                # 에 surface. 이전에는 후속 top-level entity 가
                # 한도 체크를 다시 trigger 해야만 limit_exceeded 가
                # True 가 되어 silent truncation 발생.
                if not ctx.get("limit_exceeded", False):
                    logger.warning(
                        "블록 확장 중 엔티티 수 제한 초과 — 최대 "
                        f"{self.max_entities:,}개. block_truncated_count "
                        "에 누적합니다."
                    )
                ctx["limit_exceeded"] = True
                ctx["block_truncated_count"] = (
                    ctx.get("block_truncated_count", 0) + 1
                )
                break
            result[be.entity_type].append(be)
            block_count += 1

        return block_count

    def extract_from_file(self, dxf_path: Path) -> Dict[str, List[NormalizedEntity]]:
        """DXF 파일에서 엔티티 추출

        Args:
            dxf_path: DXF 파일 경로

        Returns:
            엔티티 타입별 정규화된 엔티티 목록
        """
        doc = ezdxf.readfile(str(dxf_path))
        return self.extract(doc)

    def get_layers(self, doc: "Drawing") -> List[str]:
        """DXF 문서의 모든 레이어 이름 반환

        Args:
            doc: ezdxf Document 객체

        Returns:
            레이어 이름 목록
        """
        return sorted([layer.dxf.name for layer in doc.layers])

    def get_entity_layers(self, doc: "Drawing") -> List[str]:
        """실제 엔티티가 있는 레이어만 반환

        Codex P2 [RV-20260508-005] — INSERT 의 sub-ATTRIB 가 별도
        레이어에 있을 수 있어 (예: INSERT=BEAM_LAYER, ATTRIB=
        TEXT_LAYER) UI 의 layer-filter 콤보가 ATTRIB-only 레이어를
        제공하려면 ``insert.attribs`` 도 visit 해야 함. 그렇지 않으면
        text 레이어만 필터링하려는 사용자가 그 레이어를 선택지에서
        볼 수 없음.

        Phase Q5 Codex follow-up [P2] (RV-20260509-002): paperspace
        레이아웃의 entity 가 default 비교에 포함되므로, layer 필터 콤보
        가 paperspace-only 레이어 (예: title block 의 SHEETBORDER) 를
        반드시 노출해야 함. modelspace + 모든 paperspace layout 을
        union 으로 수집.

        Args:
            doc: ezdxf Document 객체

        Returns:
            엔티티가 존재하는 레이어 이름 목록
        """
        layers = set()
        spaces = [doc.modelspace()]
        try:
            for layout_name in doc.layouts.names():
                if layout_name.lower() in ("model", "*model_space"):
                    continue
                try:
                    spaces.append(doc.layouts.get(layout_name))
                except Exception:
                    continue
        except Exception:
            # paperspace iter 실패해도 modelspace 는 보존
            pass

        for space in spaces:
            for entity in space:
                if hasattr(entity.dxf, "layer"):
                    layers.add(entity.dxf.layer)
                # ATTRIB layers (INSERT 자체와 다를 수 있음)
                if entity.dxftype() == "INSERT":
                    try:
                        attribs = getattr(entity, "attribs", None) or []
                        for a in attribs:
                            try:
                                layers.add(a.dxf.layer)
                            except Exception:
                                continue
                    except Exception:
                        continue
        return sorted(layers)

    def get_layouts(self, doc: "Drawing") -> List[str]:
        """모든 레이아웃 이름 반환 (Model 제외)

        Args:
            doc: ezdxf Document 객체

        Returns:
            레이아웃(Paper Space) 이름 목록
        """
        return [layout.name for layout in doc.layouts if layout.name != "Model"]

    def extract_layout(
        self,
        doc: "Drawing",
        layout_name: str,
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
    ) -> Dict[str, List[NormalizedEntity]]:
        """특정 레이아웃(Paper Space)에서 엔티티 추출

        Args:
            doc: ezdxf Document 객체
            layout_name: 레이아웃 이름
            include_layers: 포함할 레이어 목록
            exclude_layers: 제외할 레이어 목록

        Returns:
            엔티티 타입별 정규화된 엔티티 목록
        """
        # Codex P1 [RV-20260508-008] — block fingerprint cache 무효화
        try:
            self._normalizer_factory.reset_per_extraction_state()
        except Exception:
            pass

        result = {t: [] for t in self.SUPPORTED_TYPES}

        try:
            layout = doc.layouts.get(layout_name)
        except KeyError:
            logger.warning(f"레이아웃을 찾을 수 없음: {layout_name}")
            return result

        # Codex P2 [RV-20260508-004] — paper-space 비교 (DwgDiffer.
        # compare_layouts) 도 modelspace 와 동일한 ATTRIB 검출이
        # 가능해야 하므로 ``ctx`` 셰이프를 ``_extract_insert_attribs``
        # 와 호환되게 mock 으로 구성하고 INSERT 처리 후 호출.
        ctx: Dict[str, Any] = {"entity_count": 0, "filtered_count": 0}

        for entity in layout:
            etype = entity.dxftype()
            if etype not in self.SUPPORTED_TYPES:
                continue

            # 레이어 필터링 — INSERT 는 sub-ATTRIB 가 별도 layer 일 수
            # 있으므로 부모 layer skip 시에도 ATTRIB 추출은 시도 (Codex
            # P2 finding [RV-20260508-004]).
            layer = entity.dxf.layer
            skip = (include_layers and layer not in include_layers) or (
                exclude_layers and layer in exclude_layers
            )
            if skip and etype != "INSERT":
                ctx["filtered_count"] += 1
                continue
            if skip and etype == "INSERT":
                ctx["filtered_count"] += 1
                self._extract_insert_attribs(
                    entity, result, ctx,
                    include_layers=include_layers,
                    exclude_layers=exclude_layers,
                )
                continue

            normalized = self._normalize(entity)
            if normalized:
                result[etype].append(normalized)
                ctx["entity_count"] += 1

            # RV-20260508-003 + Codex P2 — INSERT sub-ATTRIB 추출 (
            # paper-space 도면도 DOWEL @100 -> @200 검출).
            if etype == "INSERT":
                self._extract_insert_attribs(
                    entity, result, ctx,
                    include_layers=include_layers,
                    exclude_layers=exclude_layers,
                )

        logger.info(
            f"레이아웃 '{layout_name}' 엔티티 추출: {ctx['entity_count']}개 "
            f"(필터링 제외: {ctx['filtered_count']}개)"
        )
        return result

    # =========================================================================
    # extract() 헬퍼 메서드 (P1-2: 복잡도 감소)
    # =========================================================================

    def _check_entity_limit(
        self,
        entity_count: int,
        block_count: int,
        limit_exceeded: bool,
    ) -> Tuple[bool, bool]:
        """엔티티 수 제한 체크

        Args:
            entity_count: 현재 추출된 엔티티 수
            block_count: 블록에서 추출된 엔티티 수
            limit_exceeded: 이미 제한 초과 여부

        Returns:
            (should_break, new_limit_exceeded) 튜플
        """
        total_count = entity_count + block_count
        if total_count >= self.max_entities:
            if not limit_exceeded:
                logger.warning(
                    f"엔티티 수 제한 초과! 최대 {self.max_entities}개까지만 처리합니다. "
                    "메모리 보호를 위해 중단합니다."
                )
            return True, True
        return False, limit_exceeded

    def _report_progress(
        self,
        progress_callback: Optional[callable],
        loop_count: int,
        total_entities: Optional[int],
        interval: int = 500,
    ) -> None:
        """진행률 보고

        Args:
            progress_callback: 콜백 함수
            loop_count: 현재 루프 카운터
            total_entities: 전체 엔티티 수 (None이면 메시지 전용)
            interval: 보고 간격
        """
        if not progress_callback or loop_count % interval != 0:
            return

        try:
            if total_entities:
                progress_callback(
                    loop_count,
                    total_entities,
                    f"엔티티 추출 중... ({loop_count:,}/{total_entities:,}개)",
                )
            else:
                progress_callback(
                    loop_count,
                    0,
                    f"엔티티 추출 중... ({loop_count:,}개)",
                )
        except Exception:
            pass

    def _check_cancellation(
        self,
        is_cancelled: Optional[callable],
        loop_count: int,
        interval: int = 1000,
    ) -> bool:
        """취소 여부 확인

        Args:
            is_cancelled: 취소 확인 함수
            loop_count: 현재 루프 카운터
            interval: 체크 간격

        Returns:
            True if 취소됨
        """
        if not is_cancelled or loop_count % interval != 0:
            return False

        try:
            return is_cancelled()
        except Exception:
            return False

    def _should_skip_layer(
        self,
        layer: str,
        include_layers: Optional[List[str]],
        exclude_layers: Optional[List[str]],
    ) -> bool:
        """레이어 필터링 체크

        Args:
            layer: 엔티티 레이어 이름
            include_layers: 포함 레이어 목록
            exclude_layers: 제외 레이어 목록

        Returns:
            True if 건너뛰어야 함
        """
        if include_layers and layer not in include_layers:
            return True
        if exclude_layers and layer in exclude_layers:
            return True
        return False

    def _log_extraction_result(
        self,
        entity_count: int,
        filtered_count: int,
        block_count: int,
        limit_exceeded: bool,
        result: Dict[str, List[NormalizedEntity]],
    ) -> None:
        """추출 결과 로깅

        Args:
            entity_count: 추출된 엔티티 수
            filtered_count: 필터링된 엔티티 수
            block_count: 블록에서 추출된 엔티티 수
            limit_exceeded: 제한 초과 여부
            result: 결과 딕셔너리
        """
        if limit_exceeded:
            logger.warning(
                f"[P2 메모리 가드] 처리된 엔티티: {entity_count + block_count}개 / 제한: {self.max_entities}개"
            )
        else:
            logger.info(
                f"DXF 엔티티 추출 완료: {entity_count}개 "
                f"(필터링: {filtered_count}개, 블록 확장: {block_count}개)"
            )

        for etype, entities in result.items():
            if entities:
                logger.debug(f"  {etype}: {len(entities)}개")

    # Phase Q-FU-1 round-1 [Codex P2-2] — block_text_detection=False
    # 일 때 expansion 경로에서 skip 해야 하는 text-bearing entity types.
    _TEXT_BEARING_TYPES = frozenset({"TEXT", "MTEXT", "ATTDEF", "ATTRIB"})

    @staticmethod
    def _effective_block_child_layer(
        child_layer: str, parent_effective_layer: str
    ) -> str:
        """Phase Q-FU-1 round-2 [Codex P2-NEW-1] — DXF BYBLOCK 의미 보존.

        DXF block 정의 내부 entity 의 layer 가 ``"0"`` 인 경우는 표준
        DXF 의 BYBLOCK 의미 — 실제 표시 layer 는 부모 INSERT 의 visible
        layer 를 따른다. 따라서 layer/include/exclude 필터링 시
        ``"0"`` 을 그대로 비교하면 BEAM_LAYER 에 INSERT 된 block 의
        layer-0 LINE 이 ``include_layers=['BEAM_LAYER']`` 에서 false
        negative drop 됨. 효과적인 layer 를 산출해 비교에 사용한다.

        - layer == "" or "0": 부모 INSERT 의 effective layer 사용
        - else: 명시적 layer 그대로 사용 (BYBLOCK 무관)
        """
        if not child_layer or child_layer == "0":
            return parent_effective_layer
        return child_layer

    def _expand_block(
        self,
        doc: "Drawing",
        insert_entity,
        depth: int = 1,
        parent_name: Optional[str] = None,
        is_cancelled: Optional[callable] = None,
        *,
        include_layers: Optional[List[str]] = None,
        exclude_layers: Optional[List[str]] = None,
        skip_text_types: bool = False,
        parent_effective_layer: Optional[str] = None,
    ) -> List[NormalizedEntity]:
        """INSERT 블록 내부 엔티티 추출

        Phase Q-FU-1 round-1 [Codex P2-1/P2-2] 수정:
        - include/exclude layer 를 child entity layer 에 적용 (P2-1).
        - skip_text_types=True 시 TEXT/MTEXT/ATTDEF skip — caller
          가 ``block_text_detection=False`` 명시했을 때 호출 (P2-2).

        Phase Q-FU-1 round-2 [Codex P2-NEW-1/P2-NEW-2] 추가 수정:
        - layer-0 BYBLOCK 의미 보존: child layer 가 "0" / 비어있을 때
          부모 INSERT 의 effective layer 로 비교 (P2-NEW-1).
        - 중첩 INSERT recursion 전에 layer 필터 적용: 제외된 nested
          INSERT 가 children 을 emit 하지 않도록 (P2-NEW-2).
        - ``parent_effective_layer`` 매번 재귀 단계에서 갱신 — 다단계
          중첩에서도 BYBLOCK 의미 일관 적용.

        Args:
            doc: ezdxf Document 객체
            insert_entity: INSERT 엔티티
            depth: 재귀 깊이 (0이면 중단)
            parent_name: 상위 블록 이름
            is_cancelled: 취소 확인 함수 () -> bool
            include_layers: 포함 layer 화이트리스트 (Codex P2-1)
            exclude_layers: 제외 layer 블랙리스트 (Codex P2-1)
            skip_text_types: True 시 block 내부 TEXT/MTEXT/ATTDEF skip
                — block_text_detection opt-out 일관 동작 (Codex P2-2)
            parent_effective_layer: 부모 INSERT 의 effective layer
                (recursion 다단계에서 BYBLOCK 의미 전파; None 이면
                ``insert_entity.dxf.layer`` 로 초기화)

        Returns:
            블록 내부 정규화된 엔티티 목록
        """
        if depth <= 0:
            return []

        # 취소 확인
        if is_cancelled and is_cancelled():
            return []

        block_name = insert_entity.dxf.name
        full_name = f"{parent_name}/{block_name}" if parent_name else block_name

        try:
            block = doc.blocks.get(block_name)
        except KeyError:
            logger.warning(f"블록을 찾을 수 없음: {block_name}")
            return []

        result = []
        insert_point = self._round_point(insert_entity.dxf.insert)
        xscale = insert_entity.dxf.xscale
        yscale = insert_entity.dxf.yscale
        rotation = insert_entity.dxf.rotation

        # Codex P2-NEW-1 — 부모 INSERT 자신의 effective layer 산출.
        # top-level 호출 시 parent_effective_layer=None 이므로
        # insert_entity.dxf.layer 사용. 재귀 시 caller 가 명시적으로
        # nested INSERT 의 effective layer 전달.
        try:
            insert_own_layer = insert_entity.dxf.layer or ""
        except Exception:
            insert_own_layer = ""
        if parent_effective_layer is None:
            this_effective_layer = insert_own_layer
        else:
            # nested INSERT 자신이 layer "0" 이면 그 부모 effective layer
            # 를 따른다 (BYBLOCK chain).
            this_effective_layer = self._effective_block_child_layer(
                insert_own_layer, parent_effective_layer
            )

        # 블록 내부 취소 체크 간격
        BLOCK_CANCEL_INTERVAL = 100
        block_loop_count = 0

        for entity in block:
            block_loop_count += 1

            # 취소 확인 (N개마다 - 성능 최적화)
            if is_cancelled and block_loop_count % BLOCK_CANCEL_INTERVAL == 0:
                if is_cancelled():
                    return result

            etype = entity.dxftype()

            # Codex P2-NEW-2 — child 의 effective layer 를 먼저 산출.
            # nested INSERT recursion 전에 layer 필터를 적용하기 위해.
            try:
                raw_child_layer = entity.dxf.layer or ""
            except Exception:
                raw_child_layer = ""
            child_effective_layer = self._effective_block_child_layer(
                raw_child_layer, this_effective_layer
            )

            # 재귀 블록 확장 — 자식 INSERT 도 같은 필터 + skip_text 적용
            # Codex round-2 P2-NEW-2: recursion *전*에 nested INSERT 자신의
            # effective layer 검증.
            # Codex round-3 [P2-NEW3-1]: pre-recursion check 는 *exclude
            # 만* 적용. include_layers 는 nested children 이 다른 layer
            # 로 명시될 수 있으므로 — 예: 부모 INSERT layer=AUX,
            # block 안에 LINE layer=BEAM_LAYER, include_layers=[BEAM_LAYER]
            # → 부모 INSERT 는 통과 못해도 LINE 은 통과해야 함.
            if etype == "INSERT" and depth > 1:
                if (exclude_layers
                        and child_effective_layer
                        and child_effective_layer in exclude_layers):
                    # nested INSERT 자체가 명시적으로 exclude → 자손까지 skip
                    continue
                # include_layers 만 있는 경우 (또는 둘 다) recursion 진입.
                # children 의 layer 는 _expand_block 안에서 다시 검증됨.
                result.extend(self._expand_block(
                    doc, entity, depth - 1, full_name, is_cancelled,
                    include_layers=include_layers,
                    exclude_layers=exclude_layers,
                    skip_text_types=skip_text_types,
                    parent_effective_layer=child_effective_layer,
                ))
                continue

            if etype not in self.SUPPORTED_TYPES:
                continue

            # Codex P2-2 — block_text_detection=False 시 text-bearing
            # entity skip. expand_blocks=True default 가 도입되기 전에는
            # 이 toggle 이 INSERT 의 block_text_fingerprint 만 차단했지만,
            # default flip 후에는 expanded TEXT/MTEXT 도 같이 차단해야
            # 일관됨.
            if skip_text_types and etype in self._TEXT_BEARING_TYPES:
                continue

            # Codex P2-1 + P2-NEW-1 — child entity 의 effective layer 로
            # 필터. layer "0" / 비어있는 child 는 부모 effective layer 로
            # 평가 (BYBLOCK semantics). 명시 layer (e.g. "IGNORE_LAYER")
            # 는 그대로 비교.
            if child_effective_layer and self._should_skip_layer(
                child_effective_layer, include_layers, exclude_layers
            ):
                continue

            normalized = self._normalize(entity)
            if normalized:
                # Codex round-3 [P2-NEW3-2] — BYBLOCK child 가 부모
                # effective layer 를 상속해 필터를 통과한 경우, 다운
                # 스트림 (by_layer 통계, priority, structural threshold)
                # 도 effective layer 를 사용하도록 ``layer`` 를 override.
                # 그렇지 않으면 BEAM 변경이 "layer 0" 변경으로 보고되어
                # priority/threshold 가 잘못 적용됨. raw_child_layer 가
                # 명시적이면 (예: "IGNORE_LAYER") override 하지 않음.
                if (raw_child_layer in ("", "0")
                        and child_effective_layer
                        and child_effective_layer != raw_child_layer):
                    try:
                        normalized.layer = child_effective_layer
                    except Exception:
                        pass
                # 블록 변환 적용 (위치 오프셋)
                transformed = self._transform_for_block(
                    normalized, insert_point, xscale, yscale, rotation, full_name
                )
                result.append(transformed)

        return result

    def _transform_for_block(
        self,
        entity: NormalizedEntity,
        offset: Tuple[float, float],
        xscale: float,
        yscale: float,
        rotation: float,
        block_name: str,
    ) -> NormalizedEntity:
        """블록 삽입점 기준으로 좌표 변환

        Args:
            entity: 원본 엔티티
            offset: 블록 삽입점
            xscale, yscale: 스케일
            rotation: 회전 각도
            block_name: 상위 블록 이름

        Returns:
            변환된 엔티티
        """
        import math
        from copy import deepcopy

        result_data = deepcopy(entity.data)

        # 회전 행렬 (간소화 - 스케일 적용)
        cos_r = math.cos(math.radians(rotation))
        sin_r = math.sin(math.radians(rotation))

        def transform_point(px: float, py: float) -> Tuple[float, float]:
            # 스케일 적용
            sx = px * xscale
            sy = py * yscale
            # 회전 적용
            rx = sx * cos_r - sy * sin_r
            ry = sx * sin_r + sy * cos_r
            # 오프셋 적용
            return (round(rx + offset[0], 2), round(ry + offset[1], 2))

        # 위치 변환
        new_location = transform_point(entity.location[0], entity.location[1])

        # 해시 재생성 (블록 정보 포함)
        new_hash = self._generate_hash(f"{entity.hash}:BLOCK:{block_name}:{offset}")

        return NormalizedEntity(
            hash=new_hash,
            entity_type=entity.entity_type,
            layer=entity.layer,
            data=result_data,
            location=new_location,
            parent_block=block_name,
        )

    def _normalize(self, entity) -> Optional[NormalizedEntity]:
        """엔티티 타입별 정규화 (Strategy Pattern 적용)

        NormalizerFactory를 통해 엔티티 타입에 맞는 Normalizer를 선택하고
        정규화를 수행합니다.

        Args:
            entity: ezdxf 엔티티 객체

        Returns:
            정규화된 엔티티 또는 None
        """
        etype = entity.dxftype()

        try:
            # Strategy Pattern: NormalizerFactory 사용
            result = self._normalizer_factory.normalize(entity)

            if result is None:
                return None

            # entity_normalizers.NormalizedEntity → 로컬 NormalizedEntity 변환.
            # CRITICAL bug fix (2026-05-08): cosmetic 필드 (color/lineweight/
            # linetype) 누락으로 Phase O3 cosmetic detection 이 production 에서
            # silent no-op 였음. 양쪽 NormalizedEntity 가 동일 필드를 가지므로
            # 단순 getattr 패스스루로 안전 — entity_normalizers 가 cosmetic
            # 추출 안 한 케이스(예: 토이 backend) 도 None default 로 호환.
            return NormalizedEntity(
                hash=result.hash,
                entity_type=result.entity_type,
                layer=result.layer,
                data=result.data,
                location=result.location,
                parent_block=result.parent_block,
                color=getattr(result, "color", None),
                lineweight=getattr(result, "lineweight", None),
                linetype=getattr(result, "linetype", None),
            )
        except Exception as e:
            logger.warning(f"{etype} 정규화 실패: {e}")
            return None

    def _round_point(self, point, precision: Optional[int] = None) -> Tuple[float, float]:
        """좌표 반올림

        Args:
            point: (x, y) 또는 Vec3 객체
            precision: 소수점 자릿수 (None이면 기본값 사용)

        Returns:
            (x, y) 튜플
        """
        p = precision or self.precision

        if hasattr(point, "x"):
            return (round(point.x, p), round(point.y, p))
        else:
            return (round(point[0], p), round(point[1], p))

    def _generate_hash(self, key: str) -> str:
        """해시 생성

        Args:
            key: 해시 입력 문자열

        Returns:
            MD5 해시 (16진수 문자열)
        """
        return hashlib.md5(key.encode()).hexdigest()

    def _extract_cosmetic(self, entity) -> Dict[str, Any]:
        """Phase O3 — ezdxf entity 의 cosmetic 속성 (color/lineweight/linetype)
        안전 추출. CRITICAL bug fix (2026-05-08): DxfEntityExtractor 의
        local _normalize_* 메서드들이 ``NormalizedEntity`` 의 cosmetic
        필드 (color/lineweight/linetype) 를 채우지 않아 production 에서
        Phase O3 cosmetic detection 이 silent no-op 였던 문제를 수정.

        ``entity_normalizers.py`` 의 동일 헬퍼와 의도적으로 동일한
        시그니처/동작 (두 normalizer 경로의 cosmetic semantics 일치).
        """
        out: Dict[str, Any] = {"color": None, "lineweight": None, "linetype": None}
        dxf_attrs = getattr(entity, "dxf", None)
        if dxf_attrs is None:
            return out
        for attr_name in ("color", "lineweight", "linetype"):
            try:
                value = getattr(dxf_attrs, attr_name, None)
                if value is not None:
                    if attr_name == "linetype":
                        out[attr_name] = str(value) if value else None
                    else:
                        out[attr_name] = int(value)
            except Exception:
                # 일부 entity 는 속성 접근 시 예외 — 무시 (None 유지)
                continue
        return out

    # =========================================================================
    # 엔티티별 정규화 메서드
    # =========================================================================

    def _normalize_line(self, line) -> NormalizedEntity:
        """LINE 정규화"""
        start = self._round_point(line.dxf.start)
        end = self._round_point(line.dxf.end)

        # 방향 무관하게 정렬 (A→B == B→A)
        key = tuple(sorted([start, end]))

        center = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)

        return NormalizedEntity(
            hash=self._generate_hash(f"LINE:{key}"),
            entity_type="LINE",
            layer=line.dxf.layer,
            data={"start": start, "end": end},
            location=center,
            **self._extract_cosmetic(line),
        )

    def _normalize_circle(self, circle) -> NormalizedEntity:
        """CIRCLE 정규화"""
        center = self._round_point(circle.dxf.center)
        radius = round(circle.dxf.radius, self.precision)

        return NormalizedEntity(
            hash=self._generate_hash(f"CIRCLE:{center}:{radius}"),
            entity_type="CIRCLE",
            layer=circle.dxf.layer,
            data={"center": center, "radius": radius},
            location=center,
                    **self._extract_cosmetic(circle),
)

    def _normalize_arc(self, arc) -> NormalizedEntity:
        """ARC 정규화"""
        center = self._round_point(arc.dxf.center)
        radius = round(arc.dxf.radius, self.precision)
        start_angle = round(arc.dxf.start_angle % 360, 1)
        end_angle = round(arc.dxf.end_angle % 360, 1)

        return NormalizedEntity(
            hash=self._generate_hash(f"ARC:{center}:{radius}:{start_angle}:{end_angle}"),
            entity_type="ARC",
            layer=arc.dxf.layer,
            data={
                "center": center,
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
            },
            location=center,
                    **self._extract_cosmetic(arc),
)

    def _normalize_polyline(self, poly) -> NormalizedEntity:
        """POLYLINE/LWPOLYLINE 정규화"""
        # 정점 추출
        if hasattr(poly, "get_points"):
            # LWPOLYLINE
            points = [self._round_point(p) for p in poly.get_points()]
        else:
            # POLYLINE
            points = [self._round_point(v.dxf.location) for v in poly.vertices]

        if not points:
            raise ValueError("폴리라인에 정점이 없습니다")

        # Closed 폴리라인: 시작점 기준 회전하여 정규화
        is_closed = poly.is_closed if hasattr(poly, "is_closed") else False

        if is_closed and len(points) > 1:
            # 최소 좌표 기준으로 회전
            min_idx = points.index(min(points))
            points = points[min_idx:] + points[:min_idx]

        # 중심점 계산
        center_x = sum(p[0] for p in points) / len(points)
        center_y = sum(p[1] for p in points) / len(points)

        return NormalizedEntity(
            hash=self._generate_hash(f"POLY:{tuple(points)}:{is_closed}"),
            entity_type="LWPOLYLINE",
            layer=poly.dxf.layer,
            data={"points": points, "closed": is_closed},
            location=(round(center_x, 1), round(center_y, 1)),
                    **self._extract_cosmetic(poly),
)

    def _normalize_text(self, text) -> NormalizedEntity:
        """TEXT 정규화"""
        pos = self._round_point(text.dxf.insert, precision=1)
        content = text.dxf.text.strip()

        return NormalizedEntity(
            hash=self._generate_hash(f"TEXT:{pos}:{content}"),
            entity_type="TEXT",
            layer=text.dxf.layer,
            data={"position": pos, "content": content},
            location=pos,
                    **self._extract_cosmetic(text),
)

    def _normalize_mtext(self, mtext) -> NormalizedEntity:
        """MTEXT 정규화"""
        pos = self._round_point(mtext.dxf.insert, precision=1)
        # MTEXT는 rich text - plain text로 변환
        content = mtext.plain_text().strip()

        return NormalizedEntity(
            hash=self._generate_hash(f"MTEXT:{pos}:{content}"),
            entity_type="MTEXT",
            layer=mtext.dxf.layer,
            data={"position": pos, "content": content},
            location=pos,
                    **self._extract_cosmetic(mtext),
)

    def _normalize_dimension(self, dim) -> NormalizedEntity:
        """DIMENSION 정규화"""
        # 치수 측정값
        try:
            measurement = round(dim.get_measurement(), 1)
        except Exception:
            measurement = 0.0

        # 정의점
        defpoint = self._round_point(dim.dxf.defpoint, precision=1)

        # 텍스트 오버라이드 확인
        text_override = getattr(dim.dxf, "text", "")

        return NormalizedEntity(
            hash=self._generate_hash(f"DIM:{defpoint}:{measurement}:{text_override}"),
            entity_type="DIMENSION",
            layer=dim.dxf.layer,
            data={
                "defpoint": defpoint,
                "measurement": measurement,
                "text_override": text_override,
            },
            location=defpoint,
                    **self._extract_cosmetic(dim),
)

    def _normalize_insert(self, insert) -> NormalizedEntity:
        """LEGACY DEAD CODE — DO NOT CALL [RV-20260508-011 H1].

        Live INSERT normalization 은 ``self._normalize()`` →
        ``NormalizerFactory`` → ``InsertNormalizer`` 로 흐른다 (Phase
        O Commit 2 의 block_text_fingerprint 포함). 이 메서드는 그
        경로 이전의 legacy 본체 — 호출되지 않으며 hash 가 live 경로와
        다르다 (fingerprint 누락 → cache 충돌 위험).

        남겨둔 이유: 다른 _normalize_* 헬퍼들과 대칭 + 외부 docstring
        / 문서가 참조할 수 있어 함부로 삭제하지 않음. 향후 cleanup
        commit 에서 일괄 제거 예정.
        """
        block_name = insert.dxf.name
        insert_point = self._round_point(insert.dxf.insert, precision=1)

        # 스케일 및 회전
        xscale = round(insert.dxf.xscale, 2)
        yscale = round(insert.dxf.yscale, 2)
        rotation = round(insert.dxf.rotation % 360, 1)

        return NormalizedEntity(
            hash=self._generate_hash(
                f"INSERT:{block_name}:{insert_point}:{xscale}:{yscale}:{rotation}"
            ),
            entity_type="INSERT",
            layer=insert.dxf.layer,
            data={
                "block_name": block_name,
                "insert_point": insert_point,
                "xscale": xscale,
                "yscale": yscale,
                "rotation": rotation,
            },
            location=insert_point,
                    **self._extract_cosmetic(insert),
)
