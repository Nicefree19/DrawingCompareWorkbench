"""Phase P (RV-20260508-013) — 구조 부재 layer 패턴 single source of truth.

Phase O 까지는 ``zone_classifier._LAYER_PATTERNS`` (regex, 한국어 포함) 와
``ChangeZoneOptions.structural_layer_patterns`` (fnmatch, 영문만) 가 별도
시스템으로 운영됨. 이 분리가 사용자 보고 정확도 회귀의 직접 원인:

- 한국 도면이 한국어 layer 사용 (예: ``기둥-1F``, ``보-2F``) 시
  ``ChangeZoneOptions.structural_layer_patterns`` 의 fnmatch 영문 패턴은
  매칭 실패 → ``_compute_zone_noise_score`` 에서 ``noise_score += 0.2``
  가산 → 단일 entity 변경이 ``min_changes_per_zone>=2`` recommended 와
  결합 시 zone 폐기 → **사용자 보고 시나리오 미검출**.

이 모듈은 양쪽이 공유하는 단일 패턴 소스를 제공한다.
"""
from __future__ import annotations

import re
from typing import Iterable, Tuple


# 한국어 + 영문 + 일반 약어. fnmatch 와일드카드 (``*BEAM*``) 와 한국어
# substring (``기둥``) 두 종류 매칭을 모두 지원.
STRUCTURAL_LAYER_PATTERNS_FNMATCH: Tuple[str, ...] = (
    # 영문 부재 종류
    "*BEAM*", "*COL*", "*COLUMN*", "*BRACE*", "*BRACING*",
    "*GIRDER*", "*TRUSS*", "*WALL*", "*SLAB*", "*PLATE*",
    "*FOOTING*", "*FOUNDATION*", "*PILE*", "*FRAME*",
    # 일반 약어
    "*GR_*", "*BM_*", "*CL_*", "*WL_*", "*FT_*",
)

STRUCTURAL_LAYER_PATTERNS_KOREAN: Tuple[str, ...] = (
    "기둥", "보", "가새", "거더", "트러스",
    "벽", "슬래브", "기초", "파일", "프레임",
)


# Regex 패턴 (zone_classifier 호환 — case-insensitive). Phase O 까지의
# ``_LAYER_PATTERNS`` 와 동일 의미를 유지 + 일반 약어 (BM_, CL_, GR_,
# WL_, FT_) 도 매칭. 약어는 ``\b`` 워드 경계로 영문 단어 일부가 우연히
# 잡히는 false-positive 방지 (예: "PROBLEM" 의 "BM" 미매칭).
STRUCTURAL_MEMBER_REGEX: re.Pattern[str] = re.compile(
    r"BEAM|COL(?:UMN)?|BRACE|BRACING|TRUSS|GIRDER|FRAME|"
    r"WALL|SLAB|PLATE|FOOTING|FOUNDATION|PILE|"
    r"\bBM[_-]|\bCL[_-]|\bGR[_-]|\bWL[_-]|\bFT[_-]|"
    r"기둥|보|가새|거더|트러스|벽|슬래브|기초|파일|프레임",
    re.IGNORECASE,
)


def is_structural_layer(layer: str) -> bool:
    """Layer 이름이 구조 부재 패턴에 매칭되는지 검사.

    한국어 substring + 영문 regex 둘 다 시도. ``layer=""`` 또는 ``None``
    이면 False.

    Args:
        layer: layer 이름. 케이스 민감 X.

    Returns:
        True if any 구조 부재 패턴에 매칭.
    """
    if not layer:
        return False
    layer_str = str(layer).strip()
    if not layer_str:
        return False
    # 한국어 substring (regex 도 잡지만 빠른 short-circuit)
    for kr in STRUCTURAL_LAYER_PATTERNS_KOREAN:
        if kr in layer_str:
            return True
    # 영문/일반 regex
    return bool(STRUCTURAL_MEMBER_REGEX.search(layer_str))


def any_structural_layer(layers: Iterable[str]) -> bool:
    """layer 시퀀스 중 하나라도 구조 부재 패턴이면 True."""
    return any(is_structural_layer(layer) for layer in layers)


__all__ = [
    "STRUCTURAL_LAYER_PATTERNS_FNMATCH",
    "STRUCTURAL_LAYER_PATTERNS_KOREAN",
    "STRUCTURAL_MEMBER_REGEX",
    "is_structural_layer",
    "any_structural_layer",
]
