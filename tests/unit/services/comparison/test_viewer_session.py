# -*- coding: utf-8 -*-
"""Unit tests for ViewerSession (Phase G2.1)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List, Tuple

import pytest

ezdxf = pytest.importorskip("ezdxf")

from src.services.comparison.render_modes import RenderMode
from src.services.comparison.viewer_manifest_v3 import (
    ScenePackRef,
    SourceSignature,
    ViewerManifestV3,
    write_manifest_v3,
)
from src.services.comparison.viewer_session import (
    PairSessionState,
    ViewerSession,
    _scene_pack_cache_key,
    _try_load_cached_pack,
)


def _make_sample_dxf(path: Path) -> None:
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))
    msp.add_line((10, 10), (20, 0))
    msp.add_circle((5, 5), 2)
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)])
    doc.saveas(str(path))


def _make_sample_manifest(
    pair_id: str,
    after_source: str,
    *,
    before_source: str = "",
) -> ViewerManifestV3:
    return ViewerManifestV3(
        pair_uuid=pair_id,
        package_version="phaseG2.1-test",
        source_kind="normalized_dxf",
        before_source_signature=SourceSignature(source_path=before_source),
        after_source_signature=SourceSignature(source_path=after_source),
        before_world_bbox=(0.0, 0.0, 100.0, 100.0),
        after_world_bbox=(0.0, 0.0, 100.0, 100.0),
        shared_world_bbox=(0.0, 0.0, 100.0, 100.0),
    )


# ---------------------------------------------------------------------------
# Cache key helpers (pure functions, no I/O)
# ---------------------------------------------------------------------------


def test_cache_key_uses_mtime_and_size(tmp_path: Path) -> None:
    p = tmp_path / "a.dxf"
    p.write_text("x", encoding="utf-8")
    k1 = _scene_pack_cache_key(p)
    assert "a__" in k1
    # Same file, same mtime → same key
    assert _scene_pack_cache_key(p) == k1
    # Modify file → key changes (size differs)
    time.sleep(0.01)
    p.write_text("xy", encoding="utf-8")
    k2 = _scene_pack_cache_key(p)
    assert k2 != k1


def test_cache_key_handles_missing_file(tmp_path: Path) -> None:
    k = _scene_pack_cache_key(tmp_path / "absent.dxf")
    assert "nostat" in k


def test_cache_key_includes_render_version(tmp_path: Path) -> None:
    """Packs built by an older renderer must miss after a version bump.

    Without this, rendering fixes (e.g. the 2026-06-12 Korean text-style
    font remap) never reach sources whose mtime+size didn't change.
    """
    from src.services.comparison.viewer_session import _PACK_RENDER_VERSION

    p = tmp_path / "a.dxf"
    p.write_text("x", encoding="utf-8")
    assert _scene_pack_cache_key(p).endswith(f"__{_PACK_RENDER_VERSION}")
    assert _scene_pack_cache_key(tmp_path / "absent.dxf").endswith(
        f"__{_PACK_RENDER_VERSION}"
    )


def test_try_load_cached_pack_returns_none_when_missing(tmp_path: Path) -> None:
    src = tmp_path / "a.dxf"
    src.write_text("x", encoding="utf-8")
    assert _try_load_cached_pack(src, root=tmp_path / "cache") is None


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------


def test_load_manifest_seeds_per_side_state(tmp_path: Path) -> None:
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    manifest = _make_sample_manifest(
        "pair-1", str(src), before_source=str(src),
    )
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        loaded = session.load_manifest(mpath)
        assert loaded.pair_uuid == "pair-1"
        before_state = session.get_pair_state("pair-1", "before")
        after_state = session.get_pair_state("pair-1", "after")
        assert before_state.source_path == str(src)
        assert after_state.source_path == str(src)
        assert before_state.render_mode == "relative_only"
        assert after_state.render_mode == "relative_only"
    finally:
        session.shutdown()


def test_load_manifest_reflects_existing_scene_pack_ref(tmp_path: Path) -> None:
    """If the manifest already carries a ScenePackRef, the session should
    promote the initial state to whatever the manifest's current_render_mode is."""

    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    # 2026-06-12: refs are only trusted when the overview actually exists
    # (sharable redaction / stale manifests carried dead pointers).
    (tmp_path / "overview_lod0.json").write_text(
        '{"primitives": [], "world_bbox": [0,0,1,1]}', encoding="utf-8"
    )
    pack = ScenePackRef(
        json_path=str(tmp_path / "scene_pack.json"),
        index_path=str(tmp_path / "primitive_index.json"),
        overview_lod0_path=str(tmp_path / "overview_lod0.json"),
        primitive_count=10,
    )
    manifest = _make_sample_manifest("pair-1", str(src))
    manifest.after_scene_pack = pack
    manifest.current_render_mode = "skeleton_preview"
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        session.load_manifest(mpath)
        st = session.get_pair_state("pair-1", "after")
        assert st.scene_pack_ref is not None
        assert st.render_mode == "skeleton_preview"
    finally:
        session.shutdown()


