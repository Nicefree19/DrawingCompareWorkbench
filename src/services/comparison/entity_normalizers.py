"""엔티티 정규화 전략 패턴

Sprint 9 Phase 2: P1-3 Strategy Pattern - EntityNormalizer
각 DXF 엔티티 타입별 정규화 로직을 Strategy 패턴으로 분리합니다.

구조:
    - EntityNormalizer: 추상 베이스 클래스
    - LineNormalizer, CircleNormalizer 등: 구체 전략
    - NormalizerFactory: 팩토리 클래스
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Type

# NormalizedEntity 임포트 (순환 참조 방지를 위해 TYPE_CHECKING 사용)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dxf_entity_extractor import NormalizedEntity


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
        color: Phase O3 — ACI 색상 코드 (256=BYLAYER, 0=BYBLOCK).
            ``None`` 이면 미설정 또는 추출 실패 (cache 호환).
        lineweight: Phase O3 — 선두께 (100ths of mm; -1=BYLAYER,
            -2=BYBLOCK, -3=DEFAULT). ``None`` = 미설정.
        linetype: Phase O3 — 선종류 이름 ("Continuous", "DASHED", ...).
            ``None`` = 미설정.
    """

    hash: str
    entity_type: str
    layer: str
    data: Dict[str, Any]
    location: Tuple[float, float]
    parent_block: Optional[str] = None
    # Phase O3 — cosmetic 속성 (좌표 hash 와 분리)
    color: Optional[int] = None
    lineweight: Optional[int] = None
    linetype: Optional[str] = None

    def __eq__(self, other):
        if not isinstance(other, NormalizedEntity):
            return False
        return self.hash == other.hash

    def __hash__(self):
        return hash(self.hash)


class EntityNormalizer(ABC):
    """엔티티 정규화 추상 베이스 클래스

    Strategy 패턴의 Strategy 인터페이스.
    각 엔티티 타입별 정규화 로직을 캡슐화합니다.
    """

    def __init__(self, precision: int = 2):
        """
        Args:
            precision: 좌표 정밀도 (소수점 자릿수)
        """
        self.precision = precision

    @property
    @abstractmethod
    def entity_type(self) -> str:
        """지원하는 엔티티 타입 반환"""
        pass

    @abstractmethod
    def normalize(self, entity) -> NormalizedEntity:
        """엔티티 정규화

        Args:
            entity: ezdxf 엔티티 객체

        Returns:
            정규화된 엔티티
        """
        pass

    def _round_point(self, point) -> Tuple[float, float]:
        """좌표 반올림

        Args:
            point: (x, y) 또는 Vec3 객체

        Returns:
            (x, y) 튜플
        """
        if hasattr(point, "x"):
            return (round(point.x, self.precision), round(point.y, self.precision))
        return (round(point[0], self.precision), round(point[1], self.precision))

    def _is_non_default_extrusion(self, entity) -> bool:
        """Phase Q4 (RV-20260509-002) — entity 가 default OCS (extrusion=
        (0,0,1)) 외의 OCS 를 사용하는지 cheap probe.

        DXF 의 OCS-aware entity (CIRCLE/ARC/POLYLINE/TEXT/MTEXT/INSERT/
        HATCH/DIMENSION/ATTDEF/ATTRIB) 는 extrusion 벡터가 (0,0,1) 이
        아니면 좌표가 OCS 기준으로 저장됨. WCS 좌표가 필요한 비교에서는
        ``entity.ocs().to_wcs(point)`` 변환 필수.

        ``getattr`` 안전 fallback 으로 dxf 객체 없거나 extrusion 속성
        부재 시 default 로 가정 (False 반환).
        """
        dxf = getattr(entity, "dxf", None)
        if dxf is None:
            return False
        try:
            extrusion = getattr(dxf, "extrusion", None)
        except Exception:
            return False
        if extrusion is None:
            return False
        # extrusion 은 보통 Vec3 (또는 tuple). default = (0,0,1).
        try:
            x = float(getattr(extrusion, "x", extrusion[0]))
            y = float(getattr(extrusion, "y", extrusion[1]))
            z = float(getattr(extrusion, "z", extrusion[2]))
        except Exception:
            return False
        # 부동소수점 오차 허용
        return not (abs(x) < 1e-9 and abs(y) < 1e-9 and abs(z - 1.0) < 1e-9)

    def _extrusion_key(self, entity) -> str:
        """Phase Q4 Codex follow-up [P2] (RV-20260509-002) — extrusion
        벡터를 hash key 로 직렬화. default OCS 면 빈 문자열 (legacy hash
        보존), non-default 면 부호 + 자릿수 문자열.

        ARC/INSERT 등 OCS basis 가 시각적 의미를 갖는 entity 의 hash 에
        포함되어, 같은 OCS 좌표를 가진 두 entity 가 다른 extrusion
        때문에 WCS 에서 다른 모습을 갖는 케이스를 hash 분리한다.

        Phase Q4 Codex round-2 follow-up [P2] (RV-20260509-002): extrusion
        벡터는 raw magnitude (예: (0,0,-2)) 로 들어올 수 있으나 ezdxf 의
        ocs() 가 unit normalize 하므로 동일 OCS basis 를 산출. raw 값을
        hash 하면 (0,0,-1) vs (0,0,-2) 가 false-different 로 표시됨. 본
        helper 도 동일하게 unit normalize 후 직렬화 + normalized 가
        (0,0,1) 이면 빈 문자열 반환 (legacy hash 와 정확히 동등).
        """
        dxf = getattr(entity, "dxf", None)
        if dxf is None:
            return ""
        try:
            extrusion = getattr(dxf, "extrusion", None)
        except Exception:
            return ""
        if extrusion is None:
            return ""
        try:
            x = float(getattr(extrusion, "x", extrusion[0]))
            y = float(getattr(extrusion, "y", extrusion[1]))
            z = float(getattr(extrusion, "z", extrusion[2]))
        except Exception:
            return ""
        # Unit normalize so equivalent magnitudes produce identical key.
        magnitude = (x * x + y * y + z * z) ** 0.5
        if magnitude < 1e-12:
            # zero-length extrusion → fallback to default (no key)
            return ""
        # Phase Q4 Codex round-3 follow-up [P2] (RV-20260509-002): default
        # OCS check 는 unrounded unit vector 로 수행해야 함. rounding 후
        # check 시 (0.0004, 0, 1.0) 같은 small non-default extrusion 이
        # (0,0,1) 로 collapse 되어 false-equivalence (legacy hash 와 동일).
        # ezdxf 는 이 OCS basis 를 다르게 처리하므로 visual diff 누락 위험.
        nx_raw = x / magnitude
        ny_raw = y / magnitude
        nz_raw = z / magnitude
        # Tight tolerance (1e-9) on UNROUNDED unit vector — only true
        # +Z gets the empty key. Rounded key for serialization happens
        # only after non-default check.
        if (abs(nx_raw) < 1e-9 and abs(ny_raw) < 1e-9
                and abs(nz_raw - 1.0) < 1e-9):
            return ""
        nx = round(nx_raw, 3)
        ny = round(ny_raw, 3)
        nz = round(nz_raw, 3)
        return f"E:{nx},{ny},{nz}"

    def _to_wcs(self, entity, point) -> Tuple[float, float]:
        """Phase Q4 (RV-20260509-002) — OCS-aware entity 의 좌표를 WCS 로
        변환 후 (x, y) 튜플 반환. default OCS 면 _round_point 와 동일.

        ezdxf 의 ``entity.ocs().to_wcs(point)`` 사용. 일부 entity
        (예: 특수 LEADER) 가 ``ocs()`` 를 노출하지 않거나 변환 실패 시
        graceful fallback (raw round 값 반환) — silent drop 보다 best-
        effort 비교 결과 surface 가 우선.
        """
        if not self._is_non_default_extrusion(entity):
            return self._round_point(point)
        try:
            ocs = entity.ocs()
            wcs_point = ocs.to_wcs(point if hasattr(point, "x") else (
                float(point[0]), float(point[1]),
                float(point[2]) if len(point) > 2 else 0.0,
            ))
            return (
                round(wcs_point.x, self.precision),
                round(wcs_point.y, self.precision),
            )
        except Exception:
            # graceful fallback — OCS 변환 실패 시 raw 값
            return self._round_point(point)

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
        안전 추출.

        모든 ezdxf entity 가 세 속성을 모두 가지지는 않음 (예: TEXT 는
        lineweight 없음). ``getattr`` + try/except 로 누락 시 None 반환.

        반환 dict 는 ``NormalizedEntity(**)`` 의 키워드 인자로 그대로
        spread 가능.
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
                # ezdxf 일부 entity 는 속성 접근 시 예외 — 무시 (None 유지)
                continue
        return out


