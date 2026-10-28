"""Linux permission checks and the one-time setup guide for /dev/uinput and /dev/input."""
from __future__ import annotations

import glob
import grp
import os
import pwd

UDEV_RULE_FILE = "/etc/udev/rules.d/60-pointclicker.rules"
UDEV_RULE = 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"'


def permissions() -> dict:
    events = glob.glob("/dev/input/event*")
    user = pwd.getpwuid(os.getuid()).pw_name
    try:
        group = grp.getgrnam("input")
        if group.gr_gid in os.getgroups():
            in_group = "yes"
        elif user in group.gr_mem:
            in_group = "added, but not active until you log out and back in"
        else:
            in_group = "no"
    except KeyError:
        in_group = "no 'input' group on this system"
    return {
        "uinput_exists": os.path.exists("/dev/uinput"),
        "uinput_writable": os.access("/dev/uinput", os.W_OK),
        "keyboards_readable": sum(os.access(p, os.R_OK) for p in events),
        "input_devices": len(events),
        "input_group": in_group,
        "udev_rule_installed": os.path.exists(UDEV_RULE_FILE),
    }


GUIDE = f"""PointClicker on Wayland

Clicking normally goes through the desktop's Remote Desktop permission dialog, which needs no setup.
Two things need extra access to input devices:
  • global hotkeys, when the desktop has no GlobalShortcuts portal (e.g. GNOME before 48), and
  • clicking, if the Remote Desktop portal is missing or was denied.

One-time setup (needs sudo):

1. Let the input group use /dev/uinput:
   echo '{UDEV_RULE}' | sudo tee {UDEV_RULE_FILE}
   sudo udevadm control --reload-rules && sudo udevadm trigger

2. Add yourself to the input group:
   sudo usermod -aG input $USER

3. Log out and back in (group changes only apply to new sessions).

Check the result with:  pointclicker --diagnose

Security note: members of the input group can read every keystroke on this machine and inject
input. Only do this on a computer you trust, and undo it with
   sudo gpasswd -d $USER input && sudo rm {UDEV_RULE_FILE}
"""
