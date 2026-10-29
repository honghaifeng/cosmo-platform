#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量抓取收尾：把 MediaCrawler 的 jsonl 全部入库 + 时间完整性核对 + 导出带时间的表格。

用法：
  /Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3 crawler/finalize_full.py
"""
import csv
import datetime
import glob
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
PY = "/Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3"


def ingest_all():
    """把所有 jsonl（含历史几天）都过一次入库；已存在的用 UPDATE 补时间。"""
    total_new = 0
    for plat, sub in (("xhs", "xhs"), ("douyin", "douyin")):
        for f in sorted(glob.glob(os.path.join(ROOT, f"tools/MediaCrawler/data/{sub}/jsonl/search_contents_*.jsonl"))):
            notes = f
            comments = f.replace("search_contents_", "search_comments_")
            r = os.system(f'"{PY}" "{os.path.join(ROOT, "crawler/mediacrawler_ingest.py")}" '
                          f'{plat} "{notes}" "{comments}"')
            if r != 0:
                print(f"  ! 入库失败 {plat} {os.path.basename(f)}", flush=True)
            else:
                total_new += 1
    return total_new


def backfill_time():
    """按 text_hash 精确回填 publish_time（抖音 create_time / 小红书 time 都在 jsonl 里）。"""
    import datetime as dt

    def ms_to_str(ms):
        if not ms:
            return None
        try:
            ms = int(ms)
            return dt.datetime.fromtimestamp(ms if ms < 10**12 else ms / 1000).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return None

    con = sqlite3.connect(DB)
    upd = 0
    for sub, pk in (("xhs", "note_id"), ("douyin", "aweme_id")):
        for f in glob.glob(os.path.join(ROOT, f"tools/MediaCrawler/data/{sub}/jsonl/search_contents_*.jsonl")):
            for line in open(f, encoding="utf-8"):
                n = json.loads(line)
                nid = n.get(pk)
                if not nid:
                    continue
                pub = ms_to_str(n.get("time")) or ms_to_str(n.get("create_time"))
                if pub:
                    upd += con.execute(
                        "UPDATE content SET publish_time=? WHERE text_hash=? AND (publish_time IS NULL OR publish_time='')",
                        (pub, f"{sub}:{nid}")).rowcount
    con.commit()
    con.close()
    return upd


def report():
    con = sqlite3.connect(DB)
    print("\n=== 各平台时间完整性 ===")
    for p, n, miss in con.execute(
            "SELECT platform, COUNT(*), SUM(CASE WHEN publish_time IS NULL OR publish_time='' THEN 1 ELSE 0 END) "
            "FROM content WHERE verified=1 GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {p:12s} 共 {n:4d} 条，缺时间 {miss:4d}，填充率 {100*(n-miss)/max(n,1):5.1f}%")
    tot = con.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
    miss = con.execute("SELECT COUNT(*) FROM content WHERE verified=1 AND (publish_time IS NULL OR publish_time='')").fetchone()[0]
    print(f"  合计 {tot} 条，缺时间 {miss} 条，填充率 {100*(tot-miss)/max(tot,1):.1f}%")

    print("\n=== 时间跨度（按发布时间） ===")
    for r in con.execute(
            "SELECT platform, MIN(publish_time), MAX(publish_time) FROM content "
            "WHERE verified=1 AND publish_time IS NOT NULL AND publish_time<>'' GROUP BY 1 ORDER BY 3 DESC"):
        print(f"  {r[0]:12s} {r[1]}  →  {r[2]}")
    con.close()


def export_csv():
    """导出带发布时间的内容总表。"""
    con = sqlite3.connect(DB)
    cur = con.execute("""
        SELECT c.brand, c.platform, c.content_type, c.publish_time, c.collect_time,
               c.author, c.title, json_extract(c.metrics_json,'$.likes') likes,
               json_extract(c.metrics_json,'$.collects') collects,
               json_extract(c.metrics_json,'$.comments') comments,
               LENGTH(COALESCE(c.body,'')) body_len, c.url
        FROM content c WHERE c.verified=1 ORDER BY c.platform, c.brand, c.publish_time DESC
    """)
    cols = ["brand", "platform", "content_type", "publish_time", "collect_time", "author", "title",
            "likes", "collects", "comments", "body_len", "url"]
    out_dir = os.path.join(ROOT, "exports", "data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"content_full_{datetime.date.today().isoformat()}.csv")
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["品牌", "平台", "类型", "发布时间", "采集时间", "作者", "标题",
                    "点赞", "收藏", "评论", "正文字数", "链接"])
        for row in cur:
            w.writerow(row)
            n += 1
    con.close()
    print(f"\n已导出 {n} 条 → {path}")
    return path


if __name__ == "__main__":
    print("=== 1. 全量 jsonl 入库 ===", flush=True)
    ingest_all()
    print("\n=== 2. 回填发布时间 ===", flush=True)
    print(f"  回填 {backfill_time()} 条", flush=True)
    print("\n=== 3. 完整性核对 ===", flush=True)
    report()
    print("\n=== 4. 导出表格 ===", flush=True)
    export_csv()
    print("\n=== 5. 校验闸门（只有通过校验的才允许对外展示）===", flush=True)
    os.system(f'"{PY}" "{os.path.join(ROOT, "crawler/verify_and_gate.py")}"')
    print("\n=== 6. 重新导出（只含已校验数据）===", flush=True)
    export_csv()
    print("\n=== 7. 对外数据边界守卫 ===", flush=True)
    os.system(f'"{PY}" "{os.path.join(ROOT, "crawler/check_data_boundary.py")}"')