#!/usr/bin/env python3
"""
Course Viewer — desktop entry point.
Wraps proxy.py in a background thread and manages a system tray icon.
Usage: course-viewer [<folder-path>]
"""
import hashlib
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import pystray
from PIL import Image, ImageDraw

import proxy
import updater

APP_VERSION = "1.1.13"


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _static_dir():
    if len(sys.argv) > 1:
        candidate = os.path.normpath(sys.argv[1])
        if os.path.isdir(candidate):
            return candidate
    return _app_dir()


def _dir_id(path):
    """Same hash as proxy.py: first 8 hex chars of MD5(path)."""
    return hashlib.md5(path.encode()).hexdigest()[:8]


def _port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _switch_dir(port, new_path):
    """POST /api/switch-dir to an already-running server."""
    try:
        data = json.dumps({"path": new_path}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/switch-dir",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def _make_icon():
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=12, fill=(30, 41, 84, 255))
    margin = 18
    d.polygon(
        [(margin, margin), (margin, size - margin), (size - margin, size // 2)],
        fill=(124, 140, 248, 255),
    )
    return img


def _load_icon(app_dir):
    bundle_dir = getattr(sys, "_MEIPASS", app_dir)
    icon_path = os.path.join(bundle_dir, "assets", "icon.png")
    if os.path.isfile(icon_path):
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception:
            pass
    return _make_icon()


def _browser_url(port, dir_id):
    """Unique URL per invocation so the browser always opens a fresh tab."""
    return f"http://127.0.0.1:{port}/?dirId={dir_id}&_t={int(time.time())}"


def main():
    app_dir = _app_dir()
    static_dir = _static_dir()
    port = proxy.PORT
    dir_id = _dir_id(static_dir)

    if _port_open(port):
        # Another instance is already running — just switch its directory and
        # open a new browser tab. No second tray or server needed.
        _switch_dir(port, static_dir)
        time.sleep(0.25)  # give switch-dir time to complete before browser loads
        webbrowser.open(_browser_url(port, dir_id))
        return

    # ── Fresh start ──────────────────────────────────────────────────────────
    bundle_dir = getattr(sys, "_MEIPASS", app_dir)
    proxy.APP_DIR = bundle_dir
    proxy.STATIC_DIR = static_dir

    server_thread = threading.Thread(target=proxy.run, daemon=True)
    server_thread.start()
    time.sleep(0.4)

    webbrowser.open(_browser_url(port, dir_id))

    icon_image = _load_icon(app_dir)

    def on_open(icon, item):
        webbrowser.open(_browser_url(port, _dir_id(proxy.STATIC_DIR)))

    def on_check_updates(icon, item):
        tag, release_url, has_update = updater.check(APP_VERSION)
        if has_update:
            webbrowser.open(release_url)

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Open in Browser", on_open, default=True),
        pystray.MenuItem("Check for Updates", on_check_updates),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    tray = pystray.Icon(
        "Course Viewer",
        icon_image,
        f"Course Viewer — {os.path.basename(static_dir)}",
        menu,
    )

    def _bg_update_check():
        time.sleep(6)
        tag, release_url, has_update = updater.check(APP_VERSION)
        if has_update:
            tray.title = f"Course Viewer — Update available: v{tag}"

    threading.Thread(target=_bg_update_check, daemon=True).start()

    tray.run()


if __name__ == "__main__":
    main()
