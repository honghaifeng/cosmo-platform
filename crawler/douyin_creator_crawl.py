#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""douyin_creator_crawl.py — 抖音官号/达人主页内容采集 → 底表。

为什么需要：搜索流只能覆盖关键词命中，官号主页内容拿不全；
作者名脱敏导致官号内容无法从搜索结果识别。本脚本按 sec_uid 爬创作者主页全量作品。

特点：
  - 复用 MediaCrawler 的 DouYinClient（a_bogus 签名走已登录页面）
  - 复用已登录 profile：tools/MediaCrawler/browser_data/dy_user_data_dir
  - 节奏控制：翻页间隔随机 2~5s，每 30 条长停 15~30s（有节奏，不暴力）
  - 幂等：INSERT OR IGNORE，text_hash = douyin:<aweme_id>
  - 官号内容打 tags='official'，verified=1（账号实名归属即品牌归属，
    不走正文字面提及校验——搜索内容的 H1/H3 规则不适用于官号自有发布）
  - 原始层落盘 data/raw/douyin_official/<date>/<sec_uid>.jsonl，再入底表

用法：
  python3 crawler/douyin_creator_crawl.py --info  MS4wLjABAAAA...            # 只识别账号
  python3 crawler/douyin_creator_crawl.py --crawl MS4wLjABAAAA... --brand LAIFE
  python3 crawler/douyin_creator_crawl.py --crawl <sec_uid> --brand MitoQ --max-posts 30
