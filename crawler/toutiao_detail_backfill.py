#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条站内详情回填：补发布时间 + 正文（提高 verified 通过率）。

背景：
  toutiao_full.py 从搜索卡片只能拿到标题+摘要，publish_time 为空、
  正文常低于 verify 闸门的 150 字阈值（H2/H3 双卡）。
  本脚本逐条打开 www.toutiao.com 文章/视频页，从页面 DOM 补：
    · publish_time  优先取页面明文时间（YYYY-MM-DD HH:MM），其次 YYYY-MM-DD
    · body          article 正文 innerText（仅在新值更长时覆盖，避免倒退）

边界：
  · 只处理 platform='toutiao' 且 url 含 toutiao.com 的记录（站外归 web 桶，
    由 backfill_nonbaidu.py 负责）
  · 幂等：只挑缺时间或正文不足的记录，可反复跑、断点续跑
  · 拟人节奏：每页间隔 3~6s，命中验证码立即停（宁少勿封）
  · 每条留痕 crawl_log

用法：
  python3 crawler/toutiao_detail_backfill.py            # 跑完剩余
  python3 crawler/toutiao_detail_backfill.py --limit 20
"""
import argparse
import datetime
import json
import os
import random
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

TIME_FULL = re.compile(r"(20\d{2}-\d{2}-\d{2})[ ](\d{2}:\d{2})")
TIME_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")

PAGE_JS = """() => {
  const art = document.querySelector('article');
  let body = art ? (art.innerText || '').trim() : '';
  if (!body) {
    const m = document.querySelector('.article-content, .detail-content, .video-info-desc');
    body = m ? (m.innerText || '').trim() : '';
  }
  let t = '';
  const meta = document.querySelector('meta[itemprop="datePublished"], meta[name="publish"]');
  if (meta) t = meta.getAttribute('content') || '';
  if (!t) {
    const el = document.querySelector('.article-meta, .article-sub, .video-info-pubdate, time');
    t = el ? (el.innerText || '').trim() : '';
  }
  return {body: body, t: t};
}"""


def norm_time(raw):
    if not raw:
        return ""
    m = TIME_FULL.search(raw)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = TIME_DATE.search(raw)
    return m.group(0) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep-min", type=float, default=3.0)
    ap.add_argument("--sleep-max", type=float, default=6.0)
    args = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT id, url, title, COALESCE(body,'') AS body, publish_time
           FROM content
           WHERE platform='toutiao' AND url LIKE '%toutiao.com%'
             AND (publish_time IS NULL OR publish_time='' OR LENGTH(COALESCE(body,''))<150)
           ORDER BY id""").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"待回填 {len(rows)} 条", flush=True)
    if not rows:
        return

    from playwright.sync_api import sync_playwright

    ok_t = ok_b = nf = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="zh-CN",
                                  viewport={"width": 1440, "height": 900}, user_agent=UA)
        pg = ctx.new_page()
        for i, r in enumerate(rows, 1):
            try:
                pg.goto(r["url"], wait_until="domcontentloaded", timeout=40000)
                pg.wait_for_timeout(3000)
                pg.mouse.wheel(0, 2000)
                pg.wait_for_timeout(1200)
                data = pg.evaluate(PAGE_JS)
            except Exception as e:
                print(f"  ! [{r['id']}] {type(e).__name__}: {str(e)[:80]}", flush=True)
                nf += 1
                continue
            page_text = (pg.inner_text("body") or "")[:400]
            if "验证" in page_text or "访问过于频繁" in page_text or "请输入验证码" in page_text:
                print(f"  × 命中验证码/限流，在第 {i} 条停止（已处理 {i-1}）", flush=True)
                con.execute(
                    "INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note)"
                    " VALUES ('toutiao','ALL','detail回填',datetime('now','localtime'),'blocked',"
                    f"'验证码中断，已处理 {i-1}/{len(rows)}')")
                con.commit()
                break
            new_t = norm_time(data.get("t", ""))
            new_b = (data.get("body") or "").strip()
            sets, vals = [], []
            if new_t and not r["publish_time"]:
                sets.append("publish_time=?"); vals.append(new_t); ok_t += 1
            if len(new_b) > len(r["body"]) and len(new_b) >= 30:
                sets.append("body=?"); vals.append(new_b[:8000]); ok_b += 1
            if sets:
                vals.append(r["id"])
                con.execute(f"UPDATE content SET {', '.join(sets)} WHERE id=?", vals)
                con.commit()
            tag = []
            if new_t: tag.append("时间✓")
            if len(new_b) > len(r["body"]): tag.append(f"正文{len(new_b)}字")
            print(f"  · [{i}/{len(rows)}] id={r['id']} {' '.join(tag) or '无增量'}", flush=True)
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))
        ctx.close()
        browser.close()
    con.execute(
        "INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note)"
        " VALUES ('toutiao','ALL','detail回填',datetime('now','localtime'),'ok',"
        f"'补时间 {ok_t} / 补正文 {ok_b} / 失败 {nf} / 共 {len(rows)}')")
    con.commit()
    con.close()
    print(f"=== 完成：补时间 {ok_t}，补正文 {ok_b}，失败 {nf} ===", flush=True)


if __name__ == "__main__":
    main()