# ---------------------------------------------------------------------------
# select_pair — triggers lazy build
# ---------------------------------------------------------------------------


def test_select_pair_triggers_build_and_transitions_to_skeleton(tmp_path: Path) -> None:
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    manifest = _make_sample_manifest("pair-1", str(src))
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    transitions: List[Tuple[str, str, RenderMode]] = []
    done_event = threading.Event()

    def _cb(pair_id: str, side: str, mode: RenderMode) -> None:
        transitions.append((pair_id, side, mode))
        if mode in {"skeleton_preview", "render_failed"}:
            done_event.set()

    session = ViewerSession(
        cache_root=tmp_path / "cache",
        max_workers=1,
        on_state_change=_cb,
    )
    try:
        session.load_manifest(mpath)
        st = session.select_pair("pair-1", side="after")
        # State immediately after select_pair: render_pending (worker submitted)
        assert st.render_mode in {"render_pending", "skeleton_preview"}
        assert done_event.wait(timeout=30.0), "build never completed"
        final = session.get_pair_state("pair-1", "after")
        assert final.render_mode == "skeleton_preview"
        assert final.scene_pack_ref is not None
        assert final.scene_pack_ref.primitive_count > 0
    finally:
        session.shutdown()

    # The transition stream should include render_pending → skeleton_preview.
    modes = [t[2] for t in transitions if t[0] == "pair-1" and t[1] == "after"]
    assert "skeleton_preview" in modes


def test_select_pair_with_missing_source_stays_relative_only(tmp_path: Path) -> None:
    manifest = _make_sample_manifest("pair-1", "")  # empty source
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        session.load_manifest(mpath)
        st = session.select_pair("pair-1", side="after")
        assert st.render_mode == "relative_only"
        assert st.scene_pack_ref is None
    finally:
        session.shutdown()


def test_select_pair_unknown_pair_creates_default_state(tmp_path: Path) -> None:
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        st = session.select_pair("not-in-manifest", side="after")
        assert st.pair_id == "not-in-manifest"
        assert st.render_mode == "relative_only"
    finally:
        session.shutdown()


# ---------------------------------------------------------------------------
# ensure_pair_source — inject a usable local source for a GUI pair_id that the
# redacted, package-keyed V3 manifest does not cover.
# ---------------------------------------------------------------------------


def test_ensure_pair_source_seeds_unknown_pair_state(tmp_path: Path) -> None:
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        session.ensure_pair_source("gui-pair", "after", "/local/after.dxf")
        assert session.get_pair_state("gui-pair", "after").source_path == "/local/after.dxf"
    finally:
        session.shutdown()


def test_ensure_pair_source_overwrites_redacted_but_keeps_real(tmp_path: Path) -> None:
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    # Manifest seeds a REDACTED source under the package pair_uuid.
    manifest = _make_sample_manifest("pkg", "<redacted>/after.dxf")
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        session.load_manifest(mpath)
        # redacted -> overwritten with the real local path
        session.ensure_pair_source("pkg", "after", str(src))
        assert session.get_pair_state("pkg", "after").source_path == str(src)
        # real path present -> NOT clobbered by a later (different) call
        session.ensure_pair_source("pkg", "after", "/other/x.dxf")
        assert session.get_pair_state("pkg", "after").source_path == str(src)
    finally:
        session.shutdown()


def test_ensure_pair_source_ignores_empty(tmp_path: Path) -> None:
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        session.ensure_pair_source("gui-pair", "after", "")
        assert session.get_pair_state("gui-pair", "after").source_path == ""
    finally:
        session.shutdown()


