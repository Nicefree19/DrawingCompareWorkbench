# 02_single_modification — AC1032 DWG fixtures

`before.dwg` / `after.dwg` are AC1032 (AutoCAD 2018) DWG renderings of the
sibling `../before.dxf` / `../after.dxf` golden pair. AC1032 is **not** readable
by the native adapter, so these exercise the real ODA conversion on-ramp
(DWG → DXF → compare) end-to-end — the path that unit tests only mock.

Used by the `@skipif(converter not installed)` real-ODA e2e test in
`tests/unit/scripts/test_run_pilot_spotcheck.py` (single-file and folder input).

## Regeneration (when the golden DXF changes)

ODA File Converter works on folders: `ODAFileConverter <inDir> <outDir> ACAD2018 DWG 0 1`.
Put `before.dxf` in an input folder, run the command, copy the produced `before.dwg`
here; repeat for `after.dxf`. (The exact byte content is not asserted — tests check
the detected change, not byte equality — so ODA-version drift is tolerated.)
