#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证官号笔记的正确采集路径：主页 → 点击卡片 → 抽取正文/时间/图片。

反深链教训：主页卡片 href 的 xsec_token 只在主页上下文有效，
直接 goto 笔记 URL 得到的是导航空壳页。必须像真人一样点击进入。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")
TOKEN_JSON = os.path.join(ROOT, "data", "creator_tokens.json")

nid = sys.argv[1] if len(sys.argv) > 1 else "5c4a73a000000000110201d1"  # MitoQ 官号
tokens = json.load(open(TOKEN_JSON, encoding="utf-8"))
purl = f"https://www.xiaohongshu.com/user/profile/{nid}?xsec_token={tokens[nid]}&xsec_source=pc_search"

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        locale="zh-CN", viewport={"width": 1280, "height": 900})
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto(purl, wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(4000)
    cards = pg.locator("a.cover:visible")
    print("主页卡片数:", cards.count())
    # 点击卡片：可能新标签页，也可能当前页跳转，两种都处理
    before_url = pg.url
    pg2 = None
    try:
        with ctx.expect_page(timeout=15000) as newpg_info:
            cards.first.click()
        pg2 = newpg_info.value
        print("打开方式: 新标签页")
    except Exception:
        pg2 = pg
        pg2.wait_for_timeout(4000)
        print("打开方式: 当前页跳转" if pg.url != before_url else "打开方式: URL 未变化")
    pg2.wait_for_load_state("domcontentloaded")
    pg2.wait_for_timeout(4000)
    print("新页 URL:", pg2.url[:90])
    state = pg2.evaluate("""() => {
      try {
        const st = window.__INITIAL_STATE__;
        if (!st || !st.note || !st.note.noteDetailMap) return {err: 'no state'};
        const m = st.note.noteDetailMap;
        const k = Object.keys(m).find(x => m[x] && m[x].note);
        if (!k) return {err: 'no note'};
        const n = m[k].note;
        return {title: n.title, descLen: (n.desc||'').length,
                time: n.time, type: n.type,
                imgs: (n.imageList||[]).length,
                video: !!(n.video && n.video.media)};
      } catch(e) { return {err: String(e)}; }
    }""")
    print("state:", json.dumps(state, ensure_ascii=False))
    body_head = pg2.inner_text("body")[:150].replace("\n", " ")
    print("页面首屏:", body_head)
    pg2.close()
    ctx.close()