def test_request_zone_builds_only_after_ensure_pair_source(tmp_path: Path) -> None:
    """The ⑤ fix end-to-end: request_zone for a GUI pair_id absent from the
    package-keyed manifest submits NO build (no source) until
    ensure_pair_source hands it a real local source — then the native
    zone-focus build runs and fires evidence (which the Workbench turns into
    the lightweight full-detail vector)."""
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)

    evidence_seen: List[Tuple[str, str]] = []
    done = threading.Event()

    def _ev(pair_id: str, zone_id: str, evidence) -> None:
        evidence_seen.append((pair_id, zone_id))
        done.set()

    session = ViewerSession(
        cache_root=tmp_path / "cache", max_workers=1, on_zone_evidence=_ev,
    )
    try:
        bbox = (0.0, 0.0, 20.0, 20.0)
        # No source for this GUI pair -> request_zone is a no-op build.
        session.request_zone(pair_id="gui-pair", zone_id="z1", side="after", bbox_world=bbox)
        assert not done.wait(timeout=2.0), "must not build without a usable source"
        # Inject the repaired local source -> the native build now runs.
        session.ensure_pair_source("gui-pair", "after", str(src))
        session.request_zone(pair_id="gui-pair", zone_id="z1", side="after", bbox_world=bbox)
        assert done.wait(timeout=30.0), "zone build never fired evidence after ensure_pair_source"
        assert ("gui-pair", "z1") in evidence_seen
    finally:
        session.shutdown()


# ---------------------------------------------------------------------------
# Cache reuse — second select_pair on the same source skips build
# ---------------------------------------------------------------------------


def test_cache_reuse_skips_second_build(tmp_path: Path) -> None:
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    manifest = _make_sample_manifest("pair-1", str(src))
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    cache_root = tmp_path / "cache"
    done_event = threading.Event()

    def _cb(pair_id: str, side: str, mode: RenderMode) -> None:
        if mode == "skeleton_preview":
            done_event.set()

    # First session: cold build.
    s1 = ViewerSession(cache_root=cache_root, max_workers=1, on_state_change=_cb)
    try:
        s1.load_manifest(mpath)
        s1.select_pair("pair-1", side="after")
        assert done_event.wait(timeout=30.0)
    finally:
        s1.shutdown()

    # Second session — same cache root, same source. Should hit cache + skip build.
    done_event.clear()
    s2 = ViewerSession(cache_root=cache_root, max_workers=1, on_state_change=_cb)
    try:
        s2.load_manifest(mpath)
        st = s2.select_pair("pair-1", side="after")
        # Cache hit path is synchronous → state should already be skeleton_preview.
        assert st.cache_hit is True or st.render_mode == "skeleton_preview"
        # Even if the callback path was async, the final state is correct.
        if st.render_mode != "skeleton_preview":
            assert done_event.wait(timeout=10.0)
        final = s2.get_pair_state("pair-1", "after")
        assert final.render_mode == "skeleton_preview"
        assert final.scene_pack_ref is not None
    finally:
        s2.shutdown()


# ---------------------------------------------------------------------------
# Zone request queue
# ---------------------------------------------------------------------------


def test_request_zone_appends_to_queue_and_manifest(tmp_path: Path) -> None:
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    manifest = _make_sample_manifest("pair-1", str(src))
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    try:
        session.load_manifest(mpath)
        key = session.request_zone(
            pair_id="pair-1", zone_id="Z-1", side="after",
            bbox_world=(10.0, 10.0, 20.0, 20.0),
        )
        assert key
        pending = session.pending_zone_requests()
        assert len(pending) == 1
        assert pending[0].zone_id == "Z-1"
        assert pending[0].cache_key == key
        # Manifest should also have a ZoneRequestRef recorded.
        m = session.manifest()
        assert m is not None
        assert any(z.zone_id == "Z-1" for z in m.zone_requests)
    finally:
        session.shutdown()


def test_request_zone_after_shutdown_raises(tmp_path: Path) -> None:
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    session.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        session.request_zone(
            pair_id="p", zone_id="z", side="after",
            bbox_world=(0.0, 0.0, 1.0, 1.0),
        )


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    session.shutdown()
    session.shutdown()  # second call: no-op


def test_select_pair_after_shutdown_raises(tmp_path: Path) -> None:
    session = ViewerSession(cache_root=tmp_path / "cache", max_workers=1)
    session.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        session.select_pair("pair-1", side="after")


# ---------------------------------------------------------------------------
# save_manifest
# ---------------------------------------------------------------------------


