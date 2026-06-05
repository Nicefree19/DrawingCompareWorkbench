from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts import build_accuracy_synthetic_controls as builder
from scripts import evaluate_accuracy_evidence as evaluator


def test_evaluate_synthetic_controls_reports_structural_metrics(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    report = evaluator.evaluate_evidence(manifest_path, truth_path)

    assert report["status"] == "blocked"
    assert report["summary"]["active_pair_count"] == 7
    assert report["summary"]["evaluated_pair_count"] == 7
    assert report["summary"]["skipped_pair_count"] == 0
    assert report["summary"]["tp_count"] == 4
    assert report["summary"]["tn_count"] == 3
    assert report["summary"]["fp_count"] == 0
    assert report["summary"]["fn_count"] == 0
    assert report["summary"]["precision"] == 1.0
    assert report["summary"]["recall"] == 1.0
    assert report["by_pair_type"]["non_structural_noise"]["tn_count"] == 3
    assert report["by_pair_type"]["block_transform_case"]["tp_count"] == 2
    assert report["by_pair_type"]["import_edge_case"]["tp_count"] == 2
    assert report["target_assessment"]["internal_pilot_accuracy"]["status"] == "blocked"
    assert "active_pair_count=7/50" in report["target_assessment"]["internal_pilot_accuracy"]["blockers"]


def test_evaluate_skips_non_fixture_dwg_without_marking_failure(tmp_path: Path) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015plain")
    after.write_bytes(b"AC1015plain")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    _file("before", before),
                    _file("after", after),
                ]
            }
        ),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "plain",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                        "reviewer_status": "agent_draft",
                        "confidence": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = evaluator.evaluate_evidence(manifest, truth)

    assert report["status"] == "skipped"
    assert report["summary"]["evaluated_pair_count"] == 0
    assert report["summary"]["skipped_pair_count"] == 1
    assert report["pairs"][0]["skip_reason"] == "requires_non_fixture_dwg_backend"
    assert report["summary"]["skip_reason_counts"] == {"requires_non_fixture_dwg_backend": 1}


def test_evaluate_reuses_import_cache_for_duplicate_file_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )
    truth_payload = json.loads(truth_path.read_text(encoding="utf-8"))
    first_pair = dict(truth_payload["pairs"][0])
    duplicate_pair = dict(first_pair)
    duplicate_pair["pair_id"] = f"{first_pair['pair_id']}_duplicate"
    truth_payload["pairs"] = [first_pair, duplicate_pair]
    truth_path.write_text(json.dumps(truth_payload), encoding="utf-8")

    calls: Counter[str] = Counter()
    original_import_file = evaluator.DwgImporter.import_file

    def counting_import(self, path):
        calls[str(path)] += 1
        return original_import_file(self, path)

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", counting_import)

    report = evaluator.evaluate_evidence(manifest_path, truth_path)

    assert report["summary"]["evaluated_pair_count"] == 2
    assert sum(calls.values()) == 2
    assert report["pairs"][0]["import_cache"] == {"before_hit": False, "after_hit": False}
    assert report["pairs"][1]["import_cache"] == {"before_hit": True, "after_hit": True}


def test_evaluate_skips_after_import_when_before_import_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015fixture")
    after.write_bytes(b"AC1015fixture")
    before_record = _file("before", before)
    after_record = _file("after", after)
    before_record["json_fixture"] = True
    after_record["json_fixture"] = True
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(json.dumps({"files": [before_record, after_record]}), encoding="utf-8")
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "before-timeout",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[Path] = []

    def fake_import(self, path):
        path = Path(path)
        calls.append(path)
        if path.name == "before.dwg":
            return {"import_report": {"status": "error", "error_code": "DWG_IMPORT_TIMEOUT"}}
        raise AssertionError("after import should not run after before import failure")

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)

    report = evaluator.evaluate_evidence(manifest, truth)

    assert [path.name for path in calls] == ["before.dwg"]
    assert report["pairs"][0]["skip_reason"] == "before_import_DWG_IMPORT_TIMEOUT"
    assert report["pairs"][0]["import_report"]["after"] is None
    assert report["pairs"][0]["import_cache"] == {"before_hit": False, "after_hit": False}


