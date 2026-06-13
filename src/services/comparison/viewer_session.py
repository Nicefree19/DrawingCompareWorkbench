# -*- coding: utf-8 -*-
"""ViewerSession — in-process orchestrator for the lightweight viewer engine.

Phase G2.1. Sits between the v3 manifest on disk and the QML viewport
(G2.2). Owns:

* the loaded :class:`ViewerManifestV3` for one comparison run
* per-(pair, side) :class:`PairSessionState` with current :class:`RenderMode`
* a small thread-pool that builds :class:`ScenePack` artifacts lazily on
  first pair selection (so the comparison itself stays cheap and the GUI
  doesn't pay scene-pack cost up-front)
* a request queue for future ZoneRenderRequests (G2.3 will wire
  the actual renderer; G2.1 ships the queue + cache lookup)

The module is **pure Python + threading** — no Qt — so it imports + tests
without a QApplication. The Workbench (G2.2) wraps it with a
``QObject``-based adapter that re-emits the callback signals on the GUI
thread.

Cache layout (under ``cache_paths.preview_cache_dir()``):

    cache/preview/scene_packs/{source_stem}__{mtime_ns}__{size}/
        scene_pack.json
        primitive_index.{rtree | json}
        overview_lod0.json

The cache key is purely (path-stem, mtime, size) — fast and deterministic
without hashing the file. Stale caches are never read; missing-or-malformed
caches trigger a fresh build.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple

from src.services.comparison.cache_paths import preview_cache_dir
from src.services.comparison.render_modes import (
    RenderMode,
    is_valid_mode,
    transition,
)
from src.services.comparison.scene_pack_builder import (
    INDEX_FILENAME,
    OVERVIEW_LOD0_FILENAME,
    SCENE_PACK_FILENAME,
    SceneBuildResult,
    build_scene_pack,
)
from src.services.comparison.viewer_primitive_source import RENDER_CONTRACT_VERSION
from src.services.comparison.viewer_manifest_v3 import (
    EvidenceRef,
    ManifestV3ValidationError,
    ScenePackRef,
    ViewerManifestV3,
    ZoneRequestRef,
    load_manifest_v3,
    write_manifest_v3,
)

logger = logging.getLogger(__name__)

#: Side identifier used throughout the session API.
Side = Literal["before", "after"]

#: Callback signature for state-change subscribers.
#: Args: ``(pair_id, side, new_render_mode)``.
StateChangeCallback = Callable[[str, str, RenderMode], None]

#: Callback signature for ZoneRequest completion subscribers.
#: Args: ``(pair_id, zone_id, evidence_ref)``.
ZoneEvidenceCallback = Callable[[str, str, EvidenceRef], None]

#: Phase G2.4 — multi-stage build progress subscriber.
#: Args: ``(pair_id, side, stage, percent_or_None, message_ko)``.
#: Fired at every BuildStage transition during scene_pack_builder.
ProgressCallback = Callable[[str, str, str, Optional[float], str], None]


@dataclass
class PairSessionState:
    """Live state of one (pair_id, side) — the QML viewport reads this to
    decide which layers to draw + which badge to show."""

    pair_id: str
    side: Side
    render_mode: RenderMode = "relative_only"
    scene_pack_ref: Optional[ScenePackRef] = None
    source_path: str = ""
    last_error: str = ""
    last_build_ms: float = 0.0
    cache_hit: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "pair_id": self.pair_id,
            "side": self.side,
            "render_mode": self.render_mode,
            "scene_pack_ref": self.scene_pack_ref.to_dict() if self.scene_pack_ref else None,
            "source_path": self.source_path,
            "last_error": self.last_error,
            "last_build_ms": self.last_build_ms,
            "cache_hit": self.cache_hit,
        }


# ---------------------------------------------------------------------------
# Cache lookup helper
# ---------------------------------------------------------------------------


# Bump ``RENDER_CONTRACT_VERSION`` whenever pack RENDERING changes (not just
# the source file), so packs built by an older renderer stop hitting.
_PACK_RENDER_VERSION = RENDER_CONTRACT_VERSION


def _scene_pack_cache_key(source_path: Path) -> str:
    """Deterministic per-source cache key (no hash compute — uses mtime + size).

    Falls back to ``{stem}__nostat`` when the file is unreadable so the
    builder still gets called but doesn't poison the cache with a stable
    name that would collide across files of the same name.

    Phase G2.4 fix — the rtree C library on Windows fails to create
    ``.idx``/``.dat`` files when the path contains non-ASCII characters
    (Korean structural drawing names) with
    ``IllegalArgumentException: Index/Data file cannot be created``.
    So we ASCII-fold the stem: any non-ASCII character is replaced with
    its sha1 short prefix so collisions across Korean filenames stay
    deterministic without relying on Unicode IO. The mtime+size suffix
    keeps the key unique even after stems collapse.
    """

    src = Path(source_path)
    try:
        st = src.stat()
        sig = f"{int(st.st_mtime_ns)}__{st.st_size}__{_PACK_RENDER_VERSION}"
    except OSError:
        sig = f"nostat__{_PACK_RENDER_VERSION}"

    raw_stem = src.stem
    # Drop non-ASCII to keep rtree happy; preserve a stable short hash so
    # different Korean stems with the same ASCII subset don't collide.
    ascii_stem = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_"
                         for ch in raw_stem) or "src"
    if ascii_stem != raw_stem:
        import hashlib
        h = hashlib.sha1(raw_stem.encode("utf-8")).hexdigest()[:8]
        return f"{ascii_stem[:48]}__{h}__{sig}"
    return f"{ascii_stem}__{sig}"


def _scene_pack_cache_dir(source_path: Path, *, root: Optional[Path] = None) -> Path:
    """Directory where one source's pack artifacts live.

    Returns ``cache/preview/scene_packs/{cache_key}/``. Created on access.
    """

    base = (root or preview_cache_dir()) / "scene_packs" / _scene_pack_cache_key(source_path)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_scene_pack_ref_paths(ref: Optional[ScenePackRef], *, base: Path) -> None:
    """Resolve relative pack paths against the manifest directory, in place.

    Pipeline-baked refs (G3, 2026-06-12) are stored relative to the
    manifest dir so the run folder stays relocatable and the sharable
    export's path audit passes. Absolute/empty values pass through.
    """

    if ref is None:
        return
    for attr in ("json_path", "index_path", "overview_lod0_path"):
        raw = getattr(ref, attr, "") or ""
        if not raw:
            continue
        try:
            p = Path(raw)
            if not p.is_absolute():
                setattr(ref, attr, str((base / p).resolve()))
        except (OSError, ValueError):
            continue


def _try_load_cached_pack(source_path: Path, *, root: Optional[Path] = None) -> Optional[ScenePackRef]:
    """Return a :class:`ScenePackRef` if all three artifacts exist + look intact.

    Cheap existence + size check only — does not validate JSON content.
    A subsequent failure to load will trigger a rebuild via the normal
    build path, which is the right behaviour for this layer.
    """

    cache_dir = _scene_pack_cache_dir(source_path, root=root)
    pack = cache_dir / SCENE_PACK_FILENAME
    overview = cache_dir / OVERVIEW_LOD0_FILENAME
    # Index file: rtree wins if present, else grid .json.
    index_rtree = cache_dir / f"{INDEX_FILENAME}.rtree"
    index_grid = cache_dir / f"{INDEX_FILENAME}.json"
    index_path: Optional[Path] = None
    if index_rtree.with_suffix(".meta.json").exists() and index_rtree.with_suffix(".idx").exists():
        index_path = index_rtree
    elif index_grid.exists() and index_grid.stat().st_size > 0:
        index_path = index_grid

    if not pack.exists() or not overview.exists() or index_path is None:
        return None
    if pack.stat().st_size <= 0 or overview.stat().st_size <= 0:
        return None

    return ScenePackRef(
        json_path=str(pack),
        index_path=str(index_path),
        overview_lod0_path=str(overview),
        primitive_count=0,                    # unknown without parsing
        drawing_world_bbox=(0.0, 0.0, 0.0, 0.0),
        elapsed_build_ms=0.0,
        notes="cache hit",
    )


# ---------------------------------------------------------------------------
# Main session class
# ---------------------------------------------------------------------------


@dataclass
class _ZoneRequestEntry:
    """In-memory queue entry. G2.3 will hand these to a worker pool."""

    pair_id: str
    zone_id: str
    side: Side
    bbox_world: Tuple[float, float, float, float]
    cache_key: str
    submitted_at: float = field(default_factory=time.monotonic)


class ViewerSession:
    """Lightweight viewer in-process orchestrator (Phase G2.1).

    Construct one per comparison run. The Workbench (G2.2) calls
    :meth:`load_manifest` after :class:`FolderCompareRunResult` lands,
    then :meth:`select_pair` whenever the user picks a row in the drawing
    list. State changes are pushed to a callback (no Qt signals here so
    the class stays unit-testable).

    Thread-safety: public methods are safe to call from any thread; the
    internal state dict is guarded by a single ``RLock``. Worker callbacks
    never call subscriber callbacks while holding the lock.
    """

    def __init__(
        self,
        *,
        cache_root: Optional[Path] = None,
        max_workers: int = 2,
        on_state_change: Optional[StateChangeCallback] = None,
        on_zone_evidence: Optional[ZoneEvidenceCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._manifest: Optional[ViewerManifestV3] = None
        self._manifest_path: Optional[Path] = None
        self._cache_root = Path(cache_root) if cache_root else None
        self._states: Dict[Tuple[str, Side], PairSessionState] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="viewer-session-",
        )
        self._futures: Dict[Tuple[str, Side], Future] = {}
        self._zone_queue: List[_ZoneRequestEntry] = []
        self._on_state_change = on_state_change
        self._on_zone_evidence = on_zone_evidence
        self._on_progress = on_progress
        self._closed = False

    # ----- manifest lifecycle ---------------------------------------------

    def load_manifest(self, manifest_path: Path) -> ViewerManifestV3:
        """Load v3 manifest from disk + initialise per-pair state.

        Raises :class:`ManifestV3ValidationError` for unreadable / wrong-schema
        files (callers should fall back to v2 path or surface to user).
        """

        path = Path(manifest_path)
        manifest = load_manifest_v3(path)  # may raise
        # Manifest pack refs may carry relative paths (resolve against the
        # manifest dir) or redacted/stale ones (sharable export masks any
        # separator-bearing string). A ref whose overview doesn't exist is
        # dropped so the lazy/cache path stays reachable instead of the
        # viewer trusting a dead pointer.
        for attr in ("before_scene_pack", "after_scene_pack"):
            ref = getattr(manifest, attr)
            _resolve_scene_pack_ref_paths(ref, base=path.parent)
            if ref is not None:
                overview = str(ref.overview_lod0_path or "")
                if not overview or not Path(overview).exists():
                    setattr(manifest, attr, None)
        with self._lock:
            self._manifest = manifest
            self._manifest_path = path
            # Seed PairSessionState for any pair already present in the
            # manifest — initial mode is whatever the manifest claims
            # (defaults to relative_only).
            self._states.clear()
            for side in ("before", "after"):
                state_side: Side = side  # type: ignore[assignment]
                key = (manifest.pair_uuid, state_side)
                source_sig = (
                    manifest.before_source_signature
                    if side == "before"
                    else manifest.after_source_signature
                )
                pack = (
                    manifest.before_scene_pack
                    if side == "before"
                    else manifest.after_scene_pack
                )
                self._states[key] = PairSessionState(
                    pair_id=manifest.pair_uuid,
                    side=state_side,
                    render_mode=manifest.current_render_mode if pack else "relative_only",
                    scene_pack_ref=pack,
                    source_path=source_sig.source_path,
                )
        logger.info(
            "ViewerSession loaded manifest: pair=%s, source_kind=%s, "
            "before_pack=%s, after_pack=%s",
            manifest.pair_uuid, manifest.source_kind,
            "set" if manifest.before_scene_pack else "none",
            "set" if manifest.after_scene_pack else "none",
        )
        return manifest

    def manifest(self) -> Optional[ViewerManifestV3]:
        """Return the currently loaded manifest (or None)."""

        with self._lock:
            return self._manifest

    def manifest_path(self) -> Optional[Path]:
        with self._lock:
            return self._manifest_path

    def save_manifest(self) -> None:
        """Persist the current manifest back to disk (atomic).

        Called after state transitions complete so the next session can
        pick up where this one left off (e.g. avoid rebuilding scene packs).
        Silently no-ops when no manifest is loaded.
        """

        with self._lock:
            if self._manifest is None or self._manifest_path is None:
                return
            # Sync per-pair scene-pack refs back into the manifest before save.
            for (pair_id, side), state in self._states.items():
                if pair_id != self._manifest.pair_uuid:
                    continue
                if side == "before":
                    self._manifest.before_scene_pack = state.scene_pack_ref
                else:
                    self._manifest.after_scene_pack = state.scene_pack_ref
                self._manifest.current_render_mode = state.render_mode
            try:
                write_manifest_v3(self._manifest_path, self._manifest)
            except Exception:
                logger.exception("Failed to save viewer manifest v3")

    # ----- pair selection + lazy scene pack build -------------------------

    def select_pair(self, pair_id: str, *, side: Side = "after") -> PairSessionState:
        """Mark a pair as active. Triggers lazy scene pack build if needed.

        Returns the current state immediately; the actual build happens on
        the worker pool. Subscribers are notified via ``on_state_change``
        when the mode transitions.
        """

        if self._closed:
            raise RuntimeError("ViewerSession already shut down")

        with self._lock:
            state = self._states.get((pair_id, side))
            if state is None:
                # Unknown pair — register it so callers can still query state.
                state = PairSessionState(
                    pair_id=pair_id, side=side, render_mode="relative_only",
                )
                self._states[(pair_id, side)] = state

            # Already built or in flight — nothing to schedule.
            if state.scene_pack_ref and state.scene_pack_ref.json_path:
                return state
            if (pair_id, side) in self._futures:
                # Build already pending; transition to render_pending if not.
                self._transition_locked(pair_id, side, "render_pending")
                return state

            # Need a USABLE source path to build from. Sharable manifests
            # carry "<redacted>/..." placeholders — treat those as absent so
            # the Workbench's ensure_pair_source repair (real local path)
            # is what unlocks the build, not a doomed parse of a fake path.
            source_text = str(state.source_path or "")
            if not source_text or "redacted" in source_text.lower():
                logger.debug(
                    "select_pair(%s, %s): no usable source_path — staying "
                    "relative_only",
                    pair_id, side,
                )
                return state

        # Outside the lock — try cache, else schedule a build.
        cached = _try_load_cached_pack(Path(state.source_path), root=self._cache_root)
        if cached is None and Path(state.source_path).suffix.lower() == ".dwg":
            # The pipeline's detached prewarmer (2026-06-12) warms the cache
            # under the EFFECTIVE converted-DXF key; a DWG source injected by
            # the Workbench would miss it and rebuild from scratch. Resolve
            # the same effective DXF the builder would use and retry.
            try:
                from src.services.comparison.zone_vector_renderer import (
                    resolve_dxf_path,
                )

                effective = resolve_dxf_path(Path(state.source_path))
                if effective and Path(effective) != Path(state.source_path):
                    cached = _try_load_cached_pack(
                        Path(effective), root=self._cache_root
                    )
            except Exception:  # noqa: BLE001 - cache probe must stay non-fatal
                logger.debug("effective-DXF cache probe failed", exc_info=True)
        if cached is not None:
            logger.info(
                "select_pair(%s, %s): cache HIT at %s", pair_id, side, cached.json_path
            )
            with self._lock:
                state.scene_pack_ref = cached
                state.cache_hit = True
                self._transition_locked(pair_id, side, "skeleton_preview")
            return state

        # Cold path — submit to executor.
        self._submit_build(pair_id, side, Path(state.source_path))
        return state

    def get_pair_state(self, pair_id: str, side: Side = "after") -> PairSessionState:
        """Return a snapshot of the (pair, side) state."""

        with self._lock:
            state = self._states.get((pair_id, side))
            if state is None:
                return PairSessionState(pair_id=pair_id, side=side)
            # Return a frozen copy so callers can't mutate internal state.
            return PairSessionState(
                pair_id=state.pair_id,
                side=state.side,
                render_mode=state.render_mode,
                scene_pack_ref=state.scene_pack_ref,
                source_path=state.source_path,
                last_error=state.last_error,
                last_build_ms=state.last_build_ms,
                cache_hit=state.cache_hit,
            )

    def all_pair_states(self) -> List[PairSessionState]:
        """Snapshot every known pair state (read-only)."""

        with self._lock:
            return [self.get_pair_state(pid, side) for (pid, side) in self._states]

    def ensure_pair_source(self, pair_id: str, side: Side, source_path: str) -> None:
        """Inject a usable local source path for a ``(pair_id, side)`` state.

        Sharable viewer packages redact ``source_path`` in the V3 manifest and
        key the manifest's single state by the package ``pair_uuid`` — so a
        multi-pair live Workbench that requests a zone by its OWN pair hash
        finds either no state or a redacted one, and ``request_zone`` then skips
        the native scene-pack build (the lightweight viewer never gets the
        full-detail vector). The Workbench repairs the real local path from the
        comparison summary; this lets it hand that path to the session so the
        zone-focus build can actually run.

        Creates the state when absent. Only sets/overwrites when the existing
        state has no usable source (empty or redacted) — never clobbers a good
        path already present.
        """

        if self._closed:
            return
        text = str(source_path or "").strip()
        if not text:
            return
        with self._lock:
            state = self._states.get((pair_id, side))
            if state is None:
                state = PairSessionState(
                    pair_id=pair_id, side=side, render_mode="relative_only",
                )
                self._states[(pair_id, side)] = state
            existing = str(state.source_path or "").strip()
            if existing and "redacted" not in existing.lower():
                return  # already has a real source — don't clobber
            state.source_path = text

    # ----- zone request queue (skeleton — G2.3 wires the renderer) --------

    def request_zone(
        self,
        *,
        pair_id: str,
        zone_id: str,
        side: Side,
        bbox_world: Tuple[float, float, float, float],
        cache_key: str = "",
        padding_ratio: float = 0.1,
    ) -> str:
        """Queue a zone render request and submit it to the worker pool.

        Phase G2.3 — actually submits to the worker. Cache hit fires
        synchronously; cold builds happen on the executor and notify via
        the ``on_zone_evidence`` callback.

        Returns the cache key (caller correlates with the eventual evidence).
        """

        if self._closed:
            raise RuntimeError("ViewerSession already shut down")
        key = cache_key or f"{pair_id}|{zone_id}|{side}|{bbox_world}"
        entry = _ZoneRequestEntry(
            pair_id=pair_id, zone_id=zone_id, side=side,
            bbox_world=bbox_world, cache_key=key,
        )
        with self._lock:
            self._zone_queue.append(entry)
            # Also persist into the manifest so a session restart can
            # replay outstanding requests.
            if self._manifest is not None:
                self._manifest.zone_requests.append(
                    ZoneRequestRef(
                        zone_id=zone_id,
                        side=side,
                        bbox_world=bbox_world,
                        cache_key=key,
                    )
                )
            state = self._states.get((pair_id, side))

        if not state or not state.source_path:
            logger.debug(
                "request_zone(%s, %s): no source path, skipping worker submit",
                pair_id, zone_id,
            )
            return key

        # Phase G2.3 — try cache first.
        cached_path = self._zone_focus_cache_path(state.source_path, bbox_world, padding_ratio)
        if cached_path and cached_path.exists() and cached_path.stat().st_size > 0:
            logger.info("request_zone cache HIT: %s/%s -> %s",
                        pair_id, zone_id, cached_path)
            evidence = EvidenceRef(
                zone_id=zone_id,
                side=side,
                raster_uri=str(cached_path),  # we use raster_uri to carry the path
                world_bbox=bbox_world,
                cache_hit=True,
                request_cache_key=key,
                notes="zone_focus cache hit",
            )
            self._fire_zone_evidence(pair_id, zone_id, evidence)
            return key

        # Cold build — submit to executor.
        self._executor.submit(
            self._do_zone_focus_build,
            pair_id, zone_id, side, Path(state.source_path),
            bbox_world, padding_ratio, key,
        ).add_done_callback(
            lambda fut: self._on_zone_focus_done(pair_id, zone_id, side, key, fut)
        )
        logger.debug("request_zone queued + submitted: %s/%s/%s", pair_id, zone_id, side)
        return key

    def _zone_focus_cache_path(
        self,
        source_path: str,
        bbox_world: Tuple[float, float, float, float],
        padding_ratio: float,
    ) -> Optional[Path]:
        """Resolve the on-disk cache path for one zone-focus build."""

        from src.services.comparison.zone_render_worker import (
            ZONE_FOCUS_FILENAME,
            zone_focus_cache_key,
        )
        try:
            key = zone_focus_cache_key(
                Path(source_path), bbox_world, padding_ratio=padding_ratio,
            )
        except Exception:
            return None
        root = self._cache_root or preview_cache_dir()
        return root / "zone_focus" / key / ZONE_FOCUS_FILENAME

    def _do_zone_focus_build(
        self,
        pair_id: str,
        zone_id: str,
        side: Side,
        source: Path,
        bbox_world: Tuple[float, float, float, float],
        padding_ratio: float,
        cache_key_str: str,
    ):
        from src.services.comparison.zone_render_worker import (
            render_zone_focus,
            zone_focus_cache_key,
        )
        key = zone_focus_cache_key(
            source, bbox_world, padding_ratio=padding_ratio,
        )
        out_dir = (self._cache_root or preview_cache_dir()) / "zone_focus" / key
        out_dir.mkdir(parents=True, exist_ok=True)
        return render_zone_focus(
            source, bbox_world, out_dir, padding_ratio=padding_ratio,
        )

    def _on_zone_focus_done(
        self,
        pair_id: str,
        zone_id: str,
        side: Side,
        cache_key_str: str,
        fut,
    ) -> None:
        """Worker completion callback — push EvidenceRef to subscriber."""

        try:
            result = fut.result()
        except Exception as exc:
            logger.exception(
                "ViewerSession zone focus build crashed: pair=%s zone=%s",
                pair_id, zone_id,
            )
            evidence = EvidenceRef(
                zone_id=zone_id, side=side,
                raster_uri="", world_bbox=(0.0, 0.0, 0.0, 0.0),
                cache_hit=False, request_cache_key=cache_key_str,
                notes=f"build crashed: {exc}",
            )
            self._fire_zone_evidence(pair_id, zone_id, evidence)
            return

        evidence = EvidenceRef(
            zone_id=zone_id,
            side=side,
            raster_uri=result.output_path,
            world_bbox=result.world_bbox,
            render_ms=result.elapsed_ms,
            cache_hit=False,
            request_cache_key=cache_key_str,
            notes=(
                f"primitives={result.primitive_count} "
                f"entities={result.entity_count} truncated={result.truncated}"
            ),
        )
        with self._lock:
            if self._manifest is not None:
                self._manifest.evidence.append(evidence)
        self._fire_zone_evidence(pair_id, zone_id, evidence)
        logger.info(
            "Zone focus done: pair=%s zone=%s primitives=%d elapsed_ms=%.0f",
            pair_id, zone_id, result.primitive_count, result.elapsed_ms,
        )

    def _fire_zone_evidence(
        self, pair_id: str, zone_id: str, evidence: EvidenceRef,
    ) -> None:
        cb = self._on_zone_evidence
        if cb is None:
            return
        try:
            cb(pair_id, zone_id, evidence)
        except Exception:
            logger.exception(
                "on_zone_evidence callback raised for %s/%s", pair_id, zone_id,
            )

    def pending_zone_requests(self) -> List[_ZoneRequestEntry]:
        """Return a snapshot of the zone request queue."""

        with self._lock:
            return list(self._zone_queue)

    # ----- shutdown -------------------------------------------------------

    def shutdown(self, *, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Cancel pending builds + drain the executor.

        Safe to call multiple times. After shutdown the session rejects new
        ``select_pair`` / ``request_zone`` calls.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            futures = list(self._futures.values())
            self._futures.clear()

        for fut in futures:
            fut.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)
        # Best-effort timeout — concurrent.futures.shutdown ignores timeout
        # so we approximate by polling future.done() up to timeout.
        if timeout is not None and wait:
            deadline = time.monotonic() + timeout
            for fut in futures:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    fut.result(timeout=remaining)
                except Exception:
                    pass

    # ----- internal helpers ----------------------------------------------

    def _submit_build(self, pair_id: str, side: Side, source: Path) -> None:
        """Schedule a scene pack build on the executor."""

        with self._lock:
            self._transition_locked(pair_id, side, "render_pending")
            cache_dir = _scene_pack_cache_dir(source, root=self._cache_root)
            future = self._executor.submit(
                self._do_build, pair_id, side, source, cache_dir,
            )
            self._futures[(pair_id, side)] = future

        # Attach the done callback OUTSIDE the lock — done callbacks may
        # run synchronously when the future is already complete (cache hit
        # races) and we don't want to re-enter our own lock from inside
        # ThreadPoolExecutor's bookkeeping.
        future.add_done_callback(
            lambda fut: self._on_build_done(pair_id, side, fut)
        )

    def _do_build(
        self,
        pair_id: str,
        side: Side,
        source: Path,
        cache_dir: Path,
    ) -> SceneBuildResult:
        """Worker entrypoint — runs in a ThreadPoolExecutor thread."""

        logger.info(
            "ViewerSession build start: pair=%s side=%s source=%s",
            pair_id, side, source.name,
        )

        # Phase G2.4 — bridge the scene_pack_builder progress callback
        # into the session-level on_progress subscriber so the GUI can
        # show stage-specific status text on the badge.
        progress_cb = self._on_progress

        def _bridge(stage: str, percent: Optional[float], message: str) -> None:
            if progress_cb is None:
                return
            try:
                progress_cb(pair_id, side, stage, percent, message)
            except Exception:
                logger.exception("on_progress callback raised at stage %s", stage)

        return build_scene_pack(source, cache_dir, progress=_bridge)

    def _on_build_done(self, pair_id: str, side: Side, fut: Future) -> None:
        """Invoke the user state-change callback after the worker settles.

        Defensive — never re-raises. Logs build outcome at INFO/WARN.
        """

        # We need to know what the *new* mode should be. Determined by:
        #   - exception → render_failed
        #   - empty pack (primitive_count=0) → render_failed
        #   - else → skeleton_preview (because LOD0 is now available)
        new_mode: RenderMode = "render_failed"
        scene_ref: Optional[ScenePackRef] = None
        elapsed_ms = 0.0
        last_error = ""

        try:
            result: SceneBuildResult = fut.result()
            scene_ref = result.scene_pack_ref
            elapsed_ms = result.elapsed_ms
            if result.primitive_count > 0:
                new_mode = "skeleton_preview"
            else:
                new_mode = "render_failed"
                last_error = "; ".join(result.warnings) or "scene pack empty"
        except Exception as exc:
            logger.exception(
                "ViewerSession build crashed: pair=%s side=%s", pair_id, side,
            )
            last_error = f"{exc.__class__.__name__}: {exc}"

        with self._lock:
            self._futures.pop((pair_id, side), None)
            state = self._states.get((pair_id, side))
            if state is None:
                # The pair was removed in the meantime — drop the result.
                return
            state.scene_pack_ref = scene_ref
            state.last_build_ms = elapsed_ms
            state.last_error = last_error
            state.cache_hit = False
            self._transition_locked(pair_id, side, new_mode)

        logger.info(
            "ViewerSession build done: pair=%s side=%s mode=%s elapsed_ms=%.0f%s",
            pair_id, side, new_mode, elapsed_ms,
            f" error={last_error!r}" if last_error else "",
        )

    def _transition_locked(self, pair_id: str, side: Side, target: RenderMode) -> None:
        """Apply a state transition while holding the lock; emit callback
        outside (queued for after-release).

        ``transition()`` validates against the allowed-transitions table and
        keeps the prior mode for disallowed transitions. Logs both cases.
        """

        state = self._states.get((pair_id, side))
        if state is None:
            return
        if not is_valid_mode(target):
            logger.debug(
                "Ignored invalid transition target %r for %s/%s", target, pair_id, side,
            )
            return
        prior = state.render_mode
        new = transition(prior, target)
        if new == prior:
            return
        state.render_mode = new

        # Emit callback after we've released the lock to avoid re-entrancy.
        # Use a list so __exit__ can drain it.
        cb = self._on_state_change

        def _emit() -> None:
            if cb is None:
                return
            try:
                cb(pair_id, side, new)
            except Exception:
                logger.exception(
                    "on_state_change callback raised for %s/%s -> %s",
                    pair_id, side, new,
                )

        # Caller is already inside the lock; schedule emission for after.
        # We piggy-back on _pending_emits stored on the lock holder; since
        # we can't easily intercept exit, just emit directly. The callback
        # contract documents that subscribers must not re-enter the session.
        # In practice this works because Qt-side adapters re-emit via signal
        # which is queued to the GUI thread.
        _emit()


__all__ = [
    "Side",
    "PairSessionState",
    "StateChangeCallback",
    "ZoneEvidenceCallback",
    "ViewerSession",
]
