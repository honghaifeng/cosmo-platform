import os
#!/usr/bin/env python3
"""打开线上页面，抓真实失败请求（URL + 状态码 + content-type）与页面上的报错文案。

用途：线上页面显示「加载失败」时，定位到底是哪一条请求、被谁拒的。
不要用它数图片——数图请用 crawler/verify_media_render.py。
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "https://niling-guancha.app.workbuddy.host"
TARGETS = [
    BASE + "/?sharecode=" + os.environ.get("CB_SHARECODE", ""),
    BASE + "/",
]


def probe(url):
    print("=" * 78)
    print("打开:", url)
    fails, bad = [], []
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1180, "height": 900})
        pg = ctx.new_page()

        def on_resp(r):
            try:
                if r.status >= 400:
                    fails.append((r.status, r.url, (r.headers or {}).get("content-type", "")))
            except Exception:
                pass

        pg.on("response", on_resp)
        pg.on("console", lambda m: bad.append("console:" + m.type + " " + m.text[:160])
              if m.type in ("error",) else None)
        pg.on("requestfailed", lambda r: bad.append("reqfail:" + r.url[:120]))
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("  goto 失败:", e)
            br.close()
            return
        pg.wait_for_timeout(9000)

        print("  最终地址:", pg.url)
        print("  标题:", (pg.title() or "")[:60])
        txt = pg.inner_text("body")[:600].replace("\n", " | ")
        print("  页面文字前 600:", txt)
        print("  失败请求 %d 条:" % len(fails))
        for s, u, ct in fails[:12]:
            print("    %s  %-28s %s" % (s, ct[:28], u.replace(BASE, "")[:120]))
        if bad:
            print("  其他异常:")
            for b in bad[:8]:
                print("    ", b[:160])
        # 当前激活 tab 的内容区文本
        try:
            active = pg.inner_text(".tab.on") if pg.locator(".tab.on").count() else ""
            print("  激活 tab:", active.strip()[:20])
        except Exception:
            pass
        pg.screenshot(path="/tmp/probe_%d.png" % len(fails), full_page=False)
        br.close()


if __name__ == "__main__":
    for u in (TARGETS if len(sys.argv) < 2 else sys.argv[1:]):
        probe(u)
