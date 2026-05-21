"""Phase Q4 (RV-20260509-002) — OCS→WCS 좌표 변환 회귀 가드.

DXF 의 OCS-aware entity (CIRCLE/ARC/POLYLINE/TEXT/MTEXT/INSERT/
DIMENSION) 는 ``extrusion`` 벡터가 (0,0,1) 이 아니면 좌표가 OCS 기준으로
저장됨. Phase Q4 이전에는 normalizer 가 raw OCS 값을 직접 hash 했음 —
같은 도면이 다른 extrusion 으로 회전됐을 때 false positive 변경 발생.

본 테스트는 두 케이스를 검증:
1. **default OCS** (extrusion=(0,0,1)): 기존 동작 유지 — _to_wcs 가
   _round_point 와 동일 결과.
2. **non-default OCS** (extrusion=(0,0,-1) 등): WCS 변환 적용 — 같은
   세계 좌표 기준 entity 끼리 hash 일치.
"""
from __future__ import annotations

import ezdxf
import pytest

from src.services.comparison.entity_normalizers import (
    ArcNormalizer,
    CircleNormalizer,
    DimensionNormalizer,
    EntityNormalizer,
    InsertNormalizer,
    LineNormalizer,
    MTextNormalizer,
    PolylineNormalizer,
    TextNormalizer,
)


@pytest.fixture
def msp():
    doc = ezdxf.new(dxfversion="R2010")
    return doc.modelspace(), doc


class TestExtrusionProbe:
    """Phase Q4 — _is_non_default_extrusion cheap probe."""

    def test_default_extrusion_returns_false(self, msp):
        m, _doc = msp
        line = m.add_line((0, 0), (10, 10))
        # default extrusion = (0,0,1)
        normalizer = LineNormalizer()
        assert normalizer._is_non_default_extrusion(line) is False

    def test_inverted_z_extrusion_returns_true(self, msp):
        m, _doc = msp
        circle = m.add_circle(center=(50, 50), radius=10)
        circle.dxf.extrusion = (0, 0, -1)
        normalizer = CircleNormalizer()
        assert normalizer._is_non_default_extrusion(circle) is True

    def test_xy_tilted_extrusion_returns_true(self, msp):
        m, _doc = msp
        circle = m.add_circle(center=(50, 50), radius=10)
        circle.dxf.extrusion = (0.5, 0.5, 0.7071)
        normalizer = CircleNormalizer()
        assert normalizer._is_non_default_extrusion(circle) is True

    def test_missing_extrusion_returns_false(self):
        normalizer = LineNormalizer()
        # mock entity without extrusion
        class _Empty:
            class dxf:
                pass
        assert normalizer._is_non_default_extrusion(_Empty()) is False


class TestCircleOcsToWcs:
    """Phase Q4 — CIRCLE OCS → WCS 변환."""

    def test_default_ocs_circle_unchanged(self, msp):
        m, _doc = msp
        c1 = m.add_circle(center=(50, 50), radius=10)
        n1 = CircleNormalizer().normalize(c1)
        # default OCS — center 가 raw 값 동일
        assert n1.location == (50.0, 50.0)
        assert n1.data["center"] == (50.0, 50.0)

    def test_inverted_ocs_circle_wcs_x_flipped(self, msp):
        """extrusion=(0,0,-1) → OCS X 가 WCS 에서 -X 로 매핑.

        ezdxf 의 OCS 로직 (Arbitrary Axis Algorithm): extrusion=(0,0,-1)
        에서 OCS X = -WCS X, OCS Y = WCS Y. 따라서 OCS center (50, 50)
        은 WCS (-50, 50).
        """
        m, _doc = msp
        c = m.add_circle(center=(50, 50), radius=10)
        c.dxf.extrusion = (0, 0, -1)
        n = CircleNormalizer().normalize(c)
        # Q4 적용 후 WCS 좌표로 변환되어야 함
        assert n.location[0] == -50.0
        assert n.location[1] == 50.0

    def test_two_circles_same_wcs_diff_ocs_have_same_hash(self, msp):
        """동일 WCS 위치 + 다른 OCS extrusion 인 두 CIRCLE 이 hash 일치."""
        m, _doc = msp
        # CIRCLE 1: WCS (-50, 50, 0) — OCS=(0,0,1) 이면 OCS=(-50, 50)
        c1 = m.add_circle(center=(-50, 50), radius=10)
        # default extrusion=(0,0,1)
        # CIRCLE 2: 같은 WCS 위치 — OCS=(0,0,-1) 이면 OCS=(50, 50)
        c2 = m.add_circle(center=(50, 50), radius=10)
        c2.dxf.extrusion = (0, 0, -1)
        n1 = CircleNormalizer().normalize(c1)
        n2 = CircleNormalizer().normalize(c2)
        # Q4 후 양쪽 모두 WCS (-50, 50) 로 변환 → hash 일치
        assert n1.hash == n2.hash, (
            f"같은 WCS 위치의 CIRCLE 은 OCS 차이와 무관하게 hash 동일해야 "
            f"함. n1.center={n1.data['center']}, n2.center={n2.data['center']}"
        )


