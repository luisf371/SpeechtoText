"""Native StatusNotifierItem (SNI) system tray backend over D-Bus.

KDE Plasma (and most modern Linux desktops on Wayland) host the tray through
the StatusNotifierItem specification on D-Bus, *not* the legacy XEMBED tray that
``pystray`` falls back to when libappindicator/PyGObject are missing. Driving
XEMBED on Plasma is what produces a square icon, flicker, laggy updates and a
"stuck" colour. This module talks SNI directly using the pure-Python ``jeepney``
library so the host renders our PNG natively (proper alpha → real circle) and
updates the icon in place via ``NewIcon`` (no flicker, no lag).

It implements just enough of ``org.kde.StatusNotifierItem`` and
``com.canonical.dbusmenu`` for an icon, tooltip and a small context menu.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from loguru import logger

_SNI_PATH = "/StatusNotifierItem"
_MENU_PATH = "/MenuBar"
_SNI_IFACES = {
    "org.kde.StatusNotifierItem",
    "org.freedesktop.StatusNotifierItem",
}
_MENU_IFACE = "com.canonical.dbusmenu"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"
_INTROSPECT_IFACE = "org.freedesktop.DBus.Introspectable"
_PEER_IFACE = "org.freedesktop.DBus.Peer"

# How often the serve loop wakes to flush pending icon/title changes emitted
# from other threads. Small enough that updates feel instant.
_POLL_INTERVAL = 0.05


def sni_host_available() -> bool:
    """Return True when a StatusNotifierWatcher with a host is on the session bus."""
    try:
        from jeepney import DBusAddress, Properties
        from jeepney.io.blocking import open_dbus_connection
    except Exception:
        return False

    conn = None
    try:
        conn = open_dbus_connection(bus="SESSION")
        watcher = DBusAddress(
            "/StatusNotifierWatcher",
            bus_name="org.kde.StatusNotifierWatcher",
            interface="org.kde.StatusNotifierWatcher",
        )
        reply = conn.send_and_get_reply(
            Properties(watcher).get("IsStatusNotifierHostRegistered"),
            timeout=1.0,
        )
        # reply.body[0] is a variant ('b', value)
        return bool(reply.body[0][1])
    except Exception as error:
        logger.debug(f"No StatusNotifierWatcher available: {error}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _argb_bytes(image: Any) -> tuple[int, int, bytes]:
    """Convert a PIL RGBA image to (width, height, ARGB32 big-endian bytes)."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    raw = rgba.tobytes("raw", "RGBA")  # R, G, B, A per pixel
    out = bytearray(len(raw))
    out[0::4] = raw[3::4]  # A
    out[1::4] = raw[0::4]  # R
    out[2::4] = raw[1::4]  # G
    out[3::4] = raw[2::4]  # B
    return width, height, bytes(out)


def _machine_id() -> str:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path) as handle:
                return handle.read().strip()
        except Exception:
            continue
    return ""


