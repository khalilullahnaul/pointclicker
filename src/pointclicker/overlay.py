"""Point-picking overlay and on-screen target markers."""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme, themeColor

from . import clicker
from .targets import Target, resolve_all

log = logging.getLogger(__name__)

HWND_TOPMOST = -1
MARKER_BATCH = 50
SWP_NOSIZE_NOACTIVATE = 0x0001 | 0x0010
LOUPE_PX, LOUPE_ZOOM = 15, 8  # the magnifier shows 15x15 screen pixels, each 8x8


def portal_screenshot() -> QImage | None:
    """The whole desktop via the Screenshot portal (Wayland apps can't capture the screen themselves)."""
    from .platform.linux.portal import Portal
    try:
        p = Portal()
        try:
            uri = p.request("org.freedesktop.portal.Screenshot", "Screenshot", "sa{sv}", "",
                            {"interactive": ("b", False)}, timeout=60)["uri"][1]
        finally:
            p.close()
        path = Path(unquote(urlparse(uri).path))
        try:
            img = QImage(str(path))
        finally:
            path.unlink(missing_ok=True)  # GNOME saves it into ~/Pictures; it was only taken for picking
        return None if img.isNull() else img
    except Exception as e:
        log.warning("screenshot for picking failed, showing the live screen instead: %s", e)
        return None


def split_desktop(img: QImage | None, screens) -> dict:
    """Cut a whole-desktop screenshot into one image per screen name (it may be in physical pixels: scale)."""
    if img is None or img.isNull() or not screens:
        return {}
    box = QRect()
    for sc in screens:
        box = box.united(sc.geometry())
    k = img.width() / box.width()
    return {sc.name(): img.copy(QRect(round((g.x() - box.x()) * k), round((g.y() - box.y()) * k),
                               round(g.width() * k), round(g.height() * k)))
            for sc in screens for g in [sc.geometry()]}


def grab_screen(screen) -> QImage | None:
    img = screen.grabWindow(0).toImage()  # X11 and Windows; Wayland gives nothing
    return None if img.isNull() else img


