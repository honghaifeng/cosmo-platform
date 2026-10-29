#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书全量抓取驱动器：按 config/brands.json 的完整词表分批跑 MediaCrawler。

为什么需要它：
  - 全量词表有 40+ 个词（含品牌名、别名、产品词、评价词、平台分组词），
    一次全跑会被限流打断；
  - 每批之间留冷却，单批失败不阻塞后续；
  - 每批跑完自动入库 + 报告，避免成果滞留 jsonl。

用法：
  python3 crawler/full_crawl_xhs.py --list                 # 只看词表与分批计划
  python3 crawler/full_crawl_xhs.py --batch-size 3         # 跑前 3 批（默认全跑）
  python3 crawler/full_crawl_xhs.py --start-batch 4        # 从第 4 批继续
  python3 crawler/full_crawl_xhs.py --max-notes 100        # 每关键词上限
  python3 crawler/full_crawl_xhs.py --skip-done            # 跳过库内已有数据的品牌（仍跑词）
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
UV = os.path.expanduser("~/.local/bin/uv")
PY = "/Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
LOGDIR = os.path.join(ROOT, "logs")

# 词表优先级：品牌主体词最先跑（最关键），产品词/评价词随后，泛词最后
TIER = {
    "brand": ["COSMOBOUNTY", "Cosmo Bounty", "科诗博缇", "MitoQ", "GRANVER", "LAIFE", "乐梵",
              "端粒塔", "赛乐瑞", "赛乐瑞PRO", "Cellerator"],
    "product": ["黑白分时胶囊", "分时抗衰", "日活瓶", "夜修瓶", "节律营养", "NR 麦角硫因",
                "麦角硫因 抗衰", "奶蓟草胶囊", "姜黄素Pro", "舒心Pro", "吉利肝", "吉清肺",
                "吉力威", "红宝瓶", "乐悦泰", "乐轻肝", "NMN 25000", "精华倍莱", "Super-K"],
    "evaluate": ["COSMOBOUNTY 怎么样", "科诗博缇 怎么样", "黑白分时胶囊 怎么样", "节律营养 有用吗",
                 "MitoQ 怎么样", "MitoQ 测评", "MitoQ 智商税", "GRANVER 测评",
                 "LAIFE 测评", "LAIFE 智商税", "塞乐瑞 测评", "端粒塔 测评", "抗衰胶囊 测评 对比",
                 "NR 和 NMN 哪个好", "分时抗衰 哪个牌子好", "抗衰胶囊 成分党"],
    "scene": ["熬夜后怎么恢复", "入睡难 怎么调理", "睡眠浅 怎么办", "下午犯困怎么办",
              "精力差 吃什么", "皮肤松弛暗沉怎么调理", "昼夜节律 调理", "分时营养"],
}


def keywords_from_config():
    """brands.json 里的词，按 TIER 归类并去重。"""
    cfg = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))
    seen, buckets = set(), {k: [] for k in TIER}

    def put(tier, w):
        w = (w or "").strip()
        if not w or w.lower() in seen:
            return
        seen.add(w.lower())
        buckets[tier].append(w)

    for b in cfg["brands"]:
        own = b.get("is_own_brand")
        tier = "brand" if own else "brand"
        put(tier, b["name"])
        for w in b.get("aliases", []):
            put("brand" if own else "product", w)
        for w in b.get("keywords", []):
            put("evaluate", w)
        for g in (b.get("keyword_groups") or {}).values():
            for w in g:
                put("brand" if own else "product", w)
    # 补充人工整理的层级词
    for tier, ws in TIER.items():
        for w in ws:
            put(tier, w)
    return buckets


