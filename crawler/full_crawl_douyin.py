#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音全量抓取驱动器：按 config/brands.json 的完整词表分批跑 MediaCrawler。

与 full_crawl_xhs.py 同一套思路，差异：
  - 抖音通道目前稳定（登录态有效、无验证码），可以连续跑；
  - 入库平台名必须是 douyin（写 dy 会产生同名但多一个 platform 值，历史上踩过）；
  - 已跑过的词记在 logs/dy_done.txt，中断后 --resume 直接续；
  - 每批跑完立即入库，成果不滞留 jsonl。

用法：
  python3 crawler/full_crawl_douyin.py --list
  python3 crawler/full_crawl_douyin.py --batch-size 6 --max-notes 15
  python3 crawler/full_crawl_douyin.py --resume
"""
import argparse
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
UV = os.path.expanduser("~/.local/bin/uv")
PY = "/usr/bin/python3"
LOGDIR = os.path.join(ROOT, "logs")
DONE = os.path.join(LOGDIR, "dy_done.txt")
TODAY_JSONL = os.path.join(MC, "data", "douyin", "jsonl", "search_contents_2026-10-23.jsonl")


def keywords_from_config():
    cfg = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))
    seen, out = set(), []
    for b in cfg["brands"]:
        ws = [b["name"]] + list(b.get("aliases", [])) + list(b.get("keywords", []))
        for g in (b.get("keyword_groups") or {}).values():
            ws += list(g)
        for w in ws:
            w = (w or "").strip()
            if w and w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
    return out


def run_batch(words, max_notes, log_name):
    env = dict(os.environ)
    env["CB_KEYWORDS"] = ",".join(words)
    env["CB_MAX_NOTES"] = str(max_notes)
    env["CB_COMMENTS"] = os.getenv("CB_COMMENTS", "0")
    env["CB_SLEEP"] = os.getenv("CB_SLEEP", "3")
    log = os.path.join(LOGDIR, log_name)
    with open(log, "a", encoding="utf-8") as fh:
        p = subprocess.run([UV, "run", "python", "main.py", "--platform", "dy",
                            "--lt", "qrcode", "--type", "search"],
                           cwd=MC, env=env, stdout=fh, stderr=subprocess.STDOUT)
    return p.returncode, log


def ingest():
    """平台名用 douyin；jsonl 当日文件不存在时安静跳过。"""
    if not os.path.exists(TODAY_JSONL):
        return
    comments = TODAY_JSONL.replace("search_contents_", "search_comments_")
    r = subprocess.run([PY, os.path.join(ROOT, "crawler", "mediacrawler_ingest.py"),
                        "douyin", TODAY_JSONL, comments], capture_output=True, text=True)
    for line in (r.stdout + r.stderr).splitlines():
        if "新增内容" in line:
            print("   ", line.strip(), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=6)
    ap.add_argument("--max-notes", type=int, default=15)
    ap.add_argument("--resume", action="store_true", help="跳过 logs/dy_done.txt 里已跑过的词")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    os.makedirs(LOGDIR, exist_ok=True)
    words = keywords_from_config()
    done = set()
    if args.resume and os.path.exists(DONE):
        done = {l.strip() for l in open(DONE, encoding="utf-8") if l.strip()}
        words = [w for w in words if w not in done]
    batches = [words[i:i + args.batch_size] for i in range(0, len(words), args.batch_size)]
    print(f"待跑词 {len(words)} 个，分 {len(batches)} 批（每批 {args.batch_size}，每词上限 {args.max_notes}）", flush=True)
    for i, b in enumerate(batches, 1):
        print(f" 第{i}批: {'、'.join(b)}", flush=True)
    if args.list:
        return

    for i, batch in enumerate(batches, 1):
        print(f"\n=== 第{i}/{len(batches)} 批：{'、'.join(batch)} ===", flush=True)
        rc, log = run_batch(batch, args.max_notes, "dy_batches.log")
        print(f"  rc={rc}", flush=True)
        ingest()
        with open(DONE, "a", encoding="utf-8") as fh:
            for w in batch:
                fh.write(w + "\n")
        if i < len(batches):
            time.sleep(20)
    print("=== 抖音全量批次结束 ===", flush=True)


if __name__ == "__main__":
    main()