class SniTrayIcon:
    """A StatusNotifierItem served on the session bus via jeepney."""

    def __init__(
        self,
        *,
        app_id: str,
        title: str,
        image: Any,
        tooltip: str = "",
        on_activate: Callable[[], None] | None = None,
        menu_items: list[tuple[str, Callable[[], None]]] | None = None,
    ):
        self._app_id = app_id
        self._title = title
        self._tooltip = tooltip or title
        self._on_activate = on_activate
        # Menu entries are addressed by stable dbusmenu ids starting at 1.
        self._menu_items = list(menu_items or [])

        self._pixmap = _argb_bytes(image)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pending = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn = None
        self._item_addr = None
        self._menu_addr = None
        self._started = threading.Event()
        self._ok = False

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self, timeout: float = 2.0) -> bool:
        """Start the serve thread and register with the watcher. Returns success."""
        self._stop_event.clear()
        self._started.clear()
        self._thread = threading.Thread(
            target=self._serve, name="PushToTalkSniTray", daemon=True
        )
        self._thread.start()
        self._started.wait(timeout)
        return self._ok

    def stop(self) -> None:
        self._stop_event.set()
        self._pending.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None

    def update(self, *, image: Any, title: str, tooltip: str | None = None) -> None:
        """Replace the icon/title; the serve loop emits the change to the host."""
        with self._lock:
            self._pixmap = _argb_bytes(image)
            self._title = title
            if tooltip is not None:
                self._tooltip = tooltip
        self._pending.set()

    # ── serve loop ──────────────────────────────────────────────────────────
    def _serve(self) -> None:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        try:
            self._conn = open_dbus_connection(bus="SESSION")
            self._item_addr = DBusAddress(
                _SNI_PATH,
                interface="org.kde.StatusNotifierItem",
            )
            self._menu_addr = DBusAddress(_MENU_PATH, interface=_MENU_IFACE)

            watcher = DBusAddress(
                "/StatusNotifierWatcher",
                bus_name="org.kde.StatusNotifierWatcher",
                interface="org.kde.StatusNotifierWatcher",
            )
            register = new_method_call(
                watcher,
                "RegisterStatusNotifierItem",
                "s",
                (self._conn.unique_name,),
            )
            self._conn.send(register)
            self._ok = True
        except Exception as error:
            logger.info(f"SNI tray registration failed: {error}")
            self._ok = False
            self._started.set()
            return

        self._started.set()
        logger.info("SNI tray registered with StatusNotifierWatcher")

        while not self._stop_event.is_set():
            try:
                msg = self._conn.receive(timeout=_POLL_INTERVAL)
            except TimeoutError:
                self._flush_pending()
                continue
            except Exception as error:
                logger.debug(f"SNI receive error: {error}")
                break
            try:
                self._dispatch(msg)
            except Exception as error:
                logger.debug(f"SNI dispatch error: {error}")
            self._flush_pending()

        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass

    def _flush_pending(self) -> None:
        if not self._pending.is_set():
            return
        self._pending.clear()
        from jeepney import new_signal

        try:
            self._conn.send(new_signal(self._item_addr, "NewTitle"))
            self._conn.send(new_signal(self._item_addr, "NewIcon"))
            self._conn.send(new_signal(self._item_addr, "NewToolTip"))
        except Exception as error:
            logger.debug(f"SNI signal emit failed: {error}")

    # ── dispatch ────────────────────────────────────────────────────────────
    def _dispatch(self, msg: Any) -> None:
        from jeepney import HeaderFields, MessageFlag, MessageType, new_error

        hdr = msg.header
        if hdr.message_type != MessageType.method_call:
            return

        fields = hdr.fields
        interface = fields.get(HeaderFields.interface)
        member = fields.get(HeaderFields.member)
        body = msg.body
        no_reply = bool(hdr.flags & MessageFlag.no_reply_expected)

        reply = self._handle_call(interface, member, body, msg)

        if no_reply:
            return
        if reply is None:
            reply = new_error(
                msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (f"{interface}.{member}",)
            )
        try:
            self._conn.send(reply)
        except Exception as error:
            logger.debug(f"SNI reply send failed: {error}")

    def _handle_call(
        self, interface: str | None, member: str | None, body: Any, msg: Any
    ) -> Any:
        from jeepney import new_method_return

        if interface == _PROPS_IFACE:
            return self._handle_properties(member, body, msg)
        if interface == _INTROSPECT_IFACE and member == "Introspect":
            return new_method_return(msg, "s", (_INTROSPECT_XML,))
        if interface == _PEER_IFACE:
            if member == "Ping":
                return new_method_return(msg)
            if member == "GetMachineId":
                return new_method_return(msg, "s", (_machine_id(),))
            return None
        if interface in _SNI_IFACES:
            return self._handle_sni_method(member, body, msg)
        if interface == _MENU_IFACE:
            return self._handle_menu_method(member, body, msg)
        return None

    # ── org.freedesktop.DBus.Properties ────────────────────────────────────
    def _handle_properties(self, member: str | None, body: Any, msg: Any) -> Any:
        from jeepney import new_method_return

        if member == "Get":
            iface, name = body[0], body[1]
            value = self._property(iface, name)
            if value is None:
                return None
            return new_method_return(msg, "v", (value,))
        if member == "GetAll":
            iface = body[0]
            return new_method_return(msg, "a{sv}", (self._all_properties(iface),))
        if member == "Set":
            return new_method_return(msg)
        return None

    def _sni_properties(self) -> dict[str, Any]:
        with self._lock:
            width, height, data = self._pixmap
            title = self._title
            tooltip = self._tooltip
        pixmap = [(width, height, data)]
        return {
            "Category": ("s", "ApplicationStatus"),
            "Id": ("s", self._app_id),
            "Title": ("s", title),
            "Status": ("s", "Active"),
            "WindowId": ("i", 0),
            "IconName": ("s", ""),
            "IconPixmap": ("a(iiay)", pixmap),
            "OverlayIconName": ("s", ""),
            "OverlayIconPixmap": ("a(iiay)", []),
            "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []),
            "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", ("", [], title, tooltip)),
            "ItemIsMenu": ("b", False),
            "Menu": ("o", _MENU_PATH),
        }

    def _menu_properties(self) -> dict[str, Any]:
        return {
            "Version": ("u", 3),
            "Status": ("s", "normal"),
            "TextDirection": ("s", "ltr"),
            "IconThemePath": ("as", []),
        }

    def _property(self, iface: str, name: str) -> Any:
        if iface in _SNI_IFACES or iface == "":
            value = self._sni_properties().get(name)
            if value is not None:
                return value
        if iface == _MENU_IFACE:
            return self._menu_properties().get(name)
        # Some hosts query without specifying the interface.
        return self._sni_properties().get(name)

    def _all_properties(self, iface: str) -> dict[str, Any]:
        if iface == _MENU_IFACE:
            return self._menu_properties()
        return self._sni_properties()

    # ── org.kde.StatusNotifierItem methods ──────────────────────────────────
    def _handle_sni_method(self, member: str | None, body: Any, msg: Any) -> Any:
        from jeepney import new_method_return

        if member in {"Activate", "SecondaryActivate"}:
            self._invoke(self._on_activate)
            return new_method_return(msg)
        if member in {"ContextMenu", "Scroll", "ProvideXdgActivationToken"}:
            return new_method_return(msg)
        return None

    # ── com.canonical.dbusmenu methods ──────────────────────────────────────
    def _handle_menu_method(self, member: str | None, body: Any, msg: Any) -> Any:
        from jeepney import new_method_return

        if member == "GetLayout":
            return new_method_return(msg, "u(ia{sv}av)", (1, self._menu_layout(body)))
        if member == "GetGroupProperties":
            return new_method_return(msg, "a(ia{sv})", (self._group_properties(body),))
        if member == "GetProperty":
            item_id, name = body[0], body[1]
            props = self._menu_item_props(item_id)
            return new_method_return(msg, "v", (props.get(name, ("s", "")),))
        if member == "Event":
            self._menu_event(body)
            return new_method_return(msg)
        if member == "EventGroup":
            return new_method_return(msg)
        if member == "AboutToShow":
            return new_method_return(msg, "b", (False,))
        if member == "AboutToShowGroup":
            return new_method_return(msg, "aiai", ([], []))
        return None

    def _menu_node(self, item_id: int, props: dict[str, Any]) -> tuple:
        return (item_id, props, [])

    def _menu_layout(self, body: Any) -> tuple:
        children = []
        for index, (label, _callback) in enumerate(self._menu_items, start=1):
            node = self._menu_node(index, self._menu_item_props(index))
            children.append(("(ia{sv}av)", node))
        root_props = {"children-display": ("s", "submenu")}
        return (0, root_props, children)

    def _menu_item_props(self, item_id: int) -> dict[str, Any]:
        if item_id == 0:
            return {"children-display": ("s", "submenu")}
        index = item_id - 1
        if 0 <= index < len(self._menu_items):
            label = self._menu_items[index][0]
            return {
                "label": ("s", label),
                "enabled": ("b", True),
                "visible": ("b", True),
            }
        return {}

    def _group_properties(self, body: Any) -> list:
        ids = body[0] if body else []
        if not ids:
            ids = [0] + list(range(1, len(self._menu_items) + 1))
        return [(item_id, self._menu_item_props(item_id)) for item_id in ids]

    def _menu_event(self, body: Any) -> None:
        try:
            item_id, event_id = body[0], body[1]
        except Exception:
            return
        if event_id != "clicked":
            return
        index = item_id - 1
        if 0 <= index < len(self._menu_items):
            self._invoke(self._menu_items[index][1])

    @staticmethod
    def _invoke(callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception as error:
            logger.debug(f"SNI menu callback failed: {error}")


_INTROSPECT_XML = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
  </interface>
</node>"""