class LineNormalizer(EntityNormalizer):
    """LINE 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "LINE"

    def normalize(self, line) -> NormalizedEntity:
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
            **self._extract_cosmetic(line),  # Phase O3
        )


class CircleNormalizer(EntityNormalizer):
    """CIRCLE 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "CIRCLE"

    def normalize(self, circle) -> NormalizedEntity:
        # Phase Q4 (RV-20260509-002) — CIRCLE 의 center 는 OCS 좌표.
        # default OCS 일 땐 raw 값 동일 (cheap probe).
        center = self._to_wcs(circle, circle.dxf.center)
        radius = round(circle.dxf.radius, self.precision)

        return NormalizedEntity(
            hash=self._generate_hash(f"CIRCLE:{center}:{radius}"),
            entity_type="CIRCLE",
            layer=circle.dxf.layer,
            data={"center": center, "radius": radius},
            location=center,
            **self._extract_cosmetic(circle),  # Phase O3
        )


class ArcNormalizer(EntityNormalizer):
    """ARC 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "ARC"

    def normalize(self, arc) -> NormalizedEntity:
        # Phase Q4 (RV-20260509-002) — ARC center 는 OCS.
        center = self._to_wcs(arc, arc.dxf.center)
        radius = round(arc.dxf.radius, self.precision)
        start_angle = round(arc.dxf.start_angle % 360, 1)
        end_angle = round(arc.dxf.end_angle % 360, 1)
        # Phase Q4 Codex follow-up [P2] (RV-20260509-002): start/end
        # angle 은 OCS 기준 — extrusion 차이는 같은 raw angle 도 WCS
        # 시작 방향이 다르게 함. 기본은 angle WCS 변환이지만 정확한
        # 각도 변환 (ARB axis) 은 비용 큼. defensive 접근: extrusion
        # 을 hash key 에 포함해 OCS basis 차이가 즉시 hash 분리.
        extrusion_key = self._extrusion_key(arc)

        return NormalizedEntity(
            hash=self._generate_hash(
                f"ARC:{center}:{radius}:{start_angle}:{end_angle}:{extrusion_key}"
            ),
            entity_type="ARC",
            layer=arc.dxf.layer,
            data={
                "center": center,
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "extrusion_key": extrusion_key,
            },
            location=center,
            **self._extract_cosmetic(arc),  # Phase O3
        )


class PolylineNormalizer(EntityNormalizer):
    """POLYLINE/LWPOLYLINE 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "LWPOLYLINE"

    def normalize(self, poly) -> NormalizedEntity:
        # Phase Q4 (RV-20260509-002) — LWPOLYLINE / 2D POLYLINE 의 vertex
        # 좌표는 OCS. ``_to_wcs`` 가 default OCS 일 땐 cheap (raw round
        # 값 동일).
        if hasattr(poly, "get_points"):
            # LWPOLYLINE — get_points() 는 (x, y[, start_width, end_width,
            # bulge]) 튜플. 첫 두 요소만 좌표. shared OCS z 는 dxf.elevation.
            # Phase Q4 Codex follow-up [P2] (RV-20260509-002): 이전엔
            # (x, y) 만 전달하여 _to_wcs 가 z=0 가정 → tilted extrusion +
            # non-zero elevation 시 WCS x/y 부정확. (x, y, elevation)
            # 으로 z 보존.
            try:
                elevation = float(getattr(poly.dxf, "elevation", 0.0) or 0.0)
            except Exception:
                elevation = 0.0
            points = [
                self._to_wcs(poly, (p[0], p[1], elevation))
                for p in poly.get_points()
            ]
        else:
            # 2D POLYLINE — vertex.dxf.location 은 OCS 좌표 (3D 인 경우
            # z 는 vertex 자체에 포함)
            points = [
                self._to_wcs(poly, v.dxf.location) for v in poly.vertices
            ]

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
            **self._extract_cosmetic(poly),  # Phase O3
        )