def test_evaluate_quarantines_same_file_across_different_file_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stuck = tmp_path / "stuck.dwg"
    after_one = tmp_path / "after_one.dwg"
    after_two = tmp_path / "after_two.dwg"
    stuck.write_bytes(b"AC1015stuck")
    after_one.write_bytes(b"AC1015after1")
    after_two.write_bytes(b"AC1015after2")
    records = [
        _fixture_file("stuck_a", stuck),
        _fixture_file("stuck_b", stuck),
        _fixture_file("after_one", after_one),
        _fixture_file("after_two", after_two),
    ]
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(json.dumps({"files": records}), encoding="utf-8")
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "first",
                        "before_file_id": "stuck_a",
                        "after_file_id": "after_one",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                    },
                    {
                        "pair_id": "second",
                        "before_file_id": "stuck_b",
                        "after_file_id": "after_two",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: Counter[str] = Counter()

    def fake_import(self, path):
        path = Path(path)
        calls[path.name] += 1
        if path.name == "stuck.dwg":
            return {"import_report": {"status": "error", "error_code": "DWG_IMPORT_TIMEOUT"}}
        raise AssertionError("after import should not run after before import failure")

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)

    report = evaluator.evaluate_evidence(manifest, truth)

    assert calls == Counter({"stuck.dwg": 1})
    assert [pair["skip_reason"] for pair in report["pairs"]] == [
        "before_import_DWG_IMPORT_TIMEOUT",
        "before_import_DWG_IMPORT_TIMEOUT",
    ]
    assert report["summary"]["skip_reason_counts"] == {"before_import_DWG_IMPORT_TIMEOUT": 2}
    assert report["pairs"][0]["import_cache"]["before_hit"] is False
    assert report["pairs"][1]["import_cache"]["before_hit"] is True


