from __future__ import annotations

import json
import re
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from tools import autocad_dwg_json_bridge as bridge


def test_autocad_bridge_requires_existing_input(tmp_path: Path) -> None:
    args = Namespace(
        input=tmp_path / "missing.dwg",
        acadver="AC1027",
        accoreconsole=None,
        timeout_seconds=1,
        max_entities=10,
        keep_temp=False,
    )

    with pytest.raises(bridge.BridgeError, match="input DWG does not exist"):
        bridge.run_bridge(args)


def test_autocad_bridge_wraps_extractor_output_with_native_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1027 test")
    accoreconsole = tmp_path / "accoreconsole.exe"
    accoreconsole.write_text("stub", encoding="ascii")

    def fake_resolve(explicit=None):
        return accoreconsole

    def fake_run(command, *, capture_output, timeout, check):
        script_path = Path(command[command.index("/s") + 1])
        script = script_path.read_text(encoding="ascii")
        match = re.search(r'\(setq DCW_OUT "([^"]+)"\)', script)
        assert match is not None
        out = Path(match.group(1).replace("/", "\\"))
        out.write_text(
            json.dumps(
                {
                    "header": {"$ACADVER": "AC1027"},
                    "layers": [{"name": "0"}],
                    "entities": [
                        {
                            "type": "LINE",
                            "layer": "0",
                            "handle": "10",
                            "geometry": {"start": [0, 0, 0], "end": [1, 0, 0]},
                        }
                    ],
                    "metadata": {"autocad_dwg_json_bridge": {"entity_count": 1}},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(bridge, "resolve_accoreconsole", fake_resolve)
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    payload = bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="AC1027",
            accoreconsole=None,
            timeout_seconds=1,
            max_entities=10,
            keep_temp=False,
        )
    )

    metadata = payload["drawing"]["metadata"]
    provenance = metadata["commercial_dwg_json_bridge"]
    assert payload["schema_version"] == bridge.SCHEMA_VERSION
    assert provenance["evidence_scope"] == "native_dwg_bridge"
    assert provenance["uses_native_dwg"] is True
    assert provenance["uses_converted_dxf"] is False
    assert provenance["accoreconsole_path"] == str(accoreconsole)
    assert metadata["autocad_dwg_json_bridge"]["bridge"] == bridge.BRIDGE_NAME


def test_resolve_accoreconsole_prefers_explicit_path(tmp_path: Path) -> None:
    explicit = tmp_path / "accoreconsole.exe"
    explicit.write_text("stub", encoding="ascii")

    assert bridge.resolve_accoreconsole(explicit) == explicit.resolve()