class _ScreenPicker(QWidget):
    """Full-screen layer for one monitor: a frozen screenshot of it (with a magnifier), or the dimmed live screen
    if no screenshot could be taken."""

    def __init__(self, screen, owner: PickOverlay, shot: QImage | None = None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self._owner, self._pos, self._origin = owner, None, screen.geometry().topLeft()
        self.shot = shot

    def global_pos(self) -> QPoint | None:
        """Where the pointer is, from our own events: the only source on Wayland (fullscreen on this screen)."""
        return None if self._pos is None else self._origin + self._pos

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.shot is not None:
            p.drawImage(QRectF(self.rect()), self.shot)  # exactly what was on screen: every window stays visible
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, 96))  # dimmed screen; fully transparent pixels wouldn't get clicks
        if self._pos is None:
            return
        accent, gap = themeColor(), 14
        x, y = self._pos.x(), self._pos.y()
        guide = QColor(accent)
        guide.setAlpha(180)
        p.setPen(QPen(guide, 1))
        p.drawLine(x, 0, x, y - gap)
        p.drawLine(x, y + gap, x, self.height())
        p.drawLine(0, y, x - gap, y)
        p.drawLine(x + gap, y, self.width(), y)
        center = QPointF(x + 0.5, y + 0.5)
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, 9, 9)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(center, 2, 2)
        if self._owner.from_events:
            cx, cy = self.global_pos().x(), self.global_pos().y()
        else:
            cx, cy = clicker.cursor_pos()
        self._paint_card(p, x, y, f"X {cx}    Y {cy}", "Click to pick · Esc or right-click to cancel")

    def _paint_loupe(self, p: QPainter, x: int, y: int, left: float, top: float):
        """The pixels around the pointer, magnified; the boxed one is the pixel that will be clicked."""
        size, half = LOUPE_PX * LOUPE_ZOOM, LOUPE_PX // 2
        k = self.shot.width() / max(1, self.width())  # screenshot pixels per logical pixel (HiDPI)
        box = QRectF(left, top, size, size)
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0))
        p.drawRoundedRect(box, 8, 8)
        p.setClipRect(box)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)  # hard pixel edges
        p.drawImage(box, self.shot, QRectF((x - half) * k, (y - half) * k, LOUPE_PX * k, LOUPE_PX * k))
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(themeColor(), 2))
        p.drawRect(QRectF(left + half * LOUPE_ZOOM, top + half * LOUPE_ZOOM, LOUPE_ZOOM, LOUPE_ZOOM))
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        p.drawRoundedRect(box, 8, 8)
        p.restore()

    def _paint_card(self, p: QPainter, x: int, y: int, title: str, caption: str):
        """Fluent-style tooltip card (under the magnifier, if any) next to the pointer, flipped to stay on screen."""
        dark = isDarkTheme()
        title_font, caption_font = QFont(self.font()), QFont(self.font())
        title_font.setWeight(QFont.DemiBold)
        title_font.setPointSizeF(self.font().pointSizeF() * 1.1)
        caption_font.setPointSizeF(self.font().pointSizeF() * 0.9)
        tm, cm = QFontMetrics(title_font), QFontMetrics(caption_font)
        pad = 12
        w = max(tm.horizontalAdvance(title), cm.horizontalAdvance(caption)) + 2 * pad
        h = tm.height() + cm.height() + 2 * pad + 2
        loupe = LOUPE_PX * LOUPE_ZOOM + 8 if self.shot is not None else 0
        bw, bh = max(w, loupe), h + loupe
        left = x + 20 if x + 20 + bw < self.width() else x - 20 - bw
        top = y + 20 if y + 20 + bh < self.height() else y - 20 - bh
        if loupe:
            self._paint_loupe(p, x, y, left, top)
        card = QRectF(left, top + loupe, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))  # soft shadow
        p.drawRoundedRect(card.translated(0, 2).adjusted(-1, -1, 1, 1), 9, 9)
        p.setPen(QPen(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 26), 1))
        p.setBrush(QColor(43, 43, 43, 248) if dark else QColor(252, 252, 252, 248))
        p.drawRoundedRect(card, 8, 8)
        p.setFont(title_font)
        p.setPen(QColor(255, 255, 255) if dark else QColor(27, 27, 27))
        p.drawText(card.adjusted(pad, pad, -pad, 0), Qt.AlignLeft | Qt.AlignTop, title)
        p.setFont(caption_font)
        p.setPen(QColor(200, 200, 200) if dark else QColor(96, 96, 96))
        p.drawText(card.adjusted(pad, pad + tm.height() + 2, -pad, 0), Qt.AlignLeft | Qt.AlignTop, caption)

    def mouseMoveEvent(self, e):
        self._pos = e.position().toPoint()
        self.update()

    def leaveEvent(self, _):
        self._pos = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.RightButton):
            self._pos = e.position().toPoint()
            self._owner._finish(picked=e.button() == Qt.LeftButton, at=self.global_pos())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._owner._finish(picked=False)


