#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书图片落地（轻量版，纯 HTTP，不走浏览器）。

为什么必须现采现下：
  小红书图片地址带签名时间戳（sns-webpic-qc.xhscdn.com/202610261815/...），
  约 24 小时后一律 403，事后补下必定失败。

落盘命名与 service/app.py 的 local_media() 对齐：
  knowledge/media/<brand>/post/xhs:<note_id>.webp     首图（表格直接显示）
  knowledge/media/<brand>/post/xhs:<note_id>_2.webp   第 2 张起（备用）

用法：
  python3 crawler/fetch_xhs_images.py --dry-run
  python3 crawler/fetch_xhs_images.py --limit 300
  python3 crawler/fetch_xhs_images.py --all --limit 2000   # 含未校验
"""
import argparse
import json
import os
import random
import re
import sqlite3
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
KB = os.path.join(ROOT, "service", "knowledge")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"),
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}
EXT_BY_CT = {"image/webp": ".webp", "image/jpeg": ".jpg", "image/png": ".png",
             "image/gif": ".gif"}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-images", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    where = ("platform='xhs' AND media_json LIKE '%http%' "
             "AND media_json LIKE '%images%'")
    if not args.all:
        where += " AND verified=1"
    rows = con.execute(f"SELECT id,brand,url,media_json FROM content WHERE {where} "
                       f"ORDER BY id DESC").fetchall()
    print(f"候选 {len(rows)} 条")

    todo = []
    for r in rows:
        m = re.search(r"/(?:explore|discovery/item|search_result)/([0-9a-f]{16,32})", r["url"] or "")
        if not m:
            continue
        nid = m.group(1)
        brand = (r["brand"] or "未归属").replace("/", "_")
        pdir = os.path.join(KB, "media", brand, "post")
        if os.path.isfile(os.path.join(pdir, f"xhs:{nid}.webp")):
            continue
        try:
            media = json.loads(r["media_json"] or "{}")
        except Exception:
            continue
        imgs = [u for u in (media.get("images") or []) if u.startswith("http")]
        if imgs:
            todo.append((nid, brand, imgs[: args.max_images]))
    print(f"待下载 {len(todo)} 条笔记")

    if args.dry_run:
        for nid, brand, imgs in todo[:10]:
            print(f"  {nid} {brand} 图{len(imgs)} {imgs[0][:80]}")
        return

    ok = fail = 0
    n_done = 0
    for nid, brand, imgs in todo:
        if n_done >= args.limit:
            break
        pdir = os.path.join(KB, "media", brand, "post")
        os.makedirs(pdir, exist_ok=True)
        for k, u in enumerate(imgs):
            name = f"xhs:{nid}.webp" if k == 0 else f"xhs:{nid}_{k + 1}.webp"
            fp = os.path.join(pdir, name)
            if os.path.isfile(fp):
                continue
            try:
                data, ct = fetch(u)
                ext = EXT_BY_CT.get(ct.split(";")[0].strip(), ".webp")
                fp2 = fp[:-5] + ext
                with open(fp2, "wb") as f:
                    f.write(data)
                ok += 1
            except Exception as e:
                fail += 1
                if fail <= 15:
                    print(f"  × {nid} #{k + 1} {type(e).__name__} {str(e)[:50]}", flush=True)
            time.sleep(random.uniform(0.2, 0.5))
        n_done += 1
        if n_done % 20 == 0:
            print(f"  …{n_done} 条 / 落盘 {ok} / 失败 {fail}", flush=True)

    con.close()
    print(f"=== 完成：{n_done} 条笔记，落盘 {ok} 张图，失败 {fail} ===")


if __name__ == "__main__":
    main()
