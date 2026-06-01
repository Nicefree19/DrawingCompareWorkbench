"""Build synthetic multi-sheet DXF fixtures for sheet-match metrics.

The generated data is explicitly synthetic and must not be used as
customer-grade evidence. It exists to verify that the sheet matching metric
contract reports precision/recall/manual-review burden deterministically.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import ezdxf


PAGE_W = 420.0
PAGE_H = 297.0
PAGE_GAP = 80.0


@dataclass(frozen=True)
class SyntheticSheet:
    sheet_id: str
    drawing_number: str
    title: str
    manual_required: bool = False


@dataclass(frozen=True)
class FixtureCase:
    name: str
    before: Sequence[SyntheticSheet]
    after: Sequence[SyntheticSheet]


def _cases() -> list[FixtureCase]:
    return [
        FixtureCase(
            name="2sheets_clear",
            before=[
                SyntheticSheet("clear-01", "S22-0001", "LEVEL 1 FRAMING"),
                SyntheticSheet("clear-02", "S22-0002", "LEVEL 2 FRAMING"),
            ],
            after=[
                SyntheticSheet("clear-01", "S22-0001", "LEVEL 1 FRAMING"),
                SyntheticSheet("clear-02", "S22-0002", "LEVEL 2 FRAMING"),
            ],
        ),
        FixtureCase(
            name="3sheets_ambiguous",
            before=[
                SyntheticSheet("amb-01", "S23-0001", "LEVEL 1 FRAMING"),
                SyntheticSheet("amb-02", "", "TYPICAL DETAIL", manual_required=True),
                SyntheticSheet("amb-03", "S23-0003", "ROOF FRAMING"),
            ],
            after=[
                SyntheticSheet("amb-01", "S23-0001", "LEVEL 1 FRAMING"),
                SyntheticSheet("amb-02", "", "TYPICAL DETAIL", manual_required=True),
                SyntheticSheet("amb-03", "S23-0003", "ROOF FRAMING"),
            ],
        ),
        FixtureCase(
            name="5sheets_one_renamed",
            before=[
                SyntheticSheet("rename-01", "S25-0001", "LEVEL 1 FRAMING"),
                SyntheticSheet("rename-02", "S25-0002", "LEVEL 2 FRAMING"),
                SyntheticSheet("rename-03", "S25-0003", "LEVEL 3 FRAMING"),
                SyntheticSheet("rename-04", "S25-0004", "ROOF FRAMING"),
                SyntheticSheet("rename-05", "S25-0005", "STAIR DETAIL", manual_required=True),
            ],
            after=[
                SyntheticSheet("rename-01", "S25-0001", "LEVEL 1 FRAMING"),
                SyntheticSheet("rename-02", "S25-0002", "LEVEL 2 FRAMING"),
                SyntheticSheet("rename-03", "S25-0003", "LEVEL 3 FRAMING"),
                SyntheticSheet("rename-04", "S25-0004", "ROOF FRAMING"),
                SyntheticSheet("rename-05", "S25-0005R", "STAIR DETAIL", manual_required=True),
            ],
        ),
    ]


def _frame_bbox(index: int) -> list[float]:
    x0 = index * (PAGE_W + PAGE_GAP)
    y0 = 0.0
    return [x0, y0, x0 + PAGE_W, y0 + PAGE_H]


def _write_dxf(path: Path, sheets: Sequence[SyntheticSheet]) -> list[dict[str, object]]:
    doc = ezdxf.new("R2010")
    doc.layers.add("FRAME")
    doc.layers.add("TITLE")
    doc.layers.add("CHANGE")
    msp = doc.modelspace()
    manifest_sheets: list[dict[str, object]] = []
    for index, sheet in enumerate(sheets):
        x0, y0, x1, y1 = _frame_bbox(index)
        msp.add_lwpolyline(
            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            close=True,
            dxfattribs={"layer": "FRAME"},
        )
        number_text = sheet.drawing_number or "NO-DWG-NUMBER"
        title_line = f"{number_text} {sheet.title}"
        text = msp.add_text(title_line, dxfattribs={"layer": "TITLE", "height": 8.0})
        text.dxf.insert = (x0 + 15.0, y0 + 30.0)
        marker = msp.add_text("REVISION MARK", dxfattribs={"layer": "CHANGE", "height": 5.0})
        marker.dxf.insert = (x0 + 180.0, y0 + 150.0)
        manifest_sheets.append(
            {
                "id": sheet.sheet_id,
                "drawing_number": sheet.drawing_number,
                "title": sheet.title,
                "texts": [value for value in (sheet.drawing_number, sheet.title) if value],
                "frame_bbox": [x0, y0, x1, y1],
                "manual_required": sheet.manual_required,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return manifest_sheets


def build_multi_sheet_fixtures(out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    fixtures: list[dict[str, object]] = []
    for case in _cases():
        before_path = out / f"{case.name}.dxf"
        after_path = out / f"{case.name}_after.dxf"
        before_sheets = _write_dxf(before_path, case.before)
        after_sheets = _write_dxf(after_path, case.after)
        truth = []
        for before, after in zip(before_sheets, after_sheets):
            truth.append(
                {
                    "before_id": before["id"],
                    "after_id": after["id"],
                    "manual_required": bool(before.get("manual_required") or after.get("manual_required")),
                }
            )
        fixtures.append(
            {
                "name": case.name,
                "before_path": before_path.name,
                "after_path": after_path.name,
                "synthetic": True,
                "before_sheets": before_sheets,
                "after_sheets": after_sheets,
                "ground_truth": truth,
            }
        )
    manifest = {
        "schema_version": 1,
        "synthetic": True,
        "fixture_count": len(fixtures),
        "fixtures": fixtures,
    }
    manifest_path = out / "multi_sheet_ground_truth.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_multi_sheet_fixtures(args.out)
    print(json.dumps({"synthetic": True, "fixture_count": manifest["fixture_count"], "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
