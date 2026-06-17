import QtQuick

Item {
    id: root
    property string imageSource: ""
    // Phase G2.7-FOCUSFIX — track the last image source we ran an
    // auto-fitToView for. Each NEW image source gets one initial fit;
    // subsequent re-loads (e.g. higher-DPI PDF re-render of the same
    // page) preserve whatever camera the user / Python set explicitly.
    property string _lastFitImageSource: ""
    property var visibleTiles: []
    property bool useTiles: false
    property real sceneWidth: 800
    property real sceneHeight: 500
    property bool hasBackground: false
    property string emptyNotice: ""
    property var overlays: []
    property var overlaysCloud: []
    property var overlaysFocus: []
    property string viewportSide: ""
    // User-controlled opacity scale (0.3-1.0) applied on top of per-entry
    // baseline opacity. Lets the reviewer see the underlying drawing through
    // the overlay markers without losing them entirely.
    property real overlayOpacityScale: 1.0
    property string selectedZoneId: ""
    property string statusText: ""
    property real zoom: 1.0
    property real panX: 0.0
    property real panY: 0.0
    property string focusZoneId: ""
    property real focusPaddingRatio: 0.25
    property int fitRequest: 0
    // Phase B1.5 — inline SVG vector overlay for the active zone. The SVG is
    // produced by zone_vector_renderer (full fidelity, including INSERT/MTEXT
    // /HATCH/DIMENSION which the fast PNG renderer skips) and overlaid on top
    // of the background PNG at the zone's pixel-coord rectangle. QML's
    // built-in Image element renders SVG natively; the high sourceSize keeps
    // the rasterization sharp through deep zoom.
    property string vectorSvgPath: ""
    property real vectorSvgX: 0
    property real vectorSvgY: 0
    property real vectorSvgW: 0
    property real vectorSvgH: 0
    // Capped raster grid for the SVG overlay (computed Python-side by
    // capped_svg_source_size) so it stays under Qt's 256 MB QImageIOHandler
    // limit and always decodes instead of being silently rejected.
    property real vectorSvgSourceW: 2048
    property real vectorSvgSourceH: 2048
    property real vectorSvgOpacity: 1.0
    // Phase F P0 — explicit fidelity & job status (driven by viewer_manifest_v2).
    // Background fidelity drives the colour of the small status badge in the top
    // right corner, and forces a "상대 위치 모드" watermark + measurement-tool
    // disable signal when set to ``relative_only`` (the legacy implicit state
    // where overlays were drawn without a real exact background).
    //   exact_world_render       → 🟢 "실배경 정확"
    //   exact_world_tile_sparse  → 🔵 "부분 실배경"
    //   simplified_world_preview → ⚪ "단순화 미리보기"
    //   relative_only            → 🟠 "상대 위치 모드" + watermark
    property string backgroundFidelity: "relative_only"
    //   idle | queued | rendering | timed_out | failed
    property string renderJobStatus: "idle"
    signal viewportChanged(real zoom, real panX, real panY)
    // Phase I4 — emitted when the user clicks a cloud or focus marker.
    // Wired by GpuDrawingViewport (Python) → workbench
    // _select_zone_in_list_v2 so the list auto-selects the clicked zone.
    signal overlayClicked(string zoneId)

    clip: true

    Rectangle {
        anchors.fill: parent
        color: "#F7F8FA"
        border.color: "#9CA3AF"
    }

    Item {
        id: content
        x: root.panX
        y: root.panY
        scale: root.zoom
        transformOrigin: Item.TopLeft
        // Phase I4 — content sits above the root pan MouseArea so per-
        // overlay MouseAreas inside this subtree win event delivery.
        // Pan still works in empty areas because no inner MouseArea
        // catches the click there → root pan area gets it as fallback.
        z: 100

        Image {
            id: background
            // Always render the background PNG when available; tiles draw on top
            // as a higher-resolution overlay when LOD kicks in. Without this the
            // viewport stayed blank whenever ``useTiles`` was true but the tile
            // grid was sparse for the current viewport rect.
            // Explicit width/height (driven by Python via sceneWidth/sceneHeight)
            // gives the Image a non-zero footprint immediately, so fitToView and
            // overlay positioning work even before the async PNG decode finishes.
            source: root.imageSource
            visible: root.imageSource !== ""
            x: 0
            y: 0
            width: root.sceneWidth > 0 ? root.sceneWidth : implicitWidth
            height: root.sceneHeight > 0 ? root.sceneHeight : implicitHeight
            cache: true
            asynchronous: false
            fillMode: Image.PreserveAspectFit
            onStatusChanged: {
                // Phase G2.7-FOCUSFIX — auto-fit only on the FIRST load
                // of a NEW image source. Without this guard, every async
                // image-load completion (including the high-DPI re-render
                // that follows zone-focus) re-fitted the camera and wiped
                // out the user's zone-focus zoom — exactly the
                // "변경부 확대가 안 되고 전체 뷰로 돌아가는" symptom.
                //
                // Python still drives explicit fits via ``fitRequest`` (see
                // _refresh_quick_model in drawing_compare_workbench.py),
                // and per-zone focus via ``focusZoneId``, so the explicit
                // path is unaffected.
                if (status === Image.Ready) {
                    if (root._lastFitImageSource !== root.imageSource) {
                        root._lastFitImageSource = root.imageSource
                        root.fitToView()
                    }
                }
            }
        }

        Repeater {
            model: root.visibleTiles
            Image {
                required property var modelData
                source: modelData.source || ""
                x: Number(modelData.x || 0)
                y: Number(modelData.y || 0)
                width: Math.max(1, Number(modelData.width || 1))
                height: Math.max(1, Number(modelData.height || 1))
                cache: true
                asynchronous: true
                fillMode: Image.Stretch
                smooth: root.zoom < 1.0
            }
        }

        // Phase B1.5 — inline SVG vector overlay for the active zone.
        // Sits between the PNG/tile layers and the cloud/focus markers so the
        // change cloud still draws on top, but the underlying drawing detail
        // (text, dimensions, INSERT block content) is now vector-quality and
        // sharpens further as the user zooms in. The high sourceSize lets Qt
        // re-rasterize at higher resolution when zoomed past 1.0 without
        // triggering another network/disk fetch.
        Image {
            id: vectorOverlay
            visible: root.vectorSvgPath !== "" && root.vectorSvgW > 0 && root.vectorSvgH > 0
            source: root.vectorSvgPath !== "" ? "file:///" + root.vectorSvgPath : ""
            x: root.vectorSvgX
            y: root.vectorSvgY
            width: Math.max(1, root.vectorSvgW)
            height: Math.max(1, root.vectorSvgH)
            fillMode: Image.PreserveAspectFit
            smooth: true
            antialiasing: true
            cache: true
            asynchronous: true
            opacity: root.vectorSvgOpacity
            // sourceSize controls the raster grid Qt allocates for the SVG.
            // Computed Python-side (capped_svg_source_size): ~4x the displayed
            // size for zoom sharpness, but scaled down to stay under Qt's 256 MB
            // QImageIOHandler limit so the overlay always decodes. Previously a
            // raw ``displayed * 4`` grid silently exceeded the limit on large
            // zones and the change overlay vanished (live-test 2026-06-17).
            sourceSize.width: Math.max(1, Math.round(root.vectorSvgSourceW))
            sourceSize.height: Math.max(1, Math.round(root.vectorSvgSourceH))
        }

        Repeater {
            model: root.overlaysCloud.length > 0 ? root.overlaysCloud : root.overlays
            Item {
                id: cloudWrapper
                required property var modelData
                x: Number(modelData.x || 0)
                y: Number(modelData.y || 0)
                width: Math.max(1, Number(modelData.width || 1))
                height: Math.max(1, Number(modelData.height || 1))
                opacity: (modelData.dimmed === true ? 0.45 : 0.85) * root.overlayOpacityScale

                property color cloudColor: root.cloudBorderColor(modelData)
                property real cloudLineWidth: modelData.dimmed === true ? 1.2 : 2.0

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
                            // Outer halo — slightly larger semi-transparent
                            // rectangle that gives the marker a glow effect
                            ctx.lineWidth = Math.max(3, cloudWrapper.cloudLineWidth + 2)
                            ctx.strokeStyle = cloudWrapper.cloudColor
                            ctx.beginPath()
                            ctx.rect(0, 0, w, h)
                            ctx.stroke()
                            // Inner contrast ring (white) for visibility on
                            // dark backgrounds
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

                // Phase I4 — overlay click selects the zone in the list.
                // Use onClicked (press+release without drag) so users can
                // still wheel-zoom over an overlay without selecting it.
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.PointingHandCursor
                    onClicked: function(mouse) {
                        var zid = modelData && modelData.zoneId
                            ? String(modelData.zoneId) : ""
                        if (zid !== "") root.overlayClicked(zid)
                    }
                    // Let wheel events fall through to the root pan/zoom
                    // MouseArea so the user can still zoom while hovering
                    // over a cloud overlay.
                    onWheel: function(wheel) { wheel.accepted = false }
                }

                Rectangle {
                    visible: !!(modelData.label)
                    x: 0
                    y: -22
                    width: Math.max(48, areaLabel.implicitWidth + 10)
                    height: 20
                    color: "#F9FAFB"
                    border.color: cloudWrapper.cloudColor
                    border.width: 1
                    opacity: 0.92 * root.overlayOpacityScale
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
            }
        }

        Repeater {
            model: root.overlaysFocus
            Item {
                required property var modelData
                x: 0
                y: 0
                width: 0
                height: 0
                // Container opacity propagates to all child Rectangles/labels
                // so the user-controlled scale also dims the focus marker stack
                // (selection box, crosshair, pin, label) uniformly.
                opacity: root.overlayOpacityScale

                Rectangle {
                    visible: modelData.pinOnly !== true
                    x: Number(modelData.x || 0)
                    y: Number(modelData.y || 0)
                    width: Math.max(1, Number(modelData.width || 1))
                    height: Math.max(1, Number(modelData.height || 1))
                    color: "#33005FCC"
                    border.color: root.colorFor(modelData.changeType)
                    border.width: 5
                    radius: 2

                    Rectangle {
                        x: parent.width / 2 - 1.5
                        y: -16
                        width: 3
                        height: parent.height + 32
                        color: "#005FCC"
                        opacity: 0.9
                    }

                    Rectangle {
                        x: -16
                        y: parent.height / 2 - 1.5
                        width: parent.width + 32
                        height: 3
                        color: "#005FCC"
                        opacity: 0.9
                    }
                }

                Rectangle {
                    width: 16
                    height: 16
                    radius: 8
                    color: root.colorFor(modelData.changeType)
                    border.color: "#FFFFFF"
                    border.width: 2
                    x: Number(modelData.pinX || (Number(modelData.x || 0) + Number(modelData.width || 0) / 2)) - 8
                    y: Number(modelData.pinY || (Number(modelData.y || 0) + Number(modelData.height || 0) / 2)) - 8
                }

                Rectangle {
                    visible: !!(modelData.label)
                    x: Number(modelData.x || 0)
                    y: Number(modelData.y || 0) - 22
                    width: Math.max(56, focusLabel.implicitWidth + 12)
                    height: 20
                    color: "#005FCC"
                    border.color: "#FFFFFF"
                    border.width: 1
                    opacity: 0.96
                }

                Text {
                    id: focusLabel
                    visible: !!(modelData.label)
                    x: Number(modelData.x || 0) + 6
                    y: Number(modelData.y || 0) - 21
                    text: modelData.label || ""
                    color: "#FFFFFF"
                    font.pixelSize: 12
                    font.bold: true
                }
            }
        }
    }

    Rectangle {
        id: statusBadge
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: 12
        width: statusLabel.implicitWidth + 16
        height: statusLabel.implicitHeight + 12
        color: "#EAF2FF"
        border.color: "#8DBBFF"
        radius: 8

        Text {
            id: statusLabel
            anchors.centerIn: parent
            text: root.statusText
            color: "#111827"
            font.pixelSize: 13
            font.bold: true
        }
    }

    Rectangle {
        id: emptyNoticePanel
        visible: !root.hasBackground && root.emptyNotice !== ""
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 560)
        height: noticeText.implicitHeight + 32
        color: "#FFFFFF"
        border.color: "#9CA3AF"
        border.width: 1
        radius: 8
        opacity: 0.96

        Text {
            id: noticeText
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

    // ----------------------------------------------------------------------
    // Phase F P0 — Background fidelity overlay layer (badges + watermark).
    // Sits OUTSIDE the zoomable ``content`` Item so it never scales with pan/
    // zoom. Two visible elements:
    //   1. A diagonal "상대 위치 모드" watermark, only when the underlying
    //      fidelity is ``relative_only`` — the goal is to make it impossible
    //      for the user to mistake a normalized overlay for a real background.
    //   2. A small coloured badge in the top-right corner showing both the
    //      fidelity tier (colour) and the current render job status (text).
    // ----------------------------------------------------------------------

    Text {
        id: relativeOnlyWatermark
        anchors.centerIn: parent
        text: "상대 위치 모드 — 실배경 아님"
        color: "#F97316"
        font.pixelSize: 36
        font.bold: true
        opacity: 0.18
        rotation: -22
        visible: root.backgroundFidelity === "relative_only"
        z: 9000
    }

    Rectangle {
        id: fidelityBadge
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        width: badgeRow.width + 18
        height: badgeRow.height + 10
        radius: 4
        z: 9001
        color: {
            switch (root.backgroundFidelity) {
            case "exact_world_render": return "#1f9d55"        // green
            case "exact_world_tile_sparse": return "#0969da"   // blue
            case "simplified_world_preview": return "#6b7280"  // gray
            case "relative_only": return "#F97316"             // orange
            default: return "#6b7280"
            }
        }
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
                text: {
                    switch (root.backgroundFidelity) {
                    case "exact_world_render": return "🟢 실배경 정확"
                    case "exact_world_tile_sparse": return "🔵 부분 실배경"
                    case "simplified_world_preview": return "⚪ 단순화 미리보기"
                    case "relative_only": return "🟠 상대 위치 모드"
                    default: return "fidelity?"
                    }
                }
            }

            Text {
                color: "#FFFFFF"
                font.pixelSize: 11
                font.italic: true
                visible: root.renderJobStatus !== "" && root.renderJobStatus !== "idle"
                text: {
                    switch (root.renderJobStatus) {
                    case "queued": return "· 대기"
                    case "rendering": return "· 렌더 중"
                    case "timed_out": return "· 시간 초과"
                    case "failed": return "· 실패"
                    default: return "· " + root.renderJobStatus
                    }
                }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        property real lastX: 0
        property real lastY: 0
        onPressed: function(mouse) {
            lastX = mouse.x
            lastY = mouse.y
        }
        onPositionChanged: function(mouse) {
            if (!pressed) {
                return
            }
            root.panX += mouse.x - lastX
            root.panY += mouse.y - lastY
            lastX = mouse.x
            lastY = mouse.y
            root.viewportChanged(root.zoom, root.panX, root.panY)
        }
        onWheel: function(wheel) {
            var factor = wheel.angleDelta.y > 0 ? 1.15 : 1 / 1.15
            var oldZoom = root.zoom
            var nextZoom = Math.max(0.05, Math.min(20.0, root.zoom * factor))
            var worldX = (wheel.x - root.panX) / oldZoom
            var worldY = (wheel.y - root.panY) / oldZoom
            root.zoom = nextZoom
            root.panX = wheel.x - worldX * nextZoom
            root.panY = wheel.y - worldY * nextZoom
            root.viewportChanged(root.zoom, root.panX, root.panY)
            wheel.accepted = true
        }
    }

    onFocusZoneIdChanged: {
        if (focusZoneId !== "") {
            focusZone(focusZoneId, focusPaddingRatio)
        }
    }

    onFitRequestChanged: fitToView()

    function fitToView() {
        var iw = root.sceneWidth > 0 ? root.sceneWidth : (background.implicitWidth > 0 ? background.implicitWidth : 800)
        var ih = root.sceneHeight > 0 ? root.sceneHeight : (background.implicitHeight > 0 ? background.implicitHeight : 500)
        if (root.width <= 0 || root.height <= 0) {
            return
        }
        root.zoom = Math.max(0.05, Math.min(root.width / iw, root.height / ih))
        root.panX = (root.width - iw * root.zoom) / 2
        root.panY = (root.height - ih * root.zoom) / 2
        root.viewportChanged(root.zoom, root.panX, root.panY)
    }

    function focusZone(zoneId, paddingRatio) {
        for (var i = 0; i < overlays.length; ++i) {
            var item = overlays[i]
            if (String(item.zoneId) === String(zoneId)) {
                var pad = Math.max(40, Math.max(Number(item.width), Number(item.height)) * paddingRatio)
                var targetW = Math.max(1, Number(item.width) + pad * 2)
                var targetH = Math.max(1, Number(item.height) + pad * 2)
                var nextZoom = Math.max(0.05, Math.min(20.0, Math.min(root.width / targetW, root.height / targetH)))
                root.zoom = nextZoom
                root.panX = root.width / 2 - (Number(item.x) + Number(item.width) / 2) * nextZoom
                root.panY = root.height / 2 - (Number(item.y) + Number(item.height) / 2) * nextZoom
                root.viewportChanged(root.zoom, root.panX, root.panY)
                return
            }
        }
    }

    // Phase P (RV-20260508-014) — AIA 표준 색상 (revision_marker SSoT
    // 와 일치). modified=cyan, added=green, deleted=magenta. AEC 도면
    // 표준 표기와 동일하여 사용자가 추가 학습 없이 인식.
    function colorFor(changeType) {
        if (changeType === "added") return "#00CC00"     // ACI green
        if (changeType === "deleted") return "#FF00FF"   // ACI magenta
        if (changeType === "modified") return "#00C8DC"  // AIA cyan
        if (changeType === "moved") return "#00C8DC"     // cyan (위치 변경 = modified 류)
        return "#8250df"
    }

    function matchSideColorFor(side) {
        if (side === "a_only") return "#FF00FF"   // magenta = deleted (A 만 존재)
        if (side === "b_only") return "#00CC00"   // green = added (B 만 존재)
        if (side === "matched") return "#00C8DC"  // cyan = matched/modified
        if (side === "mixed") return "#8250df"
        return "#6b7280"
    }

    function cloudBorderColor(modelData) {
        if (!modelData) return "#9CA3AF"
        var side = String(modelData.matchSide || "")
        if (root.viewportSide === "before" && side === "b_only") return "#9CA3AF"
        if (root.viewportSide === "after" && side === "a_only") return "#9CA3AF"
        if (side) return matchSideColorFor(side)
        return colorFor(modelData.changeType)
    }
}
