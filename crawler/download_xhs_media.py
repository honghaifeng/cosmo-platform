#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载小红书视频/图片到本地（离线可看）。

背景：小红书 CDN 直链带签名、会过期，页面上的图/视频随时可能失效。
要在本地保留原貌，就必须把媒体文件落盘。

要点：
  - 用浏览器会话的 request（自带 cookie + referer），比裸 urllib 成功率高
  - 视频按「品牌相关优先」下载；图片全量尝试
  - 断点续跑：已存在的文件跳过
  - 失败记录到 manifest.json，便于后续重试

用法：
  tools/MediaCrawler/.venv/bin/python crawler/download_xhs_media.py --videos --brand-only
  tools/MediaCrawler/.venv/bin/python crawler/download_xhs_media.py --images --brand-only --max-images 6
  tools/MediaCrawler/.venv/bin/python crawler/download_xhs_media.py --videos --all --dry-run
"""
import argparse
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
ARCHIVE = os.path.join(ROOT, "data", "xhs_archive")
MEDIA = os.path.join(ARCHIVE, "media")
NOTES = os.path.join(ARCHIVE, "notes.jsonl")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")
MANIFEST = os.path.join(ARCHIVE, "media_manifest.json")


def load_notes():
    rows = []
    for line in open(NOTES, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", action="store_true")
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--brand-only", action="store_true", help="只下载已归属品牌的")
    ap.add_argument("--all", action="store_true", help="不限品牌（含未归属）")
    ap.add_argument("--max-images", type=int, default=6, help="每条笔记最多下几张图")
    ap.add_argument("--max-size", type=int, default=80, help="单文件大小上限 MB")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.videos and not args.images:
        args.videos = True
    os.makedirs(MEDIA, exist_ok=True)
    notes = load_notes()
    if args.brand_only:
        notes = [n for n in notes if n["brand_mention"]]

    jobs = []
    if args.videos:
        for n in notes:
            vu = n.get("video_url")
            if not vu:
                continue
            # video_url 可能是逗号分隔的多段（同一视频的不同清晰度版本）。
            # 每段都是完整可播的 mp4（各有 ftyp/moov/mdat），
            # 但整串请求会 403 —— 必须拆开逐个请求，取第一个成功的。
            segs = [x.strip() for x in vu.split(",") if x.strip().startswith("http")]
            if not segs:
                continue
            jobs.append(("video", n["note_id"], segs[0], n.get("title", ""),
                         {"all_segments": segs}))
    if args.images:
        for n in notes:
            for i, u in enumerate((n.get("images") or [])[: args.max_images]):
                jobs.append(("image", f'{n["note_id"]}_{i}', u, n.get("title", ""), {}))
    print(f"待下载 {len(jobs)} 个文件（视频 {sum(1 for j in jobs if j[0]=='video')}，"
          f"图片 {sum(1 for j in jobs if j[0]=='image')}）")

    manifest = {}
    if os.path.exists(MANIFEST):
        try:
            manifest = json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            manifest = {}
    done = set(manifest.get("done", []))

    if args.dry_run:
        for kind, key, url, title in jobs[:15]:
            print(f"  [{kind}] {key} {title[:24]}")
        return

    from playwright.sync_api import sync_playwright
    ok = fail = skip = 0
    total_bytes = 0
    with sync_playwright() as p:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="zh-CN",
                  viewport={"width": 1280, "height": 900},
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                        "--disable-gpu", "--disable-dev-shm-usage"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kw)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto("https://www.xiaohongshu.com/explore",
                    wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(2500)
        except Exception:
            pass

        for idx, (kind, key, url, title, extra) in enumerate(jobs, 1):
            if key in done:
                skip += 1
                continue
            ext = "mp4" if kind == "video" else "jpg"
            path = os.path.join(MEDIA, f"{key}.{ext}")
            if os.path.exists(path) and os.path.getsize(path) > 1024:
                skip += 1
                done.add(key)
                continue
            try:
                # video_url 可能是逗号分隔的多段（同一视频的不同清晰度版本），
                # 每段都是完整可播的 mp4；整串请求会 403，必须逐段试。
                candidates = extra.get("all_segments") or [url]
                body = None
                last_status = 0
                for cand in candidates:
                    try:
                        resp = pg.request.get(cand, headers={"Referer": "https://www.xiaohongshu.com/"})
                        last_status = resp.status
                        if resp.ok:
                            body = resp.body()
                            break
                    except Exception:
                        last_status = 0
                if body is None:
                    fail += 1
                    # 403 常见两种：视频是「整串多段 URL 没拆」，图片是「URL 已过期」
                    # （图片 URL 路径里的 12 位时间戳就是生成时间，TTL 约 24h）
                    reason = "url_expired" if kind == "image" else "segments_all_failed"
                    manifest.setdefault("failed", {})[key] = {
                        "url": url, "status": last_status, "reason": reason,
                        "hint": ("图片 CDN URL 已过期，只能靠现采现下补齐"
                                 if kind == "image" else "所有分段都失败")}
                    continue
                if len(body) > args.max_size * 1024 * 1024:
                    fail += 1
                    manifest.setdefault("failed", {})[key] = {"url": url, "reason": "too_large"}
                    continue
                with open(path, "wb") as f:
                    f.write(body)
                ok += 1
                total_bytes += len(body)
                done.add(key)
                manifest["done"] = sorted(done)
                if idx % 10 == 0 or idx == len(jobs):
                    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    print(f"  进度 {idx}/{len(jobs)} 成功 {ok} 失败 {fail} 跳过 {skip} "
                          f"已存 {total_bytes/1024/1024:.0f}MB", flush=True)
                time.sleep(0.6)          # 轻微限速，别把 CDN 也打挂
            except Exception as e:
                fail += 1
                manifest.setdefault("failed", {})[key] = {"url": url, "err": str(e)[:120]}
        ctx.close()

    manifest["done"] = sorted(done)
    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"=== 完成：成功 {ok}，失败 {fail}，跳过 {skip}，共 {total_bytes/1024/1024:.0f}MB → {MEDIA}")


if __name__ == "__main__":
    main()