class TextNormalizer(EntityNormalizer):
    """TEXT 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "TEXT"

    def normalize(self, text) -> NormalizedEntity:
        # Phase Q4 (RV-20260509-002) — TEXT.insert 는 OCS 좌표.
        pos = self._to_wcs(text, text.dxf.insert)
        # 위치 정밀도는 1로 제한
        pos = (round(pos[0], 1), round(pos[1], 1))
        content = text.dxf.text.strip()

        return NormalizedEntity(
            hash=self._generate_hash(f"TEXT:{pos}:{content}"),
            entity_type="TEXT",
            layer=text.dxf.layer,
            data={"position": pos, "content": content},
            location=pos,
            **self._extract_cosmetic(text),  # Phase O3
        )


class MTextNormalizer(EntityNormalizer):
    """MTEXT 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "MTEXT"

    def normalize(self, mtext) -> NormalizedEntity:
        # Phase Q4 (RV-20260509-002) — MTEXT.insert 는 OCS.
        pos = self._to_wcs(mtext, mtext.dxf.insert)
        pos = (round(pos[0], 1), round(pos[1], 1))
        # MTEXT는 rich text - plain text로 변환
        content = mtext.plain_text().strip()

        return NormalizedEntity(
            hash=self._generate_hash(f"MTEXT:{pos}:{content}"),
            entity_type="MTEXT",
            layer=mtext.dxf.layer,
            data={"position": pos, "content": content},
            location=pos,
            **self._extract_cosmetic(mtext),  # Phase O3
        )


class DimensionNormalizer(EntityNormalizer):
    """DIMENSION 엔티티 정규화"""

    @property
    def entity_type(self) -> str:
        return "DIMENSION"

    def normalize(self, dim) -> NormalizedEntity:
        # 치수 측정값
        try:
            measurement = round(dim.get_measurement(), 1)
        except Exception:
            measurement = 0.0

        # Phase Q4 Codex follow-up [P2] (RV-20260509-002): DIMENSION
        # 의 defpoint 는 ezdxf/DXF 사양상 *WCS* 점. _to_wcs 적용 시
        # double-transform 으로 (0,0,-1) extrusion 케이스에서 X 좌표
        # 두 번 flip → spurious diff. text_midpoint 등 일부 다른 필드만
        # OCS 인데 본 normalizer 는 hash 입력에 사용하지 않음.
        defpoint = self._round_point(dim.dxf.defpoint)
        defpoint = (round(defpoint[0], 1), round(defpoint[1], 1))

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
            **self._extract_cosmetic(dim),  # Phase O3
        )


