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
- Disk sidecar probe: `out/h5d_sidecar_probe/h5d_sidecar_probe_summary.json` -> passed
  - `visual_fidelity`: `pdf_render`
  - `display_overlay_space`: `image_pixels_tl`
  - `transform_quality`: `exact`
  - `rendered_pair_count`: 1
  - overlay display bbox emitted in image-pixel top-left space
- `python -m pytest tests/unit/services/comparison/test_viewer_package.py -q` -> 23 passed
- `python scripts/cad_policy_gate.py` -> passed
- `git diff --check` -> passed

## Remaining

- Real 3PG1 DWG/PDF end-to-end overlay error validation is still pending because no exact real 3PG1 CAD/PDF sidecar fixture was available in this run.
- The disk sidecar probe validates the H5d manifest/render/overlay path with actual files on disk, but it is not customer-grade 3PG1 evidence.
- No `src/gui/drawing_compare_workbench.py` changes were made.
