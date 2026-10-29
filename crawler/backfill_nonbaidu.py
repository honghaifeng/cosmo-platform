#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补抓「非百度域名」的正文与时间（凤凰/搜狐/新浪/网易/腾讯/千龙等）。

为什么单独一个脚本：
  百家号详情页对本机 IP 已上验证码，curl 与浏览器都过不去；
  但同一批搜索结果里，非百度域名的站点仍可直连，可先吃掉这部分。
  幂等：只挑仍未达标记录，可反复跑、断点续跑。

用法：
  python3 crawler/backfill_nonbaidu.py            # 跑完剩余
  python3 crawler/backfill_nonbaidu.py --limit 30
"""
import argparse
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enrich_baidu_records as E

DB = E.DB
MIN_BODY = E.MIN_BODY


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep-min", type=float, default=1.2)
    ap.add_argument("--sleep-max", type=float, default=2.6)
    args = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT id, url, title, body FROM content
           WHERE platform IN ('baidu','toutiao','web') AND verified IS NOT 1
             AND url IS NOT NULL AND url!='' AND url NOT LIKE '%baidu.com%'
             AND (LENGTH(COALESCE(body,''))<? OR publish_time IS NULL OR publish_time='')
           ORDER BY id""", (MIN_BODY,)).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"候选 {len(rows)} 条（非百度域名）", flush=True)

    nb = nt = nf = 0
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
        if not html:
            nf += 1
            continue
        body = E.extract_body(html, r["title"])
        pt = src = None
        if E._from_html:
            pt, src = E._from_html(html)
        if not pt and E._from_url:
            pt, src = E._from_url(real)
        if body:
            con.execute("UPDATE content SET body=? WHERE id=?", (body, r["id"]))
            nb += 1
        if pt:
            con.execute("UPDATE content SET publish_time=?, pub_time_src=?, url=? WHERE id=? "
                        "AND (publish_time IS NULL OR publish_time='')",
                        (pt, "N-" + (src or "url"), real, r["id"]))
            nt += 1
        if body or pt:
            con.commit()
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} 正文 {nb} 时间 {nt} 失败 {nf}", flush=True)
        time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    print(f"完成：补正文 {nb}，补时间 {nt}，失败 {nf}", flush=True)
    con.close()


if __name__ == "__main__":
    main()
