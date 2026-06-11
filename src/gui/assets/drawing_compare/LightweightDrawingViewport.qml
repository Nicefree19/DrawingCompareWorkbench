// Phase G2.2 — Lightweight diff-steered viewport.
//
// Replaces the raster-PNG-based DrawingGpuViewport with native vector
// rendering. Five layers, painted in z-order:
//
//   1. backgroundLayer   — flat fill + grid pattern (skeleton mode only)
//   2. vectorLayer       — Canvas painting primitives from scene_pack
//                          (lines + path commands). Sharp at any zoom.
//   3. changeOverlayLayer — change-zone bbox + cloud (Repeater of Rect)
//   4. focusMarkerLayer  — selected zone pin/bbox (single Rect)
//   5. stateBadgeLayer   — top-right 7-state badge + diagonal watermark
//                          when relative_only
//
// Coordinate model: world coordinates pushed verbatim from Python via
// ``primitives``; QML transforms the Canvas with a 2D affine derived from
// ``worldBbox`` + viewport size + zoom. Pan/zoom are stored in world
// units so the rendered output stays sharp at any zoom level — no raster
// resampling artifacts.
//
// Performance note: Canvas paint is single-threaded but plenty fast for
// the LOD0 skeleton (~few thousand line segments). The QML must stay
// loadable without optional native/QSG extensions so PDF previews still
// render in packaged builds.

import QtQuick