class InsertNormalizer(EntityNormalizer):
    """INSERT (블록 참조) 정규화 — Phase O Commit 2 (RV-20260508-007)
    이후로는 hash 에 block 정의 내부의 텍스트 (TEXT/MTEXT plain text
    + ATTDEF default text) fingerprint 도 포함한다.

    동기: 사용자 사례에서 발견된 두 번째 누락 — 블록 *정의* 내부의
    TEXT 가 변경되었지만 ATTRIB 로 realize 되지 않은 케이스. 예를
    들어 dowel callout block 의 정의 안에 ``DOWEL @100`` 이라는
    TEXT 가 박혀 있고 라이브러리 차원에서 ``DOWEL @200`` 로 갱신된
    경우, INSERT 자체는 동일 block_name + 좌표 + scale + rotation
    이라 hash 가 같아 변경이 invisible 했음.

    설계:
    - block_text_fingerprint = sorted (text content, position) tuple 의 md5
    - cache: (id(doc), block_name) → fingerprint, InsertNormalizer 인스턴스
      lifetime 내 재사용 (factory 가 동일 instance 를 재공급하므로
      한 extraction pass 의 모든 INSERT 가 캐시 공유)
    - block 정의를 못 찾거나 doc 접근 실패 시 fingerprint=""(빈 문자
      열) 로 graceful degradation — 기존 hash 가 나옴
    """

    def __init__(
        self,
        precision: int = 2,
        *,
        block_text_detection: bool = True,
    ):
        super().__init__(precision)
        # (id(doc), block_name) → fingerprint (16-hex). Codex P1
        # [RV-20260508-008]: ``id()`` 는 객체가 GC 된 후 재사용
        # 가능하므로 (CPython 메모리 주소 재사용), 한 extractor 가
        # 여러 doc 에 재사용되면 stale fingerprint 위험. ``extract()``
        # 가 매 호출 시 ``reset_per_extraction_state()`` 로 캐시
        # 초기화함으로써 cross-doc 오염 차단.
        self._block_fingerprint_cache: Dict[Tuple[int, str], str] = {}
        # Phase O Commit 3 [RV-20260508-009] — 정밀 텍스트 감지 toggle.
        # False 면 ``_compute_block_text_fingerprint`` 가 즉시 ""
        # 반환하여 INSERT hash 가 Phase O Commit 1 이전 동작으로
        # 회귀. 기본값 True (사용자 사례 보호).
        self._block_text_detection = bool(block_text_detection)

    def reset_per_extraction_state(self) -> None:
        """Codex P1 [RV-20260508-008] — 새 extraction 시작 시 호출되어
        block fingerprint 캐시를 무효화. ``DxfEntityExtractor.extract``
        가 doc 별 호출 직전에 invoke."""
        self._block_fingerprint_cache.clear()

    @property
    def entity_type(self) -> str:
        return "INSERT"

    def normalize(self, insert, *, fingerprint_mode: str = "full") -> NormalizedEntity:  # noqa: C901
        """Normalize INSERT.

        Phase Q3 Codex round-2 follow-up [P2] (RV-20260509-002):
        ``fingerprint_mode`` 옵션 추가.

        - ``"full"`` (기본): block_text fingerprint 포함. legacy 동작 — 어떤
          INSERT 변경 (transform OR block-internal text) 도 hash 차이로
          surface. ``expand_blocks=False`` 경로에서 사용.
        - ``"transform_only"``: block_text fingerprint 제외. ``expand_blocks
          =True`` 경로에서 사용 — block-internal TEXT/ATTDEF 변경은 expanded
          children 이 직접 surface 하므로 parent INSERT 가 동일 변경을 다시
          보고하면 double-count. transform-only hash 로 그 중복을 차단하면서
          xscale/yscale/rotation 변경은 여전히 detect.
        """
        block_name = insert.dxf.name
        # Phase Q4 (RV-20260509-002) — INSERT.insert 는 OCS 좌표.
        insert_point = self._to_wcs(insert, insert.dxf.insert)
        insert_point = (round(insert_point[0], 1), round(insert_point[1], 1))

        # 스케일 및 회전
        xscale = round(insert.dxf.xscale, 2)
        yscale = round(insert.dxf.yscale, 2)
        rotation = round(insert.dxf.rotation % 360, 1)

        # Phase Q4 Codex follow-up [P2] (RV-20260509-002): extrusion 을
        # hash 에 포함. raw rotation 만으론 OCS basis 차이를 표현하지
        # 못해서 flipped block 이 default block 과 동일 hash 가 될 수
        # 있음. extrusion 의 부호화된 정수 키를 추가하면 OCS basis 차이가
        # 즉시 hash 분리.
        extrusion_key = self._extrusion_key(insert)

        if fingerprint_mode == "transform_only":
            block_text_fp = ""
        else:
            # Block-internal text fingerprint (Phase O Commit 2)
            block_text_fp = self._compute_block_text_fingerprint(insert, block_name)

        return NormalizedEntity(
            hash=self._generate_hash(
                f"INSERT:{block_name}:{insert_point}:{xscale}:{yscale}:"
                f"{rotation}:{extrusion_key}:{block_text_fp}"
            ),
            entity_type="INSERT",
            layer=insert.dxf.layer,
            data={
                "block_name": block_name,
                "insert_point": insert_point,
                "xscale": xscale,
                "yscale": yscale,
                "rotation": rotation,
                "extrusion_key": extrusion_key,
                "block_text_fingerprint": block_text_fp,
                "fingerprint_mode": fingerprint_mode,
            },
            location=insert_point,
            **self._extract_cosmetic(insert),  # Phase O3
        )

    def _compute_block_text_fingerprint(self, insert, block_name: str) -> str:
        """Block 정의 내부의 텍스트만 fingerprint.

        (text content, rounded position) 튜플들을 sorted 후 md5 16자리
        반환. block 을 찾지 못하거나 doc 접근 실패 시 빈 문자열.

        - TEXT.dxf.text, MTEXT.plain_text(), ATTDEF.dxf.text 만 수집
        - 좌표는 정밀도 1 (mm 단위) — 미세 좌표 차이가 fingerprint
          에 잡히지 않도록
        - geometry (LINE/CIRCLE/...) 는 의도적으로 제외 — Phase F P0
          truth-layer 결정 (block geometry 변경은 expand_blocks=True
          경로의 책임)
        - ``block_text_detection=False`` (Phase O Commit 3 토글) 시
          즉시 빈 문자열 반환하여 hash 가 legacy 와 동일.
        """
        if not self._block_text_detection:
            return ""
        if not block_name:
            return ""

        try:
            doc = getattr(insert, "doc", None)
        except Exception:
            doc = None
        if doc is None:
            return ""

        cache_key = (id(doc), block_name)
        cached = self._block_fingerprint_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            block = doc.blocks.get(block_name)
        except Exception:
            block = None
        if block is None:
            self._block_fingerprint_cache[cache_key] = ""
            return ""

        text_items: list = []
        try:
            for entity in block:
                etype = entity.dxftype()
                if etype == "TEXT":
                    try:
                        content = (entity.dxf.text or "").strip()
                    except Exception:
                        continue
                    if not content:
                        continue
                    pos = self._round_point(entity.dxf.insert)
                    text_items.append(("TEXT", content, (round(pos[0], 1), round(pos[1], 1))))
                elif etype == "MTEXT":
                    try:
                        content = (entity.plain_text() or "").strip()
                    except Exception:
                        continue
                    if not content:
                        continue
                    pos = self._round_point(entity.dxf.insert)
                    text_items.append(("MTEXT", content, (round(pos[0], 1), round(pos[1], 1))))
                elif etype == "ATTDEF":
                    try:
                        content = (entity.dxf.text or "").strip()
                        tag = (entity.dxf.tag or "").strip()
                    except Exception:
                        continue
                    pos = self._round_point(entity.dxf.insert)
                    text_items.append(
                        ("ATTDEF", f"{tag}={content}",
                         (round(pos[0], 1), round(pos[1], 1)))
                    )
        except Exception:
            # 블록 iterate 실패 시 빈 fingerprint (graceful)
            self._block_fingerprint_cache[cache_key] = ""
            return ""

        if not text_items:
            self._block_fingerprint_cache[cache_key] = ""
            return ""

        text_items.sort()
        serialized = "|".join(
            f"{kind}:{content}:{pos}" for kind, content, pos in text_items
        )
        digest = hashlib.md5(serialized.encode()).hexdigest()[:16]
        self._block_fingerprint_cache[cache_key] = digest
        return digest


