# -*- coding: utf-8 -*-
"""
PP-StructureV3 레이아웃 분석기 (Sprint 3)
==========================================

도면/문서의 레이아웃을 분석하여 표, 텍스트, 이미지 영역을 자동 분류합니다.
PaddleOCR의 PP-StructureV3를 사용하여 PDF/이미지를 구조화된 데이터로 변환.

보안 강화 (2025-12-18):
- Path Traversal 방어 추가
- 파일 크기/페이지 제한 적용
- DPI 제한으로 OOM 공격 방지

Author: TEKLA_MCP Team
Date: 2025-12-16
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
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

# PaddlePaddle 동적 임포트 — Phase G2.7-PERF: lazy-load (defer ~500MB
# import-time RSS until layout analysis is actually requested). Same
# rationale as paddle_ocr_backend.py — lock allocated at module load
# for fully thread-safe double-checked locking.
_PPSTRUCTURE_AVAILABLE: Optional[bool] = None  # tri-state
_PPStructure: Any = None
_PPSTRUCTURE_PROBE_LOCK: threading.Lock = threading.Lock()


def _probe_ppstructure() -> bool:
    """Lazy probe — imports paddleocr.PPStructure exactly once.

    Thread-safe via double-checked locking around a module-load lock.
    """

    global _PPSTRUCTURE_AVAILABLE, _PPStructure
    if _PPSTRUCTURE_AVAILABLE is not None:
        return _PPSTRUCTURE_AVAILABLE
    with _PPSTRUCTURE_PROBE_LOCK:
        if _PPSTRUCTURE_AVAILABLE is not None:
            return _PPSTRUCTURE_AVAILABLE
        try:
            from paddleocr import PPStructure as _PPStructureClass
            _PPStructure = _PPStructureClass
            _PPSTRUCTURE_AVAILABLE = True
            logger.debug("PP-StructureV3 lazy-imported")
        except ImportError:
            _PPSTRUCTURE_AVAILABLE = False
            logger.debug(
                "PPStructure not installed (pip install paddleocr paddlepaddle)"
            )
    return _PPSTRUCTURE_AVAILABLE


def is_ppstructure_available() -> bool:
    """PP-StructureV3 가용성 확인 — triggers lazy import on first call."""
    return _probe_ppstructure()


def is_ppstructure_imported() -> bool:
    """Cheap check (no import) for callers that just need status."""
    return _PPSTRUCTURE_AVAILABLE is True


class LayoutAnalyzer:
    """PP-StructureV3 레이아웃 분석기

    도면 및 문서의 레이아웃을 자동 분석하여 영역별 정보를 추출합니다.

    Examples:
        >>> analyzer = LayoutAnalyzer(lang="korean")
        >>> layout = analyzer.analyze("drawing.pdf")
        >>> print(f"표: {len(layout['tables'])}개")
        >>> print(f"텍스트: {len(layout['text_blocks'])}개")
    """

    # 지원되는 영역 유형
    REGION_TYPES = ["table", "text", "figure", "title", "header", "footer"]

    def __init__(
        self,
        lang: str = "korean",
        recovery: bool = True,
        use_gpu: bool = False,
    ):
        """초기화

        Args:
            lang: 언어 설정
            recovery: 표 구조 복원 활성화 (HTML 출력)
            use_gpu: GPU 사용 여부
        """
        # Phase G2.7-PERF — trigger lazy probe before reading globals
        if not _probe_ppstructure():
            raise RuntimeError(
                "PP-StructureV3가 설치되지 않았습니다.\n" "설치: pip install paddleocr paddlepaddle"
            )

        self._engine = _PPStructure(
            recovery=recovery,
            lang=lang,
            use_gpu=use_gpu,
            show_log=False,
        )

        self._lang = lang
        self._recovery = recovery
        logger.info(f"LayoutAnalyzer initialized: lang={lang}, recovery={recovery}")

    def analyze(
        self,
        source: Any,
        page: int = 0,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """이미지/PDF에서 레이아웃 분석

        Args:
            source: 이미지 경로(str/Path), numpy 배열, 또는 PDF 경로
            page: PDF인 경우 분석할 페이지 번호

        Returns:
            레이아웃 정보: {
                "tables": [{"bbox", "html", ...}],
                "text_blocks": [{"bbox", "content", ...}],
                "figures": [{"bbox", ...}],
                "titles": [{"bbox", "text", ...}],
            }

        Raises:
            PathValidationError: 잘못된 파일 경로
            FileSizeError: 파일 크기 초과
        """
        # PDF 및 이미지 처리 + 보안 검증
        if isinstance(source, (str, Path)):
            # [보안] Path Traversal 방어 + 파일 크기 검증
            try:
                validated_path = validate_path(source, must_exist=True)
                validate_file_size(validated_path)
            except (PathValidationError, FileSizeError) as e:
                logger.error(f"파일 검증 실패: {e}")
                raise

            if validated_path.suffix.lower() == ".pdf":
                # [보안] PDF 페이지 수 검증
                validate_pdf_pages(validated_path)
                img_array = self._load_pdf_page(validated_path, page)
            else:
                img_array = str(validated_path)
        elif isinstance(source, np.ndarray):
            img_array = source
        else:
            raise ValueError(f"지원하지 않는 입력 타입: {type(source)}")

        # 레이아웃 분석 수행
        try:
            result = self._engine(img_array)
        except Exception as e:
            logger.error(f"레이아웃 분석 실패: {e}")
            return self._empty_layout()

        # 결과 파싱
        return self._parse_result(result)

    def _load_pdf_page(self, pdf_path: Path, page: int) -> np.ndarray:
        """PDF 페이지를 이미지로 변환"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PDF 처리를 위해 PyMuPDF를 설치하세요: pip install PyMuPDF")

        doc = fitz.open(str(pdf_path))
        if page >= len(doc):
            raise ValueError(f"페이지 {page}가 없습니다 (총 {len(doc)} 페이지)")

        pix = doc[page].get_pixmap(dpi=200)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

        # RGB로 변환
        if pix.n == 4:
            img = img[:, :, :3]

        doc.close()
        return img

    def _parse_result(
        self,
        result: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """PP-Structure 결과 파싱"""
        layout = self._empty_layout()

        if not result:
            return layout

        for item in result:
            region_type = item.get("type", "").lower()
            bbox = item.get("bbox", [])
            res = item.get("res", {})

            # 영역 타입별 분류
            if "table" in region_type:
                table_info = {
                    "bbox": bbox,
                    "html": res.get("html", "") if isinstance(res, dict) else "",
                }
                layout["tables"].append(table_info)

            elif "text" in region_type:
                text_info = {
                    "bbox": bbox,
                    "content": self._extract_text_content(res),
                }
                layout["text_blocks"].append(text_info)

            elif "figure" in region_type or "image" in region_type:
                layout["figures"].append({"bbox": bbox})

            elif "title" in region_type:
                layout["titles"].append(
                    {
                        "bbox": bbox,
                        "text": self._extract_text_content(res),
                    }
                )

        logger.info(
            f"레이아웃 분석 완료: "
            f"표 {len(layout['tables'])}개, "
            f"텍스트 {len(layout['text_blocks'])}개, "
            f"이미지 {len(layout['figures'])}개"
        )

        return layout

    def _extract_text_content(self, res: Any) -> str:
        """텍스트 내용 추출"""
        if isinstance(res, str):
            return res
        elif isinstance(res, list):
            # [(text, confidence), ...] 형식
            texts = []
            for item in res:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
                elif isinstance(item, (list, tuple)) and len(item) >= 1:
                    texts.append(str(item[0]))
            return " ".join(texts)
        elif isinstance(res, dict):
            return res.get("text", str(res))
        return str(res)

    @staticmethod
    def _empty_layout() -> Dict[str, List[Dict[str, Any]]]:
        """빈 레이아웃 구조 반환"""
        return {
            "tables": [],
            "text_blocks": [],
            "figures": [],
            "titles": [],
        }

    def to_markdown(
        self,
        layout: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        """레이아웃을 Markdown 문자열로 변환

        Args:
            layout: analyze() 메서드의 결과

        Returns:
            Markdown 형식의 문자열
        """
        md_parts = []

        # 제목
        for title in layout.get("titles", []):
            md_parts.append(f"# {title.get('text', '')}\n")

        # 텍스트 블록
        for text_block in layout.get("text_blocks", []):
            md_parts.append(text_block.get("content", "") + "\n")

        # 표 (HTML)
        for table in layout.get("tables", []):
            html = table.get("html", "")
            if html:
                md_parts.append(f"\n{html}\n")

        # 이미지 플레이스홀더
        for i, _ in enumerate(layout.get("figures", [])):
            md_parts.append(f"\n[Figure {i + 1}]\n")

        return "\n".join(md_parts)
