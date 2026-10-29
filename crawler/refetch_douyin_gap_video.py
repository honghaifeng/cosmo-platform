#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音缺口视频重取（只下载，不写库）。

库里存的 douyinvod / aweme/v1/play/ 直链可能已 404（实测 2/2 404），
只能回源 detail 接口拿当次签发的 play_addr。

与 crawler/refetch_douyin_media.py 的区别：
  · 那个脚本会顺手 UPDATE content.media_json；这里**只下载文件**，
    不碰数据库（media_json 是溯源证据，不该被后面的临时链接覆盖）。
  · 只处理 media_gap_list 里 douyin 且仍缺视频的条目。

用法：
  python3 crawler/refetch_douyin_gap_video.py --dry-run
  python3 crawler/refetch_douyin_gap_video.py --limit 5
"""
import argparse
import asyncio
import os
import random
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
MEDIA = os.path.join(ROOT, "knowledge", "media")
PROFILE = os.path.join(MC, "browser_data", "dy_user_data_dir")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
sys.path.insert(0, MC)
sys.path.insert(0, os.path.join(ROOT, "crawler"))


def download(url, dst, max_mb=150):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    part = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://www.douyin.com/"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
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
        if n < 4096:
            os.remove(part)
            return False, n, "文件过小"
        os.replace(part, dst)
        return True, n, ""
    except Exception as e:
        if os.path.exists(part):
            os.remove(part)
        return False, 0, f"{type(e).__name__} {str(e)[:60]}"


async def run(limit, dry):
    import media_gap_list
    jobs = [j for j in media_gap_list.build()
            if j["platform"] == "douyin" and j["need_video"]]
    if limit:
        jobs = jobs[:limit]
    print(f"抖音待补视频 {len(jobs)} 条")
    if not jobs or dry:
        for j in jobs:
            print(f"  id={j['id']} {j['brand']} {j['url']}")
        return

    from playwright.async_api import async_playwright
    from media_platform.douyin.client import DouYinClient

    ok = fail = 0
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=True, locale="zh-CN", channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-gpu", "--disable-dev-shm-usage"])
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await pg.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(2500)
        except Exception:
            pass
        cd = {c["name"]: c["value"] for c in await ctx.cookies()}
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in cd.items()),
                   "User-Agent": UA, "Origin": "https://www.douyin.com",
                   "Referer": "https://www.douyin.com/"}
        cli = DouYinClient(headers=headers, playwright_page=pg, cookie_dict=cd)

        for i, j in enumerate(jobs, 1):
            aid = (j["url"] or "").rstrip("/").split("/")[-1].split("?")[0]
            if not aid.isdigit():
                print(f"  ✗ {aid} 不是数字 aweme_id")
                fail += 1
                continue
            brand = j["brand"] or "UNKNOWN"
            dst = os.path.join(MEDIA, brand, "video", f"douyin:{aid}.mp4")
            if os.path.exists(dst) and os.path.getsize(dst) > 4096:
                print(f"  = {aid} 已在盘")
                continue
            try:
                d = await cli.get_video_by_id(aid)
            except Exception as e:
                print(f"  ✗ {aid} detail 失败 {type(e).__name__} {str(e)[:60]}")
                fail += 1
                await asyncio.sleep(random.uniform(5, 8))
                continue
            urls = (((d or {}).get("video") or {}).get("play_addr") or {}).get("url_list") or []
            got = False
            for u in urls[:3]:
                got, n, info = download(u, dst)
                if got:
                    print(f"  ✓ {i}/{len(jobs)} {aid} {n//1024}KB")
                    ok += 1
                    break
            if not got:
                print(f"  ✗ {i}/{len(jobs)} {aid} 下载失败 {info if urls else '无播放地址'}")
                fail += 1
            await asyncio.sleep(random.uniform(3, 6))
        await ctx.close()
    print(f"\n完成：成功 {ok} 失败 {fail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.limit, a.dry_run))


if __name__ == "__main__":
    main()
