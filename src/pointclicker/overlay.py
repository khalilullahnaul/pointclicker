"""Point-picking overlay and on-screen target markers."""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme, themeColor

from . import clicker
from .targets import Target, resolve_all

HWND_TOPMOST = -1
MARKER_BATCH = 50
SWP_NOSIZE_NOACTIVATE = 0x0001 | 0x0010


class _ScreenPicker(QWidget):
    """Dimmed full-screen layer for one monitor."""

    def __init__(self, screen, owner: PickOverlay):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self._owner, self._pos, self._origin = owner, None, screen.geometry().topLeft()

    def global_pos(self) -> QPoint | None:
        """Where the pointer is, from our own events: the only source on Wayland (fullscreen on this screen)."""
        return None if self._pos is None else self._origin + self._pos

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
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

    def _paint_card(self, p: QPainter, x: int, y: int, title: str, caption: str):
        """Fluent-style tooltip card next to the pointer, flipped to stay on screen."""
        dark = isDarkTheme()
        title_font, caption_font = QFont(self.font()), QFont(self.font())
        title_font.setWeight(QFont.DemiBold)
        title_font.setPointSizeF(self.font().pointSizeF() * 1.1)
        caption_font.setPointSizeF(self.font().pointSizeF() * 0.9)
        tm, cm = QFontMetrics(title_font), QFontMetrics(caption_font)
        pad = 12
        w = max(tm.horizontalAdvance(title), cm.horizontalAdvance(caption)) + 2 * pad
        h = tm.height() + cm.height() + 2 * pad + 2
        left = x + 20 if x + 20 + w < self.width() else x - 20 - w
        top = y + 20 if y + 20 + h < self.height() else y - 20 - h
        card = QRectF(left, top, w, h)
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

    def __init__(self):
        super().__init__()
        self._layers: list[_ScreenPicker] = []
        self.from_events = False  # True where the global cursor can't be read (Wayland)

    @property
    def active(self) -> bool:
        return bool(self._layers)

    def start(self):
        if self._layers:
            return
        screens = QGuiApplication.screens()
        if not screens:
            self.cancelled.emit()
            return
        self.from_events = not clicker.get_backend().capabilities().get("can_read_cursor", True)
        wayland = QGuiApplication.platformName() == "wayland"
        self._layers = [_ScreenPicker(s, self) for s in screens]
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
