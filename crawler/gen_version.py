#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_version.py — 从数据库实时生成 service/data/version.json（数据版本快照）。

为什么需要：
  version.json 原来靠手工写，改了数据忘了同步，就会出现「接口返回 1249 条，
  版本快照还写着 854」的两套数字。报告页、导出接口都读这个文件，
  一旦对不上，对外就自相矛盾。本脚本让快照永远由库算出。

口径（与 verify_and_gate / app.py 保持一致）：
  - contents     : content 表 verified=1 的条数
  - comments     : comment 表总数
  - by_platform  : verified=1 按平台分组
  - with_media   : verified=1 且 media_json 含实际媒体的条数

用法：
  python crawler/gen_version.py            # 生成 db 与 service 两份快照
  python crawler/gen_version.py --dry      # 只打印，不写文件
"""
import argparse
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
OUTS = [
    os.path.join(ROOT, "service", "data", "version.json"),
    os.path.join(ROOT, "data", "version.json"),
]


def has_media(mj):
    """media_json 里是否真有图/视频（排除空壳 {} 和 {"images":[],"video":"","cover":""}）"""
    if not mj:
        return False
    try:
        m = json.loads(mj)
    except Exception:
        return False
    if not isinstance(m, dict):
        return False
    return bool(m.get("images") or m.get("video") or m.get("cover"))


def build(con):
    con.row_factory = sqlite3.Row
    contents = con.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
    comments = con.execute("SELECT COUNT(*) FROM comment").fetchone()[0]
    by_platform = {r["platform"]: r["n"] for r in con.execute(
        "SELECT platform, COUNT(*) n FROM content WHERE verified=1 GROUP BY 1 ORDER BY n DESC")}
    with_media = 0
    for (mj,) in con.execute("SELECT media_json FROM content WHERE verified=1"):
        if has_media(mj):
            with_media += 1
    return {
        "date": time.strftime("%Y-%m-%d"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "contents": contents,
        "comments": comments,
        "by_platform": by_platform,
        "with_media": with_media,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    v = build(con)
    con.close()

    print(json.dumps(v, ensure_ascii=False, indent=1))
    if a.dry:
        return
    for p in OUTS:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=1)
        print("写入", p)


if __name__ == "__main__":
    main()
