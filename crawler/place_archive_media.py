#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/xhs_archive/media 里的归档媒体「归位」到 knowledge/media 的标准命名位置。

背景：
  小红书归档（build_xhs_archive.py）把媒体下到 data/xhs_archive/media/<note_id>[_N].<ext>，
  但线上服务只认 knowledge/media/<品牌>/<video|post>/<平台>:<note_id>[_N].<ext>。
  结果：这批 155 个视频 + 578 张图虽然本地有、也校验过，但页面上一条也看不到 ——
  不是没抓到，是放错了地方、也不在七牛映射里。

做法：
  1. note_id → 品牌 从数据库 content.url 反查（不猜）
  2. 复制（不是移动，归档保持可还原）到 knowledge/media/<品牌>/<类型>/xhs:<note_id>[_N].<ext>
  3. 已存在则跳过；品牌查不到的不瞎放，单独报告

用法：
  python3 crawler/place_archive_media.py --dry-run
  python3 crawler/place_archive_media.py --apply
"""
import argparse
import collections
import os
import re
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "xhs_archive", "media")
DST = os.path.join(ROOT, "knowledge", "media")
DB = os.path.join(ROOT, "service", "data", "cosmobounty.db")

VIDEO_EXT = {".mp4", ".m4v", ".mov"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
NAME_RX = re.compile(r"^(?P<nid>[0-9a-f]{16,32})(?:_(?P<idx>\d+))?\.(?P<ext>[a-z0-9]+)$", re.I)


def brand_map():
    """note_id → 品牌。同一 note 多行时取第一条（同一条内容只会属于一个品牌）。"""
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    try:
        for url, brand in c.execute("SELECT url, brand FROM content WHERE platform='xhs'"):
            m = re.search(r"/(?:explore|discovery/item|search_result)/([0-9a-f]{16,32})", url or "")
            if m and m.group(1) not in out:
                out[m.group(1)] = brand
    finally:
        c.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.apply and not a.dry_run:
        a.dry_run = True

    bm = brand_map()
    print(f"数据库可反查品牌的小红书 note：{len(bm)}", flush=True)

    files = sorted(f for f in os.listdir(SRC) if not f.startswith("."))
    plan = collections.Counter()
    unmapped = []
    todo = []

    for f in files:
        m = NAME_RX.match(f)
        ext = os.path.splitext(f)[1].lower()
        if not m or ext not in (VIDEO_EXT | IMAGE_EXT):
            plan["无法识别文件名"] += 1
            unmapped.append((f, "文件名不符合 <note_id>[_N].<ext>"))
            continue
        nid, idx = m.group("nid"), m.group("idx")
        brand = bm.get(nid)
        if not brand:
            plan["note_id 不在库中"] += 1
            unmapped.append((f, "库里没有这个 note_id"))
            continue
        kind = "video" if ext in VIDEO_EXT else "post"
        name = f"xhs:{nid}" + (f"_{idx}" if idx else "") + ext
        dst = os.path.join(DST, brand, kind, name)
        if os.path.exists(dst):
            plan["已存在"] += 1
            continue
        plan["待归位"] += 1
        todo.append((os.path.join(SRC, f), dst, brand, kind))

    by_brand = collections.Counter((b, k) for _, _, b, k in todo)
    print("\n处理结果：")
    for k, v in plan.most_common():
        print(f"  {k:14s} {v}")
    print("\n待归位明细（品牌/类型）：")
    for (b, k), n in sorted(by_brand.items()):
        print(f"  {b:14s} {k:6s} {n}")

    if unmapped:
        print(f"\n未归位 {len(unmapped)} 个（前 10）：")
        for f, why in unmapped[:10]:
            print(f"  {f}  ← {why}")

    if not a.apply:
        print("\n[dry-run] 未写盘。加 --apply 执行。")
        return

    ok = 0
    for src, dst, brand, kind in todo:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        ok += 1
    print(f"\n已归位 {ok} 个文件")


if __name__ == "__main__":
    main()