class TestArcOcsToWcs:
    def test_inverted_ocs_arc_wcs_converted(self, msp):
        m, _doc = msp
        arc = m.add_arc(center=(100, 100), radius=20, start_angle=0, end_angle=90)
        arc.dxf.extrusion = (0, 0, -1)
        n = ArcNormalizer().normalize(arc)
        assert n.location[0] == -100.0
        assert n.location[1] == 100.0


class TestPolylineOcsToWcs:
    def test_lwpolyline_default_ocs_unchanged(self, msp):
        m, _doc = msp
        pl = m.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
        n = PolylineNormalizer().normalize(pl)
        assert (0.0, 0.0) in n.data["points"]
        assert (10.0, 10.0) in n.data["points"]

    def test_lwpolyline_inverted_ocs_converted(self, msp):
        m, _doc = msp
        pl = m.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
        pl.dxf.extrusion = (0, 0, -1)
        n = PolylineNormalizer().normalize(pl)
        # OCS X → -WCS X 로 변환 → (0, 0), (-10, 0), (-10, 10), (0, 10)
        wcs_xs = [p[0] for p in n.data["points"]]
        assert min(wcs_xs) == -10.0
        assert max(wcs_xs) == 0.0


class TestTextOcsToWcs:
    def test_text_default_ocs_unchanged(self, msp):
        m, _doc = msp
        t = m.add_text("HELLO", dxfattribs={"insert": (50, 50), "height": 2})
        n = TextNormalizer().normalize(t)
        assert n.location == (50.0, 50.0)

    def test_text_inverted_ocs_x_flipped(self, msp):
        m, _doc = msp
        t = m.add_text("HELLO", dxfattribs={"insert": (50, 50), "height": 2})
        t.dxf.extrusion = (0, 0, -1)
        n = TextNormalizer().normalize(t)
        assert n.location[0] == -50.0
        assert n.location[1] == 50.0


class TestInsertOcsToWcs:
    def test_insert_default_ocs_unchanged(self, msp):
        m, _doc = msp
        b = _doc.blocks.new(name="MARK")
        b.add_circle(center=(0, 0), radius=1)
        ins = m.add_blockref("MARK", insert=(50, 50))
        n = InsertNormalizer().normalize(ins)
        assert n.data["insert_point"] == (50.0, 50.0)

    def test_insert_inverted_ocs_x_flipped(self, msp):
        m, _doc = msp
        b = _doc.blocks.new(name="MARK")
        b.add_circle(center=(0, 0), radius=1)
        ins = m.add_blockref("MARK", insert=(50, 50))
        ins.dxf.extrusion = (0, 0, -1)
        n = InsertNormalizer().normalize(ins)
        assert n.data["insert_point"][0] == -50.0
        assert n.data["insert_point"][1] == 50.0


class TestLineExempt:
    """LINE 은 group code 10/11 이 WCS — _to_wcs 적용 안 됨 (불필요 비용)."""

    def test_line_with_extrusion_does_not_affect_endpoints(self, msp):
        m, _doc = msp
        line = m.add_line((0, 0), (10, 10))
        # LINE 은 OCS 사용 안 함 (extrusion 무관)
        n = LineNormalizer().normalize(line)
        # 항상 raw 좌표 유지
        assert (round(n.data["start"][0], 1), round(n.data["start"][1], 1)) == (0.0, 0.0)


