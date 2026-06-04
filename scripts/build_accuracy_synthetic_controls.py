"""Build local synthetic DWG accuracy controls.

These files are intentionally local evidence, not product native-DWG proof.
They use the repository's MIT-safe ``CANONICAL_DWG_FIXTURE_V1`` adapter payload
so the corpus can cover noise, block transform, and importer edge categories
without copying customer drawings or requiring a proprietary converter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path(".local/accuracy-evidence/synthetic-controls")
DEFAULT_MANIFEST = Path(".local/accuracy-evidence/synthetic_controls_manifest.json")
DEFAULT_TRUTH = Path(".local/accuracy-evidence/synthetic_controls_truth.json")
DEFAULT_DWG_VERSION = "AC1015"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter  # noqa: E402


@dataclass(frozen=True)
class SyntheticControl:
    pair_id: str
    pair_type: str
    drawing_category: str
    complexity_tags: tuple[str, ...]
    before_payload: dict[str, Any]
    after_payload: dict[str, Any]
    expected_changed: bool
    expected_changes: tuple[dict[str, Any], ...]
    confidence: str
    notes: str


def build_controls(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    truth_path: Path = DEFAULT_TRUTH,
    clean: bool = False,
    dwg_version: str = DEFAULT_DWG_VERSION,
) -> dict[str, Any]:
    output_dir = _resolve(output_dir)
    manifest_path = _resolve(manifest_path)
    truth_path = _resolve(truth_path)
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for control in _controls():
        pair_dir = output_dir / control.pair_id
        pair_dir.mkdir(parents=True, exist_ok=True)
        before_path = _write_fixture(pair_dir / "before.dwg", dwg_version, control.before_payload)
        after_path = _write_fixture(pair_dir / "after.dwg", dwg_version, control.after_payload)
        before_record = _file_record(control, before_path, "before", dwg_version)
        after_record = _file_record(control, after_path, "after", dwg_version)
        files.extend([before_record, after_record])
        pairs.append(_truth_record(control, before_record["file_id"], after_record["file_id"], dwg_version))

    manifest = {
        "schema_version": "dwg-accuracy-synthetic-controls-manifest/v1",
        "generated_at": datetime.now().isoformat(),
        "source_policy": "local MIT-safe DwgJsonFixtureAdapter fixtures; no customer drawings copied",
        "output_dir": str(output_dir),
        "summary": {
            "file_count": len(files),
            "pair_count": len(pairs),
            "pair_type_counts": _counts(pair["pair_type"] for pair in pairs),
        },
        "files": files,
    }
    truth = {
        "schema_version": "dwg-accuracy-synthetic-controls-truth/v1",
        "generated_at": datetime.now().isoformat(),
        "source_manifest": str(manifest_path),
        "summary": {
            "pair_count": len(pairs),
            "pair_type_counts": _counts(pair["pair_type"] for pair in pairs),
        },
        "pairs": pairs,
    }
    _write_json(manifest_path, manifest)
    _write_json(truth_path, truth)
    return {"manifest": manifest, "truth": truth}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--dwg-version", default=DEFAULT_DWG_VERSION)
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_controls(
        args.output_dir,
        manifest_path=args.manifest,
        truth_path=args.truth,
        clean=args.clean,
        dwg_version=args.dwg_version,
    )
    summary = result["truth"]["summary"]
    print(f"pairs={summary['pair_count']}")
    print(f"pair_types={summary['pair_type_counts']}")
    return 0


def _controls() -> list[SyntheticControl]:
    return [
        SyntheticControl(
            pair_id="synth_noise_title_text",
            pair_type="non_structural_noise",
            drawing_category="title block-heavy",
            complexity_tags=("synthetic", "title_block", "text"),
            before_payload=_payload([_line("L1"), _text("T1", "REV 0", layer="TITLE", y=40)]),
            after_payload=_payload([_line("L1"), _text("T1", "REV 1", layer="TITLE", y=40)]),
            expected_changed=False,
            expected_changes=(),
            confidence="high",
            notes="Title block revision text changed; structural geometry is unchanged.",
        ),
        SyntheticControl(
            pair_id="synth_noise_style_only",
            pair_type="non_structural_noise",
            drawing_category="simple geometry",
            complexity_tags=("synthetic", "style_only", "lineweight"),
            before_payload=_payload([_line("L1", style={"color": 1, "lineweight": 18})]),
            after_payload=_payload([_line("L1", style={"color": 3, "lineweight": 35})]),
            expected_changed=False,
            expected_changes=(),
            confidence="high",
            notes="Only visual style changed; geometry and structural semantics are unchanged.",
        ),
        SyntheticControl(
            pair_id="synth_noise_mtext_note",
            pair_type="non_structural_noise",
            drawing_category="text/dimension-heavy",
            complexity_tags=("synthetic", "annotation", "mtext"),
            before_payload=_payload([_line("L1"), _mtext("M1", "CHECKED BY A")]),
            after_payload=_payload([_line("L1"), _mtext("M1", "CHECKED BY B")]),
            expected_changed=False,
            expected_changes=(),
            confidence="high",
            notes="Annotation-only note changed; structural geometry is unchanged.",
        ),
        SyntheticControl(
            pair_id="synth_block_rotation",
            pair_type="block_transform_case",
            drawing_category="block-heavy",
            complexity_tags=("synthetic", "block", "rotation"),
            before_payload=_payload([_insert("I1", rotation=0.0)], blocks=[_block("B_ROT")]),
            after_payload=_payload([_insert("I1", rotation=90.0)], blocks=[_block("B_ROT")]),
            expected_changed=True,
            expected_changes=(
                _expected_change("block_transform_noise", "INSERT", "block rotation changed from 0 to 90 degrees"),
            ),
            confidence="medium",
            notes="Block reference rotation changed; normalization must preserve the transform.",
        ),
        SyntheticControl(
            pair_id="synth_block_scale",
            pair_type="block_transform_case",
            drawing_category="block-heavy",
            complexity_tags=("synthetic", "block", "scale"),
            before_payload=_payload([_insert("I1", block_name="B_SCALE", scale=1.0)], blocks=[_block("B_SCALE")]),
            after_payload=_payload([_insert("I1", block_name="B_SCALE", scale=1.2)], blocks=[_block("B_SCALE")]),
            expected_changed=True,
            expected_changes=(
                _expected_change("block_transform_noise", "INSERT", "block scale changed from 1.0 to 1.2"),
            ),
            confidence="medium",
            notes="Block reference scale changed; normalization must preserve the transform.",
        ),
        SyntheticControl(
            pair_id="synth_import_circle_normal",
            pair_type="import_edge_case",
            drawing_category="import edge geometry",
            complexity_tags=("synthetic", "circle", "ocs_normal"),
            before_payload=_payload([_circle("C1", normal=(0.0, 0.0, 1.0))]),
            after_payload=_payload([_circle("C1", normal=(0.0, 0.0, -1.0))]),
            expected_changed=True,
            expected_changes=(
                _expected_change("ocs_normal_mismatch", "CIRCLE", "circle normal flipped"),
            ),
            confidence="medium",
            notes="Circle OCS normal changed; importer must preserve normal metadata.",
        ),
        SyntheticControl(
            pair_id="synth_import_arc_angles",
            pair_type="import_edge_case",
            drawing_category="import edge geometry",
            complexity_tags=("synthetic", "arc", "curve"),
            before_payload=_payload([_arc("A1", start=0.0, end=90.0)]),
            after_payload=_payload([_arc("A1", start=15.0, end=105.0)]),
            expected_changed=True,
            expected_changes=(
                _expected_change("curve_approximation_noise", "ARC", "arc angle window shifted"),
            ),
            confidence="medium",
            notes="Arc angle range changed; curve canonicalization must preserve the sweep.",
        ),
    ]


def _payload(entities: list[dict[str, Any]], *, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "header": {"$INSUNITS": 4},
        "layers": [
            {"name": "BEAM", "color": 7, "linetype": "Continuous", "lineweight": 25},
            {"name": "TITLE", "color": 2, "linetype": "Continuous", "lineweight": 13},
            {"name": "ANNO", "color": 3, "linetype": "Continuous", "lineweight": 13},
        ],
        "blocks": blocks or [],
        "model_space": entities,
        "metadata": {"fixture": True, "accuracy_synthetic_control": True},
    }


def _line(handle: str, *, layer: str = "BEAM", style: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "LINE",
        "handle": handle,
        "layer": layer,
        "style": style or {"color": 256, "linetype": "BYLAYER", "lineweight": -1},
        "geometry": {"start": _point(0.0, 0.0), "end": _point(1000.0, 0.0)},
    }


def _text(handle: str, text: str, *, layer: str = "ANNO", y: float = 20.0) -> dict[str, Any]:
    return {
        "type": "TEXT",
        "handle": handle,
        "layer": layer,
        "geometry": {"insert": _point(0.0, y), "height": 2.5, "text": text},
    }


def _mtext(handle: str, text: str) -> dict[str, Any]:
    return {
        "type": "MTEXT",
        "handle": handle,
        "layer": "ANNO",
        "geometry": {"insert": _point(0.0, 60.0), "height": 2.5, "raw_content": text, "box_width": 120.0},
    }


def _block(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "origin": _point(0.0, 0.0),
        "entities": [_line(f"{name}_L1")],
    }


def _insert(
    handle: str,
    *,
    block_name: str = "B_ROT",
    rotation: float = 0.0,
    scale: float = 1.0,
) -> dict[str, Any]:
    return {
        "type": "INSERT",
        "handle": handle,
        "layer": "BEAM",
        "geometry": {
            "block_name": block_name,
            "insert": _point(100.0, 100.0),
            "scale": _point(scale, scale, 1.0),
            "rotation_deg": rotation,
        },
    }


def _circle(handle: str, *, normal: tuple[float, float, float]) -> dict[str, Any]:
    return {
        "type": "CIRCLE",
        "handle": handle,
        "layer": "BEAM",
        "geometry": {"center": _point(200.0, 200.0), "radius": 25.0, "normal": _point(*normal)},
    }


def _arc(handle: str, *, start: float, end: float) -> dict[str, Any]:
    return {
        "type": "ARC",
        "handle": handle,
        "layer": "BEAM",
        "geometry": {
            "center": _point(250.0, 250.0),
            "radius": 40.0,
            "start_angle_deg": start,
            "end_angle_deg": end,
            "normal": _point(0.0, 0.0, 1.0),
        },
    }


def _expected_change(bucket: str, entity_type: str, notes: str) -> dict[str, Any]:
    return {
        "sheet": "Model",
        "region_id": "synthetic-A1",
        "entity_type": entity_type,
        "change_type": "geometry_modification",
        "severity": "structural",
        "approx_bbox": [0.0, 0.0, 500.0, 500.0],
        "tolerance_class": "structural_position_tolerance_mm",
        "failure_bucket_hint": bucket,
        "notes": notes,
    }


def _point(x: float, y: float, z: float = 0.0) -> dict[str, float]:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _write_fixture(path: Path, version: str, payload: dict[str, Any]) -> Path:
    data = version.encode("ascii") + DwgJsonFixtureAdapter.MARKER + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    path.write_bytes(data)
    return path


def _file_record(control: SyntheticControl, path: Path, side: str, version: str) -> dict[str, Any]:
    digest = _sha256(path)
    file_id = f"{control.pair_id}_{side}_{version}_{digest[:8]}"
    return {
        "file_id": file_id,
        "absolute_path": str(path),
        "sha256": digest,
        "file_size_bytes": path.stat().st_size,
        "dwg_version": version,
        "source_type": "generated",
        "confidentiality": "public",
        "license_or_permission": "MIT",
        "drawing_category": control.drawing_category,
        "complexity_tags": list(control.complexity_tags),
        "has_model_space": True,
        "has_paper_space": False,
        "has_blocks": "block" in control.complexity_tags,
        "has_nested_blocks": False,
        "has_text": any(tag in control.complexity_tags for tag in ("text", "annotation")),
        "has_dimensions": False,
        "has_hatch": False,
        "json_fixture": True,
        "fixture_pair_id": control.pair_id,
        "fixture_side": side,
        "notes": control.notes,
    }


def _truth_record(control: SyntheticControl, before_id: str, after_id: str, version: str) -> dict[str, Any]:
    return {
        "pair_id": control.pair_id,
        "before_file_id": before_id,
        "after_file_id": after_id,
        "pair_type": control.pair_type,
        "expected_changed": control.expected_changed,
        "expected_change_count": len(control.expected_changes),
        "expected_changes": list(control.expected_changes),
        "reviewer_status": "agent_draft",
        "confidence": control.confidence,
        "dwg_version": version,
        "synthetic_control": True,
        "notes": control.notes,
    }


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
