"""엔티티 정규화 전략 패턴 테스트

Sprint 9 Phase 2: P1-3 EntityNormalizer 테스트
"""

from unittest.mock import MagicMock, Mock, PropertyMock

import pytest

from src.services.comparison.entity_normalizers import (
    ArcNormalizer,
    CircleNormalizer,
    DimensionNormalizer,
    EntityNormalizer,
    InsertNormalizer,
    LineNormalizer,
    MTextNormalizer,
    NormalizedEntity,
    NormalizerFactory,
    PolylineNormalizer,
    TextNormalizer,
    get_default_factory,
)


class TestNormalizedEntity:
    """NormalizedEntity 테스트"""

    def test_creation(self):
        """기본 생성 테스트"""
        entity = NormalizedEntity(
            hash="abc123",
            entity_type="LINE",
            layer="0",
            data={"start": (0, 0), "end": (10, 10)},
            location=(5, 5),
        )
        assert entity.hash == "abc123"
        assert entity.entity_type == "LINE"
        assert entity.layer == "0"
        assert entity.parent_block is None

    def test_equality_by_hash(self):
        """해시 기반 동등성 테스트"""
        e1 = NormalizedEntity(
            hash="same_hash",
            entity_type="LINE",
            layer="0",
            data={},
            location=(0, 0),
        )
        e2 = NormalizedEntity(
            hash="same_hash",
            entity_type="CIRCLE",  # 다른 타입
            layer="1",  # 다른 레이어
            data={"different": True},
            location=(100, 100),
        )
        assert e1 == e2

    def test_inequality(self):
        """다른 해시 불동등성 테스트"""
        e1 = NormalizedEntity(
            hash="hash1", entity_type="LINE", layer="0", data={}, location=(0, 0)
        )
        e2 = NormalizedEntity(
            hash="hash2", entity_type="LINE", layer="0", data={}, location=(0, 0)
        )
        assert e1 != e2

    def test_hash_function(self):
        """해시 함수 테스트 (set/dict 사용 가능)"""
        e1 = NormalizedEntity(
            hash="hash1", entity_type="LINE", layer="0", data={}, location=(0, 0)
        )
        e2 = NormalizedEntity(
            hash="hash1", entity_type="LINE", layer="0", data={}, location=(0, 0)
        )
        assert hash(e1) == hash(e2)
        assert len({e1, e2}) == 1


class TestLineNormalizer:
    """LineNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return LineNormalizer(precision=2)

    @pytest.fixture
    def mock_line(self):
        line = Mock()
        line.dxf.start = Mock(x=0.0, y=0.0)
        line.dxf.end = Mock(x=100.0, y=100.0)
        line.dxf.layer = "0"
        return line

    def test_entity_type(self, normalizer):
        """엔티티 타입 확인"""
        assert normalizer.entity_type == "LINE"

    def test_normalize_basic(self, normalizer, mock_line):
        """기본 정규화 테스트"""
        result = normalizer.normalize(mock_line)
        assert result.entity_type == "LINE"
        assert result.layer == "0"
        assert result.data["start"] == (0.0, 0.0)
        assert result.data["end"] == (100.0, 100.0)
        assert result.location == (50.0, 50.0)

    def test_direction_invariant(self, normalizer):
        """방향 무관성 테스트 (A→B == B→A)"""
        line1 = Mock()
        line1.dxf.start = Mock(x=0.0, y=0.0)
        line1.dxf.end = Mock(x=100.0, y=100.0)
        line1.dxf.layer = "0"

        line2 = Mock()
        line2.dxf.start = Mock(x=100.0, y=100.0)
        line2.dxf.end = Mock(x=0.0, y=0.0)
        line2.dxf.layer = "0"

        result1 = normalizer.normalize(line1)
        result2 = normalizer.normalize(line2)
        assert result1.hash == result2.hash


class TestCircleNormalizer:
    """CircleNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return CircleNormalizer(precision=2)

    @pytest.fixture
    def mock_circle(self):
        circle = Mock()
        circle.dxf.center = Mock(x=50.0, y=50.0)
        circle.dxf.radius = 25.0
        circle.dxf.layer = "CIRCLES"
        return circle

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "CIRCLE"

    def test_normalize_basic(self, normalizer, mock_circle):
        result = normalizer.normalize(mock_circle)
        assert result.entity_type == "CIRCLE"
        assert result.data["center"] == (50.0, 50.0)
        assert result.data["radius"] == 25.0
        assert result.location == (50.0, 50.0)


