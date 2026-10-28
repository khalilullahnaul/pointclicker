"""Targets page: target cards, empty state, and the edit dialog."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (Action, BodyLabel, CaptionLabel, CardWidget, ComboBox, FluentIcon as FIF, IconWidget, InfoBadge, LineEdit, MessageBoxBase,
                            PrimaryPushButton, ProgressRing, RoundMenu, ScrollArea, SegmentedWidget, SubtitleLabel,
                            SwitchButton, TitleLabel, ToolTipFilter, TransparentToolButton, themeColor)

from ..targets import _CHOICES, Target
from .style import GRID, AppIcon, level_color
from .widgets import CommandBar, DoubleSpinBox, ElidedCaption, ElidedStrong, SpinBox, fmt_seconds

UNIT_LABELS = {"seconds": "sec", "minutes": "min", "hours": "hr"}


def interval_text(t: Target) -> str:
    text = f"Every {t.interval:g} {UNIT_LABELS[t.interval_unit]}"
    return text + (f" ±{t.jitter_pct:g}%" if t.jitter_pct else "")


class NumberBadge(QWidget):
    """Accent-colored circle with the target number."""

    def __init__(self, number: int, parent=None):
        super().__init__(parent)
        self.number = number
        self.setFixedSize(GRID * 4, GRID * 4)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(themeColor())
        p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        p.setPen(QColor("white"))
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, str(self.number))


class TargetCard(CardWidget):
    edit_requested = Signal(str)

    def __init__(self, number: int, t: Target, controller, parent=None):
        super().__init__(parent)
        self.t, self.c = t, controller
        self.setMinimumHeight(GRID * 9)

        self.badge = NumberBadge(number)
        self.name = ElidedStrong(t.name)
        where = f"{t.x}, {t.y}" + (f"  ·  {t.window_title or t.window_class}" if t.window_class else "  ·  screen point")
        self.detail = ElidedCaption(where)
        self.status = CaptionLabel()
        text = QVBoxLayout()
        text.setSpacing(2)
        for w in (self.name, self.detail, self.status):
            text.addWidget(w)

        self.interval = CaptionLabel(interval_text(t))
        light, dark = (themeColor(), themeColor()) if t.mode == "background" else (QColor("#5f5f5f"), QColor("#9f9f9f"))
        self.mode = InfoBadge.custom(("Double · " if t.click_type == "double" else "") + t.mode.capitalize(), light, dark)
        self.meta = QWidget()
        meta = QVBoxLayout(self.meta)
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(4)
        meta.addWidget(self.interval, 0, Qt.AlignRight)
        meta.addWidget(self.mode, 0, Qt.AlignRight)

        self.ring = ProgressRing(useAni=False)
        self.ring.setFixedSize(GRID * 4, GRID * 4)
        self.ring.setStrokeWidth(4)
        self.ring.setTextVisible(False)
        self.ring.setRange(0, 1000)
        self.countdown = BodyLabel("–")
        self.countdown.setMinimumWidth(GRID * 6)
        self.clicks = CaptionLabel()
        self.clicks.setMinimumWidth(GRID * 7)

        self.actions = QWidget()
        acts = QHBoxLayout(self.actions)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(0)
        for icon, tip, slot in self._action_specs():
            b = TransparentToolButton(icon)
            b.setToolTip(tip)
            b.installEventFilter(ToolTipFilter(b))
            b.clicked.connect(slot)
            acts.addWidget(b)
        self.actions.setVisible(False)  # hover actions; the context menu offers the same

        self.switch = SwitchButton()
        self.switch.setOnText("")
        self.switch.setOffText("")
        self.switch.setChecked(t.enabled)
        self.switch.checkedChanged.connect(lambda on: self.c.set_enabled(self.t.id, on))
        self.switch.setToolTip("Enabled")

        row = QHBoxLayout(self)
        row.setContentsMargins(GRID * 2, 12, GRID * 2, 12)
        row.setSpacing(GRID * 2)
        row.addWidget(self.badge)
        row.addLayout(text, 1)
        row.addWidget(self.meta)
        row.addWidget(self.ring)
        row.addWidget(self.countdown)
        row.addWidget(self.clicks)
        row.addWidget(self.actions)
        row.addWidget(self.switch)
        self.clicked.connect(lambda: self.edit_requested.emit(self.t.id))
        self.refresh()

    def _action_specs(self):
        tid = self.t.id
        return [(FIF.EDIT, "Edit", lambda: self.edit_requested.emit(tid)),
                (FIF.SEND, "Test click now", lambda: self.c.test_click(tid)),
                (FIF.COPY, "Duplicate", lambda: self.c.duplicate_target(tid)),
                (FIF.DELETE, "Delete", lambda: self.c.delete_target(tid))]

    def refresh(self):
        """Live parts only: countdown, ring, clicks, status."""
        left, st = self.c.remaining(self.t.id), self.c.stats(self.t.id)
        self.countdown.setText(fmt_seconds(left))
        self.ring.setValue(int(self.c.progress(self.t) * 1000))
        n = st.clicks if st else 0
        plural = (self.t.repeat or n) != 1  # "1 / 8 clicks", "1 click", "0 clicks"
        self.clicks.setText(f"{n}{f' / {self.t.repeat}' if self.t.repeat else ''} click{'s' * plural}")
        text, level = self.c.status(self.t)
        if text != self.status.text():
            self.status.setText(text)
        self.status.setTextColor(QColor(level_color(level)), QColor(level_color(level)))

    def enterEvent(self, e):
        self.actions.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.actions.setVisible(False)
        super().leaveEvent(e)

    def contextMenuEvent(self, e):
        menu = RoundMenu(parent=self)
        for icon, text, slot in self._action_specs():
            menu.addAction(Action(icon, text, triggered=slot))
        menu.exec(e.globalPos())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < GRID * 75
        self.meta.setVisible(not narrow)  # small windows: the edit dialog still shows interval/mode
        self.clicks.setVisible(self.width() >= GRID * 60)


class TargetsPage(QWidget):
    pick_requested = Signal()
    edit_requested = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("targets")
        self.c = controller
        self.cards: list[TargetCard] = []

        self.title = TitleLabel("Targets")
        self.subtitle = CaptionLabel()
        self.add_btn = PrimaryPushButton(AppIcon.CROSSHAIR, "Pick a point")
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        header.addWidget(self.add_btn, 0, Qt.AlignBottom)
        self.add_btn.clicked.connect(self.pick_requested)

        self.command_bar = CommandBar(controller)

        self.list = QWidget()
        self.list_layout = QVBoxLayout(self.list)
        self.list_layout.setContentsMargins(0, 0, GRID, 0)
        self.list_layout.setSpacing(GRID)
        self.list_layout.addStretch()
        self.scroll = ScrollArea()
        self.scroll.setWidget(self.list)
        self.scroll.setWidgetResizable(True)
        self.scroll.enableTransparentBackground()

        self.empty = self._empty_state()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(GRID * 4, GRID * 3, GRID * 3, GRID * 2)
        layout.setSpacing(GRID * 2)
        layout.addLayout(header)
        layout.addWidget(self.command_bar)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.empty, 1)

        controller.targets_changed.connect(self.rebuild)
        controller.profiles_changed.connect(self.rebuild)
        controller.target_updated.connect(self.tick)
        self.rebuild()

    def _empty_state(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(GRID * 2)
        v.addStretch()
        icon = IconWidget(AppIcon.CROSSHAIR)
        icon.setFixedSize(GRID * 8, GRID * 8)
        v.addWidget(icon, 0, Qt.AlignHCenter)
        v.addWidget(SubtitleLabel("No targets yet"), 0, Qt.AlignHCenter)
        hint = BodyLabel("Pick a point on screen and PointClicker will click it on your schedule.")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        v.addWidget(hint)
        self.empty_pick_btn = PrimaryPushButton(AppIcon.CROSSHAIR, "Pick a point")
        self.empty_pick_btn.setMinimumSize(GRID * 25, GRID * 6)
        self.empty_pick_btn.clicked.connect(self.pick_requested)
        v.addWidget(self.empty_pick_btn, 0, Qt.AlignHCenter)
        v.addStretch()
        return w

    def rebuild(self):
        for card in self.cards:
            card.deleteLater()
        self.cards = []
        for i, t in enumerate(self.c.targets, 1):
            card = TargetCard(i, t, self.c)
            card.edit_requested.connect(self.edit_requested)
            self.list_layout.insertWidget(i - 1, card)
            self.cards.append(card)
        empty = not self.cards
        self.empty.setVisible(empty)
        self.scroll.setVisible(not empty)
        self.add_btn.setVisible(not empty)
        n = len(self.cards)
        self.subtitle.setText(f"Profile “{self.c.cfg.active_profile}” · {n} target{'s' * (n != 1)}")

    def tick(self, *_):
        for card in self.cards:
            card.refresh()
        self.command_bar.tick()


class TargetDialog(MessageBoxBase):
    """Edit every Target field. validate() builds the Target, so invalid input never leaves the dialog."""

    def __init__(self, t: Target, controller, parent, title: str):
        super().__init__(parent)
        self.t, self.result_target = t, None
        supports_bg = controller.supports_background()
        note = controller.caps.get("background_note", "")

        self.name = LineEdit()
        self.name.setText(t.name)
        self.x, self.y = self._spin(t.x), self._spin(t.y)
        self.window_title = LineEdit()
        self.window_title.setText(t.window_title)
        self.window_class = LineEdit()
        self.window_class.setText(t.window_class)
        self.window_class.setPlaceholderText("empty = fixed screen point")
        self.rel_x, self.rel_y = self._spin(t.rel_x), self._spin(t.rel_y)
        self.button = ComboBox()
        for b in _CHOICES["button"]:
            self.button.addItem(b.capitalize(), userData=b)
        self.button.setCurrentIndex(_CHOICES["button"].index(t.button))
        self.double = SwitchButton()
        self.double.setChecked(t.click_type == "double")
        self.mode = SegmentedWidget()
        self.mode.addItem("background", "Background")
        self.mode.addItem("foreground", "Foreground")
        self.mode.items["background"].setEnabled(supports_bg)
        self.mode.setCurrentItem(t.mode if supports_bg else "foreground")
        self.interval = DoubleSpinBox()
        self.interval.setRange(0.001, 1_000_000)
        self.interval.setDecimals(3)
        self.interval.setValue(t.interval)
        self.unit = ComboBox()
        for u in _CHOICES["interval_unit"]:
            self.unit.addItem(UNIT_LABELS[u], userData=u)
        self.unit.setCurrentIndex(_CHOICES["interval_unit"].index(t.interval_unit))
        self.start_delay = DoubleSpinBox()
        self.start_delay.setRange(0, 1_000_000)
        self.start_delay.setValue(t.start_delay)
        self.start_delay.setSuffix(" s")
        self.repeat = SpinBox()
        self.repeat.setRange(0, 1_000_000)
        self.repeat.setValue(t.repeat)
        self.repeat.setSpecialValueText("forever")
        self.jitter = SpinBox()
        self.jitter.setRange(0, 99)
        self.jitter.setValue(int(t.jitter_pct))
        self.jitter.setPrefix("± ")
        self.jitter.setSuffix(" %")
        self.enabled = SwitchButton()
        self.enabled.setChecked(t.enabled)

        left, right = self._form(), self._form()
        left.addRow("Name", self.name)
        left.addRow("Screen X / Y", self._pair(self.x, self.y))
        left.addRow("Window title", self.window_title)
        left.addRow("Window class", self.window_class)
        left.addRow("Offset X / Y", self._pair(self.rel_x, self.rel_y))
        left.addRow("Enabled", self.enabled)
        right.addRow("Click mode", self.mode)
        right.addRow("Button", self.button)
        right.addRow("Double click", self.double)
        right.addRow("Interval", self._pair(self.interval, self.unit))
        right.addRow("Start delay", self.start_delay)
        right.addRow("Repeat", self.repeat)
        right.addRow("Jitter", self.jitter)

        columns = QHBoxLayout()
        columns.setSpacing(GRID * 3)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        self.error = CaptionLabel()
        self.error.setTextColor(QColor(level_color("error")), QColor(level_color("error")))
        self.error.setWordWrap(True)
        self.error.hide()
        self.viewLayout.addWidget(SubtitleLabel(title))
        self.viewLayout.addLayout(columns)
        if note:
            hint = CaptionLabel(note)
            hint.setWordWrap(True)
            self.viewLayout.addWidget(hint)
        self.viewLayout.addWidget(self.error)
        self.viewLayout.setSpacing(GRID * 2)
        self.yesButton.setText("Save")
        self.cancelButton.setText("Cancel")
        self.widget.setMinimumWidth(min(GRID * 82, max(GRID * 40, parent.width() - GRID * 8)))

    @staticmethod
    def _spin(value: int) -> SpinBox:
        s = SpinBox()
        s.setRange(-100_000, 100_000)
        s.setValue(value)
        return s

    @staticmethod
    def _pair(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(GRID)
        h.addWidget(a, 1)
        h.addWidget(b, 1)
        return w

    @staticmethod
    def _form() -> QFormLayout:
        f = QFormLayout()
        f.setHorizontalSpacing(GRID * 2)
        f.setVerticalSpacing(GRID)
        f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return f

    def values(self) -> dict:
        v = {
            "name": self.name.text().strip(), "x": self.x.value(), "y": self.y.value(),
            "window_title": self.window_title.text(), "window_class": self.window_class.text().strip(),
            "rel_x": self.rel_x.value(), "rel_y": self.rel_y.value(), "button": self.button.currentData(),
            "click_type": "double" if self.double.isChecked() else "single",
            "mode": self.mode.currentRouteKey(), "interval": self.interval.value(),
            "interval_unit": self.unit.currentData(), "start_delay": self.start_delay.value(),
            "repeat": self.repeat.value(), "jitter_pct": float(self.jitter.value()), "enabled": self.enabled.isChecked(),
        }
        return v

    def validate(self) -> bool:
        try:
            self.result_target = Target(id=self.t.id, **self.values())
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return False
        return True
