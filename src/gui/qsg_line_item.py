# -*- coding: utf-8 -*-
"""GPU scene-graph line renderer for the lightweight viewport skeleton (T2).

Why this exists (2026-06-11, "버벅거리는게 뷰어상 반응속도가 너무 느려"):
the Canvas skeleton repaints the whole primitive set on every camera
settle — measured ~40-60 ms of GUI-thread block at the 66k-segment scale
of a real detail sheet, felt as a hitch at the end of every pan/zoom.
T1 (cheap-pan) only deferred that cost; this module removes it.

Design: ONE ``QSGGeometryNode`` holding every segment as GL_LINES with
per-vertex colour (``ColoredPoint2D`` + ``QSGVertexColorMaterial``),
parented under a ``QSGTransformNode``. Camera changes update ONLY the
4x4 matrix — no retessellation, no repaint, no GUI-thread raster work.
Geometry is rebuilt only when the primitive payload itself changes
(drawing load / zone focus merge), via a single ``ctypes.memmove`` into
the vertex buffer.

The wiring contract predates this module (Phase G3 socket):

* ``register_qml_type()`` — importable hook called by
  ``LightweightDrawingViewport.__init__``; import failure of THIS module
  must keep the Canvas fallback working (never raise at import time
  beyond ImportError semantics).
* ``QSGLineItem.setPrimitives(list)`` / ``lineCount()`` — called by
  ``_push_primitives_to_qsg`` with the same primitive dicts the Canvas
  reads: ``{type: "lines"|"path", geometry: [...], properties: {...}}``.
* The item instance is created Python-side and parented into the QML
  ``qsgSkeletonPlaceholder`` container with objectName ``qsgSkeleton``.

Threading note: under ``QQuickWidget`` the scene graph renders on the
GUI thread, so ``updatePaintNode`` runs with the GIL on the main thread
— plain Python state needs no locking.
"""

from __future__ import annotations

import ctypes
import logging
import struct
from typing import Iterable, Optional, Sequence

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtGui import QMatrix4x4
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import (
    QQuickItem,
    QSGGeometry,
    QSGGeometryNode,
    QSGNode,
    QSGTransformNode,
    QSGVertexColorMaterial,
)

logger = logging.getLogger(__name__)

# Matches the Canvas paint default pen (#0F172A) so QSG output is
# visually identical to the fallback renderer.
DEFAULT_INK_RGBA = (15, 23, 42, 255)
# Curve flattening for path "C"/"Q" commands. 8 chords per curve keeps
# revision-cloud arcs round at skeleton scale while staying cheap.
BEZIER_STEPS = 8
# ColoredPoint2D layout: float x, float y, uchar r, g, b, a == 12 bytes.
_VERTEX_STRUCT = struct.Struct("<ffBBBB")
_VERTEX_SIZE = _VERTEX_STRUCT.size


def _parse_color(text: object) -> tuple[int, int, int, int]:
    """#rrggbb / #rrggbbaa → RGBA bytes; anything else → default ink."""

    if isinstance(text, str):
        value = text.strip()
        if value.startswith("#"):
            value = value[1:]
        if len(value) in (6, 8):
            try:
                r = int(value[0:2], 16)
                g = int(value[2:4], 16)
                b = int(value[4:6], 16)
                a = int(value[6:8], 16) if len(value) == 8 else 255
                return (r, g, b, a)
            except ValueError:
                pass
    return DEFAULT_INK_RGBA


