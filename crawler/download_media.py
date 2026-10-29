#!/usr/bin/env python3
"""download_media.py — 从 content.media_json 把图片/视频落地到知识库媒体目录。

用法:
    python3 crawler/download_media.py [--per-brand 5] [--brand MitoQ] [--timeout 15]

规则:
- 来源: db/cosmobounty.db content 表 media_json ({"images": [...], "video": "..."})
- 落地: knowledge/media/<brand>/post/  (内容配图) 或 video/ (视频)
- 清单: knowledge/media/manifest.json 追加记录 (content_id, brand, url, path, status, time)
- CDN 链接有时效, 过期返回 403/404 时在 manifest 标 expired, 不重试
- 幂等: 已下载的 text_hash 跳过
"""
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "db", "cosmobounty.db")
MEDIA_ROOT = os.path.join(PROJ, "knowledge", "media")
MANIFEST = os.path.join(MEDIA_ROOT, "manifest.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {"items": []}


def done_keys(manifest):
    return {(it.get("content_id"), it.get("url")) for it in manifest["items"]
            if it.get("status") == "ok"}


def fetch(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ext_of(url, default):
    path = urlparse(url).path
    for e in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".gif"):
        if e in path.lower():
            return e.lstrip(".")
    return default


def main():
    args = sys.argv[1:]
    per_brand = 5
    only_brand = None
    timeout = 15
    if "--per-brand" in args:
        per_brand = int(args[args.index("--per-brand") + 1])
    if "--brand" in args:
        only_brand = args[args.index("--brand") + 1]
    if "--timeout" in args:
        timeout = int(args[args.index("--timeout") + 1])

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT id, brand, media_json, text_hash FROM content "
        "WHERE media_json IS NOT NULL AND media_json != '' "
        + ("AND brand = ? " if only_brand else "")
        + "ORDER BY id DESC", (only_brand,) if only_brand else ()
    ).fetchall()

    manifest = load_manifest()
    have = done_keys(manifest)
    stats = {"ok": 0, "expired": 0, "skip": 0}
    counts = {}

    for cid, brand, mjson, th in rows:
        if counts.get(brand, 0) >= per_brand:
            continue
        try:
            media = json.loads(mjson)
        except Exception:
            continue
        targets = []
        for u in (media.get("images") or [])[:1]:  # 每条只取首图, 控量
            targets.append(("post", u, "jpg"))
        v = media.get("video")
        if v:
            targets.append(("video", v, "mp4"))
        for kind, url, dft in targets:
            if (cid, url) in have:
                stats["skip"] += 1
                continue
            bdir = os.path.join(MEDIA_ROOT, brand, kind)
            os.makedirs(bdir, exist_ok=True)
            fname = f"{th or cid}.{ext_of(url, dft)}"
            fpath = os.path.join(bdir, fname)
            try:
                data = fetch(url, timeout)
                if len(data) < 2048:  # 占位图
                    raise ValueError("too small")
                with open(fpath, "wb") as f:
                    f.write(data)
                status = "ok"
            except Exception as e:
                status = f"expired({type(e).__name__})"
            manifest["items"].append({
                "content_id": cid, "brand": brand, "kind": kind, "url": url,
                "path": os.path.relpath(fpath, PROJ) if status == "ok" else None,
                "status": status, "time": datetime.now().isoformat(timespec="seconds"),
            })
            if status == "ok":
                stats["ok"] += 1
                counts[brand] = counts.get(brand, 0) + 1
            else:
                stats["expired"] += 1

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(json.dumps(stats, ensure_ascii=False), "| per-brand:", counts)


if __name__ == "__main__":
    main()
