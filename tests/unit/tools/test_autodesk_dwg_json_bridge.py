from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from tools import autodesk_dwg_json_bridge as bridge


def test_autodesk_bridge_requires_existing_input(tmp_path: Path) -> None:
    args = Namespace(
        input=tmp_path / "missing.dwg",
        acadver="AC1032",
        autodesk_root=None,
        csc=None,
        build_root=tmp_path / "build",
        timeout_seconds=1,
        max_entities=10,
    )

    with pytest.raises(bridge.BridgeError, match="input DWG does not exist"):
        bridge.run_bridge(args)


def test_autodesk_bridge_wraps_extractor_output_with_native_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1032 test")
    autodesk_root = tmp_path / "TrueView"
    autodesk_root.mkdir()
    csc = tmp_path / "csc.exe"
    csc.write_text("stub", encoding="ascii")
    extractor = tmp_path / "DcwAutodeskDwgJsonExtractor.exe"
    extractor.write_text("stub", encoding="ascii")

    def fake_run(command, **kwargs):
        assert command[0] == str(extractor)
        assert command[1] == str(input_dwg.resolve())
        assert command[2] == "AC1032"
        assert command[4] == "25"
        assert str(autodesk_root) in kwargs["env"]["PATH"].split(os.pathsep)
        out = Path(command[3])
        out.write_text(
            json.dumps(
                {
                    "header": {"$ACADVER": "AC1032"},
                    "layers": [{"name": "0"}],
                    "entities": [
                        {
                            "type": "LINE",
                            "layer": "0",
                            "handle": "10",
                            "geometry": {"start": [0, 0, 0], "end": [1, 0, 0]},
                        }
                    ],
                    "metadata": {"autodesk_dwg_json_bridge": {"entity_count": 1}},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bridge, "resolve_autodesk_root", lambda explicit=None: autodesk_root)
    monkeypatch.setattr(bridge, "resolve_csc", lambda explicit=None: csc)
    monkeypatch.setattr(bridge, "build_extractor", lambda **kwargs: extractor)
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    payload = bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="ac1032",
            autodesk_root=None,
            csc=None,
            build_root=tmp_path / "build",
            timeout_seconds=1,
            max_entities=25,
        )
    )

    metadata = payload["drawing"]["metadata"]
    provenance = metadata["commercial_dwg_json_bridge"]
    assert payload["schema_version"] == bridge.SCHEMA_VERSION
    assert provenance["evidence_scope"] == "native_dwg_bridge"
    assert provenance["uses_native_dwg"] is True
    assert provenance["uses_converted_dxf"] is False
    assert provenance["autodesk_root"] == str(autodesk_root)
    assert provenance["extractor_exe"] == str(extractor)
    assert provenance["csc_path"] == str(csc)
    assert provenance["max_entities"] == 25
    assert metadata["autodesk_dwg_json_bridge"]["bridge"] == bridge.BRIDGE_NAME


def test_resolve_autodesk_root_prefers_explicit_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / "DWG TrueView"
    runtime.mkdir()
    for dll in bridge.MANAGED_DLLS:
        (runtime / dll).write_text("stub", encoding="ascii")

    assert bridge.resolve_autodesk_root(runtime) == runtime.resolve()
