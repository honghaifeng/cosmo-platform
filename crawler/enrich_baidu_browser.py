#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器通道的正文富化 —— curl 抓不到的站点的兜底。

背景：
  · curl 能抓到的：大部分新闻站、论坛、政府站、静态站
  · curl 抓不到的：知乎专栏、微信公众号、什么值得买、百度贴吧/百科、
    京东/淘宝商品页、m.baidu.com 笔记页 —— 这些站点对非浏览器请求
    返回验证页或空壳（实测 2KB 左右）
  · 这些站点恰恰是「品牌提及密度最高」的站（用户测评、种草笔记），
    丢了可惜 → 用真实 Chromium 打开一次，DOM 里正文和时间都在

做法：
  1. 找出库里「curl 富化后仍缺正文或缺时间」的百度系记录
  2. 浏览器逐条打开，抽取正文 + 时间
  3. 写回 content

用法：
  tools/MediaCrawler/.venv/bin/python crawler/enrich_baidu_browser.py
  tools/MediaCrawler/.venv/bin/python crawler/enrich_baidu_browser.py --limit 60
"""
import argparse
import datetime
import hashlib
import html as ihtml
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
CACHE = os.path.join(ROOT, "data", "raw", "pubtime_cache")
TODAY = datetime.date.today()
MIN_BODY = 220

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backfill_baidu_time_browser import (from_struct, from_text, from_url,  # noqa: E402
                                         parse_dt)
from enrich_baidu_records import extract_body, is_blocked  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="baidu")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--wait", type=float, default=2.5)
    ap.add_argument("--profile-dir", default=os.path.join(ROOT, "data", "browser_profiles", "baidu_bt"))
    ap.add_argument("--need", default="both", choices=["both", "body", "time"],
                    help="补什么：both=正文+时间, body=只补正文, time=只补时间")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    have = {r[1] for r in con.execute("PRAGMA table_info(content)")}
    if "pub_time_src" not in have:
        con.execute("ALTER TABLE content ADD COLUMN pub_time_src TEXT")
        con.commit()

    conds = ["platform=?"]
    if args.need in ("both", "body"):
        conds.append("(body IS NULL OR LENGTH(body)<? OR body LIKE '（SERP 收录级%')")
    if args.need in ("both", "time"):
        conds.append("(publish_time IS NULL OR publish_time='')")
    q = f"SELECT id,url,title,body,publish_time FROM content WHERE {' AND '.join(conds)}"
    rows = con.execute(q, (args.platform, MIN_BODY)).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"待浏览器富化 {len(rows)} 条（platform={args.platform}, need={args.need}）", flush=True)
    if not rows:
        con.close()
        return

    os.makedirs(CACHE, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        args.profile_dir, headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        locale="zh-CN", timezone_id="Asia/Shanghai",
        viewport={"width": 1366, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(28000)

    n_body = n_time = 0
    for i, r in enumerate(rows, 1):
        url = r["url"]
        html = ""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=28000)
            page.wait_for_timeout(1500)
            html = page.content()
        except Exception as e:
            print(f"  ! [{r['id']}] {type(e).__name__}: {str(e)[:60]}", flush=True)
        if html and not is_blocked(html):
            try:
                with open(os.path.join(CACHE,
                                       hashlib.md5(page.url.encode()).hexdigest() + ".html"),
                          "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass
        else:
            html = ""

        new_body = extract_body(html, r["title"]) if html else None
        pt, src = (None, None)
        if html:
            pt, src = from_struct(html)
            if not pt:
                pt, src = from_text(html)
        if not pt:
            pt, src = from_url(page.url)

        if new_body:
            con.execute("UPDATE content SET body=? WHERE id=? AND "
                        "(body IS NULL OR LENGTH(body)<? OR body LIKE '（SERP 收录级%')",
                        (new_body, r["id"], MIN_BODY))
            n_body += 1
        if pt:
            con.execute("UPDATE content SET publish_time=?, pub_time_src=?, url=? WHERE id=? "
                        "AND (publish_time IS NULL OR publish_time='')",
                        (pt, "EB-" + (src or "url"), page.url, r["id"]))
            n_time += 1
        con.commit()
        mark = []
        if new_body:
            mark.append(f"body{len(new_body)}")
        if pt:
            mark.append(f"time={pt}")
        print(f"  {'✓' if mark else '·'} [{r['id']}] {' '.join(mark) or '未取到'} "
              f"{(r['title'] or '')[:34]}", flush=True)
        time.sleep(args.wait)

    print(f"\n浏览器富化完成：正文 {n_body} 条，时间 {n_time} 条 / 共 {len(rows)} 条")
    try:
        ctx.close()
    except Exception:
        pass
    pw.stop()
    con.close()


if __name__ == "__main__":
    main()
