#!/usr/bin/env python3
"""SVG/HTML → PNG 截图（headless Chrome，--no-sandbox）

用法:
  python3 scripts/shot.py in.svg out.png [宽度 高度]
  python3 scripts/shot.py in.html out.png 1600 900
"""
import os
import subprocess
import sys
import tempfile

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def shot(src, out, w=1200, h=900):
    src = os.path.abspath(src)
    out = os.path.abspath(out)
    target = src
    tmp = None
    if src.lower().endswith(".svg"):
        # 用 HTML 包一层，保证背景为白色且尺寸精确
        svg = open(src, encoding="utf-8").read()
        html = (
            "<!doctype html><meta charset='utf-8'>"
            f"<body style='margin:0;background:#fff;display:flex;align-items:center;"
            f"justify-content:center;width:{w}px;height:{h}px'>"
            f"<div style='width:{w}px;height:{h}px'>{svg}</div></body>"
        )
        fd, tmp = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        target = tmp
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=2",
        f"--user-data-dir={tempfile.gettempdir()}/cbchrome-shot",
        f"--screenshot={out}", f"--window-size={w},{h}",
        "file://" + target,
    ]
    subprocess.run(cmd, capture_output=True, timeout=90)
    if tmp:
        os.unlink(tmp)
    print(("ok  " if os.path.exists(out) else "fail ") + out)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        sys.exit(1)
    shot(a[0], a[1], int(a[2]) if len(a) > 2 else 1200, int(a[3]) if len(a) > 3 else 900)