class TestArcNormalizer:
    """ArcNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return ArcNormalizer(precision=2)

    @pytest.fixture
    def mock_arc(self):
        arc = Mock()
        arc.dxf.center = Mock(x=100.0, y=100.0)
        arc.dxf.radius = 50.0
        arc.dxf.start_angle = 0.0
        arc.dxf.end_angle = 90.0
        arc.dxf.layer = "ARCS"
        return arc

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "ARC"

    def test_normalize_basic(self, normalizer, mock_arc):
        result = normalizer.normalize(mock_arc)
        assert result.entity_type == "ARC"
        assert result.data["center"] == (100.0, 100.0)
        assert result.data["radius"] == 50.0
        assert result.data["start_angle"] == 0.0
        assert result.data["end_angle"] == 90.0


class TestPolylineNormalizer:
    """PolylineNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return PolylineNormalizer(precision=2)

    @pytest.fixture
    def mock_lwpolyline(self):
        poly = Mock()
        poly.get_points = Mock(
            return_value=[(0, 0), (100, 0), (100, 100), (0, 100)]
        )
        poly.is_closed = True
        poly.dxf.layer = "POLY"
        return poly

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "LWPOLYLINE"

    def test_normalize_lwpolyline(self, normalizer, mock_lwpolyline):
        result = normalizer.normalize(mock_lwpolyline)
        assert result.entity_type == "LWPOLYLINE"
        assert len(result.data["points"]) == 4
        assert result.data["closed"] is True

    def test_empty_polyline_raises(self, normalizer):
        poly = Mock()
        poly.get_points = Mock(return_value=[])
        poly.dxf.layer = "0"

        with pytest.raises(ValueError, match="정점이 없습니다"):
            normalizer.normalize(poly)


class TestTextNormalizer:
    """TextNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return TextNormalizer(precision=2)

    @pytest.fixture
    def mock_text(self):
        text = Mock()
        text.dxf.insert = Mock(x=10.0, y=20.0)
        text.dxf.text = "  Hello World  "
        text.dxf.layer = "TEXT"
        return text

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "TEXT"

    def test_normalize_strips_whitespace(self, normalizer, mock_text):
        result = normalizer.normalize(mock_text)
        assert result.data["content"] == "Hello World"

    def test_normalize_position_precision(self, normalizer, mock_text):
        result = normalizer.normalize(mock_text)
        # TEXT는 precision=1로 제한
        assert result.data["position"] == (10.0, 20.0)


class TestMTextNormalizer:
    """MTextNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return MTextNormalizer(precision=2)

    @pytest.fixture
    def mock_mtext(self):
        mtext = Mock()
        mtext.dxf.insert = Mock(x=30.0, y=40.0)
        mtext.plain_text = Mock(return_value="  Rich Text Content  ")
        mtext.dxf.layer = "MTEXT"
        return mtext

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "MTEXT"

    def test_normalize_uses_plain_text(self, normalizer, mock_mtext):
        result = normalizer.normalize(mock_mtext)
        assert result.data["content"] == "Rich Text Content"
        mock_mtext.plain_text.assert_called_once()


