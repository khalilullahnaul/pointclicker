"""Small reusable widgets: command bar, hotkey recorder, eliding labels, setting-card helper."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CompactDoubleSpinBox,
    CompactSpinBox,
    PrimaryPushButton,
    PushButton,
    SettingCard,
    SimpleCardWidget,
    StrongBodyLabel,
    ToolTipFilter,
)
from qfluentwidgets import FluentIcon as FIF

from ..scheduler import State
from .style import GRID, AppIcon


def fmt_seconds(s: float | None) -> str:
    if s is None:
        return "–"
    h, rest = divmod(int(s + 0.999), 3600)  # round up so the countdown reads 0 when the click fires
    m, s = divmod(rest, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"


class _EditFieldFix:
    """Qt 6.12 lays out a spin box that has style-sheet padding and NoButtons (what qfluentwidgets uses, since
    it draws its own buttons) with a 1 px wide edit field, so the number is invisible. Re-lay it out between
    the left padding and our buttons. Does nothing on Qt versions without the bug."""

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fix_edit_field)  # after the button layout has placed the spin buttons

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._fix_edit_field)

    def _fix_edit_field(self):
        edit = self.lineEdit()
        if edit.width() > 1:
            return
        buttons = [b for b in self.findChildren(QAbstractButton) if "SpinButton" in type(b).__name__ and b.isVisible()]
        right = min((b.x() for b in buttons), default=self.width())
        edit.setGeometry(10, 1, max(right - 12, GRID), self.height() - 2)


class SpinBox(_EditFieldFix, CompactSpinBox):
    pass


class DoubleSpinBox(_EditFieldFix, CompactDoubleSpinBox):
    pass


class _Elide:
    """Mixin: the label shrinks with '…' instead of widening the window."""

    def init_elide(self, text: str = ""):
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(GRID * 4)
        self.set_full_text(text)

    def set_full_text(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width(), GRID * 4))
        if shown != self.text():
            self.setText(shown)


class ElidedBody(_Elide, BodyLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.init_elide(text)


class ElidedStrong(_Elide, StrongBodyLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.init_elide(text)


class ElidedCaption(_Elide, CaptionLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.init_elide(text)


def control_card(icon, title: str, content: str | None, *controls: QWidget, parent=None) -> SettingCard:
    """A Fluent setting card with our own controls on the right (no qconfig binding)."""
    card = SettingCard(icon, title, content, parent)
    for w in controls:
        card.hBoxLayout.addWidget(w, 0, Qt.AlignRight)
        card.hBoxLayout.addSpacing(GRID)
    card.hBoxLayout.addSpacing(GRID)
    return card


class CommandBar(SimpleCardWidget):
    """Start (accent) / Pause-Resume / Stop with hotkey hints, wired straight to controller slots."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.state_label = StrongBodyLabel()
        self.next_label = ElidedCaption()
        self.start_btn = PrimaryPushButton(FIF.PLAY, "Start")
        self.pause_btn = PushButton(FIF.PAUSE, "Pause")
        self.stop_btn = PushButton(AppIcon.STOP, "Stop")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.state_label)
        texts.addWidget(self.next_label)
        row = QHBoxLayout(self)
        row.setContentsMargins(GRID * 2, 12, GRID * 2, 12)
        row.setSpacing(GRID)
        row.addLayout(texts, 1)
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            b.setMinimumHeight(GRID * 5)
            b.installEventFilter(ToolTipFilter(b))
            row.addWidget(b)
        self.start_btn.clicked.connect(self.c.start)
        self.pause_btn.clicked.connect(self.c.pause_resume)
        self.stop_btn.clicked.connect(self.c.stop)
        self.c.state_changed.connect(self.refresh)
        self.c.settings_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        state, keys = self.c.state, self.c.cfg.hotkeys
        hint = lambda action: f"  {keys[action]}" if keys.get(action) else ""  # noqa: E731
        compact = self.width() < GRID * 65  # small windows: icons only, hints move to tooltips
        self.start_btn.setText("" if compact else "Start" + hint("start_pause"))
        self.pause_btn.setText("" if compact else ("Resume" if state is State.PAUSED else "Pause") + hint("start_pause"))
        self.stop_btn.setText("" if compact else "Stop" + hint("stop"))
        self.start_btn.setToolTip(f"Start{hint('start_pause')}")
        self.pause_btn.setToolTip(f"{'Resume' if state is State.PAUSED else 'Pause'}{hint('start_pause')}")
        self.stop_btn.setToolTip(f"Stop{hint('stop')}")
        self.pause_btn.setIcon(FIF.PLAY if state is State.PAUSED else FIF.PAUSE)
        self.start_btn.setEnabled(state is not State.RUNNING)
        self.pause_btn.setEnabled(state is not State.STOPPED)
        self.stop_btn.setEnabled(state is not State.STOPPED)
        self.state_label.setText({State.STOPPED: "Stopped", State.RUNNING: "Running", State.PAUSED: "Paused"}[state])
        self.tick()

    def tick(self):
        nxt = self.c.next_click()
        self.next_label.set_full_text(f"Next: {nxt[0]} in {fmt_seconds(nxt[1])}" if nxt else
                                      f"{sum(t.enabled for t in self.c.targets)} enabled target(s)")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.refresh()


class HotkeyRecorder(PushButton):
    """Click, then press the combination. Esc cancels, Backspace clears."""

    recorded = Signal(str)  # combo like "Ctrl+Alt+F6", "" = cleared
    recording = Signal(bool)

    def __init__(self, combo: str, parent=None):
        super().__init__(parent)  # PushButton's (icon, text) overload re-enters __init__, which our signature breaks
        self.setIcon(AppIcon.KEYBOARD)
        self.setText(combo or "Not set")
        self.setMinimumWidth(GRID * 18)
        self._combo, self._active = combo, False
        self.clicked.connect(self._begin)

    def set_combo(self, combo: str):
        self._combo = combo
        if not self._active:
            self.setText(combo or "Not set")

    def _begin(self):
        self._active = True
        self.recording.emit(True)
        self.setText("Press keys…")
        self.grabKeyboard()

    def _end(self, combo: str | None):
        self.releaseKeyboard()
        self._active = False
        self.recording.emit(False)
        self.setText(self._combo or "Not set")
        if combo is not None:
            self.recorded.emit(combo)

    def keyPressEvent(self, e):
        if not self._active:
            return super().keyPressEvent(e)
        key, mods = e.key(), e.modifiers() & ~Qt.KeypadModifier
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr):
            prefix = QKeySequence(mods.value).toString(QKeySequence.PortableText)
            self.setText(f"{prefix}…")
            return
        if key == Qt.Key_Escape and not mods:
            return self._end(None)
        if key in (Qt.Key_Backspace, Qt.Key_Delete) and not mods:
            return self._end("")
        self._end(QKeySequence(e.keyCombination()).toString(QKeySequence.PortableText))

    def focusOutEvent(self, e):
        if self._active:
            self._end(None)
        super().focusOutEvent(e)
