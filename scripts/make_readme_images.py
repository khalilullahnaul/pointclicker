"""Regenerate docs/images/social-preview.png (link previews, og:image) and docs/images/picker.png (the real pick
overlay, rendered offscreen over a sample desktop).    python scripts/make_readme_images.py"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
sys.path.insert(0, str(ROOT / "src"))
patch("pointclicker.targets.config_path", lambda: Path(tempfile.mkdtemp()) / "config.json").start()

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])


def rounded(p: QPainter, img: QImage, rect: QRectF, radius: float):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    p.drawImage(rect, img)
    p.restore()


def social_preview():
    w, h = 1280, 640
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    bg = QLinearGradient(0, 0, w, h)
    bg.setColorAt(0, QColor("#0b1220"))
    bg.setColorAt(1, QColor("#13305e"))
    p.fillRect(img.rect(), bg)

    shot = QImage(str(IMAGES / "targets-dark.png"))
    sw = 640
    sh = sw * shot.height() / shot.width()
    frame = QRectF(w - sw - 56, (h - sh) / 2 + 20, sw, sh)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 110))
    p.drawRoundedRect(frame.translated(0, 14).adjusted(-6, -6, 6, 6), 18, 18)  # shadow
    rounded(p, shot, frame, 14)

    icon = QImage(str(IMAGES / "icon.png"))
    p.drawImage(QRectF(64, 96, 96, 96), icon)
    p.setPen(QColor("white"))
    p.setFont(QFont("Inter", 52, QFont.Bold))
    p.drawText(QRectF(64, 210, 560, 80), Qt.AlignLeft | Qt.AlignVCenter, "PointClicker")
    p.setFont(QFont("Inter", 25, QFont.DemiBold))
    p.setPen(QColor("#9ecbff"))
    p.drawText(QRectF(64, 292, 560, 90), Qt.AlignLeft | Qt.TextWordWrap, "Free auto clicker for Windows and Linux")
    p.setFont(QFont("Inter", 17))
    p.setPen(QColor("#c9d6ea"))
    for i, line in enumerate(["Many points, each on its own schedule", "Background clicks without moving the mouse",
                              "Follows windows · X11 and Wayland · MIT"]):
        p.drawText(QRectF(64, 400 + i * 38, 560, 36), Qt.AlignLeft | Qt.AlignVCenter, "•  " + line)
    p.end()
    img.save(str(IMAGES / "social-preview.png"))


def picker():
    """The real overlay (frozen screenshot + magnifier + coordinates) over a sample desktop."""
    from pointclicker import overlay
    from pointclicker.platform import FakeBackend, set_backend
    fake = FakeBackend()
    set_backend(fake)
    geo = QGuiApplication.primaryScreen().geometry()
    desktop = QImage(geo.width(), geo.height(), QImage.Format_RGB32)
    p = QPainter(desktop)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    bg = QLinearGradient(0, 0, geo.width(), geo.height())
    bg.setColorAt(0, QColor("#5b8def"))
    bg.setColorAt(1, QColor("#a06cd5"))
    p.fillRect(desktop.rect(), bg)
    rounded(p, QImage(str(IMAGES / "schedule-light.png")), QRectF(40, 30, 715, 455), 10)
    p.end()
    with patch.object(overlay, "grab_screen", lambda s: desktop):
        o = overlay.PickOverlay()
        o.start()
        layer = o._layers[0]
        at = QPointF(588, 133)  # the edge of the Pause button: something to see in the magnifier
        fake.set_cursor_pos(int(at.x()), int(at.y()))
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        QApplication.sendEvent(layer, QMouseEvent(QEvent.MouseMove, at, layer.mapToGlobal(at), Qt.NoButton,
                                                  Qt.NoButton, Qt.NoModifier))
        shot = layer.grab().toImage()
        o._finish(picked=False, at=QPoint(0, 0))
    shot.copy(0, 0, min(shot.width(), 800), 500).save(str(IMAGES / "picker.png"))


social_preview()
picker()
print("wrote", IMAGES / "social-preview.png", "and", IMAGES / "picker.png")
