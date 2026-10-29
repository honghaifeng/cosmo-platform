#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条站内全量（Playwright 渲染 + DOM 结构化抽取）

为什么不用 so.toutiao.com 直连：
  2026-10-23 起该站结果改为客户端渲染，HTML 里没有结果链（jump 链接归零），
  curl/urllib 拿到的只是壳页。改由真实 Chromium 渲染后从卡片 DOM 抽：
  div.result-content 内的 data-log-extra 带 result_type / group_id，
  group_id 即 www.toutiao.com/article/<gid>/ 的 id，可直接拼回原链。

用法：
  python3 crawler/toutiao_full.py --brand MitoQ --pages 2   # 只跑一个品牌
  python3 crawler/toutiao_full.py                           # 六品牌全量
  python3 crawler/toutiao_full.py --dry                     # 只打印不写库
环境变量：
  TT_SLEEP  每个关键词后的等待秒（默认 6，随机 +0~3）
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW_DIR = os.path.join(ROOT, "data", "raw", "toutiao")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

# 卡片抽取：标题取首行，末行是来源域名，中间是摘要
export = None
CARD_JS = """() => {
  const out = [];
  for (const c of document.querySelectorAll('div.result-content')) {
    const holder = c.querySelector('[data-log-extra]');
    let meta = {};
    if (holder) { try { meta = JSON.parse(holder.getAttribute('data-log-extra') || '{}'); } catch(e) {} }
    const txt = (c.innerText || '').trim();
    const lines = txt.split('\\n').map(s => s.trim()).filter(Boolean);
    out.push({meta: {result_type: meta.result_type || '', group_id: meta.group_id || meta.search_result_id || '', rank: meta.rank},
              title: lines[0] || '', host: lines[lines.length-1] || '', text: txt});
  }
  return out;
}"""


def canon_brand_map():
    """关键词/别名 → 规范品牌名，与 mediacrawler_ingest 同口径。"""
    mapping = {}
    for b in json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))["brands"]:
        for w in [b["name"]] + list(b.get("aliases", [])) + list(b.get("keywords", [])):
            mapping[str(w).strip().lower()] = b["name"]
    return mapping


def brand_keywords(b):
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


def brand_mentions(text, brand):
    """只留真正提到该品牌/别名/产品的条目，与 clean_brand_mentions 的口径一致。"""
    cfg = {b["name"]: b for b in json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))["brands"]}
    b = cfg.get(brand, {})
    words = [brand] + list(b.get("aliases", [])) + list(b.get("products", []))
    low = text.lower()
    return any(w and w.lower() in low for w in words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="")
    ap.add_argument("--pages", type=int, default=2, help="每个词翻几页（page_num 递进）")
    ap.add_argument("--sleep", type=float, default=float(os.getenv("TT_SLEEP", "6")))
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    cfg = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))
    brands = [b for b in cfg["brands"] if not args.brand or b["name"] == args.brand]
    os.makedirs(RAW_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")

    grand = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 900}, user_agent=UA)
        pg = ctx.new_page()
        for b in brands:
            kws = brand_keywords(b)
            items, seen, blocked = [], set(), 0
            for kw in kws:
                for pno in range(args.pages):
                    url = ("https://so.toutiao.com/search?keyword=" + urllib.parse.quote(kw)
                           + f"&dvpf=pc&pd=information&only_toutiao=1&page_num={pno}")
                    try:
                        pg.goto(url, wait_until="domcontentloaded", timeout=40000)
                        pg.wait_for_timeout(3500)
                        pg.mouse.wheel(0, 2600)
                        pg.wait_for_timeout(1500)
                        cards = pg.evaluate(CARD_JS)
                    except Exception as e:
                        print(f"  ! [{kw} p{pno+1}] {type(e).__name__}: {e}", flush=True)
                        continue
                    body = pg.inner_text("body")
                    if "验证" in body[:400] or "访问过于频繁" in body:
                        blocked += 1
                        print(f"  × [{kw} p{pno+1}] 命中验证码/限流，停止翻页", flush=True)
                        break
                    new = 0
                    for c in cards:
                        gid = (c.get("meta") or {}).get("group_id") or ""
                        title = (c.get("title") or "").strip()
                        if not gid or len(title) < 6:
                            continue
                        rt = c["meta"].get("result_type") or ""
                        if "video" in rt:
                            u = f"https://www.toutiao.com/video/{gid}/"
                        else:
                            u = f"https://www.toutiao.com/article/{gid}/"
                        if u in seen:
                            continue
                        seen.add(u)
                        # 摘要：去掉标题与来源行
                        rest = [l for l in (c.get("text") or "").split("\n")
                                if l.strip() and l.strip() != title and l.strip() != c.get("host")]
                        abstract = " ".join(rest).strip()[:600]
                        if not brand_mentions(title + " " + abstract, b["name"]):
                            continue
                        items.append({"title": title[:120], "url": u, "abstract": abstract,
                                      "keyword": kw, "result_type": rt, "group_id": gid})
                        new += 1
                    print(f"  · [{kw} p{pno+1}] 卡片 {len(cards)}，新增入库候选 {new}", flush=True)
                    if new == 0 and pno > 0:
                        break
                    time.sleep(args.sleep + random.random() * 2)
                time.sleep(1)
            json.dump(items, open(os.path.join(RAW_DIR, f"{b['name']}.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            cur_new = 0
            for it in items:
                h = hashlib.md5(it["url"].encode()).hexdigest()
                if args.dry:
                    continue
                cur = con.execute(
                    """INSERT OR IGNORE INTO content
                       (brand, platform, content_type, title, body, publish_time, collect_time,
                        url, raw_path, text_hash, tags)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (b["name"], "toutiao", "video" if "video" in it["result_type"] else "article",
                     it["title"], it["abstract"], None, now, it["url"],
                     os.path.join(RAW_DIR, f"{b['name']}.json"), h,
                     "kw:" + it["keyword"] + "|rt:" + it["result_type"]))
                cur_new += cur.rowcount
            con.execute(
                "INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) VALUES (?,?,?,?,?,?)",
                ("toutiao", b["name"], f"{len(kws)}词x{args.pages}页", now,
                 "ok" if items else "empty",
                 f"{len(items)} 条候选 / 新增 {cur_new}" + (f" / 验证码 {blocked} 次" if blocked else "")))
            con.commit()
            grand += cur_new
            print(f"[toutiao] {b['name']}: 候选 {len(items)}，新增入库 {cur_new}"
                  + (f"，验证码 {blocked} 次" if blocked else ""), flush=True)
        ctx.close()
        browser.close()
    con.close()
    print(f"=== 完成：新增入头条 {grand} 条 ===", flush=True)


if __name__ == "__main__":
    main()
