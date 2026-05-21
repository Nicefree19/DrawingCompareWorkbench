# -*- coding: utf-8 -*-
"""
PaddleOCR 3.0 백엔드 (Sprint 1)
================================

PaddleOCR PP-OCRv5를 사용한 고정밀 OCR 백엔드.
한글 포함 다국어 지원, GPU 가속 옵션.

보안 강화 (2025-12-18):
- Path Traversal 방어 추가
- 파일 크기 제한 적용
- DPI 제한으로 OOM 공격 방지

Author: TEKLA_MCP Team
Date: 2025-12-16
"""

import logging
import threading
from pathlib import Path
from typing import List, Optional, Any
import numpy as np

from src.utils.security_validators import (
    validate_path,
    validate_file_size,
    validate_pdf_pages,
    validate_dpi,
    PathValidationError,
    FileSizeError,
    SecurityConfig,
)

logger = logging.getLogger(__name__)

# PaddleOCR 동적 임포트 — Phase G2.7-PERF: lazy-load to defer the
# ~500MB import-time RSS hit until OCR is actually needed. Drawing
# Compare workbench imports this module at startup but only invokes
# OCR when a PDF page lacks an extractable text layer (rare for
# vector PDFs, common only for scanned drawings).
_PADDLEOCR_AVAILABLE: Optional[bool] = None  # tri-state: None=unprobed, True/False=resolved
_PaddleOCR: Any = None
# Lock initialised at module load (allocated once per process) so the
# probe is fully thread-safe. Allocating at module load adds ~100 bytes
# to the import — negligible vs the 500MB we save by deferring paddleocr.
_PADDLEOCR_PROBE_LOCK: threading.Lock = threading.Lock()


def _probe_paddleocr() -> bool:
    """Best-effort lazy probe — imports paddleocr exactly once and caches
    the result. Returns True when import succeeds, False otherwise.

    The probe IS the import (paddleocr's import IS the heavy step), so
    callers should only invoke this when they actually intend to OCR.
    Use ``is_paddleocr_imported()`` for cheap status checks that should
    NOT trigger the import.

    Thread safety: double-checked locking around module-load lock.
    Multiple concurrent first-callers all wait on the same lock; only
    one performs the import.
    """

    global _PADDLEOCR_AVAILABLE, _PaddleOCR
    # Fast path — already resolved (no lock acquisition needed because
    # _PADDLEOCR_AVAILABLE is set as the LAST step inside the lock and
    # Python's GIL guarantees atomic visibility of None vs bool).
    if _PADDLEOCR_AVAILABLE is not None:
        return _PADDLEOCR_AVAILABLE
    with _PADDLEOCR_PROBE_LOCK:
        # Re-check inside lock (another thread may have just resolved it)
        if _PADDLEOCR_AVAILABLE is not None:
            return _PADDLEOCR_AVAILABLE
        try:
            from paddleocr import PaddleOCR as _PaddleOCRClass
            _PaddleOCR = _PaddleOCRClass
            _PADDLEOCR_AVAILABLE = True
            logger.debug("PaddleOCR 3.0 lazy-imported (RSS bumped ~500MB)")
        except ImportError:
            _PADDLEOCR_AVAILABLE = False
            logger.debug(
                "PaddleOCR not installed (pip install paddleocr paddlepaddle)"
            )
    return _PADDLEOCR_AVAILABLE


def is_paddleocr_available() -> bool:
    """PaddleOCR 가용성 확인.

    Phase G2.7-PERF: previously this returned a flag set at module
    import time, which forced the heavy paddleocr import on every
    process start (workbench RSS jumped from ~50MB to ~580MB before
    a single OCR call ever ran). Now it triggers the lazy probe — if
    the caller is asking, they're presumably about to OCR, so the
    import cost is justified. The probe is cached, so subsequent
    calls return instantly.
    """
    return _probe_paddleocr()


def is_paddleocr_imported() -> bool:
    """Cheap check that does NOT trigger the import — only returns True
    when paddleocr has already been loaded by an earlier probe.

    Use this in startup paths (e.g. workbench bootstrap) that just want
    to log "OCR backend available" without paying the import cost.
    """
    return _PADDLEOCR_AVAILABLE is True


