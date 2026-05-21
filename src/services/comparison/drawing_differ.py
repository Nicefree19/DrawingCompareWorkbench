# -*- coding: utf-8 -*-
"""
도면 비교 엔진 (Drawing Differ)
================================

PDF/이미지 도면의 시각적 차이를 감지하고 텍스트 변경을 추적합니다.

주요 기능:
- 자동 정렬 (Auto-Alignment) via OpenCV Feature Matching
- SSIM 기반 시각적 차이 감지
- PDF 텍스트 추출 및 변경 추적

Author: TEKLA_MCP Team
Date: 2025-12-14
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import fitz  # PyMuPDF

    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False

from .base import (
    BaseComparator,
    ChangeRecord,
    ChangeType,
    ComparisonResult,
)

# OCR Fallback — Phase G2.7-PERF: defer the actual probe. We only
# care about OCR availability when text extraction returns empty
# (rare for vector PDFs, common only for scanned drawings). Calling
# check_ocr_availability() at module load forces the heavy paddle/
# easyocr imports for the 99% of runs that never hit the OCR path.
# Now: assume OCR is available IF the module imports cleanly, and
# let the runtime path probe lazily on first need.
try:
    from .ocr_extractor import OCRExtractor

    # Optimistic flag — actual availability is re-checked at runtime
    # via _check_ocr_lazily() below when the OCR fallback path fires.
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    OCRExtractor = None


def _check_ocr_lazily() -> bool:
    """Phase G2.7-PERF — runtime probe used by the OCR fallback path."""

    if not OCR_AVAILABLE or OCRExtractor is None:
        return False
    try:
        from .ocr_extractor import check_ocr_availability
        return any(check_ocr_availability().values())
    except Exception:
        return False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase O5 — PDF visual diff 노이즈 필터 프리셋
# ---------------------------------------------------------------------------
#
# 각 프리셋은 4개 파라미터로 구성:
#   sigma_k         — _measure_noise_floor 의 mean + k·σ (노이즈 임계값)
#   morph_kernel    — 1차 OPEN/CLOSE 의 정사각 커널 변(px)
#   second_morph    — anti-aliasing 보정용 2차 OPEN 사용 여부
#   blob_min_area   — connected component 최소 면적 (px²) — 미만은 노이즈
#
# 사용자는 GUI 노이즈 필터 dialog 또는 config 로 strength 선택. 줄어든
# false positive 와 증가한 false negative (over-erosion) 의 균형 조정.
NOISE_PROFILES: Dict[str, Dict[str, Any]] = {
    "low": {  # 가장 보수적 — 변경 누락 최소화 (false negative 회피 우선)
        "sigma_k": 2.5,
        "morph_kernel": 3,
        "second_morph": False,
        "blob_min_area": 10,
    },
    "medium": {  # 기본값 — 균형
        "sigma_k": 3.0,
        "morph_kernel": 5,
        "second_morph": True,
        "blob_min_area": 25,
    },
    "high": {  # 가장 적극적 — false positive 최소화 (사용자 검토 부담 ↓)
        "sigma_k": 3.5,
        "morph_kernel": 7,
        "second_morph": True,
        "blob_min_area": 50,
    },
}


def _resolve_noise_profile(strength: Optional[str]) -> Dict[str, Any]:
    """문자열 strength → NOISE_PROFILES 항목. 잘못된 값은 medium fallback."""
    key = (strength or "medium").lower()
    return NOISE_PROFILES.get(key, NOISE_PROFILES["medium"])


class DrawingDiffer(BaseComparator):
    """도면 비교 엔진

    PDF 또는 이미지 도면을 비교하여 시각적 차이 및 텍스트 변경을 감지합니다.

    Examples:
        >>> differ = DrawingDiffer(config={
        ...     "alignment_enabled": True,
        ...     "ssim_threshold": 0.95,
        ...     "text_extraction": True,
        ... })
        >>> result = differ.compare("old_drawing.pdf", "new_drawing.pdf")
        >>> print(f"변경 영역: {result.total_changes}건")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        # 의존성 확인
        if not CV2_AVAILABLE:
            logger.warning("OpenCV(cv2)가 설치되지 않았습니다. 일부 기능이 제한됩니다.")
        if not FITZ_AVAILABLE:
            logger.warning("PyMuPDF(fitz)가 설치되지 않았습니다. PDF 처리가 제한됩니다.")

        # 설정
        self._alignment_enabled: bool = self.get_config("alignment_enabled", True)
        self._ssim_threshold: float = self.get_config("ssim_threshold", 0.95)
        self._text_extraction: bool = self.get_config("text_extraction", True)
        self._dpi: int = self.get_config("dpi", 150)
        self._ocr_fallback: bool = self.get_config("ocr_fallback", True)  # Sprint 3.1
        self._ocr_lang: str = self.get_config("ocr_lang", "kor+eng")
        # Phase 3 P3-5: OCR 신뢰도 임계값
        self._ocr_confidence_threshold: float = self.get_config("ocr_confidence_threshold", 0.7)

        # OCR Extractor (Lazy)
        self._ocr_extractor = None

        # 결과 이미지 저장용
        self._diff_image: Optional[np.ndarray] = None
        self._overlay_image: Optional[np.ndarray] = None

        # Sprint 7: 원본 이미지 저장 (뷰어 고해상도 표시용)
        self._original_img_a: Optional[np.ndarray] = None
        self._original_img_b: Optional[np.ndarray] = None

        # Phase 4: 정렬/리사이즈된 이미지 저장 (좌표 정합용)
        self._aligned_img_a: Optional[np.ndarray] = None
        self._aligned_img_b: Optional[np.ndarray] = None

        # Sprint 8 Phase 2: Vector Diff 설정
        self._vector_diff_enabled: bool = self.get_config("vector_diff", True)

        # Phase O5 — 노이즈 필터 강도 프리셋 (low/medium/high)
        self._noise_filter_strength: str = self.get_config("noise_filter_strength", "medium")
        self._noise_profile: Dict[str, Any] = _resolve_noise_profile(self._noise_filter_strength)
        # RANSAC inlier ratio 이 이 값 미만이면 정렬 신뢰도 낮음 — warning
        self._alignment_min_inlier_ratio: float = self.get_config(
            "alignment_min_inlier_ratio", 0.3
        )
        # Phase R1 (RV-20260510-003) — inlier_ratio < threshold 시 정렬을
        # 적용하지 않고 원본 target 반환 (skip warp). 기본 True 인 안전한
        # 동작 — 잘못된 homography 가 SSIM/diff 계산을 망가뜨려 silent
        # drop 을 만드는 cascade 회피. False 로 끄면 Phase O5 동작 유지
        # (warning 만 + warp 적용).
        self._alignment_skip_warp_below_inlier: bool = bool(self.get_config(
            "alignment_skip_warp_below_inlier", True
        ))
        # SSIM ≥ 이 값일 때만 anti-aliasing 보정 (2차 OPEN) 적용
        self._anti_alias_ssim_gate: float = self.get_config("anti_alias_ssim_gate", 0.98)

    def compare(
        self,
        source_a: Union[str, Path],
        source_b: Union[str, Path],
        page: int = 0,
        *,
        page_a: Optional[int] = None,
        page_b: Optional[int] = None,
    ) -> ComparisonResult:
        """두 도면을 비교합니다.

        Args:
            source_a: 비교 대상 A (기준, Old)
            source_b: 비교 대상 B (신규, New)
            page: 두 PDF 모두에서 사용할 페이지 번호 (back-compat shortcut).
                ``page_a`` 또는 ``page_b``가 명시되면 그쪽이 우선합니다.
            page_a: A 측 PDF의 페이지 번호 (Phase H2 — 페이지 자동 매칭으로
                A.page_2 ↔ B.page_5 같은 cross-page 비교 지원).
            page_b: B 측 PDF의 페이지 번호.

        Returns:
            ComparisonResult
        """
        source_a = Path(source_a)
        source_b = Path(source_b)

        # Phase H2 — resolve effective page indices. ``page`` remains the
        # shortcut for "both pages identical"; explicit ``page_a``/
        # ``page_b`` override (used by ``compare_pdf_documents`` when the
        # page matcher recovers a reordered pair).
        effective_page_a = page if page_a is None else page_a
        effective_page_b = page if page_b is None else page_b

        if effective_page_a == effective_page_b:
            logger.info(f"도면 비교 시작: {source_a.name} vs {source_b.name} (page={effective_page_a})")
        else:
            logger.info(
                f"도면 비교 시작 (cross-page): {source_a.name}#{effective_page_a} "
                f"vs {source_b.name}#{effective_page_b}"
            )

        self._result = ComparisonResult(
            source_a=str(source_a),
            source_b=str(source_b),
        )

        try:
            # 1. 이미지 로드 (PDF -> Image 변환 포함)
            img_a = self._load_image(source_a, effective_page_a)
            img_b = self._load_image(source_b, effective_page_b)

            if img_a is None or img_b is None:
                raise ValueError("이미지 로드 실패")

            # Sprint 7: 원본 이미지 저장 (뷰어 고해상도 표시용)
            self._original_img_a = img_a.copy()
            self._original_img_b = img_b.copy()

            # 2. 자동 정렬 (선택)
            if self._alignment_enabled and CV2_AVAILABLE:
                img_b_aligned = self._align_images(img_a, img_b)
            else:
                img_b_aligned = img_b

            # 3. 시각적 차이 계산
            if CV2_AVAILABLE:
                diff_regions = self._compute_visual_diff(img_a, img_b_aligned)
                for region in diff_regions:
                    self._result.add_change(
                        ChangeRecord(
                            key=f"Region_{region['id']}",
                            change_type=ChangeType.MODIFIED,
                            location=(
                                f"({region['x']}, {region['y']}) - "
                                f"({region['x'] + region['w']}, {region['y'] + region['h']})"
                            ),
                            metadata=region,
                        )
                    )

            # Sprint 8 Phase 2: Vector Diff (PDF 전용)
            if (
                self._vector_diff_enabled
                and FITZ_AVAILABLE
                and source_a.suffix.lower() == ".pdf"
                and source_b.suffix.lower() == ".pdf"
            ):

                # 벡터 PDF 여부 확인
                is_vector_a = self._is_vector_pdf(source_a, page)
                is_vector_b = self._is_vector_pdf(source_b, page)

                if is_vector_a and is_vector_b:
                    logger.info("Vector PDF 감지 - Vector Diff Engine 활성화")

                    paths_a = self._extract_vector_paths(source_a, page)
                    paths_b = self._extract_vector_paths(source_b, page)

                    vector_changes = self._compare_vectors(paths_a, paths_b)
                    for vc in vector_changes:
                        self._result.add_change(vc)

                    # 메타데이터에 벡터 정보 추가
                    self._result.metadata["vector_diff"] = {
                        "enabled": True,
                        "paths_a": len(paths_a),
                        "paths_b": len(paths_b),
                    }
                else:
                    logger.info("Raster PDF 감지 - Adaptive Raster Engine 사용")

            # 4. 텍스트 추출 및 비교 (PDF의 경우)
            if self._text_extraction and FITZ_AVAILABLE:
                if source_a.suffix.lower() == ".pdf" and source_b.suffix.lower() == ".pdf":
                    # Phase H2 fix — pass effective_page_a/b instead of the
                    # legacy ``page`` arg. The previous code compared text
                    # on page ``page`` (default 0) of both PDFs even when
                    # cross-page matching had pointed image diff at
                    # different pages. Result: image diff correct, text
                    # diff garbage. Now both use the same matched indices.
                    text_changes = self._compare_text(
                        source_a, source_b,
                        page_a=effective_page_a,
                        page_b=effective_page_b,
                    )
                    for tc in text_changes:
                        self._result.add_change(tc)

            # 메타데이터
            self._result.metadata.update(
                {
                    "alignment_enabled": self._alignment_enabled,
                    "ssim_threshold": self._ssim_threshold,
                    "dpi": self._dpi,
                    "image_size_a": img_a.shape[:2] if img_a is not None else None,
                    "image_size_b": img_b.shape[:2] if img_b is not None else None,
                }
            )

            logger.info(f"도면 비교 완료: {self._result.total_changes}개 변경 감지")

        except Exception as e:
            logger.error(f"도면 비교 실패: {e}")
            self._result.warnings.append(f"비교 중 오류 발생: {e}")
            raise

        return self._result

    def get_original_images(self) -> tuple:
        """Sprint 7: 원본 이미지 반환 (뷰어 고해상도 표시용)

        Returns:
            tuple: (img_a, img_b) - 150 DPI numpy 배열
        """
        return self._original_img_a, self._original_img_b

    def get_compare_images(self) -> tuple:
        """Phase 4: 정렬/리사이즈된 이미지 반환 (좌표 정합용)

        뷰어에서 diff_image와 동일한 좌표계를 사용하려면 이 메서드를 사용하세요.

        Returns:
            tuple: (img_a_aligned, img_b_aligned) - 리사이즈된 numpy 배열
        """
        return self._aligned_img_a, self._aligned_img_b

    # =========================================================================
    # Sprint 8 Phase 2: Vector Diff Engine
    # =========================================================================

    def _is_vector_pdf(self, path: Path, page: int = 0) -> bool:
        """PDF가 벡터 기반인지 스캔본인지 판별 (Sprint 8)

        Args:
            path: PDF 파일 경로
            page: 검사할 페이지 번호

        Returns:
            True: 벡터 PDF (CAD 저장본)
            False: 래스터 PDF (스캔본)
        """
        if not FITZ_AVAILABLE:
            return False

        try:
            doc = fitz.open(str(path))
            if page >= len(doc):
                return False

            page_obj = doc[page]

            # 벡터 경로 추출
            drawings = page_obj.get_drawings()

            # 벡터 경로가 10개 이상이면 벡터 PDF로 판단
            is_vector = len(drawings) > 10

            doc.close()

            logger.info(
                f"PDF 타입 감지: {'Vector' if is_vector else 'Raster'} ({len(drawings)} paths)"
            )
            return is_vector

        except Exception as e:
            logger.warning(f"PDF 타입 감지 실패: {e}")
            return False

    def _extract_vector_paths(self, path: Path, page: int = 0) -> List[Dict[str, Any]]:
        """벡터 경로 추출 및 정규화 (Sprint 8)

        Args:
            path: PDF 파일 경로
            page: 추출할 페이지 번호

        Returns:
            벡터 경로 목록 [{"type", "points", "color", "width"}, ...]
        """
        if not FITZ_AVAILABLE:
            return []

        try:
            doc = fitz.open(str(path))
            page_obj = doc[page]
            drawings = page_obj.get_drawings()

            paths = []
            for d in drawings:
                # 경로의 모든 아이템 추출
                items = d.get("items", [])
                normalized_points = []

                for item in items:
                    # item[0] = 명령어 (l=line, c=curve, re=rect 등)
                    # item[1:] = 좌표
                    coords = item[1:] if len(item) > 1 else []

                    # 좌표를 0.01mm 단위로 반올림 (부동소수점 오차 제거)
                    for coord in coords:
                        if isinstance(coord, (int, float)):
                            normalized_points.append(round(float(coord), 2))
                        elif hasattr(coord, "__iter__"):
                            for c in coord:
                                normalized_points.append(round(float(c), 2))

                paths.append(
                    {
                        "type": d.get("type", "path"),
                        "points": tuple(normalized_points),  # hashable
                        "color": d.get("color"),
                        "width": round(d.get("width", 1.0), 2),
                        "fill": d.get("fill"),
                    }
                )

            doc.close()

            logger.info(f"벡터 경로 추출: {len(paths)}개")
            return paths

        except Exception as e:
            logger.warning(f"벡터 경로 추출 실패: {e}")
            return []

    def _hash_path(self, path_data: Dict[str, Any]) -> str:
        """경로 해시 생성 (Sprint 8)

        Args:
            path_data: 벡터 경로 데이터

        Returns:
            MD5 해시 문자열
        """
        import hashlib

        # 해시에 포함할 요소들
        path_str = f"{path_data['type']}:{path_data['points']}:{path_data['width']}"
        return hashlib.md5(path_str.encode()).hexdigest()

    def _compare_vectors(
        self,
        paths_a: List[Dict[str, Any]],
        paths_b: List[Dict[str, Any]],
    ) -> List[ChangeRecord]:
        """벡터 경로 비교 (Sprint 8)

        Args:
            paths_a: 기준(Old) 벡터 경로
            paths_b: 신규(New) 벡터 경로

        Returns:
            변경 사항 목록
        """
        changes = []

        # 해시 세트 생성
        set_a = {self._hash_path(p) for p in paths_a}
        set_b = {self._hash_path(p) for p in paths_b}

        added = set_b - set_a
        removed = set_a - set_b

        # 개수 변화 기록
        if len(paths_a) != len(paths_b):
            changes.append(
                ChangeRecord(
                    key="VectorPathCount",
                    change_type=ChangeType.MODIFIED,
                    old_value=len(paths_a),
                    new_value=len(paths_b),
                    location="Vector Layer",
                )
            )

        # 추가된 경로
        for hash_val in added:
            changes.append(
                ChangeRecord(
                    key=f"VectorPath_ADD_{hash_val[:8]}",
                    change_type=ChangeType.ADDED,
                    new_value=f"Path {hash_val[:8]}",
                    location="Vector Layer",
                )
            )

        # 삭제된 경로
        for hash_val in removed:
            changes.append(
                ChangeRecord(
                    key=f"VectorPath_DEL_{hash_val[:8]}",
                    change_type=ChangeType.DELETED,
                    old_value=f"Path {hash_val[:8]}",
                    location="Vector Layer",
                )
            )

        logger.info(f"Vector Diff 완료: {len(added)}개 추가, {len(removed)}개 삭제")

        return changes

    def _load_image(self, path: Path, page: int = 0) -> Optional[np.ndarray]:
        """이미지 로드 (PDF -> Image 변환 포함)"""
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            if not FITZ_AVAILABLE:
                raise ImportError("PDF 처리를 위해 PyMuPDF(fitz)를 설치하세요: pip install PyMuPDF")

            doc = fitz.open(str(path))
            if page >= len(doc):
                raise ValueError(f"페이지 {page}가 없습니다 (총 {len(doc)} 페이지)")

            pix = doc[page].get_pixmap(dpi=self._dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

            # RGB로 변환 (RGBA인 경우)
            if pix.n == 4:
                if CV2_AVAILABLE:
                    img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
                else:
                    img = img[:, :, :3]

            doc.close()
            return img

        elif suffix in [".png", ".jpg", ".jpeg", ".bmp", ".tiff"]:
            if not CV2_AVAILABLE:
                raise ImportError(
                    "이미지 처리를 위해 OpenCV를 설치하세요: pip install opencv-python"
                )

            img = cv2.imread(str(path))
            if img is None:
                raise ValueError(f"이미지 로드 실패: {path}")
            return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        else:
            raise ValueError(f"지원하지 않는 파일 형식: {suffix}")

    def _align_images(
        self,
        img_ref: np.ndarray,
        img_target: np.ndarray,
    ) -> np.ndarray:
        """특징점 기반 자동 정렬"""
        if not CV2_AVAILABLE:
            return img_target

        # 그레이스케일 변환
        gray_ref = cv2.cvtColor(img_ref, cv2.COLOR_RGB2GRAY)
        gray_target = cv2.cvtColor(img_target, cv2.COLOR_RGB2GRAY)

        # ORB 특징점 검출
        orb = cv2.ORB_create(nfeatures=5000)
        kp1, desc1 = orb.detectAndCompute(gray_ref, None)
        kp2, desc2 = orb.detectAndCompute(gray_target, None)

        if desc1 is None or desc2 is None:
            logger.warning("특징점을 찾을 수 없어 정렬을 건너뜁니다.")
            return img_target

        # 매칭
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(desc1, desc2)
        matches = sorted(matches, key=lambda x: x.distance)

        # 상위 매칭만 사용
        good_matches = matches[: min(50, len(matches))]

        if len(good_matches) < 4:
            logger.warning("충분한 매칭점이 없어 정렬을 건너뜁니다.")
            return img_target

        # Homography 계산
        pts_ref = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        pts_target = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(pts_target, pts_ref, cv2.RANSAC, 5.0)

        if H is None:
            logger.warning("Homography 계산 실패")
            return img_target

        # Phase O5 — RANSAC inlier ratio warning. mask 는 N×1 uint8 (1=inlier).
        # Phase R1 (RV-20260510-003) — inlier_ratio 가 임계값 미만일 때
        # alignment_skip_warp_below_inlier=True 면 warp 을 건너뛰고 원본
        # target 반환 → 잘못된 homography cascade 차단 (silent drop 방지).
        skip_warp = False
        if mask is not None:
            inlier_count = int(mask.sum())
            total_count = int(mask.shape[0])
            inlier_ratio = inlier_count / total_count if total_count else 0.0
            logger.info(
                "RANSAC inlier ratio: %.2f (%d/%d)",
                inlier_ratio, inlier_count, total_count,
            )
            if inlier_ratio < self._alignment_min_inlier_ratio:
                msg = (
                    f"PDF alignment quality LOW (inlier ratio {inlier_ratio:.2f} "
                    f"< {self._alignment_min_inlier_ratio:.2f}) — diff 결과 신뢰도 낮음"
                )
                logger.warning(msg)
                if self._result is not None:
                    self._result.warnings.append(msg)
                    self._result.metadata["alignment_inlier_ratio"] = round(inlier_ratio, 3)
                if self._alignment_skip_warp_below_inlier:
                    skip_warp = True
                    skip_msg = (
                        "PDF alignment skipped (inlier ratio "
                        f"{inlier_ratio:.2f} < {self._alignment_min_inlier_ratio:.2f}) "
                        "— 원본 target 사용. 잘못된 homography cascade 차단."
                    )
                    logger.warning(skip_msg)
                    if self._result is not None:
                        self._result.warnings.append(skip_msg)
                        self._result.metadata["alignment_skipped"] = True

        if skip_warp:
            return img_target

        # 변환 적용
        h, w = img_ref.shape[:2]
        aligned = cv2.warpPerspective(img_target, H, (w, h))

        logger.info(f"자동 정렬 완료: {len(good_matches)}개 매칭점 사용")
        return aligned

    def _measure_noise_floor(self, img: np.ndarray) -> float:
        """배경 노이즈 레벨 측정 (Sprint 8: Adaptive Raster, Phase O5: 파라미터화)

        Phase O5 변경:
        - sigma_k 가 noise_filter_strength 프리셋에서 주어짐 (2.5/3.0/3.5)
        - 상한선이 DPI 비례 (DPI 240 → 100, DPI 60 → 25; baseline DPI 120 → 50)

        Args:
            img: 그레이스케일 이미지 (diff_map)

        Returns:
            noise_threshold: 추천 임계값 (mean + sigma_k·std), DPI 비례 cap
        """
        # 배경 영역 샘플링 (상위 90% 밝기 픽셀 = 흰색/배경)
        background_pixels = img[img > np.percentile(img, 90)]

        if len(background_pixels) == 0:
            logger.warning("배경 픽셀 감지 실패 - 기본 임계값 사용")
            return 30.0  # Fallback

        mean_noise = float(np.mean(background_pixels))
        std_noise = float(np.std(background_pixels))

        sigma_k = float(self._noise_profile.get("sigma_k", 3.0))
        threshold = mean_noise + sigma_k * std_noise

        # DPI-aware cap — DPI 120 baseline 에서 [20, 50], 비례 확장
        dpi_factor = max(0.5, float(self._dpi) / 120.0)
        cap_low = 20.0 * dpi_factor
        cap_high = 50.0 * dpi_factor
        capped_threshold = min(max(threshold, cap_low), cap_high)

        logger.info(
            "Noise profile: mean=%.2f std=%.2f sigma_k=%.1f raw=%.2f cap=[%.1f,%.1f] → %.2f",
            mean_noise, std_noise, sigma_k, threshold, cap_low, cap_high, capped_threshold,
        )
        return capped_threshold

    def compute_ssim_optimized(
        self, img_a: np.ndarray, img_b: np.ndarray, target_size: int = 1024
    ) -> float:
        """다운샘플링된 SSIM 계산 (4x 속도 향상)

        PRD 기반 최적화:
        - 가장 긴 변을 target_size로 리사이즈 (cv2.INTER_AREA)
        - 확대는 하지 않음 (scale < 1.0인 경우만)
        - Grayscale 변환 최적화
        - skimage.metrics.structural_similarity 사용

        Args:
            img_a: 첫 번째 이미지 (numpy array)
            img_b: 두 번째 이미지 (numpy array)
            target_size: 다운샘플링 목표 크기 (기본 1024)

        Returns:
            SSIM 점수 (0.0 ~ 1.0)
        """
        if not CV2_AVAILABLE:
            logger.warning("OpenCV 미설치 - SSIM 계산 불가")
            return -1.0

        try:
            from skimage.metrics import structural_similarity as ssim
        except ImportError:
            logger.warning("scikit-image 미설치 - SSIM 계산 불가")
            return -1.0

        # 1. 다운샘플링 필요 여부 확인
        h_a, w_a = img_a.shape[:2]
        h_b, w_b = img_b.shape[:2]

        max_dim_a = max(h_a, w_a)
        max_dim_b = max(h_b, w_b)
        max_dim = max(max_dim_a, max_dim_b)

        # 확대는 하지 않음 (scale < 1.0인 경우만)
        if max_dim > target_size:
            scale = target_size / max_dim

            # 새 크기 계산
            new_h_a = int(h_a * scale)
            new_w_a = int(w_a * scale)
            new_h_b = int(h_b * scale)
            new_w_b = int(w_b * scale)

            # 다운샘플링 (INTER_AREA: 고품질 축소)
            img_a_resized = cv2.resize(img_a, (new_w_a, new_h_a), interpolation=cv2.INTER_AREA)
            img_b_resized = cv2.resize(img_b, (new_w_b, new_h_b), interpolation=cv2.INTER_AREA)

            logger.info(f"SSIM 다운샘플링: {max_dim}px -> {target_size}px (scale={scale:.3f})")
        else:
            img_a_resized = img_a
            img_b_resized = img_b
            logger.info(f"SSIM 다운샘플링 스킵: {max_dim}px <= {target_size}px")

        # 2. 크기 맞추기 (둘 중 큰 크기로)
        h = max(img_a_resized.shape[0], img_b_resized.shape[0])
        w = max(img_a_resized.shape[1], img_b_resized.shape[1])

        img_a_final = cv2.resize(img_a_resized, (w, h), interpolation=cv2.INTER_AREA)
        img_b_final = cv2.resize(img_b_resized, (w, h), interpolation=cv2.INTER_AREA)

        # 3. Grayscale 변환 (이미 grayscale인 경우 스킵)
        if len(img_a_final.shape) == 3:
            gray_a = cv2.cvtColor(img_a_final, cv2.COLOR_RGB2GRAY)
        else:
            gray_a = img_a_final

        if len(img_b_final.shape) == 3:
            gray_b = cv2.cvtColor(img_b_final, cv2.COLOR_RGB2GRAY)
        else:
            gray_b = img_b_final

        # 4. SSIM 계산 (full=False로 점수만 계산)
        ssim_score = ssim(gray_a, gray_b, full=False)

        logger.info(f"SSIM 점수 (최적화): {ssim_score:.4f}")

        return float(ssim_score)

    def _compute_visual_diff(
        self,
        img_a: np.ndarray,
        img_b: np.ndarray,
    ) -> List[Dict[str, Any]]:
        """시각적 차이 영역 계산 (SSIM 기반 - Sprint 2)"""
        if not CV2_AVAILABLE:
            return []

        # 크기 맞추기
        h = max(img_a.shape[0], img_b.shape[0])
        w = max(img_a.shape[1], img_b.shape[1])

        img_a_resized = cv2.resize(img_a, (w, h))
        img_b_resized = cv2.resize(img_b, (w, h))

        # Phase 4: 정렬/리사이즈된 이미지 저장
        self._aligned_img_a = img_a_resized.copy()
        self._aligned_img_b = img_b_resized.copy()

        # 그레이스케일
        gray_a = cv2.cvtColor(img_a_resized, cv2.COLOR_RGB2GRAY)
        gray_b = cv2.cvtColor(img_b_resized, cv2.COLOR_RGB2GRAY)

        # Sprint 8 Fix: Bilateral Filter 제거 (텍스트 감지 저하 방지)
        # gray_a = cv2.bilateralFilter(gray_a, d=5, sigmaColor=75, sigmaSpace=75)
        # gray_b = cv2.bilateralFilter(gray_b, d=5, sigmaColor=75, sigmaSpace=75)

        # === SSIM 기반 차이 계산 (Sprint 2) ===
        try:
            from skimage.metrics import structural_similarity as ssim

            # SSIM 계산 (차이 맵 포함) - 여전히 full diff map이 필요함
            ssim_score, ssim_diff = ssim(gray_a, gray_b, full=True)

            # 차이 맵을 0-255로 변환 (1 - ssim_diff: 차이가 클수록 밝음)
            diff_map = (1 - ssim_diff) * 255
            diff_map = diff_map.astype(np.uint8)

            logger.info(f"SSIM 점수: {ssim_score:.4f}")

        except ImportError:
            # scikit-image 없으면 기존 absdiff 사용
            logger.warning("scikit-image 미설치 - 기본 absdiff 사용")
            diff_map = cv2.absdiff(gray_a, gray_b)
            ssim_score = -1.0

        # Sprint 8: 동적 임계값 계산 (Adaptive Raster, Phase O5: 파라미터화)
        adaptive_threshold = self._measure_noise_floor(diff_map)
        _, thresh = cv2.threshold(diff_map, adaptive_threshold, 255, cv2.THRESH_BINARY)

        # Phase O5 — 1차 모폴로지 (CLOSE → OPEN, kernel 은 프리셋)
        kernel_size = int(self._noise_profile.get("morph_kernel", 5))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        # Phase O5 — 2차 OPEN: anti-aliasing 잔여 제거.
        # 조건: SSIM ≥ gate (거의 동일한 페이지에서만 — thin line 변경 보호)
        #       AND 프로파일이 second_morph 활성. 2차 OPEN 은 작은 (≤
        #       blob_min_area px²) connected component 를 마스킹.
        ssim_score_for_gate = float(ssim_score) if ssim_score is not None else -1.0
        second_morph_enabled = bool(self._noise_profile.get("second_morph", True))
        if second_morph_enabled and ssim_score_for_gate >= self._anti_alias_ssim_gate:
            blob_min_area = int(self._noise_profile.get("blob_min_area", 25))
            preview_contours, _ = cv2.findContours(
                thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            small_count = 0
            for c in preview_contours:
                if cv2.contourArea(c) < blob_min_area:
                    cv2.drawContours(thresh, [c], -1, 0, -1)
                    small_count += 1
            if small_count:
                logger.info(
                    "[Phase O5] anti-alias OPEN: %d small blobs masked (SSIM=%.4f ≥ %.2f)",
                    small_count, ssim_score_for_gate, self._anti_alias_ssim_gate,
                )

        # 컨투어 검출
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # === SSIM 히트맵 시각화 (Sprint 2) ===
        # 컬러맵 적용 (JET: 차이가 클수록 빨강)
        heatmap_bgr = cv2.applyColorMap(diff_map, cv2.COLORMAP_JET)
        # Phase 3: BGR → RGB 변환 (원본이 RGB이므로 통일)
        heatmap_rgb = cv2.cvtColor(heatmap_bgr, cv2.COLOR_BGR2RGB)

        # 원본과 블렌딩 (50% 투명도)
        self._diff_image = cv2.addWeighted(img_a_resized, 0.5, heatmap_rgb, 0.5, 0)

        regions = []
        # Phase P (RV-20260508-013) — Phase O5 의 ``max(..., 100)`` 하드
        # 코딩 제거. 100 px² 안전장치가 NOISE_PROFILES.low(10) /
        # medium(25) / high(50) 의 의도를 무력화하여 작은 dimension
        # 텍스트 변경 (8-12pt, ~25-80px²) 이 모두 silent drop 되던
        # 회귀를 차단. 사용자가 "OCR 의 헛점" 으로 비유한 boundary case
        # 핵심 사례.
        #
        # 프로파일 default 는 backward-compat 유지 — 외부 캘러는 25
        # 받음. 사용자가 NOISE_PROFILES["low"] (=10) 을 명시해야 작은
        # 변경까지 surface.
        min_area = int(self._noise_profile.get("blob_min_area", 25))
        if min_area <= 0:
            min_area = 25

        for i, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            x, y, w_rect, h_rect = cv2.boundingRect(contour)

            # Sprint 8 Fix: 종횡비 필터 제거 (텍스트 감지 저하 방지)
            # aspect_ratio = float(w_rect) / h_rect if h_rect > 0 else 0
            # if aspect_ratio < 0.1 or aspect_ratio > 10.0:
            #     continue

            regions.append(
                {
                    "id": i,
                    "x": int(x),
                    "y": int(y),
                    "w": int(w_rect),
                    "h": int(h_rect),
                    "area": float(area),
                }
            )

            # 시각화: 빨간색 사각형 테두리
            cv2.rectangle(self._diff_image, (x, y), (x + w_rect, y + h_rect), (0, 0, 255), 2)

        logger.info(f"시각적 차이 영역: {len(regions)}개 (SSIM: {ssim_score:.4f})")
        return regions

    def _compare_text(
        self,
        pdf_a: Path,
        pdf_b: Path,
        page: int = 0,
        *,
        page_a: Optional[int] = None,
        page_b: Optional[int] = None,
    ) -> List[ChangeRecord]:
        """PDF 텍스트 비교.

        Phase H2 — added ``page_a``/``page_b`` keyword args mirroring
        :meth:`compare`. ``page`` remains the back-compat shortcut for
        identical pages. Required so cross-page matched pairs (page_a=2
        ↔ page_b=5) compare TEXT on the right pages, not on page 0.
        """
        if not FITZ_AVAILABLE:
            return []

        # Resolve effective indices
        effective_page_a = page if page_a is None else page_a
        effective_page_b = page if page_b is None else page_b

        changes = []

        doc_a = fitz.open(str(pdf_a))
        doc_b = fitz.open(str(pdf_b))

        try:
            if effective_page_a >= len(doc_a) or effective_page_b >= len(doc_b):
                return changes
            if effective_page_a < 0 or effective_page_b < 0:
                return changes

            # 텍스트 블록 추출 (위치 정보 포함)
            blocks_a = self._extract_text_blocks(doc_a[effective_page_a])
            blocks_b = self._extract_text_blocks(doc_b[effective_page_b])

            # Sprint 3.1: OCR Fallback - 텍스트 레이어가 없는 경우 (스캔 PDF)
            if (not blocks_a or not blocks_b) and self._ocr_fallback and _check_ocr_lazily():
                logger.info("텍스트 레이어가 없습니다. OCR Fallback을 시도합니다...")

                if self._ocr_extractor is None:
                    self._ocr_extractor = OCRExtractor(lang=self._ocr_lang)

                # OCR로 텍스트 추출
                if not blocks_a:
                    ocr_result_a = self._ocr_extractor.extract(str(pdf_a))
                    blocks_a = []
                    review_count_a = 0
                    for b in ocr_result_a.blocks:
                        # Phase H2 — match A's effective page (was: page)
                        if b.page == effective_page_a:
                            # Phase 3 P3-5: 신뢰도 체크 및 검토 필요 라벨링
                            b.check_confidence(self._ocr_confidence_threshold)
                            if b.review_needed:
                                review_count_a += 1
                                logger.warning(f"검토 필요: '{b.text}' - {b.review_reason}")
                            blocks_a.append(
                                {
                                    "bbox": b.bbox,
                                    "text": b.text,
                                    "confidence": b.confidence,
                                    "review_needed": b.review_needed,
                                    "review_reason": b.review_reason,
                                }
                            )
                    self._result.warnings.append(f"파일 A: OCR 사용 (블록 {len(blocks_a)}개)")
                    if review_count_a > 0:
                        self._result.warnings.append(
                            f"파일 A: 검토 필요 {review_count_a}개 (신뢰도 < {self._ocr_confidence_threshold:.0%})"
                        )

                if not blocks_b:
                    ocr_result_b = self._ocr_extractor.extract(str(pdf_b))
                    blocks_b = []
                    review_count_b = 0
                    for b in ocr_result_b.blocks:
                        # Phase H2 — match B's effective page (was: page)
                        if b.page == effective_page_b:
                            # Phase 3 P3-5: 신뢰도 체크 및 검토 필요 라벨링
                            b.check_confidence(self._ocr_confidence_threshold)
                            if b.review_needed:
                                review_count_b += 1
                                logger.warning(f"검토 필요: '{b.text}' - {b.review_reason}")
                            blocks_b.append(
                                {
                                    "bbox": b.bbox,
                                    "text": b.text,
                                    "confidence": b.confidence,
                                    "review_needed": b.review_needed,
                                    "review_reason": b.review_reason,
                                }
                            )
                    self._result.warnings.append(f"파일 B: OCR 사용 (블록 {len(blocks_b)}개)")
                    if review_count_b > 0:
                        self._result.warnings.append(
                            f"파일 B: 검토 필요 {review_count_b}개 (신뢰도 < {self._ocr_confidence_threshold:.0%})"
                        )

            # 위치 기반 매칭 및 비교 (최적화됨)
            # 블록을 Y 좌표(위->아래) 순으로 정렬하여 검색 범위를 제한
            blocks_a.sort(key=lambda b: b["bbox"][1])
            blocks_b.sort(key=lambda b: b["bbox"][1])

            matched_indices_b = set()

            # 검색 윈도우 크기 (Y 좌표 차이 허용 범위, 픽셀 단위)
            Y_TOLERANCE = 50.0

            # Optimization: 2중 루프 범위를 줄임
            b_start_idx = 0

            for block_a in blocks_a:
                best_match = None
                best_iou = 0
                best_j = -1

                y_center_a = (block_a["bbox"][1] + block_a["bbox"][3]) / 2

                # blocks_b의 검색 시작점을 업데이트 (정렬되어 있으므로)
                # 현재 block_a보다 훨씬 위에 있는 block_b는 건너뜀
                while b_start_idx < len(blocks_b):
                    # block_b의 하단 < block_a의 상단 - tolerance
                    if blocks_b[b_start_idx]["bbox"][3] < block_a["bbox"][1] - Y_TOLERANCE:
                        b_start_idx += 1
                    else:
                        break

                # 후보군 검색
                for j in range(b_start_idx, len(blocks_b)):
                    block_b = blocks_b[j]

                    # block_b의 상단 > block_a의 하단 + tolerance 이면 더 이상 볼 필요 없음 (정렬되어 있으므로)
                    if block_b["bbox"][1] > block_a["bbox"][3] + Y_TOLERANCE:
                        break

                    if j in matched_indices_b:
                        continue

                    iou = self._compute_bbox_iou(block_a["bbox"], block_b["bbox"])
                    if iou > best_iou:
                        best_iou = iou
                        best_match = block_b
                        best_j = j

                if best_match and best_iou > 0.5:
                    matched_indices_b.add(best_j)

                    # 텍스트가 다르면 수정됨
                    if block_a["text"].strip() != best_match["text"].strip():
                        changes.append(
                            ChangeRecord(
                                key=f"Text_{block_a['bbox']}",
                                change_type=ChangeType.MODIFIED,
                                field_name="텍스트",
                                old_value=block_a["text"].strip(),
                                new_value=best_match["text"].strip(),
                                location=f"Page {page + 1}, {block_a['bbox']}",
                            )
                        )
                else:
                    # 매칭되지 않으면 삭제됨
                    changes.append(
                        ChangeRecord(
                            key=f"Text_{block_a['bbox']}",
                            change_type=ChangeType.DELETED,
                            old_value=block_a["text"].strip(),
                            location=f"Page {page + 1}, {block_a['bbox']}",
                        )
                    )

            # 추가된 텍스트
            for j, block_b in enumerate(blocks_b):
                if j not in matched_indices_b:
                    changes.append(
                        ChangeRecord(
                            key=f"Text_{block_b['bbox']}",
                            change_type=ChangeType.ADDED,
                            new_value=block_b["text"].strip(),
                            location=f"Page {page + 1}, {block_b['bbox']}",
                        )
                    )

        finally:
            doc_a.close()
            doc_b.close()

        return changes

    def _extract_text_blocks(self, page) -> List[Dict[str, Any]]:
        """페이지에서 텍스트 블록 추출"""
        blocks = []
        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            if block.get("type") == 0:  # 텍스트 블록
                text = ""
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text += span.get("text", "")
                    text += "\n"

                if text.strip():
                    blocks.append(
                        {
                            "bbox": tuple(block["bbox"]),
                            "text": text,
                        }
                    )

        return blocks

    def _compute_bbox_iou(
        self,
        bbox_a: Tuple[float, ...],
        bbox_b: Tuple[float, ...],
    ) -> float:
        """바운딩 박스 IoU 계산"""
        x1 = max(bbox_a[0], bbox_b[0])
        y1 = max(bbox_a[1], bbox_b[1])
        x2 = min(bbox_a[2], bbox_b[2])
        y2 = min(bbox_a[3], bbox_b[3])

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area_a = (bbox_a[2] - bbox_a[0]) * (bbox_a[3] - bbox_a[1])
        area_b = (bbox_b[2] - bbox_b[0]) * (bbox_b[3] - bbox_b[1])
        union = area_a + area_b - intersection

        return intersection / union if union > 0 else 0.0

    def get_diff_image(self) -> Optional[np.ndarray]:
        """차이가 표시된 이미지 반환"""
        return self._diff_image

    def export_report(
        self,
        output_path: Union[str, Path],
        format: str = "image",
    ) -> Path:
        """비교 결과를 리포트로 내보냅니다."""
        if not self._result:
            raise ValueError("비교 결과가 없습니다. 먼저 compare()를 실행하세요.")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "image":
            return self._export_diff_image(output_path)
        elif format == "json":
            return self._export_json_report(output_path)
        else:
            raise ValueError(f"지원하지 않는 형식: {format}")

    def _export_diff_image(self, output_path: Path) -> Path:
        """차이 이미지 저장"""
        if self._diff_image is None:
            raise ValueError("차이 이미지가 없습니다.")

        if not CV2_AVAILABLE:
            raise ImportError("이미지 저장을 위해 OpenCV가 필요합니다.")

        # RGB -> BGR 변환 후 저장
        img_bgr = cv2.cvtColor(self._diff_image, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(output_path), img_bgr)

        logger.info(f"차이 이미지 저장: {output_path}")
        return output_path

    def _export_json_report(self, output_path: Path) -> Path:
        """JSON 형식으로 리포트 내보내기"""
        import json

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self._result.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(f"JSON 리포트 생성: {output_path}")
        return output_path

    def export_cloud_marked_pdf(
        self,
        source_pdf: Union[str, Path],
        output_path: Union[str, Path],
        page: int = 0,
        cloud_color: Tuple[float, float, float] = (1.0, 0.0, 0.0),
        cloud_width: float = 2.0,
        margin: float = 10.0,
    ) -> Path:
        """변경 영역에 구름마크를 추가한 PDF 내보내기

        Args:
            source_pdf: 원본 PDF 경로 (New 파일 기준)
            output_path: 저장 경로
            page: 페이지 번호
            cloud_color: 구름마크 색상 (RGB, 0.0-1.0)
            cloud_width: 선 두께
            margin: 영역 주위 여백

        Returns:
            저장된 파일 경로
        """
        if not FITZ_AVAILABLE:
            raise ImportError("PDF 처리를 위해 PyMuPDF(fitz)를 설치하세요: pip install PyMuPDF")

        if not self._result:
            raise ValueError("비교 결과가 없습니다. 먼저 compare()를 실행하세요.")

        source_pdf = Path(source_pdf)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 원본 PDF 복사하여 열기
        doc = fitz.open(str(source_pdf))

        if page >= len(doc):
            doc.close()
            raise ValueError(f"페이지 {page}가 없습니다 (총 {len(doc)} 페이지)")

        page_obj = doc[page]
        page_rect = page_obj.rect

        # 이미지 좌표 -> PDF 좌표 변환 비율 계산
        # 원본 이미지 크기 확인
        if self._original_img_b is not None:
            img_h, img_w = self._original_img_b.shape[:2]
            scale_x = page_rect.width / img_w
            scale_y = page_rect.height / img_h
        else:
            scale_x = scale_y = 1.0

        # 변경 영역에 구름마크 그리기
        cloud_count = 0
        for change in self._result.changes:
            meta = change.metadata
            if not meta or "x" not in meta:
                continue

            # 이미지 좌표를 PDF 좌표로 변환
            x = meta["x"] * scale_x
            y = meta["y"] * scale_y
            w = meta["w"] * scale_x
            h = meta["h"] * scale_y

            # 마진 추가
            rect = fitz.Rect(x - margin, y - margin, x + w + margin, y + h + margin)

            # 구름마크 (물결 모양 사각형) 그리기
            self._draw_cloud_mark(page_obj, rect, cloud_color, cloud_width)
            cloud_count += 1

        # 저장
        doc.save(str(output_path))
        doc.close()

        logger.info(f"구름마크 PDF 저장: {output_path} ({cloud_count}개 영역)")
        return output_path

    def _draw_cloud_mark(
        self,
        page,
        rect: "fitz.Rect",
        color: Tuple[float, float, float],
        width: float,
    ):
        """구름마크(Revision Cloud) 그리기

        Args:
            page: PyMuPDF 페이지 객체
            rect: 영역 사각형
            color: RGB 색상 (0.0-1.0)
            width: 선 두께
        """
        # 구름마크 세그먼트 크기
        segment = 15.0

        shape = page.new_shape()

        # 상단 가로선 (물결)
        self._draw_wavy_line(shape, rect.x0, rect.y0, rect.x1, rect.y0, segment, "down")
        # 우측 세로선 (물결)
        self._draw_wavy_line(shape, rect.x1, rect.y0, rect.x1, rect.y1, segment, "left")
        # 하단 가로선 (물결)
        self._draw_wavy_line(shape, rect.x1, rect.y1, rect.x0, rect.y1, segment, "up")
        # 좌측 세로선 (물결)
        self._draw_wavy_line(shape, rect.x0, rect.y1, rect.x0, rect.y0, segment, "right")

        shape.finish(color=color, width=width)
        shape.commit()

    def _draw_wavy_line(
        self,
        shape,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        segment: float,
        curve_dir: str,
    ):
        """물결 모양 선 그리기 (구름마크용) - 부드러운 반원 호 사용

        Args:
            shape: PyMuPDF Shape 객체
            x1, y1: 시작점
            x2, y2: 끝점
            segment: 물결 세그먼트 크기
            curve_dir: 곡선 방향 (up, down, left, right)
        """
        import math

        dx = x2 - x1
        dy = y2 - y1
        length = math.sqrt(dx * dx + dy * dy)

        if length < segment:
            shape.draw_line((x1, y1), (x2, y2))
            return

        num_segments = max(1, int(length / segment))
        step_x = dx / num_segments
        step_y = dy / num_segments

        # 수직 방향 벡터 계산 (곡선 방향용)
        if length > 0:
            # 단위 법선 벡터
            nx = -dy / length
            ny = dx / length
        else:
            nx, ny = 0, 0

        # 곡선 방향에 따른 오프셋 부호 결정
        if curve_dir == "down":
            sign = 1
        elif curve_dir == "up":
            sign = -1
        elif curve_dir == "left":
            sign = -1
        elif curve_dir == "right":
            sign = 1
        else:
            sign = 1

        # 호 반지름 (세그먼트의 절반)
        arc_radius = segment * 0.5

        current_x, current_y = x1, y1

        for i in range(num_segments):
            next_x = x1 + step_x * (i + 1)
            next_y = y1 + step_y * (i + 1)

            # 세그먼트 중심점
            mid_x = (current_x + next_x) / 2
            mid_y = (current_y + next_y) / 2

            # 제어점 (반원 호의 정점)
            # 법선 방향으로 오프셋
            ctrl_x = mid_x + sign * nx * arc_radius
            ctrl_y = mid_y + sign * ny * arc_radius

            # PyMuPDF의 draw_bezier로 부드러운 2차 베지어 곡선 그리기
            # 2차 베지어: 시작점 -> 제어점 -> 끝점
            # quadratic bezier를 cubic으로 변환
            # P0 = current, P1 = ctrl, P2 = next
            # Cubic: C1 = P0 + 2/3*(P1-P0), C2 = P2 + 2/3*(P1-P2)
            c1_x = current_x + (2 / 3) * (ctrl_x - current_x)
            c1_y = current_y + (2 / 3) * (ctrl_y - current_y)
            c2_x = next_x + (2 / 3) * (ctrl_x - next_x)
            c2_y = next_y + (2 / 3) * (ctrl_y - next_y)

            shape.draw_bezier((current_x, current_y), (c1_x, c1_y), (c2_x, c2_y), (next_x, next_y))

            current_x, current_y = next_x, next_y
