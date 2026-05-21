# -*- coding: utf-8 -*-
"""
Comparison Module Theme
=======================

PySide6 UI를 위한 컬러 팔레트 및 스타일시트 정의. (Unified UI 호환)

Author: TEKLA_MCP Team
Date: 2025-12-23
"""

from typing import Dict

# -----------------------------------------------------------------------------
# Color Palette (Dark Mode Default)
# -----------------------------------------------------------------------------
COLORS: Dict[str, str] = {
    "background": "#1E1E1E",
    "surface": "#252526",
    "surface_variant": "#2D2D30",
    "primary": "#007ACC",
    "primary_hover": "#0098FF",
    "secondary": "#C0C0C0",  # Phase 1: 대비 개선
    "text_primary": "#FFFFFF",
    "text_secondary": "#F0F0F0",  # Phase 1: 대비 개선
    "border": "#555555",  # Phase 2: 테두리 개선
    "success": "#4CAF50",
    "warning": "#FF9800",
    "error": "#F44336",
    "table_header_bg": "#333333",
    "table_grid": "#444444",
    "table_select_bg": "#094771",
}


class NanoColors:
    """Unified UI용 색상 팔레트 (High Contrast Dark Mode)"""

    # Backgrounds (Phase 2: 배경 개선 - 2026-01-09)
    BG_DEEP = "#181818"  # 가장 어두운 배경 (Main Window)
    BG_SURFACE = "#1E1E1E"  # 카드/패널 배경
    BG_SECONDARY = "#252526"  # 헤더/그룹박스 배경
    BG_LIGHT = "#3D3D42"  # 호버 상태 (기존 #333337 → 더 밝게)
    BG_INPUT = "#3C3C42"  # Phase 3-3: 입력 필드 배경

    # Text (Phase 1: 대비 개선 - 2026-01-09)
    TEXT_PRIMARY = "#FFFFFF"  # 기본 텍스트 (명도 100%)
    TEXT_SECONDARY = "#F0F0F0"  # 2차 텍스트 (명도 94%) - 대비 개선
    TEXT_TERTIARY = "#C0C0C0"  # 비활성/설명 텍스트 (명도 75%) - 대비 개선
    TEXT_MUTED = "#999999"  # 흐릿한 텍스트 (명도 60%) - 가독성 개선
    TEXT_PLACEHOLDER = "#808080"  # Phase 3-3: placeholder 텍스트 (명도 50%)

    # Borders (Phase 2: 테두리 개선 - 2026-01-09)
    BORDER = "#555555"  # 기본 테두리 (기존 #454545 → 더 밝게)
    BORDER_SUBTLE = "#404040"  # 옅은 테두리 (기존 #303030 → 더 밝게)
    BORDER_FOCUS = "#007ACC"  # 포커스 테두리

    # Accents
    PRIMARY = "#007ACC"  # VS Code Blue
    ACCENT = "#007ACC"
    ACCENT_HOVER = "#0098FF"

    # Status
    SUCCESS = "#4CAF50"
    WARNING = "#FFC107"  # 밝은 노랑으로 개선 (기존 오렌지는 어두운 배경에서 잘 안보임)
    ERROR = "#FF5252"  # 밝은 빨강으로 개선
    DANGER = "#dc3545"  # Bootstrap danger red

    # Table (Phase 3-2: 헤더 대비 개선 - 2026-01-09)
    TABLE_HEADER = "#3D3D3D"  # 더 밝게 변경 (기존 #2D2D2D)
    TABLE_GRID = "#404040"

    # Specific
    CARD_BG = BG_SURFACE
    HOVER = BG_LIGHT

    # Glass/Panel effects
    GLASS_PANEL = "rgba(40, 40, 45, 0.95)"  # 반투명 패널 효과
    GLASS_OVERLAY = "rgba(30, 30, 30, 0.85)"  # 오버레이 효과

    # Legacy/Compatibility aliases
    SURFACE = BG_SURFACE  # Alias for BG_SURFACE
    SURFACE_HOVER = BG_LIGHT  # Alias for hover state


class NanoFonts:
    """폰트 설정"""

    FAMILY = "Segoe UI"
    MONO = "Consolas"

    @classmethod
    def header(cls, size: int = 14) -> "QFont":
        from PySide6.QtGui import QFont

        font = QFont(cls.FAMILY, size)
        font.setBold(True)
        return font

    @classmethod
    def body(cls, size: int = 10) -> "QFont":
        from PySide6.QtGui import QFont

        return QFont(cls.FAMILY, size)


class LAYOUT:
    """레이아웃 상수"""

    MARGIN_MAIN = 20
    MARGIN_SUB = 10
    SPACING = 10

    RADIUS_S = 4
    RADIUS_M = 8
    RADIUS_L = 12


