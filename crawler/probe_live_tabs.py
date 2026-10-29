import os
#!/usr/bin/env python3
"""逐个点击线上页面每个 tab，记录失败请求与可见报错。

用于定位「某个 tab 打不开」这类问题：直接给 出 URL + 状态码，
而不是靠猜。
"""
import sys
from playwright.sync_api import sync_playwright

URL = os.environ.get("CB_PROBE_URL", "http://127.0.0.1:8098/") + "?sharecode=" + os.environ.get("CB_SHARECODE", "")
TABS = ["内容库", "数据完整度", "原始数据", "分析报告", "竞品分析", "品牌知识库", "档案库"]


def main():
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1280, "height": 900}).new_page()
        fails = []
        pg.on("response", lambda r: fails.append((r.status, r.url))
              if r.status >= 400 else None)
        pg.goto(URL, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(4000)
        for i, t in enumerate(TABS):
            n0 = len(fails)
            try:
                pg.click("text=%s" % t, timeout=8000)
            except Exception as e:
                print("%-8s 点不到: %s" % (t, str(e)[:60]))
                continue
            pg.wait_for_timeout(4500)
            body = pg.inner_text("body")
            err = "加载失败" in body
            new = fails[n0:]
            print("%-8s 报错=%-5s 失败请求=%d" % (t, err, len(new)))
            for s, u in new[:6]:
                print("     %s %s" % (s, u.replace("https://niling-guancha.app.workbuddy.host", "")))
            if err:
                k = body.find("加载失败")
                print("     文案:", body[k:k + 120].replace("\n", " "))
                pg.screenshot(path="/tmp/tab_err_%d.png" % i)
        br.close()


if __name__ == "__main__":
    main()
