# -*- coding: utf-8 -*-
"""Phase H Stage-2 prototype corpus + reference embeddings.

The Stage-2 classifier reduces a zone to a category via:
    1. Canonicalise zone evidence text (normalizer.py)
    2. Embed via the configured backend (Qwen3-Embedding-0.6B-GGUF
       in production; stub backend in tests)
    3. Cosine similarity vs N pre-computed prototype embeddings
    4. Top-1 category wins (margin gates abstain)

This module owns the prototype corpus — the small set of seed
phrases that define what each category "looks like" in the
embedding space. The corpus is small (8 categories × 5-7 seeds =
~50 vectors) because:
  * Cosine similarity at this scale is essentially free (50 × 1024
    floats = 200 KB; under 1 ms per zone)
  * Prototype quality matters more than quantity
  * Adding a new category or refining a seed is a 1-line code change

Seed phrases are written by a structural engineer for a structural
engineer. They cover:
  * The literal Korean phrasing reviewers actually use ("보 단면 변경")
  * Representative section codes (H400×200×8×13, □400×400)
  * Typical change verbs (변경 / 추가 / 삭제 / 이동)

Reference embeddings are computed lazily on first launch and cached
per (backend, normalizer, corpus) version tuple via the manifest
machinery.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .schema import ChangeCategory

logger = logging.getLogger(__name__)


PROTOTYPE_CORPUS_VERSION = "v2.0"
CORPUS_FILENAME = "prototype_corpus_v2.json"
EMBEDDINGS_FILENAME = "prototype_embeddings_v2.npy"


# ---------------------------------------------------------------------------
# Seed corpus — 8 categories × 5-7 phrases (51 total)
# ---------------------------------------------------------------------------

# Each seed is a SHORT, REALISTIC Korean phrase a structural engineer
# would write in a revision note. Section codes use × (Unicode
# multiplication sign) — the normalizer canonicalises both x/× forms
# so either is fine here, but × matches the revision-cloud convention.

SEED_CORPUS: dict[ChangeCategory, list[str]] = {
    ChangeCategory.STRUCTURAL_MEMBER: [
        "보 단면 H400×200×8×13에서 H450×200×9×14로 변경",
        "기둥 강관 □400×400×16 단면 추가",
        "철골보 G3 H588×300×10×15 → H600×300×11×17 보강",
        "RC 슬래브 두께 200mm에서 250mm로 증가",
        "벽체 두께 200 → 250 변경, 전단벽 보강",
        "기초 D13@200 → HD16@150 배근 변경",
        "데크플레이트 합성보 추가, 1FL 구조 보강",
    ],
    ChangeCategory.DIMENSION: [
        "치수 8000mm에서 8500mm로 변경",
        "스팬 6000 → 7000 수정",
        "베이스 플레이트 두께 25t → 28t",
        "층고 4500 → 4800 변경",
        "보 길이 12000mm 치수 수정",
        "기둥 간격 9000 → 9500 조정",
    ],
    ChangeCategory.TEXT_LABEL: [
        "주기 추가: 시공 시 X-Ray 검사 필수",
        "도면 번호 S20-0002 → S20-0002A 변경",
        "재질 표기 SS400 → SM490 수정",
        "부재 명칭 G3 → G3' 표기 변경",
        "주석 텍스트 위치 이동",
        "도면 제목 폰트 변경",
    ],
    ChangeCategory.GRID: [
        "그리드 X3 위치 변경",
        "Y축 그리드 간격 조정 8000 → 8500",
        "그리드 명칭 X1A → X2 수정",
        "GRID A-1 좌표 이동",
        "FAB-1 그리드 라인 추가",
        "축선 X4' 신설, 평면도 보강",
    ],
    ChangeCategory.LAYOUT: [
        "기둥 위치 GRID A-1에서 A-2로 이동",
        "보 위치 조정, 슬래브 가장자리 정렬",
        "코어월 위치 변경, 평면 재배치",
        "엘리베이터 샤프트 위치 이동",
        "계단실 위치 변경, 동선 보강",
    ],
    ChangeCategory.DETAIL_DRAWING: [
        "DET-03 디테일 단면 변경",
        "SEC A-A 단면 상세 추가",
        "보-기둥 접합부 디테일 수정",
        "베이스 플레이트 상세도 보강",
        "거푸집 상세 추가, 시공도 업데이트",
    ],
    ChangeCategory.NOTE: [
        "일반 주기 추가, 시공 유의사항",
        "REMARK 1 → REMARK 2 항목 변경",
        "도면 일반 주석 업데이트",
        "구조 검토 의견 반영, 주기 추가",
        "심의 의견 반영, 노트 보강",
    ],
    ChangeCategory.UNKNOWN: [
        "기타 도면 수정",
        "분류 미정 변경 사항",
        "기타 코멘트 보강",
        "비분류 항목 업데이트",
    ],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrototypeEntry:
    """One seed in the corpus."""

    seed_id: int                # stable index across corpus version
    category: ChangeCategory
    raw_text: str               # original Korean phrasing
    canonical_text: str = ""    # populated by normalizer (lazy)


@dataclass
class PrototypeCorpus:
    """Loaded corpus + reference embeddings.

    Two life-cycle states:
      1. ``embeddings is None`` — corpus loaded but embeddings not
         yet computed. Caller should run ``compute_embeddings(backend)``
         to materialise ``embeddings``.
      2. ``embeddings`` populated — ready for cosine matching.
    """

    version: str
    entries: list[PrototypeEntry] = field(default_factory=list)
    embeddings: Optional[np.ndarray] = None  # shape (n, embedding_dim)
    embedding_dim: int = 0
    corpus_sha256: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def category_for(self, entry_index: int) -> ChangeCategory:
        return self.entries[entry_index].category


# ---------------------------------------------------------------------------
# Corpus serialisation
# ---------------------------------------------------------------------------


def build_default_corpus() -> PrototypeCorpus:
    """Build the in-memory corpus from ``SEED_CORPUS``.

    Doesn't compute embeddings — caller does that with their backend.
    Caller may also choose to canonicalise here or later.
    """

    entries: list[PrototypeEntry] = []
    seed_id = 0
    for category, phrases in SEED_CORPUS.items():
        for phrase in phrases:
            entries.append(PrototypeEntry(
                seed_id=seed_id,
                category=category,
                raw_text=phrase,
            ))
            seed_id += 1

    payload = json.dumps(
        {
            "version": PROTOTYPE_CORPUS_VERSION,
            "entries": [
                {"seed_id": e.seed_id, "category": e.category.value,
                 "raw_text": e.raw_text}
                for e in entries
            ],
        },
        sort_keys=True, ensure_ascii=False,
    )
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return PrototypeCorpus(
        version=PROTOTYPE_CORPUS_VERSION,
        entries=entries,
        corpus_sha256=sha,
    )


def save_corpus_json(corpus: PrototypeCorpus, path: Path) -> Path:
    """Persist the corpus seeds (no embeddings) to JSON.

    Embeddings live separately in a .npy alongside so they can be
    re-computed when backend/normalizer changes without re-emitting
    the seeds.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": corpus.version,
        "corpus_sha256": corpus.corpus_sha256,
        "entries": [
            {
                "seed_id": e.seed_id,
                "category": e.category.value,
                "raw_text": e.raw_text,
                "canonical_text": e.canonical_text,
            }
            for e in corpus.entries
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_corpus_json(path: Path) -> Optional[PrototypeCorpus]:
    """Read a previously-saved corpus JSON. Returns None on missing /
    corrupt file (caller falls back to ``build_default_corpus``)."""

    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Prototype corpus JSON unreadable at %s", path)
        return None
    entries = []
    for raw in data.get("entries", []):
        try:
            entries.append(PrototypeEntry(
                seed_id=int(raw["seed_id"]),
                category=ChangeCategory(raw["category"]),
                raw_text=str(raw["raw_text"]),
                canonical_text=str(raw.get("canonical_text", "")),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return PrototypeCorpus(
        version=str(data.get("version") or ""),
        entries=entries,
        corpus_sha256=str(data.get("corpus_sha256") or ""),
    )


# ---------------------------------------------------------------------------
# Embedding computation
# ---------------------------------------------------------------------------


def compute_corpus_embeddings(
    corpus: PrototypeCorpus,
    backend,  # EmbeddingBackend Protocol; loose typing avoids circular import
    *,
    canonicalise: bool = True,
    instruction: str = "",
    truncate_dim: Optional[int] = None,
) -> PrototypeCorpus:
    """Run the backend over every seed and return a NEW corpus with
    embeddings populated.

    Args:
        corpus: input corpus (entries only; embeddings ignored).
        backend: EmbeddingBackend instance (warmed-up or will be).
        canonicalise: when True, run normalizer over each seed first
            so the corpus matches what classify-time inputs see.
        instruction: when non-empty, prepend to each text (Qwen3-
            Embedding instruction-aware retrieval). Format follows
            the Qwen model card: "Instruct: {instruction}\\nQuery: {text}".
        truncate_dim: Phase I — Matryoshka truncation target. When
            set, prototypes are sliced to this many dimensions BEFORE
            L2-normalisation (the only correct order for unit-norm
            output). The dispatcher MUST pass the same value to
            backend.encode() for queries — otherwise cosine math fails
            with a shape mismatch.

    Returns:
        Fresh ``PrototypeCorpus`` with embeddings + embedding_dim
        populated. Original input is unchanged.
    """

    effective_dim = (
        int(truncate_dim) if truncate_dim is not None
        else getattr(backend, "native_dim", backend.embedding_dim)
    )

    if not corpus.entries:
        return PrototypeCorpus(
            version=corpus.version,
            entries=[],
            embeddings=np.zeros((0, effective_dim), dtype=np.float32),
            embedding_dim=effective_dim,
            corpus_sha256=corpus.corpus_sha256,
        )

    if canonicalise:
        from .normalizer import canonicalize_zone_text
        canonical_entries = [
            PrototypeEntry(
                seed_id=e.seed_id,
                category=e.category,
                raw_text=e.raw_text,
                canonical_text=canonicalize_zone_text(e.raw_text),
            )
            for e in corpus.entries
        ]
    else:
        canonical_entries = list(corpus.entries)

    texts = [
        _format_embedding_input(e.canonical_text or e.raw_text, instruction)
        for e in canonical_entries
    ]
    embeddings = backend.encode(
        texts,
        normalize=True,
        truncate_dim=truncate_dim,
    )

    return PrototypeCorpus(
        version=corpus.version,
        entries=canonical_entries,
        embeddings=embeddings.astype(np.float32),
        embedding_dim=int(embeddings.shape[1]) if embeddings.size else 0,
        corpus_sha256=corpus.corpus_sha256,
    )


def _format_embedding_input(text: str, instruction: str) -> str:
    """Apply the Qwen3-Embedding instruction-aware prompt format."""

    if not instruction:
        return text
    # Format from Qwen3-Embedding model card
    return f"Instruct: {instruction}\nQuery: {text}"


def save_embeddings_npy(corpus: PrototypeCorpus, path: Path) -> Path:
    """Persist the reference embedding matrix to .npy."""

    if corpus.embeddings is None:
        raise ValueError("corpus.embeddings is None — call "
                         "compute_corpus_embeddings first")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(path), corpus.embeddings)
    return path


def load_embeddings_npy(path: Path) -> Optional[np.ndarray]:
    """Read previously-saved embedding matrix. Returns None on missing /
    corrupt file."""

    path = Path(path)
    if not path.exists():
        return None
    try:
        return np.load(str(path)).astype(np.float32)
    except (OSError, ValueError):
        logger.warning("Prototype embeddings .npy unreadable at %s", path)
        return None


__all__ = [
    "PROTOTYPE_CORPUS_VERSION",
    "CORPUS_FILENAME",
    "EMBEDDINGS_FILENAME",
    "SEED_CORPUS",
    "PrototypeEntry",
    "PrototypeCorpus",
    "build_default_corpus",
    "save_corpus_json",
    "load_corpus_json",
    "compute_corpus_embeddings",
    "save_embeddings_npy",
    "load_embeddings_npy",
]