class AttribNormalizer(EntityNormalizer):
    """ATTRIB (블록 attribute 인스턴스) 정규화 — RV-20260508-003.

    ATTRIB 은 INSERT 의 sub-entity 로, modelspace 직접 iterate 시
    노출되지 않고 ``insert.attribs`` 를 통해서만 접근됨. extractor
    가 INSERT 처리 시 명시적으로 iterate 해야 함.

    Hash 의 1차 신호는 (tag, position) — 사용자가 본 ``DOWEL BAR ...
    @100 -> @200`` 케이스는 동일 ATTRIB 의 text 만 변한 것이라 좌표
    동일 + tag 동일 + text 다름 → MODIFIED 로 검출돼야 함.

    parent_block 은 ``_normalize_with_parent`` 에서 별도 set
    (extractor 가 INSERT 와 함께 전달).
    """

    @property
    def entity_type(self) -> str:
        return "ATTRIB"

    def normalize(self, attrib) -> NormalizedEntity:
        # CRITICAL [RV-20260508-011] — ezdxf 의 ``attrib.dxf.insert`` 는
        # parent INSERT 기준 LOCAL 좌표 (블록 정의 좌표계). 따라서
        # 동일 block 의 모든 INSERT 에서 동일 ATTRIB tag 는 동일
        # local pos 를 가져 hash 가 충돌함 — 사용자가 본 dowel callout
        # (수십~수백 개) 시나리오에서 모든 ATTRIB 이 같은 hash 슬롯에
        # 모이고 FIFO deque 매칭으로 변경이 잘못된 callout 에 attribute
        # 됨. 진짜 사용자 사례를 무력화.
        #
        # 수정: ``_extract_insert_attribs`` 가 normalize 직후 parent
        # INSERT 의 modelspace 좌표를 합쳐서 ``rehash_with_parent_context``
        # 로 hash 를 재계산. 이 normalize 는 hash 의 partial form 만
        # 만들고 (re-hash 가 강제임을 명시), data 에 raw local pos 를
        # 저장하여 후속 단계가 parent 와 결합 가능하도록 함.
        pos = self._round_point(attrib.dxf.insert)
        pos = (round(pos[0], 1), round(pos[1], 1))
        tag = (attrib.dxf.tag or "").strip()
        # ATTRIB.dxf.text 가 일반적인 값. plain_text() 는 MText-style
        # ATTRIB (rare) 에 대비.
        try:
            text = (attrib.dxf.text or "").strip()
        except Exception:
            text = ""
        # 일부 attrib 는 plain_text 를 가짐 (ezdxf >=1.0)
        if not text and hasattr(attrib, "plain_text"):
            try:
                text = (attrib.plain_text() or "").strip()
            except Exception:
                pass

        # Partial hash — caller MUST re-hash via ``rehash_with_parent_context``
        # if the ATTRIB belongs to an INSERT in modelspace. The partial
        # form is sufficient when ATTRIB is read out of the BLOCK
        # definition directly (no modelspace context yet).
        return NormalizedEntity(
            hash=self._generate_hash(f"ATTRIB:{tag}:{pos}"),
            entity_type="ATTRIB",
            layer=attrib.dxf.layer,
            data={
                "tag": tag,
                "text": text,
                "content": text,  # comparator 의 TEXT/MTEXT diff 경로 재활용
                "position": pos,
            },
            location=pos,
            **self._extract_cosmetic(attrib),  # Phase O3 호환
        )

    @staticmethod
    def rehash_with_parent_context(
        normalized: "NormalizedEntity",
        parent_block: str,
        parent_insert_point: Tuple[float, float],
        parent_xscale: float = 1.0,
        parent_yscale: float = 1.0,
        parent_rotation_deg: float = 0.0,
    ) -> None:
        """RV-20260508-011 — partial ATTRIB hash 를 parent INSERT 의
        modelspace 좌표 + block 이름과 결합하여 재계산. In-place 수정.

        호출 위치: ``DxfEntityExtractor._extract_insert_attribs``. 매
        ATTRIB extraction 직후 parent INSERT 의 ``dxf.insert`` /
        ``xscale`` / ``yscale`` / ``rotation`` 을 넘겨주어 동일 block
        의 여러 INSERT 인스턴스에서 ATTRIB hash 가 충돌하지 않도록.

        ``location`` 은 parent insert point + (rotation/scale 적용된)
        local pos 합성 = ATTRIB 의 실제 modelspace 위치로 갱신
        (Codex P2 [RV-20260508-012]). 동일 INSERT 내 여러 ATTRIB 가
        각자 다른 local 좌표를 가질 때 spatial index / change marker
        가 정확한 위치를 가리킴.
        """
        import math as _math

        tag = normalized.data.get("tag", "")
        local_pos = normalized.data.get("position", (0.0, 0.0))
        try:
            lx, ly = float(local_pos[0]), float(local_pos[1])
        except (TypeError, ValueError, IndexError):
            lx, ly = 0.0, 0.0
        parent_pt = (round(parent_insert_point[0], 1), round(parent_insert_point[1], 1))

        # Local → modelspace affine: scale → rotation → translation.
        cos_r = _math.cos(_math.radians(parent_rotation_deg))
        sin_r = _math.sin(_math.radians(parent_rotation_deg))
        sx = lx * float(parent_xscale)
        sy = ly * float(parent_yscale)
        wx = sx * cos_r - sy * sin_r + parent_pt[0]
        wy = sx * sin_r + sy * cos_r + parent_pt[1]
        world_pt = (round(wx, 1), round(wy, 1))

        # Hash: tag + local_pos + parent_block + parent_pt — 동일 block
        # 의 두 INSERT 에서 같은 tag/local_pos 면 parent_pt 가 다르므로
        # hash 가 분리됨.
        new_hash_input = (
            f"ATTRIB:{tag}:{local_pos}:{parent_block}:{parent_pt}"
        )
        normalized.hash = hashlib.md5(new_hash_input.encode()).hexdigest()
        normalized.parent_block = parent_block
        # Codex P2 [RV-20260508-012] — location 을 *변환된 ATTRIB
        # modelspace 좌표* 로 갱신. parent_pt 만 사용했던 직전 구현은
        # 동일 INSERT 의 여러 ATTRIB 들이 모두 parent insert point 로
        # 누적되어 spatial precision 손실 (change marker 가 잘못된
        # 위치 표시). data["position"] 은 디버그용 raw local pos 유지.
        normalized.location = world_pt
        normalized.data["parent_insert_point"] = parent_pt
        normalized.data["modelspace_position"] = world_pt


