"""Phase P (RV-20260508-014) — Revision cloud + triangle marker SSoT.

이전까지 도면 변경부 표기는 4개의 분리된 구현이 각자 다른 수학으로
"cloud" 를 그렸음:
- QML ``DrawingGpuViewport.qml:181-262`` — semicircular bumps
- Qt offline ``cloud_mark_layer.py:140-230`` — Bezier waves
- PIL raster ``confirmed_cloud_export.py:234-282`` — arc tile
- DXF ``dxf_cloud_marker.py:215-225`` — ezdxf.revcloud (표준)
- DXF ``pdf_cloud_dxf_export.py:402-406`` — **plain rectangle** (비표준)

사용자 요구 (2026-05-08): "일반적 도면상에 변경부에 클라우드 마크를
치는 게 일반적. 사용자에게 친숙하고 일반적인 표현으로 접근."

이 모듈은 AIA / KS / AutoCAD ``REVCLOUD`` 호환의 표준 revision cloud
지오메트리 + revision triangle (Δn) tag 를 모든 rendering path 가
공유하는 single source of truth 로 제공.

표준 reference:
- AIA G1.1 (Revision Clouds): closed bulge polyline, chord ≈ 1.5mm × scale
- AutoCAD REVCLOUD: arc length proportional to bbox perimeter (segment_length)
- AIA color scheme: cyan (ACI 4) for modified, green for added, magenta for deleted
- Lineweight 0.50mm (DXF lineweight=50) for revcloud, 0.35mm for tags
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 색상 표준화 — AIA / Industry 관행
# ---------------------------------------------------------------------------

# DXF ACI (AutoCAD Color Index) 표준 색상 매핑
ACI_CYAN = 4       # modified 변경 — AIA 표준
ACI_GREEN = 3      # added — 일반 관행
ACI_MAGENTA = 6    # deleted — 일반 관행
ACI_RED = 1        # legacy / 강조용
ACI_YELLOW = 2     # 라벨 텍스트
ACI_GRAY = 8       # secondary

# Hex (QML / Qt / PIL 등 RGB 환경)
HEX_CYAN = "#00FFFF"
HEX_GREEN = "#00CC00"
HEX_MAGENTA = "#FF00FF"
HEX_RED = "#DC2626"
HEX_BLACK = "#000000"

# Lineweight (1/100 mm)
LINEWEIGHT_REVCLOUD_MM = 50   # 0.50mm AIA 표준
LINEWEIGHT_TAG_MM = 35        # 0.35mm tag/leader
LINEWEIGHT_LABEL_MM = 25      # 0.25mm label text


ChangeKind = Literal["modified", "added", "deleted", "mixed"]


def color_aci_for_change(kind: ChangeKind, scheme: str = "aia") -> int:
    """변경 타입 → DXF ACI 색상.

    Args:
        kind: 변경 분류
        scheme: "aia" (cyan/green/magenta) 또는 "legacy" (red/blue/orange)
    """
    if scheme == "legacy":
        return {
            "modified": ACI_YELLOW,
            "added": ACI_GREEN,
            "deleted": ACI_RED,
            "mixed": ACI_RED,
        }.get(kind, ACI_RED)
    return {
        "modified": ACI_CYAN,
        "added": ACI_GREEN,
        "deleted": ACI_MAGENTA,
        "mixed": ACI_CYAN,
    }.get(kind, ACI_CYAN)


def color_hex_for_change(kind: ChangeKind, scheme: str = "aia") -> str:
    """변경 타입 → Hex 색상 (QML / Qt / PIL)."""
    if scheme == "legacy":
        return {
            "modified": "#FFA500",
            "added": HEX_GREEN,
            "deleted": HEX_RED,
            "mixed": HEX_RED,
        }.get(kind, HEX_RED)
    return {
        "modified": HEX_CYAN,
        "added": HEX_GREEN,
        "deleted": HEX_MAGENTA,
        "mixed": HEX_CYAN,
    }.get(kind, HEX_CYAN)


# ---------------------------------------------------------------------------
# Revision cloud chord 수학 (모든 path 공유)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevcloudGeometry:
    """Revision cloud 지오메트리 — bbox 기반 chord segments + bulges.

    AutoCAD REVCLOUD 와 호환되는 closed polyline 의 vertex+bulge 표현.
    Bulge 0.5 ≈ 90° 호 = revcloud 표준 (semicircular).
    """

    vertices: Tuple[Tuple[float, float], ...]
    bulges: Tuple[float, ...]  # vertex 와 1:1 — 다음 segment 의 호 곡률

    @property
    def is_closed(self) -> bool:
        return True


def compute_chord_length(
    bbox: Tuple[float, float, float, float],
    target_chords: int = 24,
    min_chord: float = 5.0,
    max_chord: float = 100.0,
) -> float:
    """Bbox 둘레를 기반으로 적절한 chord 길이 산출.

    AutoCAD REVCLOUD 의 segment_length 인자에 해당. 너무 짧으면 jagged,
    너무 길면 plain polyline 처럼 보임. 권장: 둘레 / 24 ≈ AIA 표준 1.5mm
    × 도면 스케일 (1:50 에서 약 7.5mm).

    Args:
        bbox: (x0, y0, x1, y1)
        target_chords: 권장 chord 개수 (둘레당)
        min_chord, max_chord: chord 길이 clamp (mm)
    """
    x0, y0, x1, y1 = bbox
    w = abs(x1 - x0)
    h = abs(y1 - y0)
    perimeter = 2 * (w + h)
    if perimeter <= 0:
        return min_chord
    chord = perimeter / max(1, target_chords)
    return max(min_chord, min(max_chord, chord))


def revcloud_geometry_from_bbox(
    bbox: Tuple[float, float, float, float],
    chord_length: Optional[float] = None,
    bulge: float = 0.5,
) -> RevcloudGeometry:
    """Bbox 사방을 chord 길이로 분할하고 각 segment 에 bulge 부여.

    bulge=0.5 → 90° 호 (semicircular outward). AutoCAD REVCLOUD 와 동일.

    Returns:
        RevcloudGeometry — 시작 vertex 부터 시계 방향. Polyline 가 ``close=
        True`` 로 닫히면 closed bumpy curve.
    """
    x0, y0, x1, y1 = bbox
    if chord_length is None:
        chord_length = compute_chord_length(bbox)

    vertices: List[Tuple[float, float]] = []

    def _segment(start: Tuple[float, float], end: Tuple[float, float]) -> None:
        sx, sy = start
        ex, ey = end
        dx = ex - sx
        dy = ey - sy
        length = math.hypot(dx, dy)
        if length <= 0:
            vertices.append(start)
            return
        n = max(1, int(round(length / chord_length)))
        for i in range(n):
            t = i / n
            vertices.append((sx + dx * t, sy + dy * t))

    # 시계 방향 4 변 — bottom → right → top → left
    _segment((x0, y0), (x1, y0))
    _segment((x1, y0), (x1, y1))
    _segment((x1, y1), (x0, y1))
    _segment((x0, y1), (x0, y0))

    bulges = tuple(bulge for _ in vertices)
    return RevcloudGeometry(
        vertices=tuple(vertices),
        bulges=bulges,
    )


# ---------------------------------------------------------------------------
# Revision triangle (Δn) tag
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevisionTriangle:
    """삼각형 revision tag — 꼭지점 + 안쪽 텍스트 위치 + 크기."""

    apex: Tuple[float, float]            # 위 꼭지점
    bottom_left: Tuple[float, float]
    bottom_right: Tuple[float, float]
    text_anchor: Tuple[float, float]     # 중심 (텍스트 배치)
    size: float                          # 한 변 길이 (mm)
    revision_number: int


def revision_triangle_from_anchor(
    anchor: Tuple[float, float],
    revision_number: int,
    size: float = 8.0,
) -> RevisionTriangle:
    """앵커 (예: cloud bbox 의 우상단) 에 revision triangle 배치.

    AIA 표준: equilateral triangle, apex 위, 안쪽 중심에 revision number.

    Args:
        anchor: triangle 의 중심 위치 (bbox 의 attached point)
        revision_number: Δn 의 n 값 (1, 2, 3, ...)
        size: 변 길이 (mm). AIA 권장 6-10mm @ 1:50 도면.
    """
    cx, cy = anchor
    h = size * (math.sqrt(3) / 2)  # equilateral 높이
    return RevisionTriangle(
        apex=(cx, cy + h * 2 / 3),
        bottom_left=(cx - size / 2, cy - h / 3),
        bottom_right=(cx + size / 2, cy - h / 3),
        text_anchor=(cx, cy - size * 0.15),
        size=size,
        revision_number=revision_number,
    )


# ---------------------------------------------------------------------------
# DXF rendering helpers (ezdxf 의존, lazy import)
# ---------------------------------------------------------------------------


def add_revcloud_to_msp(
    msp: object,  # ezdxf modelspace
    bbox: Tuple[float, float, float, float],
    *,
    layer: str = "CLOUD_MARKS",
    kind: ChangeKind = "modified",
    color_scheme: str = "aia",
    chord_length: Optional[float] = None,
    use_native_revcloud: bool = True,
) -> object:
    """DXF modelspace 에 표준 revcloud entity 추가. 결과 entity 반환.

    use_native_revcloud=True 면 ezdxf.revcloud.add_entity 사용 (AutoCAD
    REVCLOUD 호환), False 면 vertex+bulge LWPOLYLINE 으로 fallback.
    """
    aci = color_aci_for_change(kind, scheme=color_scheme)
    dxfattribs = {
        "layer": layer,
        "color": aci,
        "lineweight": LINEWEIGHT_REVCLOUD_MM,
    }

    if use_native_revcloud:
        try:
            from ezdxf import revcloud  # type: ignore

            x0, y0, x1, y1 = bbox
            seg_len = chord_length or compute_chord_length(bbox)
            return revcloud.add_entity(
                msp,
                vertices=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                segment_length=seg_len,
                calligraphy=True,
                dxfattribs=dxfattribs,
            )
        except (ImportError, AttributeError):
            pass  # fallback below

    # Fallback: bulge LWPOLYLINE
    geom = revcloud_geometry_from_bbox(bbox, chord_length=chord_length)
    poly = msp.add_lwpolyline(
        [(v[0], v[1], 0, 0, b) for v, b in zip(geom.vertices, geom.bulges)],
        format="xyseb",
        close=True,
        dxfattribs=dxfattribs,
    )
    return poly


def add_revision_triangle_to_msp(
    msp: object,
    anchor: Tuple[float, float],
    revision_number: int,
    *,
    size: float = 8.0,
    layer: str = "CLOUD_LABELS",
    kind: ChangeKind = "modified",
    color_scheme: str = "aia",
) -> List[object]:
    """DXF modelspace 에 revision triangle (closed polyline + 텍스트) 추가.

    AIA 표준: 삼각형 + 안쪽 중심 revision number. apex 위.
    """
    aci = color_aci_for_change(kind, scheme=color_scheme)
    triangle = revision_triangle_from_anchor(anchor, revision_number, size=size)

    poly = msp.add_lwpolyline(
        [
            triangle.apex,
            triangle.bottom_left,
            triangle.bottom_right,
            triangle.apex,
        ],
        close=True,
        dxfattribs={
            "layer": layer,
            "color": aci,
            "lineweight": LINEWEIGHT_TAG_MM,
        },
    )

    text = msp.add_text(
        str(revision_number),
        dxfattribs={
            "layer": layer,
            "color": aci,
            "height": size * 0.5,
        },
    )
    # ezdxf 1.0+: text.set_placement
    try:
        text.set_placement(triangle.text_anchor, align="MIDDLE_CENTER")  # type: ignore
    except Exception:
        try:
            text.dxf.insert = triangle.text_anchor  # type: ignore
            text.dxf.halign = 4  # middle  # type: ignore
            text.dxf.valign = 2  # center  # type: ignore
        except Exception:
            pass

    return [poly, text]


__all__ = [
    "ACI_CYAN", "ACI_GREEN", "ACI_MAGENTA", "ACI_RED", "ACI_YELLOW", "ACI_GRAY",
    "HEX_CYAN", "HEX_GREEN", "HEX_MAGENTA", "HEX_RED", "HEX_BLACK",
    "LINEWEIGHT_REVCLOUD_MM", "LINEWEIGHT_TAG_MM", "LINEWEIGHT_LABEL_MM",
    "ChangeKind",
    "color_aci_for_change", "color_hex_for_change",
    "RevcloudGeometry", "compute_chord_length", "revcloud_geometry_from_bbox",
    "RevisionTriangle", "revision_triangle_from_anchor",
    "add_revcloud_to_msp", "add_revision_triangle_to_msp",
]
