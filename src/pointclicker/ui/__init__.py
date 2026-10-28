"""Fluent (Windows 11 style) user interface built on PySide6-Fluent-Widgets."""
import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):  # qfluentwidgets prints an advert on import
    import qfluentwidgets  # noqa: F401


def main():
    from .window import main as run
    run()
