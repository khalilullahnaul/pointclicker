"""Theme, accent, fonts, spacing and app icons. Everything visual that isn't a widget."""
from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

from PySide6.QtGui import QColor, QFont
from qfluentwidgets import FluentIconBase, Theme, setFontFamilies, setTheme, setThemeColor
from qfluentwidgets.common.config import isDarkTheme

GRID = 8  # every margin and gap is a multiple of this
FLUENT_BLUE = QColor("#0078D4")  # Windows' default accent
FONT_FAMILIES = (["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"] if sys.platform == "win32"
                 else ["Inter", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans"])
ICON_DIR = Path(__file__).with_name("icons")
APP_ICON = Path(__file__).parent.parent / "pointclicker.ico"


class AppIcon(FluentIconBase, Enum):
    """Icons the bundled Fluent set lacks; black/white variants follow the theme like FluentIcon does."""

    STOP = "stop"
    KEYBOARD = "keyboard"
    CROSSHAIR = "crosshair"

    def path(self, theme=Theme.AUTO):
        dark = isDarkTheme() if theme == Theme.AUTO else theme == Theme.DARK
        return str(ICON_DIR / f"{self.value}_{'white' if dark else 'black'}.svg")


def apply_fonts(app) -> None:
    setFontFamilies(FONT_FAMILIES)  # qfluentwidgets labels/buttons
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(9.5 if sys.platform == "win32" else 10)
    app.setFont(font)  # plain Qt widgets, dialogs, the picker overlay


def system_accent() -> QColor:
    if sys.platform == "win32":
        try:
            from qframelesswindow.utils import getSystemAccentColor
            color = getSystemAccentColor()
            if color.isValid():
                return color
        except Exception:
            pass
    return QColor(FLUENT_BLUE)


def accent_for(cfg) -> QColor:
    color = QColor(cfg.accent) if cfg.accent else QColor()
    return color if color.isValid() else system_accent()


def apply_theme(cfg) -> None:
    setTheme({"light": Theme.LIGHT, "dark": Theme.DARK}.get(cfg.theme, Theme.AUTO))
    setThemeColor(accent_for(cfg))


LEVEL_COLORS = {  # status text colors per level, (light, dark)
    "ok": ("#0f7b0f", "#6ccb5f"),
    "warning": ("#9d5d00", "#fce100"),
    "error": ("#c42b1c", "#ff99a4"),
    "muted": ("#5f5f5f", "#9f9f9f"),
}


def level_color(level: str) -> str:
    light, dark = LEVEL_COLORS.get(level, LEVEL_COLORS["muted"])
    return dark if isDarkTheme() else light
