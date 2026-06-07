# Task Checklist — review remediation

## Pre
- [x] Green baseline (37 passed) captured 2026-06-05
- [x] implementation_plan.md created

## Phase 1 (DONE — 41 suite / 71 related green, 0 regressions)
- [x] 1.1 truncation SoT — bridge `_extraction_truncated`; evaluator drops `>=` heuristic
- [x] 1.1 truncation SoT — cap-hit test uses explicit flag + new exact-cap-FN regression test
- [x] 1.2 roi_first fallback — evaluated no-change falls through to full; skip stays terminal; +test
- [x] 1.3 ROI cache/quarantine — ROI-aware key, cache successes only; +2 tests
- [x] 1.4 sweep cap/log — `--dwg-bridge-roi-max-attempts`, launched_attempts, progress line; +test
- [~] 1.5 result-builder refactor — DEFERRED (correctness applied without it; cleanup follow-up)
- [x] Phase 1 full suite green (41 passed)
- [ ] Phase 1 commit — awaiting go-ahead

## Phase 2 — baseline-review mode (concurrent Codex = stage diagnostics)
Gap analysis: concurrent baseline = timeout STAGE diagnostics only; findings 7-12 all open.
- [x] 1.1 bridge half — `_extraction_truncated`, possibly_truncated from authoritative flag (bridge) — DONE, green
- [x] 2.3/finding 8 — happy-path cleanup grace_seconds=0.0 (no 5s stall) — DONE +test
- [x] 2.5/finding 11 — watchdog cancel() joins; killed_pids race fixed — DONE +2 tests
- [x] finding 7 (bridge) — pin spawned PID at dispatch via only_pids; cleanup+watchdog target only pinned — DONE +2 tests
- [~] finding 7 (adapter) — NOT feasible (COM-launched ZWCAD not a child of bridge subprocess); coarse image cleanup is opt-in fallback; precise pinning lives in bridge
- [x] finding 9 — adapter settle-detection: returns once killed+quiet, not full grace — DONE +test
- [x] finding 10 — no early-return-on-first-found; late-spawn polling + kill-fail retry — DONE +test (late-spawn test updated)
- [x] finding 12 — DONE: extracted shared leaf module src/services/comparison/_process_cleanup.py
      (process_ids_for_image/kill_process_tree/terminate_process). Adapter imports it directly;
      bridge imports it resiliently (package import + importlib file-path fallback for standalone
      subprocess). win32 internals now exist in ONE place. Impl tests consolidated into
      tests/.../test_process_cleanup.py; orchestration tests unchanged (subprocess patching is global).
- [ ] finding 12b/altitude — structured timeout exit code vs stderr regex (Phase 4)

Phase 1 → ba30b2d. Phase 2a (truncation+8+11) → swept into Codex's f846a5f.
Phase 2b (7 bridge, 9, 10) → d0afc3e.

## open_document timeout investigation (AC1027_pair_016) — chosen over Phase 3
Diagnosis: NOT ROI/size (files 0.7-1.3MB). AC1027 PSRC composite-column detail
drawings -> modal dialog blocks COM Documents.Open (proxy notice / missing Korean
SHX font / xref prompt); suppression sysvars ran only AFTER open. Watchdog fired
but killed nothing (image-name enumeration empty) -> 87s instead of ~30s.
- [x] A: pre-open dialog suppression (PROXYNOTICE/FILEDIA/CMDDIA/SECURELOAD/XLOADCTL/FONTALT)
      sent to the initial doc BEFORE Documents.Open, in com + lisp-com paths. +tests
- [x] B: pin exact PID via app.HWND -> GetWindowThreadProcessId; cleanup kills pinned
      PID DIRECTLY even when image enumeration is blind (clears hang at watchdog). +tests
- [ ] CAD실측 (사용자): run --visible on AC1027_pair_016 before file to confirm which
      dialog; verify suppression opens it (or fails fast at ~30s).
Phase 2c (A+B) → committing now. 54 suite / 84 related green.

## Phase 4 (DONE — committing now; 59 suite / 89 related green)
- [x] 12b: bridge emits TIMEOUT_EXIT_CODE=124 on BridgeTimeoutError; adapter classifies
      IMPORT_TIMEOUT by exit code (BRIDGE_TIMEOUT_EXIT_CODE), stderr sniff kept as
      fallback; details.timeout_signal records source. +4 tests
- [~] 12 (win32 dedup): DEFERRED by decision — would couple the self-contained bridge
      subprocess to src/ (or vice-versa) for a no-behavior-change refactor of stable
      ctypes plumbing; coupling risk > benefit. Revisit only if the dup actually drifts.
- [x] 13 (quarantine policy): DECIDED keep current — AC1027 fails reliably (not
      transient) per open_document diagnosis, so quarantine correctly avoids repeated
      multi-minute CAD timeouts; ROI retries already bypass it (Phase 1.3). No change.
- [x] 4.3: args_template exception-restore test added; real watchdog _fire exercised
      by test_watchdog_cancel_joins (Phase 2a).

## Phase 3 — ROI geometry (DONE)
- [x] 3a COM (findings 1,6): _entity_in_roi via COM GetBoundingBox; block body overlap
      kept; point-less entities get a real box. Offline-verified +3 tests → 3bbffc0
- [x] 3b LISP (findings 1,2): dropped ssget "_C" (entnext walk only); dcw-block-in-roi-p
      uses vla-getboundingbox for INSERT/TEXT/MTEXT w/ point fallback. Paren-balanced,
      CAD실측 REQUIRED → c4bebac
- [x] 14 (per-change ROI boxes vs union): DONE — ROI schema now a list of boxes
      ("boxes":[[minx,miny,maxx,maxy],...]) across evaluator + bridge Python + LISP
      (DCW_ROI_BOXES, any-box membership). Single "bbox" still accepted. evaluator emits
      one box per expected change (no whole-drawing union). Python verified +tests;
      LISP CAD실측 REQUIRED.

## Remaining / pending verification
- CAD실측 (사용자): (a) open_document A+B on AC1027_pair_016; (b) LISP ROI (3b) on a
  frozen-layer / block-heavy pair.
- Deferred by decision: finding 12 (win32 dedup), finding 14 (multi-box ROI).
ALL review findings now coded except 12 & 14 (both deferred with rationale).
Commits: ba30b2d, f846a5f, d0afc3e, 9e86fa1, 2b7bbb3, 3bbffc0, c4bebac.

## Phase 3 (CAD gate)
- [ ] 3.1–3.3 code + offline tests green
- [ ] AC1027_pair_016 실측 통과 (사용자)
- [ ] commit after gate

## Phase 4
- [ ] 4.1 code + test
- [ ] 4.2 decision recorded
- [ ] 4.3 tests + green + commit