class PickOverlay(QObject):
    """Covers every monitor; the next left click becomes a Target, Esc cancels."""

    picked = Signal(object)  # Target
    cancelled = Signal()
    _captured = Signal(object)  # whole-desktop QImage or None, from the screenshot thread

    def __init__(self):
        super().__init__()
        self._layers: list[_ScreenPicker] = []
        self._capturing = False
        self.from_events = False  # True where the global cursor can't be read (Wayland)
        self._captured.connect(self._show)

    @property
    def active(self) -> bool:
        return bool(self._layers) or self._capturing

    def start(self):
        """Freeze the screen first (so every window shows and the magnifier has pixels), then cover it."""
        if self.active:
            return
        screens = QGuiApplication.screens()
        if not screens:
            self.cancelled.emit()
            return
        if QGuiApplication.platformName() == "wayland":
            self._capturing = True  # the portal may take a moment: don't block the GUI thread
            threading.Thread(target=lambda: self._captured.emit(portal_screenshot()), name="screenshot",
                             daemon=True).start()
        else:
            self._show({s.name(): shot for s in screens if (shot := grab_screen(s)) is not None})

    def _show(self, shots):
        self._capturing = False
        screens = QGuiApplication.screens()
        if not screens:
            self.cancelled.emit()
            return
        if not isinstance(shots, dict):
            shots = split_desktop(shots, screens)
        self.from_events = not clicker.get_backend().capabilities().get("can_read_cursor", True)
        wayland = QGuiApplication.platformName() == "wayland"
        self._layers = [_ScreenPicker(s, self, shots.get(s.name())) for s in screens]
        for w in self._layers:
            # Wayland apps can't place windows; fullscreen-on-this-screen is the only way to cover each monitor.
            w.showFullScreen() if wayland else w.show()
        under = QGuiApplication.screenAt(QCursor.pos())
        active = next((w for w in self._layers if w.screen() == under), self._layers[0])
        active.raise_()
        active.activateWindow()

    def _finish(self, picked: bool, at: QPoint | None = None):
        x, y = (at.x(), at.y()) if self.from_events and at is not None else clicker.cursor_pos()
        for w in self._layers:
            w.close()
        self._layers = []
        if picked:
            self.picked.emit(Target.from_point(x, y))
        else:
            self.cancelled.emit()


class _Marker(QWidget):
    SIZE = 22

    def __init__(self, number: int, target: Target):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.number, self.target = number, target

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(themeColor() if self.target.enabled else QColor(120, 120, 120))
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        p.drawText(self.rect(), Qt.AlignCenter, str(self.number))

    def place(self, x: int, y: int):
        if sys.platform == "win32":
            hwnd = int(self.winId())
            r = clicker.window_rect(hwnd)
            if r:
                w, h = r.right - r.left, r.bottom - r.top
                clicker.SetWindowPos(hwnd, HWND_TOPMOST, x - w // 2, y - h // 2, 0, 0, SWP_NOSIZE_NOACTIVATE)
        else:
            self.move(x - self.width() // 2, y - self.height() // 2)


class MarkerLayer(QObject):
    """Numbered, click-through markers at each target; follows windows as they move."""

    def __init__(self):
        super().__init__()
        self._markers: list[_Marker] = []
        self._placed: dict[int, tuple[int, int] | None] = {}  # id(marker) -> last position
        self._pending: list[tuple[int, Target]] = []  # markers still to create
        self._timer = QTimer(self, interval=500, timeout=self._refresh)

    def show_targets(self, targets: list[Target]):
        """Reconcile with the target list, reusing marker windows (each is a top-level window: costly to create)."""
        old = {m.target.id: m for m in self._markers}
        self._markers, pending = [], []
        for i, t in enumerate(targets, 1):
            if m := old.pop(t.id, None):
                if (m.number, m.target) != (i, t):
                    m.number, m.target = i, t
                    m.update()
                self._markers.append(m)
            else:
                pending.append((i, t))
        for m in old.values():
            self._placed.pop(id(m), None)
            m.close()
            m.deleteLater()
        self._pending = pending
        self._create_some()
        self._timer.start()

    def _create_some(self):
        """Marker windows are created MARKER_BATCH per event-loop turn, so hundreds don't freeze the UI."""
        batch, self._pending = self._pending[:MARKER_BATCH], self._pending[MARKER_BATCH:]
        for i, t in batch:
            self._markers.append(_Marker(i, t))
        self._refresh()
        if self._pending:
            QTimer.singleShot(0, self._create_some)

    def clear(self):
        self._timer.stop()
        for m in self._markers:
            m.close()
            m.deleteLater()
        self._markers = []
        self._placed = {}
        self._pending = []

    def _refresh(self):
        positions = resolve_all([m.target for m in self._markers])
        for m in self._markers:
            pos = positions.get(m.target.id)
            if pos == self._placed.get(id(m), ()) and m.isVisible() == (pos is not None):
                continue  # unchanged: don't touch the window
            self._placed[id(m)] = pos
            if pos is None:
                m.hide()
            else:
                m.show()
                m.place(*pos)
