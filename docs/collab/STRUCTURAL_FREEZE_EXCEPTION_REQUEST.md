# Structural Freeze Exception Request

- Date: 2026-06-08
- Owner: Codex
- Scope: `src/gui/drawing_compare_workbench.py` selected-zone lightweight crop glue.
- Reason: The live defect is in the existing monolith method that applies selected-zone crops to the active lightweight before/after viewports. The behavior change must preserve existing request ordering, fidelity state, overlay push, and camera sync calls at that call site.
- Mitigation: Core bbox union, empty-side framing, and camera-sync helpers were moved to `src/gui/zone_crop_alignment.py`; the monolith change is limited to wiring those helpers into the existing method.
- Approval: User requested direct implementation and debugging of the viewer mismatch/text defects in this turn; no broader refactor approved.