class AttdefNormalizer(EntityNormalizer):
    """ATTDEF (블록 정의 내부 attribute 템플릿) 정규화 —
    RV-20260508-003.

    ATTDEF 는 BLOCK definition 안에 살며, 일반적으로 ``expand_blocks
    =True`` 또는 INSERT block-text fingerprint (Commit 2) 경로에서
    iterate 됨. modelspace 직접 등장은 드뭄. ATTRIB 와 거의 동일한
    구조이지만 default 값을 가짐 (실제 INSERT 에서 ATTRIB 로 realize
    되기 전까지).
    """

    @property
    def entity_type(self) -> str:
        return "ATTDEF"

    def normalize(self, attdef) -> NormalizedEntity:
        pos = self._round_point(attdef.dxf.insert)
        pos = (round(pos[0], 1), round(pos[1], 1))
        tag = (attdef.dxf.tag or "").strip()
        try:
            text = (attdef.dxf.text or "").strip()
        except Exception:
            text = ""
        prompt = (getattr(attdef.dxf, "prompt", "") or "").strip()

        return NormalizedEntity(
            hash=self._generate_hash(f"ATTDEF:{tag}:{pos}"),
            entity_type="ATTDEF",
            layer=attdef.dxf.layer,
            data={
                "tag": tag,
                "text": text,
                "content": text,
                "prompt": prompt,
                "position": pos,
            },
            location=pos,
            **self._extract_cosmetic(attdef),
        )


class HatchNormalizer(EntityNormalizer):
    """HATCH 엔티티 정규화 — Phase Q1 (RV-20260509-002).

    HATCH 는 도면에서 가장 흔한 entity 중 하나 (단면 채움, 벽체 내부,
    콘크리트 빗금). Phase O 까지 SUPPORTED_TYPES 에 없어 silent drop —
    사용자의 "단면 변경 시각적 단서" 가 비교 결과에 surface 안 됨.

    Hash 입력: layer + pattern_name + scale + 첫 boundary path 의 vertex
    리스트 정렬 hash. boundary 가 여러 개인 경우 첫 번째만 — full path
    diff 는 후속 phase 에서 보강. 위치 (location) 는 첫 boundary 의
    bounding box 중심.
    """

    @property
    def entity_type(self) -> str:
        return "HATCH"

    def normalize(self, hatch) -> NormalizedEntity:
        try:
            pattern = str(getattr(hatch.dxf, "pattern_name", "SOLID")).strip().upper()
        except Exception:
            pattern = "SOLID"
        try:
            scale = round(float(getattr(hatch.dxf, "pattern_scale", 1.0)), self.precision)
        except Exception:
            scale = 1.0
        try:
            angle = round(float(getattr(hatch.dxf, "pattern_angle", 0.0)) % 360, 1)
        except Exception:
            angle = 0.0

        # boundary path 의 vertex 추출 — 첫 번째 path 만 hash 에 사용,
        # 추가 path 는 count 만 hash 에 포함하여 변경 감지.
        boundary_vertices: List[Tuple[float, float]] = []
        path_count = 0
        try:
            paths = list(hatch.paths)
            path_count = len(paths)
            if paths:
                first_path = paths[0]
                if hasattr(first_path, "vertices"):
                    for v in first_path.vertices:
                        try:
                            boundary_vertices.append(self._round_point(v))
                        except Exception:
                            continue
                elif hasattr(first_path, "edges"):
                    # Edge path — 각 edge 의 시작점 수집
                    for edge in first_path.edges:
                        if hasattr(edge, "start"):
                            try:
                                boundary_vertices.append(self._round_point(edge.start))
                            except Exception:
                                continue
        except Exception:
            pass

        if boundary_vertices:
            xs = [v[0] for v in boundary_vertices]
            ys = [v[1] for v in boundary_vertices]
            center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        else:
            center = (0.0, 0.0)

        # 정렬된 vertex tuple 로 hash — 시작점 순환에도 동일 hash
        sorted_verts = tuple(sorted(boundary_vertices))
        hash_input = f"HATCH:{pattern}:{scale}:{angle}:{path_count}:{sorted_verts}"

        return NormalizedEntity(
            hash=self._generate_hash(hash_input),
            entity_type="HATCH",
            layer=hatch.dxf.layer,
            data={
                "pattern_name": pattern,
                "pattern_scale": scale,
                "pattern_angle": angle,
                "path_count": path_count,
                "boundary_vertex_count": len(boundary_vertices),
            },
            location=center,
            **self._extract_cosmetic(hatch),
        )


class SolidNormalizer(EntityNormalizer):
    """SOLID / 3DSOLID 엔티티 정규화 — Phase Q1 (RV-20260509-002).

    SOLID 는 4 corner points (혹은 3 — triangle 형태). Tekla 도면의
    plate / mass element. Hash 는 corner points 정렬.
    """

    @property
    def entity_type(self) -> str:
        return "SOLID"

    def normalize(self, solid) -> NormalizedEntity:
        corners: List[Tuple[float, float]] = []
        for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
            try:
                pt = getattr(solid.dxf, attr, None)
                if pt is not None:
                    corners.append(self._round_point(pt))
            except Exception:
                continue

        if corners:
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        else:
            center = (0.0, 0.0)

        sorted_corners = tuple(sorted(corners))
        return NormalizedEntity(
            hash=self._generate_hash(f"SOLID:{sorted_corners}"),
            entity_type="SOLID",
            layer=solid.dxf.layer,
            data={"corners": corners, "corner_count": len(corners)},
            location=center,
            **self._extract_cosmetic(solid),
        )


