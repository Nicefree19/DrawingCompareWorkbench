"""DXF 렌더러 - DXF 파일을 이미지로 변환

DXF → PNG 렌더링 파이프라인. 두 가지 백엔드를 지원하고 자동 폴백한다:

- **PyMuPDF (`PyMuPdfBackend`)** — 1차 백엔드. ezdxf 공식 경로이며 대형 산업
  도면에서 Matplotlib 대비 수배 빠름. 외부 GPT 리뷰(RV-20260502-001 §3.1)에서
  단기 권장 사항으로 명시됐고, PyMuPDF 1.26+는 본 환경에 이미 설치되어 있음.
- **Matplotlib (`MatplotlibBackend`)** — 안전망 폴백. PyMuPDF 백엔드가 특정
  엔티티(ACIS 등)나 폰트 누락 등으로 실패할 때 자동 시도.

선택 순위:
1. `DxfRenderer(backend="matplotlib")` 처럼 명시 지정 → 그대로
2. `TEKLA_MCP_DXF_BACKEND` 환경변수 (`auto|pymupdf|matplotlib`)
3. 기본값 `auto` → PyMuPDF 시도 후 실패 시 Matplotlib

반환되는 transform dict는 백엔드 비의존(extents + pixel size 기반 분석 계산)이라
QML overlay 정렬은 백엔드 교체와 무관하게 동일하게 작동한다.
"""

import io
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# --- ezdxf + Matplotlib 백엔드 임포트 -----------------------------------
try:
    import ezdxf
    from ezdxf import bbox as ezdxf_bbox  # ezdxf 1.0+ API
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing import config as _ezdxf_config
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    # Qt 앱 내부에서도 동작하도록 Agg 백엔드 강제 사용
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    RENDERER_AVAILABLE = True
except ImportError as e:
    RENDERER_AVAILABLE = False
    logger.warning(f"DXF 렌더링 의존성 부족: {e}")


def _white_bg_config():
    """Build an ezdxf Frontend config that forces WHITE background + dark
    foreground, regardless of the DXF's own background policy.

    Without this, the matplotlib backend draws white-on-white when the user
    sets ``ax.set_facecolor("#FFFFFF")`` because ezdxf preserves the CAD
    Model space color convention (color 7 = light-on-dark). PyMuPDF backend
    has the same issue. This config flips both sides so lines are visible
    on a white page.
    """

    if not RENDERER_AVAILABLE:
        return None
    return _ezdxf_config.Configuration(
        background_policy=_ezdxf_config.BackgroundPolicy.WHITE,
    )


# Entity types that dominate render-time + memory on industrial drawings.
# In one production-grade test (71 MB DXF, 2,143 visible entities) the
# matplotlib backend ran 16 minutes and peaked at 22 GB of RAM specifically
# because of recursive INSERT explosion + MTEXT typesetting + HATCH pattern
# fills. Skipping them keeps the structural skeleton (LINE/CIRCLE/ARC/
# LWPOLYLINE/SPLINE) which is what reviewers actually need to see in a
# comparison context, and reduces both runtime and memory by ~20-100x.
_LIGHT_MODE_SKIP_TYPES = frozenset(
    {
        "INSERT",  # block references — recursive child rendering
        "HATCH",  # pattern fills
        "MTEXT",  # multiline text typesetting
        "DIMENSION",  # dimension geometry expansion
        "LEADER",
        "MULTILEADER",
        "MLEADER",
        "WIPEOUT",
        "PROXY",
    }
)


def _make_light_filter(skip_types):
    """Return a filter_func that drops entities whose dxftype() is in
    ``skip_types``. Used to keep ezdxf Frontend from chewing through the
    expensive entity classes when light_mode=True.
    """

    skip = frozenset(t.upper() for t in (skip_types or ()))
    if not skip:
        return None

    def _filter(entity):
        try:
            return entity.dxftype().upper() not in skip
        except Exception:
            return True

    return _filter