def get_stylesheet() -> str:
    """전역 스타일시트 반환"""
    return f"""
        QDialog, QMainWindow {{
            background-color: {NanoColors.BG_DEEP};
            color: {NanoColors.TEXT_PRIMARY};
        }}
        
        QLabel {{
            color: {NanoColors.TEXT_PRIMARY};
            font-family: '{NanoFonts.FAMILY}';
            font-size: 13px;
        }}
        
        /* Disabled State Fix */
        QWidget:disabled {{
            color: {NanoColors.TEXT_MUTED};
        }}
        
        QGroupBox {{
            background-color: {NanoColors.BG_SURFACE};
            border: 1px solid {NanoColors.BORDER};
            border-radius: {LAYOUT.RADIUS_M}px;
            margin-top: 1.2em;
            padding-top: 15px;
            color: {NanoColors.TEXT_PRIMARY};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 5px;
            color: {NanoColors.PRIMARY};
            font-weight: bold;
        }}
        
        /* Phase 3-3: 입력 필드 개선 (2026-01-09) */
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
            background-color: {NanoColors.BG_INPUT};
            border: 1px solid {NanoColors.BORDER};
            color: {NanoColors.TEXT_PRIMARY};
            border-radius: {LAYOUT.RADIUS_S}px;
            padding: 6px 8px;
            selection-background-color: {NanoColors.PRIMARY};
            min-height: 18px;
        }}

        /* Placeholder 스타일 */
        QLineEdit[placeholderText] {{
            color: {NanoColors.TEXT_PRIMARY};
        }}
        QLineEdit::placeholder {{
            color: {NanoColors.TEXT_PLACEHOLDER};
            font-style: italic;
        }}

        /* 호버 상태 */
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
            border: 1px solid #5A5A60;
            background-color: #424248;
        }}

        /* 포커스 상태 - 글로우 효과 */
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 2px solid {NanoColors.BORDER_FOCUS};
            background-color: #3E3E44;
            padding: 5px 7px;
        }}

        /* 비활성 상태 */
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
            background-color: {NanoColors.BG_SECONDARY};
            color: {NanoColors.TEXT_MUTED};
            border: 1px solid {NanoColors.BORDER_SUBTLE};
        }}

        /* QComboBox 드롭다운 버튼 */
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 24px;
            border-left: 1px solid {NanoColors.BORDER};
            border-top-right-radius: {LAYOUT.RADIUS_S}px;
            border-bottom-right-radius: {LAYOUT.RADIUS_S}px;
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 6px solid {NanoColors.TEXT_SECONDARY};
            margin-right: 6px;
        }}
        QComboBox::down-arrow:hover {{
            border-top-color: {NanoColors.PRIMARY};
        }}

        /* QComboBox 드롭다운 목록 */
        QComboBox QAbstractItemView {{
            background-color: {NanoColors.BG_INPUT};
            border: 1px solid {NanoColors.BORDER};
            selection-background-color: {NanoColors.PRIMARY};
            selection-color: white;
            padding: 4px;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 6px 8px;
            min-height: 24px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background-color: {NanoColors.BG_LIGHT};
        }}

        /* QSpinBox/QDoubleSpinBox 버튼 */
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 20px;
            border-left: 1px solid {NanoColors.BORDER};
            background-color: {NanoColors.BG_SECONDARY};
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 20px;
            border-left: 1px solid {NanoColors.BORDER};
            background-color: {NanoColors.BG_SECONDARY};
        }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
            background-color: {NanoColors.BG_LIGHT};
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-bottom: 5px solid {NanoColors.TEXT_SECONDARY};
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {NanoColors.TEXT_SECONDARY};
        }}
        
        QPushButton {{
            background-color: {NanoColors.BG_SECONDARY};
            border: 1px solid {NanoColors.BORDER};
            color: {NanoColors.TEXT_PRIMARY};
            border-radius: {LAYOUT.RADIUS_S}px;
            padding: 6px 16px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: {NanoColors.BG_LIGHT};
            border-color: {NanoColors.BORDER_FOCUS};
        }}
        QPushButton:pressed {{
            background-color: {NanoColors.BG_DEEP};
        }}
        QPushButton:disabled {{
            background-color: {NanoColors.BG_DEEP};
            color: {NanoColors.TEXT_MUTED};
            border-color: {NanoColors.BORDER_SUBTLE};
        }}
        
        /* Primary Button */
        QPushButton[primary="true"], QPushButton#primary {{
            background-color: {NanoColors.PRIMARY};
            border-color: {NanoColors.PRIMARY};
            color: white;
        }}
        QPushButton[primary="true"]:hover, QPushButton#primary:hover {{
            background-color: {NanoColors.ACCENT_HOVER};
        }}
        QPushButton[primary="true"]:disabled, QPushButton#primary:disabled {{
            background-color: {NanoColors.BG_SECONDARY};
            color: {NanoColors.TEXT_MUTED};
        }}
        
        /* Phase 3-1: 탭 스타일 개선 - 2026-01-09 */
        QTabWidget::pane {{
            border: 1px solid {NanoColors.BORDER};
            background-color: {NanoColors.BG_SURFACE};
        }}
        QTabBar::tab {{
            background-color: {NanoColors.BG_SECONDARY};
            color: {NanoColors.TEXT_PRIMARY};
            padding: 8px 16px;
            border-top-left-radius: {LAYOUT.RADIUS_S}px;
            border-top-right-radius: {LAYOUT.RADIUS_S}px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background-color: {NanoColors.BG_SURFACE};
            color: {NanoColors.PRIMARY};
            border-bottom: 3px solid {NanoColors.PRIMARY};
            font-weight: bold;
        }}
        QTabBar::tab:hover:!selected {{
            background-color: #404045;
            color: {NanoColors.ACCENT_HOVER};
        }}
        
        QTableWidget {{
            background-color: {NanoColors.BG_SURFACE};
            gridline-color: {NanoColors.TABLE_GRID};
            color: {NanoColors.TEXT_PRIMARY};
            border: 1px solid {NanoColors.BORDER};
        }}
        QHeaderView::section {{
            background-color: {NanoColors.TABLE_HEADER};
            color: {NanoColors.TEXT_PRIMARY};
            border: none;
            border-right: 1px solid {NanoColors.BG_DEEP};
            border-bottom: 2px solid {NanoColors.PRIMARY};
            padding: 6px 8px;
            font-weight: bold;
        }}
    """