"""
import argparse
import asyncio
import datetime
import json
import os
import random
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
PROFILE = os.path.join(MC, "browser_data", "dy_user_data_dir")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW_DIR = os.path.join(ROOT, "data", "raw", "douyin_official")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def clear_profile_locks():
    """历史教训：残留 Chrome 进程/SingletonLock 会让 persistent context 起不来。"""
    import glob, subprocess
    subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], capture_output=True)
    time.sleep(1)
    subprocess.run(["pkill", "-9", "-f", f"user-data-dir={PROFILE}"], capture_output=True)
    time.sleep(1)
    for p in glob.glob(os.path.join(PROFILE, "Singleton*")):
        try:
            os.remove(p)
        except OSError:
            pass


def to_int(v):
    try:
        return int(str(v).strip() or 0)
    except Exception:
        return 0


def ms_to_str(s):
    if not s:
        return None
    try:
        s = int(s)
        return datetime.datetime.fromtimestamp(s if s < 10**12 else s / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


async def run(sec_uid, brand, max_posts, info_only):
    sys.path.insert(0, MC)
    from playwright.async_api import async_playwright
    from media_platform.douyin.client import DouYinClient

    clear_profile_locks()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=False, user_agent=UA,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            # 首页必须真实加载成功：a_bogus 签名和 localStorage(msToken) 都依赖页面环境
            for attempt in range(3):
                try:
                    await page.goto("https://www.douyin.com/", timeout=45000,
                                    wait_until="domcontentloaded")
                    break
                except Exception as e:
                    print(f"[warn] 首页加载失败({attempt + 1}/3)：{e}")
                    await page.wait_for_timeout(3000)
            await page.wait_for_timeout(5000)
            client = DouYinClient(headers={"User-Agent": UA,
                                           "Referer": "https://www.douyin.com/"},
                                  playwright_page=page, cookie_dict={})
            # 关键：把登录态 Cookie 装进请求头（漏掉这步 = account blocked）
            await client.update_cookies(ctx)
            ls = await page.evaluate("() => window.localStorage")
            if ls.get("HasUserLogin") != "1":
                print("[error] 抖音登录态失效（HasUserLogin!=1），需要重新扫码：python3 scripts/login_window.py")
                await ctx.close()
                return

            info = await client.get_user_info(sec_uid)
            user = (info or {}).get("user") or {}
            nickname = user.get("nickname") or "?"
            print(f"账号识别：昵称={nickname} ｜ 抖音号={user.get('unique_id') or user.get('short_id')}"
                  f" ｜ 粉丝={user.get('follower_count')} ｜ 获赞={user.get('total_favorited')} ｜ 作品={user.get('aweme_count')}")
            print(f"  认证：{user.get('custom_verify') or '-'} ｜ {user.get('enterprise_verify_reason') or '-'}")
            print(f"  简介：{(user.get('signature') or '')[:100]}")
            print(f"  sec_uid={sec_uid}")
            if info_only:
                return

            if not brand:
                print("需要 --brand（识别结果请人工确认品牌后传入）")
                return
            brand = {"LAIFE": "LAIFE"}.get(brand, brand)

            date = datetime.date.today().isoformat()
            out_dir = os.path.join(RAW_DIR, date)
            os.makedirs(out_dir, exist_ok=True)
            raw_path = os.path.join(out_dir, f"{sec_uid[:20]}.jsonl")

            crawled, added, skipped, since_long_stop = 0, 0, 0, 0
            max_cursor, has_more = "", 1
            con = sqlite3.connect(DB)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rawf = open(raw_path, "a", encoding="utf-8")

            while has_more == 1 and (max_posts <= 0 or crawled < max_posts):
                res = await client.get_user_aweme_posts(sec_uid, max_cursor)
                has_more = (res or {}).get("has_more", 0)
                max_cursor = str((res or {}).get("max_cursor") or "")
                aweme_list = (res or {}).get("aweme_list") or []
                if not aweme_list:
                    break
                for a in aweme_list:
                    crawled += 1
                    rawf.write(json.dumps(a, ensure_ascii=False) + "\n")
                    vid = a.get("aweme_id", "")
                    author = (a.get("author") or {})
                    desc = (a.get("desc") or "").strip()
                    video = (a.get("video") or {})
                    cover = ""
                    try:
                        cover = video.get("cover", {}).get("url_list", [""])[0]
                    except Exception:
                        pass
                    vurl = ""
                    try:
                        vurl = video.get("play_addr", {}).get("url_list", [""])[0]
                    except Exception:
                        pass
                    media = {}
                    if cover.startswith("http"):
                        media["cover"] = cover
                    if vurl.startswith("http"):
                        media["video"] = vurl
                    stats = (a.get("statistics") or {})
                    metrics = {"likes": to_int(stats.get("digg_count")),
                               "collects": to_int(stats.get("collect_count")),
                               "comments": to_int(stats.get("comment_count")),
                               "shares": to_int(stats.get("share_count"))}
                    cur = con.execute(
                        """INSERT OR IGNORE INTO content
                           (brand, platform, content_type, title, body, author, author_id,
                            publish_time, collect_time, url, metrics_json, tags, raw_path,
                            text_hash, media_json, verified, verify_note)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'official:账号实名归属')""",
                        (brand, "douyin", "official_video",
                         (desc or "(无文案)")[:120], desc,
                         author.get("nickname") or nickname,
                         author.get("uid") or sec_uid,
                         ms_to_str(a.get("create_time")), now,
                         f"https://www.douyin.com/video/{vid}",
                         json.dumps(metrics, ensure_ascii=False),
                         "official",
                         raw_path, f"douyin:{vid}",
                         json.dumps(media, ensure_ascii=False) if media else None))
                    if cur.rowcount:
                        added += 1
                    else:
                        skipped += 1
                    # 官号进 creator 表（profile 用 web 页面路径）
                    con.execute(
                        """INSERT INTO creator
                             (platform, creator_id, nickname, profile_url, note_count,
                              total_likes, total_collects, total_comments,
                              first_publish, last_publish, brands, updated_at)
                           VALUES ('douyin',?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(platform, creator_id) DO UPDATE SET
                             note_count = note_count + 1,
                             updated_at = excluded.updated_at""",
                        (author.get("uid") or sec_uid, nickname,
                         f"https://www.douyin.com/user/{sec_uid}",
                         1, metrics["likes"], metrics["collects"], metrics["comments"],
                         ms_to_str(a.get("create_time")), ms_to_str(a.get("create_time")),
                         brand, now))
                rawf.flush()
                con.commit()
                print(f"  已爬 {crawled} 条（新增 {added}，重复 {skipped}），cursor={max_cursor[:12]}…", flush=True)
                since_long_stop += len(aweme_list)
                if has_more == 1:
                    if since_long_stop >= 30:
                        wait = random.uniform(15, 30)
                        since_long_stop = 0
                    else:
                        wait = random.uniform(2, 5)
                    await asyncio.sleep(wait)
            rawf.close()
            con.commit()
            print(f"完成：共 {crawled} 条，新增 {added}，重复跳过 {skipped} ｜ 原始层 {raw_path}")
            con.close()
        finally:
            await ctx.close()


async def run_batch(items, min_works=1):
    """批量模式：一个浏览器会话内逐个「识别→(作品数>阈值则)爬取」。
    items: [{uid, brand, max_posts}]"""
    sys.path.insert(0, MC)
    from playwright.async_api import async_playwright
    from media_platform.douyin.client import DouYinClient

    clear_profile_locks()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=False, user_agent=UA,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for attempt in range(3):
                try:
                    await page.goto("https://www.douyin.com/", timeout=45000,
                                    wait_until="domcontentloaded")
                    break
                except Exception as e:
                    print(f"[warn] 首页加载失败({attempt + 1}/3)：{e}")
                    await page.wait_for_timeout(3000)
            await page.wait_for_timeout(5000)
            client = DouYinClient(headers={"User-Agent": UA,
                                           "Referer": "https://www.douyin.com/"},
                                  playwright_page=page, cookie_dict={})
            await client.update_cookies(ctx)
            con = sqlite3.connect(DB)
            date = datetime.date.today().isoformat()
            out_dir = os.path.join(RAW_DIR, date)
            os.makedirs(out_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for it in items:
                uid, brand, max_posts = it["uid"], it["brand"], it.get("max_posts", 0)
                print(f"\n===== [{brand}] {uid[:24]}… =====", flush=True)
                try:
                    info = await client.get_user_info(uid)
                except Exception as e:
                    print(f"  [error] 识别失败：{e}")
                    continue
                user = (info or {}).get("user") or {}
                works = int(user.get("aweme_count") or 0)
                print(f"  {user.get('nickname')} ｜ 粉丝={user.get('follower_count')} ｜ 作品={works} ｜ 认证={user.get('enterprise_verify_reason') or user.get('custom_verify') or '-'}", flush=True)
                if works < min_works:
                    print("  跳过（作品数不足）")
                    continue
                # 爬取
                raw_path = os.path.join(out_dir, f"{uid[:20]}.jsonl")
                rawf = open(raw_path, "a", encoding="utf-8")
                crawled = added = since_long = 0
                max_cursor, has_more = "", 1
                while has_more == 1 and (max_posts <= 0 or crawled < max_posts):
                    try:
                        res = await client.get_user_aweme_posts(uid, max_cursor)
                    except Exception as e:
                        print(f"  [error] 翻页失败：{e}")
                        break
                    has_more = (res or {}).get("has_more", 0)
                    max_cursor = str((res or {}).get("max_cursor") or "")
                    aweme_list = (res or {}).get("aweme_list") or []
                    if not aweme_list:
                        break
                    for a in aweme_list:
                        crawled += 1
                        rawf.write(json.dumps(a, ensure_ascii=False) + "\n")
                        vid = a.get("aweme_id", "")
                        author = (a.get("author") or {})
                        desc = (a.get("desc") or "").strip()
                        video = (a.get("video") or {})
                        media = {}
                        try:
                            c = video.get("cover", {}).get("url_list", [""])[0]
                            if c.startswith("http"):
                                media["cover"] = c
                        except Exception:
                            pass
                        try:
                            v = video.get("play_addr", {}).get("url_list", [""])[0]
                            if v.startswith("http"):
                                media["video"] = v
                        except Exception:
                            pass
                        stats = (a.get("statistics") or {})
                        metrics = {"likes": to_int(stats.get("digg_count")),
                                   "collects": to_int(stats.get("collect_count")),
                                   "comments": to_int(stats.get("comment_count")),
                                   "shares": to_int(stats.get("share_count"))}
                        cur = con.execute(
                            """INSERT OR IGNORE INTO content
                               (brand, platform, content_type, title, body, author, author_id,
                                publish_time, collect_time, url, metrics_json, tags, raw_path,
                                text_hash, media_json, verified, verify_note)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'official:账号实名归属')""",
                            (brand, "douyin", "official_video",
                             (desc or "(无文案)")[:120], desc,
                             author.get("nickname") or user.get("nickname") or "",
                             author.get("uid") or uid,
                             ms_to_str(a.get("create_time")), now,
                             f"https://www.douyin.com/video/{vid}",
                             json.dumps(metrics, ensure_ascii=False),
                             "official", raw_path, f"douyin:{vid}",
                             json.dumps(media, ensure_ascii=False) if media else None))
                        if cur.rowcount:
                            added += 1
                        con.execute(
                            """INSERT INTO creator
                                 (platform, creator_id, nickname, profile_url, note_count,
                                  total_likes, total_collects, total_comments,
                                  first_publish, last_publish, brands, updated_at)
                               VALUES ('douyin',?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(platform, creator_id) DO UPDATE SET
                                 note_count = note_count + 1,
                                 updated_at = excluded.updated_at""",
                            (author.get("uid") or uid, user.get("nickname") or "",
                             f"https://www.douyin.com/user/{uid}",
                             1, metrics["likes"], metrics["collects"], metrics["comments"],
                             ms_to_str(a.get("create_time")), ms_to_str(a.get("create_time")),
                             brand, now))
                    rawf.flush()
                    con.commit()
                    since_long += len(aweme_list)
                    print(f"  已爬 {crawled}（新增 {added}）", flush=True)
                    if has_more == 1:
                        wait = random.uniform(15, 30) if since_long >= 30 else random.uniform(2, 5)
                        if since_long >= 30:
                            since_long = 0
                        await asyncio.sleep(wait)
                rawf.close()
                con.commit()
                print(f"  √ 完成：{crawled} 条，新增 {added} ｜ {raw_path}", flush=True)
            con.close()
        finally:
            await ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--info", metavar="SEC_UID", help="只识别账号身份，不爬内容")
    ap.add_argument("--crawl", metavar="SEC_UID", help="爬取该账号全部作品")
    ap.add_argument("--brand", help="品牌规范名（爬取必填）")
    ap.add_argument("--max-posts", type=int, default=0, help="最多爬 N 条，0=全量")
    ap.add_argument("--batch", metavar="JSON", help="批量：[{uid,brand,max_posts}]，识别+爬取一体")
    ap.add_argument("--min-works", type=int, default=1)
    a = ap.parse_args()
    if a.info:
        asyncio.run(run(a.info, None, 0, True))
    elif a.crawl:
        asyncio.run(run(a.crawl, a.brand, a.max_posts, False))
    elif a.batch:
        items = json.load(open(a.batch, encoding="utf-8"))
        if isinstance(items, dict):
            items = items.get("accounts") or items.get("items") or []
        asyncio.run(run_batch(items, a.min_works))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
