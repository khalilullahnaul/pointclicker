"""Tiny X11 test window that reports every click as a JSON line on stdout.

    python x11_clickcounter.py --display :5 --geometry 400x300+100+80 --title "Counter A" --class CounterA

stdout:  {"ev": "ready", "id": ...}  then  {"ev": "press"|"release", "button": n, "x": .., "y": .., "rx": .., "ry": ..,
         "synthetic": bool}
stdin:   "move X Y" | "raise" | "quit"
"""
import argparse
import json
import os
import re
import select
import sys

from Xlib import X, Xatom, display


def say(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--display", required=True)
    ap.add_argument("--geometry", default="400x300+100+100")
    ap.add_argument("--title", default="PointClicker click counter")
    ap.add_argument("--class", dest="cls", default="ClickCounter")
    a = ap.parse_args()
    w_, h_, x_, y_ = map(int, re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", a.geometry).groups())

    d = display.Display(a.display)
    scr = d.screen()
    win = scr.root.create_window(x_, y_, w_, h_, 0, scr.root_depth, X.InputOutput, X.CopyFromParent,
                                 background_pixel=scr.white_pixel,
                                 event_mask=X.ButtonPressMask | X.ButtonReleaseMask | X.StructureNotifyMask)
    win.set_wm_name(a.title)
    win.change_property(d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"), 8, a.title.encode())
    win.set_wm_class(a.cls.lower(), a.cls)
    win.change_property(d.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
    win.map()
    d.sync()
    while True:
        ev = d.next_event()
        if ev.type == X.MapNotify:
            break
    say(ev="ready", id=win.id)

    while True:
        ready = select.select([d.fileno(), sys.stdin], [], [], 1.0)[0]
        if sys.stdin in ready:
            cmd = sys.stdin.readline().split()
            if not cmd or cmd[0] == "quit":
                return
            if cmd[0] == "move":
                win.configure(x=int(cmd[1]), y=int(cmd[2]))
            elif cmd[0] == "raise":
                win.configure(stack_mode=X.Above)
            d.sync()
            say(ev="ok", cmd=cmd[0])
        while d.pending_events():
            ev = d.next_event()
            if ev.type in (X.ButtonPress, X.ButtonRelease):
                say(ev="press" if ev.type == X.ButtonPress else "release", button=ev.detail, x=ev.event_x,
                    y=ev.event_y, rx=ev.root_x, ry=ev.root_y, synthetic=bool(ev.send_event))


if __name__ == "__main__":
    main()