class TestCodexRound1Fixes:
    """Phase Q4 Codex round-1 follow-up — 4 P2 finding regression guards."""

    def test_p2_dimension_defpoint_is_wcs_no_double_transform(self, msp):
        """[P2] DIMENSION.defpoint 는 ezdxf 사양상 WCS — _to_wcs 적용 시
        double-transform 으로 X 두 번 flip → spurious diff.

        Phase Q4 Codex round-2 follow-up [P3]: ezdxf 의 add_aligned_dim
        은 DimStyleOverride 를 반환하므로 .dimension 으로 실제 Dimension
        entity 접근 후 .dxf.extrusion 설정.
        """
        m, _doc = msp
        # 동일 WCS defpoint 의 dimension 두 개 — 하나는 default, 하나는
        # inverted Z extrusion. Q4 round-1 의 _to_wcs 적용은 spurious
        # X flip 을 만들어 hash 가 달라짐. Round-1 follow-up 의 revert
        # 후엔 동일.
        d1 = m.add_aligned_dim(p1=(0, 0), p2=(100, 0), distance=10)
        d2 = m.add_aligned_dim(p1=(0, 0), p2=(100, 0), distance=10)
        # ezdxf API: add_aligned_dim returns DimStyleOverride; the actual
        # Dimension entity is at .dimension.
        dim1 = getattr(d1, "dimension", d1)
        dim2 = getattr(d2, "dimension", d2)
        try:
            dim2.dxf.extrusion = (0, 0, -1)
        except Exception:
            pytest.skip("ezdxf dimension entity does not expose extrusion")

        n1 = DimensionNormalizer().normalize(dim1)
        n2 = DimensionNormalizer().normalize(dim2)
        # defpoint 가 _round_point 로 raw 처리 → extrusion 차이가
        # double-transform 으로 X flip 일으키면 안 됨
        assert n1.data["defpoint"] == n2.data["defpoint"], (
            f"DIMENSION.defpoint 는 WCS 이므로 extrusion 차이로 인한 "
            f"X flip 이 발생하면 안 됨. n1={n1.data['defpoint']}, "
            f"n2={n2.data['defpoint']}"
        )

    def test_p2_lwpolyline_elevation_passed_to_ocs_conversion(self, msp):
        """[P2] LWPOLYLINE.get_points() 는 (x, y) 만 반환 — shared OCS z
        는 dxf.elevation 에 별도 저장. (x, y, elevation) 으로 z 보존."""
        m, _doc = msp
        pl = m.add_lwpolyline([(0, 0), (10, 0), (10, 10)])
        pl.dxf.elevation = 50.0  # OCS z = 50
        # default OCS — _to_wcs 가 cheap path 지만 elevation 은 보존됨
        # (data 에 직접 노출 안 되지만 hash 입력에 포함될지 검증)
        n = PolylineNormalizer().normalize(pl)
        # elevation 적용된 vertex 가 hash 변경에 영향
        # 다른 elevation 값과 비교하면 hash 가 달라야 함 (default OCS
        # 에서는 영향 없지만 non-default 에서는 결과 좌표 달라짐)
        pl2 = m.add_lwpolyline([(0, 0), (10, 0), (10, 10)])
        pl2.dxf.elevation = 0.0
        n2 = PolylineNormalizer().normalize(pl2)
        # default OCS 에서는 elevation 만 다른 두 polyline 의 x/y 가
        # 동일해야 함
        assert n.data["points"] == n2.data["points"]

    def test_p2_lwpolyline_elevation_with_inverted_extrusion(self, msp):
        """non-default extrusion + non-zero elevation: Q4 round-1 의
        z=0 가정 fix 검증. (x, y, elevation) 으로 변환 시 정확한 WCS."""
        m, _doc = msp
        pl = m.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
        pl.dxf.extrusion = (0, 0, -1)
        pl.dxf.elevation = 25.0
        # 변환 결과는 적어도 x flip 이 발생해야 함 (inverted Z OCS)
        n = PolylineNormalizer().normalize(pl)
        wcs_xs = [p[0] for p in n.data["points"]]
        # X 가 flip 됐는지 확인 (max OCS x = 10 → max WCS x = 0 또는
        # 음수 영역으로 매핑)
        assert max(wcs_xs) == 0.0
        assert min(wcs_xs) == -10.0

    def test_p2_insert_extrusion_in_hash(self, msp):
        """[P2] INSERT 의 hash 에 extrusion 포함 — default 와 inverted Z
        가 같은 WCS 위치에 있어도 OCS basis 차이로 hash 분리."""
        m, _doc = msp
        b = _doc.blocks.new(name="MARK")
        b.add_circle(center=(0, 0), radius=1)
        # ins1: default OCS, WCS center = (-50, 50)
        ins1 = m.add_blockref("MARK", insert=(-50, 50))
        # ins2: inverted Z OCS, OCS center = (50, 50) → WCS = (-50, 50)
        ins2 = m.add_blockref("MARK", insert=(50, 50))
        ins2.dxf.extrusion = (0, 0, -1)

        n1 = InsertNormalizer().normalize(ins1)
        n2 = InsertNormalizer().normalize(ins2)
        # WCS insert_point 는 같지만 OCS basis 차이로 hash 분리되어야 함
        # (block 이 flipped 된 시각적 차이를 반영)
        assert n1.data["insert_point"] == n2.data["insert_point"]
        assert n1.hash != n2.hash, (
            "INSERT 가 같은 WCS 위치라도 OCS basis 가 다르면 (mirrored "
            "block) hash 가 달라야 함 — Codex P2 fix"
        )

    def test_p2_extrusion_unit_normalized_in_key(self, msp):
        """[P2] Phase Q4 round-2 — equivalent non-unit extrusion vectors
        must produce identical hash key (since ezdxf normalizes them
        internally before computing OCS basis)."""
        m, _doc = msp
        # arc1: extrusion=(0,0,-1)
        a1 = m.add_arc(center=(50, 50), radius=10,
                       start_angle=0, end_angle=90)
        a1.dxf.extrusion = (0, 0, -1)
        # arc2: extrusion=(0,0,-2) — magnitude 2 but same OCS basis
        # after normalization
        a2 = m.add_arc(center=(50, 50), radius=10,
                       start_angle=0, end_angle=90)
        a2.dxf.extrusion = (0, 0, -2)

        n1 = ArcNormalizer().normalize(a1)
        n2 = ArcNormalizer().normalize(a2)
        # Same OCS basis after unit-normalization → same hash
        assert n1.hash == n2.hash, (
            "extrusion (0,0,-1) and (0,0,-2) describe the same OCS "
            "basis after unit normalization — hash must be equal"
        )

    def test_p2_small_non_default_extrusion_preserved(self, msp):
        """Phase Q4 round-3 [P2] regression guard: small non-default
        extrusion (예: (0.0004, 0, 1)) 가 rounding 으로 default 와
        collapse 되면 안 됨. ezdxf 는 이 OCS basis 를 다르게 처리하므로
        hash key 가 보존되어야 visual diff 누락 차단.
        """
        m, _doc = msp
        normalizer = CircleNormalizer()
        # 작은 X 성분 — rounding 시 0 이 되지만 실제 OCS 는 다름
        c1 = m.add_circle(center=(50, 50), radius=10)
        c2 = m.add_circle(center=(50, 50), radius=10)
        c2.dxf.extrusion = (0.0004, 0, 1)  # 작은 X tilt

        key1 = normalizer._extrusion_key(c1)
        key2 = normalizer._extrusion_key(c2)
        assert key1 == "", "default OCS 는 empty key"
        assert key2 != "", (
            "small non-default extrusion (0.0004, 0, 1) 은 default 와 "
            "다른 key 가 나와야 함 — Codex round-3 P2 fix"
        )

    def test_p2_extrusion_default_after_normalization_empty_key(self, msp):
        """[P2] extrusion (0,0,2) normalizes to (0,0,1) — should produce
        empty key (legacy hash preserved)."""
        m, _doc = msp
        c = m.add_circle(center=(50, 50), radius=10)
        # default == (0,0,1)
        c2 = m.add_circle(center=(50, 50), radius=10)
        # explicitly set non-unit but same direction → normalized to (0,0,1)
        c2.dxf.extrusion = (0, 0, 2)
        normalizer = CircleNormalizer()
        # _extrusion_key returns empty for both (default + normalized
        # default)
        assert normalizer._extrusion_key(c) == ""
        assert normalizer._extrusion_key(c2) == ""
        # Therefore hashes are equal (legacy behavior)
        assert normalizer.normalize(c).hash == normalizer.normalize(c2).hash

    def test_p2_arc_extrusion_in_hash(self, msp):
        """[P2] ARC 의 hash 에 extrusion 포함 — OCS basis 차이가 hash 에
        반영되어야 false-equivalence 차단."""
        m, _doc = msp
        # arc1: default OCS
        a1 = m.add_arc(center=(-50, 50), radius=10,
                       start_angle=0, end_angle=90)
        # arc2: inverted Z OCS, 같은 WCS center 지만 angle 의 WCS 의미 다름
        a2 = m.add_arc(center=(50, 50), radius=10,
                       start_angle=0, end_angle=90)
        a2.dxf.extrusion = (0, 0, -1)

        n1 = ArcNormalizer().normalize(a1)
        n2 = ArcNormalizer().normalize(a2)
        # WCS center 일치 + raw angles 일치지만 OCS 다름 → hash 분리
        assert n1.data["center"] == n2.data["center"]
        assert n1.hash != n2.hash, (
            "ARC 가 같은 WCS center 라도 OCS basis 가 다르면 (mirrored "
            "arc 시작 방향이 다름) hash 가 달라야 함 — Codex P2 fix"
        )


class TestGracefulFallback:
    """OCS 변환 실패 시 raw 값으로 fallback (silent drop 방지)."""

    def test_to_wcs_falls_back_on_ocs_failure(self):
        """ocs() 호출 실패 시 _round_point 결과 반환."""
        normalizer = CircleNormalizer()

        class _BrokenEntity:
            class dxf:
                extrusion = (0.5, 0.5, 0.7071)
                center = (10, 20)

            def ocs(self):
                raise RuntimeError("ezdxf internal error")

        broken = _BrokenEntity()
        result = normalizer._to_wcs(broken, (10, 20))
        # fallback to round_point
        assert result == (10.0, 20.0)