class MLeaderNormalizer(EntityNormalizer):
    """MULTILEADER 엔티티 정규화 — Phase Q1 (RV-20260509-002).

    사용자 보고 사례 ``DOWEL BAR (2)SHD13@100 → @200`` 가 일반적으로
    MULTILEADER (mleader) 로 작성됨. Phase O 까지는 추출 안 되어 변경
    검출 자체 불가. ``dxf_comparator._TEXT_LIKE_ENTITY_TYPES`` 가
    "MULTILEADER" 를 포함하지만 이 normalizer 가 없으면 무용지물.

    Hash 입력: leader anchor (text 위치) + mtext content 의 plain text.
    """

    @property
    def entity_type(self) -> str:
        return "MULTILEADER"

    def normalize(self, mleader) -> NormalizedEntity:
        # MText content
        content = ""
        try:
            ctx = getattr(mleader, "context", None)
            if ctx is not None:
                mtext = getattr(ctx, "mtext", None)
                if mtext is not None and hasattr(mtext, "plain_text"):
                    content = str(mtext.plain_text() or "")
                elif mtext is not None and hasattr(mtext, "default_content"):
                    content = str(mtext.default_content or "")
        except Exception:
            pass
        # Fallback — block content (label block 형식)
        if not content:
            try:
                blk = getattr(mleader.context, "block", None)
                if blk is not None:
                    content = str(getattr(blk, "name", "") or "")
            except Exception:
                pass

        # Anchor — leader insert point 또는 첫 leader 의 시작점
        anchor: Tuple[float, float] = (0.0, 0.0)
        try:
            ctx = mleader.context
            if hasattr(ctx, "base_point") and ctx.base_point is not None:
                anchor = self._round_point(ctx.base_point)
            elif hasattr(ctx, "leaders") and ctx.leaders:
                first_leader = ctx.leaders[0]
                if hasattr(first_leader, "lines") and first_leader.lines:
                    first_line = first_leader.lines[0]
                    if hasattr(first_line, "vertices") and first_line.vertices:
                        anchor = self._round_point(first_line.vertices[0])
        except Exception:
            pass

        return NormalizedEntity(
            hash=self._generate_hash(f"MULTILEADER:{anchor}:{content}"),
            entity_type="MULTILEADER",
            layer=mleader.dxf.layer,
            data={"content": content, "anchor": anchor},
            location=anchor,
            **self._extract_cosmetic(mleader),
        )


class LeaderNormalizer(EntityNormalizer):
    """LEADER (구식) 엔티티 정규화 — Phase Q1 (RV-20260509-002).

    구식 LEADER (annotation 화살표 + 단일 직선). Hash 는 vertex 리스트.
    """

    @property
    def entity_type(self) -> str:
        return "LEADER"

    def normalize(self, leader) -> NormalizedEntity:
        verts: List[Tuple[float, float]] = []
        try:
            for v in leader.vertices:
                try:
                    verts.append(self._round_point(v))
                except Exception:
                    continue
        except Exception:
            pass

        if verts:
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        else:
            center = (0.0, 0.0)

        return NormalizedEntity(
            hash=self._generate_hash(f"LEADER:{tuple(verts)}"),
            entity_type="LEADER",
            layer=leader.dxf.layer,
            data={"vertices": verts, "vertex_count": len(verts)},
            location=center,
            **self._extract_cosmetic(leader),
        )


class SplineNormalizer(EntityNormalizer):
    """SPLINE 엔티티 정규화 — Phase Q1 (RV-20260509-002).

    SPLINE 의 control point 또는 fit point 정렬 hash.
    """

    @property
    def entity_type(self) -> str:
        return "SPLINE"

    def normalize(self, spline) -> NormalizedEntity:
        # control_points 우선, 없으면 fit_points
        points: List[Tuple[float, float]] = []
        try:
            for p in spline.control_points:
                try:
                    points.append(self._round_point(p))
                except Exception:
                    continue
        except Exception:
            pass
        if not points:
            try:
                for p in spline.fit_points:
                    try:
                        points.append(self._round_point(p))
                    except Exception:
                        continue
            except Exception:
                pass

        try:
            degree = int(getattr(spline.dxf, "degree", 3))
        except Exception:
            degree = 3
        try:
            closed = bool(getattr(spline, "closed", False))
        except Exception:
            closed = False

        if points:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        else:
            center = (0.0, 0.0)

        return NormalizedEntity(
            hash=self._generate_hash(
                f"SPLINE:{degree}:{closed}:{tuple(points)}"
            ),
            entity_type="SPLINE",
            layer=spline.dxf.layer,
            data={
                "degree": degree,
                "closed": closed,
                "point_count": len(points),
            },
            location=center,
            **self._extract_cosmetic(spline),
        )


class EllipseNormalizer(EntityNormalizer):
    """ELLIPSE 엔티티 정규화 — Phase Q1 (RV-20260509-002).

    중심 + major axis vector + ratio + start/end param.
    """

    @property
    def entity_type(self) -> str:
        return "ELLIPSE"

    def normalize(self, ellipse) -> NormalizedEntity:
        center = self._round_point(ellipse.dxf.center)
        try:
            major_axis = self._round_point(ellipse.dxf.major_axis)
        except Exception:
            major_axis = (1.0, 0.0)
        try:
            ratio = round(float(ellipse.dxf.ratio), self.precision)
        except Exception:
            ratio = 1.0
        try:
            start_param = round(float(ellipse.dxf.start_param), 3)
            end_param = round(float(ellipse.dxf.end_param), 3)
        except Exception:
            start_param = 0.0
            end_param = 6.283185

        return NormalizedEntity(
            hash=self._generate_hash(
                f"ELLIPSE:{center}:{major_axis}:{ratio}:{start_param}:{end_param}"
            ),
            entity_type="ELLIPSE",
            layer=ellipse.dxf.layer,
            data={
                "center": center,
                "major_axis": major_axis,
                "ratio": ratio,
                "start_param": start_param,
                "end_param": end_param,
            },
            location=center,
            **self._extract_cosmetic(ellipse),
        )


