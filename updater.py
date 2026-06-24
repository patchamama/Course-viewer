import urllib.request
import urllib.error
import json
import re

RELEASES_API = "https://api.github.com/repos/patchamama/Course-viewer/releases/latest"


def _ver_tuple(v):
    parts = re.findall(r'\d+', str(v))
    return tuple(int(x) for x in parts[:3]) if parts else (0,)


def check(current_version):
    """Return (tag, release_url, has_update). Never raises."""
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": "Course-Viewer-Updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        tag = data.get("tag_name", "").lstrip("v")
        url = data.get("html_url", "")
        has_update = bool(tag) and _ver_tuple(tag) > _ver_tuple(current_version)
        return tag, url, has_update
    except Exception:
        return "", "", False