def flatten_primitives(
    primitives: Optional[Iterable[dict]],
) -> tuple[bytearray, int]:
    """Pack scene-pack primitives into a ColoredPoint2D GL_LINES buffer.

    Returns ``(buffer, segment_count)`` where the buffer holds
    ``segment_count * 2`` vertices. Mirrors the Canvas paint loop's
    interpretation of the payload (same default colour, same path
    command set M/L/C/Q/Z), with curves flattened to ``BEZIER_STEPS``
    chords. Malformed entries are skipped, never raised.
    """

    buf = bytearray()
    pack = _VERTEX_STRUCT.pack
    segments = 0

    def _emit(ax: float, ay: float, bx: float, by: float,
              rgba: tuple[int, int, int, int]) -> None:
        nonlocal segments
        r, g, b, a = rgba
        buf.extend(pack(ax, ay, r, g, b, a))
        buf.extend(pack(bx, by, r, g, b, a))
        segments += 1

    for prim in primitives or []:
        if not isinstance(prim, dict):
            continue
        geometry = prim.get("geometry")
        if not geometry:
            continue
        props = prim.get("properties")
        rgba = _parse_color(props.get("color") if isinstance(props, dict) else None)
        ptype = prim.get("type")
        if ptype == "lines":
            for seg in geometry:
                if not isinstance(seg, (list, tuple)) or len(seg) < 4:
                    continue
                try:
                    _emit(float(seg[0]), float(seg[1]),
                          float(seg[2]), float(seg[3]), rgba)
                except (TypeError, ValueError):
                    continue
        elif ptype == "path":
            _flatten_path(geometry, rgba, _emit)
    return buf, segments


def _flatten_path(commands: Sequence, rgba: tuple[int, int, int, int],
                  emit) -> None:
    """Flatten an M/L/C/Q/Z command list into line segments."""

    cur_x = cur_y = None
    start_x = start_y = None
    for cmd in commands:
        if not isinstance(cmd, (list, tuple)) or not cmd:
            continue
        op = cmd[0]
        try:
            if op == "M" and len(cmd) >= 3:
                cur_x, cur_y = float(cmd[1]), float(cmd[2])
                start_x, start_y = cur_x, cur_y
            elif op == "L" and len(cmd) >= 3 and cur_x is not None:
                nx, ny = float(cmd[1]), float(cmd[2])
                emit(cur_x, cur_y, nx, ny, rgba)
                cur_x, cur_y = nx, ny
            elif op == "C" and len(cmd) >= 7 and cur_x is not None:
                x1, y1 = float(cmd[1]), float(cmd[2])
                x2, y2 = float(cmd[3]), float(cmd[4])
                x3, y3 = float(cmd[5]), float(cmd[6])
                px, py = cur_x, cur_y
                for i in range(1, BEZIER_STEPS + 1):
                    t = i / BEZIER_STEPS
                    mt = 1.0 - t
                    qx = (mt * mt * mt * cur_x + 3 * mt * mt * t * x1
                          + 3 * mt * t * t * x2 + t * t * t * x3)
                    qy = (mt * mt * mt * cur_y + 3 * mt * mt * t * y1
                          + 3 * mt * t * t * y2 + t * t * t * y3)
                    emit(px, py, qx, qy, rgba)
                    px, py = qx, qy
                cur_x, cur_y = x3, y3
            elif op == "Q" and len(cmd) >= 5 and cur_x is not None:
                x1, y1 = float(cmd[1]), float(cmd[2])
                x2, y2 = float(cmd[3]), float(cmd[4])
                px, py = cur_x, cur_y
                for i in range(1, BEZIER_STEPS + 1):
                    t = i / BEZIER_STEPS
                    mt = 1.0 - t
                    qx = mt * mt * cur_x + 2 * mt * t * x1 + t * t * x2
                    qy = mt * mt * cur_y + 2 * mt * t * y1 + t * t * y2
                    emit(px, py, qx, qy, rgba)
                    px, py = qx, qy
                cur_x, cur_y = x2, y2
            elif op == "Z" and cur_x is not None and start_x is not None:
                if cur_x != start_x or cur_y != start_y:
                    emit(cur_x, cur_y, start_x, start_y, rgba)
                cur_x, cur_y = start_x, start_y
        except (TypeError, ValueError):
            continue


