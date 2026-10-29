#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度全量 SERP 采集（v2）：多关键词 × 多页 + 抽取发布时间。

相比 crawler/baidu_serp.py 的改动：
  1. 每个品牌跑「全部关键词」，不再只跑 keywords[0]
  2. 支持翻页（pn=0,10,20…，百度约 10 页封顶）
  3. 抽取每条结果的发布时间（2026年10月13日 / 10-13 / 3天前 / 昨天…）写入 publish_time
  4. URL 全局去重（同一条结果被多个关键词命中只入一次）
  5. 结果按品牌存 JSON，便于核对

用法：
  python3 crawler/baidu_serp_full.py                 # 六品牌全量
  python3 crawler/baidu_serp_full.py --pages 5       # 每个词只翻 5 页（更快）
  python3 crawler/baidu_serp_full.py --brand MitoQ   # 只跑一个品牌
环境变量：
  BAIDU_COOKIE  可选，带上同源 cookie 更稳（实测不带也能拿到 SERP）
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

DATE_FULL = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
DATE_MD = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日(?!\d)")
REL_DAY = re.compile(r"(\d+)\s*天前")
REL_HOUR = re.compile(r"(\d+)\s*小时前")


def fetch(url, cookie=""):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def norm_date(text, today):
    """把百度 SERP 里的各种日期写法归一成 YYYY-MM-DD。"""
    if not text:
        return None
    m = DATE_FULL.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = DATE_MD.search(text)
        if m:
            y, mo, d = today.year, int(m.group(1)), int(m.group(2))
        else:
            m = REL_DAY.search(text)
            if m:
                return (today - datetime.timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
            m = REL_HOUR.search(text)
            if m:
                return (today - datetime.timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
            if "昨天" in text:
                return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            if "前天" in text:
                return (today - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
            return None
    try:
        return datetime.date(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_serp(html, today):
    """按结果块解析：标题 / 链接 / 摘要 / 发布时间。"""
    items = []
    # 百度结果块以 <div id="N" ...> 或 class="result/c-container" 起始
    blocks = re.split(r'<div[^>]+(?:id="[rs_]*\d+"|class="result[^"]*"|class="c-container")[^>]*>',
                      html)
    for blk in blocks[1:]:
        m = re.search(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title or not url.startswith("http"):
            continue
        # 跳转链里带真实落地 URL 时一并取出来（人工核对、跨关键词去重都靠它）
        land = re.search(r'data-landurl="([^"]+)"', blk) or re.search(
            r'class="[^"]*c-showurl[^"]*"[^>]*>(.*?)<', blk, re.S)
        real_url = ""
        if land:
            real_url = land.group(1).strip()
            real_url = re.sub(r"<[^>]+>", "", real_url).strip()
            if not real_url.startswith("http"):
                real_url = ""
        # 摘要：取 h3 之后最长的一段纯文本
        snippet = ""
        cands = re.findall(r'class="[^"]*(?:content-right|c-span-last|abstract)[^"]*"[^>]*>(.*?)<',
                           blk, re.S)
        if cands:
            snippet = max((re.sub(r"<[^>]+>", "", c) for c in cands), key=len).strip()
        # 时间：块内任意日期写法
        pub = None
        for pat in (DATE_FULL, DATE_MD, REL_DAY, REL_HOUR):
            mm = pat.search(blk)
            if mm:
                pub = norm_date(mm.group(0), today)
                if pub:
                    break
        if pub is None and ("昨天" in blk or "前天" in blk):
            pub = norm_date("昨天", today)
        items.append({"title": title, "url": url, "real_url": real_url,
                      "snippet": snippet[:200], "publish_time": pub})
    return items


def brand_keywords(b):
    """品牌名 + 别名 + 产品词，去重保序。"""
    kws = [b["name"]] + list(b.get("aliases", [])) + list(b.get("keywords", []))
    for g in (b.get("keyword_groups") or {}).values():
        kws += list(g)
    seen, out = set(), []
    for k in kws:
        k = (k or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=10, help="每个关键词翻多少页（百度约 10 页封顶）")
    ap.add_argument("--brand", default="", help="只跑指定品牌")
    ap.add_argument("--sleep", type=float, default=8.0, help="每次请求间隔秒")
    ap.add_argument("--platform", default="baidu", help="baidu | toutiao")
    ap.add_argument("--browser", action="store_true",
                    help="用真实浏览器取 SERP（绕开百度安全验证，推荐）")
    ap.add_argument("--profile", default="", help="浏览器 profile 目录，留空用临时上下文")
    ap.add_argument("--state", default="", help="playwright storage_state 路径（含登录态）")
    ap.add_argument("--login", action="store_true",
                    help="打开浏览器手动登录百度并保存登录态到 --state，之后复用")
    args = ap.parse_args()

    cookie = os.environ.get("BAIDU_COOKIE", "")
    # 没有显式给 cookie 时，尝试从 playwright 登录态里拼一个
    _state = args.state or os.path.join(ROOT, "data", "browser_profiles", "baidu_state.json")
    if not cookie and os.path.exists(_state):
        try:
            st = json.load(open(_state, encoding="utf-8"))
            ck = [c for c in st.get("cookies", []) if "baidu.com" in c.get("domain", "")]
            cookie = "; ".join(f'{c["name"]}={c["value"]}' for c in ck)
            print(f"已从登录态加载 {len(ck)} 个百度 Cookie", flush=True)
        except Exception as e:
            print(f"  登录态读取失败：{e}", flush=True)
    brands = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))["brands"]
    if args.brand:
        brands = [b for b in brands if b["name"] == args.brand]
    con = sqlite3.connect(os.path.join(ROOT, "db", "cosmobounty.db"))
    today = datetime.date.today()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    raw_dir = os.path.join(ROOT, "data", "raw", "baidu_full")
    os.makedirs(raw_dir, exist_ok=True)

    state_path = args.state or os.path.join(ROOT, "data", "browser_profiles", "baidu_state.json")
    if args.login:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=False)
            c = b.new_context(locale="zh-CN")
            pg = c.new_page()
            pg.goto("https://www.baidu.com/", wait_until="domcontentloaded", timeout=30000)
            print("请在弹出的浏览器里完成百度登录（扫码或账号），登录后回到这里等待保存…", flush=True)
            # 等到页面出现登录态标志
            for _ in range(180):
                if "login" not in pg.url.lower() and pg.context.cookies():
                    cookies = {x["name"] for x in pg.context.cookies()}
                    if {"BAIDUID"} & cookies:
                        os.makedirs(os.path.dirname(state_path), exist_ok=True)
                        c.storage_state(path=state_path)
                        print(f"登录态已保存 → {state_path}", flush=True)
                        break
                pg.wait_for_timeout(1000)
            else:
                print("未检测到登录态，未保存。可手动登录后重试。", flush=True)
            b.close()
        return

    # 浏览器模式：真实 Chromium 指纹，安全验证命中率远低于 urllib 直连
    pw = None
    browser = ctx = None
    page = None
    if args.browser:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        launch_args = ["--disable-blink-features=AutomationControlled"]
        if args.profile:
            kw = {"locale": "zh-CN", "viewport": {"width": 1280, "height": 900}}
            if os.path.exists(state_path):
                kw["storage_state"] = state_path
            ctx = pw.chromium.launch_persistent_context(args.profile, headless=False, args=launch_args, **kw)
        else:
            browser = pw.chromium.launch(headless=False, args=launch_args)
            ctx = browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900},
                                      user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = ctx.new_page() if not args.profile else ctx.pages[0] if ctx.pages else ctx.new_page()

    def get_html(url):
        if not args.browser:
            return fetch(url, cookie)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        return page.content()

    grand_new = 0
    for b in brands:
        kws = brand_keywords(b)
        seen_url = set()
        all_items, blocked = [], 0
        for kw in kws:
            for pno in range(args.pages):
                if args.platform == "baidu":
                    url = (f"https://www.baidu.com/s?wd={urllib.parse.quote(kw)}"
                           f"&rn=20&pn={pno * 10}")
                else:
                    url = (f"https://so.toutiao.com/search?keyword={urllib.parse.quote(kw)}"
                           f"&pd=synthesis&page_num={pno}")
                try:
                    html = get_html(url)
                except Exception as e:
                    print(f"  ! [{kw} p{pno+1}] {type(e).__name__}: {e}", flush=True)
                    time.sleep(args.sleep)
                    continue
                if "百度安全验证" in html or "wappass" in html:
                    blocked += 1
                    print(f"  × [{kw} p{pno+1}] 命中安全验证，停止翻页", flush=True)
                    break
                items = parse_serp(html, today)
                if not items:
                    break
                new = 0
                for it in items:
                    key = it.get("real_url") or it["url"]
                    if key in seen_url:
                        continue
                    seen_url.add(key)
                    it["keyword"] = kw
                    it["page"] = pno + 1
                    all_items.append(it)
                    new += 1
                print(f"  · [{kw} p{pno+1}] {len(items)} 条，新增 {new}", flush=True)
                if new == 0:
                    break
                time.sleep(args.sleep)

        # 落库
        cur_new = 0
        for it in all_items:
            h = hashlib.md5(it["url"].encode()).hexdigest()
            cur = con.execute(
                """INSERT OR IGNORE INTO content
                   (brand, platform, content_type, title, body, publish_time,
                    collect_time, url, raw_path, text_hash, tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (b["name"], args.platform, "article", it["title"], it.get("snippet"),
                 it.get("publish_time"), now, it.get("real_url") or it["url"],
                 os.path.join(raw_dir, f"{b['name']}.json"), h,
                 (it.get("keyword") or "") + ("|" + it["url"] if it.get("real_url") else "")))
            if cur.rowcount:
                cur_new += 1
        # 已有行但缺时间的，回填
        back = 0
        for it in all_items:
            if it.get("publish_time"):
                h = hashlib.md5(it["url"].encode()).hexdigest()
                back += con.execute(
                    "UPDATE content SET publish_time=? WHERE text_hash=? AND (publish_time IS NULL OR publish_time='')",
                    (it["publish_time"], h)).rowcount
        grand_new += cur_new
        json.dump(all_items, open(os.path.join(raw_dir, f"{b['name']}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        withtime = sum(1 for x in all_items if x.get("publish_time"))
        con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) VALUES (?,?,?,?,?,?)",
                    (args.platform, b["name"], f"{len(kws)}词x{args.pages}页", now,
                     "ok" if all_items else "empty",
                     f"{len(all_items)} 条 / 新增 {cur_new} / 回填时间 {back} / 有时间 {withtime}"
                     + (f" / 安全验证 {blocked} 次" if blocked else "")))
        print(f"[{args.platform}] {b['name']}: 命中 {len(all_items)}，新增入库 {cur_new}，"
              f"回填时间 {back}，其中带发布时间 {withtime}\n")
        con.commit()

    if pw:
        try:
            ctx.close()
            if browser:
                browser.close()
        except Exception:
            pass
        pw.stop()
    print(f"=== 全部完成：新增入库 {grand_new} 条 ===", flush=True)
    con.close()


if __name__ == "__main__":
    main()