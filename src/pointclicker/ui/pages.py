"""Schedule/Run, Profiles and About pages."""
from __future__ import annotations

import sys
import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    IconWidget,
    InfoBadge,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    ToolTipFilter,
    TransparentToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from .. import targets as targets_mod
from .. import updates
from ..controller import LOG_DIR
from .style import APP_ICON, GRID, level_color
from .targets import interval_text
from .widgets import CommandBar, ElidedCaption, ElidedStrong, fmt_seconds


def _page(name: str, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    """A scrollable page with a title; returns (page, content layout)."""
    page = ScrollArea()
    page.setObjectName(name)
    page.setWidgetResizable(True)
    body = QWidget()
    page.setWidget(body)
    page.enableTransparentBackground()  # after setWidget: it only clears the widget present at call time
    v = QVBoxLayout(body)
    v.setContentsMargins(GRID * 4, GRID * 3, GRID * 4, GRID * 3)
    v.setSpacing(GRID * 2)
    v.addWidget(TitleLabel(title))
    if subtitle:
        sub = CaptionLabel(subtitle)
        sub.setWordWrap(True)
        v.addWidget(sub)
    return page, v


class RunPage(QWidget):
    COLUMNS = ["Target", "Every", "Mode", "Next click", "Clicks", "Last result"]

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("run")
        self.c = controller
        self.command_bar = CommandBar(controller)
        self.emergency = PushButton(FIF.POWER_BUTTON, "Emergency stop")
        self.emergency.clicked.connect(controller.emergency_stop)
        self.emergency.setToolTip("Stop and disable every target" +
                                  (f"  ({controller.cfg.hotkeys['emergency_stop']})"
                                   if controller.cfg.hotkeys.get("emergency_stop") else ""))
        self.emergency.installEventFilter(ToolTipFilter(self.emergency))

        self.table = TableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setBorderRadius(GRID)
        self.table.setBorderVisible(True)
        # Fixed widths sized once per rebuild: "resize to contents" re-measures a column over every row on each
        # cell change, which made rewriting a 500-row table take minutes.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)

        top = QHBoxLayout()
        top.addWidget(TitleLabel("Schedule"), 1)
        top.addWidget(self.emergency, 0, Qt.AlignBottom)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(GRID * 4, GRID * 3, GRID * 3, GRID * 2)
        layout.setSpacing(GRID * 2)
        layout.addLayout(top)
        layout.addWidget(self.command_bar)
        layout.addWidget(self.table, 1)
        controller.targets_changed.connect(self.rebuild)
        controller.target_updated.connect(self._update_row)
        controller.profiles_changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self, *_):
        ts = self.c.targets
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(ts))
        for r, t in enumerate(ts):
            self._fill_row(r, t)
        for col in (1, 2, 3, 4):
            self.table.resizeColumnToContents(col)
        self.table.setUpdatesEnabled(True)
        self.tick()

    def _fill_row(self, r, t):
        for col, text in enumerate([t.name, interval_text(t).removeprefix("Every "),
                                    t.mode.capitalize() + (" ×2" if t.click_type == "double" else ""), "", "", ""]):
            item = self.table.item(r, col)
            if item is None:
                self.table.setItem(r, col, QTableWidgetItem(text))
            elif col < 3 and item.text() != text:
                item.setText(text)

    def _update_row(self, target_id: str):
        for r, t in enumerate(self.c.targets):
            if t.id == target_id:
                self._fill_row(r, t)
                self.tick()
                return

    def tick(self, *_):
        for r, t in enumerate(self.c.targets):
            st = self.c.stats(t.id)
            text, level = self.c.status(t)
            self._set(r, 3, fmt_seconds(self.c.remaining(t.id)))
            self._set(r, 4, f"{st.clicks if st else 0}" + (f" / {t.repeat}" if t.repeat else ""))
            self._set(r, 5, text, level)
        self.command_bar.tick()

    def _set(self, r, c, text, level=None):
        item = self.table.item(r, c)
        if item and item.text() != text:
            item.setText(text)
        if item and level:
            item.setForeground(QColor(level_color(level)))


