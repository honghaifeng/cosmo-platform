#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度剩余缺口 · 浏览器通道补抓

为什么需要：curl 直连百家号会被「百度安全验证」拦（IP 级限流），
慢速 curl 也只能过一小部分。真实浏览器有完整 JS 上下文与 cookie，
能正常渲染正文，是当前把百度这批补齐的唯一稳定通路。

特点：
  · 复用 Playwright chromium（tools/MediaCrawler/.venv）
  · 单页面单进程，每页 3~6 秒间隔，遇验证页跳过并计数
  · 正文用 clean_article 过滤导航噪声，抽不干净宁可不写
  · 幂等：只挑仍未达标记录，可反复跑

用法：
  tools/MediaCrawler/.venv/bin/python crawler/baidu_browser_backfill.py --limit 5 --dry
  tools/MediaCrawler/.venv/bin/python crawler/baidu_browser_backfill.py --platform baidu
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enrich_baidu_records as E

DB = E.DB
MIN_BODY = E.MIN_BODY
UA = E.UA

BODY_SELECTORS = [
    "div.article-content", "div#article", "div.article", "div.mainContent",
    "div.content", "article", "div.post-content", "div.rich_media_content",
]


def pending(con, platform, limit=0, scope="unverified", min_body=None):
    """scope=unverified：原有用途，补未校验条目（补完再走校验闸门）
    scope=verified  ：已校验条目只补正文（摘要→全文），不动归属与 verified 状态"""
    mb = MIN_BODY if min_body is None else min_body
    if scope == "verified":
        q = """SELECT id, url, title, body FROM content
               WHERE platform=? AND verified=1
                 AND url IS NOT NULL AND url!=''
                 AND LENGTH(COALESCE(body,''))<?
               ORDER BY LENGTH(COALESCE(body,'')) ASC"""
        rows = con.execute(q, (platform, mb)).fetchall()
    else:
        q = """SELECT id, url, title, body FROM content
               WHERE platform=? AND verified IS NOT 1
                 AND url IS NOT NULL AND url!=''
                 AND (LENGTH(COALESCE(body,''))<? OR publish_time IS NULL OR publish_time='')
               ORDER BY id"""
        rows = con.execute(q, (platform, mb)).fetchall()
    return rows[:limit] if limit else rows


def grab(page, url):
    """返回 (body, publish_time, status)。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=35000)
    except Exception as e:
        return None, None, "nav-fail:" + type(e).__name__
    try:
        page.wait_for_timeout(2200)
    except Exception:
        pass
    html = page.content() or ""
    if "百度安全验证" in html:
        return None, None, "blocked"
    if len(html) < 1500:
        return None, None, "tiny"

    txt = ""
    for sel in BODY_SELECTORS:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            t = (el.inner_text() or "").strip()
            if len(t) > len(txt):
                txt = t
        except Exception:
            continue
    if len(txt) < 120:
        try:
            txt = page.inner_text("body") or ""
        except Exception:
            txt = ""
    body = E.clean_article(txt)
    if len(body) < MIN_BODY:
        body = None
    pt, src = (None, None)
    if E._from_html:
        try:
            pt, src = E._from_html(html)
        except Exception:
            pt, src = None, None
    if not pt and E._from_url:
        try:
            pt, src = E._from_url(page.url)
        except Exception:
            pass
    return body, pt, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="baidu")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sleep-min", type=float, default=1.5)
    ap.add_argument("--sleep-max", type=float, default=3.5)
    ap.add_argument("--block-pause", type=int, default=420)
    ap.add_argument("--scope", default="unverified", choices=["unverified", "verified"],
                    help="verified=只给已校验条目补正文（摘要→全文），不动归属")
    ap.add_argument("--min-body", type=int, default=600,
                    help="body 低于该长度才补（scope=verified 时生效）")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = pending(con, args.platform, args.limit, args.scope, args.min_body)
    print(f"待补 {len(rows)} 条（浏览器通道·{args.scope}）", flush=True)

    n_body = n_time = n_block = n_fail = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                                  locale="zh-CN")
        page = ctx.new_page()
        try:
            page.goto("https://www.baidu.com/", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

        for i, r in enumerate(rows, 1):
            if E.is_block_domain(r["url"]):
                # 已校验条目不动 verify_note（改它就是改判定结论），只跳过
                if not args.dry and args.scope != "verified":
                    con.execute("UPDATE content SET verify_note='X-非内容页(贴吧/百科/电商)' "
                                "WHERE id=?", (r["id"],))
                    con.commit()
                continue
            body, pt, status = grab(page, r["url"])
            if status == "blocked":
                n_block += 1
                print(f"[风控] 第 {i} 条遇验证页，长停 {args.block_pause//60} 分钟", flush=True)
                time.sleep(args.block_pause)
                continue
            if status != "ok":
                n_fail += 1
                continue
            # 只在新正文明显更完整时才覆盖，避免用半截页面把摘要换掉
            if body and len(body) > max(len(r["body"] or "") * 1.2, 300):
                n_body += 1
                if not args.dry:
                    con.execute("UPDATE content SET body=? WHERE id=?", (body, r["id"]))
            if pt:
                n_time += 1
                if not args.dry:
                    con.execute("UPDATE content SET publish_time=?, pub_time_src=?, url=? "
                                "WHERE id=? AND (publish_time IS NULL OR publish_time='')",
                                (pt, "B-html", page.url, r["id"]))
            if not args.dry:
                con.commit()
            if i % 10 == 0:
                ok = con.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
                print(f"  进度 {i}/{len(rows)} · 正文 {n_body} 时间 {n_time} "
                      f"风控 {n_block} 失败 {n_fail} · 已达标 {ok}", flush=True)
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

        browser.close()

    print(f"\n浏览器通道：补正文 {n_body}，补时间 {n_time}，风控 {n_block}，失败 {n_fail}")
    print("下一步：python3 crawler/verify_and_gate.py  # 重新打标")
    con.close()


if __name__ == "__main__":
    main()
