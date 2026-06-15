# Own CAD Viewer — AC1032 Spike TechSpec

Date: 2026-06-14
Status: IN PROGRESS (de-risking spike; not a support claim, not default enablement)
North star: `own-cad-viewer-goal` (deep-interview, 2026-06-14)
Progress (2026-06-14, `dwg_r2018_reader.py`, 13 tests):
- **S0 (provenance) DONE** — `AC1032_CLEANROOM_PROVENANCE.md`.
- **S1 (R2018 container navigable) DONE** — R2004 LCG de-obfuscation reproduces
  the `AcFssFcAJMB` magic and locates the section-page-map in bounds on two real
  AC1032 files.
- **S2 (decompression + section-page-map + section-map) DONE** —
  `decompress_r2004` implements the public-spec LZ77 variant (consulted the
  published ODA spec sections 4.4-4.7, clean-room, no GPL source);
  `read_r2004_section_page_map` decodes the page directory;
  `read_r2004_section_map` enumerates the named data sections and locates
  `AcDb:AcDbObjects` (sample: 41 pages, 1.19 MB), `AcDb:Handles`, `AcDb:Header`
  on both real files. (Fixed a real literal-length-0 decompression bug the
  larger section-map stream exposed.)

- **S3a (reach the object bytes) DONE** — `read_r2004_data_section` +
  `decrypt_r2004_data_page_header` decrypt (0x4164536B ^ offset) + decompress
  every page of a named data section; `AcDb:AcDbObjects` assembles to
  1,192,851 B (41 pages) and `AcDb:Handles` to its exact size on both real
  files. Reuse for the bit-decode: `DwgBinaryReader` already provides the DWG
  bit-code toolkit (B/BB/BS/BL/BD/MC/MS/handle/string) and `dwg_object_decoder`
  has LINE/CIRCLE/ARC/LWPOLYLINE geometry decoders + object-type constants.

- **S3b (object framing + object-type decode) DONE** — cracked the R2018
  object stride against real data: each object is `[MS object-size][MC
  handle-stream-bits][bit stream: MS bytes][RS CRC]`, so the next object is at
  `offset + (MS+MC field bytes) + MS + 2`. `read_r2018_object_type` (spec 2.12:
  bit pair + 1-2 bytes) + `read_r2018_object_run` decode a contiguous run on
  both real files into real DWG object types (sample: 111 objects ->
  LINE 32 / POINT 21 / DICTIONARY 19 / LAYER 13 / CIRCLE 1 / INSERT 4 / ...).

**Both spike unknowns are answered YES, the container is fully navigated, and
real object TYPES decode from the bit stream.**

- **S3 part 1 (AcDb:Handles index -> full enumeration) DONE** —
  `parse_r2018_handle_map` / `read_r2018_handle_map` / `read_r2018_object_table`
  decode the handle map (public-spec R2004+ structure, identical to the AC1015
  object map: big-endian u16 section size; `(handle-delta MC, location-delta
  signed MC)` pairs; 2-byte CRC; `size<=2` terminator). PROVEN on the primary
  sample: 842 entries, handles strictly increasing, clean terminator, and ALL
  111 contiguous-run object offsets located by the map (exact 1:1). This
  resolves the earlier "the handle encoding drifts" note — the prior "drift to
  132876" was a real object location; the decode is correct.

- **S3 part 1b (multi-page assembly fix) DONE** — root cause: each page fills a
  `max_decompressed_size` slot (section descriptor word @0x0C = 0x7400), but
  `read_r2004_data_section` decompressed to the page header's size word, which
  UNDER-reports the page content, leaving zero gaps. Fix: decompress each page to
  `min(max_decompressed_size, section.size - start_offset)`. Object framing
  55%→**88.2%** (acadsharp) / 42%→**75.9%** (calpoly); the fix also cleaned up
  calpoly's handle-map garbage tail (AcDb:Handles was under-decompressed too).
  Residual unframed are non-framable handle entries (stale/deleted/other-section),
  not gaps. `read_r2004_data_section` is spike-only (no product caller).

Remaining S3: **geometry decode** (LINE/CIRCLE/ARC/LWPOLYLINE/TEXT coordinates,
common-entity header field-by-field) → canonical, now with correctly-assembled
object extents. Still diagnostic-only — no geometry decoded yet, contract still
DECODING-gated.

## 1. Goal

Prove, on one real **AC1032** drawing pair, that the product can read DWG with an
**own clean-room reader** (no ODA, no ezdxf), render it with the **own renderer**,
and auto-draw **revision clouds** on the diff using the **existing** canonical
diff/cloud engine — with **zero ODA calls**. The spike exists to de-risk the one
unknown that gates the whole program: *can AC1032 (R2018) geometry be parsed from
the public ODA spec in a clean room?*

This is a thin vertical slice, not the product. It is allowed to support only
basic geometry and one drawing pair.

## 2. Architecture (what is new vs reused)

```
 NEW (own)                              NEW (skeleton exists)        REUSED (unchanged)
 AC1032 DWG --clean-room parse--> canonical --native_scene_pack--> viewport
   dwg_r2018_*.py                  (canonical/v1)   _builder + ViewerPrimitiveSource
                                                          |
                                                          v
                                       existing diff + revision-cloud engine
                                       (drawing_differ / change_zones / revision_marker)
                                                  --> 구름마크 (auto)
```

- **Reader (NEW):** AC1032 binary → `canonical-drawing/v1`. Reuses the AC1015
  clean-room *architecture* (`dwg_binary_reader` bit/byte reader; the
  reader→section→object→canonical layering) — NOT its R2000 section format.
