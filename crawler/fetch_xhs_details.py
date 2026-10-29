#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书详情补全 + 原文归档（网络解封后运行）。

补齐归档里缺失的字段：
  正文 desc / 图片列表 / 话题标签 / 互动数 / IP属地 / 分享数
  以及：
  ① 评论（口碑分析的核心）
  ② 笔记页 HTML 存档（真正意义上的「还原原文页面」）
  ③ 图片下载到本地（CDN 直链会过期）

断点续跑：已有 desc 的笔记自动跳过，随时可重跑。
风控退避：遇到 461/验证码自动拉长间隔并重试，不中断整轮。

用法：
  tools/MediaCrawler/.venv/bin/python crawler/fetch_xhs_details.py --limit 50
  tools/MediaCrawler/.venv/bin/python crawler/fetch_xhs_details.py --comments --limit 200
  tools/MediaCrawler/.venv/bin/python crawler/fetch_xhs_details.py --html --limit 20
"""
import argparse
import asyncio
import datetime
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW_DIR = os.path.join(ROOT, "data", "xhs_archive", "raw")
MEDIA_DIR = os.path.join(ROOT, "data", "xhs_archive", "media")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")

sys.path.insert(0, MC)


def load_pending(limit, only_missing=True, ids=None, brands=None):
    con = sqlite3.connect(DB)
    params = []
    q = ("SELECT note_id, title, brand, xsec_token, note_url FROM xhs_note "
         "WHERE xsec_token != '' ")
    if brands:
        q += "AND brand IN (%s) " % ",".join("?" * len(brands))
        params += brands
    if ids:
        # 定点补抓：只处理指定 note_id（与补正文的断点逻辑解耦）
        ph = ",".join("?" * len(ids))
        q += f"AND note_id IN ({ph}) "
        params += ids
    if only_missing:
        q += "AND (body IS NULL OR body = '' OR images_json IS NULL OR images_json = '[]') "
    q += "ORDER BY publish_time DESC LIMIT ?"
    params.append(limit)
    rows = con.execute(q, params).fetchall()
    con.close()
    return rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--comments", action="store_true", help="同时抓评论")
    ap.add_argument("--html", action="store_true", help="同时保存笔记页 HTML")
    ap.add_argument("--media", action="store_true", help="下载图片到本地")
    ap.add_argument("--sleep", type=float, default=2.5)
    ap.add_argument("--all", action="store_true", help="不只补缺失，全部刷新")
    ap.add_argument("--ids", type=str, default="",
                    help="逗号分隔的 note_id 列表：只补这几条（定点救回无正文笔记）")
    ap.add_argument("--video", action="store_true", help="同时下载视频到本地（ASR 用）")
    ap.add_argument("--brand", type=str, default="", help="逗号分隔品牌，只补这些品牌")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)

    import config
    config.KEYWORDS = "MitoQ"
    config.CRAWLER_MAX_NOTES_COUNT = 20
    config.CRAWLER_TYPE = "search"
    config.ENABLE_GET_COMMENTS = args.comments
    from playwright.async_api import async_playwright
    from media_platform.xhs.client import XiaoHongShuClient

    rows = load_pending(args.limit, only_missing=not args.all,
                        ids=[x.strip() for x in args.ids.split(",") if x.strip()],
                        brands=[b.strip() for b in args.brand.split(",") if b.strip()] or None)
    print(f"待补全 {len(rows)} 条", flush=True)
    if not rows:
        return

    ok = fail = 0
    blocked_streak = 0
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=True, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-gpu", "--disable-dev-shm-usage"])
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pg.goto("https://www.xiaohongshu.com/explore",
                     wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(3000)
        cookies = await ctx.cookies()
        cd = {c["name"]: c["value"] for c in cookies}
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in cd.items()),
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Origin": "https://www.xiaohongshu.com",
            "Referer": "https://www.xiaohongshu.com/",
            "Content-Type": "application/json;charset=UTF-8",
        }
        client = XiaoHongShuClient(headers=headers, playwright_page=pg, cookie_dict=cd)
        con = sqlite3.connect(DB)

        for i, (nid, title, brand, tok, url) in enumerate(rows, 1):
            if blocked_streak >= 8:
                print("  ⚠️ 连续风控，暂停本轮（下次自动续跑）", flush=True)
                break
            try:
                d = await client.get_note_by_id(nid, "pc_search", tok)
            except Exception as e:
                msg = str(e)
                if "461" in msg or "CAPTCHA" in msg:
                    blocked_streak += 1
                    wait = min(120, 10 * blocked_streak)
                    print(f"  [{i}/{len(rows)}] 风控，等待 {wait}s …", flush=True)
                    await asyncio.sleep(wait)
                    continue
                fail += 1
                print(f"  [{i}/{len(rows)}] 失败 {type(e).__name__}", flush=True)
                await asyncio.sleep(args.sleep * 2)
                continue

            blocked_streak = 0
            ok += 1
            # 原始 JSON 存档（页面还原的权威依据）
            with open(os.path.join(RAW_DIR, f"{nid}.json"), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)

            imgs = d.get("image_list") or []
            imgs = [x if isinstance(x, str) else (x.get("url_default") or x.get("url") or "")
                    for x in imgs] if not isinstance(imgs, str) else imgs.split(",")
            imgs = [u for u in imgs if u]
            # 视频直链（详情 JSON: video.media.video_streams[].master_url）
            vurl = ""
            try:
                from media_platform.xhs.media import extract_video_urls
                vus = extract_video_urls(d)
                vurl = vus[0] if vus else ""
            except Exception:
                vurl = ""
            tags = d.get("tag_list") or []
            tags = [t.get("name") if isinstance(t, dict) else str(t) for t in tags]
            metrics = {"likes": d.get("liked_count"), "collects": d.get("collected_count"),
                       "comments": d.get("comment_count"), "shares": d.get("share_count")}
            user = d.get("user") or {}

            con.execute("""UPDATE xhs_note SET title=COALESCE(NULLIF(title,''),?), body=?,
                    images_json=?, tags_json=?, video_url=?,
                    likes=?, collects=?, comments=?, shares=?,
                    author=COALESCE(NULLIF(author,''),?), author_id=COALESCE(NULLIF(author_id,''),?),
                    publish_time=COALESCE(publish_time,?), updated_at=?
                    WHERE note_id=?""",
                (title or d.get("title"), d.get("desc") or "",
                 json.dumps(imgs, ensure_ascii=False), json.dumps(tags, ensure_ascii=False),
                 vurl,
                 metrics["likes"] or 0, metrics["collects"] or 0, metrics["comments"] or 0,
                 metrics["shares"] or 0, user.get("nickname"), user.get("user_id"),
                 d.get("time"), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), nid))
            # 同步回 content（品牌维度）；media_json 合并 images+video，不覆盖已有
            try:
                _old_mj = json.loads(con.execute(
                    "SELECT media_json FROM content WHERE text_hash=?",
                    (f"xhs:{nid}",)).fetchone()[0] or "{}")
            except Exception:
                _old_mj = {}
            _new_mj = {**_old_mj, "images": imgs or _old_mj.get("images") or []}
            if vurl:
                _new_mj["video"] = vurl
            con.execute("""UPDATE content SET body=COALESCE(NULLIF(body,''),?),
                    media_json=?, tags=COALESCE(NULLIF(tags,''),?),
                    metrics_json=?, publish_time=COALESCE(publish_time,?),
                    author=COALESCE(NULLIF(author,''),?), author_id=COALESCE(NULLIF(author_id,''),?)
                    WHERE text_hash=?""",
                (d.get("desc") or "",
                 json.dumps(_new_mj, ensure_ascii=False) if _new_mj else None,
                 ",".join(tags), json.dumps(metrics, ensure_ascii=False),
                 d.get("time"), user.get("nickname"), user.get("user_id"), f"xhs:{nid}"))

            # 评论
            if args.comments:
                try:
                    cs = await client.get_note_comments(nid, xsec_token=tok)
                    for c in (cs or []):
                        u = c.get("user_info") or {}
                        con.execute("""INSERT OR IGNORE INTO comment
                            (content_id, body, like_count, publish_time, text_hash)
                            VALUES ((SELECT id FROM content WHERE text_hash=?),?,?,?,?)""",
                            (f"xhs:{nid}", (c.get("content") or "")[:2000], c.get("like_count") or 0,
                             None, f"xhs_c:{c.get('id')}"))
                except Exception as e:
                    print(f"    评论失败: {type(e).__name__}", flush=True)

            # 原文页面 HTML 存档
            if args.html:
                try:
                    await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await pg.wait_for_timeout(3500)
                    page_html = await pg.content()
                    with open(os.path.join(RAW_DIR, f"{nid}.html"), "w", encoding="utf-8") as f:
                        f.write(page_html)
                    if "安全限制" in page_html or "访问频繁" in page_html:
                        print(f"    [{i}] 页面被安全限制拦截", flush=True)
                except Exception as e:
                    print(f"    页面存档失败: {type(e).__name__}", flush=True)

            # 图片下载
            if args.media:
                for j, u in enumerate(imgs[:6]):
                    try:
                        req = await pg.request.get(u, headers={"Referer": "https://www.xiaohongshu.com/"})
                        if req.ok:
                            body = await req.body()
                            with open(os.path.join(MEDIA_DIR, f"{nid}_{j}.jpg"), "wb") as f:
                                f.write(body)
                    except Exception:
                        pass

            # 视频下载（CDN 直链几小时就过期，必须现采现下）
            if args.video and vurl and not os.path.exists(os.path.join(MEDIA_DIR, f"{nid}.mp4")):
                try:
                    req = await pg.request.get(vurl, headers={"Referer": "https://www.xiaohongshu.com/"})
                    if req.ok:
                        body = await req.body()
                        if len(body) > 100_000:  # 小于 100KB 视为异常占位
                            with open(os.path.join(MEDIA_DIR, f"{nid}.mp4"), "wb") as f:
                                f.write(body)
                            print(f"    视频 {len(body)//1024//1024}MB 已落盘", flush=True)
                except Exception as e:
                    print(f"    视频下载失败: {type(e).__name__}", flush=True)

            con.commit()
            print(f"  [{i}/{len(rows)}] ✅ {nid} 正文{len(d.get('desc') or '')} 图{len(imgs)} 标签{len(tags)}",
                  flush=True)
            await asyncio.sleep(args.sleep)

        con.commit()
        con.close()
        await ctx.close()
    print(f"=== 补全结束：成功 {ok}，失败 {fail} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
