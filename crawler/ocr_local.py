#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从「本地已下载的图片」补做 OCR。

为什么需要：
  extract_media_text.py 的 OCR 走图片 CDN 直链，签名几小时就过期，
  历史库存全 403；但图片当时已经落地到 data/xhs_archive/media/。
  本脚本直接用本地文件 OCR，不再依赖网络。

产物：media_text(kind='ocr', source='image:i')，正文 body 不动。

用法：
  PY=/Users/v/.workbuddy/binaries/python/envs/default/bin/python
  $PY crawler/ocr_local.py --limit 5 --dry
  $PY crawler/ocr_local.py            # 全量补
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
MEDIA = os.path.join(ROOT, "data", "xhs_archive", "media")
KB_MEDIA = os.path.join(ROOT, "knowledge", "media")


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def local_images(content_id, platform, url, brand, media_json):
    """返回可用的本地图片路径列表。"""
    out = []
    m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{16,32})", url or "")
    nid = m.group(1) if m else ""
    if nid:
        out += sorted(glob.glob(os.path.join(MEDIA, f"{nid}_*.jpg")))
        out += sorted(glob.glob(os.path.join(MEDIA, f"{nid}_*.png")))
    if brand:
        p = os.path.join(KB_MEDIA, brand, "post")
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, f"{platform}:{nid}*.jpg")))
    # 去重保序
    seen, uniq = set(), []
    for f in out:
        if f not in seen and os.path.getsize(f) > 2000:
            seen.add(f)
            uniq.append(f)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="xhs")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-images", type=int, default=6)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT c.id, c.platform, c.title, c.url, c.brand, c.media_json FROM content c
           WHERE c.platform=? AND c.media_json LIKE '%images%'
             AND NOT EXISTS (SELECT 1 FROM media_text m WHERE m.content_id=c.id AND m.kind='ocr')
           ORDER BY c.id""", (args.platform,)).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"待补 OCR {len(rows)} 条（本地图片）", flush=True)

    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    ok = nofile = empty = 0
    for i, r in enumerate(rows, 1):
        imgs = local_images(r["id"], r["platform"], r["url"], r["brand"], r["media_json"])
        if not imgs:
            nofile += 1
            continue
        texts = []
        for j, f in enumerate(imgs[:args.max_images]):
            try:
                res, _ = ocr(f)
                t = "\n".join(x[1].strip() for x in (res or []) if x[1].strip())
            except Exception:
                t = ""
            if t:
                texts.append(t)
                if not args.dry:
                    con.execute(
                        "INSERT OR REPLACE INTO media_text (content_id,kind,source,text,created_at)"
                        " VALUES (?,?,?,?,?)", (r["id"], "ocr", f"image:{j}", t, now()))
        if texts:
            ok += 1
            if not args.dry:
                con.commit()
        else:
            empty += 1
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} 成功 {ok} 无图 {nofile} 无字 {empty}", flush=True)

    print(f"完成：OCR 成功 {ok} 条，无本地图 {nofile} 条，图内无文字 {empty} 条")
    con.close()


if __name__ == "__main__":
    main()