- **Renderer (skeleton exists):** `native_scene_pack_builder.build_native_scene_pack(_ref)`
  + `resolve_viewer_primitive_source` already turn canonical entities into
  viewport primitives (test-proven, commit 3c7bbcb). Extend entity coverage only.
- **Diff + clouds (REUSED, untouched):** the canonical-level engine already
  produces change zones and revision clouds. The spike feeds it canonical from
  the own reader instead of ezdxf/ODA.

## 3. Clean-room governance gate (Step 0 — mandatory, do FIRST)

`dwg_cleanroom_contract.py` already gates AC1032 as `approval_status="blocked"`
with `next_safe_step = "record an approved AC1032 section-map format contract
before decoding"`. Required evidence to unblock:

1. approved public-reference citation with license/provenance — **ODA "Open
   Design Specification for .dwg files"** (publicly published for implementers;
   implementing from it is permitted). **No GPL (LibreDWG) code referenced.**
2. product-owner approval recorded in docs — **granted 2026-06-14** (deep-interview).
3. clean-room implementation notes written without incompatible source code.
4. diagnostic-only tests before any entity-decoding support claim.

**S0 deliverable:** flip the AC1032 contract to an approved state (reference =
ODA public spec, provenance recorded), add a short `docs/collab/` clean-room
provenance note. No byte layouts in the contract object itself. Until S0 lands,
S1+ must not decode AC1032 object bytes.

## 4. Spike steps (each step ships a test + a kill/go check)

| Step | Deliverable | Validates | Kill signal |
| --- | --- | --- | --- |
| **S0 governance** | Approved AC1032 clean-room contract + provenance note | gate unblocked legitimately | (none — paperwork) |
| **S1 header+section-page-map (diagnostic only)** | `dwg_r2018_reader`: parse the R2018 file header → locate the section-page map / section map; emit a DIAGNOSTIC (counts, offsets), decode NO objects | the R2018 container is navigable from spec | header/section map not reproducible from spec in ~1 wk → reassess |
| **S2 section extract (decompress)** | Locate + decompress the `AcDb:Handles` (object map) and `AcDb:AcDbObjects` sections (R2004+ compression) | object stream is reachable | compression/encryption not decodable from spec → reassess |
| **S3 basic-geometry decode** | Decode LINE/LWPOLYLINE/CIRCLE/ARC/TEXT objects → `canonical-drawing/v1` (fail-closed visible-unsupported for the rest, per the W1 pattern) | real AC1032 imports to canonical entities | per-object layout not stable across the sample → narrow scope |
| **S4 render** | Feed canonical → `build_native_scene_pack_ref` → `resolve_viewer_primitive_source`; confirm primitives + bbox render | own renderer shows the drawing | (renderer already proven; low risk) |
| **S5 diff + clouds** | Run one real AC1032 before/after pair through the existing diff/cloud engine on own-reader canonical; clouds appear on the changes | end-to-end own stack, ODA calls = 0 | clouds misplaced → coordinate/transform gap to fix |

Steps land as separate commits behind a flag; the current ODA/ezdxf pipeline is
untouched and remains the default.

## 5. Go / Kill criteria

- **GO (spike succeeds):** one real AC1032 pair renders basic geometry and shows
  auto revision clouds via the own stack, ODA calls = 0, diff matches the
  existing ODA-based result on the basic-geometry subset (golden compare).
  → proceed to the entity-coverage roadmap (MTEXT/INSERT/HATCH/DIMENSION…).
- **KILL / REASSESS:** S1 or S2 cannot be reproduced from the ODA public spec
  within their week budget (R2018 container or compression intractable
  clean-room). → report honestly; reconsider scope (e.g. start at an easier
  version, or revisit the ODA-dependency decision). No silent grinding.

## 6. Validation harness

- `python -m pytest tests/unit/services/comparison/test_dwg_r2018_reader.py -p no:xdist -q`
- diagnostic-only fixtures first (S1/S2), real-sample integration behind `skipif`
  on a local AC1032 sample (S3–S5), mirroring `test_native_scene_pack_builder.py`.
- `python scripts/cad_policy_gate.py` (no forbidden wording / no support claim).
- ODA-call assertion: the spike path must not import or invoke any ODA/converter.
- `git diff --check`.

## 7. Done criteria (this spike only)

A repeatable command/test takes one real AC1032 pair → own reader → own render →
existing cloud engine → revision clouds, with ODA calls = 0, and a golden compare
showing the basic-geometry diff matches the existing pipeline. Matrix promotion
and any "native AC1032" support wording remain OUT (HITL, later).

## 8. Out of scope (spike)

DWG write/edit; AC1032 entity types beyond the 5 basic ones; other versions
(AC1027/24/21); DXF/PDF paths; performance tuning; default enablement; support
wording; GUI monolith changes (service layer only).

## 9. Sample data

Local AC1032 samples already present (git-ignored): e.g.
`.local/native_cad_real_samples/acadsharp/sample_AC1032.dwg`,
`.local/native_cad_real_samples/calpoly_floor_plans/Building001-0_Floor2.dwg`,
and the user's `D:\도면 비교` / `D:\04. 작성도면` AC1032 sets for a real
before/after pair. The spike picks one real pair for S5; raw drawings stay local.

## 10. Open questions (resolved by the spike or deferred)

1. AC1032 spec parsing feasibility — the spike's core test (S1/S2).
2. Entity-coverage order after GO — by real structural-drawing frequency.
3. Golden accuracy bar — basic-geometry diff parity vs the existing pipeline (S5).
4. Renderer performance on large sheets — deferred until after GO.
5. Full "ship" definition (coverage/versions) — deferred until after GO.
