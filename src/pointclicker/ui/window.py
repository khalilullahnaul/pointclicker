"""Main Fluent window: navigation, toasts, picking flow, tray, theme following."""
from __future__ import annotations

import sys

import darkdetect
from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from qfluentwidgets import (
    Action,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    NavigationItemPosition,
    SystemTrayMenu,
    TeachingTip,
    TeachingTipTailPosition,
    Theme,
    isDarkTheme,
    setTheme,
)
from qfluentwidgets import FluentIcon as FIF

from ..controller import AppController
from ..overlay import MarkerLayer, PickOverlay
from ..scheduler import State
from .pages import AboutPage, ProfilesPage, RunPage
from .settings import SettingsPage, show_setup_guide
from .style import APP_ICON, GRID, AppIcon, apply_theme
from .targets import TargetDialog, TargetsPage
from .widgets import fmt_seconds

TOASTS = {"success": InfoBar.success, "info": InfoBar.info, "warning": InfoBar.warning, "error": InfoBar.error}
TRAY_ICONS = {"success": QSystemTrayIcon.Information, "info": QSystemTrayIcon.Information,
              "warning": QSystemTrayIcon.Warning, "error": QSystemTrayIcon.Critical}


class MainWindow(FluentWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.c = c = controller
        self._restore_after_pick = False
        self._quitting = False
        self._suggest_pending: list[str] = []
        self._suggest_open = False
        self._first_show = True
        self.markers_supported = QApplication.platformName() != "wayland"  # native Wayland can't place windows
        self.setWindowTitle("PointClicker")
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.setMinimumSize(GRID * 80, GRID * 60)

        self.markers = MarkerLayer()
        self.overlay = PickOverlay()
        self.overlay.picked.connect(self._picked)
        self.overlay.cancelled.connect(self._after_pick)

        self.targets_page = TargetsPage(c)
        self.run_page = RunPage(c)
        self.profiles_page = ProfilesPage(c)
        self.settings_page = SettingsPage(c, self.markers_supported)
        self.about_page = AboutPage(c)
        self.addSubInterface(self.targets_page, AppIcon.CROSSHAIR, "Targets")
        self.addSubInterface(self.run_page, FIF.CALENDAR, "Schedule / Run")
        self.addSubInterface(self.profiles_page, FIF.PEOPLE, "Profiles")
        self.addSubInterface(self.settings_page, FIF.SETTING, "Settings", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.about_page, FIF.INFO, "About", NavigationItemPosition.BOTTOM)
        self.navigationInterface.setExpandWidth(GRID * 28)

        self.targets_page.pick_requested.connect(self.start_pick)
        self.targets_page.edit_requested.connect(self.edit_target)
        c.pick_requested.connect(self.start_pick)
        c.notify.connect(self.toast)
        c.suggest_foreground.connect(self._suggest_foreground)
        c.targets_changed.connect(self._apply_markers)
        c.target_updated.connect(self._apply_markers)  # cheap: markers are reconciled, not rebuilt
        c.settings_changed.connect(self._apply_markers)
        c.state_changed.connect(self._update_tray)

        self._build_tray()
        # Follow OS light/dark changes. A timer, not qfluentwidgets' SystemThemeListener thread: that thread
        # sleeps up to 2 s between checks and aborted the app when it was still running at quit.
        self._theme_poll = QTimer(self, interval=2000, timeout=self._follow_os_theme)
        self._theme_poll.start()
        if c.cfg.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(c.cfg.window_geometry.encode()))
        else:
            self.resize(GRID * 130, GRID * 88)
        self._tick = QTimer(self, interval=250, timeout=self._refresh_live)
        self._tick.start()
        self._apply_markers()

    def _follow_os_theme(self):
        if self.c.cfg.theme == "system" and bool(darkdetect.isDark()) != isDarkTheme():
            setTheme(Theme.AUTO)  # re-resolves from the OS; qfluentwidgets restyles every widget

    # --- live refresh (reads controller state; never touches the engine) ---

    def _refresh_live(self):
        page = self.stackedWidget.currentWidget()
        if hasattr(page, "tick"):
            page.tick()
        self._update_tray()

    def _update_tray(self, *_):
        state = self.c.state
        nxt = self.c.next_click()
        text = state.value.capitalize() + (f" — next: {nxt[0]} in {fmt_seconds(nxt[1])}" if nxt else "")
        self.tray.setToolTip(f"PointClicker — {text}")
        self.tray_toggle.setText({State.STOPPED: "Start", State.RUNNING: "Pause", State.PAUSED: "Resume"}[state])

    # --- toasts and questions ---

    def toast(self, level: str, title: str, message: str):
        if not self.isVisible() or self.isMinimized():
            self.tray.showMessage(title, message, TRAY_ICONS.get(level, QSystemTrayIcon.Information), 5000)
            return
        TOASTS.get(level, InfoBar.info)(title, message, orient=Qt.Horizontal, isClosable=True,
                                       duration=-1 if level == "error" else 4000,
                                       position=InfoBarPosition.TOP_RIGHT, parent=self)

    SUGGEST_GATHER_MS = 400

    def _suggest_foreground(self, target_id: str):
        """Suggestions arriving together (every background target's first click of a run) become one dialog,
        and a dialog never opens while another is showing: stacking one modal dialog per target froze the UI."""
        if target_id not in self._suggest_pending:
            self._suggest_pending.append(target_id)
        if not self._suggest_open:
            QTimer.singleShot(self.SUGGEST_GATHER_MS, self._show_suggestions)

    def _show_suggestions(self):
        if self._suggest_open:
            return
        found = [self.c.find(tid) for tid in self._suggest_pending]
        self._suggest_pending = []
        ts = [t for t in found if t and t.mode == "background"]
        if not ts:
            return
        names = ", ".join(t.name for t in ts[:5]) + (f" and {len(ts) - 5} more" if len(ts) > 5 else "")
        if not self.isVisible():
            self.toast("warning", "Background click had no effect", f"{names}: consider foreground mode.")
            return
        one = len(ts) == 1
        box = MessageBox("Background click had no effect?",
                         f"{names}: the window didn't change after a background click. Games, browsers and many "
                         f"modern apps ignore background clicks.\n\nSwitch {'it' if one else 'them'} to foreground "
                         "mode? Foreground clicks briefly move the real pointer. If the click did work, keep background.",
                         self)
        box.yesButton.setText("Use foreground")
        box.cancelButton.setText("Keep background")
        self._suggest_open = True
        try:
            accepted = box.exec()
        finally:
            self._suggest_open = False
        if accepted:
            for t in ts:
                self.c.use_foreground(t.id)
        if self._suggest_pending:  # more arrived while the dialog was open
            QTimer.singleShot(0, self._show_suggestions)

    # --- picking and editing ---

    def start_pick(self):
        if self.overlay.active:
            return
        self._restore_after_pick = True  # the add dialog needs the window, even if we were in the tray
        self.hide()  # keep our own window out of the way of the point being picked
        QTimer.singleShot(150, self.overlay.start)  # let the compositor unmap us first

    def _after_pick(self):
        if self._restore_after_pick:
            self._restore_after_pick = False
            self.show_from_tray()

    def _picked(self, t):
        self._after_pick()
        dlg = TargetDialog(self.c.new_target(t), self.c, self, "Add target")
        if dlg.exec():
            self.c.add_target(dlg.result_target)

    def edit_target(self, target_id: str):
        if t := self.c.find(target_id):
            dlg = TargetDialog(t, self.c, self, "Edit target")
            if dlg.exec():
                self.c.update_target(dlg.result_target)

    def _apply_markers(self):
        if self.c.cfg.show_markers and self.markers_supported:
            self.markers.show_targets(self.c.targets)
        else:
            self.markers.clear()

    # --- first launch ---

    def showEvent(self, e):
        super().showEvent(e)
        if self._first_show:
            self._first_show = False
            if self.width() >= GRID * 114:  # wide enough for an inline (not overlay) navigation pane
                self.navigationInterface.expand(useAni=False)
            if not self.c.cfg.tip_shown:
                QTimer.singleShot(700, self._show_tip)
            if self.c.caps.get("setup_needed") and not self.c.cfg.setup_guide_shown:
                self.c.set_setting("setup_guide_shown", True)
                QTimer.singleShot(1200, lambda: show_setup_guide(self, self.c.linux_setup_guide()))

    def _show_tip(self):
        page = self.targets_page
        anchor = page.empty_pick_btn if page.empty.isVisible() else page.add_btn
        key = self.c.cfg.hotkeys.get("pick")
        extra = "" if not key else (f" Or press {key} to add the point under the mouse."
                                    if self.c.caps.get("can_read_cursor", True)
                                    else f" {key} opens this picker from anywhere.")
        TeachingTip.create(target=anchor, icon=AppIcon.CROSSHAIR, title="Pick a point",
                           content="Click here, then click anywhere on screen. That point becomes a target that "
                                   f"clicks on its own schedule.{extra}",
                           isClosable=True, duration=-1, tailPosition=TeachingTipTailPosition.TOP, parent=self)
        self.c.set_setting("tip_shown", True)

    # --- tray, minimize, quit ---

    def _build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(str(APP_ICON)), self)
        menu = SystemTrayMenu(parent=self)
        self.tray_toggle = Action(FIF.PLAY, "Start", triggered=self.c.toggle_start_pause)
        menu.addActions([
            Action(FIF.HOME, "Show PointClicker", triggered=self.show_from_tray),
            self.tray_toggle,
            Action(AppIcon.STOP, "Stop", triggered=self.c.stop),
            Action(FIF.POWER_BUTTON, "Emergency stop", triggered=self.c.emergency_stop),
            Action(AppIcon.CROSSHAIR, "Pick a point", triggered=self.start_pick),
        ])
        menu.addSeparator()
        menu.addAction(Action(FIF.CLOSE, "Quit", triggered=self.quit_app))
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_from_tray() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, e):
        if (e.type() == QEvent.WindowStateChange and self.isMinimized() and self.c.cfg.minimize_to_tray
                and QSystemTrayIcon.isSystemTrayAvailable()):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(e)

    def closeEvent(self, e):
        self.quit_app()
        e.accept()

    def quit_app(self):
        if self._quitting:  # QApplication.quit() sends close events, which land here again
            return
        self._quitting = True
        self.c.shutdown(self.saveGeometry().toBase64().data().decode())
        self.markers.clear()
        self.tray.hide()
        QApplication.quit()


def main():
    if "--diagnose" in sys.argv[1:]:
        from ..diagnose import run
        sys.exit(run())
    from .. import targets
    from ..controller import launch_at_login_enabled, setup_logging
    from .style import apply_fonts
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("pointclicker")
    app.setApplicationDisplayName("PointClicker")
    app.setDesktopFileName("pointclicker")
    app.setWindowIcon(QIcon(str(APP_ICON)))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PointClicker.App")
        except Exception:
            pass
    app.setQuitOnLastWindowClosed(False)  # we live in the tray; quitting is explicit
    apply_fonts(app)
    cfg = targets.load()
    cfg.launch_at_login = launch_at_login_enabled()  # the installer or the user may have changed it outside the app
    apply_theme(cfg)
    controller = AppController(cfg)
    win = MainWindow(controller)
    if cfg.start_minimized and QSystemTrayIcon.isSystemTrayAvailable():
        win.hide()
    else:
        win.show()
    controller.start_services()
    sys.exit(app.exec())
