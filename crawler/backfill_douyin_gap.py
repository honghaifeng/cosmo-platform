#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音缺口补齐（只补「页面看不到」的那几条，不做全量）。

抖音这边缺口很小（几百条里只剩 2 个视频 + 2 张封面），大部分是库里
media_json 的直链还能用：aweme/v1/play/ 会 302 到真实 CDN，签名过期也能重定向。

做法：从 media_gap_list.build() 取 platform=douyin 且仍缺的条目，
用库里地址直接下（带 Referer），落盘到标准命名：
  knowledge/media/<品牌>/video/douyin:<aweme_id>.mp4
  knowledge/media/<品牌>/cover/douyin:<aweme_id>.<ext>

用法：
  python3 crawler/backfill_douyin_gap.py --dry-run
  python3 crawler/backfill_douyin_gap.py
"""
import argparse
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "crawler"))

MEDIA = os.path.join(ROOT, "knowledge", "media")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")


def aweme_id(url):
    m = re.search(r"(\d{15,25})", url or "")
    return m.group(1) if m else ""


def fetch(url, dst, max_mb=150):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    part = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://www.douyin.com/",
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            ctype = r.headers.get("Content-Type", "")
            n = 0
            with open(part, "wb") as f:
                while True:
                    b = r.read(262144)
                    if not b:
                        break
                    n += len(b)
                    if n > max_mb * 1024 * 1024:
                        raise IOError("超过大小上限")
                    f.write(b)
        if n < 1024:
            os.remove(part)
            return False, 0, "文件过小"
        os.replace(part, dst)
        return True, n, ctype
    except Exception as e:
        if os.path.exists(part):
            os.remove(part)
        return False, 0, f"{type(e).__name__} {str(e)[:60]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import media_gap_list
    jobs = [i for i in media_gap_list.build() if i["platform"] == "douyin"]
    print(f"抖音缺口 {len(jobs)} 条")
    if a.dry_run:
        for j in jobs:
            print(f"  id={j['id']} {j['brand']} 缺视频{j['need_video']} 缺封面{j['miss_cover']} {j['url']}")
        return

    ok = fail = 0
    for j in jobs:
        aid = aweme_id(j["url"])
        if not aid:
            print(f"  ✗ id={j['id']} 取不到 aweme_id")
            fail += 1
            continue
        brand = j["brand"] or "UNKNOWN"
        if j["need_video"] and j["video_url"]:
            dst = os.path.join(MEDIA, brand, "video", f"douyin:{aid}.mp4")
            if os.path.exists(dst) and os.path.getsize(dst) > 4096:
                print(f"  = {aid} 视频已在盘")
            else:
                got, n, info = fetch(j["video_url"], dst)
                print(f"  {'✓' if got else '✗'} {aid} 视频 {n//1024}KB {'' if got else info}")
                ok, fail = (ok + 1, fail) if got else (ok, fail + 1)
        if j["miss_cover"] and j["cover_url"]:
            ext = ".png" if ".png" in j["cover_url"].lower() else ".jpg"
            dst = os.path.join(MEDIA, brand, "cover", f"douyin:{aid}{ext}")
            if os.path.exists(dst) and os.path.getsize(dst) > 1024:
                print(f"  = {aid} 封面已在盘")
            else:
                got, n, info = fetch(j["cover_url"], dst)
                print(f"  {'✓' if got else '✗'} {aid} 封面 {n} B {'' if got else info}")
                ok, fail = (ok + 1, fail) if got else (ok, fail + 1)
        time.sleep(2)
    print(f"\n完成：成功 {ok} 失败 {fail}")


if __name__ == "__main__":
    main()
