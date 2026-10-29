"""Settings page: appearance, hotkeys, behavior, Linux permissions."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    ColorPickerButton,
    ComboBox,
    MessageBox,
    PushButton,
    SettingCardGroup,
    SwitchButton,
    TransparentPushButton,
)
from qfluentwidgets import FluentIcon as FIF

from ..controller import ACTIONS
from .pages import _page
from .style import GRID, AppIcon, accent_for, apply_theme
from .widgets import HotkeyRecorder, control_card


def _switch(checked: bool, slot) -> SwitchButton:
    s = SwitchButton()
    s.setOnText("On")
    s.setOffText("Off")
    s.setChecked(checked)
    s.checkedChanged.connect(slot)
    return s


class SettingsPage(QWidget):
    def __init__(self, controller, markers_supported: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self.c = c = controller
        cfg = c.cfg
        page, v = _page("settings-scroll", "Settings")
        v.setSpacing(GRID * 3)

        # --- appearance ---
        look = SettingCardGroup("Appearance", page)
        self.theme = ComboBox()
        for key, text in (("system", "Use system setting"), ("light", "Light"), ("dark", "Dark")):
            self.theme.addItem(text, userData=key)
        self.theme.setCurrentIndex(["system", "light", "dark"].index(cfg.theme))
        self.theme.currentIndexChanged.connect(lambda _: self._set_look("theme", self.theme.currentData()))
        look.addSettingCard(control_card(FIF.BRUSH, "Theme", "Follows your OS light/dark setting by default",
                                         self.theme, parent=look))
        self.accent = ColorPickerButton(accent_for(cfg), "Accent color")
        self.accent.colorChanged.connect(lambda col: self._set_look("accent", col.name()))
        reset = TransparentPushButton("Use system accent")
        reset.clicked.connect(self._reset_accent)
        look.addSettingCard(control_card(FIF.PALETTE, "Accent color",
                                         "Windows accent color by default" if sys.platform == "win32" else None,
                                         reset, self.accent, parent=look))
        v.addWidget(look)

        # --- hotkeys ---
        keys = SettingCardGroup("Hotkeys", page)
        self.recorders: dict[str, HotkeyRecorder] = {}
        for action, label in ACTIONS.items():
            rec = HotkeyRecorder(cfg.hotkeys.get(action, ""))
            rec.recording.connect(c.suspend_hotkeys)
            rec.recorded.connect(lambda combo, a=action: self._set_hotkey(a, combo))
            self.recorders[action] = rec
            keys.addSettingCard(control_card(AppIcon.KEYBOARD, label, "Click, then press keys · Backspace clears",
                                             rec, parent=keys))
        self.hotkey_card = control_card(FIF.INFO, "Hotkey status", c.hotkey_status(), parent=keys)
        keys.addSettingCard(self.hotkey_card)
        v.addWidget(keys)

        # --- behavior ---
        beh = SettingCardGroup("Behavior", page)
        self.default_mode = ComboBox()
        for mode in ("background", "foreground"):
            self.default_mode.addItem(mode.capitalize(), userData=mode)
        if not c.supports_background():
            self.default_mode.setEnabled(False)
            self.default_mode.setCurrentIndex(1)
        else:
            self.default_mode.setCurrentIndex(["background", "foreground"].index(cfg.default_mode))
        self.default_mode.currentIndexChanged.connect(
            lambda _: c.set_setting("default_mode", self.default_mode.currentData()))
        beh.addSettingCard(control_card(FIF.APPLICATION, "Default click mode",
                                        "For newly picked points" if c.supports_background()
                                        else "Only foreground is available here", self.default_mode, parent=beh))
        for icon, title, content, key in (
                (FIF.MINIMIZE, "Minimize to tray", "The minimize button hides PointClicker in the tray",
                 "minimize_to_tray"),
                (FIF.HIDE, "Start minimized", "Open straight to the tray", "start_minimized"),
                (FIF.PLAY, "Start clicking on launch", "Run the active profile as soon as the app opens",
                 "start_clicking_on_launch"),
                (FIF.POWER_BUTTON, "Launch at login", "Start PointClicker when you sign in", "launch_at_login")):
            beh.addSettingCard(control_card(icon, title, content,
                                            _switch(getattr(cfg, key), lambda on, k=key: c.set_setting(k, on)),
                                            parent=beh))
        markers = _switch(cfg.show_markers and markers_supported, lambda on: c.set_setting("show_markers", on))
        markers.setEnabled(markers_supported)
        beh.addSettingCard(control_card(FIF.PIN, "Show target markers",
                                        "Numbered dots where each target clicks" if markers_supported else
                                        "Not possible on native Wayland", markers, parent=beh))
        v.addWidget(beh)

        # --- linux permissions ---
        if sys.platform.startswith("linux"):
            linux = SettingCardGroup("Linux permissions", page)
            self.perm_card = control_card(FIF.CERTIFICATE, "Input access", "", parent=linux)
            guide = PushButton("Setup guide")
            guide.clicked.connect(lambda: show_setup_guide(self.window(), c.linux_setup_guide()))
            recheck = TransparentPushButton(FIF.SYNC, "Re-check")
            recheck.clicked.connect(self._refresh_permissions)
            self.perm_card.hBoxLayout.addWidget(recheck, 0, Qt.AlignRight)
            self.perm_card.hBoxLayout.addWidget(guide, 0, Qt.AlignRight)
            self.perm_card.hBoxLayout.addSpacing(GRID * 2)
            linux.addSettingCard(self.perm_card)
            self._refresh_permissions()
            v.addWidget(linux)

        v.addStretch()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        c.settings_changed.connect(self._sync)

    def _set_look(self, key: str, value):
        self.c.set_setting(key, value)
        apply_theme(self.c.cfg)

    def _reset_accent(self):
        self._set_look("accent", "")
        self.accent.setColor(accent_for(self.c.cfg))

    def _set_hotkey(self, action: str, combo: str):
        if error := self.c.set_hotkey(action, combo):
            self.c.notify.emit("error", "Hotkey not changed", error)
        else:
            self.c.notify.emit("success", "Hotkey saved", f"{ACTIONS[action]}: {combo or 'off'}")

    def _sync(self):
        for action, rec in self.recorders.items():
            rec.set_combo(self.c.cfg.hotkeys.get(action, ""))
        self.hotkey_card.setContent(self.c.hotkey_status())

    def _refresh_permissions(self):
        p = self.c.linux_permissions()
        ok = p["uinput_writable"] and p["keyboards_readable"]
        self.perm_card.setContent(
            f"uinput: {'writable' if p['uinput_writable'] else 'not writable'} · keyboards readable: "
            f"{p['keyboards_readable']}/{p['input_devices']} · input group: {p['input_group']}"
            + ("" if ok else " · only needed without the portals"))


def show_setup_guide(parent, guide: str):
    box = MessageBox("Linux setup guide", guide, parent)
    box.contentLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)  # so the commands can be copied
    box.cancelButton.hide()
    box.yesButton.setText("Close")
    box.widget.setMinimumWidth(min(GRID * 80, parent.width() - GRID * 8))
    box.exec()

