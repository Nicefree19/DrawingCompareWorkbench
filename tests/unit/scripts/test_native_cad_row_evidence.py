from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.native_cad_row_evidence import build_evidence_packet, build_fixture_evidence_packet, main


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_BRIDGE = ROOT / "tools" / "native_cad_fixture_bridge.py"


def _write_dwg(path: Path, code: str = "AC1032") -> Path:
    path.write_bytes(code.encode("ascii") + b"\nfixture\n")
    return path


def test_row_evidence_packet_passes_with_fixture_bridge(tmp_path: Path) -> None:
    before = _write_dwg(tmp_path / "before.dwg")
    after = _write_dwg(tmp_path / "after_r1.dwg")

    packet = build_evidence_packet(
        code="AC1032",
        before=before,
        after=after,
        bridge_command=sys.executable,
        bridge_args_template=(str(FIXTURE_BRIDGE), "{input}", "{acadver}"),
    )

    assert packet["status"] == "PASS"
    assert packet["code"] == "AC1032"
    assert packet["canonical_schema_result"]["before_valid"] is True
    assert packet["compare_expected_result"]["has_modified_text"] is True
    assert packet["viewer_lod0_budget"]["before_within_budget"] is True
    assert packet["cache_identity_fields"]["before"]["present"] is True
    assert packet["cache_identity_fields"]["fingerprints_differ_for_different_sources"] is True


def test_row_evidence_packet_fails_on_header_mismatch(tmp_path: Path) -> None:
    before = _write_dwg(tmp_path / "before.dwg", code="AC1027")
    after = _write_dwg(tmp_path / "after_r1.dwg", code="AC1027")

    packet = build_evidence_packet(
        code="AC1032",
        before=before,
        after=after,
        bridge_command=sys.executable,
        bridge_args_template=(str(FIXTURE_BRIDGE), "{input}", "{acadver}"),
    )

    assert packet["status"] == "FAIL"
    assert "does not match" in packet["reason"]


def test_row_evidence_packet_is_json_serializable(tmp_path: Path) -> None:
    before = _write_dwg(tmp_path / "before.dwg", code="AC1018")
    after = _write_dwg(tmp_path / "after_r1.dwg", code="AC1018")

    packet = build_evidence_packet(
        code="AC1018",
        before=before,
        after=after,
        bridge_command=sys.executable,
        bridge_args_template=(str(FIXTURE_BRIDGE), "{input}", "{acadver}"),
    )

    assert json.loads(json.dumps(packet, ensure_ascii=False))["status"] == "PASS"


def test_fixture_row_writes_pair_and_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence" / "AC1024.json"

    packet = build_fixture_evidence_packet(
        code="AC1024",
        fixture_root=tmp_path / "fixtures",
        artifact_path=artifact,
    )

    assert packet["status"] == "PASS"
    assert packet["promotion_decision"] == "fixture_evidence_only"
    assert (tmp_path / "fixtures" / "AC1024" / "before.dwg").read_bytes().startswith(b"AC1024")
    assert (tmp_path / "fixtures" / "AC1024" / "after_r1.dwg").read_bytes().startswith(b"AC1024")
    assert json.loads(artifact.read_text(encoding="utf-8"))["code"] == "AC1024"


def test_fixture_row_cli_writes_default_artifact_under_requested_dir(tmp_path: Path) -> None:
    result = main(
        [
            "--code",
            "AC1014",
            "--fixture-row",
            "--fixture-root",
            str(tmp_path / "fixtures"),
            "--artifact-dir",
            str(tmp_path / "artifacts"),
        ]
    )

    assert result == 0
    assert json.loads((tmp_path / "artifacts" / "AC1014.json").read_text(encoding="utf-8"))["status"] == "PASS"


def test_explicit_cli_requires_inputs_without_fixture_row() -> None:
    with pytest.raises(SystemExit):
        main(["--code", "AC1032"])
