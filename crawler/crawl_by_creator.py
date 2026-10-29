#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按达人主页抓全量历史。

为什么需要：搜索每个关键词只给前 5 页（约 100 条），而且第 3 页起大量重复，
所以「搜关键词」永远抓不全某个品牌的真实内容量。真正拿全量要走创作者主页。

流程：
  1. 从批次日志解析「达人 ID → 用户 xsec_token」（搜索响应原文里带）
  2. 从 creator 表挑目标账号（各品牌官方号 + 发帖量最高的号）
  3. 写入 MediaCrawler 的 XHS_CREATOR_ID_LIST，跑 --type creator
  4. 入库

用法：
  python3 crawler/crawl_by_creator.py --list
  python3 crawler/crawl_by_creator.py --top 6         # 每个品牌取前 6 个号
  python3 crawler/crawl_by_creator.py --official-only  # 只抓官方号
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
UV = os.path.expanduser("~/.local/bin/uv")
PY = "/Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
LOGDIR = os.path.join(ROOT, "logs")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
TOKEN_JSON = os.path.join(ROOT, "data", "creator_tokens.json")

# 官方号昵称特征
OFFICIAL_PAT = re.compile(
    r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时|mitoq|granver|laife|乐梵|"
    r"端粒塔|w\+|赛乐瑞|塞乐瑞|cellerator|超.*官方|官方.*)", re.I)


def load_tokens():
    """从日志解析 达人ID → 用户 xsec_token。"""
    if os.path.exists(TOKEN_JSON):
        try:
            return json.load(open(TOKEN_JSON, encoding="utf-8"))
        except Exception:
            pass
    pat = re.compile(r"'user_id':\s*'([0-9a-f]{20,28})'[^}]*?'xsec_token':\s*'([^']+)'")
    m = {}
    for f in glob.glob(os.path.join(LOGDIR, "*.log")):
        try:
            s = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for uid, tok in pat.findall(s):
            m.setdefault(uid, tok)
    os.makedirs(os.path.dirname(TOKEN_JSON), exist_ok=True)
    json.dump(m, open(TOKEN_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def pick_targets(tokens, top, official_only):
    con = sqlite3.connect(DB)
    rows = con.execute(
        'SELECT creator_id, nickname, note_count, total_likes, brands FROM creator '
        "WHERE platform='xhs'").fetchall()
    con.close()
    by_brand = {}
    for cid, nick, n, likes, brands in rows:
        if cid not in tokens:
            continue
        for b in (brands or "").split(","):
            if not b:
                continue
            by_brand.setdefault(b, []).append((cid, nick, n, likes))
    targets = []
    for brand, lst in by_brand.items():
        lst.sort(key=lambda x: -(x[2] or 0))
        if official_only:
            lst = [x for x in lst if OFFICIAL_PAT.search(x[1] or "")] or lst[:1]
        else:
            off = [x for x in lst if OFFICIAL_PAT.search(x[1] or "")]
            lst = off + [x for x in lst if x not in off]
        for cid, nick, n, likes in lst[:top]:
            targets.append({
                "brand": brand, "creator_id": cid, "nickname": nick,
                "seen_notes": n, "likes": likes,
                "url": (f"https://www.xiaohongshu.com/user/profile/{cid}"
                        f"?xsec_token={tokens[cid]}&xsec_source=pc_search"),
            })
    return targets


def run_creator(targets, batch=4):
    """把目标写进 xhs_config.XHS_CREATOR_ID_LIST，分批跑 creator 模式。"""
    cfg = os.path.join(MC, "config", "xhs_config.py")
    orig = open(cfg, encoding="utf-8").read()
    try:
        for i in range(0, len(targets), batch):
            chunk = targets[i:i + batch]
            urls = ",\n".join(f'    "{t["url"]}"' for t in chunk)
            block = f'XHS_CREATOR_ID_LIST = [\n{urls}\n]'
            new = re.sub(r"XHS_CREATOR_ID_LIST\s*=\s*\[.*?\n\]", block, orig, flags=re.S)
            open(cfg, "w", encoding="utf-8").write(new)

            env = dict(os.environ)
            env["CB_IDENTITY"] = "1"
            env["CB_COMMENTS"] = "0"
            env["CB_SLEEP"] = "8"
            env["CB_BATCH"] = "8"
            log = os.path.join(LOGDIR, f"creator_batch{i//batch+1}.log")
            prof = os.path.join(MC, "browser_data", "xhs_user_data_dir")
            subprocess.run(["pkill", "-f", f"user-data-dir={prof}"], capture_output=True)
            time.sleep(2)
            with open(log, "w", encoding="utf-8") as fh:
                subprocess.run([UV, "run", "python", "main.py", "--platform", "xhs",
                                "--lt", "qrcode", "--type", "creator"],
                               cwd=MC, env=env, stdout=fh, stderr=subprocess.STDOUT)
            n = 0
            if os.path.exists(log):
                n = sum(1 for l in open(log, encoding="utf-8", errors="ignore")
                        if "update_xhs_note" in l)
            print(f"  第{i//batch+1} 批（{len(chunk)} 个号）抓到 {n} 条", flush=True)
            # 入库
            files = sorted(glob.glob(os.path.join(MC, "data", "xhs", "jsonl", "search_contents_*.jsonl")))
            if files:
                f = files[-1]
                subprocess.run([PY, os.path.join(ROOT, "crawler", "mediacrawler_ingest.py"),
                                "xhs", f, f.replace("search_contents_", "search_comments_")],
                               capture_output=True, text=True)
            time.sleep(20)
    finally:
        open(cfg, "w", encoding="utf-8").write(orig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--top", type=int, default=6, help="每个品牌取多少个号")
    ap.add_argument("--official-only", action="store_true")
    ap.add_argument("--batch", type=int, default=4)
    args = ap.parse_args()

    tokens = load_tokens()
    targets = pick_targets(tokens, args.top, args.official_only)
    print(f"可用达人 token {len(tokens)} 个，选中目标账号 {len(targets)} 个")
    for t in targets[:30]:
        flag = "官方" if OFFICIAL_PAT.search(t["nickname"] or "") else "达人"
        print(f"  [{flag}] {t['brand']:14s} {t['nickname'][:18]:20s} 已见 {t['seen_notes']:3d} 条  赞 {t['likes'] or 0:7d}")
    if args.list:
        return
    print("\n开始按主页抓取…", flush=True)
    run_creator(targets, args.batch)
    print("=== 创作者抓取完成 ===")


if __name__ == "__main__":
    main()