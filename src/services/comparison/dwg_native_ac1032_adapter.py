"""Experimental, opt-in read-only AC1032 (R2018) / AC1027 (R2013) native DWG adapter.

This wraps the diagnostic clean-room R2018 reader (``dwg_r2018_reader``) as a
``DwgImporterAdapter`` so an AC1032 (R2018) OR AC1027 (R2013) DWG can flow through
the existing import -> canonical -> compare pipeline with ZERO commercial-converter
/ ezdxf calls. AC1027 shares the SAME R2004+ container, R2010+ Common Entity Data,
and R2007+ string stream as AC1032 (verified 1:1 vs ODA-converted DXF ground
truth), so the single R2018 decode path handles both versions verbatim.

OPT-IN ONLY.  The adapter is default-inert: ``supports_version`` is True for an
AC1032/AC1027 file only when ``DRAWING_COMPARE_DWG_AC1032_NATIVE`` is set to a
truthy value (the SAME opt-in switch governs both versions).  With it unset (the
default), an AC1032/AC1027 file reports unsupported, so the pipeline's existing
default path handles it exactly as before.  Other versions always delegate to the
fallback adapter (typically the AC1015 native adapter), so the default selection
is unchanged when the opt-in is off.

The clean-room format contract for AC1032 and AC1027 both remain ``blocked``; this
adapter is diagnostic/experimental and makes no DWG support claim.
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dwg_importer import (
    DwgAdapterBlock,
    DwgAdapterDrawing,
    DwgAdapterEntity,
    DwgFailureCode,
    DwgImportError,
    DwgImporterAdapter,
    DwgVersionInfo,
)
from .dwg_r2018_reader import (
    ACCEPTED_VERSION_CODES,
    R2018Entity,
    read_r2018_entities,
)


#: Opt-in switch.  Unset/falsey keeps the native AC1032 decode path disabled and
#: the product's default DWG path unchanged.
AC1032_NATIVE_OPT_IN_ENV = "DRAWING_COMPARE_DWG_AC1032_NATIVE"
_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled"}


def ac1032_native_opt_in(*, settings_path: Optional["Path"] = None) -> bool:
    """Whether the experimental AC1032 native decode path is opted in (default off).

    Resolution order (mirrors the DWG auto-convert backend so the GUI behaves
    consistently):
    1. ``DRAWING_COMPARE_DWG_AC1032_NATIVE`` env var — an explicit on/off override
       both ways (a launcher or a test can pin it regardless of saved settings).
    2. The persisted settings file (``ac1032_native_opt_in`` key) — so the user
       can enable it once without re-setting an env var on every launch.
    3. Default: off.
    """

    raw = os.environ.get(AC1032_NATIVE_OPT_IN_ENV)
    if raw is not None:
        token = raw.strip().casefold()
        if token in _TRUTHY:
            return True
        if token in _FALSY or token == "":
            return False
        # an unrecognised value falls through to the persisted setting
    from .dwg_autoconvert_settings import load_ac1032_native_enabled

    return bool(load_ac1032_native_enabled(settings_path))


def set_ac1032_native_opt_in(enabled: bool, *, settings_path: Optional["Path"] = None) -> None:
    """Persist the AC1032 native opt-in so it survives across launches.

    A convenience for a settings UI / CLI to toggle the experimental path without
    an env var; the env var still overrides this when set.
    """

    from .dwg_autoconvert_settings import save_ac1032_native_enabled

    save_ac1032_native_enabled(enabled, settings_path)


def _adapter_entity(
    entity: R2018Entity, unique_block_names: "Optional[Dict[int, str]]" = None
) -> DwgAdapterEntity:
    """Map one decoded ``R2018Entity`` to a ``DwgAdapterEntity`` for the importer.

    The R2018 geometry points are ``(x, y, z)`` tuples that ``DwgImporter._point``
    reads directly.  POINT/DIMENSION/HATCH carry their decoded payload (location /
    measurement+dimtype / pattern+bbox) through to ``DwgImporter._map_entity``,
    which maps them to the same canonical shape the DXF importer emits, so the
    structural diff reads the measurement and pattern regardless of source.  Any
    other type passes through with an empty geometry and is counted
    unsupported-visible (recorded, not dropped).
    """

    geometry = entity.geometry
    name = entity.type_name
    mapped: Dict[str, Any]
    if name == "LINE":
        mapped = {"start": geometry["start"], "end": geometry["end"]}
    elif name == "CIRCLE":
        mapped = {"center": geometry["center"], "radius": geometry["radius"]}
    elif name == "ARC":
        mapped = {
            "center": geometry["center"],
            "radius": geometry["radius"],
            "start_angle_deg": geometry["start_angle_deg"],
            "end_angle_deg": geometry["end_angle_deg"],
        }
    elif name == "ELLIPSE":
        mapped = {
            "center": geometry["center"],
            "major_axis": geometry["major_axis"],
            "ratio": geometry["ratio"],
            "start_param": geometry["start_param"],
            "end_param": geometry["end_param"],
        }
    elif name == "LWPOLYLINE":
        bulges = list(geometry["bulges"]) + [0.0] * len(geometry["vertices"])
        mapped = {
            "vertices": [
                {"point": (vx, vy, 0.0), "bulge": bulge}
                for (vx, vy), bulge in zip(geometry["vertices"], bulges)
            ],
            "closed": geometry["closed"],
        }
    elif name == "TEXT":
        mapped = {
            "insert": geometry["insert"],
            "height": geometry["height"],
            "rotation_deg": geometry["rotation_deg"],
            "text": geometry["text"],
        }
    elif name == "MTEXT":
        mapped = {
            "insert": geometry["insert"],
            "height": geometry["height"],
            "text": geometry["text"],
            "raw_content": geometry["text"],
        }
    elif name == "INSERT":
        # Remap to the UNIQUE block name (keyed by the inserted block's handle) so
        # the INSERT references the exact block definition the importer emits —
        # essential for anonymous *U/*D blocks that share a raw name.
        block_name = geometry["block_name"]
        if unique_block_names:
            block_name = unique_block_names.get(
                geometry.get("block_handle") or 0, block_name
            )
        mapped = {
            "insert": geometry["insert"],
            "scale": geometry["scale"],
            "rotation_deg": geometry["rotation_deg"],
            "block_name": block_name,
        }
    elif name == "POINT":
        mapped = {"location": geometry["location"]}
    elif name == "DIMENSION":
        mapped = {
            "text_midpoint": geometry["text_midpoint"],
            "measurement": geometry["measurement"],
            "dimtype": geometry["dimtype"],
            "text": geometry["text"],
        }
    elif name == "HATCH":
        mapped = {
            "pattern": geometry["pattern"],
            "gradient_name": geometry["gradient_name"],
            "is_gradient": geometry["is_gradient"],
            "solid": geometry["solid"],
            "num_paths": geometry["num_paths"],
            "bbox": geometry["bbox"],
        }
    else:  # any other type -> unsupported-visible (counted, not dropped)
        mapped = {}
    return DwgAdapterEntity(
        raw_type=name,
        geometry=mapped,
        layer=entity.layer or "0",
        handle=f"{entity.handle:X}",
        style={"linetype": entity.linetype, "color": entity.color},
    )


def _build_unique_block_names(block_names: Dict[int, str]) -> Dict[int, str]:
    """Map each BLOCK_HEADER handle to a UNIQUE canonical block name.

    Named blocks keep their name (block names are unique by the DWG invariant).
    Anonymous (``*U``/``*D`` ...) or empty names — which the reader returns
    without the per-instance index, so many distinct blocks share one raw string
    — get a handle suffix so the (here 71) distinct anonymous blocks stay
    distinct for ezdxf expansion. A duplicate name is likewise disambiguated.
    """

    counts: Dict[str, int] = {}
    for name in block_names.values():
        counts[name] = counts.get(name, 0) + 1
    unique: Dict[int, str] = {}
    for handle, name in block_names.items():
        nm = (name or "").strip()
        if not nm or nm.startswith("*") or counts.get(nm, 0) > 1:
            unique[handle] = f"{nm or 'BLK'}_{handle:X}"
        else:
            unique[handle] = nm
    return unique


def native_decode_partial_summary(
    entities: "List[DwgAdapterEntity]",
) -> Dict[str, Any]:
    """Surface entity types decoded structurally but NOT geometrically mapped.

    A mapped entity with empty geometry (e.g. SPLINE/LEADER/WIPEOUT — types the
    clean-room reader does not yet decode to geometry) means the native decode is
    PARTIAL. This summary makes that explicit so the viewer/compare can badge a
    "partial native" state, never silently presenting an incomplete drawing as
    complete ([[silent_fallback_pattern]]).
    """

    unsupported = sorted({e.raw_type for e in entities if not e.geometry})
    return {
        "partial_native_decode": bool(unsupported),
        "unsupported_native_entity_types": unsupported,
    }


class DwgNativeAc1032Adapter(DwgImporterAdapter):
    """Experimental opt-in AC1032/AC1027 native adapter (clean-room R2018 reader).

    Default-inert: ``supports_version`` is True for an AC1032 (R2018) or AC1027
    (R2013) file only when the opt-in env is set (the SAME switch for both), so
    with it unset such a file reports unsupported and the pipeline's existing
    default path handles it unchanged.  Other versions delegate to the fallback
    adapter (typically the AC1015 native adapter). AC1027 reuses the AC1032
    decode path verbatim — the R2004+ container family is genuinely shared.
    """

    name = "native-ac1032"
    version = "0.1"
    license_id = "INTERNAL"
    backend_mode = "cleanroom_native"
    implementation_status = "ac1032_experimental_opt_in"
    approval_required = False

    def __init__(self, fallback_adapter: Optional[DwgImporterAdapter] = None):
        self.fallback_adapter = fallback_adapter

    def is_available(self) -> bool:
        return True

    def _handles_native_version(self, version: DwgVersionInfo) -> bool:
        # AC1032 (R2018) and AC1027 (R2013) share the R2004+ container family and
        # are both gated behind the single experimental opt-in.
        return version.code in ACCEPTED_VERSION_CODES and ac1032_native_opt_in()

    def supports_version(self, version: DwgVersionInfo) -> bool:
        if self._handles_native_version(version):
            return True
        if self.fallback_adapter is not None:
            return self.fallback_adapter.supports_version(version)
        return False

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        if self._handles_native_version(version):
            return self._read_native(Path(path), version)
        if self.fallback_adapter is not None:
            return self.fallback_adapter.read_file(path, version)
        raise DwgImportError(
            DwgFailureCode.UNSUPPORTED_VERSION,
            f"Native AC1032 adapter does not handle {version.code} and has no fallback.",
            details={"adapter": self.name, "path": str(path)},
        )

    def _read_native(self, path: Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        # Pass the detected version code through so the navigator validates against
        # the actual container version (AC1032 or AC1027); the decode path is shared.
        table = read_r2018_entities(path.read_bytes(), version_code=version.code)
        if table.status != "decoded":
            raise DwgImportError(
                DwgFailureCode.ADAPTER_FAILED,
                f"Native AC1032 reader could not decode {path.name}: {table.status}",
                details={
                    "adapter": self.name,
                    "path": str(path),
                    "status": table.status,
                    "message": table.message,
                },
            )
        if not table.entities:
            raise DwgImportError(
                DwgFailureCode.NO_READABLE_ENTITIES,
                f"Native AC1032 reader decoded no entities from {path.name}.",
                details={"adapter": self.name, "path": str(path)},
            )
        # Separate real model-space content (entmode==2) from block-definition
        # geometry (entmode==0), grouping the latter under its owning block. This
        # both removes the block-local pollution (block geometry was emitted at
        # local coords in model space) and lets each model-space INSERT expand its
        # block at the correct transform. Anonymous *U/*D blocks stay distinct via
        # the unique-name map keyed by handle.
        unique_block_names = _build_unique_block_names(table.block_names)
        model_space: List[DwgAdapterEntity] = []
        block_entities: Dict[int, List[DwgAdapterEntity]] = defaultdict(list)
        for entity in table.entities:
            mapped = _adapter_entity(entity, unique_block_names)
            if entity.entmode == 0 and entity.owner_handle:
                block_entities[entity.owner_handle].append(mapped)
            else:
                model_space.append(mapped)
        blocks = [
            DwgAdapterBlock(
                name=unique_block_names.get(owner_handle) or f"BLK_{owner_handle:X}",
                entities=ents,
            )
            for owner_handle, ents in block_entities.items()
        ]
        all_mapped = model_space + [e for ents in block_entities.values() for e in ents]
        partial_summary = native_decode_partial_summary(all_mapped)
        return DwgAdapterDrawing(
            header={"$ACADVER": version.code},
            model_space=model_space,
            blocks=blocks,
            metadata={
                "adapter": self.name,
                "decoder": "native-ac1032-cleanroom",
                "decoded_entity_count": table.decoded_count,
                "type_counts": dict(table.type_counts),
                "model_space_entity_count": len(model_space),
                "block_definition_count": len(blocks),
                # No silent partial decode: types decoded structurally but not
                # geometrically mapped are surfaced so the UI/compare can badge them.
                **partial_summary,
            },
        )


__all__ = [
    "AC1032_NATIVE_OPT_IN_ENV",
    "DwgNativeAc1032Adapter",
    "ac1032_native_opt_in",
    "native_decode_partial_summary",
    "set_ac1032_native_opt_in",
]
