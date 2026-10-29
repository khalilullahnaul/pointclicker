"""Regenerates the application icon (used by the window, tray, exe, shortcuts, and dashboard).

Generates:
  - src/pointclicker/pointclicker.ico (multi-res Windows icon: 16, 24, 32, 48, 64, 128, 256)
  - docs/images/icon.png (high-resolution PNG)
  - build/pointclicker.png (Linux desktop icon)

Run: .venv/bin/python scripts/make_icon.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

ROOT = Path(__file__).resolve().parents[1]
ICO_OUT = ROOT / "src" / "pointclicker" / "pointclicker.ico"
DOCS_OUT = ROOT / "docs" / "images" / "icon.png"
BUILD_OUT = ROOT / "build" / "pointclicker.png"


def render_icon(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    s = size / 512.0

    # 1. Subtle drop shadow for the squircle tile
    shadow_path = QPainterPath()
    shadow_path.addRoundedRect(QRectF(32 * s, 36 * s, 448 * s, 448 * s), 110 * s, 110 * s)
    p.fillPath(shadow_path, QColor(0, 0, 0, 55))

    # 2. Main squircle tile with smooth modern gradient
    tile_rect = QRectF(32 * s, 30 * s, 448 * s, 448 * s)
    tile_path = QPainterPath()
    tile_path.addRoundedRect(tile_rect, 110 * s, 110 * s)

    bg_grad = QLinearGradient(32 * s, 30 * s, 480 * s, 478 * s)
    bg_grad.setColorAt(0.0, QColor("#1D72F0"))
    bg_grad.setColorAt(0.5, QColor("#0D52D6"))
    bg_grad.setColorAt(1.0, QColor("#073399"))
    p.fillPath(tile_path, bg_grad)

    # Rim light / subtle top border
    rim_path = QPainterPath()
    rim_path.addRoundedRect(tile_rect.adjusted(2 * s, 2 * s, -2 * s, -2 * s), 108 * s, 108 * s)
    p.strokePath(rim_path, QPen(QColor(255, 255, 255, 65), 3.5 * s))

    # 3. Concentric click ripple waves around target point
    cx, cy = 256 * s, 185 * s

    # Outer ripple
    p.setPen(QPen(QColor(255, 255, 255, 40), 6 * s))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), 130 * s, 130 * s)

    # Middle ripple
    p.setPen(QPen(QColor(255, 255, 255, 80), 8 * s))
    p.drawEllipse(QPointF(cx, cy), 85 * s, 85 * s)

    # Inner ripple (bright glowing ring)
    p.setPen(QPen(QColor(255, 255, 255, 170), 9 * s))
    p.drawEllipse(QPointF(cx, cy), 46 * s, 46 * s)

    # Center target dot (glowing cyan / white bullseye)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 240))
    p.drawEllipse(QPointF(cx, cy), 18 * s, 18 * s)
    p.setBrush(QColor("#00E5FF"))
    p.drawEllipse(QPointF(cx, cy), 9 * s, 9 * s)

    # 4. Stylized mouse click pointer / cursor
    tip_x, tip_y = 256 * s, 185 * s

    cursor = QPainterPath()
    cursor.moveTo(tip_x, tip_y)
    cursor.lineTo(tip_x, tip_y + 195 * s)
    cursor.lineTo(tip_x + 55 * s, tip_y + 145 * s)
    cursor.lineTo(tip_x + 115 * s, tip_y + 250 * s)
    cursor.lineTo(tip_x + 158 * s, tip_y + 228 * s)
    cursor.lineTo(tip_x + 98 * s, tip_y + 122 * s)
    cursor.lineTo(tip_x + 170 * s, tip_y + 122 * s)
    cursor.closeSubpath()

    # Shadow for cursor
    cursor_shadow = QPainterPath(cursor)
    cursor_shadow.translate(8 * s, 12 * s)
    p.fillPath(cursor_shadow, QColor(0, 0, 0, 85))

    # Cursor fill
    cursor_grad = QLinearGradient(tip_x, tip_y, tip_x + 170 * s, tip_y + 250 * s)
    cursor_grad.setColorAt(0.0, QColor("#FFFFFF"))
    cursor_grad.setColorAt(1.0, QColor("#EAF0F8"))
    p.fillPath(cursor, cursor_grad)

    # Cursor outline
    p.strokePath(cursor, QPen(QColor(18, 28, 45, 225), 7 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    resolutions = [16, 24, 32, 48, 64, 128, 256]
    images = [(n, png_bytes(render_icon(n))) for n in resolutions]

    # Write multi-res ICO
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    for n, data in images:
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    ICO_OUT.parent.mkdir(parents=True, exist_ok=True)
    ICO_OUT.write_bytes(header + entries + b"".join(d for _, d in images))
    print(f"wrote {ICO_OUT}")

    # Write PNG for docs and build
    DOCS_OUT.parent.mkdir(parents=True, exist_ok=True)
    render_icon(256).save(str(DOCS_OUT))
    print(f"wrote {DOCS_OUT}")

    BUILD_OUT.parent.mkdir(parents=True, exist_ok=True)
    render_icon(256).save(str(BUILD_OUT))
    print(f"wrote {BUILD_OUT}")
