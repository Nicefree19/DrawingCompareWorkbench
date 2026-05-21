# -*- coding: utf-8 -*-
"""Phase R1 (RV-20260510-003) — PDF alignment skip-warp 안전 동작.

Stage A 가 DXF entity 추출의 silent-drop 카리어들을 닫은 데 이어,
Stage B Phase R 은 PDF 비교 분기의 동등 위험을 다룬다. R1 은 가장
명확한 carrier — RANSAC inlier ratio 가 임계값 미만일 때
``cv2.warpPerspective`` 가 그래도 적용되어 잘못된 정렬이 SSIM/diff
계산을 오염시키는 cascade 를 차단한다.

Phase O5 가 ``alignment_inlier_ratio`` warning 만 추가했을 때, warning
은 result.warnings 에 기록되지만 warped image 는 그대로 down-stream
으로 흘러갔다. Phase R1 은 ``alignment_skip_warp_below_inlier`` config
(default True) 로 warning 발생 시 warp 을 건너뛰고 원본 target 을
반환 → 잘못된 homography cascade 차단.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.services.comparison.base import ComparisonResult
from src.services.comparison.drawing_differ import DrawingDiffer


def _dotted_image(seed: int = 0) -> np.ndarray:
    """입력 모양만 맞추는 더미 이미지 (ORB 는 mock 이라 features 불필요)."""
    img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    np.random.seed(seed)
    for _ in range(8):
        y = int(np.random.randint(10, 90))
        x = int(np.random.randint(10, 90))
        img[y - 3 : y + 3, x - 3 : x + 3] = 0
    return img


def _mock_orb_pipeline(stack: ExitStack, n_matches: int = 10) -> tuple:
    """ORB → BFMatcher → findHomography → warpPerspective 전체 chain mock.

    Codex R1 round-1 P2 fix: 실제 ORB 호출은 환경에 따라 features 가
    적어 early-exit (line 575-588) 발생 → R1 skip-warp 분기 미도달.
    이 helper 가 모든 CV2 단계를 mock 하여 ``findHomography`` 가 항상
    호출되도록 보장.

    Returns:
        (mock_find, mock_warp): findHomography + warpPerspective MagicMock
    """
    # Mock ORB instance with detectAndCompute returning keypoints + descriptors
    fake_kp = [MagicMock(pt=(float(i), float(i))) for i in range(n_matches)]
    fake_desc = np.zeros((n_matches, 32), dtype=np.uint8)

    mock_orb = MagicMock()
    mock_orb.detectAndCompute = MagicMock(return_value=(fake_kp, fake_desc))
    stack.enter_context(patch(
        "src.services.comparison.drawing_differ.cv2.ORB_create",
        return_value=mock_orb,
    ))

    # Mock BFMatcher returning n_matches DMatch-like objects
    fake_matches = [
        MagicMock(distance=float(i), queryIdx=i, trainIdx=i)
        for i in range(n_matches)
    ]
    mock_matcher = MagicMock()
    mock_matcher.match = MagicMock(return_value=fake_matches)
    stack.enter_context(patch(
        "src.services.comparison.drawing_differ.cv2.BFMatcher",
        return_value=mock_matcher,
    ))

    return None, None  # callers patch findHomography/warpPerspective separately


class TestSkipWarpDefault:
    """default (skip_warp_below_inlier=True) 동작.

    Codex R1 round-1 P2 fix: ORB/매칭이 early-exit 하는 케이스 (descriptors
    None, matches < 4) 에서도 warp 호출은 발생하지 않아 ``mock_warp.
    assert_not_called()`` 가 false-pass 한다는 지적을 반영. 모든 테스트
    는 ``findHomography`` 가 실제로 호출되었음을 먼저 검증해서 R1
    skip-warp 분기에 정확히 도달했음을 보장.
    """

    def test_low_inlier_skips_warp_and_returns_target_unmodified(self):
        """inlier_ratio 0.2 < 0.3 → warpPerspective 호출 안 됨,
        반환 이미지가 원본 img_target 과 동일.

        Codex P2 가드: ORB 전체 chain mock + findHomography 호출 assert
        → ORB early-exit 가 아닌 R1 skip-warp 분기에서만 통과."""
        d = DrawingDiffer()  # default: skip_warp_below_inlier=True
        d._result = ComparisonResult(source_a="a.pdf", source_b="b.pdf")

        fake_H = np.eye(3, dtype=np.float32)
        fake_mask = np.array(
            [[1], [1], [0], [0], [0], [0], [0], [0], [0], [0]], dtype=np.uint8
        )

        img1 = _dotted_image(seed=1)
        img2 = _dotted_image(seed=2)

        with ExitStack() as stack:
            _mock_orb_pipeline(stack, n_matches=10)
            mock_find = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.findHomography",
                return_value=(fake_H, fake_mask),
            ))
            mock_warp = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.warpPerspective"
            ))
            aligned = d._align_images(img1, img2)

        # Codex P2: R1 skip-warp 분기에 도달했음 증명 — findHomography 호출
        assert mock_find.called, (
            "findHomography 까지 도달 못함 — R1 분기를 exercise 못함."
        )
        # warpPerspective 호출 안 됐어야 — skip_warp 활성
        mock_warp.assert_not_called()
        # 반환 이미지가 원본 img_target (img2) 와 동일
        assert aligned is img2 or np.array_equal(aligned, img2)

    def test_low_inlier_emits_skip_warning(self):
        """skip 시 result.warnings 에 LOW + skipped 두 메시지, metadata
        ``alignment_skipped=True`` set."""
        d = DrawingDiffer()
        d._result = ComparisonResult(source_a="a.pdf", source_b="b.pdf")

        fake_H = np.eye(3, dtype=np.float32)
        fake_mask = np.array(
            [[1], [0], [0], [0], [0], [0], [0], [0], [0], [0]], dtype=np.uint8
        )

        img1 = _dotted_image(seed=10)
        img2 = _dotted_image(seed=11)

        with ExitStack() as stack:
            _mock_orb_pipeline(stack, n_matches=10)
            mock_find = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.findHomography",
                return_value=(fake_H, fake_mask),
            ))
            d._align_images(img1, img2)

        # Codex P2: findHomography 호출됐음 증명 — ORB early-exit 아님
        assert mock_find.called, (
            "findHomography 호출 안 됨 — R1 분기 미도달, 테스트 무의미"
        )
        # 메시지 검증 — alignment quality LOW + skipped 양쪽
        assert any("alignment quality LOW" in w for w in d._result.warnings)
        assert any("alignment skipped" in w for w in d._result.warnings), (
            "Phase R1: skip_warp=True 일 때 'alignment skipped' warning "
            f"필수 (got: {d._result.warnings})"
        )
        assert d._result.metadata.get("alignment_skipped") is True

    def test_high_inlier_warp_applied_no_skip(self):
        """inlier_ratio 1.0 ≥ 0.3 → warp 그대로 적용, alignment_skipped 미설정."""
        d = DrawingDiffer()
        d._result = ComparisonResult(source_a="a.pdf", source_b="b.pdf")

        fake_H = np.eye(3, dtype=np.float32)
        fake_mask = np.ones((10, 1), dtype=np.uint8)  # 10/10 inlier

        img1 = _dotted_image(seed=20)
        img2 = _dotted_image(seed=21)

        with ExitStack() as stack:
            _mock_orb_pipeline(stack, n_matches=10)
            mock_find = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.findHomography",
                return_value=(fake_H, fake_mask),
            ))
            mock_warp = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.warpPerspective",
                return_value=img2,
            ))
            d._align_images(img1, img2)

        # Codex P2: 두 mock 모두 호출됐음 — high-inlier path 정확히 exercise
        assert mock_find.called, (
            "findHomography 호출 안 됨 — high-inlier path 미도달"
        )
        assert mock_warp.called, (
            "high inlier 시 warpPerspective 적용되어야 함 (skip 안 됨)"
        )
        # alignment_skipped flag 미설정
        assert d._result.metadata.get("alignment_skipped") is not True


class TestSkipWarpDisabled:
    """``alignment_skip_warp_below_inlier=False`` — Phase O5 legacy 동작."""

    def test_legacy_mode_warps_even_with_low_inlier(self):
        """opt-out 시 inlier_ratio 낮아도 warp 적용 (Phase O5 동작 보존).
        backward-compat — 명시적으로 끈 사용자는 기존 동작 유지.

        Codex P2 가드: findHomography + warpPerspective 모두 호출됐음
        assert → ORB early-exit 가 아닌 legacy 분기 정확히 exercise."""
        d = DrawingDiffer(config={"alignment_skip_warp_below_inlier": False})
        d._result = ComparisonResult(source_a="a.pdf", source_b="b.pdf")
        assert d._alignment_skip_warp_below_inlier is False

        fake_H = np.eye(3, dtype=np.float32)
        fake_mask = np.array(
            [[1], [0], [0], [0], [0], [0], [0], [0], [0], [0]], dtype=np.uint8
        )

        img1 = _dotted_image(seed=30)
        img2 = _dotted_image(seed=31)

        with ExitStack() as stack:
            _mock_orb_pipeline(stack, n_matches=10)
            mock_find = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.findHomography",
                return_value=(fake_H, fake_mask),
            ))
            mock_warp = stack.enter_context(patch(
                "src.services.comparison.drawing_differ.cv2.warpPerspective",
                return_value=img2,
            ))
            d._align_images(img1, img2)

        assert mock_find.called, (
            "findHomography 호출 안 됨 — legacy 분기 미도달"
        )
        # legacy 모드 — warp 호출되어야 함 (LOW warning 만 emit, skip 안 함)
        assert mock_warp.called, (
            "legacy 모드 (skip_warp=False) 에서 inlier 낮아도 warp 적용 필수"
        )
        # alignment_skipped flag 미설정 (legacy 모드)
        assert d._result.metadata.get("alignment_skipped") is not True


class TestConfigDefaults:
    """config 기본값 검증."""

    def test_default_skip_warp_is_true(self):
        """default 가 True (안전한 새 동작)."""
        d = DrawingDiffer()
        assert d._alignment_skip_warp_below_inlier is True

    def test_explicit_true_works(self):
        """explicit True 도 동작."""
        d = DrawingDiffer(config={"alignment_skip_warp_below_inlier": True})
        assert d._alignment_skip_warp_below_inlier is True

    def test_explicit_false_disables(self):
        """explicit False 로 끄기 가능 (legacy)."""
        d = DrawingDiffer(config={"alignment_skip_warp_below_inlier": False})
        assert d._alignment_skip_warp_below_inlier is False