class PaddleOCRBackend:
    """PaddleOCR 3.0 백엔드

    PP-OCRv5를 사용한 고정밀 OCR 처리.

    Examples:
        >>> backend = PaddleOCRBackend(lang="korean")
        >>> blocks = backend.extract_from_image("drawing.png")
        >>> for block in blocks:
        ...     print(block["text"], block["confidence"])
    """

    # 언어 코드 매핑 (Tesseract 스타일 -> PaddleOCR 스타일)
    LANG_MAP = {
        "kor": "korean",
        "eng": "en",
        "kor+eng": "korean",  # PaddleOCR은 한국어 모델이 영어도 인식
        "jpn": "japan",
        "chi_sim": "ch",
    }

    def __init__(
        self,
        lang: str = "korean",
        use_gpu: bool = False,
        use_angle_cls: bool = True,
    ):
        """초기화

        Args:
            lang: 언어 설정 ("korean", "en", "ch", ...)
            use_gpu: GPU 사용 여부
            use_angle_cls: 텍스트 방향 분류 사용 여부
        """
        # Phase G2.7-PERF — trigger lazy probe before reading globals
        if not _probe_paddleocr():
            raise RuntimeError(
                "PaddleOCR이 설치되지 않았습니다.\n" "설치: pip install paddleocr paddlepaddle"
            )

        # 언어 코드 변환
        paddle_lang = self.LANG_MAP.get(lang, lang)

        self._ocr = _PaddleOCR(
            use_angle_cls=use_angle_cls,
            lang=paddle_lang,
            use_gpu=use_gpu,
            show_log=False,
        )

        self._lang = paddle_lang
        logger.info(f"PaddleOCR initialized: lang={paddle_lang}, gpu={use_gpu}")

    def extract_from_image(
        self,
        image_source: Any,
        page: int = 0,
    ) -> List[dict]:
        """이미지에서 텍스트 추출

        Args:
            image_source: 이미지 경로(str/Path) 또는 numpy 배열
            page: 페이지 번호 (멀티페이지 문서용)

        Returns:
            텍스트 블록 리스트 [{"text", "confidence", "bbox", "page"}, ...]

        Raises:
            PathValidationError: 잘못된 파일 경로
            FileSizeError: 파일 크기 초과
        """
        # 입력 타입 처리 + 보안 검증
        if isinstance(image_source, (str, Path)):
            # [보안] Path Traversal 방어 + 파일 크기 검증
            try:
                validated_path = validate_path(image_source, must_exist=True)
                validate_file_size(validated_path)
                img_input = str(validated_path)
            except (PathValidationError, FileSizeError) as e:
                logger.error(f"이미지 파일 검증 실패: {e}")
                raise
        elif isinstance(image_source, np.ndarray):
            img_input = image_source
        else:
            raise ValueError(f"지원하지 않는 입력 타입: {type(image_source)}")

        # OCR 수행
        try:
            result = self._ocr.ocr(img_input, cls=True)
        except Exception as e:
            logger.error(f"PaddleOCR 처리 실패: {e}")
            return []

        # 결과가 없는 경우
        if result is None or len(result) == 0 or result[0] is None:
            return []

        # 결과 파싱
        blocks = []
        for line in result[0]:
            if line is None or len(line) < 2:
                continue

            bbox_points, (text, confidence) = line

            # bbox: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]] -> (x1, y1, x2, y2)
            x1 = min(p[0] for p in bbox_points)
            y1 = min(p[1] for p in bbox_points)
            x2 = max(p[0] for p in bbox_points)
            y2 = max(p[1] for p in bbox_points)

            blocks.append(
                {
                    "text": text,
                    "confidence": float(confidence),
                    "bbox": (x1, y1, x2, y2),
                    "page": page,
                }
            )

        return blocks

    def extract_from_pdf(
        self,
        pdf_path: str,
        dpi: int = 200,
        pages: Optional[List[int]] = None,
    ) -> List[dict]:
        """PDF에서 텍스트 추출

        Args:
            pdf_path: PDF 파일 경로
            dpi: 렌더링 해상도 (최대 300 DPI 제한)
            pages: 처리할 페이지 목록 (None이면 전체)

        Returns:
            텍스트 블록 리스트

        Raises:
            PathValidationError: 잘못된 파일 경로
            FileSizeError: 파일 크기/페이지 수 초과
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("PyMuPDF 미설치 - PDF 처리 불가")
            return []

        # [보안] Path Traversal 방어 + 파일 크기 검증
        try:
            validated_path = validate_path(pdf_path, must_exist=True)
            validate_file_size(validated_path)
            validate_pdf_pages(validated_path)
        except (PathValidationError, FileSizeError) as e:
            logger.error(f"PDF 파일 검증 실패: {e}")
            raise

        # [보안] DPI 제한 (OOM 공격 방지)
        dpi = validate_dpi(dpi)

        doc = fitz.open(str(validated_path))
        all_blocks = []

        page_range = pages if pages else range(len(doc))

        for page_num in page_range:
            if page_num >= len(doc):
                continue

            page = doc[page_num]

            # PDF 페이지를 이미지로 렌더링
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)

            # numpy 배열로 변환
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )

            # RGB로 변환 (RGBA인 경우)
            if pix.n == 4:
                img_array = img_array[:, :, :3]

            # OCR 수행
            blocks = self.extract_from_image(img_array, page=page_num)
            all_blocks.extend(blocks)

        doc.close()
        return all_blocks
