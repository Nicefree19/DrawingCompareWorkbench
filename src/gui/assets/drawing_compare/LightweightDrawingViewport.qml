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
    // below be the guaranteed renderer.
    Item {
        id: qsgContainer
        anchors.fill: parent
        visible: false
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
        // Repaint whenever any input the paint code reads changes — wired
        // via Connections on the root item so we catch every property
        // assignment from Python regardless of binding cycles.

        Connections {
            target: root
            function onPrimitivesChanged() { vectorCanvas.requestPaint() }
            function onWorldBboxChanged()  { vectorCanvas.requestPaint() }
            function onCameraCenterXChanged() { vectorCanvas.requestPaint() }
            function onCameraCenterYChanged() { vectorCanvas.requestPaint() }
            function onUnitsPerPixelChanged() { vectorCanvas.requestPaint() }
        }

        onPaint: {
            var ctx = getContext("2d")
            ctx.save()
            ctx.clearRect(0, 0, width, height)

            if (!root.primitives || root.primitives.length === 0) {
                ctx.restore()
                return
            }

            // World→pixel affine. We render around (cameraCenterX, cameraCenterY)
            // with ``unitsPerPixel`` world units per screen pixel. Y axis flipped.
            var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
            var cx = root.cameraCenterX
            var cy = root.cameraCenterY
            var w  = width
            var h  = height
            // Translate so (cx, cy) maps to (w/2, h/2), with Y flipped.
            ctx.translate(w / 2.0, h / 2.0)
            ctx.scale(s, -s)
            ctx.translate(-cx, -cy)

            // Pen — thin black line scaled to look ~1 px regardless of zoom.
            ctx.lineWidth = root.unitsPerPixel
            ctx.strokeStyle = "#0F172A"
            ctx.lineCap = "round"
            ctx.lineJoin = "round"

            for (var i = 0; i < root.primitives.length; ++i) {
                var prim = root.primitives[i]
                if (!prim) continue
                var props = prim.properties
                if (props && props.color) {
                    ctx.strokeStyle = props.color
                }
                var t = prim.type
                var g = prim.geometry
                if (!g) continue
                if (t === "lines") {
                    for (var k = 0; k < g.length; ++k) {
                        var seg = g[k]
                        if (!seg || seg.length < 4) continue
                        ctx.beginPath()
                        ctx.moveTo(seg[0], seg[1])
                        ctx.lineTo(seg[2], seg[3])
                        ctx.stroke()
                    }
                } else if (t === "path") {
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
                }
            }
            ctx.restore()
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
                x: {
                    var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                    return root.width / 2 + (modelData.x - root.cameraCenterX) * s
                }
                y: {
                    var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                    return root.height / 2 - ((modelData.y + (modelData.h || 0)) - root.cameraCenterY) * s
                }
                width: {
                    var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                    return Math.max(2, (modelData.w || 0) * s)
                }
                height: {
                    var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                    return Math.max(2, (modelData.h || 0) * s)
                }

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
                    x: {
                        var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                        return root.width / 2 + (modelData.x - root.cameraCenterX) * s
                    }
                    y: {
                        var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                        return root.height / 2 - ((modelData.y + (modelData.h || 0)) - root.cameraCenterY) * s
                    }
                    width: {
                        var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                        return Math.max(8, (modelData.w || 0) * s)
                    }
                    height: {
                        var s = 1.0 / Math.max(0.0001, root.unitsPerPixel)
                        return Math.max(8, (modelData.h || 0) * s)
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