def test_save_manifest_persists_scene_pack_back(tmp_path: Path) -> None:
    src = tmp_path / "after.dxf"
    _make_sample_dxf(src)
    manifest = _make_sample_manifest("pair-1", str(src))
    mpath = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(mpath, manifest)

    done = threading.Event()

    def _cb(pair_id: str, side: str, mode: RenderMode) -> None:
        if mode == "skeleton_preview":
            done.set()

    session = ViewerSession(
        cache_root=tmp_path / "cache", max_workers=1, on_state_change=_cb,
    )
    try:
        session.load_manifest(mpath)
        session.select_pair("pair-1", side="after")
        assert done.wait(timeout=30.0)
        session.save_manifest()
    finally:
        session.shutdown()

    # Reload from disk and verify the scene_pack ref made it.
    from src.services.comparison.viewer_manifest_v3 import load_manifest_v3
    reloaded = load_manifest_v3(mpath)
    assert reloaded.after_scene_pack is not None
    assert reloaded.current_render_mode == "skeleton_preview"


def test_load_manifest_resolves_relative_scene_pack_paths(tmp_path: Path) -> None:
    """G3 (2026-06-12): pipeline-baked packs carry manifest-relative paths;
    the session must resolve them so the GUI's seeded state points at real
    files (relocatable run dirs + sharable path audit)."""

    from src.services.comparison.viewer_manifest_v3 import (
        ScenePackRef,
        ViewerManifestV3,
        write_manifest_v3,
    )
    from src.services.comparison.viewer_session import ViewerSession

    pack_dir = tmp_path / "scene_packs" / "after"
    pack_dir.mkdir(parents=True)
    overview = pack_dir / "overview_lod0.json"
    overview.write_text('{"primitives": [], "world_bbox": [0,0,1,1]}',
                        encoding="utf-8")

    manifest = ViewerManifestV3(
        pair_uuid="pair_rel77",
        package_version="g3",
        source_kind="normalized_dxf",
        after_scene_pack=ScenePackRef(
            json_path="scene_packs/after/scene_pack.json",
            overview_lod0_path="scene_packs/after/overview_lod0.json",
            primitive_count=0,
        ),
    )
    path = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(path, manifest)

    session = ViewerSession(max_workers=1)
    try:
        loaded = session.load_manifest(path)
        ref = loaded.after_scene_pack
        assert ref is not None
        assert Path(ref.overview_lod0_path).is_absolute()
        assert Path(ref.overview_lod0_path).exists()
        state = session.get_pair_state("pair_rel77", "after")
        assert state.scene_pack_ref is not None
        assert Path(state.scene_pack_ref.overview_lod0_path).exists()
    finally:
        session.shutdown() if hasattr(session, "shutdown") else None


def test_load_manifest_drops_dead_or_redacted_pack_refs(tmp_path: Path) -> None:
    """2026-06-12: sharable redaction masks separator-bearing strings, so a
    manifest can carry "<redacted>/..." (or otherwise stale) pack paths.
    Trusting them froze the viewer on a dead pointer; they must be dropped
    so the lazy/cache build path stays reachable."""

    from src.services.comparison.viewer_manifest_v3 import (
        ScenePackRef,
        ViewerManifestV3,
        write_manifest_v3,
    )
    from src.services.comparison.viewer_session import ViewerSession

    manifest = ViewerManifestV3(
        pair_uuid="pair_dead",
        package_version="g3",
        source_kind="normalized_dxf",
        after_scene_pack=ScenePackRef(
            overview_lod0_path="<redacted>/overview_lod0.json",
            primitive_count=10,
        ),
    )
    path = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(path, manifest)

    session = ViewerSession(max_workers=1)
    loaded = session.load_manifest(path)
    assert loaded.after_scene_pack is None
    state = session.get_pair_state("pair_dead", "after")
    assert state.scene_pack_ref is None


def test_select_pair_treats_redacted_source_as_absent(tmp_path: Path) -> None:
    from src.services.comparison.viewer_session import (
        PairSessionState,
        ViewerSession,
    )

    session = ViewerSession(max_workers=1)
    session._states[("p1", "after")] = PairSessionState(
        pair_id="p1", side="after",
        source_path="<redacted>/detail.dxf",
    )
    state = session.select_pair("p1", side="after")
    # No build scheduled against the fake path; stays relative_only.
    assert state.render_mode == "relative_only"
    assert ("p1", "after") not in session._futures
