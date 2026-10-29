#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日补抓：把三块缺口用「低频、错峰」的方式补上。

缺口与对策：
  1. 场景词（熬夜后怎么恢复/皮肤松弛暗沉怎么调理…）—— 白天批量跑会被风控，
     放到每天 03:30 单独跑，只取前 60 条/词
  2. 创作者主页全量 —— 官方号与头部达人的历史内容，滚动加载抓
  3. 百度 —— 多关键词 × 多页，浏览器模式（带登录态复用）

用法（由 daily_update.sh 调用）：
  python3 crawler/daily_backfill.py --part scene|creator|baidu|all
"""
import argparse
import os
import json
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
UV = os.path.expanduser("~/.local/bin/uv")
MPY = os.path.join(MC, ".venv", "bin", "python")
PY = "/Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
LOGDIR = os.path.join(ROOT, "logs")

SCENE_WORDS = ("熬夜后怎么恢复,入睡难怎么调理,睡眠浅怎么办,下午犯困怎么办,"
               "精力差吃什么,皮肤松弛暗沉怎么调理,昼夜节律 调理,分时营养")


def run(cmd, log, cwd=None, env=None):
    with open(log, "a", encoding="utf-8") as fh:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT)
    return p.returncode


def kill_browser():
    prof = os.path.join(MC, "browser_data", "xhs_user_data_dir")
    subprocess.run(["pkill", "-f", f"user-data-dir={prof}"], capture_output=True)
    time.sleep(2)
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(prof, n)
        try:
            if os.path.exists(p) or os.path.islink(p):
                os.unlink(p)
        except OSError:
            pass


def part_scene(ts):
    log = os.path.join(LOGDIR, f"backfill_scene_{ts}.log")
    env = dict(os.environ)
    env.update(CB_KEYWORDS=SCENE_WORDS, CB_MAX_NOTES="60", CB_COMMENTS="0",
               CB_BATCH="8", CB_SLEEP="12", CB_IDENTITY="1", CB_QR_ATTEMPTS="180")
    kill_browser()
    rc = run([UV, "run", "python", "main.py", "--platform", "xhs", "--lt", "qrcode", "--type", "search"],
             log, cwd=MC, env=env)
    n = sum(1 for l in open(log, encoding="utf-8", errors="ignore") if "update_xhs_note" in l) \
        if os.path.exists(log) else 0
    print(f"[scene] rc={rc} 抓到 {n} 条", flush=True)
    if n:
        f = os.path.join(MC, "data", "xhs", "jsonl", f"search_contents_{ts}.jsonl")
        if os.path.exists(f):
            run([PY, os.path.join(ROOT, "crawler", "mediacrawler_ingest.py"), "xhs", f,
                 f.replace("search_contents_", "search_comments_")], log + ".ingest")
    return n


def part_creator(ts):
    log = os.path.join(LOGDIR, f"backfill_creator_{ts}.log")
    kill_browser()
    rc = run([MPY, os.path.join(ROOT, "crawler", "crawl_creator_scroll.py"),
              "--run", "--top", "3", "--max-scroll", "25"], log)
    print(f"[creator] rc={rc}", flush=True)
    return rc


def part_human(ts):
    """拟人采集补正文/昵称：每晚小批量，节奏慢，遇风控即停（不硬冲）。"""
    log = os.path.join(LOGDIR, f"backfill_human_{ts}.log")
    # 冷却未到就跳过
    st_path = os.path.join(ROOT, "data", "xhs_archive", "human_state.json")
    if os.path.exists(st_path):
        try:
            st = json.load(open(st_path, encoding="utf-8"))
            if st.get("blocked_until", 0) > time.time():
                print(f"[human] 仍在冷却（还剩 {int(st['blocked_until'] - time.time()) // 60} 分钟），跳过")
                return 0
        except Exception:
            pass
    # 拟人采集 + 现采现下（图片 CDN URL 约 24h 过期，当场落盘是唯一可靠方式）
    # 拟人采集 + 现采现下（图片 CDN URL 约 24h 过期，当场落盘是唯一可靠方式）
    # 采集器内置 grab_media()：每抓到一条笔记就把图片/视频写进 data/xhs_archive/media
    rc = run([MPY, os.path.join(ROOT, "crawler", "xhs_human_harvest.py"),
              "--per-keyword", "3", "--total", "30"], log)
    print(f"[human] rc={rc}", flush=True)
    run([PY, os.path.join(ROOT, "crawler", "build_xhs_archive.py"), "--build"], log)
    # 输出当日完整度体检
    rc2 = run([PY, os.path.join(ROOT, "crawler", "xhs_health_check.py")], log)
    print(f"[health] rc={rc2}", flush=True)
    # 正确性校验（有 FAIL 要能被发现，不能只看完整度）
    rc3 = run([PY, os.path.join(ROOT, "crawler", "verify_xhs_data.py"), "--strict"], log)
    print(f"[verify] rc={rc3}（非 0 表示有校验失败，需人工看）", flush=True)
    # 对外数据边界守卫：确认未校验数据一条都流不出去
    rc4 = run([PY, os.path.join(ROOT, "crawler", "check_data_boundary.py")], log)
    print(f"[boundary] rc={rc4}（非 0 表示有数据越界，需立刻修）", flush=True)
    return rc


def part_details(ts):
    """详情补全：正文/图片/标签/评论（风控解除后自动跑，断点续跑）。"""
    log = os.path.join(LOGDIR, f"backfill_details_{ts}.log")
    kill_browser()
    # 针对性采集：小配额、随机间隔、遇风控即停（不再用批量接口硬冲）
    rc = run([MPY, os.path.join(ROOT, "crawler", "xhs_targeted.py"),
              "--run", "--notes-only", "--total", "25",
              "--min-delay", "10", "--max-delay", "26"], log)
    print(f"[details] rc={rc}", flush=True)
    # 补完重建归档与本地页面
    rc2 = run([PY, os.path.join(ROOT, "crawler", "build_xhs_archive.py"), "--build"], log)
    print(f"[archive] rc={rc2}", flush=True)
    return rc


def part_official_detail(ts):
    """官号笔记详情补全：content 表里 creator_scroll 入库的官号笔记只有
    标题/互动/链接，缺正文与发布时间（H2/H3 卡点）。走拟人页面通道
    （fetch_detail_browser：持久化登录态 + 随机间隔 + 遇风控整体停），
    每晚一小批，断点续跑（fetch_skip 记录失败，>=2 次自动跳过）。"""
    log = os.path.join(LOGDIR, f"backfill_official_detail_{ts}.log")
    kill_browser()
    rc = run([MPY, os.path.join(ROOT, "crawler", "fetch_detail_browser.py"),
              "--platform", "xhs", "--limit", "80", "--media"], log)
    print(f"[official_detail] rc={rc}", flush=True)
    return rc


def part_baidu(ts):
    log = os.path.join(LOGDIR, f"backfill_baidu_{ts}.log")
    prof = os.path.join(ROOT, "data", "browser_profiles", "baidu_daily")
    os.makedirs(prof, exist_ok=True)
    rc = run([MPY, os.path.join(ROOT, "crawler", "baidu_serp_full.py"), "--browser",
              "--profile", prof, "--pages", "5", "--sleep", "9"], log)
    print(f"[baidu] rc={rc}", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all",
                    choices=["scene", "human", "creator", "details", "official_detail",
                             "baidu", "all"])
    args = ap.parse_args()
    ts = time.strftime("%Y-%m-%d")
    parts = (["scene", "human", "creator", "details", "official_detail", "baidu"]
             if args.part == "all" else [args.part])
    for p in parts:
        try:
            if p == "scene":
                part_scene(ts)
            elif p == "creator":
                part_creator(ts)
            elif p == "human":
                part_human(ts)
            elif p == "details":
                part_details(ts)
            elif p == "official_detail":
                part_official_detail(ts)
            else:
                part_baidu(ts)
        except Exception as e:
            print(f"[{p}] 失败: {type(e).__name__}: {e}", flush=True)
        time.sleep(20)   # 错峰，避免连续请求触发风控
    print("=== 补抓结束 ===", flush=True)


if __name__ == "__main__":
    main()
