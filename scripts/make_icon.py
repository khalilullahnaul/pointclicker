"""Regenerates src/pointclicker/pointclicker.ico (used by the window, the tray and the exe).

Run: python scripts/make_icon.py
"""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap

OUT = Path(__file__).resolve().parents[1] / "src" / "pointclicker" / "pointclicker.ico"


def png(size: int) -> bytes:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64
    p.setPen(QPen(Qt.white, max(1.0, 4 * s)))
    p.setBrush(QColor(220, 40, 40))
    p.drawEllipse(6 * s, 6 * s, 52 * s, 52 * s)
    p.setBrush(Qt.white)
    p.drawEllipse(26 * s, 26 * s, 12 * s, 12 * s)
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(data)


if __name__ == "__main__":
    app = QGuiApplication([])
    images = [(n, png(n)) for n in (16, 24, 32, 48, 64, 256)]
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))  # ICO with PNG-compressed entries (Vista+)
    entries = b""
    for n, data in images:
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    OUT.write_bytes(header + entries + b"".join(d for _, d in images))
    print(f"wrote {OUT}")
