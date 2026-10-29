"""Run in a subprocess with QT_SCALE_FACTOR set: builds the window at 800x550 and checks every page fits."""
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QAbstractButton, QAbstractScrollArea, QApplication

patch("pointclicker.targets.config_path", lambda: Path(tempfile.mkdtemp()) / "config.json").start()
app = QApplication([])
from pointclicker import ui  # noqa: E402,F401
from pointclicker.controller import AppController  # noqa: E402
from pointclicker.platform import FakeBackend, FakeHotkeyBackend, set_backend, set_hotkey_backend  # noqa: E402
from pointclicker.targets import Config, Target  # noqa: E402
from pointclicker.ui.targets import TargetDialog  # noqa: E402
from pointclicker.ui.window import MainWindow  # noqa: E402

set_backend(FakeBackend())
set_hotkey_backend(FakeHotkeyBackend())
cfg = Config(tip_shown=True, setup_guide_shown=True)
cfg.targets.extend(Target(name="A rather long target name that must elide " * 2, x=i, y=i) for i in range(6))
c = AppController(cfg)
win = MainWindow(c)
win.resize(800, 550)
win.show()


def pump(s):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def in_scroll_area(w):
    while (w := w.parentWidget()) is not None:
        if isinstance(w, QAbstractScrollArea):
            return True
    return False


problems = []
frame = QRect(QPoint(0, 0), win.size()).adjusted(-1, -1, 1, 1)
for page in (win.targets_page, win.run_page, win.profiles_page, win.settings_page, win.about_page):
    win.switchTo(page)
    pump(0.4)
    for b in page.findChildren(QAbstractButton):
        if b.isVisible() and b.width():
            r = QRect(b.mapTo(win, QPoint(0, 0)), b.size())
            sideways = r.left() < -1 or r.right() > win.width()
            cut_off = not in_scroll_area(b) and (r.top() < -1 or r.bottom() > win.height())
            if sideways or cut_off:
                problems.append(f"{page.objectName()}: {type(b).__name__} {b.text()!r} {r.getRect()}")
    for area in page.findChildren(QAbstractScrollArea):
        if area.isVisible() and area.horizontalScrollBar().maximum() > 0:
            problems.append(f"{page.objectName()}: horizontal scroll in {type(area).__name__}")
win.switchTo(win.targets_page)
dlg = TargetDialog(cfg.targets[0], c, win, "Edit target")
dlg.show()
pump(0.5)
r = QRect(dlg.widget.mapTo(win, QPoint(0, 0)), dlg.widget.size())
if not frame.contains(r):
    problems.append(f"edit dialog {r.getRect()} doesn't fit {win.size().toTuple()}")
print("scale", app.primaryScreen().devicePixelRatio(), "window", win.size().toTuple())
print("\n".join(problems) if problems else "FITS")
win.quit_app()
sys.exit(1 if problems else 0)
