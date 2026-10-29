#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「页面实际可见」口径列出媒体缺口（逐条，可导出 JSON）。

判定函数直接复用 service/app.py 的 local_media / local_media_list，
所以这里「缺」= 线上页面真的看不到，不是文件不在盘上。

用法：
  python3 crawler/media_gap_list.py                 # 汇总
  python3 crawler/media_gap_list.py --json out.json # 逐条导出（补数据脚本吃它）
  python3 crawler/media_gap_list.py --by-brand
"""
import argparse
import collections
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "service"))
os.environ.setdefault("COSMOBOUNTY_DB", os.path.join(ROOT, "service", "data", "cosmobounty.db"))
os.environ.setdefault("COSMOBOUNTY_KB", os.path.join(ROOT, "knowledge"))

import app as svc  # noqa: E402


def build():
    conn = sqlite3.connect(f"file:{svc.DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, brand, platform, url, media_json, title FROM content WHERE verified=1"
    ).fetchall()
    out = []
    for r in rows:
        try:
            m = json.loads(r["media_json"] or "{}")
        except Exception:
            m = {}
        need_v = bool((m.get("video") or "").strip())
        imgs = [u for u in (m.get("images") or []) if (u or "").startswith("http")]
        need_c = bool((m.get("cover") or "").strip())
        plat, url, brand = r["platform"], r["url"] or "", r["brand"] or ""

        has_v = False
        if need_v:
            _, has_v = svc.local_media(plat, url, brand, m, "video")
        got_imgs = svc.local_media_list(plat, url, brand, "image") if imgs else []
        has_c = False
        if need_c:
            _, has_c = svc.local_media(plat, url, brand, m, "cover")

        miss_v = 1 if (need_v and not has_v) else 0
        miss_i = max(0, len(imgs) - len(got_imgs))
        miss_c = 1 if (need_c and not has_c) else 0
        if not (miss_v or miss_i or miss_c):
            continue
        out.append({"id": r["id"], "brand": brand, "platform": plat, "url": url,
                    "title": (r["title"] or "")[:60],
                    "need_video": miss_v, "miss_images": miss_i, "miss_cover": miss_c,
                    "n_images": len(imgs), "has_images": len(got_imgs),
                    "video_url": (m.get("video") or "")[:300],
                    "image_urls": imgs[:12],
                    "cover_url": (m.get("cover") or "")[:300]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--by-brand", action="store_true")
    a = ap.parse_args()
    items = build()
    if a.json:
        json.dump(items, open(a.json, "w"), ensure_ascii=False, indent=1)
        print("已导出", len(items), "条 →", a.json)

    if a.by_brand:
        g = collections.defaultdict(collections.Counter)
        for x in items:
            k = (x["platform"], x["brand"])
            g[k]["内容"] += 1
            g[k]["缺视频"] += x["need_video"]
            g[k]["缺图"] += x["miss_images"]
            g[k]["缺封面"] += x["miss_cover"]
        print(f"{'平台':8s} {'品牌':14s} {'内容':>6s} {'缺视频':>6s} {'缺图':>6s} {'缺封面':>6s}")
        for (p, b), c in sorted(g.items(), key=lambda kv: -kv[1]["缺视频"]):
            print(f"{p:8s} {b:14s} {c['内容']:6d} {c['缺视频']:6d} {c['缺图']:6d} {c['缺封面']:6d}")

    tot = collections.Counter()
    for x in items:
        tot["内容"] += 1
        tot["缺视频"] += x["need_video"]
        tot["缺图"] += x["miss_images"]
        tot["缺封面"] += x["miss_cover"]
        tot["platform_" + x["platform"]] += 1
    print("\n合计：", dict(tot))


if __name__ == "__main__":
    main()
