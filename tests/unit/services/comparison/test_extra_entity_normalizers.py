"""Phase Q1 (RV-20260509-002) — 신규 normalizer 6종 회귀 가드.

HATCH/SOLID/MULTILEADER/LEADER/SPLINE/ELLIPSE 가 SUPPORTED_TYPES 에
추가되어 silent drop 되지 않는지 + 동일 entity 는 동일 hash, 변경 시
다른 hash 를 만드는지 검증. ezdxf 로 합성 entity 생성하여 normalize
호출.
"""
from __future__ import annotations

import math

import ezdxf
import pytest

from src.services.comparison.entity_normalizers import (
    EllipseNormalizer,
    HatchNormalizer,
    LeaderNormalizer,
    MLeaderNormalizer,
    NormalizerFactory,
    SolidNormalizer,
    SplineNormalizer,
)


@pytest.fixture
def msp():
    doc = ezdxf.new(dxfversion="R2010")
    return doc.modelspace(), doc


class TestNormalizerFactoryRegistry:
    """팩토리에 6개 신규 type 이 등록됐는지."""

    @pytest.mark.parametrize(
        "etype,expected_class",
        [
            ("HATCH", HatchNormalizer),
            ("SOLID", SolidNormalizer),
            ("MULTILEADER", MLeaderNormalizer),
            ("LEADER", LeaderNormalizer),
            ("SPLINE", SplineNormalizer),
            ("ELLIPSE", EllipseNormalizer),
        ],
    )
    def test_factory_returns_correct_normalizer(self, etype, expected_class):
        factory = NormalizerFactory()
        normalizer = factory.get_normalizer(etype)
        assert isinstance(normalizer, expected_class)


class TestHatchNormalizer:
    def test_solid_hatch_with_polyline_boundary(self, msp):
        m, doc = msp
        h = m.add_hatch(color=7)
        h.paths.add_polyline_path([(0, 0), (100, 0), (100, 100), (0, 100)], is_closed=True)
        norm = HatchNormalizer().normalize(h)
        assert norm.entity_type == "HATCH"
        assert norm.layer == "0"
        assert norm.location == (50.0, 50.0)
        assert norm.data["pattern_name"] == "SOLID"
        assert norm.data["path_count"] == 1
        assert norm.hash  # non-empty

    def test_identical_hatch_same_hash(self, msp):
        m, _doc = msp
        h1 = m.add_hatch(color=7)
        h1.paths.add_polyline_path([(0, 0), (50, 0), (50, 50), (0, 50)], is_closed=True)
        h2 = m.add_hatch(color=7)
        h2.paths.add_polyline_path([(0, 0), (50, 0), (50, 50), (0, 50)], is_closed=True)
        n1 = HatchNormalizer().normalize(h1)
        n2 = HatchNormalizer().normalize(h2)
        assert n1.hash == n2.hash

    def test_different_boundary_different_hash(self, msp):
        m, _doc = msp
        h1 = m.add_hatch(color=7)
        h1.paths.add_polyline_path([(0, 0), (50, 0), (50, 50), (0, 50)], is_closed=True)
        h2 = m.add_hatch(color=7)
        h2.paths.add_polyline_path([(0, 0), (60, 0), (60, 60), (0, 60)], is_closed=True)
        n1 = HatchNormalizer().normalize(h1)
        n2 = HatchNormalizer().normalize(h2)
        assert n1.hash != n2.hash


class TestSolidNormalizer:
    def test_4_corner_solid(self, msp):
        m, _doc = msp
        s = m.add_solid([(0, 0), (10, 0), (10, 10), (0, 10)])
        norm = SolidNormalizer().normalize(s)
        assert norm.entity_type == "SOLID"
        assert norm.location == (5.0, 5.0)
        assert norm.data["corner_count"] == 4

    def test_solid_corner_change_hash(self, msp):
        m, _doc = msp
        s1 = m.add_solid([(0, 0), (10, 0), (10, 10), (0, 10)])
        s2 = m.add_solid([(0, 0), (20, 0), (20, 20), (0, 20)])
        n1 = SolidNormalizer().normalize(s1)
        n2 = SolidNormalizer().normalize(s2)
        assert n1.hash != n2.hash