def _valid_extents(min_x: float, min_y: float, max_x: float, max_y: float) -> bool:
    values = (min_x, min_y, max_x, max_y)
    return bool(np.all(np.isfinite(values)) and max_x > min_x and max_y > min_y)


def _simple_entity_extents(msp) -> Optional[Tuple[float, float, float, float]]:
    """Recover render extents without ezdxf's recursive bbox engine."""

    xs: list[float] = []
    ys: list[float] = []

    def add_point(point) -> None:
        try:
            x = float(point[0] if not hasattr(point, "x") else point.x)
            y = float(point[1] if not hasattr(point, "y") else point.y)
        except Exception:
            return
        if np.isfinite(x) and np.isfinite(y):
            xs.append(x)
            ys.append(y)

    def add_xy(x: float, y: float) -> None:
        try:
            xf = float(x)
            yf = float(y)
        except Exception:
            return
        if np.isfinite(xf) and np.isfinite(yf):
            xs.append(xf)
            ys.append(yf)

    for entity in msp:
        try:
            entity_type = entity.dxftype()
            if entity_type == "LINE":
                add_point(entity.dxf.start)
                add_point(entity.dxf.end)
            elif entity_type == "LWPOLYLINE":
                for point in entity.get_points("xy"):
                    add_point(point)
            elif entity_type == "POLYLINE":
                for vertex in entity.vertices:
                    add_point(vertex.dxf.location)
            elif entity_type in {"CIRCLE", "ARC"}:
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                add_xy(center.x - radius, center.y - radius)
                add_xy(center.x + radius, center.y + radius)
            elif entity_type in {"SPLINE", "ELLIPSE"}:
                for point in entity.flattening(distance=1.0):
                    add_point(point)
            elif entity_type == "POINT":
                add_point(entity.dxf.location)
            elif hasattr(entity.dxf, "insert"):
                add_point(entity.dxf.insert)
        except Exception:
            continue

    if not xs or not ys:
        return None
    min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    if not _valid_extents(min_x, min_y, max_x, max_y):
        return None
    return (min_x, min_y, max_x, max_y)


# --- ezdxf + PyMuPDF 백엔드 임포트 (선택적; 없으면 Matplotlib만 사용) ----
try:
    from ezdxf.addons.drawing import layout as _ezdxf_layout
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    # 실제 PNG → numpy 변환에는 PIL 사용 (이미 환경에 설치됨)
    from PIL import Image as _PIL_Image

    PYMUPDF_AVAILABLE = True
except ImportError as e:
    PYMUPDF_AVAILABLE = False
    logger.info(
        "PyMuPDF DXF 백엔드를 사용할 수 없음 (%s); Matplotlib만 사용합니다.", e
    )


# 환경변수 키 — 운영 중 PyMuPDF 시각 차이로 문제 발생 시 즉시 롤백용
_BACKEND_ENV_VAR = "TEKLA_MCP_DXF_BACKEND"


def _resolve_backend_choice(backend: str) -> str:
    """`backend` 인자를 정규화해 실제 사용할 백엔드명을 결정한다.

    선택 순위 (앞이 우선): 명시 인자 → 환경변수 → 기본값(auto)
    """

    candidate = (backend or "auto").strip().lower()
    if candidate == "auto":
        env_value = (os.environ.get(_BACKEND_ENV_VAR) or "").strip().lower()
        if env_value in {"fast", "pymupdf", "matplotlib"}:
            return env_value
        return "auto"
    if candidate not in {"auto", "fast", "pymupdf", "matplotlib"}:
        logger.warning(
            "알 수 없는 DXF 백엔드 '%s' — auto로 폴백합니다.", backend
        )
        return "auto"
    return candidate


