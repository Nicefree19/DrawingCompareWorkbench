# -*- coding: utf-8 -*-
"""Verify the Phase G3.8 per-drawing progress badge.

Sets up a Workbench with three pairs at different review states and
asserts the drawing list shows the correct badge for each:
  - All zones reviewed → "✅ 완료 (확인 N건)"
  - Some zones reviewed → "⏳ N/M"
  - Nothing reviewed → "▫ 미시작"
  - Empty pair → "" (no badge)

Also exercises ``_refresh_drawing_progress_badges_v2`` after a status
change to confirm the badge updates without rebuilding the list.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")
    for n in ("PIL", "matplotlib", "fontTools", "ezdxf", "easyocr", "paddle"):
        logging.getLogger(n).setLevel(logging.WARNING)


def main() -> int:
    _setup_logging()
    log = logging.getLogger("verify_g38")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem
    app = QApplication.instance() or QApplication([])

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.review_project import (
        ReviewStateRecord,
        review_state_key,
    )

    log.info("Constructing workbench...")
    wb = DrawingCompareWorkbenchV2()

    summary = {"test": "G3.8 drawing progress badge", "checks": {}}

    try:
        # 4 pairs:
        #   pair-A: 3 zones, 0 reviewed → "▫ 미시작"
        #   pair-B: 2/3 reviewed → "⏳ 2/3"
        #   pair-C: 4/4 reviewed → "✅ 완료 (확인 3건)"
        #   pair-D: 0 zones → "" (no badge)
        wb._viewer_overlay_cache = {
            "pair-A": [{"zone_id": "A-1"}, {"zone_id": "A-2"}, {"zone_id": "A-3"}],
            "pair-B": [{"zone_id": "B-1"}, {"zone_id": "B-2"}, {"zone_id": "B-3"}],
            "pair-C": [{"zone_id": "C-1"}, {"zone_id": "C-2"}, {"zone_id": "C-3"}, {"zone_id": "C-4"}],
            "pair-D": [],
        }

        wb._review_records_v2 = {
            # pair-A: nothing
            # pair-B: 2 confirmed, 1 pending
            review_state_key("pair-B", "B-1"): ReviewStateRecord(
                pair_id="pair-B", pair_uuid="pair-B", zone_id="B-1",
                status="confirmed", note="",
            ),
            review_state_key("pair-B", "B-2"): ReviewStateRecord(
                pair_id="pair-B", pair_uuid="pair-B", zone_id="B-2",
                status="confirmed", note="",
            ),
            # pair-C: 3 confirmed + 1 ignored = all 4 done
            review_state_key("pair-C", "C-1"): ReviewStateRecord(
                pair_id="pair-C", pair_uuid="pair-C", zone_id="C-1",
                status="confirmed", note="",
            ),
            review_state_key("pair-C", "C-2"): ReviewStateRecord(
                pair_id="pair-C", pair_uuid="pair-C", zone_id="C-2",
                status="confirmed", note="",
            ),
            review_state_key("pair-C", "C-3"): ReviewStateRecord(
                pair_id="pair-C", pair_uuid="pair-C", zone_id="C-3",
                status="confirmed", note="",
            ),
            review_state_key("pair-C", "C-4"): ReviewStateRecord(
                pair_id="pair-C", pair_uuid="pair-C", zone_id="C-4",
                status="ignored", note="",
            ),
        }

        cases = [
            ("pair-A", "▫ 미시작"),
            ("pair-B", "⏳ 2/3"),
            ("pair-C", "✅ 완료 (확인 3건)"),
            ("pair-D", ""),
        ]

        log.info("\n=== Test: _drawing_progress_badge_v2 per pair ===")
        for pid, expected in cases:
            badge = wb._drawing_progress_badge_v2(pid)
            ok = badge == expected
            log.info("  %s: badge=%r expected=%r — %s",
                     pid, badge, expected, "PASS" if ok else "FAIL")
            summary["checks"][pid] = {"badge": badge, "expected": expected, "pass": ok}

        # ---- Verify _refresh_drawing_progress_badges_v2 updates the list ----
        log.info("\n=== Test: _refresh after status change ===")
        wb._drawing_rows = [
            {"pair_id": "pair-A", "drawing_number": "S-101", "grade": "관심"},
            {"pair_id": "pair-B", "drawing_number": "S-102", "grade": "관심"},
        ]
        # populate drawing_list_v2 manually
        wb.drawing_list_v2.clear()
        for row in wb._drawing_rows:
            it = QListWidgetItem(f"{row['drawing_number']}  ▫ 미시작\n관심 · old_text")
            it.setData(Qt.UserRole, row)
            wb.drawing_list_v2.addItem(it)

        # Mark all of pair-A as confirmed → badge should become "✅ 완료"
        for zid in ["A-1", "A-2", "A-3"]:
            wb._review_records_v2[review_state_key("pair-A", zid)] = ReviewStateRecord(
                pair_id="pair-A", pair_uuid="pair-A", zone_id=zid,
                status="confirmed", note="",
            )

        wb._refresh_drawing_progress_badges_v2()
        for _ in range(5):
            app.processEvents()

        item_a = wb.drawing_list_v2.item(0)
        item_b = wb.drawing_list_v2.item(1)
        text_a = item_a.text() if item_a else ""
        text_b = item_b.text() if item_b else ""
        log.info("  Item A text: %r", text_a)
        log.info("  Item B text: %r", text_b)

        a_updated = "✅ 완료" in text_a and "확인 3건" in text_a
        b_unchanged = "⏳ 2/3" in text_b
        a_preserves_tail = "old_text" in text_a or "관심" in text_a

        summary["checks"]["refresh_a_updated"] = {
            "text": text_a, "pass": a_updated,
        }
        summary["checks"]["refresh_b_unchanged"] = {
            "text": text_b, "pass": b_unchanged,
        }
        summary["checks"]["refresh_a_preserves_tail"] = {
            "text": text_a, "pass": a_preserves_tail,
        }

        log.info("  refresh A updated to ✅ 완료? %s", a_updated)
        log.info("  refresh B stays at ⏳ 2/3? %s", b_unchanged)
        log.info("  refresh A preserves tail line? %s", a_preserves_tail)

        all_pass = all(c["pass"] for c in summary["checks"].values())
        summary["verdict"] = "PASS" if all_pass else "FAIL"

    except Exception as exc:
        log.exception("Test crashed")
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
        summary["verdict"] = "ERROR"
    finally:
        try:
            session = getattr(wb, "_viewer_session", None)
            if session:
                session.shutdown(wait=False)
        except Exception:
            pass
        try:
            wb.close()
        except Exception:
            pass

    summary_path = Path(os.environ.get("TEMP", ".")) / "g38_drawing_progress_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Summary JSON: %s", summary_path)
    log.info("VERDICT: %s", summary.get("verdict", "?"))
    return 0 if summary.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
