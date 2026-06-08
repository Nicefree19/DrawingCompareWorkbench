from __future__ import annotations

import io
import json

from src.services.comparison import zone_render_process


def test_decode_request_line_preserves_korean_windows_path() -> None:
    source = r"D:\도면 비교\dxf_registered\before\240111_P5 복합동_PSRC,HMB 상세도.dxf"
    payload = {"source_before": source, "source_after": source}
    line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

    decoded = zone_render_process._decode_request_line(line)

    assert json.loads(decoded)["source_before"] == source


def test_stdin_request_lines_reads_utf8_bytes_not_locale_text() -> None:
    source = r"D:\도면 비교\dxf_registered\after\240111_P5 복합동_PSRC,HMB 상세도_r1.dxf"
    payload = {"source_before": source, "source_after": source}
    stdin = io.BytesIO((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    decoded = list(zone_render_process._stdin_request_lines(stdin))

    assert json.loads(decoded[0])["source_after"] == source


def test_write_response_emits_utf8_bytes(monkeypatch) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    monkeypatch.setattr(zone_render_process.sys, "stdout", stdout)

    zone_render_process._write_response({"ok": True, "path": r"D:\도면 비교\상세도.dxf"})
    stdout.flush()

    raw = stdout.buffer.getvalue()
    assert json.loads(raw.decode("utf-8"))["path"] == r"D:\도면 비교\상세도.dxf"