def test_evaluate_treats_cap_hit_no_change_as_truncated_skip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps({"files": [_fixture_file("before", before), _fixture_file("after", after)]}),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "cap-hit",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [{"entity_type": "INSERT", "change_type": "geometry_modification"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_import(self, path):
        return {
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 5000, "canonical_entity_count": 5000},
            },
            "metadata": {
                "adapter_metadata": {
                    # Genuine cap hit: the bridge emits the authoritative truncation
                    # flag (more entities remained after the cap).
                    "commercial_dwg_json_bridge": {"max_entities": 5000, "truncated": True},
                }
            },
        }

    class NoChangeEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old_doc, _new_doc):
            class Result:
                def to_dict(self):
                    return {
                        "changes": [],
                        "summary": {
                            "added": 0,
                            "removed": 0,
                            "modified": 0,
                            "unchanged": 5000,
                            "total_changes": 0,
                            "total_records": 5000,
                        },
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", NoChangeEngine)

    report = evaluator.evaluate_evidence(manifest, truth)
    pair = report["pairs"][0]

    assert report["summary"]["fn_count"] == 0
    assert report["summary"]["skipped_pair_count"] == 1
    assert report["summary"]["skip_reason_counts"] == {"cap_truncated_requires_roi_extraction": 1}
    assert pair["status"] == "skipped"
    assert pair["skip_reason"] == "cap_truncated_requires_roi_extraction"
    assert pair["cap_truncation"]["possibly_truncated"] is True


def test_evaluate_exact_cap_without_truncation_flag_counts_as_fn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A drawing that holds exactly max_entities but is NOT truncated must be a real
    FN, not a silent cap-truncated skip (regression for the entity_count>=cap heuristic)."""
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps({"files": [_fixture_file("before", before), _fixture_file("after", after)]}),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "exact-cap",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [{"entity_type": "INSERT", "change_type": "geometry_modification"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_import(self, path):
        return {
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 5000, "canonical_entity_count": 5000},
            },
            # Exactly at the cap but the extractor finished cleanly -> truncated False.
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {"max_entities": 5000, "truncated": False},
                }
            },
        }

    class NoChangeEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old_doc, _new_doc):
            class Result:
                def to_dict(self):
                    return {"changes": [], "summary": {"total_changes": 0, "total_records": 5000}}

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", NoChangeEngine)

    report = evaluator.evaluate_evidence(manifest, truth)
    pair = report["pairs"][0]

    assert pair["status"] == "evaluated"
    assert pair["classification"] == "FN"
    assert report["summary"]["fn_count"] == 1
    assert report["summary"]["skipped_pair_count"] == 0


def test_evaluate_roi_retry_recovers_cap_truncated_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps({"files": [_file("before", before), _file("after", after)]}),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "cap-hit",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "dwg_version": "AC1015",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [
                            {
                                "entity_type": "LINE",
                                "change_type": "geometry_modification",
                                "approx_bbox": [100, 100, 500, 500],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True

        def __init__(self) -> None:
            self.args_template = ("bridge.py", "{input}", "{acadver}", "--max-entities", "5000")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        def __init__(self, adapter) -> None:
            self.adapter = adapter

        def to_dict(self):
            return {
                "mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK,
                "adapter": self.adapter.name,
                "adapter_version": self.adapter.version,
            }

    adapter = FakeAdapter()
    selection = FakeSelection(adapter)
    roi_args_seen: list[str] = []

    monkeypatch.setattr(
        evaluator,
        "_create_evaluation_backend",
        lambda _backend: {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": selection},
    )

    def fake_import(self, path):
        path = Path(path)
        template = tuple(
            str(item).format(
                input=str(path),
                path=str(path),
                stem=path.stem,
                version="AC1015",
                acadver="AC1015",
                family="AutoCAD 2000",
                release="AutoCAD 2000/2000i/2002",
            )
            for item in getattr(self.adapter, "args_template", ())
        )
        if "--roi-json" in template:
            roi_args_seen.append(template[template.index("--roi-json") + 1])
            return {
                "roi_retry_import": True,
                "entities": [],
                "import_report": {
                    "status": "ok",
                    "stats": {"raw_entity_count": 1, "canonical_entity_count": 1},
                },
                "metadata": {
                    "adapter_metadata": {
                        "commercial_dwg_json_bridge": {
                            "max_entities": 5000,
                            "possibly_truncated": False,
                        },
                    }
                },
            }
        return {
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 5000, "canonical_entity_count": 5000},
            },
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {
                        "max_entities": 5000,
                        "possibly_truncated": True,
                    },
                }
            },
        }

    class RetryAwareEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, old_doc, _new_doc):
            changed = bool(old_doc.get("roi_retry_import"))

            class Result:
                def to_dict(self_inner):
                    changes = (
                        [
                            {
                                "change_id": "line-1",
                                "change_type": "modified",
                                "entity_type": "line",
                                "layer_name": "0",
                                "geometry_diff": {
                                    "categories": ["geometry"],
                                    "fields": [{"path": "geometry.start"}],
                                },
                            }
                        ]
                        if changed
                        else []
                    )
                    return {
                        "changes": changes,
                        "summary": {
                            "added": 0,
                            "removed": 0,
                            "modified": len(changes),
                            "unchanged": 0,
                            "total_changes": len(changes),
                            "total_records": 1,
                        },
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", RetryAwareEngine)

    report = evaluator.evaluate_evidence(
        manifest,
        truth,
        dwg_backend="commercial_sdk",
        roi_retry_margin=250,
    )
    pair = report["pairs"][0]

    assert report["summary"]["tp_count"] == 1
    assert report["summary"]["skipped_pair_count"] == 0
    assert pair["status"] == "evaluated"
    assert pair["classification"] == "TP"
    assert pair["roi_retry"]["attempted"] is True
    assert pair["roi_retry"]["mode"] == "retry"
    assert pair["roi_retry"]["roi_request"] == {"bbox": [100.0, 100.0, 500.0, 500.0], "margin": 250.0}
    assert {json.loads(item)["margin"] for item in roi_args_seen} == {250.0}
    assert adapter.args_template == ("bridge.py", "{input}", "{acadver}", "--max-entities", "5000")


def test_evaluate_roi_first_skips_initial_full_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True

        def __init__(self) -> None:
            self.args_template = ("bridge.py", "{input}", "{acadver}", "--max-entities", "5000")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        def __init__(self, adapter) -> None:
            self.adapter = adapter

        def to_dict(self):
            return {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "adapter": self.adapter.name}

    adapter = FakeAdapter()
    roi_args_seen: list[str] = []

    def fake_import(self, path):
        path = Path(path)
        template = tuple(
            str(item).format(
                input=str(path),
                path=str(path),
                stem=path.stem,
                version="AC1015",
                acadver="AC1015",
                family="AutoCAD 2000",
                release="AutoCAD 2000/2000i/2002",
            )
            for item in getattr(self.adapter, "args_template", ())
        )
        assert "--roi-json" in template
        roi_args_seen.append(template[template.index("--roi-json") + 1])
        return {
            "roi_retry_import": True,
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 1, "canonical_entity_count": 1},
            },
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {
                        "max_entities": 5000,
                        "possibly_truncated": False,
                    },
                }
            },
        }

    class AlwaysChangedEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old_doc, _new_doc):
            class Result:
                def to_dict(self):
                    return {
                        "changes": [
                            {
                                "change_id": "line-1",
                                "change_type": "modified",
                                "entity_type": "line",
                                "geometry_diff": {
                                    "categories": ["geometry"],
                                    "fields": [{"path": "geometry.start"}],
                                },
                            }
                        ],
                        "summary": {"modified": 1, "total_changes": 1, "total_records": 1},
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", AlwaysChangedEngine)

    pair_report = evaluator._evaluate_pair(
        {
            "pair_id": "roi-first",
            "before_file_id": "before",
            "after_file_id": "after",
            "pair_type": "small_geometry_change",
            "expected_changed": True,
            "expected_change_count": 1,
            "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
        },
        {
            "before": _file("before", before),
            "after": _file("after", after),
        },
        {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": FakeSelection(adapter)},
        {},
        roi_retry_margin=100,
        roi_first=True,
    )

    assert pair_report["status"] == "evaluated"
    assert pair_report["classification"] == "TP"
    assert pair_report["roi_retry"]["mode"] == "first"
    assert len(roi_args_seen) == 2
    assert {json.loads(item)["margin"] for item in roi_args_seen} == {100.0}


def test_evaluate_roi_first_no_change_falls_through_to_full_extraction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """ROI-first with a non-empty ROI that misses the change must NOT be recorded as
    FN; it falls through to the full extraction, which detects the change (TP)."""
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True

        def __init__(self) -> None:
            self.args_template = ("bridge.py", "{input}", "{acadver}", "--max-entities", "5000")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        def __init__(self, adapter) -> None:
            self.adapter = adapter

        def to_dict(self):
            return {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "adapter": self.adapter.name}

    adapter = FakeAdapter()
    roi_imports = 0
    full_imports = 0

    def fake_import(self, path):
        nonlocal roi_imports, full_imports
        path = Path(path)
        template = tuple(
            str(item).format(
                input=str(path), path=str(path), stem=path.stem,
                version="AC1015", acadver="AC1015",
                family="AutoCAD 2000", release="AutoCAD 2000/2000i/2002",
            )
            for item in getattr(self.adapter, "args_template", ())
        )
        is_roi = "--roi-json" in template
        if is_roi:
            roi_imports += 1
        else:
            full_imports += 1
        return {
            "roi_import": is_roi,
            "entities": [{}, {}, {}],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 3, "canonical_entity_count": 3},
            },
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {"max_entities": 5000, "truncated": False},
                }
            },
        }

    class RoiMissesChangeEngine:
        """ROI docs show no change; the full (non-ROI) extraction reveals the change."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, old_doc, _new_doc):
            changed = not bool(old_doc.get("roi_import"))

            class Result:
                def to_dict(self_inner):
                    changes = (
                        [
                            {
                                "change_id": "line-1",
                                "change_type": "modified",
                                "entity_type": "line",
                                "geometry_diff": {"categories": ["geometry"], "fields": [{"path": "geometry.start"}]},
                            }
                        ]
                        if changed
                        else []
                    )
                    return {
                        "changes": changes,
                        "summary": {"total_changes": len(changes), "total_records": 3},
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", RoiMissesChangeEngine)

    pair_report = evaluator._evaluate_pair(
        {
            "pair_id": "roi-first-miss",
            "before_file_id": "before",
            "after_file_id": "after",
            "expected_changed": True,
            "expected_change_count": 1,
            "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
        },
        {"before": _file("before", before), "after": _file("after", after)},
        {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": FakeSelection(adapter)},
        {},
        roi_retry_margin=100,
        roi_first=True,
    )

    assert pair_report["status"] == "evaluated"
    assert pair_report["classification"] == "TP"
    assert roi_imports == 2  # ROI-first before+after attempted
    assert full_imports == 2  # then fell through to the full extraction
    assert pair_report["roi_first_attempt"]["fell_through_to_full"] is True
    # the adapter template is restored after the ROI attempt
    assert adapter.args_template == ("bridge.py", "{input}", "{acadver}", "--max-entities", "5000")


def test_evaluate_empty_roi_result_is_not_counted_as_fn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True
        args_template = ("bridge.py", "{input}", "{acadver}")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        adapter = FakeAdapter()

        def to_dict(self):
            return {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "adapter": self.adapter.name}

    def fake_import(self, _path):
        return {
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 0, "canonical_entity_count": 0},
            },
            "metadata": {"adapter_metadata": {"commercial_dwg_json_bridge": {"max_entities": 5000}}},
        }

    class NoChangeEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old_doc, _new_doc):
            class Result:
                def to_dict(self):
                    return {
                        "changes": [],
                        "summary": {"total_changes": 0, "total_records": 0},
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", NoChangeEngine)

    pair_report = evaluator._evaluate_pair(
        {
            "pair_id": "empty-roi",
            "before_file_id": "before",
            "after_file_id": "after",
            "expected_changed": True,
            "expected_change_count": 1,
            "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
        },
        {"before": _file("before", before), "after": _file("after", after)},
        {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": FakeSelection()},
        {},
        roi_retry_margin=100,
        roi_first=True,
    )

    assert pair_report["status"] == "skipped"
    assert pair_report["skip_reason"] == "roi_empty_requires_bbox_recalibration"
    assert pair_report["roi_retry"]["mode"] == "first"


def test_evaluate_roi_margin_sweep_recovers_after_empty_first_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")
    margins_seen: list[float] = []

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True

        def __init__(self) -> None:
            self.args_template = ("bridge.py", "{input}", "{acadver}")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        def __init__(self, adapter) -> None:
            self.adapter = adapter

        def to_dict(self):
            return {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "adapter": self.adapter.name}

    adapter = FakeAdapter()

    def fake_import(self, path):
        path = Path(path)
        template = tuple(
            str(item).format(
                input=str(path),
                path=str(path),
                stem=path.stem,
                version="AC1015",
                acadver="AC1015",
                family="AutoCAD 2000",
                release="AutoCAD 2000/2000i/2002",
            )
            for item in getattr(self.adapter, "args_template", ())
        )
        margin = 0.0
        if "--roi-json" in template:
            margin = float(json.loads(template[template.index("--roi-json") + 1])["margin"])
            margins_seen.append(margin)
        entity_count = 1 if margin >= 1000.0 else 0
        return {
            "roi_retry_import": entity_count > 0,
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": entity_count, "canonical_entity_count": entity_count},
            },
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {
                        "max_entities": 5000,
                        "possibly_truncated": False,
                    },
                }
            },
        }

    class ChangedWhenEntitiesExistEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, old_doc, _new_doc):
            changed = bool(old_doc.get("roi_retry_import"))

            class Result:
                def to_dict(self_inner):
                    changes = (
                        [
                            {
                                "change_id": "line-1",
                                "change_type": "modified",
                                "entity_type": "line",
                                "geometry_diff": {
                                    "categories": ["geometry"],
                                    "fields": [{"path": "geometry.start"}],
                                },
                            }
                        ]
                        if changed
                        else []
                    )
                    return {
                        "changes": changes,
                        "summary": {"total_changes": len(changes), "total_records": 1 if changed else 0},
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", ChangedWhenEntitiesExistEngine)

    pair_report = evaluator._evaluate_pair(
        {
            "pair_id": "roi-sweep",
            "before_file_id": "before",
            "after_file_id": "after",
            "expected_changed": True,
            "expected_change_count": 1,
            "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
        },
        {"before": _file("before", before), "after": _file("after", after)},
        {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": FakeSelection(adapter)},
        {},
        roi_retry_margins=(100, 1000),
        roi_first=True,
    )

    assert pair_report["status"] == "evaluated"
    assert pair_report["classification"] == "TP"
    assert pair_report["roi_retry"]["attempt_count"] == 2
    assert [attempt["detail"] for attempt in pair_report["roi_retry"]["attempts"]] == [
        "roi_empty_requires_bbox_recalibration",
        "TP",
    ]
    assert margins_seen == [100.0, 100.0, 1000.0, 1000.0]


def test_evaluate_roi_attempt_retries_after_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")
    import_calls = 0

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True
        args_template = ("bridge.py", "{input}", "{acadver}")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        adapter = FakeAdapter()

        def to_dict(self):
            return {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "adapter": self.adapter.name}

    def fake_import(self, _path):
        nonlocal import_calls
        import_calls += 1
        if import_calls == 1:
            return {
                "import_report": {
                    "status": "failed",
                    "error_code": "DWG_IMPORT_TIMEOUT",
                    "stats": {"raw_entity_count": 0, "canonical_entity_count": 0},
                }
            }
        return {
            "roi_retry_import": True,
            "entities": [],
            "import_report": {
                "status": "ok",
                "stats": {"raw_entity_count": 1, "canonical_entity_count": 1},
            },
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {
                        "max_entities": 5000,
                        "possibly_truncated": False,
                    },
                }
            },
        }

    class AlwaysChangedEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old_doc, _new_doc):
            class Result:
                def to_dict(self):
                    return {
                        "changes": [
                            {
                                "change_id": "line-1",
                                "change_type": "modified",
                                "entity_type": "line",
                                "geometry_diff": {
                                    "categories": ["geometry"],
                                    "fields": [{"path": "geometry.start"}],
                                },
                            }
                        ],
                        "summary": {"total_changes": 1, "total_records": 1},
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", AlwaysChangedEngine)

    pair_report = evaluator._evaluate_pair(
        {
            "pair_id": "roi-timeout-retry",
            "before_file_id": "before",
            "after_file_id": "after",
            "expected_changed": True,
            "expected_change_count": 1,
            "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
        },
        {"before": _file("before", before), "after": _file("after", after)},
        {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": FakeSelection()},
        {},
        roi_retry_margins=(100,),
        roi_attempt_retries=2,
        roi_first=True,
    )

    assert pair_report["status"] == "evaluated"
    assert pair_report["classification"] == "TP"
    assert [attempt["detail"] for attempt in pair_report["roi_retry"]["attempts"]] == [
        "roi_retry_before_import_DWG_IMPORT_TIMEOUT",
        "TP",
    ]
    assert [attempt["attempt_index"] for attempt in pair_report["roi_retry"]["attempts"]] == [1, 2]


def test_parse_roi_margins_arg_accepts_csv_and_json() -> None:
    assert evaluator._parse_roi_margins_arg("100, 500;1000") == (100.0, 500.0, 1000.0)
    assert evaluator._parse_roi_margins_arg("[250, 750]") == (250.0, 750.0)


def test_evaluate_non_fixture_dwg_reports_unavailable_backend(tmp_path: Path) -> None:
    manifest, truth = _plain_dwg_manifest_and_truth(tmp_path)

    report = evaluator.evaluate_evidence(manifest, truth, dwg_backend="commercial_sdk")

    assert report["status"] == "skipped"
    assert report["summary"]["evaluated_pair_count"] == 0
    assert report["summary"]["skipped_pair_count"] == 1
    assert report["pairs"][0]["skip_reason"] == "dwg_backend_unavailable"
    assert report["pairs"][0]["dwg_backend"]["mode"] == "commercial_sdk"


def test_evaluate_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    report_json = tmp_path / "metric.json"
    report_md = tmp_path / "metric.md"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    exit_code = evaluator.main(
        [
            "--manifest",
            str(manifest_path),
            "--truth",
            str(truth_path),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
            "--allow-blocked",
        ]
    )

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["summary"]["evaluated_pair_count"] == 7
    assert report_md.read_text(encoding="utf-8").startswith("# Accuracy Metric Report")


def test_evaluate_cli_returns_nonzero_when_target_blocked(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    report_json = tmp_path / "metric.json"
    report_md = tmp_path / "metric.md"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    exit_code = evaluator.main(
        [
            "--manifest",
            str(manifest_path),
            "--truth",
            str(truth_path),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
        ]
    )

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["status"] == "blocked"


def _file(file_id: str, path: Path) -> dict[str, object]:
    return {
        "file_id": file_id,
        "absolute_path": str(path),
        "sha256": "0" * 64,
        "file_size_bytes": path.stat().st_size,
        "dwg_version": "AC1015",
        "source_type": "generated",
        "confidentiality": "public",
    }


def _fixture_file(file_id: str, path: Path) -> dict[str, object]:
    record = _file(file_id, path)
    record["json_fixture"] = True
    return record


def _plain_dwg_manifest_and_truth(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015plain")
    after.write_bytes(b"AC1015plain")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    _file("before", before),
                    _file("after", after),
                ]
            }
        ),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "plain",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                        "reviewer_status": "agent_draft",
                        "confidence": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest, truth


def test_roi_attempt_reuses_cache_for_identical_roi(tmp_path: Path, monkeypatch) -> None:
    """A successful ROI extraction is cached under an ROI-aware key and reused on an
    identical second attempt instead of re-launching CAD (finding 4)."""
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True
        args_template = ("bridge.py", "{input}", "{acadver}")

        def is_available(self) -> bool:
            return True

    import_calls = 0

    def fake_import(self, _path):
        nonlocal import_calls
        import_calls += 1
        return {
            "roi_retry_import": True,
            "entities": [{}],
            "import_report": {"status": "ok", "stats": {"raw_entity_count": 1, "canonical_entity_count": 1}},
            "metadata": {"adapter_metadata": {"commercial_dwg_json_bridge": {"max_entities": 5000, "truncated": False}}},
        }

    class AlwaysChangedEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old, _new):
            class Result:
                def to_dict(self):
                    return {
                        "changes": [
                            {
                                "change_id": "c1",
                                "change_type": "modified",
                                "entity_type": "line",
                                "geometry_diff": {"categories": ["geometry"], "fields": [{"path": "geometry.start"}]},
                            }
                        ],
                        "summary": {"total_changes": 1, "total_records": 1},
                    }

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", AlwaysChangedEngine)

    adapter = FakeAdapter()
    backend = {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": type("S", (), {"adapter": adapter, "to_dict": lambda self: {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK}})()}
    pair = {
        "pair_id": "roi-cache",
        "expected_changed": True,
        "expected_change_count": 1,
        "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
    }
    shared_cache: dict = {}
    common = dict(
        roi_margin=100.0,
        roi_attempt_index=1,
        initial_import_ms=0.0,
        initial_compare_ms=0.0,
        initial_cap_truncation={"possibly_truncated": None, "sides": {}},
        pair_started=0.0,
        roi_mode="first",
        import_cache=shared_cache,
        before_record=_file("before", before),
        after_record=_file("after", after),
    )

    first_report, _ = evaluator._roi_attempt_pair(pair, before, after, backend, False, **common)
    assert first_report["status"] == "evaluated"
    assert import_calls == 2  # before + after launched once
    assert first_report["import_cache"] == {"before_hit": False, "after_hit": False}

    second_report, _ = evaluator._roi_attempt_pair(pair, before, after, backend, False, **common)
    assert second_report["status"] == "evaluated"
    assert import_calls == 2  # reused from cache, no new CAD launches
    assert second_report["import_cache"] == {"before_hit": True, "after_hit": True}


def test_evaluate_roi_max_attempts_caps_cad_launches(tmp_path: Path, monkeypatch) -> None:
    """roi_max_attempts bounds the per-pair CAD-launch escalation and the cap is
    reported explicitly rather than silently truncating the sweep (finding 15)."""
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015before")
    after.write_bytes(b"AC1015after")

    class FakeAdapter:
        name = "fake-commercial"
        version = "1"
        license_id = "INTERNAL"
        backend_mode = evaluator.DWG_BACKEND_COMMERCIAL_SDK
        implementation_status = "json_bridge_configured"
        approval_required = True
        args_template = ("bridge.py", "{input}", "{acadver}")

        def is_available(self) -> bool:
            return True

    class FakeSelection:
        adapter = FakeAdapter()

        def to_dict(self):
            return {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "adapter": self.adapter.name}

    import_calls = 0

    def fake_import(self, _path):
        nonlocal import_calls
        import_calls += 1
        return {
            "entities": [],
            "import_report": {"status": "ok", "stats": {"raw_entity_count": 0, "canonical_entity_count": 0}},
            "metadata": {"adapter_metadata": {"commercial_dwg_json_bridge": {"max_entities": 5000, "truncated": False}}},
        }

    class NoChangeEngine:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def compare(self, _old, _new):
            class Result:
                def to_dict(self):
                    return {"changes": [], "summary": {"total_changes": 0, "total_records": 0}}

            return Result()

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", fake_import)
    monkeypatch.setattr(evaluator, "DrawingCompareEngine", NoChangeEngine)

    pair_report = evaluator._evaluate_pair(
        {
            "pair_id": "roi-capped",
            "before_file_id": "before",
            "after_file_id": "after",
            "expected_changed": True,
            "expected_change_count": 1,
            "expected_changes": [{"approx_bbox": [100, 100, 500, 500]}],
        },
        {"before": _file("before", before), "after": _file("after", after)},
        {"mode": evaluator.DWG_BACKEND_COMMERCIAL_SDK, "selection": FakeSelection()},
        {},
        roi_retry_margins=(100, 1000, 5000),
        roi_first=True,
        roi_max_attempts=1,
    )

    assert pair_report["status"] == "skipped"
    assert pair_report["roi_retry"]["launched_attempts"] == 1
    assert pair_report["roi_retry"]["capped_at_max_attempts"] is True
    # one attempt = before + after only, not the full 3-margin sweep (which would be 6)
    assert import_calls == 2


def test_temporary_bridge_roi_args_restores_on_exception() -> None:
    """The ROI args context manager must restore args_template even when the body
    raises, so a failed ROI import never leaves a stale --roi-json on a shared adapter."""
    class _Adapter:
        args_template = ("bridge.py", "{input}", "{acadver}")

    adapter = _Adapter()
    original = adapter.args_template
    try:
        with evaluator._temporary_bridge_roi_args(adapter, {"bbox": [0, 0, 10, 10], "margin": 5}):
            assert "--roi-json" in adapter.args_template
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert adapter.args_template == original
