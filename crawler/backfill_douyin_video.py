#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_douyin_video.py — 补下抖音视频到本地（供 ASR 转写与站内播放）。

背景：
  抖音视频直链里有两类：
    1) sf11-cdn-tos.douyinstatic.com/obj/tos-cn-ve-2774/...  静态 CDN，不带签名，长期有效
    2) v26-web.douyinvod.com/.../<hex>/...                    带签名，约 24h 过期
  实测静态 CDN 仍可下，所以历史库存能补回来。缺视频 → 缺 ASR → 正文为空，
  原始数据页就只能显示「仅标题」，内容不完整。

规则：
  - 只补「本地还没有该 aweme_id 的 mp4」的条目，幂等
  - 下载先落 .part 再改名，避免半截文件被当成有效
  - 小于 20KB 视为失败（多半是错误页或封面占位）
  - 礼貌节奏：每次请求间隔 jitter，连续失败 5 次即停（说明 CDN 开始拦）

用法：
  python crawler/backfill_douyin_video.py --report
  python crawler/backfill_douyin_video.py --limit 10
  python crawler/backfill_douyin_video.py            # 全量补
"""
import argparse
import glob
import json
import os
import random
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
MEDIA_ROOT = os.path.join(ROOT, "knowledge", "media")
UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Safari/537.36"),
    "Referer": "https://www.douyin.com/",
}


def local_ids():
    out = set()
    for p in glob.glob(os.path.join(MEDIA_ROOT, "*", "video", "douyin:*.mp4")):
        out.add(os.path.basename(p)[len("douyin:"):-4])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--only-static", action="store_true",
                    help="只下不带签名的静态 CDN（成功率最高）")
    a = ap.parse_args()

    have = local_ids()
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id, brand, url, media_json FROM content "
        "WHERE verified=1 AND platform='douyin' ORDER BY id DESC").fetchall()

    todo = []
    for cid, brand, url, mj in rows:
        aid = (url or "").rstrip("/").split("/")[-1]
        if not aid or aid in have:
            continue
        try:
            m = json.loads(mj or "{}")
        except Exception:
            m = {}
        v = (m.get("video") or "").strip()
        if not v:
            continue
        if a.only_static and "douyinvod.com" in v:
            continue
        todo.append((cid, brand, aid, v))

    static = sum(1 for t in todo if "douyinstatic.com" in t[3])
    print(f"抖音 verified {len(rows)} · 本地已有 {len(have)} · 待补 {len(todo)}"
          f"（其中静态 CDN {static}）")
    if a.report:
        con.close()
        return
    if a.limit:
        todo = todo[:a.limit]

    ok = fail = 0
    streak = 0
    for cid, brand, aid, v in todo:
        dst_dir = os.path.join(MEDIA_ROOT, brand or "UNKNOWN", "video")
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, f"douyin:{aid}.mp4")
        part = dst + ".part"
        try:
            req = urllib.request.Request(v, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
            sz = os.path.getsize(part)
            if sz < 20480:
                os.remove(part)
                raise RuntimeError(f"too small {sz}B")
            os.replace(part, dst)
            ok += 1
            streak = 0
            print(f"[ok] {cid} {brand} {aid} {sz // 1024}KB", flush=True)
        except Exception as e:
            fail += 1
            streak += 1
            if os.path.exists(part):
                try:
                    os.remove(part)
                except Exception:
                    pass
            print(f"[fail] {cid} {aid} {repr(e)[:70]}", file=sys.stderr, flush=True)
            # 老 id 的链接大概率已过期（403/404），这是正常损耗，不能因此收工；
            # 只有连续大批失败才说明 CDN 真在拦。
            if streak >= 25:
                print("[停] 连续 25 次失败，CDN 可能在拦，收工", file=sys.stderr)
                break
        time.sleep(random.uniform(0.6, 1.6))

    con.close()
    print(f"\n完成：成功 {ok}，失败 {fail}")


if __name__ == "__main__":
    main()
