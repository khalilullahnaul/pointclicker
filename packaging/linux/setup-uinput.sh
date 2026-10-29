#!/bin/sh
# PointClicker on Wayland, optional one-time setup (needs sudo). Only needed for
#   - global hotkeys when the desktop has no GlobalShortcuts portal (e.g. GNOME before 48), and
#   - clicking when the Remote Desktop portal is missing or was denied.
# It lets the "input" group use /dev/uinput and adds you to that group.
#
#   sh setup-uinput.sh            install (asks first)
#   sh setup-uinput.sh --remove   undo
#
# Security: members of the input group can read every keystroke on this computer and inject input.
# Only do this on a machine you trust.
set -eu
RULE='KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"'
RULE_FILE=/etc/udev/rules.d/60-pointclicker.rules
ME=${SUDO_USER:-$(id -un)}

reload() {
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput || sudo udevadm trigger
}

if [ "${1:-}" = "--remove" ]; then
    sudo rm -f "$RULE_FILE"
    sudo gpasswd -d "$ME" input 2>/dev/null || true
    reload
    echo "Removed. Log out and back in for the group change to take effect."
    exit 0
fi

echo "This will:"
echo "  1. write $RULE_FILE so the 'input' group can use /dev/uinput"
echo "  2. add '$ME' to the 'input' group"
echo "Members of the input group can read every keystroke and inject input on this computer."
printf "Continue? [y/N] "
read -r answer
case "$answer" in y|Y|yes|YES) ;; *) echo "Nothing changed."; exit 1 ;; esac

getent group input >/dev/null || sudo groupadd --system input
echo "$RULE" | sudo tee "$RULE_FILE" >/dev/null
reload
sudo usermod -aG input "$ME"
echo "Done. Log out and back in, then check with:  pointclicker --diagnose"
