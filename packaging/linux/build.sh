#!/usr/bin/env bash
# Linux packages from the PyInstaller app folder (run `python build.py` first; `make build-linux` does both):
#   dist/PointClicker-<version>-x86_64.AppImage   and   dist/pointclicker_<version>_amd64.deb
# No Flatpak/Snap on purpose: their sandboxes block the input injection PointClicker exists for.
set -euo pipefail
umask 022  # package files must not be group-writable
cd "$(dirname "$0")/../.."
PY=${PY:-.venv/bin/python}
VERSION=$("$PY" -c 'ns = {}; exec(open("src/pointclicker/__init__.py").read(), ns); print(ns["__version__"])')
APP=dist/pointclicker
LINUX=packaging/linux
TOOLS=.tools
[ -x "$APP/pointclicker" ] || { echo "no $APP: run '$PY build.py' first"; exit 1; }

# pinned build tools, verified by checksum
fetch() {  # url sha256 dest
    if ! echo "$2  $3" | sha256sum -c --quiet 2>/dev/null; then
        mkdir -p "$(dirname "$3")"
        curl -fsSL -o "$3" "$1"
        echo "$2  $3" | sha256sum -c --quiet
        chmod +x "$3"
    fi
}
fetch https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage \
      ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0 "$TOOLS/appimagetool-1.9.1"
fetch https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 \
      2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d "$TOOLS/appimage-runtime-20251108"

# 256 px icon from the .ico
mkdir -p build
QT_QPA_PLATFORM=offscreen "$PY" - <<'PYEOF'
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImageReader
app = QGuiApplication([])
reader, best = QImageReader("src/pointclicker/pointclicker.ico"), None
for i in range(reader.imageCount()):
    reader.jumpToImage(i)
    img = reader.read()
    if best is None or img.width() > best.width():
        best = img
best.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save("build/pointclicker.png")
PYEOF

install_common() {  # root, app dir inside it
    install -Dm644 "$LINUX/pointclicker.desktop" "$1/usr/share/applications/pointclicker.desktop"
    install -Dm644 build/pointclicker.png "$1/usr/share/icons/hicolor/256x256/apps/pointclicker.png"
    install -Dm644 build/pointclicker.png "$1/usr/share/pixmaps/pointclicker.png"
    mkdir -p "$1/$2"
    cp -a "$APP/." "$1/$2/"
    install -Dm755 "$LINUX/setup-uinput.sh" "$1/$2/setup-uinput.sh"
}

# --- AppImage ---
APPDIR=build/AppDir
rm -rf "$APPDIR"
install_common "$APPDIR" usr/lib/pointclicker
cp "$LINUX/pointclicker.desktop" build/pointclicker.png "$APPDIR/"
cp build/pointclicker.png "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/pointclicker/pointclicker" "$@"
RUN
chmod +x "$APPDIR/AppRun"
APPIMAGE="dist/PointClicker-$VERSION-x86_64.AppImage"
rm -f "$APPIMAGE"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOLS/appimagetool-1.9.1" --no-appstream \
    --runtime-file "$TOOLS/appimage-runtime-20251108" "$APPDIR" "$APPIMAGE"

# --- .deb ---
DEBROOT=build/deb
rm -rf "$DEBROOT"
install_common "$DEBROOT" opt/pointclicker
mkdir -p "$DEBROOT/usr/bin" "$DEBROOT/DEBIAN"
ln -s /opt/pointclicker/pointclicker "$DEBROOT/usr/bin/pointclicker"
cat > "$DEBROOT/DEBIAN/control" <<CONTROL
Package: pointclicker
Version: $VERSION
Architecture: amd64
Maintainer: Khalil Ullah Naul <khalilullahnaul@users.noreply.github.com>
Depends: libxcb-cursor0, libxkbcommon-x11-0, libegl1, libgl1, libfontconfig1, libdbus-1-3
Section: utils
Priority: optional
Homepage: https://github.com/khalilullahnaul/pointclicker
Description: Scheduled auto-clicker for fixed points on screen
 PointClicker clicks fixed points on screen on their own schedules, following
 their windows when they move. Works on X11 and Wayland. For the optional
 Wayland uinput/hotkey setup run /opt/pointclicker/setup-uinput.sh.
CONTROL
DEB="dist/pointclicker_${VERSION}_amd64.deb"
dpkg-deb --root-owner-group --build "$DEBROOT" "$DEB" >/dev/null
ls -la "$APPIMAGE" "$DEB"