class NameDialog(MessageBoxBase):
    def __init__(self, title: str, text: str, parent):
        super().__init__(parent)
        self.edit = LineEdit()
        self.edit.setText(text)
        self.edit.setPlaceholderText("Profile name")
        self.edit.setClearButtonEnabled(True)
        self.viewLayout.addWidget(SubtitleLabel(title))
        self.viewLayout.addWidget(self.edit)
        self.widget.setMinimumWidth(GRID * 45)
        self.yesButton.setText("Save")
        self.cancelButton.setText("Cancel")


class ProfileCard(CardWidget):
    def __init__(self, name: str, page: ProfilesPage, parent=None):
        super().__init__(parent)
        c = page.c
        active = name == c.cfg.active_profile
        count = len(c.cfg.profiles[name])
        icon = IconWidget(FIF.PEOPLE)
        icon.setFixedSize(GRID * 3, GRID * 3)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(ElidedStrong(name))
        texts.addWidget(CaptionLabel(f"{count} target{'s' * (count != 1)}"))
        row = QHBoxLayout(self)
        row.setContentsMargins(GRID * 2, 12, GRID * 2, 12)
        row.setSpacing(GRID * 2)
        row.addWidget(icon)
        row.addLayout(texts, 1)
        if active:
            row.addWidget(InfoBadge.success("Active"))
        else:
            switch = PushButton("Switch to")
            switch.clicked.connect(lambda: c.switch_profile(name))
            row.addWidget(switch)
        for fi, tip, slot in ((FIF.EDIT, "Rename", lambda: page.rename(name)),
                              (FIF.DELETE, "Delete", lambda: page.delete(name))):
            b = TransparentToolButton(fi)
            b.setToolTip(tip)
            b.installEventFilter(ToolTipFilter(b))
            b.clicked.connect(slot)
            row.addWidget(b)
        if not active:
            self.clicked.connect(lambda: c.switch_profile(name))


class ProfilesPage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("profiles")
        self.c = controller
        self.page, self.v = _page("profiles-scroll", "Profiles",
                                  "Each profile is its own set of targets. Switching stops the clicker.")
        self.new_btn = PrimaryPushButton(FIF.ADD, "New profile")
        self.new_btn.clicked.connect(self.create)
        self.v.addWidget(self.new_btn, 0, Qt.AlignLeft)
        self.list = QVBoxLayout()
        self.list.setSpacing(GRID)
        self.v.addLayout(self.list)
        self.v.addStretch()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page)
        controller.profiles_changed.connect(self.rebuild)
        controller.targets_changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        while self.list.count():
            self.list.takeAt(0).widget().deleteLater()
        for name in self.c.profiles():
            self.list.addWidget(ProfileCard(name, self))

    def _ask(self, title: str, text: str = "") -> str | None:
        dlg = NameDialog(title, text, self.window())
        return dlg.edit.text() if dlg.exec() else None

    def create(self):
        if (name := self._ask("New profile")) is not None and (error := self.c.new_profile(name)):
            self.c.notify.emit("error", "Profile not created", error)

    def rename(self, name: str):
        if (new := self._ask("Rename profile", name)) is not None and new != name:
            if error := self.c.rename_profile(name, new):
                self.c.notify.emit("error", "Profile not renamed", error)

    def delete(self, name: str):
        n = len(self.c.cfg.profiles[name])
        box = MessageBox("Delete profile?", f"“{name}” and its {n} target{'s' * (n != 1)} will be deleted.",
                         self.window())
        box.yesButton.setText("Delete")
        box.cancelButton.setText("Cancel")
        if box.exec() and (error := self.c.delete_profile(name)):
            self.c.notify.emit("error", "Profile not deleted", error)


