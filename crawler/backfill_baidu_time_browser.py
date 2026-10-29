#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度系剩余缺时间条目的浏览器补抓（第二通道）。

背景：
  知乎 / 公众号 / 值得买 / 百度百科 / 百度贴吧 / 京东 / 淘宝 等站点对 curl 直连
  有反爬（返回 2KB 左右的验证页或空壳），用 curl 提取时间必然失败。
  但这些页面在真实 Chromium 里能正常打开，DOM 里带发布时间。

做法：
  1. 用 Playwright 逐条打开「库里缺 publish_time」的百度系记录
  2. 从 DOM 里按优先级取时间：
       ① meta / JSON-LD 结构化字段（最可信）
       ② 页面可见的发布时间元素（按选择器 + 文本模式）
       ③ URL 内嵌日期
  4. 抓到的原始 HTML 存 data/raw/pubtime_cache/ 便于回溯
  5. 只回填可确证的时间，拿不到就跳过（绝不猜）

用法：
  tools/MediaCrawler/.venv/bin/python crawler/backfill_baidu_time_browser.py
  tools/MediaCrawler/.venv/bin/python crawler/backfill_baidu_time_browser.py --only 50178,50183
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
CACHE = os.path.join(ROOT, "data", "raw", "pubtime_cache")
TODAY = datetime.date.today()

# ── 结构化时间字段 ──────────────────────────────────────────────
STRUCT_PATTERNS = [
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'"pubDate"\s*:\s*"([^"]+)"',
    r'"publishTime"\s*:\s*"?(\d{9,13})',
    r'"createTime"\s*:\s*"?(\d{9,13})',
    r'"publish_time"\s*:\s*"?(\d{9,13})',
    r'"publishDate"\s*:\s*"([^"]+)"',
    r'"lastmod"\s*:\s*"([^"]+)"',
    r'property="article:published_time"\s+content="([^"]+)"',
    r'name="pubdate"\s+content="([^"]+)"',
    r'itemprop="datePublished"\s+content="([^"]+)"',
    r'itemprop="datePublished"[^>]*>\s*([^<]{6,30})',
    r'class="date-text"[^>]*>\s*发布[^0-9]{0,20}(20\d{2}[-/]\d{1,2}[-/]\d{1,2}[^<]{0,8})',
]

# ── 可见文本里的发布时间（带上下文关键词，命中才采信）──────────
TEXT_CTX = re.compile(
    r'(?:发布(?:于|时间)?|发表于|发表于|来源[:：]|发稿(?:时间)?|首发(?:于|时间)?|'
    r'更新(?:于|时间)?|创建(?:于|时间)?|编辑于)\s*[:：]?\s*'
    r'(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}(?:\s+\d{1,2}:\d{2})?)')

# 纯日期文本（用于已知是「时间位」的容器）
TIME_SELECTORS = [
    "time", "[class*='time']", "[class*='date']", "[class*='publish']",
    "[class*='PubDate']", "[class*='post-date']", "[itemprop='datePublished']",
    "[class*='info'] span", "[class*='meta'] span", "[class*='source']",
]


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})[日]?(?:\s+(\d{1,2}):(\d{2}))?", s)
    if not m:
        return None
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if d > TODAY or d < datetime.date(2000, 1, 1):
        return None
    if m.group(4):
        hh, mm = int(m.group(4)), int(m.group(5))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{d.isoformat()} {hh:02d}:{mm:02d}"
    return d.isoformat()


def from_struct(html):
    for pat in STRUCT_PATTERNS:
        for m in re.finditer(pat, html, re.I):
            g = m.group(1)
            if re.fullmatch(r"\d{9,13}", g):
                ts = int(g)
                if ts > 1e12:
                    ts //= 1000
                try:
                    dt = datetime.datetime.fromtimestamp(ts)
                    if dt.date() <= TODAY:
                        return dt.strftime("%Y-%m-%d %H:%M"), "B2-struct-ts"
                except (ValueError, OSError):
                    continue
                continue
            v = parse_dt(g)
            if v:
                return v, "B2-struct"
    return None, None


def from_text(html):
    # 1) 带发布/来源关键词的文本
    txt = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<style[^>]*>.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ")
    for m in TEXT_CTX.finditer(txt):
        v = parse_dt(m.group(1))
        if v:
            return v, "B3-text-ctx"
    # 2) <time> 标签
    for m in re.finditer(r"<time[^>]*>([^<]{4,30})</time>", html, re.I):
        v = parse_dt(m.group(1))
        if v:
            return v, "B3-time-tag"
    return None, None


URL_DATE = re.compile(r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")


def from_url(url):
    m = URL_DATE.search(url or "")
    if m:
        v = parse_dt(m.group(0))
        if v:
            return v, "B1-url"
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理指定 id")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--wait", type=float, default=2.5, help="每条之间的等待秒")
    ap.add_argument("--profile-dir", default=os.path.join(ROOT, "data", "browser_profiles", "baidu"))
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    have = {r[1] for r in con.execute("PRAGMA table_info(content)")}
    if "pub_time_src" not in have:
        con.execute("ALTER TABLE content ADD COLUMN pub_time_src TEXT")
        con.commit()

    q = """SELECT id, url, brand, platform, title FROM content
           WHERE platform IN ('baidu','toutiao','web')
             AND (publish_time IS NULL OR publish_time='')"""
    rows = con.execute(q).fetchall()
    if args.only:
        keep = {int(x) for x in args.only.split(",") if x.strip()}
        rows = [r for r in rows if r["id"] in keep]
    if args.limit:
        rows = rows[:args.limit]
    print(f"待处理 {len(rows)} 条", flush=True)

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
    page.set_default_timeout(25000)

    stats = {}
    n_ok = 0
    for i, r in enumerate(rows, 1):
        url = r["url"]
        # 百度跳转链先解析成真实 URL
        if "baidu.com/link" in url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                url = page.url
                if "baidu.com/link" in url:
                    url = r["url"]
            except Exception:
                pass
        html = ""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(1200)
            html = page.content()
        except Exception as e:
            print(f"  ! [{r['id']}] {type(e).__name__}: {str(e)[:70]}", flush=True)

        if html:
            try:
                with open(os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest() + ".html"),
                          "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass

        v, src = from_url(url)
        if not v and html:
            v, src = from_struct(html)
        if not v and html:
            v, src = from_text(html)
        if v:
            stats[src] = stats.get(src, 0) + 1
            con.execute("UPDATE content SET publish_time=?, pub_time_src=?, url=? WHERE id=?",
                        (v, src, url, r["id"]))
            n_ok += 1
            print(f"  ✓ [{r['id']}] {v} ({src}) {r['title'][:34]}", flush=True)
        else:
            print(f"  · [{r['id']}] 未取到时间 {r['title'][:34]}", flush=True)
        con.commit()
        time.sleep(args.wait)

    print(f"\n回填 {n_ok} / {len(rows)}，来源分布 {stats}")
    con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) "
                "VALUES ('baidu','ALL','pubtime_browser',?,?,?)",
                (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok",
                 f"浏览器回填 {n_ok}/{len(rows)} 来源 {stats}"))
    con.commit()
    try:
        ctx.close()
    except Exception:
        pass
    pw.stop()
    con.close()


if __name__ == "__main__":
    main()