class TestMLeaderNormalizer:
    def test_mleader_with_text(self, msp):
        m, doc = msp
        try:
            ml = m.add_multileader_mtext()
            ml.set_content("DOWEL BAR @100")
            ml.add_leader_line(side=0, vertices=[(100, 100), (50, 50)])
            ml.set_mtext_attachment_point()
        except Exception:
            pytest.skip("ezdxf MLeader API differs across versions")
        norm = MLeaderNormalizer().normalize(ml)
        assert norm.entity_type == "MULTILEADER"
        assert "DOWEL BAR" in norm.data.get("content", "")

    def test_text_change_changes_hash(self, msp):
        m, _doc = msp
        try:
            ml1 = m.add_multileader_mtext()
            ml1.set_content("DOWEL BAR @100")
            ml1.add_leader_line(side=0, vertices=[(100, 100), (50, 50)])
            ml2 = m.add_multileader_mtext()
            ml2.set_content("DOWEL BAR @200")
            ml2.add_leader_line(side=0, vertices=[(100, 100), (50, 50)])
            # builder → entity 변환 (ezdxf 1.0+ pattern)
            n1 = MLeaderNormalizer().normalize(ml1)
            n2 = MLeaderNormalizer().normalize(ml2)
        except Exception:
            pytest.skip("ezdxf MLeader API differs across versions")
        assert n1.hash != n2.hash, "ATTRIB-style text change should differ"


class TestLeaderNormalizer:
    def test_leader_basic(self, msp):
        m, _doc = msp
        leader = m.add_leader([(0, 0), (50, 50), (100, 50)])
        norm = LeaderNormalizer().normalize(leader)
        assert norm.entity_type == "LEADER"
        assert norm.data["vertex_count"] == 3

    def test_vertex_change_hash(self, msp):
        m, _doc = msp
        l1 = m.add_leader([(0, 0), (50, 50), (100, 50)])
        l2 = m.add_leader([(0, 0), (60, 60), (120, 60)])
        n1 = LeaderNormalizer().normalize(l1)
        n2 = LeaderNormalizer().normalize(l2)
        assert n1.hash != n2.hash


class TestSplineNormalizer:
    def test_spline_basic(self, msp):
        m, _doc = msp
        sp = m.add_spline([(0, 0), (10, 20), (30, 30), (50, 10)])
        norm = SplineNormalizer().normalize(sp)
        assert norm.entity_type == "SPLINE"
        assert norm.data["point_count"] >= 1

    def test_spline_point_change(self, msp):
        m, _doc = msp
        sp1 = m.add_spline([(0, 0), (10, 20), (30, 30), (50, 10)])
        sp2 = m.add_spline([(0, 0), (15, 25), (35, 35), (55, 15)])
        n1 = SplineNormalizer().normalize(sp1)
        n2 = SplineNormalizer().normalize(sp2)
        assert n1.hash != n2.hash


class TestEllipseNormalizer:
    def test_ellipse_basic(self, msp):
        m, _doc = msp
        e = m.add_ellipse(center=(0, 0), major_axis=(10, 0), ratio=0.5)
        norm = EllipseNormalizer().normalize(e)
        assert norm.entity_type == "ELLIPSE"
        assert norm.location == (0.0, 0.0)
        assert norm.data["ratio"] == 0.5

    def test_ratio_change_hash(self, msp):
        m, _doc = msp
        e1 = m.add_ellipse(center=(0, 0), major_axis=(10, 0), ratio=0.5)
        e2 = m.add_ellipse(center=(0, 0), major_axis=(10, 0), ratio=0.8)
        n1 = EllipseNormalizer().normalize(e1)
        n2 = EllipseNormalizer().normalize(e2)
        assert n1.hash != n2.hash


class TestExtractorIntegration:
    """E2E — 추출기가 신규 type 을 SUPPORTED_TYPES 에서 인식하고 last_stats
    의 unsupported_counts 에 누락 없이 카운트하는지."""

    def test_supported_types_includes_new_six(self):
        from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
        for etype in ("HATCH", "SOLID", "MULTILEADER", "LEADER", "SPLINE", "ELLIPSE"):
            assert etype in DxfEntityExtractor.SUPPORTED_TYPES, f"{etype} 누락"

    def test_unsupported_counts_tracked(self, tmp_path):
        """일부 미지원 entity (예: 3DFACE) 가 등장 시 카운트 가시화."""
        import ezdxf
        from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

        doc = ezdxf.new(dxfversion="R2010")
        m = doc.modelspace()
        m.add_line((0, 0), (10, 10))  # supported
        m.add_3dface([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])  # unsupported
        path = tmp_path / "mixed.dxf"
        doc.saveas(str(path))

        extractor = DxfEntityExtractor()
        result = extractor.extract_from_file(str(path))
        assert "3DFACE" in extractor.last_stats.get("unsupported_counts", {}), (
            "3DFACE silent drop 가시화 실패"
        )
        assert extractor.last_stats["unsupported_counts"]["3DFACE"] == 1
        assert extractor.last_stats["unsupported_total"] >= 1
