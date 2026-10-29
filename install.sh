#!/bin/sh
# Install PointClicker on Linux: the latest AppImage from GitHub Releases, checked against SHA256SUMS.txt,
# installed for your user only (no sudo) with an app-menu entry and the `pointclicker` command.
#
#   curl -fsSL https://raw.githubusercontent.com/khalilullahnaul/pointclicker/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/khalilullahnaul/pointclicker/main/install.sh | sh -s -- --uninstall
#
# Your settings (~/.config/pointclicker) are never touched.
set -eu

REPO=khalilullahnaul/pointclicker
BIN_DIR=${XDG_BIN_HOME:-$HOME/.local/bin}
DATA_DIR=${XDG_DATA_HOME:-$HOME/.local/share}
APP_DIR=$DATA_DIR/pointclicker
APPIMAGE=$APP_DIR/PointClicker.AppImage
DESKTOP=$DATA_DIR/applications/pointclicker.desktop
ICON=$DATA_DIR/icons/hicolor/256x256/apps/pointclicker.png

say() { printf '%s\n' "$*"; }
die() { printf 'pointclicker install: %s\n' "$*" >&2; exit 1; }

refresh_menus() {
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA_DIR/applications" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$APPIMAGE" "$DESKTOP" "$ICON"
    [ -L "$BIN_DIR/pointclicker" ] && rm -f "$BIN_DIR/pointclicker"
    rmdir "$APP_DIR" 2>/dev/null || true
    refresh_menus
    say "PointClicker removed. Your settings are still in ~/.config/pointclicker (delete it for a clean removal)."
    exit 0
fi

[ "$(uname -s)" = Linux ] || die "this installer is for Linux; on Windows use install.ps1"
[ "$(uname -m)" = x86_64 ] || die "only x86_64 builds are published (this machine is $(uname -m))"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is needed (coreutils)"
if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fsSL -H "User-Agent: pointclicker-install" -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -q --header="User-Agent: pointclicker-install" -O "$2" "$1"; }
else
    die "curl or wget is needed"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fetch "https://api.github.com/repos/$REPO/releases/latest" "$TMP/release.json" ||
    die "couldn't reach GitHub (offline, or no release published yet)"
url_of() {  # the download URL of the first asset whose name matches the pattern
    grep -o '"browser_download_url": *"[^"]*"' "$TMP/release.json" | sed 's/.*"\(https[^"]*\)"/\1/' | grep "$1" | head -n 1
}
APP_URL=$(url_of '/PointClicker-[^/]*-x86_64\.AppImage$') || true
SUMS_URL=$(url_of '/SHA256SUMS\.txt$') || true
[ -n "$APP_URL" ] && [ -n "$SUMS_URL" ] || die "the latest release has no AppImage or SHA256SUMS.txt"
NAME=${APP_URL##*/}

say "Downloading $NAME..."
fetch "$APP_URL" "$TMP/$NAME"
fetch "$SUMS_URL" "$TMP/SHA256SUMS.txt"
EXPECTED=$(awk -v n="$NAME" '$2 == n || $2 == "*" n {print $1}' "$TMP/SHA256SUMS.txt")
[ -n "$EXPECTED" ] || die "$NAME is not listed in SHA256SUMS.txt"
ACTUAL=$(sha256sum "$TMP/$NAME" | cut -d' ' -f1)
[ "$EXPECTED" = "$ACTUAL" ] || die "checksum mismatch for $NAME: the download is corrupt or was tampered with"
say "Checksum OK."

mkdir -p "$APP_DIR" "$BIN_DIR" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
install -m 755 "$TMP/$NAME" "$APPIMAGE"
ln -sf "$APPIMAGE" "$BIN_DIR/pointclicker"
# the icon ships inside the AppImage; extracting a single file needs no FUSE
(cd "$TMP" && "$APPIMAGE" --appimage-extract pointclicker.png >/dev/null 2>&1 && cp squashfs-root/pointclicker.png "$ICON") ||
    say "(couldn't extract the icon; the menu entry will use a generic one)"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=PointClicker
GenericName=Auto Clicker
Comment=Click fixed points on screen on their own schedules
Exec=$APPIMAGE
Icon=pointclicker
Terminal=false
Categories=Utility;
Keywords=click;clicker;autoclicker;automation;
StartupWMClass=pointclicker
EOF
refresh_menus

say "Installed $NAME: find PointClicker in your app menu, or run 'pointclicker'."
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "Note: $BIN_DIR is not on your PATH; add it to run 'pointclicker' from a terminal." ;; esac
command -v fusermount >/dev/null 2>&1 || command -v fusermount3 >/dev/null 2>&1 ||
    say "Note: AppImages need FUSE. If it doesn't start, install it (Debian/Ubuntu: sudo apt install fuse3)."
