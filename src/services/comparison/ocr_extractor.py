# -*- coding: utf-8 -*-
"""
OCR Text Extractor
==================

스캔된 PDF 및 이미지에서 텍스트를 추출하는 OCR 모듈.

지원 백엔드:
- Tesseract (기본, 로컬)
- EasyOCR (Fallback, GPU 지원)

Author: TEKLA_MCP Team
Date: 2025-12-14
Sprint: 3.1
"""

import logging
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# OCR 백엔드 감지
# -----------------------------------------------------------------------------
_TESSERACT_AVAILABLE = False
_PYTESSERACT = None
# Note: _EASYOCR / _EASYOCR_AVAILABLE / _PADDLEOCR_AVAILABLE are now
# lazy-resolved (see _probe_easyocr below + paddle_ocr_backend probe).
# Tesseract stays eager because it's a small wrapper (no model load).

try:
    import pytesseract

    _PYTESSERACT = pytesseract
    _TESSERACT_AVAILABLE = True
    logger.debug("Tesseract OCR available")
except ImportError:
    logger.debug("Tesseract not installed (pip install pytesseract)")

# EasyOCR — Phase G2.7-PERF: lazy-load. The bare ``import easyocr``
# pulls torch + scipy + numpy + a 200MB+ model loader and bumps RSS
# by ~400MB before a single OCR call ever runs. Defer until we
# actually need it. Lock allocated at module load — same pattern as
# paddle_ocr_backend (fully thread-safe double-checked locking).
_EASYOCR: Any = None
_EASYOCR_AVAILABLE: Optional[bool] = None  # tri-state
_EASYOCR_PROBE_LOCK: threading.Lock = threading.Lock()


def _probe_easyocr() -> bool:
    global _EASYOCR, _EASYOCR_AVAILABLE
    if _EASYOCR_AVAILABLE is not None:
        return _EASYOCR_AVAILABLE
    with _EASYOCR_PROBE_LOCK:
        if _EASYOCR_AVAILABLE is not None:
            return _EASYOCR_AVAILABLE
        try:
            import easyocr
            _EASYOCR = easyocr
            _EASYOCR_AVAILABLE = True
            logger.debug("EasyOCR lazy-imported")
        except ImportError:
            _EASYOCR_AVAILABLE = False
            logger.debug("EasyOCR not installed (pip install easyocr)")
    return _EASYOCR_AVAILABLE


# PaddleOCR 3.0 지원 (Sprint 1) — Phase G2.7-PERF: defer the
# is_paddleocr_available() probe (which actually imports paddleocr)
# until a backend instance is created. The startup check uses the
# cheap ``is_paddleocr_imported()`` so module load doesn't trigger
# the 500MB import.
try:
    from .paddle_ocr_backend import (
        PaddleOCRBackend,
        is_paddleocr_available,
        is_paddleocr_imported,
    )
    _PADDLEOCR_BACKEND_IMPORTABLE = True
    _PADDLEOCR_AVAILABLE = is_paddleocr_imported()  # cheap, no probe
except ImportError:
    _PADDLEOCR_BACKEND_IMPORTABLE = False
    _PADDLEOCR_AVAILABLE = False
    PaddleOCRBackend = None  # type: ignore[assignment]
    logger.debug("PaddleOCR backend not available")


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------
@dataclass
class OCRTextBlock:
    """OCR로 추출된 텍스트 블록

    Phase 3 P3-5: OCR Confidence 기반 검토 필요 라벨링
    - review_needed: 신뢰도가 임계값 미만일 경우 True
    - review_reason: 검토가 필요한 이유 설명
    """

    text: str
    confidence: float  # 0.0 ~ 1.0
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    page: int = 0

    # Phase 3 P3-5: 검토 필요 라벨링 필드
    review_needed: bool = False
    review_reason: Optional[str] = None

    def check_confidence(self, threshold: float = 0.7) -> bool:
        """신뢰도 확인 및 검토 필요 표시

        Args:
            threshold: 신뢰도 임계값 (0.0 ~ 1.0). 기본값 0.7 (70%)

        Returns:
            bool: 검토가 필요한 경우 True

        Examples:
            >>> block = OCRTextBlock(text="A1", confidence=0.6, bbox=(0,0,10,10))
            >>> block.check_confidence(0.7)
            True
            >>> block.review_needed
            True
            >>> block.review_reason
            'OCR 신뢰도 60% < 70%'
        """
        if self.confidence < threshold:
            self.review_needed = True
            self.review_reason = f"OCR 신뢰도 {self.confidence:.0%} < {threshold:.0%}"
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "text": self.text,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "page": self.page,
        }
        # Phase 3 P3-5: 검토 필요 필드 추가
        if self.review_needed:
            result["review_needed"] = self.review_needed
            result["review_reason"] = self.review_reason
        return result


