#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""download_douyin_media.py — 批量下载抖音视频 + 封面图到本地。

背景：
  content 表 douyin 平台 269 条，media_json 里有视频直链（aweme/v1/play/）和封面图。
  实测：aweme/v1/play/ 直链会 302 重定向到真实 CDN（douyinvod.com），
  即使 URL 里的 sign 参数过期，接口仍动态重定向，视频现在仍可下载。

规则（有节奏，不暴力）：
  - 302 跟随重定向，带 Referer: https://www.douyin.com/
  - 每条视频之间随机 sleep 2~5 秒，每 30 条长停 20~40 秒
  - 幂等：已下载（manifest.json status=ok）跳过
  - 落地: knowledge/media/<brand>/video/ 和 /cover/

用法:
    python3 crawler/download_douyin_media.py --report      # 只统计可下载量
    python3 crawler/download_douyin_media.py               # 全量下载
    python3 crawler/download_douyin_media.py --limit 20    # 先下前 20 条试水
    python3 crawler/download_douyin_media.py --brand MitoQ # 只下某品牌
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "db", "cosmobounty.db")
MEDIA_ROOT = os.path.join(PROJ, "knowledge", "media")
MANIFEST = os.path.join(MEDIA_ROOT, "douyin_manifest.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {"items": []}


def done_keys(manifest):
    return {it.get("content_id") for it in manifest["items"]
            if it.get("status") == "ok"}


def fetch(url, timeout=40):
    """带 302 跟随 + 抖音 Referer 的下载。返回 bytes。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://www.douyin.com/",
                      "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ext_of(url, default):
    path = urlparse(url).path
    for e in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".gif"):
        if e in path.lower():
            return e.lstrip(".")
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只统计，不下载")
    ap.add_argument("--limit", type=int, default=0, help="最多下载 N 条")
    ap.add_argument("--brand", default="", help="只下某品牌")
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cond = "platform='douyin' AND media_json IS NOT NULL AND media_json != ''"
    params = []
    if a.brand:
        cond += " AND brand=?"
        params.append(a.brand)
    rows = con.execute(
        f"SELECT id, brand, title, media_json, text_hash FROM content "
        f"WHERE {cond} ORDER BY id", params).fetchall()
    con.close()

    manifest = load_manifest()
    have = done_keys(manifest)
    todo = [r for r in rows if r[0] not in have]

    # 统计
    n_video = sum(1 for r in todo if json.loads(r[3]).get("video"))
    n_cover = sum(1 for r in todo if json.loads(r[3]).get("cover"))
    print(f"抖音 content 共 {len(rows)} 条，已下载 {len(have)}，待下载 {len(todo)}"
          f"（含视频 {n_video}，封面 {n_cover}）")

    if a.report:
        return

    if a.limit:
        todo = todo[:a.limit]

    stats = {"ok_video": 0, "ok_cover": 0, "fail_video": 0, "fail_cover": 0}
    for i, (cid, brand, title, mjson, th) in enumerate(todo):
        try:
            media = json.loads(mjson)
        except Exception:
            continue

        # 视频
        vurl = media.get("video") or ""
        if vurl:
            vdir = os.path.join(MEDIA_ROOT, brand, "video")
            os.makedirs(vdir, exist_ok=True)
            vpath = os.path.join(vdir, f"{th or cid}.mp4")
            try:
                data = fetch(vurl)
                if len(data) < 4096:
                    raise ValueError(f"too small {len(data)}")
                with open(vpath, "wb") as f:
                    f.write(data)
                stats["ok_video"] += 1
                manifest["items"].append({"content_id": cid, "brand": brand,
                                          "kind": "video", "url": vurl,
                                          "path": os.path.relpath(vpath, PROJ),
                                          "status": "ok",
                                          "time": datetime.now().isoformat(timespec="seconds")})
                print(f"[{i+1}/{len(todo)}] ✓视频 id={cid} {title[:24]}… {len(data)//1024}KB", flush=True)
            except Exception as e:
                stats["fail_video"] += 1
                manifest["items"].append({"content_id": cid, "brand": brand,
                                          "kind": "video", "url": vurl, "path": None,
                                          "status": f"fail({type(e).__name__})",
                                          "time": datetime.now().isoformat(timespec="seconds")})
                print(f"[{i+1}/{len(todo)}] ✗视频 id={cid} {repr(e)[:60]}", flush=True)

        # 封面图
        curl = media.get("cover") or ""
        if curl:
            cdir = os.path.join(MEDIA_ROOT, brand, "cover")
            os.makedirs(cdir, exist_ok=True)
            cpath = os.path.join(cdir, f"{th or cid}.{ext_of(curl, 'jpg')}")
            try:
                data = fetch(curl, timeout=20)
                if len(data) < 1024:
                    raise ValueError("too small")
                with open(cpath, "wb") as f:
                    f.write(data)
                stats["ok_cover"] += 1
            except Exception:
                stats["fail_cover"] += 1

        # 节奏控制：每条 2~5s，每 30 条长停 20~40s
        time.sleep(random.uniform(2, 5))
        if (i + 1) % 30 == 0:
            print("  … 长停 20~40s 防限流", flush=True)
            time.sleep(random.uniform(20, 40))

        # 每 10 条落一次 manifest，防中断丢进度
        if (i + 1) % 10 == 0:
            with open(MANIFEST, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=1)

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print(f"\n完成：视频 ok {stats['ok_video']}/fail {stats['fail_video']}，"
          f"封面 ok {stats['ok_cover']}/fail {stats['fail_cover']}")


if __name__ == "__main__":
    main()
