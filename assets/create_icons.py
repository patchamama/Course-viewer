#!/usr/bin/env python3
"""
Generate tray icons (icon.png, icon.ico, icon.icns) for Course Viewer.
Run once: python assets/create_icons.py
Requires: pip install pillow
"""
import os
from PIL import Image, ImageDraw

SIZES = [16, 32, 48, 64, 128, 256]
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(2, size // 8)
    pad = max(1, size // 32)
    # Dark blue rounded background
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=r, fill=(30, 41, 84, 255))
    # Light play triangle
    m = size // 4
    d.polygon(
        [(m, m), (m, size - m), (size - m, size // 2)],
        fill=(124, 140, 248, 255),
    )
    return img


def main():
    base = make_icon(256)
    base.save(os.path.join(OUT_DIR, "icon.png"))
    print("Saved icon.png")

    # .ico: multi-resolution
    imgs = [make_icon(s) for s in [16, 32, 48, 256]]
    imgs[0].save(
        os.path.join(OUT_DIR, "icon.ico"),
        format="ICO",
        sizes=[(s, s) for s in [16, 32, 48, 256]],
        append_images=imgs[1:],
    )
    print("Saved icon.ico")

    # .icns: macOS (requires Pillow 10+)
    try:
        make_icon(512).save(os.path.join(OUT_DIR, "icon.icns"), format="ICNS")
        print("Saved icon.icns")
    except Exception as e:
        print(f"icon.icns skipped ({e}) — use iconutil on macOS if needed")


if __name__ == "__main__":
    main()
