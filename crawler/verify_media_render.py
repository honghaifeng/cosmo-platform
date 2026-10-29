#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按「页面实际能不能看到」来验收媒体，而不是按文件在不在盘上。

直接复用 service/app.py 里的解析函数（local_media / local_media_list），
所以它算出来的就是线上那套口径 —— 避免审计脚本自己一套算法、
看着 100% 上线却是裂的。

用法：
  python3 crawler/verify_media_render.py
  python3 crawler/verify_media_render.py --by-brand
"""
import argparse
import collections
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "service"))
os.environ.setdefault("COSMOBOUNTY_DB", os.path.join(ROOT, "service", "data", "cosmobounty.db"))
os.environ.setdefault("COSMOBOUNTY_KB", os.path.join(ROOT, "knowledge"))

import app as svc  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-brand", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{svc.DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, brand, platform, url, media_json FROM content WHERE verified=1").fetchall()

    tot = collections.Counter()
    per = collections.defaultdict(collections.Counter)

    for r in rows:
        try:
            media = __import__("json").loads(r["media_json"] or "{}")
        except Exception:
            media = {}
        need_v = 1 if (media.get("video") or "").strip() else 0
        need_i = len(media.get("images") or [])
        _v, has_v = svc.local_media(r["platform"], r["url"], r["brand"], media, "video")
        lis = svc.local_media_list(r["platform"], r["url"], r["brand"], "image")
        _c, has_c = svc.local_media(r["platform"], r["url"], r["brand"], media, "cover")
        b = r["brand"]

        tot["内容"] += 1
        per[b]["内容"] += 1
        if need_v:
            tot["有视频"] += 1
            per[b]["有视频"] += 1
            if has_v:
                tot["视频可播"] += 1
                per[b]["视频可播"] += 1
        if need_i:
            tot["有配图"] += 1
            tot["配图张数应有"] += need_i
            tot["配图张数可见"] += len(lis)
            per[b]["有配图"] += 1
            per[b]["配图张数应有"] += need_i
            per[b]["配图张数可见"] += len(lis)
            if lis:
                tot["配图可见"] += 1
                per[b]["配图可见"] += 1
        if has_c:
            tot["封面可见"] += 1
            per[b]["封面可见"] += 1

    def pct(x, y):
        return f"{x / y * 100:5.1f}%" if y else "  —  "

    print("=" * 66)
    print("页面可显示媒体（按 service/app.py 同一套解析口径）")
    print("=" * 66)
    print(f"校验通过内容      {tot['内容']}")
    print(f"有视频            {tot['有视频']:5d}   可播 {tot['视频可播']:5d}  "
          f"缺 {tot['有视频'] - tot['视频可播']:4d}  {pct(tot['视频可播'], tot['有视频'])}")
    print(f"有配图的内容      {tot['有配图']:5d}   可见 {tot['配图可见']:5d}  "
          f"缺 {tot['有配图'] - tot['配图可见']:4d}  {pct(tot['配图可见'], tot['有配图'])}")
    print(f"配图张数          {tot['配图张数应有']:5d}   可见 {tot['配图张数可见']:5d}  "
          f"{pct(tot['配图张数可见'], tot['配图张数应有'])}")
    print(f"封面可见          {tot['封面可见']}")

    if a.by_brand:
        print("\n按品牌：")
        print(f"{'品牌':14s}{'内容':>6s}{'视频可播/有':>16s}{'配图可见/有':>16s}{'张数':>14s}")
        for b, c in sorted(per.items(), key=lambda kv: -kv[1]["内容"]):
            print(f"{b:14s}{c['内容']:6d}"
                  f"{c['视频可播']:>8d}/{c['有视频']:<7d}"
                  f"{c['配图可见']:>8d}/{c['有配图']:<7d}"
                  f"{c['配图张数可见']:>7d}/{c['配图张数应有']:<6d}")
    print("\n注：缺 = 采集时只拿到带签名直链、本地没落盘（约 24h 后必定 403）。")


if __name__ == "__main__":
    main()
