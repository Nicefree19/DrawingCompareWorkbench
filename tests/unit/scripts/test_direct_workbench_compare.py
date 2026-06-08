from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts.direct_workbench_compare import (
    _descriptor_root,
    _first_zone_leaf,
    _manifest_fallback_fields,
    _process_events_for,
    _zone_item_id,
    _zone_item_text,
)


def _write_manifest(results_dir: Path, inputs: dict) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "run_manifest.json").write_text(
        json.dumps({"inputs": inputs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_manifest_fallback_fields_restore_effective_paths_from_descriptors_when_redacted(
    tmp_path: Path,
) -> None:
    before = tmp_path / "work" / "dxf_registered" / "before" / "detail.dxf"
    after = tmp_path / "work" / "dxf_registered" / "after" / "detail_r1.dxf"
    before.parent.mkdir(parents=True)
    after.parent.mkdir(parents=True)
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")
    results_dir = tmp_path / "out" / "results"
    _write_manifest(
        results_dir,
        {
            "effective_source_a": "<redacted>/before",
            "effective_source_b": "<redacted>/after",
            "dwg_dxf_fallback": {
                "used": True,
                "reason": "unsupported_dwg_folder_with_converted_dxf_dirs",
                "diagnostics": {"fallback_kind": "dxf_registered/before_after_dirs"},
            },
        },
    )
    result = SimpleNamespace(
        descriptors_a=[SimpleNamespace(path=str(before))],
        descriptors_b=[SimpleNamespace(path=str(after))],
    )

    fields = _manifest_fallback_fields(results_dir, result)

    assert fields["effective_source_a"] == str(before.resolve())
    assert fields["effective_source_b"] == str(after.resolve())
    assert fields["fallback_used"] is True
    assert fields["fallback_reason"] == "unsupported_dwg_folder_with_converted_dxf_dirs"
    assert fields["fallback_kind"] == "dxf_registered/before_after_dirs"


def test_manifest_fallback_fields_preserve_unredacted_manifest_paths(tmp_path: Path) -> None:
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    results_dir = tmp_path / "out" / "results"
    _write_manifest(
        results_dir,
        {
            "effective_source_a": str(before_dir),
            "effective_source_b": str(after_dir),
            "dwg_dxf_fallback": {"used": False},
        },
    )
    result = SimpleNamespace(
        descriptors_a=[SimpleNamespace(path=str(tmp_path / "other_a.dxf"))],
        descriptors_b=[SimpleNamespace(path=str(tmp_path / "other_b.dxf"))],
    )

    fields = _manifest_fallback_fields(results_dir, result)

    assert fields["effective_source_a"] == str(before_dir)
    assert fields["effective_source_b"] == str(after_dir)
    assert fields["fallback_used"] is False


def test_manifest_fallback_fields_default_when_manifest_missing(tmp_path: Path) -> None:
    fields = _manifest_fallback_fields(tmp_path / "missing")

    assert fields == {
        "effective_source_a": "",
        "effective_source_b": "",
        "fallback_used": False,
        "fallback_reason": "",
        "fallback_kind": "",
    }


def test_descriptor_root_uses_common_parent_for_multiple_descriptors(tmp_path: Path) -> None:
    root = tmp_path / "before"
    first = root / "a.dxf"
    second = root / "b.dxf"
    first.parent.mkdir(parents=True)
    first.write_text("0\nEOF\n", encoding="utf-8")
    second.write_text("0\nEOF\n", encoding="utf-8")

    assert _descriptor_root(
        [SimpleNamespace(path=str(first)), SimpleNamespace(path=str(second))]
    ) == str(root.resolve())


class _FakeItem:
    def __init__(
        self,
        text: str,
        zone_id: str = "",
        children: list["_FakeItem"] | None = None,
    ) -> None:
        self._text = text
        self._zone_id = zone_id
        self._children = children or []

    def childCount(self) -> int:
        return len(self._children)

    def child(self, idx: int) -> "_FakeItem":
        return self._children[idx]

    def text(self, column: int) -> str:
        assert column == 0
        return self._text

    def data(self, column: int, role: object) -> str:
        assert column == 0
        return self._zone_id


class _FakeTree:
    def __init__(self, items: list[_FakeItem]) -> None:
        self._items = items

    def topLevelItemCount(self) -> int:
        return len(self._items)

    def topLevelItem(self, idx: int) -> _FakeItem:
        return self._items[idx]


def test_first_zone_leaf_prefers_workbench_leaf_helper() -> None:
    leaf = _FakeItem("C-001 change", "C-001")
    workbench = SimpleNamespace(_zone_leaf_items_v2=lambda: [leaf])

    assert _first_zone_leaf(workbench) is leaf
    assert _zone_item_id(leaf) == "C-001"
    assert _zone_item_text(leaf) == "C-001 change"


def test_first_zone_leaf_falls_back_to_tree_leaf_not_header() -> None:
    leaf = _FakeItem("C-002 add", "C-002")
    header = _FakeItem("other changes", children=[leaf])
    workbench = SimpleNamespace(zone_list_v2=_FakeTree([header]))

    assert _first_zone_leaf(workbench) is leaf


def test_process_events_for_zero_duration_still_processes_once() -> None:
    calls: list[str] = []
    app = SimpleNamespace(processEvents=lambda: calls.append("tick"))

    _process_events_for(app, 0)

    assert calls
