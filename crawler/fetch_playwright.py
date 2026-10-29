#!/usr/bin/env python3
"""Playwright 批量补抓：处理反爬/JS 渲染页面（百度跳转、头条站外 PR）
运行：tools/MediaCrawler/.venv/bin/python crawler/fetch_playwright.py
"""
import sqlite3, os, re, json, sys, datetime
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
RAW = os.path.join(BASE, "data", "raw", "articles")
PENDING_PLATFORMS = ("baidu", "toutiao", "web")

def need_fetch(body):
    b = (body or "").strip()
    return b.startswith("（SERP 收录级") or len(b) < 220

def clean_text(t, title):
    t = re.sub(r"[ \t\r\f\v]+", " ", t or "")
    t = re.sub(r"\n{2,}", "\n", t).strip()
    key = (title or "")[:14]
    if key and key in t:
        t = t[t.find(key):]
    for tail in ("相关推荐", "热门推荐", "免责声明", "版权声明", "责任编辑"):
        j = t.rfind(tail)
        if j > len(t) * 0.6:
            t = t[:j]
    return t[:3000].strip()

def main(limit=None):
    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    rows = [r for r in con.execute("SELECT id, platform, title, url, body FROM content WHERE platform IN (?,?,?)",
                                   PENDING_PLATFORMS) if need_fetch(r[4])]
    if limit:
        rows = rows[:limit]
    print(f"Playwright 待抓 {len(rows)} 条", flush=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok = fail = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="zh-CN",
                                  user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                                  viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for i, (cid, platform, title, url, body) in enumerate(rows, 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1200)
                txt = page.inner_text("body")
                txt = clean_text(txt, title)
                if len(txt) > 220:
                    con.execute("UPDATE content SET body=?, url=? WHERE id=?",
                                (txt, page.url or url, cid))
                    with open(os.path.join(RAW, f"{cid}.txt"), "w") as fh:
                        fh.write(f"url: {page.url}\nfetched: {now}\ntitle: {title}\n\n{txt}")
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
            if i % 20 == 0:
                con.commit()
                print(f"  进度 {i}/{len(rows)} 成功 {ok} 失败 {fail}", flush=True)
        browser.close()
    con.commit()
    print(f"完成：成功 {ok}，失败 {fail}")
    print(*con.execute("""SELECT platform,
        SUM(CASE WHEN LENGTH(COALESCE(body,''))>220 AND body NOT LIKE '（SERP%' THEN 1 ELSE 0 END),
        COUNT(*) FROM content GROUP BY platform"""), sep="\n")
    con.close()

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
