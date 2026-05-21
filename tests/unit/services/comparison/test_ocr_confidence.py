# -*- coding: utf-8 -*-
"""OCR Confidence 라벨링 테스트

Phase 3 P3-5: OCR Confidence 기반 "검토 필요" 라벨링

수용 기준:
- AC1: OCR 신뢰도 60%인 결과에 review_needed=True
- AC2: 리포트에 "검토 필요" 항목 별도 섹션
- AC3: threshold를 Config에서 조정 가능
"""

import pytest

from src.services.comparison.ocr_extractor import OCRTextBlock, OCRResult
from src.services.comparison.comparison_config import ComparisonConfig


class TestOCRTextBlockConfidence:
    """OCRTextBlock 신뢰도 체크 테스트"""

    def test_check_confidence_below_threshold(self):
        """AC1: 신뢰도가 임계값 미만이면 review_needed=True"""
        block = OCRTextBlock(
            text="A1",
            confidence=0.6,  # 60%
            bbox=(0, 0, 100, 50),
            page=0,
        )

        result = block.check_confidence(threshold=0.7)

        assert result is True
        assert block.review_needed is True
        assert block.review_reason is not None
        assert "60%" in block.review_reason
        assert "70%" in block.review_reason

    def test_check_confidence_above_threshold(self):
        """신뢰도가 임계값 이상이면 review_needed=False"""
        block = OCRTextBlock(
            text="B2",
            confidence=0.85,  # 85%
            bbox=(0, 0, 100, 50),
            page=0,
        )

        result = block.check_confidence(threshold=0.7)

        assert result is False
        assert block.review_needed is False
        assert block.review_reason is None

    def test_check_confidence_equal_threshold(self):
        """신뢰도가 정확히 임계값이면 review_needed=False"""
        block = OCRTextBlock(
            text="C3",
            confidence=0.7,  # 70% == threshold
            bbox=(0, 0, 100, 50),
            page=0,
        )

        result = block.check_confidence(threshold=0.7)

        assert result is False
        assert block.review_needed is False

    def test_check_confidence_custom_threshold(self):
        """사용자 정의 임계값 테스트"""
        block = OCRTextBlock(
            text="D4",
            confidence=0.55,
            bbox=(0, 0, 100, 50),
            page=0,
        )

        # 60% 임계값 - 55%는 아래
        result = block.check_confidence(threshold=0.6)
        assert result is True
        assert block.review_needed is True
        assert "55%" in block.review_reason
        assert "60%" in block.review_reason

    def test_check_confidence_very_low(self):
        """매우 낮은 신뢰도 테스트"""
        block = OCRTextBlock(
            text="???",
            confidence=0.1,  # 10%
            bbox=(0, 0, 100, 50),
            page=0,
        )

        result = block.check_confidence(threshold=0.7)

        assert result is True
        assert block.review_needed is True
        assert "10%" in block.review_reason

    def test_check_confidence_default_threshold(self):
        """기본 임계값 (0.7) 테스트"""
        block = OCRTextBlock(
            text="E5",
            confidence=0.65,
            bbox=(0, 0, 100, 50),
            page=0,
        )

        # 기본 임계값 사용
        result = block.check_confidence()

        assert result is True  # 0.65 < 0.7
        assert block.review_needed is True

    def test_to_dict_with_review_needed(self):
        """AC2: to_dict()에 검토 필요 정보 포함"""
        block = OCRTextBlock(
            text="F6",
            confidence=0.5,
            bbox=(10, 20, 110, 70),
            page=1,
        )
        block.check_confidence(threshold=0.7)

        result = block.to_dict()

        assert result["text"] == "F6"
        assert result["confidence"] == 0.5
        assert result["bbox"] == (10, 20, 110, 70)
        assert result["page"] == 1
        assert result["review_needed"] is True
        assert result["review_reason"] is not None
        assert "50%" in result["review_reason"]

    def test_to_dict_without_review_needed(self):
        """to_dict()에서 검토 불필요 시 필드 미포함"""
        block = OCRTextBlock(
            text="G7",
            confidence=0.9,
            bbox=(0, 0, 100, 50),
            page=0,
        )
        block.check_confidence(threshold=0.7)

        result = block.to_dict()

        assert result["text"] == "G7"
        assert result["confidence"] == 0.9
        assert "review_needed" not in result  # 포함되지 않음
        assert "review_reason" not in result

    def test_review_reason_format(self):
        """검토 사유 형식 검증"""
        block = OCRTextBlock(
            text="H8",
            confidence=0.45,
            bbox=(0, 0, 100, 50),
            page=0,
        )
        block.check_confidence(threshold=0.6)

        # 형식: "OCR 신뢰도 45% < 60%"
        assert block.review_reason == "OCR 신뢰도 45% < 60%"