class QSGLineItem(QQuickItem):
    """GPU line-segment layer driven by camera matrix updates only."""

    cameraChanged = Signal()

    def __init__(self, parent: Optional[QQuickItem] = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._buffer = bytearray()
        self._segment_count = 0
        self._buffer_dirty = False
        self._camera_center_x = 0.0
        self._camera_center_y = 0.0
        self._units_per_pixel = 1.0
        self._camera_dirty = True
        # Python refs keep the shiboken wrappers alive for the node's
        # lifetime; the scene graph (C++) owns and deletes the objects —
        # we only touch them inside updatePaintNode.
        self._geometry: Optional[QSGGeometry] = None
        self._geom_node: Optional[QSGGeometryNode] = None
        self._material: Optional[QSGVertexColorMaterial] = None

    # ---- data ----------------------------------------------------------

    @Slot(list)
    def setPrimitives(self, primitives: Optional[list]) -> None:
        """Replace the segment payload (same dicts the Canvas reads)."""

        self._buffer, self._segment_count = flatten_primitives(primitives)
        self._buffer_dirty = True
        self.update()

    @Slot(result=int)
    def lineCount(self) -> int:
        return self._segment_count

    # ---- camera --------------------------------------------------------

    @Slot(float, float, float)
    def setCamera(self, center_x: float, center_y: float,
                  units_per_pixel: float) -> None:
        upp = max(1e-4, float(units_per_pixel))
        if (center_x == self._camera_center_x
                and center_y == self._camera_center_y
                and upp == self._units_per_pixel):
            return
        self._camera_center_x = float(center_x)
        self._camera_center_y = float(center_y)
        self._units_per_pixel = upp
        self._camera_dirty = True
        self.cameraChanged.emit()
        self.update()

    def _get_units_per_pixel(self) -> float:
        return self._units_per_pixel

    unitsPerPixel = Property(float, _get_units_per_pixel, notify=cameraChanged)

    # ---- geometry ------------------------------------------------------

    def geometryChange(self, new_geometry, old_geometry) -> None:  # noqa: N802
        super().geometryChange(new_geometry, old_geometry)
        # The world→pixel matrix anchors at the item centre.
        self._camera_dirty = True
        self.update()

    def updatePaintNode(self, old_node, _update_data):  # noqa: N802
        node = old_node
        if node is None or not isinstance(node, QSGTransformNode):
            node = QSGTransformNode()
            self._geometry = QSGGeometry(
                QSGGeometry.defaultAttributes_ColoredPoint2D(), 0
            )
            self._geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawLines)
            self._geometry.setLineWidth(1.0)
            geom_node = QSGGeometryNode()
            geom_node.setGeometry(self._geometry)
            self._material = QSGVertexColorMaterial()
            geom_node.setMaterial(self._material)
            node.appendChildNode(geom_node)
            self._geom_node = geom_node
            self._buffer_dirty = True
            self._camera_dirty = True

        if self._buffer_dirty and self._geometry is not None:
            vertex_count = self._segment_count * 2
            self._geometry.allocate(vertex_count)
            if vertex_count:
                ctypes.memmove(
                    int(self._geometry.vertexData()),
                    bytes(self._buffer),
                    vertex_count * _VERTEX_SIZE,
                )
            if self._geom_node is not None:
                self._geom_node.markDirty(QSGNode.DirtyState.DirtyGeometry)
            self._buffer_dirty = False

        if self._camera_dirty:
            w = float(self.width())
            h = float(self.height())
            upp = max(1e-4, self._units_per_pixel)
            s = 1.0 / upp
            matrix = QMatrix4x4()
            matrix.translate(w / 2.0, h / 2.0)
            matrix.scale(s, -s, 1.0)
            matrix.translate(-self._camera_center_x, -self._camera_center_y)
            node.setMatrix(matrix)
            node.markDirty(QSGNode.DirtyState.DirtyMatrix)
            self._camera_dirty = False

        return node


def register_qml_type() -> None:
    """Register the QML type (Phase G3 socket contract).

    The viewport instantiates the item from Python, so registration is
    not strictly required for rendering — but the call doubles as the
    import-time availability probe the constructor relies on.
    """

    qmlRegisterType(QSGLineItem, "DrawingCompare", 1, 0, "QSGLineItem")


__all__ = [
    "BEZIER_STEPS",
    "DEFAULT_INK_RGBA",
    "QSGLineItem",
    "flatten_primitives",
    "register_qml_type",
]
