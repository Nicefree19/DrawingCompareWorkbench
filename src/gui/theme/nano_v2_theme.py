# -*- coding: utf-8 -*-
"""
Nano Banana Pro v2 Theme
========================

Cyber-Structural Command Center를 위한 새로운 테마 정의.
Deep Space Dark Mode, Neon Accents, Glassmorphism 스타일을 제공합니다.

Author: TEKLA_MCP Team
Date: 2026-01-17
"""

from PySide6.QtGui import QColor, QFont


class NanoColors:
    """
    Nano Banana Pro v2 Color Palette (Deep Space Dark Mode)
    """

    # -------------------------------------------------------------------------
    # 1. Backgrounds (Deep Space)
    # -------------------------------------------------------------------------
    BG_DEEP = "#0F1115"  # App Background (Deep Space)
    BG_SURFACE = "#181A1F"  # Base Cards / Panels
    BG_ELEVATED = "#212429"  # Floating Panels / Dialogs
    BG_HOVER = "#2A2D33"  # Standard Hover State

    # -------------------------------------------------------------------------
    # 2. Accents (Neon Cyber)
    # -------------------------------------------------------------------------
    PRIMARY = "#00E5FF"  # Cyan Neon (Action, Brand)
    PRIMARY_HOVER = "#33EAFF"  # Lighter Cyan
    PRIMARY_GLOW = "rgba(0, 229, 255, 0.3)"  # Active Border Glow

    SECONDARY = "#F4D03F"  # Banana Neon (Highlights, Warnings)
    SUCCESS = "#00E676"  # Matrix Green
    SUCCESS_HOVER = "#059669"  # Success Hover State (Darker Green)
    WARNING = "#FFAB00"  # Amber Neon
    ERROR = "#FF1744"  # Laser Red
    ACCENT_PURPLE = "#7C3AED"  # Purple Accent (Cost, Special)

    # -------------------------------------------------------------------------
    # 3. Typography (High Contrast)
    # -------------------------------------------------------------------------
    TEXT_PRIMARY = "#FFFFFF"  # Headings, Data (100% White)
    TEXT_SUBTLE = "#8B9BB4"  # Labels (Blue-Grey)
    TEXT_MUTED = "#56657F"  # Disabled / Watermarks

    # -------------------------------------------------------------------------
    # 4. Borders & Dividers
    # -------------------------------------------------------------------------
    BORDER = "#2C313A"  # Subtle Border
    BORDER_LIGHT = "#3E4451"  # Card Border
    BORDER_ACTIVE = "#00E5FF"  # Focus Border

    # -------------------------------------------------------------------------
    # 5. Table Specific
    # -------------------------------------------------------------------------
    TABLE_HEADER = "#131519"  # Dark Header
    TABLE_STRIPE = "#181A1F"  # Zebra Stripe
    TABLE_GRID = "#21252B"  # Grid Lines


class NanoLayout:
    """
    Robust Layout Constants
    """

    # Spacing
    MARGIN_MAIN = 24  # Outer Window Margin
    MARGIN_CARD = 20  # Card Internal Padding
    SPACING = 16  # Standard Component Gap

    # Sizing
    RADIUS_S = 4
    RADIUS_M = 8
    RADIUS_L = 12

    SIDEBAR_WIDTH_EXPANDED = 240
    SIDEBAR_WIDTH_COLLAPSED = 68

    HEADER_HEIGHT = 60


class NanoFonts:
    """
    Responsive Typhography
    """

    FAMILY = "Segoe UI"
    MONO = "Consolas"

    @staticmethod
    def header(size=18, bold=True):
        f = QFont(NanoFonts.FAMILY, size)
        f.setBold(bold)
        return f

    @staticmethod
    def subheader(size=14, bold=True):
        f = QFont(NanoFonts.FAMILY, size)
        f.setBold(bold)
        return f

    @staticmethod
    def body(size=11):
        f = QFont(NanoFonts.FAMILY, size)
        return f


def get_nano_stylesheet() -> str:
    """
    Returns the Global Stylesheet for Nano Banana Pro v2
    """
    return f"""
    /* Global Reset */
    QMainWindow, QDialog {{
        background-color: {NanoColors.BG_DEEP};
        color: {NanoColors.TEXT_PRIMARY};
    }}
    
    /* ScrollBars (Slim & Dark) */
    QScrollBar:vertical {{
        border: none;
        background: {NanoColors.BG_DEEP};
        width: 10px;
        margin: 0px 0px 0px 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {NanoColors.BORDER_LIGHT};
        min-height: 30px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {NanoColors.TEXT_MUTED};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    
    /* Tooltips */
    QToolTip {{
        background-color: {NanoColors.BG_ELEVATED};
        color: {NanoColors.TEXT_PRIMARY};
        border: 1px solid {NanoColors.BORDER_LIGHT};
        padding: 5px;
        border-radius: 4px;
    }}
    
    /* Splitter Handle */
    QSplitter::handle {{
        background-color: {NanoColors.BG_DEEP};
        width: 2px;
    }}
    QSplitter::handle:hover {{
        background-color: {NanoColors.PRIMARY};
    }}
    """
