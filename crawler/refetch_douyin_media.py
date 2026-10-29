#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音媒体重取（解决「库里存的是过期签名链接」这个死结）。

背景：
  media_json 里的 video 是 douyinvod.com 的签名直链，签名几小时就过期，
  事后直接下必定 403（实测 6/6 全 403）。所以「补下视频」不能靠库里的旧链接，
  必须先回源 /aweme/v1/web/aweme/detail/ 拿新鲜 play_addr。

流程（每条）：
  aweme_id → detail 接口 → fresh play_addr → 下载 mp4
            ↘ 顺便更新 media_json 的 video / cover、回填 desc（若正文为空）

限速与熔断：
  · 每条 sleep 2~4s（detail 与 CDN 各一次，避免连续打）
  · 连续失败 5 条即停止（说明被 Argus 拦了，继续只会更糟）
  · 已落盘的不再请求（断点续跑）

用法：
  tools/MediaCrawler/.venv/bin/python crawler/refetch_douyin_media.py --report
  tools/MediaCrawler/.venv/bin/python crawler/refetch_douyin_media.py --only-thin
  tools/MediaCrawler/.venv/bin/python crawler/refetch_douyin_media.py --limit 50
"""
import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
MEDIA_ROOT = os.path.join(ROOT, "knowledge", "media")
PROFILE = os.path.join(MC, "browser_data", "dy_user_data_dir")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

sys.path.insert(0, MC)


def local_aids():
    """本地已落盘的 aweme_id → 路径"""
    import glob
    out = {}
    for p in glob.glob(os.path.join(MEDIA_ROOT, "*", "video", "douyin:*.mp4")):
        if os.path.getsize(p) > 4096:
            out[os.path.basename(p)[len("douyin:"):-4]] = p
    return out


def pick(con, only_thin=False):
    """挑选待补条目：verified 且本地无视频文件"""
    sql = """SELECT c.id, c.brand, c.url, c.media_json, c.body
             FROM content c WHERE c.platform='douyin' AND c.verified=1
             ORDER BY c.id DESC"""
    rows = con.execute(sql).fetchall()
    if only_thin:
        thin = {r[0] for r in con.execute(
            """SELECT c.id FROM content c WHERE c.platform='douyin' AND c.verified=1
               AND LENGTH(COALESCE(c.body,''))<30
               AND NOT EXISTS (SELECT 1 FROM media_text m WHERE m.content_id=c.id)""")}
    else:
        thin = None
    have = local_aids()
    todo = []
    for cid, brand, url, mj, body in rows:
        aid = (url or "").rstrip("/").split("/")[-1]
        if not aid or not aid.isdigit() or aid in have:
            continue
        if thin is not None and cid not in thin:
            continue
        try:
            m = json.loads(mj or "{}")
        except Exception:
            m = {}
        todo.append((cid, brand, aid, m, body))
    return todo


def download(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    part = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.douyin.com/"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while True:
            b = r.read(262144)
            if not b:
                break
            f.write(b)
    sz = os.path.getsize(part)
    if sz < 4096:
        os.remove(part)
        raise IOError(f"文件过小 {sz}B")
    os.replace(part, dst)
    return sz


async def run(a):
    con = sqlite3.connect(DB, timeout=30)
    todo = pick(con, a.only_thin)
    print(f"待补 {len(todo)} 条" + ("（仅内容单薄的）" if a.only_thin else ""), flush=True)
    if a.report or not todo:
        con.close()
        return
    if a.limit:
        todo = todo[:a.limit]

    from playwright.async_api import async_playwright
    from media_platform.douyin.client import DouYinClient

    ok = fail = 0
    streak = 0
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
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in cd.items()),
            "User-Agent": UA,
            "Origin": "https://www.douyin.com",
            "Referer": "https://www.douyin.com/",
        }
        cli = DouYinClient(headers=headers, playwright_page=pg, cookie_dict=cd)

        for i, (cid, brand, aid, m, body) in enumerate(todo, 1):
            try:
                d = await cli.get_video_by_id(aid)
            except Exception as e:
                fail += 1
                streak += 1
                print(f"  ✗ {i}/{len(todo)} {aid} 详情失败 {type(e).__name__} {str(e)[:80]}", flush=True)
                await asyncio.sleep(random.uniform(6, 10))
                if streak >= 5:
                    print("  连续失败 5 条，判定被拦，停止本轮", flush=True)
                    break
                continue

            if not d:
                fail += 1
                streak += 1
                print(f"  ✗ {i}/{len(todo)} {aid} 详情为空", flush=True)
                await asyncio.sleep(random.uniform(4, 7))
                if streak >= 5:
                    break
                continue

            v = d.get("video") or {}
            urls = ((v.get("play_addr") or {}).get("url_list") or [])
            desc = (d.get("desc") or "").strip()
            cover = ((v.get("cover") or {}).get("url_list") or [""])[0]
            if desc:
                m["desc"] = desc
            if cover:
                m["cover"] = cover
            dl_ok = False
            if urls:
                m["video"] = urls[0]
                dst = os.path.join(MEDIA_ROOT, brand or "UNKNOWN", "video", f"douyin:{aid}.mp4")
                for u in urls[:3]:
                    try:
                        sz = download(u, dst)
                        dl_ok = True
                        break
                    except Exception as e:
                        last = f"{type(e).__name__} {str(e)[:60]}"
                        continue
            con.execute("UPDATE content SET media_json=? WHERE id=?",
                        (json.dumps(m, ensure_ascii=False), cid))
            con.commit()

            if dl_ok:
                ok += 1
                streak = 0
                print(f"  ✓ {i}/{len(todo)} {aid} {sz//1024}KB desc={len(desc)}字", flush=True)
            else:
                fail += 1
                streak += 1
                print(f"  ✗ {i}/{len(todo)} {aid} 下载失败 {last if urls else '无播放地址'}", flush=True)
                if streak >= 5:
                    print("  连续失败 5 条，停止本轮", flush=True)
                    break
            await asyncio.sleep(random.uniform(a.sleep * 0.7, a.sleep * 1.4))

        await ctx.close()
    con.close()
    print(f"\n完成：成功 {ok}，失败 {fail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--only-thin", action="store_true", help="只补正文空且无媒体文本的")
    ap.add_argument("--sleep", type=float, default=2.5)
    a = ap.parse_args()
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
