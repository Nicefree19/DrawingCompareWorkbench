from __future__ import annotations


class _FakeProcess:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, payload) -> None:  # noqa: ANN001
        self.writes.append(bytes(payload))

    def waitForBytesWritten(self, _timeout_ms: int) -> bool:  # noqa: N802
        return True

    def state(self):  # noqa: ANN201
        from PySide6.QtCore import QProcess

        return QProcess.NotRunning

    def deleteLater(self) -> None:  # noqa: N802
        return None


class _FakeTimer:
    def __init__(self) -> None:
        self.starts: list[int] = []

    def start(self, timeout_ms: int) -> None:
        self.starts.append(int(timeout_ms))

    def stop(self) -> None:
        return None


def test_zone_render_controller_uses_longer_timeout_for_source_upgrade(qapp) -> None:
    from src.gui.drawing_compare_workbench import ZoneRenderProcessController

    controller = ZoneRenderProcessController(timeout_ms=10_000, source_timeout_ms=30_000)
    try:
        fake_process = _FakeProcess()
        fake_timer = _FakeTimer()
        controller._process = fake_process  # type: ignore[assignment]
        controller._timeout_timer = fake_timer  # type: ignore[assignment]
        controller._process_ready = True
        controller._ensure_process = lambda _process_key: True  # type: ignore[method-assign]

        assert controller.render(
            process_key="env",
            request={
                "request_id": "r1",
                "pair_uuid": "pair",
                "zone_id": "z1",
                "prefer_source_render": True,
            },
            viewer_pair={},
            overlay={},
            overlays=[],
        )

        assert fake_timer.starts == [30_000]
        assert controller._active_context["timeout_ms"] == 30_000
    finally:
        controller.shutdown()


def test_zone_render_controller_keeps_fast_timeout_for_fast_crop(qapp) -> None:
    from src.gui.drawing_compare_workbench import ZoneRenderProcessController

    controller = ZoneRenderProcessController(timeout_ms=10_000, source_timeout_ms=30_000)
    try:
        fake_process = _FakeProcess()
        fake_timer = _FakeTimer()
        controller._process = fake_process  # type: ignore[assignment]
        controller._timeout_timer = fake_timer  # type: ignore[assignment]
        controller._process_ready = True
        controller._ensure_process = lambda _process_key: True  # type: ignore[method-assign]

        assert controller.render(
            process_key="env",
            request={
                "request_id": "r1",
                "pair_uuid": "pair",
                "zone_id": "z1",
                "prefer_source_render": False,
            },
            viewer_pair={},
            overlay={},
            overlays=[],
        )

        assert fake_timer.starts == [10_000]
        assert controller._active_context["timeout_ms"] == 10_000
    finally:
        controller.shutdown()


def test_zone_render_controller_preserves_source_timeout_after_ready_event(qapp) -> None:
    from src.gui.drawing_compare_workbench import ZoneRenderProcessController

    controller = ZoneRenderProcessController(timeout_ms=10_000, source_timeout_ms=45_000)
    try:
        fake_process = _FakeProcess()
        fake_timer = _FakeTimer()
        controller._process = fake_process  # type: ignore[assignment]
        controller._timeout_timer = fake_timer  # type: ignore[assignment]
        controller._process_ready = False
        controller._ensure_process = lambda _process_key: True  # type: ignore[method-assign]

        assert controller.render(
            process_key="env",
            request={
                "request_id": "r1",
                "pair_uuid": "pair",
                "zone_id": "z1",
                "prefer_source_render": True,
            },
            viewer_pair={},
            overlay={},
            overlays=[],
        )
        controller._handle_response('{"ok": true, "event": "ready"}')

        assert fake_timer.starts == [45_000, 45_000]
    finally:
        controller.shutdown()
