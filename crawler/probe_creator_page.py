#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官号主页抓取 0 条的探针：打开一个主页，dump 页面真实状态。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")
TOKEN_JSON = os.path.join(ROOT, "data", "creator_tokens.json")

nid = sys.argv[1] if len(sys.argv) > 1 else "5c4a73a000000000110201d1"
tokens = json.load(open(TOKEN_JSON, encoding="utf-8"))
url = f"https://www.xiaohongshu.com/user/profile/{nid}?xsec_token={tokens[nid]}&xsec_source=pc_search"

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        locale="zh-CN", viewport={"width": 1280, "height": 900})
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(5000)
    print("URL:", pg.url)
    print("title:", pg.title())
    for sel in ["section.note-item", ".note-item", "section", ".cards", ".user-note",
                "a.cover", "a[href*='/explore/']", "a[href*='/discovery/item/']"]:
        try:
            print(f"{sel:28s}", pg.locator(sel).count())
        except Exception as e:
            print(f"{sel:28s} err {type(e).__name__}")
    body = pg.inner_text("body")
    print("--- body 前 500 字 ---")
    print(body[:500])
    # 登录态检查
    login_hint = ("登录" in body[:200]) or ("扫码" in body[:300])
    print("疑似未登录:", login_hint)
    ctx.close()