class TestComparisonConfigOCRThreshold:
    """ComparisonConfig OCR 임계값 테스트"""

    def test_default_ocr_confidence_threshold(self):
        """AC3: 기본 OCR 신뢰도 임계값 확인"""
        config = ComparisonConfig()

        assert config.ocr_confidence_threshold == 0.7  # 70%

    def test_custom_ocr_confidence_threshold(self):
        """AC3: 사용자 정의 OCR 신뢰도 임계값"""
        config = ComparisonConfig(ocr_confidence_threshold=0.6)

        assert config.ocr_confidence_threshold == 0.6

    def test_ocr_threshold_to_dict(self):
        """to_dict()에 ocr_confidence_threshold 포함"""
        config = ComparisonConfig(ocr_confidence_threshold=0.8)

        data = config.to_dict()

        assert "ocr_confidence_threshold" in data
        assert data["ocr_confidence_threshold"] == 0.8

    def test_ocr_threshold_from_dict(self):
        """from_dict()에서 ocr_confidence_threshold 로드"""
        data = {
            "ocr_confidence_threshold": 0.5,
            "use_ocr": True,
        }

        config = ComparisonConfig.from_dict(data)

        assert config.ocr_confidence_threshold == 0.5
        assert config.use_ocr is True

    def test_ocr_threshold_from_dict_default(self):
        """from_dict()에서 누락 시 기본값 사용"""
        data = {}

        config = ComparisonConfig.from_dict(data)

        assert config.ocr_confidence_threshold == 0.7  # 기본값

    def test_strict_config_ocr_threshold(self):
        """엄격한 설정에서 OCR 임계값 확인"""
        config = ComparisonConfig.get_strict()

        # 엄격한 설정에서도 OCR 임계값은 기본값 사용
        assert config.ocr_confidence_threshold == 0.7

    def test_relaxed_config_ocr_threshold(self):
        """완화된 설정에서 OCR 임계값 확인"""
        config = ComparisonConfig.get_relaxed()

        # 완화된 설정에서도 OCR 임계값은 기본값 사용
        assert config.ocr_confidence_threshold == 0.7


class TestP3_5_AcceptanceCriteria:
    """P3-5 수용 기준 통합 테스트"""

    def test_ac1_confidence_60_percent_review_needed(self):
        """AC1: OCR 신뢰도 60%인 결과에 review_needed=True"""
        block = OCRTextBlock(
            text="테스트",
            confidence=0.6,  # 정확히 60%
            bbox=(0, 0, 100, 50),
            page=0,
        )

        # 기본 임계값 70% 사용
        block.check_confidence(threshold=0.7)

        assert block.review_needed is True
        assert "60%" in block.review_reason

    def test_ac2_report_review_section(self):
        """AC2: 리포트에 검토 필요 항목 별도 섹션"""
        blocks = [
            OCRTextBlock(text="A1", confidence=0.9, bbox=(0, 0, 100, 50), page=0),
            OCRTextBlock(text="B2", confidence=0.5, bbox=(100, 0, 200, 50), page=0),
            OCRTextBlock(text="C3", confidence=0.3, bbox=(200, 0, 300, 50), page=0),
            OCRTextBlock(text="D4", confidence=0.8, bbox=(300, 0, 400, 50), page=0),
        ]

        # 신뢰도 체크 실행
        for block in blocks:
            block.check_confidence(threshold=0.7)

        # 검토 필요 항목 필터링
        review_needed_blocks = [b for b in blocks if b.review_needed]

        # 리포트용 데이터 생성
        review_section = [b.to_dict() for b in review_needed_blocks]

        assert len(review_section) == 2  # B2, C3
        assert all("review_needed" in item for item in review_section)
        assert all(item["review_needed"] is True for item in review_section)

    def test_ac3_threshold_configurable(self):
        """AC3: threshold를 Config에서 조정 가능"""
        # 설정에서 임계값 조정
        config = ComparisonConfig(ocr_confidence_threshold=0.5)

        block = OCRTextBlock(
            text="X",
            confidence=0.55,  # 55%
            bbox=(0, 0, 100, 50),
            page=0,
        )

        # Config에서 가져온 임계값 사용
        block.check_confidence(threshold=config.ocr_confidence_threshold)

        # 55% >= 50% 이므로 검토 불필요
        assert block.review_needed is False

    def test_multiple_blocks_mixed_confidence(self):
        """혼합 신뢰도 블록 처리"""
        config = ComparisonConfig(ocr_confidence_threshold=0.7)

        blocks = [
            OCRTextBlock(text="높음", confidence=0.95, bbox=(0, 0, 100, 50), page=0),
            OCRTextBlock(text="중간", confidence=0.75, bbox=(0, 0, 100, 50), page=0),
            OCRTextBlock(text="경계", confidence=0.70, bbox=(0, 0, 100, 50), page=0),
            OCRTextBlock(text="낮음", confidence=0.65, bbox=(0, 0, 100, 50), page=0),
            OCRTextBlock(text="매우낮음", confidence=0.30, bbox=(0, 0, 100, 50), page=0),
        ]

        for block in blocks:
            block.check_confidence(threshold=config.ocr_confidence_threshold)

        review_needed = [b for b in blocks if b.review_needed]

        assert len(review_needed) == 2  # 낮음, 매우낮음
        assert review_needed[0].text == "낮음"
        assert review_needed[1].text == "매우낮음"

    def test_ocr_result_with_blocks(self):
        """OCRResult 내 블록 신뢰도 체크"""
        result = OCRResult(
            source_file="test.pdf",
            total_pages=1,
            backend="tesseract",
        )

        # 블록 추가
        result.blocks = [
            OCRTextBlock(text="A", confidence=0.8, bbox=(0, 0, 50, 50), page=0),
            OCRTextBlock(text="B", confidence=0.4, bbox=(50, 0, 100, 50), page=0),
        ]

        # 모든 블록 신뢰도 체크
        review_count = 0
        for block in result.blocks:
            if block.check_confidence(threshold=0.7):
                review_count += 1

        assert review_count == 1  # B만 검토 필요

        # 경고 추가 (drawing_differ.py와 동일 패턴)
        if review_count > 0:
            result.warnings.append(f"검토 필요 {review_count}개 (신뢰도 < 70%)")

        assert len(result.warnings) == 1
        assert "검토 필요 1개" in result.warnings[0]