class NormalizerFactory:
    """엔티티 정규화 팩토리

    Strategy 패턴의 Context.
    엔티티 타입에 맞는 Normalizer를 생성하고 관리합니다.

    사용 예시:
        factory = NormalizerFactory(precision=2)
        normalizer = factory.get_normalizer("LINE")
        result = normalizer.normalize(line_entity)
    """

    # 지원하는 엔티티 타입과 Normalizer 클래스 매핑
    NORMALIZER_CLASSES: Dict[str, Type[EntityNormalizer]] = {
        "LINE": LineNormalizer,
        "CIRCLE": CircleNormalizer,
        "ARC": ArcNormalizer,
        "LWPOLYLINE": PolylineNormalizer,
        "POLYLINE": PolylineNormalizer,  # POLYLINE도 PolylineNormalizer 사용
        "TEXT": TextNormalizer,
        "MTEXT": MTextNormalizer,
        "DIMENSION": DimensionNormalizer,
        "INSERT": InsertNormalizer,
        # RV-20260508-003 — 블록 attribute 텍스트 변경 (예: 사용자
        # 사례 ``DOWEL BAR ... @100 -> @200``) 를 추출 단계에서 놓치지
        # 않도록 ATTRIB / ATTDEF 정식 지원.
        "ATTRIB": AttribNormalizer,
        "ATTDEF": AttdefNormalizer,
        # Phase Q1 (RV-20260509-002) — 누락된 6개 entity type 추가.
        # HATCH (단면 채움), SOLID (Tekla mass/plate), MULTILEADER (사용자
        # dowel callout 사례), LEADER (구식 화살표), SPLINE (곡선 보강근),
        # ELLIPSE (타원). Phase O 까지는 추출 자체에서 silent drop 되어
        # 변경 검출 불가능했음.
        "HATCH": HatchNormalizer,
        "SOLID": SolidNormalizer,
        "MULTILEADER": MLeaderNormalizer,
        "LEADER": LeaderNormalizer,
        "SPLINE": SplineNormalizer,
        "ELLIPSE": EllipseNormalizer,
    }

    def __init__(
        self,
        precision: int = 2,
        *,
        block_text_detection: bool = True,
    ):
        """
        Args:
            precision: 좌표 정밀도 (소수점 자릿수)
            block_text_detection: Phase O Commit 3 [RV-20260508-009] —
                INSERT block-internal text fingerprint 활성. False 시
                InsertNormalizer 가 fingerprint 계산을 skip 하여 hash
                가 Phase O Commit 1 이전 동작 (legacy) 으로 회귀.
                Workbench 의 "정밀 텍스트 감지" 체크박스 와 1:1 매핑.
        """
        self.precision = precision
        self._block_text_detection = bool(block_text_detection)
        self._cache: Dict[str, EntityNormalizer] = {}

    def get_normalizer(self, entity_type: str) -> Optional[EntityNormalizer]:
        """엔티티 타입에 맞는 Normalizer 반환

        Args:
            entity_type: 엔티티 타입 문자열

        Returns:
            EntityNormalizer 인스턴스 또는 None
        """
        if entity_type not in self.NORMALIZER_CLASSES:
            return None

        # 캐시된 인스턴스 반환 (동일 precision이면 재사용)
        if entity_type not in self._cache:
            normalizer_class = self.NORMALIZER_CLASSES[entity_type]
            # InsertNormalizer 는 block_text_detection 옵션을 받음;
            # 다른 normalizer 는 기본 시그니처 (precision 만).
            kwargs: Dict[str, Any] = {"precision": self.precision}
            if normalizer_class is InsertNormalizer:
                kwargs["block_text_detection"] = self._block_text_detection
            self._cache[entity_type] = normalizer_class(**kwargs)

        return self._cache[entity_type]

    def normalize(self, entity) -> Optional[NormalizedEntity]:
        """엔티티 정규화 (편의 메서드)

        Args:
            entity: ezdxf 엔티티 객체

        Returns:
            정규화된 엔티티 또는 None
        """
        entity_type = entity.dxftype()
        normalizer = self.get_normalizer(entity_type)

        if normalizer is None:
            return None

        return normalizer.normalize(entity)

    @classmethod
    def supported_types(cls) -> set:
        """지원하는 엔티티 타입 집합 반환"""
        return set(cls.NORMALIZER_CLASSES.keys())

    def clear_cache(self) -> None:
        """캐시 초기화"""
        self._cache.clear()

    def reset_per_extraction_state(self) -> None:
        """Codex P1 [RV-20260508-008] — 새 extraction 시작 시 호출되어
        normalizer 들이 보유한 doc-scoped 캐시를 무효화. ``id(doc)``
        기반 캐시는 doc GC 후 메모리 주소 재사용으로 stale 결과를
        반환할 수 있어 이 hook 으로 강제 무효화."""
        for normalizer in self._cache.values():
            reset = getattr(normalizer, "reset_per_extraction_state", None)
            if callable(reset):
                try:
                    reset()
                except Exception:
                    continue


# 기본 팩토리 인스턴스 (편의용)
_default_factory: Optional[NormalizerFactory] = None


def get_default_factory(precision: int = 2) -> NormalizerFactory:
    """기본 팩토리 인스턴스 반환

    Args:
        precision: 좌표 정밀도

    Returns:
        NormalizerFactory 싱글턴 인스턴스
    """
    global _default_factory
    if _default_factory is None or _default_factory.precision != precision:
        _default_factory = NormalizerFactory(precision=precision)
    return _default_factory
