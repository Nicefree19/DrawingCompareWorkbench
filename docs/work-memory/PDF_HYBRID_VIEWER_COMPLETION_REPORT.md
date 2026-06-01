# PDF Hybrid Viewer Completion Report

Date: 2026-06-01

## Completed

- H5d viewer-package emit now handles CAD/DWG pairs that have sidecar PDF visual assets.
- Sidecar PDFs are rendered as the visual background while the truth coordinate source remains `cad_world`.
- CAD overlay bboxes are projected into `image_pixels_tl` display coordinates through `cad_pdf_alignment.build_display_overlays`.
- Pair and overlay payloads now carry `display_overlay_space` and `transform_quality`.
- Viewer manifest v3 propagates display overlay space for hybrid packages.

## Validation

- `python -m pytest tests/unit/services/comparison/test_viewer_package.py::test_viewer_package_hybrid_cad_pair_renders_sidecar_pdf_and_display_overlays -q` -> passed
- `python -m pytest tests/unit/services/comparison/test_viewer_package.py tests/unit/services/comparison/test_cad_pdf_alignment.py tests/unit/services/comparison/test_viewer_manifest_v3.py -q` -> 60 passed
- `python scripts/cad_policy_gate.py` -> passed

## Remaining

- Real 3PG1 DWG/PDF end-to-end overlay error validation is still pending because no real 3PG1 fixture was available in this run.
- No `src/gui/drawing_compare_workbench.py` changes were made.