class TestDimensionNormalizer:
    """DimensionNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return DimensionNormalizer(precision=2)

    @pytest.fixture
    def mock_dimension(self):
        dim = Mock()
        dim.dxf.defpoint = Mock(x=100.0, y=200.0)
        dim.get_measurement = Mock(return_value=150.5)
        dim.dxf.text = ""
        dim.dxf.layer = "DIM"
        return dim

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "DIMENSION"

    def test_normalize_basic(self, normalizer, mock_dimension):
        result = normalizer.normalize(mock_dimension)
        assert result.entity_type == "DIMENSION"
        assert result.data["measurement"] == 150.5
        assert result.data["text_override"] == ""

    def test_normalize_measurement_failure(self, normalizer):
        dim = Mock()
        dim.dxf.defpoint = Mock(x=0, y=0)
        dim.get_measurement = Mock(side_effect=Exception("Error"))
        dim.dxf.text = ""
        dim.dxf.layer = "DIM"

        result = normalizer.normalize(dim)
        assert result.data["measurement"] == 0.0


class TestInsertNormalizer:
    """InsertNormalizer 테스트"""

    @pytest.fixture
    def normalizer(self):
        return InsertNormalizer(precision=2)

    @pytest.fixture
    def mock_insert(self):
        insert = Mock()
        insert.dxf.name = "BlockA"
        insert.dxf.insert = Mock(x=500.0, y=600.0)
        insert.dxf.xscale = 1.0
        insert.dxf.yscale = 1.0
        insert.dxf.rotation = 45.0
        insert.dxf.layer = "BLOCKS"
        return insert

    def test_entity_type(self, normalizer):
        assert normalizer.entity_type == "INSERT"

    def test_normalize_basic(self, normalizer, mock_insert):
        result = normalizer.normalize(mock_insert)
        assert result.entity_type == "INSERT"
        assert result.data["block_name"] == "BlockA"
        assert result.data["rotation"] == 45.0


class TestNormalizerFactory:
    """NormalizerFactory 테스트"""

    @pytest.fixture
    def factory(self):
        return NormalizerFactory(precision=2)

    def test_get_normalizer_line(self, factory):
        normalizer = factory.get_normalizer("LINE")
        assert isinstance(normalizer, LineNormalizer)
        assert normalizer.precision == 2

    def test_get_normalizer_circle(self, factory):
        normalizer = factory.get_normalizer("CIRCLE")
        assert isinstance(normalizer, CircleNormalizer)

    def test_get_normalizer_unknown_returns_none(self, factory):
        normalizer = factory.get_normalizer("UNKNOWN")
        assert normalizer is None

    def test_normalizer_caching(self, factory):
        """같은 타입은 같은 인스턴스 반환"""
        n1 = factory.get_normalizer("LINE")
        n2 = factory.get_normalizer("LINE")
        assert n1 is n2

    def test_supported_types(self):
        types = NormalizerFactory.supported_types()
        assert "LINE" in types
        assert "CIRCLE" in types
        assert "ARC" in types
        assert "LWPOLYLINE" in types
        assert "POLYLINE" in types
        assert "TEXT" in types
        assert "MTEXT" in types
        assert "DIMENSION" in types
        assert "INSERT" in types

    def test_normalize_convenience_method(self, factory):
        """normalize 편의 메서드 테스트"""
        mock_entity = Mock()
        mock_entity.dxftype = Mock(return_value="LINE")
        mock_entity.dxf.start = Mock(x=0, y=0)
        mock_entity.dxf.end = Mock(x=10, y=10)
        mock_entity.dxf.layer = "0"

        result = factory.normalize(mock_entity)
        assert result is not None
        assert result.entity_type == "LINE"

    def test_normalize_unknown_type(self, factory):
        mock_entity = Mock()
        mock_entity.dxftype = Mock(return_value="UNKNOWN")

        result = factory.normalize(mock_entity)
        assert result is None

    def test_clear_cache(self, factory):
        """캐시 초기화 테스트"""
        factory.get_normalizer("LINE")
        assert "LINE" in factory._cache

        factory.clear_cache()
        assert len(factory._cache) == 0


class TestGetDefaultFactory:
    """get_default_factory 테스트"""

    def test_returns_factory(self):
        factory = get_default_factory()
        assert isinstance(factory, NormalizerFactory)

    def test_default_precision(self):
        factory = get_default_factory()
        assert factory.precision == 2

    def test_custom_precision(self):
        factory = get_default_factory(precision=4)
        assert factory.precision == 4


class TestEntityNormalizerBase:
    """EntityNormalizer 베이스 클래스 테스트"""

    def test_is_abstract(self):
        """추상 클래스 인스턴스화 불가"""
        with pytest.raises(TypeError):
            EntityNormalizer()

    def test_round_point_with_vec3(self):
        """Vec3 객체 처리 테스트"""
        normalizer = LineNormalizer(precision=2)

        mock_point = Mock()
        mock_point.x = 1.234
        mock_point.y = 5.678

        result = normalizer._round_point(mock_point)
        assert result == (1.23, 5.68)

    def test_round_point_with_tuple(self):
        """튜플 처리 테스트"""
        normalizer = LineNormalizer(precision=2)
        result = normalizer._round_point((1.234, 5.678))
        assert result == (1.23, 5.68)

    def test_generate_hash(self):
        """해시 생성 테스트"""
        normalizer = LineNormalizer()
        hash1 = normalizer._generate_hash("test_key")
        hash2 = normalizer._generate_hash("test_key")
        hash3 = normalizer._generate_hash("different_key")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 32  # MD5 hex digest


class TestIntegration:
    """통합 테스트"""

    def test_factory_with_all_types(self):
        """모든 타입에 대해 normalizer 생성 가능"""
        factory = NormalizerFactory()

        for entity_type in NormalizerFactory.NORMALIZER_CLASSES:
            normalizer = factory.get_normalizer(entity_type)
            assert normalizer is not None
            assert isinstance(normalizer, EntityNormalizer)

    def test_precision_propagation(self):
        """precision 설정 전파 테스트"""
        factory = NormalizerFactory(precision=4)

        for entity_type in NormalizerFactory.NORMALIZER_CLASSES:
            normalizer = factory.get_normalizer(entity_type)
            assert normalizer.precision == 4