Item {
    id: root

    // ---- INPUT PROPERTIES (pushed by Python) ---------------------------
    // Primitive list (LOD0 skeleton + optional vector_focus subset).
    // Each entry: { type: "lines"|"path", geometry: [...], properties: {...} }
    property var primitives: []
    // World bbox of the drawing (xmin, ymin, xmax, ymax).
    property var worldBbox: [0, 0, 1, 1]
    // 7-state RenderMode (string). Drives badge colour + watermark.
    property string renderMode: "relative_only"
    // Korean badge label (precomputed Python-side from RENDER_MODE_STYLES).
    property string renderModeLabel: "🟠 상대 위치 모드"
    // CSS hex for badge background.
    property string renderModeBadgeColor: "#F97316"
    // Whether to show the diagonal "상대 위치 모드" watermark.
    property bool showWatermark: true
    // Optional Korean status text under the badge (e.g. elapsed_ms).
    property string statusText: ""
    // Cloud + focus overlays (same shape as Phase F).
    property var overlaysCloud: []
    property var overlaysFocus: []
    // User-controlled overlay opacity scale (0.3-1.0).
    property real overlayOpacityScale: 1.0
    // Phase Q1 — minimum on-screen footprint (px) for a change marker so
    // small change zones (e.g. a 110 mm text edit) stay perceptible even
    // when the camera fits the whole multi-detail drawing (fitToView yields
    // ~578 mm/px on a 137 m × 551 m sheet → a 110 mm zone is ~0.2 px). The
    // marker is expanded SYMMETRICALLY about the change centre so it still
    // points at the real spot. >= 32 routes tiny zones through the scalloped
    // revision-cloud path (not the plain rect fallback).
    property real minCloudPx: 32
    // Phase A (large-cloud) — a change whose bbox spans more than this
    // fraction of the drawing in EITHER axis is treated as "oversized"
    // (e.g. a review leader line that crosses the sheet). Its cloud is
    // rendered as a faint dashed outline + a centroid pin instead of a bold
    // scalloped fill, so it doesn't blanket the view and drown the small
    // note clouds Q1 just made visible.
    property real largeCloudFraction: 0.5
    // Empty-state notice when no primitives loaded yet.
    property string emptyNotice: "도면을 선택하면 빠르게 표시됩니다."
    // Side-specific note for added/deleted zones where the opposite drawing
    // has no corresponding detail. This avoids the "blank side is broken"
    // interpretation while keeping the actual drawing visible underneath.
    property string sideMessage: ""
    // Phase G2.7 — PDF / raster background. When set, an Image element
    // covers the canvas at the same world bbox, giving the viewport the
    // legacy "PNG behind, overlays on top" look while keeping all the
    // lightweight ergonomics (camera sync, overlay click, badges).
    // Set ``backgroundImageSource`` to a file:// URL or empty string to
    // hide. ``backgroundImageWorldBbox`` should match worldBbox so the
    // image lines up with the same affine the overlay layers use.
    property string backgroundImageSource: ""
    property var backgroundImageWorldBbox: []  // [xmin, ymin, xmax, ymax]
    property string backgroundImageStatusName: pdfBackground.status === Image.Ready ? "ready"
        : pdfBackground.status === Image.Loading ? "loading"
        : pdfBackground.status === Image.Error ? "error"
        : "null"

    // ---- ZOOM / PAN STATE (world coords) -------------------------------
    // World-space camera centre + units-per-pixel. This is the canonical
    // representation; pixel positions are derived per paint.
    property real cameraCenterX: 0
    property real cameraCenterY: 0
    property real unitsPerPixel: 1.0
    // Zoom factor for UI (1.0 = fit to view). Internally we just adjust
    // unitsPerPixel.

    // Pick which skeleton renderer Python uses. This standalone QML is
    // Canvas-safe by default; Python may still set the property, but an
    // unavailable optional QSG module must never prevent the root item
    // from loading.
    property string skeletonRenderer: "canvas"
    // T2-B (2026-06-11) — rasterise the skeleton Canvas on the scene
    // graph's worker instead of the GUI thread: the settle repaint
    // measured 40-60 ms at real sheet scale (66k segments), felt as a
    // hitch after every pan/zoom. Python turns this OFF under the
    // offscreen test platform so grab-based pixel assertions stay
    // deterministic.
    property bool canvasThreadedRaster: true

    // ---- PAINT DIAGNOSTICS (read by benchmarks / perf tooling) ---------
    // Updated by vectorCanvas.onPaint on every completed paint. These are
    // observability-only — nothing inside this QML binds to them.
    property real lastPaintMs: 0
    property int paintCount: 0
    property int lastPaintDrawnSegments: 0
    property int lastPaintCulledSegments: 0

    signal viewportChanged(real centerX, real centerY, real upp)
    // Phase I4 — emitted when the user clicks a cloud or focus marker.
    // Wired by LightweightDrawingViewport (Python) → workbench
    // _select_zone_in_list_v2 so the list auto-selects the clicked zone.
    signal overlayClicked(string zoneId)

    clip: true

    // ---- BACKGROUND ----------------------------------------------------
    Rectangle {
        anchors.fill: parent
        color: "#FAFAFA"
        border.color: "#9CA3AF"
        border.width: 1
    }

    // ---- PDF / RASTER BACKGROUND IMAGE ---------------------------------
    // Phase G2.7 — for PDF inputs, the workbench supplies a rendered
    // bitmap of the page (via qt_pdf_adapter.PdfPageRenderer). It's
    // positioned in pixel space using the same world→pixel affine as
    // the overlay layers so cloud markers align correctly.
    Image {
        id: pdfBackground
        visible: root.backgroundImageSource !== ""
            && root.backgroundImageWorldBbox && root.backgroundImageWorldBbox.length === 4
        source: root.backgroundImageSource
        cache: true
        asynchronous: true
        // Re-rasterize on demand at high zoom — Qt picks a sourceSize
        // ≥ on-screen size to avoid blur. Cap to keep memory bounded.
        sourceSize.width: Math.min(8192, Math.max(width * 2, 1024))
        sourceSize.height: Math.min(8192, Math.max(height * 2, 1024))
        smooth: true
        fillMode: Image.Stretch
        z: 5  // above the empty placeholder, below vectorCanvas (z:10)
        x: {
            var bb = root.backgroundImageWorldBbox
            if (!bb || bb.length < 4) return 0
            var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
            return root.width / 2 + (bb[0] - root.cameraCenterX) * s
        }
        y: {
            var bb = root.backgroundImageWorldBbox
            if (!bb || bb.length < 4) return 0
            var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
            return root.height / 2 - (bb[3] - root.cameraCenterY) * s
        }
        width: {
            var bb = root.backgroundImageWorldBbox
            if (!bb || bb.length < 4) return 0
            var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
            return Math.max(1, (bb[2] - bb[0]) * s)
        }
        height: {
            var bb = root.backgroundImageWorldBbox
            if (!bb || bb.length < 4) return 0
            var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
            return Math.max(1, (bb[3] - bb[1]) * s)
        }
    }

    // ---- VECTOR LAYER (optional QSG placeholder) -----------------------
    // Older builds statically imported TeklaQSG here. That made the whole
    // QML fail to load when the optional native module was absent, leaving
    // PDF previews blank even though the rendered page PNG existed. Keep a
    // harmless placeholder so object names remain stable, and let Canvas
    // below be the guaranteed renderer. T2 (2026-06-11): Python now
    // instantiates QSGLineItem INSIDE this container when the optional
    // module imports; skeletonRenderer drives which layer is visible.
    Item {
        id: qsgContainer
        anchors.fill: parent
        visible: root.skeletonRenderer === "qsg"
        z: 10
        objectName: "qsgSkeletonPlaceholder"
    }

    // ---- VECTOR LAYER (Canvas — legacy fallback) -----------------------
    Canvas {
        id: vectorCanvas
        anchors.fill: parent
        antialiasing: true
        visible: root.skeletonRenderer !== "qsg"
        z: 10
        // T2-B — paint commands are recorded on the GUI thread but
        // rasterised on the scene graph worker, so the heavy stroke
        // playback no longer blocks input. Cheap-pan's item transform
        // keeps the previous frame following the camera until the
        // threaded raster of the settled frame lands.
        renderStrategy: root.canvasThreadedRaster ? Canvas.Threaded : Canvas.Immediate
        // CHEAP-PAN (T1, 2026-06-10): repainting the whole skeleton on EVERY
        // camera tick made pan/zoom cost O(all segments) per wheel notch —
        // measured 56 ms/paint × 29 paints for 30 ticks at 100k segments
        // (scripts/benchmark_viewport_paint.py). Instead, paint at a SETTLED
        // camera and track the live camera with an item-level affine (scale
        // about the view centre + translate). While interacting, the already
        // painted pixels follow the camera (slightly soft when zooming in);
        // a short settle timer triggers ONE crisp repaint when input pauses.
        // Content changes (primitives/worldBbox) settle immediately.
        property real settledCenterX: 0
        property real settledCenterY: 0
        property real settledUnitsPerPixel: 1.0

        function settle() {
            settledCenterX = root.cameraCenterX
            settledCenterY = root.cameraCenterY
            settledUnitsPerPixel = root.unitsPerPixel
            requestPaint()
        }

        Timer {
            id: settleTimer
            interval: 120
            repeat: false
            onTriggered: vectorCanvas.settle()
        }

        transform: [
            Scale {
                origin.x: vectorCanvas.width / 2.0
                origin.y: vectorCanvas.height / 2.0
                xScale: vectorCanvas.settledUnitsPerPixel / Math.max(0.0001, root.unitsPerPixel)
                yScale: vectorCanvas.settledUnitsPerPixel / Math.max(0.0001, root.unitsPerPixel)
            },
            Translate {
                x: (vectorCanvas.settledCenterX - root.cameraCenterX) / Math.max(0.0001, root.unitsPerPixel)
                y: -(vectorCanvas.settledCenterY - root.cameraCenterY) / Math.max(0.0001, root.unitsPerPixel)
            }
        ]

        Connections {
            target: root
            function onPrimitivesChanged() { vectorCanvas.settle() }
            function onWorldBboxChanged()  { vectorCanvas.settle() }
            function onCameraCenterXChanged() { settleTimer.restart() }
            function onCameraCenterYChanged() { settleTimer.restart() }
            function onUnitsPerPixelChanged() { settleTimer.restart() }
        }

        onPaint: {
            var __t0 = Date.now()
            var __drawn = 0
            var ctx = getContext("2d")
            ctx.save()
            ctx.clearRect(0, 0, width, height)

            if (!root.primitives || root.primitives.length === 0) {
                ctx.restore()
                root.paintCount += 1
                root.lastPaintMs = Date.now() - __t0
                root.lastPaintDrawnSegments = 0
                root.lastPaintCulledSegments = 0
                return
            }

            // World→pixel affine at the SETTLED camera (cheap-pan: while the
            // user interacts, the item transform above maps these pixels to
            // the live camera; this paint runs once on settle). Y flipped.
            var __culled = 0
            var upp = Math.max(0.0001, vectorCanvas.settledUnitsPerPixel)
            var s = 1.0 / upp
            var cx = vectorCanvas.settledCenterX
            var cy = vectorCanvas.settledCenterY
            var w  = width
            var h  = height
            // Translate so (cx, cy) maps to (w/2, h/2), with Y flipped.
            ctx.translate(w / 2.0, h / 2.0)
            ctx.scale(s, -s)
            ctx.translate(-cx, -cy)

            // Pen — thin black line scaled to look ~1 px regardless of zoom.
            ctx.lineWidth = upp
            ctx.strokeStyle = "#0F172A"
            ctx.lineCap = "round"
            ctx.lineJoin = "round"

            // Viewport culling (T1): segments fully outside the visible
            // world rect (+32 px margin) are skipped. At fit-to-view this
            // skips ~nothing; zoomed into one detail it skips almost the
            // whole sheet — measured 100k drawn -> ~1k at a 1% window.
            var mWorld = 32.0 * upp
            var vxmin = cx - (w / 2.0) * upp - mWorld
            var vxmax = cx + (w / 2.0) * upp + mWorld
            var vymin = cy - (h / 2.0) * upp - mWorld
            var vymax = cy + (h / 2.0) * upp + mWorld

            // Zoom-band LOD (T2-B): at the settled zoom a segment shorter
            // than ~3/4 px contributes no legible ink, yet such segments
            // dominate dense sheets (hatch/text tessellation). Skipping
            // them cuts the stroke count where culling can't (fit-to-view
            // shows everything). Zooming in shrinks lodMin, so detail
            // returns automatically at the zoom where it becomes visible.
            var lodMin = 0.75 * upp

            // Stroke batching (T1): one path per colour run (chunked every
            // 4000 segments) instead of beginPath/stroke per segment. Round
            // caps render disjoint moveTo/lineTo subpaths identically to the
            // old per-segment strokes.
            var batchColor = "#0F172A"
            var batchN = 0

            for (var i = 0; i < root.primitives.length; ++i) {
                var prim = root.primitives[i]
                if (!prim) continue
                var props = prim.properties
                var color = (props && props.color) ? props.color : "#0F172A"
                var t = prim.type
                var g = prim.geometry
                if (!g) continue
                if (t === "lines") {
                    if (color !== batchColor) {
                        if (batchN > 0) { ctx.stroke(); batchN = 0 }
                        ctx.strokeStyle = color
                        batchColor = color
                    }
                    for (var k = 0; k < g.length; ++k) {
                        var seg = g[k]
                        if (!seg || seg.length < 4) continue
                        var ax = seg[0], ay = seg[1], bx = seg[2], by = seg[3]
                        if ((ax < vxmin && bx < vxmin) || (ax > vxmax && bx > vxmax)
                                || (ay < vymin && by < vymin) || (ay > vymax && by > vymax)) {
                            __culled += 1
                            continue
                        }
                        if (Math.abs(bx - ax) + Math.abs(by - ay) < lodMin) {
                            __culled += 1
                            continue
                        }
                        if (batchN === 0) ctx.beginPath()
                        ctx.moveTo(ax, ay)
                        ctx.lineTo(bx, by)
                        __drawn += 1
                        batchN += 1
                        if (batchN >= 4000) { ctx.stroke(); batchN = 0 }
                    }
                } else if (t === "path") {
                    if (batchN > 0) { ctx.stroke(); batchN = 0 }
                    // Quick bbox pre-pass over the command coordinates so an
                    // off-screen path costs one scan, not a full stroke.
                    var pxmin = Infinity, pxmax = -Infinity
                    var pymin = Infinity, pymax = -Infinity
                    for (var b = 0; b < g.length; ++b) {
                        var pc = g[b]
                        if (!pc) continue
                        for (var a = 1; a + 1 < pc.length; a += 2) {
                            var qx = pc[a], qy = pc[a + 1]
                            if (qx < pxmin) pxmin = qx
                            if (qx > pxmax) pxmax = qx
                            if (qy < pymin) pymin = qy
                            if (qy > pymax) pymax = qy
                        }
                    }
                    if (pxmax < vxmin || pxmin > vxmax || pymax < vymin || pymin > vymax) {
                        __culled += 1
                        continue
                    }
                    if ((pxmax - pxmin) + (pymax - pymin) < lodMin) {
                        __culled += 1
                        continue
                    }
                    ctx.strokeStyle = color
                    batchColor = color
                    ctx.beginPath()
                    for (var j = 0; j < g.length; ++j) {
                        var cmd = g[j]
                        if (!cmd || cmd.length < 1) continue
                        var op = cmd[0]
                        if (op === "M" && cmd.length >= 3) {
                            ctx.moveTo(cmd[1], cmd[2])
                        } else if (op === "L" && cmd.length >= 3) {
                            ctx.lineTo(cmd[1], cmd[2])
                        } else if (op === "C" && cmd.length >= 7) {
                            ctx.bezierCurveTo(cmd[1], cmd[2], cmd[3], cmd[4],
                                              cmd[5], cmd[6])
                        } else if (op === "Q" && cmd.length >= 5) {
                            ctx.quadraticCurveTo(cmd[1], cmd[2], cmd[3], cmd[4])
                        } else if (op === "Z") {
                            ctx.closePath()
                        }
                    }
                    ctx.stroke()
                    __drawn += 1
                }
            }
            if (batchN > 0) ctx.stroke()
            ctx.restore()
            root.paintCount += 1
            root.lastPaintMs = Date.now() - __t0
            root.lastPaintDrawnSegments = __drawn
            root.lastPaintCulledSegments = __culled
        }
    }

    // ---- CHANGE OVERLAY LAYER (cloud bboxes) ---------------------------
    Item {
        id: changeOverlayLayer
        anchors.fill: parent
        z: 100

        Repeater {
            model: root.overlaysCloud
            delegate: Item {
                id: cloudWrapper
                // modelData carries world bbox: { x, y, w, h, color, label, dimmed, zoneId }
                opacity: (modelData.dimmed === true ? 0.45 : 0.85) * root.overlayOpacityScale

                property color cloudColor: modelData.color || "#DC2626"
                property real cloudLineWidth: modelData.dimmed === true ? 1.2 : 2.0

                // Convert world bbox → pixel bbox using same affine as Canvas.
                // World→pixel scale (identical affine to the vector Canvas).
                property real _s: 1.0 / Math.max(0.0001, root.unitsPerPixel)
                // Natural pixel footprint of the change bbox at the current zoom.
                property real _natW: (modelData.w || 0) * _s
                property real _natH: (modelData.h || 0) * _s
                // Phase Q1 — clamp to a minimum perceptible footprint so small
                // zones don't vanish to sub-pixel at whole-drawing fit.
                property real _drawW: Math.max(root.minCloudPx, _natW)
                property real _drawH: Math.max(root.minCloudPx, _natH)
                // Screen position of the change CENTRE; the min-size growth
                // then expands symmetrically about it (current x/y must NOT
                // anchor to the world top-left or a clamped marker drifts off
                // the real change spot by up to minCloudPx/2 px).
                property real _cxScreen: root.width / 2 + ((modelData.x || 0) + (modelData.w || 0) / 2 - root.cameraCenterX) * _s
                property real _cyScreen: root.height / 2 - ((modelData.y || 0) + (modelData.h || 0) / 2 - root.cameraCenterY) * _s
                // Phase A — drawing extents + "oversized" test. Intrinsic to the
                // change (zoom-independent). Guarded so a degenerate worldBbox
                // ([0,0,1,1]) can't flag every zone oversized.
                property real _drawingW: Math.max(1, (root.worldBbox && root.worldBbox.length === 4 ? root.worldBbox[2] - root.worldBbox[0] : 0))
                property real _drawingH: Math.max(1, (root.worldBbox && root.worldBbox.length === 4 ? root.worldBbox[3] - root.worldBbox[1] : 0))
                property bool _oversized: _drawingW > 100 && _drawingH > 100
                    && ((modelData.w || 0) > root.largeCloudFraction * _drawingW
                        || (modelData.h || 0) > root.largeCloudFraction * _drawingH)

                x: _cxScreen - _drawW / 2
                y: _cyScreen - _drawH / 2
                width: _drawW
                height: _drawH

                // Revision-cloud border (scalloped perimeter) — replaces
                // the plain Rectangle border so the visual matches AEC
                // mark-up convention. Each side of the bbox gets a row
                // of outward-facing semicircular bumps; bump radius is
                // proportional to the shortest edge so small zones still
                // show >= 2 bumps per side.
                Canvas {
                    id: cloudBorderCanvas
                    anchors.fill: parent
                    antialiasing: true
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.reset()
                        ctx.strokeStyle = cloudWrapper.cloudColor
                        ctx.lineCap = "round"
                        ctx.lineJoin = "round"

                        var w = Math.max(2, width)
                        var h = Math.max(2, height)

                        // Phase A — oversized zone (e.g. a sheet-crossing leader
                        // line): draw only a faint dashed boundary so it recedes;
                        // the centroid pin (sibling) carries the location. No
                        // bold scallop, no inner ring.
                        if (cloudWrapper._oversized) {
                            var geom = modelData.geometry
                            if (geom && geom.length >= 2) {
                                // B안 — draw the entity's REAL shape (CAD-world mm
                                // → wrapper-local px) so the cloud follows the
                                // actual leader line, not its bbox. The wrapper
                                // spans the world bbox, so map each point relative
                                // to (modelData.x, top = modelData.y + h).
                                ctx.lineWidth = 3
                                ctx.globalAlpha = 0.95
                                ctx.strokeStyle = cloudWrapper.cloudColor
                                ctx.beginPath()
                                for (var gi = 0; gi < geom.length; gi++) {
                                    var lx = ((geom[gi][0] || 0) - (modelData.x || 0)) * cloudWrapper._s
                                    var ly = ((modelData.y || 0) + (modelData.h || 0) - (geom[gi][1] || 0)) * cloudWrapper._s
                                    if (gi === 0) ctx.moveTo(lx, ly)
                                    else ctx.lineTo(lx, ly)
                                }
                                ctx.stroke()
                                return
                            }
                            // Phase A fallback — faint dashed bbox boundary so it
                            // recedes; the centroid pin (sibling) carries location.
                            ctx.lineWidth = 1.5
                            ctx.globalAlpha = 0.5
                            ctx.setLineDash([8, 6])
                            ctx.strokeStyle = cloudWrapper.cloudColor
                            ctx.beginPath()
                            ctx.rect(0.75, 0.75, Math.max(1, w - 1.5), Math.max(1, h - 1.5))
                            ctx.stroke()
                            return
                        }

                        // G2.7-COORDFIX-2 — for very small markers (<32px on
                        // either axis) the scallops degenerate to sub-pixel
                        // arcs that look identical to a plain rectangle. In
                        // that regime, draw an extra-thick rounded rectangle
                        // PLUS a contrasting inner ring so reviewers can spot
                        // it on dense PDF backgrounds even at low zoom.
                        var minDim = Math.min(w, h)
                        if (minDim < 32) {
                            ctx.lineWidth = Math.max(3, cloudWrapper.cloudLineWidth + 2)
                            ctx.strokeStyle = cloudWrapper.cloudColor
                            ctx.beginPath()
                            ctx.rect(0, 0, w, h)
                            ctx.stroke()
                            ctx.lineWidth = 1.0
                            ctx.strokeStyle = "#FFFFFF"
                            ctx.beginPath()
                            ctx.rect(2, 2, Math.max(1, w - 4), Math.max(1, h - 4))
                            ctx.stroke()
                            return
                        }

                        // Normal path — scalloped revision-cloud border.
                        ctx.lineWidth = cloudWrapper.cloudLineWidth
                        // Bump radius: small enough for tiny zones, capped
                        // for huge ones so cloud doesn't look pillowy.
                        var bumpD = Math.max(8, Math.min(24, Math.min(w, h) / 4))
                        var topN = Math.max(2, Math.round(w / bumpD))
                        var sideN = Math.max(2, Math.round(h / bumpD))
                        var topR = w / (topN * 2)
                        var sideR = h / (sideN * 2)

                        ctx.beginPath()
                        // Top edge — bumps facing up (outward)
                        for (var i = 0; i < topN; i++) {
                            var cx = topR * (2 * i + 1)
                            ctx.moveTo(cx - topR, 0)
                            ctx.arc(cx, 0, topR, Math.PI, 0, false)
                        }
                        // Right edge — bumps facing right
                        for (var j = 0; j < sideN; j++) {
                            var cy = sideR * (2 * j + 1)
                            ctx.moveTo(w, cy - sideR)
                            ctx.arc(w, cy, sideR, -Math.PI / 2, Math.PI / 2, false)
                        }
                        // Bottom edge — bumps facing down
                        for (var k = topN - 1; k >= 0; k--) {
                            var cx2 = topR * (2 * k + 1)
                            ctx.moveTo(cx2 + topR, h)
                            ctx.arc(cx2, h, topR, 0, Math.PI, false)
                        }
                        // Left edge — bumps facing left
                        for (var l = sideN - 1; l >= 0; l--) {
                            var cy2 = sideR * (2 * l + 1)
                            ctx.moveTo(0, cy2 + sideR)
                            ctx.arc(0, cy2, sideR, Math.PI / 2, -Math.PI / 2, false)
                        }
                        ctx.stroke()
                    }
                    Connections {
                        target: cloudWrapper
                        function onWidthChanged()        { cloudBorderCanvas.requestPaint() }
                        function onHeightChanged()       { cloudBorderCanvas.requestPaint() }
                        function onCloudColorChanged()   { cloudBorderCanvas.requestPaint() }
                        function onCloudLineWidthChanged(){ cloudBorderCanvas.requestPaint() }
                    }
                }

                // Phase A — centroid pin for oversized zones. Screen-fixed
                // small marker at the bbox centre so a sheet-crossing change
                // stays locatable without a blanketing cloud. Shown only when
                // the zone is oversized; normal zones use the scallop above.
                Rectangle {
                    visible: cloudWrapper._oversized
                    width: 14
                    height: 14
                    radius: 7
                    color: cloudWrapper.cloudColor
                    border.color: "#FFFFFF"
                    border.width: 2
                    x: (cloudWrapper.width - width) / 2
                    y: (cloudWrapper.height - height) / 2
                }

                // Optional area label rendered above the bbox.
                Rectangle {
                    visible: !!(modelData.label)
                    x: 0
                    y: -22
                    width: Math.max(48, areaLabel.implicitWidth + 10)
                    height: 20
                    color: "#F9FAFB"
                    border.color: cloudWrapper.cloudColor
                    border.width: 1
                    opacity: 0.92
                }
                Text {
                    id: areaLabel
                    visible: !!(modelData.label)
                    x: 5
                    y: -21
                    text: modelData.label || ""
                    color: "#111827"
                    font.pixelSize: 12
                    font.bold: false
                }

                // Phase I4 — overlay click selects the zone in the list.
                // changeOverlayLayer is at z:100 and pan MouseArea at z:50,
                // so this MouseArea wins event delivery for clicks landing
                // on a cloud rectangle. Wheel events fall through to the
                // pan MouseArea so zoom-while-hovering still works.
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.PointingHandCursor
                    onClicked: function(mouse) {
                        var zid = modelData && modelData.zoneId
                            ? String(modelData.zoneId) : ""
                        if (zid !== "") root.overlayClicked(zid)
                    }
                    onWheel: function(wheel) { wheel.accepted = false }
                }
            }
        }
    }

    // ---- FOCUS MARKER LAYER --------------------------------------------
    Item {
        id: focusMarkerLayer
        anchors.fill: parent
        z: 101

        Repeater {
            model: root.overlaysFocus
            delegate: Item {
                anchors.fill: parent
                Rectangle {
                    color: "transparent"
                    border.color: modelData.color || "#0969DA"
                    border.width: 4
                    radius: 2
                    opacity: 1.0 * root.overlayOpacityScale
                    // Phase Q1 — same minimum-footprint + centred expansion as
                    // the cloud layer so the selected zone stays visible even
                    // before the camera zooms to it (whole-drawing fit).
                    width: Math.max(root.minCloudPx, (modelData.w || 0) / Math.max(0.0001, root.unitsPerPixel))
                    height: Math.max(root.minCloudPx, (modelData.h || 0) / Math.max(0.0001, root.unitsPerPixel))
                    x: {
                        var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                        var cx = root.width / 2 + ((modelData.x || 0) + (modelData.w || 0) / 2 - root.cameraCenterX) * s
                        return cx - width / 2
                    }
                    y: {
                        var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                        var cy = root.height / 2 - ((modelData.y || 0) + (modelData.h || 0) / 2 - root.cameraCenterY) * s
                        return cy - height / 2
                    }
                }
            }
        }
    }

    // ---- WATERMARK + BADGE (state layer) -------------------------------
    Text {
        anchors.centerIn: parent
        text: "상대 위치 모드 — 실배경 아님"
        color: "#F97316"
        font.pixelSize: 36
        font.bold: true
        opacity: 0.18
        rotation: -22
        visible: root.showWatermark
        z: 9000
    }

    Rectangle {
        id: badge
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        width: badgeRow.width + 18
        height: badgeRow.height + 10
        radius: 4
        z: 9001
        color: root.renderModeBadgeColor
        opacity: 0.92
        border.color: "#FFFFFF"
        border.width: 1

        Row {
            id: badgeRow
            anchors.centerIn: parent
            spacing: 6
            Text {
                color: "#FFFFFF"
                font.pixelSize: 11
                font.bold: true
                text: root.renderModeLabel
            }
            Text {
                color: "#FFFFFF"
                font.pixelSize: 11
                font.italic: true
                visible: root.statusText !== ""
                text: "· " + root.statusText
            }
        }
    }

    // ---- SIDE MESSAGE --------------------------------------------------
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 46
        width: Math.min(parent.width - 48, sideMessageText.implicitWidth + 34)
        height: sideMessageText.implicitHeight + 18
        radius: 6
        color: "#111827"
        opacity: 0.88
        visible: root.sideMessage !== ""
        z: 8999

        Text {
            id: sideMessageText
            anchors.fill: parent
            anchors.margins: 9
            text: root.sideMessage
            color: "#FFFFFF"
            font.pixelSize: 13
            font.bold: true
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // ---- EMPTY STATE NOTICE --------------------------------------------
    Rectangle {
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 540)
        height: emptyNoticeText.implicitHeight + 32
        color: "#FFFFFF"
        border.color: "#9CA3AF"
        border.width: 1
        radius: 8
        opacity: 0.96
        visible: (!root.primitives || root.primitives.length === 0) && root.emptyNotice !== ""
        z: 8000

        Text {
            id: emptyNoticeText
            anchors.fill: parent
            anchors.margins: 16
            text: root.emptyNotice
            color: "#111827"
            font.pixelSize: 14
            font.bold: true
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // ---- INTERACTION (pan + wheel zoom) --------------------------------
    MouseArea {
        anchors.fill: parent
        property real lastX: 0
        property real lastY: 0
        z: 50

        onPressed: function(mouse) {
            lastX = mouse.x
            lastY = mouse.y
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            var dx = mouse.x - lastX
            var dy = mouse.y - lastY
            lastX = mouse.x
            lastY = mouse.y
            // Convert pixel delta → world delta. Y axis flipped.
            root.cameraCenterX -= dx * root.unitsPerPixel
            root.cameraCenterY += dy * root.unitsPerPixel
            root.viewportChanged(root.cameraCenterX, root.cameraCenterY,
                                 root.unitsPerPixel)
        }
        onWheel: function(wheel) {
            // Zoom around the cursor: convert cursor pixel → world point
            // BEFORE zoom, then re-position camera so that world point
            // lands at the same cursor pixel AFTER zoom.
            var oldUpp = root.unitsPerPixel
            var factor = wheel.angleDelta.y > 0 ? (1.0 / 1.15) : 1.15
            var newUpp = Math.max(0.0001, oldUpp * factor)
            // World coords under cursor before zoom:
            var s_old = 1.0 / Math.max(0.0001, oldUpp)
            var worldX = root.cameraCenterX + (wheel.x - root.width / 2) / s_old
            var worldY = root.cameraCenterY - (wheel.y - root.height / 2) / s_old
            // After zoom we want the same (worldX, worldY) at (wheel.x, wheel.y).
            var s_new = 1.0 / Math.max(0.0001, newUpp)
            root.cameraCenterX = worldX - (wheel.x - root.width / 2) / s_new
            root.cameraCenterY = worldY + (wheel.y - root.height / 2) / s_new
            root.unitsPerPixel = newUpp
            root.viewportChanged(root.cameraCenterX, root.cameraCenterY,
                                 root.unitsPerPixel)
            wheel.accepted = true
        }
    }

    // ---- API HOOKS -----------------------------------------------------
    //
    // Phase G2.7-FOCUSFIX — camera preservation across resize / same-bbox
    // re-render.
    //
    // BEFORE the fix the QML did:
    //
    //     onWidthChanged: { if (width > 0) fitToView() }
    //     onHeightChanged: { if (height > 0) fitToView() }
    //     onWorldBboxChanged: fitToView()
    //
    // That meant ANY size change (splitter drag, sibling widget growing,
    // detail-panel HTML reflow, etc.) re-fit the camera to the entire
    // page — wiping out the user's zone-focus zoom. Same problem when
    // Python re-issued ``load_pdf_page`` at a higher DPI for the same
    // page: ``worldBbox`` was set with the same value, but Qt re-fired
    // ``onWorldBboxChanged`` (var-property reference comparison), so the
    // camera reset there too.
    //
    // The fix:
    //
    //   * ``cameraInitialized`` flag — fitToView only runs on the FIRST
    //     valid (worldBbox, size) combo.
    //   * ``_lastFitBbox`` cache — onWorldBboxChanged only re-fits when
    //     the bbox VALUE actually differs (a different drawing / page).
    //   * Resize doesn't fit — it just re-paints. The world point at the
    //     viewport centre stays put; the visible area expands or shrinks.
    //     This matches every other CAD/PDF viewer.
    property bool cameraInitialized: false
    property var _lastFitBbox: [0, 0, 0, 0]

    function _bboxesEqual(a, b) {
        if (!a || !b) return false
        if (a.length !== b.length) return false
        for (var i = 0; i < a.length; ++i) {
            if (Math.abs(Number(a[i]) - Number(b[i])) > 0.001) return false
        }
        return true
    }

    function fitToView() {
        var bb = root.worldBbox
        if (!bb || bb.length < 4) return
        var ww = Math.max(1.0, bb[2] - bb[0])
        var wh = Math.max(1.0, bb[3] - bb[1])
        var availW = root.width
        var availH = root.height
        if (availW <= 0 || availH <= 0) return
        // units-per-pixel = max world dimension per available pixel
        root.unitsPerPixel = Math.max(ww / availW, wh / availH) * 1.05  // 5% margin
        root.cameraCenterX = (bb[0] + bb[2]) / 2.0
        root.cameraCenterY = (bb[1] + bb[3]) / 2.0
        root.viewportChanged(root.cameraCenterX, root.cameraCenterY,
                             root.unitsPerPixel)
        root.cameraInitialized = true
        root._lastFitBbox = [bb[0], bb[1], bb[2], bb[3]]
    }

    onWorldBboxChanged: {
        // Re-fit only when the bbox VALUE actually changed (different
        // drawing / page). Same-value re-set (Python pushed worldBbox
        // again at the same value, e.g. higher-DPI PDF re-render of the
        // same page) preserves the camera so the user's zoom stays put.
        if (!_bboxesEqual(root.worldBbox, root._lastFitBbox)) {
            root.cameraInitialized = false  // new content → allow initial fit
            if (root.width > 0 && root.height > 0) fitToView()
        }
    }

    onWidthChanged: {
        // Initial layout: width transitions 0 → N. Fit-to-view ONCE so the
        // page is visible. Subsequent resizes preserve the camera.
        if (width > 0 && !cameraInitialized) {
            fitToView()
        }
    }

    onHeightChanged: {
        if (height > 0 && !cameraInitialized) {
            fitToView()
        }
    }
}