class AboutPage(QWidget):
    _update_checked = Signal(object)  # Update, None (up to date) or the exception, from the checking thread

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        self.c = controller
        self._update_checked.connect(self._show_update)
        caps = controller.caps
        page, v = _page("about-scroll", "About")

        hero = SimpleCardWidget()
        h = QHBoxLayout(hero)
        h.setContentsMargins(GRID * 3, GRID * 3, GRID * 3, GRID * 3)
        h.setSpacing(GRID * 3)
        icon = IconWidget(QIcon(str(APP_ICON)))
        icon.setFixedSize(GRID * 8, GRID * 8)
        texts = QVBoxLayout()
        texts.addWidget(SubtitleLabel("PointClicker"))
        texts.addWidget(CaptionLabel(f"Version {_version()}"))
        desc = BodyLabel("Clicks fixed points on screen on their own schedules, following their windows when they move.")
        desc.setWordWrap(True)
        texts.addWidget(desc)
        h.addWidget(icon, 0, Qt.AlignTop)
        h.addLayout(texts, 1)
        v.addWidget(hero)

        platform = SimpleCardWidget()
        p = QVBoxLayout(platform)
        p.setContentsMargins(GRID * 3, GRID * 2, GRID * 3, GRID * 2)
        p.setSpacing(GRID)
        p.addWidget(StrongBodyLabel(caps.get("platform_name", sys.platform)))
        rows = [("Clicking", caps.get("input_method", "—")),
                ("Background mode", "available" if caps.get("background_mode") else "not available"),
                ("Hotkeys", controller.hotkey_status())]
        for label, value in rows:
            line = QHBoxLayout()
            name = CaptionLabel(label)
            name.setFixedWidth(GRID * 16)
            line.addWidget(name)
            line.addWidget(ElidedCaption(str(value)), 1)
            p.addLayout(line)
        for note in caps.get("unavailable_features", []):
            n = CaptionLabel(f"• {note}")
            n.setWordWrap(True)
            n.setTextColor(QColor(level_color("muted")), QColor(level_color("muted")))
            p.addWidget(n)
        v.addWidget(platform)

        links = QHBoxLayout()
        links.setSpacing(GRID)
        for icon_, text, path in ((FIF.DOCUMENT, "Open log folder", LOG_DIR),
                                  (FIF.FOLDER, "Open config folder", targets_mod.config_path().parent)):
            b = PushButton(icon_, text)
            b.clicked.connect(lambda _=False, p_=path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p_))))
            links.addWidget(b)
        self.update_btn = PushButton(FIF.UPDATE, "Check for updates")
        self.update_btn.clicked.connect(self.check_updates)
        links.addWidget(self.update_btn)
        links.addStretch()
        v.addLayout(links)
        diag = CaptionLabel("Something not clicking? Run  pointclicker --diagnose  in a terminal for a full report "
                            "and a self-test click.")
        diag.setWordWrap(True)
        v.addWidget(diag)
        v.addStretch()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)


    def check_updates(self):
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Checking…")

        def work():
            try:
                result = updates.check()
            except Exception as e:  # offline, rate-limited, unexpected reply: tell the user, don't crash
                result = e
            self._update_checked.emit(result)
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _show_update(self, result):
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Check for updates")
        if isinstance(result, Exception):
            self.c.notify.emit("error", "Couldn't check for updates", str(result) or type(result).__name__)
        elif result is None:
            self.c.notify.emit("success", "You're up to date", f"PointClicker {_version()} is the latest version.")
        else:
            box = MessageBox(f"PointClicker {result.version} is available",
                             f"You have version {_version()}. Open the download page?", self.window())
            box.yesButton.setText("Open download page")
            box.cancelButton.setText("Later")
            if box.exec():
                QDesktopServices.openUrl(QUrl(result.url))


def _version() -> str:
    from .. import __version__  # not importlib.metadata: a PyInstaller build carries no package metadata
    return __version__
