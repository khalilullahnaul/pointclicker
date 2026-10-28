"""Minimal xdg-desktop-portal client over D-Bus (jeepney, blocking I/O).

Portal methods that need user interaction return a Request object path and answer later with a
Request.Response(code, results) signal; request() hides that dance. Values in a{sv} dicts are
jeepney variants: (signature, value) tuples.
"""
from __future__ import annotations

import itertools
import os
import threading

from jeepney import DBusAddress, MatchRule, Properties, message_bus, new_method_call
from jeepney.io.blocking import Proxy, open_dbus_connection
from jeepney.wrappers import unwrap_msg

PORTAL = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop")
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
SCREEN_CAST = "org.freedesktop.portal.ScreenCast"
GLOBAL_SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
_tokens = itertools.count(1)


class PortalError(Exception):
    pass


class Portal:
    """One session-bus connection. Not thread-safe: callers serialize access with self.lock."""

    def __init__(self):
        self.conn = open_dbus_connection(bus="SESSION")
        self.lock = threading.RLock()
        self._bus = Proxy(message_bus, self.conn)
        self._sender = self.conn.unique_name.lstrip(":").replace(".", "_")

    def close(self):
        self.conn.close()

    def version(self, interface: str) -> int | None:
        """Interface version, or None if this desktop's portal doesn't implement it."""
        try:
            reply = self.conn.send_and_get_reply(Properties(PORTAL.with_interface(interface)).get("version"), timeout=5)
            return int(unwrap_msg(reply)[0][1])
        except Exception:
            return None

    def token(self) -> str:
        return f"pointclicker{os.getpid()}_{next(_tokens)}"

    def call(self, interface: str, method: str, signature: str, *args, timeout: float = 5):
        reply = self.conn.send_and_get_reply(
            new_method_call(PORTAL.with_interface(interface), method, signature, args), timeout=timeout)
        return unwrap_msg(reply)

    def request(self, interface: str, method: str, signature: str, *args, timeout: float = 120) -> dict:
        """Call a Request-returning method and wait for its Response. The last arg must be the options dict."""
        token = self.token()
        args[-1]["handle_token"] = ("s", token)
        path = f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=path)
        self._bus.AddMatch(rule)  # subscribe before calling, so a fast Response can't be missed
        try:
            with self.conn.filter(rule) as responses:
                self.call(interface, method, signature, *args)
                code, results = self.conn.recv_until_filtered(responses, timeout=timeout).body
        except TimeoutError:
            raise PortalError(f"{method}: no answer within {timeout:.0f} s (permission dialog left open?)") from None
        finally:
            self._bus.RemoveMatch(rule)
        if code != 0:
            raise PortalError(f"{method}: {'denied by the user' if code == 1 else 'failed'} (code {code})")
        return results

    def signals(self, interface: str, member: str):
        """Context manager queue of matching signals (use with recv_until_filtered)."""
        rule = MatchRule(type="signal", interface=interface, member=member)
        self._bus.AddMatch(rule)
        return self.conn.filter(rule)