def exhausted_keywords(max_notes):
    """统计已抓过的关键词；已达上限的词不必再跑（平台返回重复，纯粹浪费请求）。"""
    import glob
    got = {}
    for f in glob.glob(os.path.join(MC, "data", "xhs", "jsonl", "search_contents_*.jsonl")):
        for line in open(f, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            kw, nid = d.get("source_keyword"), d.get("note_id")
            if not kw or not nid:
                continue
            got.setdefault(kw, set()).add(nid)
    return {kw for kw, ids in got.items() if len(ids) >= max_notes * 0.9}


def plan(buckets, batch_size):
    order = []
    for tier in ("brand", "product", "evaluate", "scene"):
        order += buckets[tier]
    return [order[i:i + batch_size] for i in range(0, len(order), batch_size)]


def run_batch(words, max_notes, log_name):
    env = dict(os.environ)
    env["CB_KEYWORDS"] = ",".join(words)
    env["CB_MAX_NOTES"] = str(max_notes)
    env["CB_COMMENTS"] = "0"          # 先抓内容，评论随后按批补
    env["CB_BATCH"] = "8"            # 详情小批，降低风控截断
    env["CB_SLEEP"] = "10"           # 放慢降限流
    env["CB_IDENTITY"] = "1"         # 保留真实达人 ID/昵称，供达人维度统计
    log = os.path.join(LOGDIR, log_name)
    # 清掉占用 profile 的残留浏览器
    prof = os.path.join(MC, "browser_data", "xhs_user_data_dir")
    subprocess.run(["pkill", "-f", f"user-data-dir={prof}"], capture_output=True)
    time.sleep(2)
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.run([UV, "run", "python", "main.py", "--platform", "xhs",
                            "--lt", "qrcode", "--type", "search"],
                           cwd=MC, env=env, stdout=fh, stderr=subprocess.STDOUT)
    n = 0
    if os.path.exists(log):
        n = sum(1 for line in open(log, encoding="utf-8", errors="ignore")
                if "update_xhs_note" in line)
    return p.returncode, n, log


def ingest(plat="xhs"):
    import glob
    notes = sorted(glob.glob(os.path.join(MC, "data", plat, "jsonl", f"search_contents_*.jsonl")))
    if not notes:
        return 0
    f = notes[-1]
    c = f.replace("search_contents_", "search_comments_")
    r = subprocess.run([PY, os.path.join(ROOT, "crawler", "mediacrawler_ingest.py"), plat, f, c],
                       capture_output=True, text=True)
    out = r.stdout + r.stderr
    for line in out.splitlines():
        if "新增内容" in line or "新增评论" in line:
            print("   ", line.strip(), flush=True)
    return 0 if r.returncode == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--batch-size", type=int, default=3)
    ap.add_argument("--start-batch", type=int, default=1)
    ap.add_argument("--max-notes", type=int, default=100)
    ap.add_argument("--skip-done", action="store_true")
    ap.add_argument("--skip-exhausted", action="store_true",
                    help="跳过已抓满上���的关键词")
    args = ap.parse_args()

    os.makedirs(LOGDIR, exist_ok=True)
    buckets = keywords_from_config()
    if args.skip_exhausted:
        done = exhausted_keywords(args.max_notes)
        if done:
            print(f"跳过已抓满的 {len(done)} 个词：{'、'.join(sorted(done))}")
        for tier in list(buckets):
            buckets[tier] = [w for w in buckets[tier] if w not in done]
    batches = plan(buckets, args.batch_size)
    total_kw = sum(len(v) for v in buckets.values())

    print(f"完整词表 {total_kw} 个，分 {len(batches)} 批（每批 {args.batch_size} 词，每词上限 {args.max_notes}）")
    for t, ws in buckets.items():
        print(f"  {t:9s} {len(ws):2d} 个：{'、'.join(ws[:8])}{'…' if len(ws) > 8 else ''}")
    print()
    for i, b in enumerate(batches, 1):
        mark = "→" if i >= args.start_batch else " "
        print(f" {mark} 第{i}批: {'、'.join(b)}")

    if args.list:
        return

    for i, words in enumerate(batches, 1):
        if i < args.start_batch:
            continue
        print(f"\n=== 第{i}/{len(batches)} 批：{'、'.join(words)} ===", flush=True)
        rc, n, log = run_batch(words, args.max_notes, f"full_xhs_batch{i}.log")
        print(f"  抓取返回 rc={rc}，本批入库 {n} 条，日志 {os.path.basename(log)}", flush=True)
        ingest("xhs")
        if i < len(batches):
            print("  冷却 30 秒再跑下一批…", flush=True)
            time.sleep(30)
    print("\n=== 全量批次结束 ===")


if __name__ == "__main__":
    main()