@dataclass
class OCRResult:
    """OCR 처리 결과"""

    source_file: str
    total_pages: int = 1
    blocks: List[OCRTextBlock] = field(default_factory=list)
    full_text: str = ""
    backend: str = "unknown"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_file": self.source_file,
            "total_pages": self.total_pages,
            "blocks": [b.to_dict() for b in self.blocks],
            "full_text": self.full_text,
            "backend": self.backend,
            "warnings": self.warnings,
        }


# -----------------------------------------------------------------------------
# OCR Extractor
# -----------------------------------------------------------------------------
class OCRExtractor:
    """OCR 텍스트 추출기

    스캔된 PDF 또는 이미지에서 텍스트를 추출합니다.

    Examples:
        >>> extractor = OCRExtractor(lang="kor+eng")
        >>> result = extractor.extract("scanned_document.pdf")
        >>> print(result.full_text)
    """

    def __init__(
        self,
        lang: str = "kor+eng",
        backend: str = "auto",
        tesseract_config: str = "",
    ):
        """초기화

        Args:
            lang: OCR 언어 설정 (Tesseract: "kor+eng", EasyOCR: ["ko", "en"])
            backend: "tesseract", "easyocr", 또는 "auto"
            tesseract_config: Tesseract 추가 설정
        """
        self.lang = lang
        self.tesseract_config = tesseract_config
        self._backend = self._select_backend(backend)
        self._easyocr_reader = None  # Lazy loading

    def _select_backend(self, preference: str) -> str:
        """백엔드 선택 (PaddleOCR 우선).

        Phase G2.7-PERF — backend probes are now lazy. Calling
        ``is_paddleocr_available()`` triggers the actual import; we do
        that here (when the user is creating an OCR engine) instead of
        at module load. Tesseract stays cheap.
        """

        # Resolve availability lazily — these calls trigger the imports
        paddle_ok = (
            _PADDLEOCR_BACKEND_IMPORTABLE
            and is_paddleocr_available()
        )
        easy_ok = _probe_easyocr()
        if preference == "paddleocr" and paddle_ok:
            return "paddleocr"
        elif preference == "tesseract" and _TESSERACT_AVAILABLE:
            return "tesseract"
        elif preference == "easyocr" and easy_ok:
            return "easyocr"
        elif preference == "auto":
            # 자동 선택: PaddleOCR > Tesseract > EasyOCR
            if paddle_ok:
                return "paddleocr"
            elif _TESSERACT_AVAILABLE:
                return "tesseract"
            elif easy_ok:
                return "easyocr"

        # 모든 백엔드 없으면 에러
        raise RuntimeError(
            "OCR 백엔드를 찾을 수 없습니다.\n"
            "설치 방법:\n"
            "  - PaddleOCR: pip install paddleocr paddlepaddle\n"
            "  - Tesseract: pip install pytesseract + Tesseract 설치\n"
            "  - EasyOCR: pip install easyocr"
        )

    def extract(self, source: str) -> OCRResult:
        """파일에서 텍스트 추출

        Args:
            source: PDF 또는 이미지 파일 경로

        Returns:
            OCRResult: 추출 결과
        """
        path = Path(source)

        if not path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {source}")

        result = OCRResult(source_file=str(path), backend=self._backend)

        # 파일 타입에 따른 처리
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            self._extract_from_pdf(path, result)
        elif suffix in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
            self._extract_from_image(path, result)
        else:
            raise ValueError(f"지원하지 않는 파일 형식: {suffix}")

        # 전체 텍스트 생성
        result.full_text = "\n".join(block.text for block in result.blocks)

        return result

    def _extract_from_pdf(self, path: Path, result: OCRResult) -> None:
        """PDF에서 텍스트 추출"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            result.warnings.append("PyMuPDF 미설치 - PDF 처리 불가")
            return

        doc = fitz.open(path)
        result.total_pages = len(doc)

        for page_num, page in enumerate(doc):
            # PDF를 이미지로 렌더링
            pix = page.get_pixmap(dpi=200)
            img_data = pix.tobytes("png")

            # 이미지에서 OCR
            blocks = self._ocr_image_data(img_data, page_num)
            result.blocks.extend(blocks)

        doc.close()

    def _extract_from_image(self, path: Path, result: OCRResult) -> None:
        """이미지에서 텍스트 추출"""
        with open(path, "rb") as f:
            img_data = f.read()

        blocks = self._ocr_image_data(img_data, page=0)
        result.blocks.extend(blocks)

    def _ocr_image_data(self, img_data: bytes, page: int) -> List[OCRTextBlock]:
        """이미지 데이터에서 OCR 수행"""
        if self._backend == "paddleocr":
            return self._ocr_paddleocr(img_data, page)
        elif self._backend == "tesseract":
            return self._ocr_tesseract(img_data, page)
        elif self._backend == "easyocr":
            return self._ocr_easyocr(img_data, page)
        else:
            return []

    def _ocr_tesseract(self, img_data: bytes, page: int) -> List[OCRTextBlock]:
        """Tesseract OCR 수행"""
        from PIL import Image
        import io

        # 바이트를 이미지로 변환
        image = Image.open(io.BytesIO(img_data))

        # OCR 수행 (세부 정보 포함)
        data = _PYTESSERACT.image_to_data(
            image,
            lang=self.lang,
            config=self.tesseract_config,
            output_type=_PYTESSERACT.Output.DICT,
        )

        blocks = []
        n_boxes = len(data["text"])

        for i in range(n_boxes):
            text = data["text"][i].strip()
            conf = int(data["conf"][i])

            # 신뢰도가 낮거나 빈 텍스트는 건너뛰기
            if conf < 30 or not text:
                continue

            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]

            blocks.append(
                OCRTextBlock(
                    text=text,
                    confidence=conf / 100.0,
                    bbox=(x, y, x + w, y + h),
                    page=page,
                )
            )

        return blocks

    def _ocr_easyocr(self, img_data: bytes, page: int) -> List[OCRTextBlock]:
        """EasyOCR 수행"""
        import numpy as np
        from PIL import Image
        import io

        # 지연 로딩
        if self._easyocr_reader is None:
            # 언어 코드 변환 (kor+eng -> ["ko", "en"])
            langs = []
            if "kor" in self.lang:
                langs.append("ko")
            if "eng" in self.lang:
                langs.append("en")
            if not langs:
                langs = ["en"]

            # Phase G2.7-PERF — _EASYOCR is lazy; trigger probe.
            if not _probe_easyocr():
                raise RuntimeError(
                    "EasyOCR 백엔드를 사용할 수 없습니다. pip install easyocr"
                )
            self._easyocr_reader = _EASYOCR.Reader(langs, gpu=False)

        # 바이트를 numpy 배열로 변환
        image = Image.open(io.BytesIO(img_data))
        img_array = np.array(image)

        # OCR 수행
        results = self._easyocr_reader.readtext(img_array)

        blocks = []
        for bbox, text, conf in results:
            # bbox: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
            x1, y1 = bbox[0]
            x2, y2 = bbox[2]

            blocks.append(
                OCRTextBlock(
                    text=text,
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                    page=page,
                )
            )

        return blocks

    def _ocr_paddleocr(self, img_data: bytes, page: int) -> List[OCRTextBlock]:
        """PaddleOCR 3.0 수행 (Sprint 1)"""
        import numpy as np
        from PIL import Image
        import io

        # 지연 로딩
        if not hasattr(self, "_paddleocr_backend") or self._paddleocr_backend is None:
            self._paddleocr_backend = PaddleOCRBackend(lang=self.lang)

        # 바이트를 numpy 배열로 변환
        image = Image.open(io.BytesIO(img_data))
        img_array = np.array(image)

        # OCR 수행
        results = self._paddleocr_backend.extract_from_image(img_array, page=page)

        # dict -> OCRTextBlock 변환
        blocks = []
        for item in results:
            blocks.append(
                OCRTextBlock(
                    text=item["text"],
                    confidence=item["confidence"],
                    bbox=item["bbox"],
                    page=item["page"],
                )
            )

        return blocks


# -----------------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------------
def check_ocr_availability() -> Dict[str, bool]:
    """OCR 백엔드 가용성 확인.

    Phase G2.7-PERF — calling this triggers the lazy probes for
    PaddleOCR + EasyOCR (and pays their import cost). Use it from CLI
    diagnostic paths or before instantiating an engine; do NOT call
    from hot startup paths.
    """
    paddle_ok = (
        _PADDLEOCR_BACKEND_IMPORTABLE
        and is_paddleocr_available()
    )
    return {
        "paddleocr": paddle_ok,
        "tesseract": _TESSERACT_AVAILABLE,
        "easyocr": _probe_easyocr(),
    }


def extract_text_simple(file_path: str, lang: str = "kor+eng") -> str:
    """간단한 텍스트 추출 (편의 함수)

    Args:
        file_path: 파일 경로
        lang: 언어 설정

    Returns:
        추출된 전체 텍스트
    """
    extractor = OCRExtractor(lang=lang)
    result = extractor.extract(file_path)
    return result.full_text