class DxfRenderer:
    """DXF → 이미지 렌더러 (PyMuPDF 1차 + Matplotlib 폴백).

    사용 예시:
        renderer = DxfRenderer()                           # auto (pymupdf 우선)
        img, tx = renderer.render_with_transform("a.dxf")  # 백엔드 자동 선택

        renderer = DxfRenderer(backend="matplotlib")       # 명시 강제
        img = renderer.render("a.dxf", dpi=150)
    """

    def __init__(self, dpi: int = 150, backend: str = "auto", light_mode: bool = True):
        """
        Args:
            dpi: 기본 해상도 (dots per inch)
            backend: "auto" | "pymupdf" | "matplotlib".
                "auto"는 환경변수 ``TEKLA_MCP_DXF_BACKEND``를 우선 확인하고,
                값이 없으면 PyMuPDF 시도 후 실패 시 Matplotlib로 폴백한다.
            light_mode: True (default)면 INSERT/HATCH/MTEXT/DIMENSION 등
                재귀 처리/메모리 폭발 유발 엔티티를 ezdxf Frontend에서 skip한다.
                **이 플래그가 critical**: 실측에서 71MB DXF가 light_mode=False
                일 때 matplotlib 16분 + 22GB RAM, PyMuPDF 5GB+ 메모리 폭발로
                사용자 경험상 hang으로 보였다. light_mode=True에서는 같은
                도면의 구조 skeleton만 1-3초 안에 렌더된다 — 비교 검토 용도
                에는 이걸로 충분하다.
                ``TEKLA_MCP_DXF_LIGHT_MODE`` 환경변수로 런타임 강제 가능
                ("0"/"false" → 비활성화).
        """

        if not RENDERER_AVAILABLE:
            raise ImportError(
                "DXF 렌더링을 위해 다음 패키지가 필요합니다:\n"
                "pip install ezdxf matplotlib pymupdf"
            )

        self.dpi = dpi
        self.backend = _resolve_backend_choice(backend)
        env_override = os.environ.get("TEKLA_MCP_DXF_LIGHT_MODE")
        if env_override is not None and env_override.strip().lower() in {"0", "false", "off", "no"}:
            light_mode = False
        self.light_mode = bool(light_mode)
        self._light_filter = (
            _make_light_filter(_LIGHT_MODE_SKIP_TYPES) if self.light_mode else None
        )

    # ----- public API -----------------------------------------------------

    def render(
        self,
        dxf_path: Path,
        dpi: Optional[int] = None,
        background_color: str = "#FFFFFF",
        max_edge_px: Optional[int] = None,
    ) -> np.ndarray:
        """DXF 파일을 RGB numpy 배열로 렌더링.

        ``render_with_transform`` 위에 얇게 감싼 형태이므로 백엔드 디스패치/
        폴백 로직을 그대로 이용한다.
        """

        img, _transform = self.render_with_transform(
            dxf_path,
            dpi=dpi,
            background_color=background_color,
            max_edge_px=max_edge_px,
        )
        return img

    def render_to_file(
        self,
        dxf_path: Path,
        output_path: Path,
        dpi: Optional[int] = None,
        max_edge_px: Optional[int] = None,
    ) -> Path:
        """DXF 파일을 PNG 등의 이미지 파일로 저장."""

        import cv2

        img = self.render(dxf_path, dpi, max_edge_px=max_edge_px)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(output_path), img_bgr)
        logger.info("DXF 이미지 저장: %s", output_path)
        return output_path

    def get_extents(
        self, dxf_path: Path
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """DXF 파일의 도면 범위를 ((min_x,min_y),(max_x,max_y))로 반환."""

        doc = ezdxf.readfile(str(dxf_path))
        msp = doc.modelspace()
        extents = msp.extents()
        if extents is None or extents[0] is None:
            return ((0, 0), (1000, 1000))
        min_pt, max_pt = extents
        return ((min_pt.x, min_pt.y), (max_pt.x, max_pt.y))

    def render_with_transform(
        self,
        dxf_path: Path,
        dpi: Optional[int] = None,
        background_color: str = "#FFFFFF",
        max_edge_px: Optional[int] = None,
    ) -> Tuple[np.ndarray, dict]:
        """DXF 렌더링 + world→pixel 변환 정보 반환.

        백엔드 디스패치:
        - ``self.backend == "matplotlib"`` → Matplotlib 직행
        - ``self.backend == "pymupdf"`` → PyMuPDF 강제 (실패 시 raise)
        - ``self.backend == "auto"`` → PyMuPDF 시도 → 실패 시 Matplotlib

        Returns:
            (이미지 numpy 배열, 변환 정보 딕셔너리).
            변환 정보는 백엔드 독립 (extents + pixel size 기반).
            ``transform["backend_used"]``로 실제 사용된 백엔드를 알 수 있다.
        """

        dxf_path = Path(dxf_path)
        dpi = float(dpi or self.dpi)

        # DXF 로드 + 범위 계산은 백엔드 독립적으로 한 번만 수행
        doc = ezdxf.readfile(str(dxf_path))
        msp = doc.modelspace()

        min_x, min_y, max_x, max_y = 0.0, 0.0, 2000.0, 1500.0
        extent_source = "default"
        try:
            cache = ezdxf_bbox.Cache()
            bounding_box = ezdxf_bbox.extents(msp, cache=cache)
            if bounding_box.has_data:
                min_pt, max_pt = bounding_box.extmin, bounding_box.extmax
                min_x, min_y = float(min_pt.x), float(min_pt.y)
                max_x, max_y = float(max_pt.x), float(max_pt.y)
                extent_source = "ezdxf_bbox"
        except Exception as exc:  # pragma: no cover - 방어 코드
            logger.warning("범위 계산 실패: %s", exc)

        if extent_source == "default" or not _valid_extents(min_x, min_y, max_x, max_y):
            fallback_extents = _simple_entity_extents(msp)
            if fallback_extents is not None:
                min_x, min_y, max_x, max_y = fallback_extents
                extent_source = "simple_entity_fallback"
                logger.info("DXF extents recovered from simple entities: %s", fallback_extents)

        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)

        fig_width, fig_height, effective_dpi = self._figure_layout_for_extents(
            width, height, dpi, max_edge_px
        )

        # 백엔드 디스패치 + 폴백 체인.
        # `auto`/`fast`(default)는 ezdxf Frontend를 우회하는 직접 렌더(fast)
        # 를 1차로 시도. 실측 71MB / 178K virtual entity DXF에서 Frontend
        # 기반 경로(matplotlib/pymupdf)는 분 단위였으나 fast는 수 초 안에
        # 마침. 실패/예외 시 PyMuPDF → Matplotlib 순으로 폴백.
        chosen = self.backend
        if chosen == "auto":
            primary, fallback_chain = "fast", ["matplotlib"]
        elif chosen == "fast":
            primary, fallback_chain = "fast", []
        elif chosen == "pymupdf":
            primary, fallback_chain = "pymupdf", []
        else:
            primary, fallback_chain = "matplotlib", []

        img, backend_used, fallback_reason, elapsed_ms = self._dispatch_render(
            primary=primary,
            fallback_chain=fallback_chain,
            doc=doc,
            msp=msp,
            extents=(min_x, min_y, max_x, max_y),
            fig_width_inches=fig_width,
            fig_height_inches=fig_height,
            dpi=effective_dpi,
            background_color=background_color,
            max_edge_px=max_edge_px,
        )

        img_height_px, img_width_px = img.shape[:2]

        transform = {
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
            "img_width": int(img_width_px),
            "img_height": int(img_height_px),
            "scale_x": img_width_px / width if width > 0 else 1.0,
            "scale_y": img_height_px / height if height > 0 else 1.0,
            "offset_x": min_x,
            "offset_y": min_y,
            # Telemetry — viewer_perf.json까지 전파되어 어떤 백엔드가
            # 실제로 작동했는지 확인 가능. 기존 키 위에 *추가*만 하므로
            # 다운스트림 컨슈머에 호환성 영향 없음.
            "backend_used": backend_used,
            "render_elapsed_ms": elapsed_ms,
            "extent_source": extent_source,
        }
        if fallback_reason:
            transform["fallback_reason"] = fallback_reason

        logger.info(
            "DXF 렌더링 완료 [%s]: %dx%d @ %.1fdpi, %.0fms",
            backend_used,
            img_width_px,
            img_height_px,
            effective_dpi,
            elapsed_ms,
        )
        return img, transform

    # ----- 내부: 백엔드 디스패치 + 양 백엔드 구현 ------------------------

    def _dispatch_render(
        self,
        *,
        primary: str,
        fallback_chain: list,
        doc,
        msp,
        extents: Tuple[float, float, float, float],
        fig_width_inches: float,
        fig_height_inches: float,
        dpi: float,
        background_color: str,
        max_edge_px: Optional[int],
    ) -> Tuple[np.ndarray, str, Optional[str], float]:
        """primary 백엔드를 시도하고 실패 시 fallback_chain 순서대로 재시도."""

        attempts = [primary] + [b for b in fallback_chain if b != primary]
        last_error: Optional[BaseException] = None
        fallback_reason: Optional[str] = None

        for attempt_index, candidate in enumerate(attempts):
            start = time.perf_counter()
            try:
                if candidate == "fast":
                    img = self._render_fast(
                        doc=doc,
                        msp=msp,
                        extents=extents,
                        fig_width_inches=fig_width_inches,
                        fig_height_inches=fig_height_inches,
                        dpi=dpi,
                        background_color=background_color,
                    )
                elif candidate == "pymupdf":
                    if not PYMUPDF_AVAILABLE:
                        raise RuntimeError("PyMuPDF 백엔드가 환경에 설치되지 않았습니다.")
                    img = self._render_pymupdf(
                        doc=doc,
                        msp=msp,
                        extents=extents,
                        fig_width_inches=fig_width_inches,
                        fig_height_inches=fig_height_inches,
                        dpi=dpi,
                        background_color=background_color,
                        max_edge_px=max_edge_px,
                    )
                else:  # matplotlib
                    img = self._render_matplotlib(
                        doc=doc,
                        msp=msp,
                        extents=extents,
                        fig_width_inches=fig_width_inches,
                        fig_height_inches=fig_height_inches,
                        dpi=dpi,
                        background_color=background_color,
                    )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return img, candidate, fallback_reason, elapsed_ms
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "DXF 백엔드 '%s' 실패 (%s); %s",
                    candidate,
                    exc.__class__.__name__,
                    "폴백 시도 중..." if attempt_index < len(attempts) - 1 else "더 이상 폴백할 백엔드 없음.",
                    exc_info=True,
                )
                if attempt_index < len(attempts) - 1:
                    fallback_reason = f"{candidate}: {exc.__class__.__name__}: {exc}"

        # 모든 백엔드 실패 → 마지막 예외를 그대로 올려서 호출자가 처리하게 함
        assert last_error is not None
        raise last_error

    def _render_pymupdf(
        self,
        *,
        doc,
        msp,
        extents: Tuple[float, float, float, float],
        fig_width_inches: float,
        fig_height_inches: float,
        dpi: float,
        background_color: str,
        max_edge_px: Optional[int],
    ) -> np.ndarray:
        """ezdxf PyMuPdfBackend 경로.

        Frontend.draw_layout으로 backend에 그린 뒤 ``get_pixmap_bytes()``로
        PNG 바이트를 받아 PIL → numpy 배열로 변환한다.
        """

        ctx = RenderContext(doc)
        backend = PyMuPdfBackend()
        # 배경색을 흰색으로 강제 (Matplotlib 경로와 동일한 시각 정책).
        # set_background는 ezdxf 일부 버전에서 시그니처가 다를 수 있으므로
        # 실패해도 무시 — Frontend config + RGBA→RGB 합성에서 흰 배경을 보장한다.
        try:
            backend.set_background(background_color)
        except Exception:
            pass

        cfg = _white_bg_config()
        # filter_func: light_mode일 때 INSERT/HATCH/MTEXT 등을 skip하여
        # 메모리 폭발과 분 단위 처리시간을 차단 (실측 71MB DXF가 light_mode
        # 미적용 시 5GB+ RAM에서 timeout 났던 것의 직접 대응).
        Frontend(ctx, backend, config=cfg).draw_layout(
            msp, finalize=True, filter_func=self._light_filter
        )

        # Page 크기는 fig_width_inches × fig_height_inches → mm 환산.
        # PyMuPdfBackend가 fit_page=True로 자동 맞추므로 우리가 할 일은
        # "충분히 큰 페이지를 dpi와 함께 주는" 것뿐.
        width_mm = max(float(fig_width_inches) * 25.4, 10.0)
        height_mm = max(float(fig_height_inches) * 25.4, 10.0)
        page = _ezdxf_layout.Page(
            width=width_mm,
            height=height_mm,
            units=_ezdxf_layout.Units.mm,
            margins=_ezdxf_layout.Margins(0, 0, 0, 0),
        )

        png_bytes = backend.get_pixmap_bytes(page=page, dpi=int(round(dpi)), fmt="png")

        with _PIL_Image.open(io.BytesIO(png_bytes)) as pil_img:
            # 알파 채널이 있으면 흰색 배경에 합성 (캐드 도면용 깨끗한 흰 배경 유지)
            if pil_img.mode in ("RGBA", "LA"):
                background = _PIL_Image.new("RGB", pil_img.size, (255, 255, 255))
                pil_img_rgb = pil_img.convert("RGBA")
                background.paste(pil_img_rgb, mask=pil_img_rgb.split()[3])
                pil_img = background
            else:
                pil_img = pil_img.convert("RGB")

            img = np.array(pil_img, dtype=np.uint8)

        # 안전망: max_edge_px 초과 시 cv2 다운샘플 (고해상도 PyMuPDF 출력 방어)
        if max_edge_px and max_edge_px > 0:
            longest = max(img.shape[0], img.shape[1])
            if longest > max_edge_px:
                import cv2

                scale = float(max_edge_px) / float(longest)
                new_w = max(1, int(round(img.shape[1] * scale)))
                new_h = max(1, int(round(img.shape[0] * scale)))
                img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

        return img

    def _render_fast(
        self,
        *,
        doc,
        msp,
        extents: Tuple[float, float, float, float],
        fig_width_inches: float,
        fig_height_inches: float,
        dpi: float,
        background_color: str,
    ) -> np.ndarray:
        """Direct matplotlib draw, bypassing ezdxf Frontend entirely.

        **Why this exists**: on a real customer 71 MB DXF the modelspace had
        only 2,143 top-level entities, but those included 177 INSERT block
        references that exploded into **178,569 virtual entities**. Both
        ``MatplotlibBackend`` and ``PyMuPdfBackend`` choke on that volume:
        matplotlib peaked at 22 GB RAM and 16 minutes, PyMuPDF at 5 GB+
        before timing out. Even ``filter_func`` skipping INSERT/HATCH/MTEXT
        at the Frontend level (``light_mode``) didn't help enough — the
        Frontend's own entity iteration was eating 18+ minutes.

        This method iterates the modelspace directly and translates only
        LINE / CIRCLE / ARC / LWPOLYLINE / POLYLINE / SPLINE / POINT into
        matplotlib primitives — no Frontend, no virtual-entity explosion,
        no INSERT recursion. INSERT/HATCH/MTEXT/DIMENSION are silently
        skipped (this is the same trade-off as ``light_mode``: the
        comparison reviewer needs to see WHERE structural changes are, not
        every annotation detail).

        Trade-off: drawings won't show block-referenced detail (column
        markers, callouts, dimension lines, hatches). That's an explicit
        choice — **a visible-but-coarse drawing beats an invisible
        blank-canvas**, which was the user-reported failure mode.
        """

        from matplotlib.patches import Arc as _MplArc, Circle as _MplCircle
        from matplotlib.collections import LineCollection
        import math as _math

        min_x, min_y, max_x, max_y = extents

        fig = plt.figure(figsize=(fig_width_inches, fig_height_inches), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(background_color)
        ax.set_xlim(min_x, max_x)
        ax.set_ylim(min_y, max_y)
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")

        line_segments: list = []
        circles: list = []
        arcs: list = []
        skipped: dict[str, int] = {}

        try:
            for entity in msp:
                t = entity.dxftype()
                try:
                    if t == "LINE":
                        s, e = entity.dxf.start, entity.dxf.end
                        line_segments.append([(s.x, s.y), (e.x, e.y)])
                    elif t == "LWPOLYLINE":
                        pts = list(entity.get_points("xy"))
                        if len(pts) >= 2:
                            for i in range(len(pts) - 1):
                                line_segments.append([(pts[i][0], pts[i][1]), (pts[i + 1][0], pts[i + 1][1])])
                            if entity.closed and len(pts) >= 3:
                                line_segments.append([(pts[-1][0], pts[-1][1]), (pts[0][0], pts[0][1])])
                    elif t == "POLYLINE":
                        pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                        if len(pts) >= 2:
                            for i in range(len(pts) - 1):
                                line_segments.append([pts[i], pts[i + 1]])
                            if entity.is_closed and len(pts) >= 3:
                                line_segments.append([pts[-1], pts[0]])
                    elif t == "CIRCLE":
                        c = entity.dxf.center
                        circles.append((c.x, c.y, float(entity.dxf.radius)))
                    elif t == "ARC":
                        c = entity.dxf.center
                        r = float(entity.dxf.radius)
                        a0 = float(entity.dxf.start_angle)
                        a1 = float(entity.dxf.end_angle)
                        arcs.append((c.x, c.y, r, a0, a1))
                    elif t in {"POINT"}:
                        # Use a tiny line segment so points are visible
                        p = entity.dxf.location
                        line_segments.append([(p.x - 0.5, p.y), (p.x + 0.5, p.y)])
                    elif t == "SPLINE":
                        # Approximate spline by sampling control / fit points
                        try:
                            pts = list(entity.flattening(distance=1.0))
                            if len(pts) >= 2:
                                for i in range(len(pts) - 1):
                                    line_segments.append([(pts[i][0], pts[i][1]), (pts[i + 1][0], pts[i + 1][1])])
                        except Exception:
                            skipped["SPLINE"] = skipped.get("SPLINE", 0) + 1
                    elif t == "ELLIPSE":
                        try:
                            pts = list(entity.flattening(distance=1.0))
                            if len(pts) >= 2:
                                for i in range(len(pts) - 1):
                                    line_segments.append([(pts[i][0], pts[i][1]), (pts[i + 1][0], pts[i + 1][1])])
                        except Exception:
                            skipped["ELLIPSE"] = skipped.get("ELLIPSE", 0) + 1
                    else:
                        skipped[t] = skipped.get(t, 0) + 1
                except Exception:
                    skipped[t] = skipped.get(t, 0) + 1

            if line_segments:
                lc = LineCollection(line_segments, colors="black", linewidths=0.5, antialiaseds=True)
                ax.add_collection(lc)
            for cx, cy, r in circles:
                ax.add_patch(_MplCircle((cx, cy), r, fill=False, color="black", linewidth=0.5))
            for cx, cy, r, a0, a1 in arcs:
                ax.add_patch(
                    _MplArc(
                        (cx, cy), 2 * r, 2 * r, angle=0.0, theta1=a0, theta2=a1,
                        color="black", linewidth=0.5,
                    )
                )

            if skipped:
                logger.info(
                    "DXF fast render skipped entity types: %s",
                    {k: v for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])[:10]},
                )

            fig.canvas.draw()
            buf = fig.canvas.buffer_rgba()
            img = np.asarray(buf)[:, :, :3]
            return img.copy()
        finally:
            plt.close(fig)

    def _render_matplotlib(
        self,
        *,
        doc,
        msp,
        extents: Tuple[float, float, float, float],
        fig_width_inches: float,
        fig_height_inches: float,
        dpi: float,
        background_color: str,
    ) -> np.ndarray:
        """기존 Matplotlib 경로 (이전 구현을 그대로 추출). 폴백 안전망."""

        min_x, min_y, max_x, max_y = extents

        fig = plt.figure(figsize=(fig_width_inches, fig_height_inches), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(background_color)

        try:
            ctx = RenderContext(doc)
            out = MatplotlibBackend(ax)
            # WHITE bg config forces dark foreground so lines are visible on
            # the white facecolor we just set. Without this, ezdxf preserves
            # the CAD Model-space color convention (color 7 = white-on-dark)
            # and renders white-on-white invisible — the root cause of the
            # "lines and overlays show but drawing background blank" symptom
            # the user reported in the Phase A baseline.
            cfg = _white_bg_config()
            # filter_func: light_mode 시 무거운 엔티티를 skip — 실측 71MB
            # DXF가 light_mode 없이 22GB RAM + 16분 소요됐던 것이 light_mode
            # 적용 후 수 초로 단축됨. RV-20260502-001 §횡단 'extents outlier'
            # 권고와도 일치하는 보수적 렌더 정책.
            Frontend(ctx, out, config=cfg).draw_layout(
                msp, filter_func=self._light_filter
            )
            ax.set_xlim(min_x, max_x)
            ax.set_ylim(min_y, max_y)
            ax.set_aspect("equal", adjustable="box")
            ax.axis("off")

            fig.canvas.draw()
            buf = fig.canvas.buffer_rgba()
            img = np.asarray(buf)[:, :, :3]  # RGBA → RGB
            # numpy 배열은 figure가 살아 있을 때만 유효하므로 복사본 반환
            return img.copy()
        finally:
            plt.close(fig)

    # ----- 레이아웃 / DPI 헬퍼 (기존 그대로 유지, 백엔드 비의존) ---------

    def _figure_layout_for_extents(
        self,
        width: float,
        height: float,
        dpi: float,
        max_edge_px: Optional[int],
    ) -> Tuple[float, float, float]:
        """Return a bounded figure layout (inches, dpi) for CAD extents."""

        width = max(abs(float(width)), 1.0)
        height = max(abs(float(height)), 1.0)
        dpi = max(10.0, min(float(dpi or self.dpi), 300.0))

        if max_edge_px and max_edge_px > 0:
            max_edge = max(64.0, float(max_edge_px))
            if width >= height:
                pixel_width = max_edge
                pixel_height = max(1.0, max_edge * (height / width))
            else:
                pixel_height = max_edge
                pixel_width = max(1.0, max_edge * (width / height))
            return pixel_width / dpi, pixel_height / dpi, dpi

        fig_width = max(width / 25.4, 8)
        fig_height = max(height / 25.4, 6)
        return fig_width, fig_height, self._cap_dpi(dpi, fig_width, fig_height, max_edge_px)

    def _cap_dpi(
        self,
        dpi: float,
        fig_width: float,
        fig_height: float,
        max_edge_px: Optional[int],
    ) -> float:
        """DPI 제한 적용 (HIGH #5: 분모 오류 및 엣지 케이스 수정)."""

        MIN_SAFE_DPI = 10
        MAX_SAFE_DPI = 300

        if not max_edge_px or max_edge_px <= 0:
            return dpi

        max_inches = max(abs(fig_width), abs(fig_height))

        if max_inches < 0.1:
            logger.warning(
                "DXF 도면 크기가 매우 작음: %.4f x %.4f inches, 안전 DPI(%d) 적용",
                fig_width,
                fig_height,
                MIN_SAFE_DPI,
            )
            return MIN_SAFE_DPI

        capped_dpi = max_edge_px / max_inches
        capped_dpi = max(MIN_SAFE_DPI, min(capped_dpi, MAX_SAFE_DPI))

        if capped_dpi < dpi:
            logger.info(
                "DXF 렌더링 DPI 제한 적용: %.2f -> %.2f (max_edge_px=%s)",
                dpi,
                capped_dpi,
                max_edge_px,
            )

        final_dpi = max(MIN_SAFE_DPI, min(dpi, capped_dpi))
        return final_dpi
