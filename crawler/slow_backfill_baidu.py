#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度剩余缺口「慢速补抓」—— 专门对付百度安全验证。

与 enrich_baidu_records.py 的区别：
  · 单线程 + 6~10 秒随机间隔，不触发风控
  · 连续命中「百度安全验证」自动长停 10~15 分钟再继续（有节奏，不硬冲）
  · 幂等：只挑仍未达标的记录，可反复运行、断点续跑
  · 每轮结束打印剩余缺口，跑不完下次接着来

用法：
  python3 crawler/slow_backfill_baidu.py                # 补到完
  python3 crawler/slow_backfill_baidu.py --limit 50     # 只补 50 条
  python3 crawler/slow_backfill_baidu.py --cooldown 900 # 起跑前先等 15 分钟
"""
import argparse
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import enrich_baidu_records as E

ROOT = E.ROOT
DB = E.DB
MIN_BODY = E.MIN_BODY
BLOCK_MARK = "百度安全验证"


def pending(con, limit=0):
    q = """SELECT id, url, title, body FROM content
           WHERE platform='baidu' AND verified IS NOT 1
             AND url IS NOT NULL AND url!=''
             AND (LENGTH(COALESCE(body,''))<?  OR publish_time IS NULL OR publish_time='')
           ORDER BY id"""
    rows = con.execute(q, (MIN_BODY,)).fetchall()
    return rows[:limit] if limit else rows


def gap(con):
    short = con.execute(
        "SELECT COUNT(*) FROM content WHERE platform='baidu' AND verified IS NOT 1 "
        "AND LENGTH(COALESCE(body,''))<?", (MIN_BODY,)).fetchone()[0]
    notime = con.execute(
        "SELECT COUNT(*) FROM content WHERE platform='baidu' AND verified IS NOT 1 "
        "AND (publish_time IS NULL OR publish_time='')").fetchone()[0]
    ok = con.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
    return short, notime, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cooldown", type=int, default=0, help="起跑前冷却秒数")
    ap.add_argument("--sleep-min", type=float, default=6.0)
    ap.add_argument("--sleep-max", type=float, default=10.0)
    args = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    if args.cooldown:
        print(f"[冷却] 先等 {args.cooldown}s 让百度侧限流解除…", flush=True)
        time.sleep(args.cooldown)

    rows = pending(con, args.limit)
    print(f"待补 {len(rows)} 条", flush=True)
    streak_block = 0
    n_body = n_time = n_fail = 0

    for i, r in enumerate(rows, 1):
        if E.is_block_domain(r["url"]):
            con.execute("UPDATE content SET verify_note='X-非内容页(贴吧/百科/电商)' WHERE id=?",
                        (r["id"],))
            con.commit()
            continue
        try:
            html, real = E.fetch(r["url"])
        except Exception:
            html, real = "", r["url"]

        if not html or BLOCK_MARK in html:
            streak_block += 1
            n_fail += 1
            if streak_block >= 3:
                pause = random.randint(600, 900)
                print(f"[风控] 连续 {streak_block} 次验证页，长停 {pause//60} 分钟", flush=True)
                time.sleep(pause)
                streak_block = 0
            continue
        streak_block = 0

        body = E.extract_body(html, r["title"])
        pt, src = (None, None)
        if E._from_html:
            pt, src = E._from_html(html)
        if not pt and E._from_url:
            pt, src = E._from_url(real)

        if body:
            con.execute("UPDATE content SET body=? WHERE id=?", (body, r["id"]))
            n_body += 1
        if pt:
            con.execute("UPDATE content SET publish_time=?, pub_time_src=?, url=? WHERE id=? "
                        "AND (publish_time IS NULL OR publish_time='')",
                        (pt, "S-" + (src or "url"), real, r["id"]))
            n_time += 1
        con.commit()

        if i % 10 == 0:
            s, t, ok = gap(con)
            print(f"  进度 {i}/{len(rows)} · 本轮补正文 {n_body} 时间 {n_time} "
                  f"· 剩余 短正文 {s} 缺时间 {t} · 已达标 {ok}", flush=True)
        time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    s, t, ok = gap(con)
    print(f"\n本轮：补正文 {n_body}，补时间 {n_time}，失败 {n_fail}")
    print(f"剩余：正文短 {s} 条，缺时间 {t} 条 · 当前达标 {ok} 条")
    con.close()


if __name__ == "__main__":
    main()
