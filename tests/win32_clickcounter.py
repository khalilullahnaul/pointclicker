"""Test window for the real Win32 click tests: a Tk window in its own process that reports every mouse press as a
JSON line on stdout. Commands on stdin: "raise", "quit". Window class "TkTopLevel", title from --title."""
import argparse
import json
import queue
import sys
import threading
import tkinter as tk

ap = argparse.ArgumentParser()
ap.add_argument("--title", required=True)
ap.add_argument("--geometry", default="400x300+200+150")
ap.add_argument("--topmost", action="store_true")
a = ap.parse_args()

root = tk.Tk()
root.title(a.title)
root.geometry(a.geometry)
root.attributes("-topmost", a.topmost)
area = tk.Frame(root, bg="white")
area.pack(fill="both", expand=True)


def say(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def press(ev, button, double=False):
    say(ev="press", button=button, double=double, rx=ev.x_root, ry=ev.y_root)


for num, name in ((1, "left"), (2, "middle"), (3, "right")):
    area.bind(f"<Button-{num}>", lambda e, n=name: press(e, n))
    area.bind(f"<Double-Button-{num}>", lambda e, n=name: press(e, n, double=True))

commands: queue.Queue = queue.Queue()
threading.Thread(target=lambda: [commands.put(line.strip()) for line in sys.stdin], daemon=True).start()


def poll():
    while not commands.empty():
        cmd = commands.get()
        if cmd == "quit":
            root.destroy()
            return
        if cmd == "raise":
            root.lift()
            root.attributes("-topmost", True)
            root.update()
        say(ev="ok")
    root.after(20, poll)


root.update()
say(ev="ready", hwnd=int(root.wm_frame(), 16))
poll()
root.mainloop()
