#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恢复视频号（shipinhao）原始数据到主库。

背景：重灌小红书时清过全库，视频号条目被清掉；原始 JSON 仍在
data/shipinhao/raw/<日期>/<账号>.json 里（含作者、发布时间、赞转评、描述）。

用法：
  python3 crawler/reingest_shipinhao.py --dry-run
  python3 crawler/reingest_shipinhao.py --apply
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import datetime
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")

# 账号 → 品牌（来自 config/brands.json 的 channels_accounts）
ACCOUNT_BRAND = {
    "MitoQ": "MitoQ",
    "LAIFE乐梵": "LAIFE",
    "满益奶": "GRANVER",
    "W+端粒塔官方营养师": "W+端粒塔",
    "Cellerator赛乐瑞": "塞乐瑞",
    "科诗博缇": "COSMOBOUNTY",
    "COSMOBOUNTY": "COSMOBOUNTY",
    "赛乐瑞": "塞乐瑞",
}
BRAND_PATTERNS = {
    "COSMOBOUNTY": r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时|分时胶囊|日活瓶|夜修瓶|分时抗衰|节律抗衰)",
    "MitoQ": r"(mito\s*q)",
    "GRANVER": r"(granve|吉利肝|吉清肺|吉力威|吉瑞维)",
    "LAIFE": r"(laife|乐梵|红宝瓶|乐悦泰|乐轻肝|乐维源|乐净肺)",
    "W+端粒塔": r"(w\+\s*端粒塔|端粒塔|w\+\s*duanlta|duanlta|nmn\s*25000)",
    "塞乐瑞": r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱|赛特茵|super\s*-?\s*k)",
}
RX = {k: re.compile(v, re.I) for k, v in BRAND_PATTERNS.items()}


def num(v):
    try:
        return int(str(v).strip() or 0)
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    items = []
    for f in glob.glob(os.path.join(ROOT, "data/shipinhao/raw/*/*.json")):
        account = os.path.splitext(os.path.basename(f))[0]
        brand = ACCOUNT_BRAND.get(account)
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        feed = d.get("feedInfo") or {}
        author = d.get("authorInfo") or {}
        desc = (feed.get("description") or "").strip()
        title = desc.split("#")[0].strip() or desc[:40]
        ts = feed.get("createtime")
        pub = None
        if ts:
            try:
                pub = datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub = None
        nick = (author.get("nickname") or account).strip()
        # 品牌归属：优先账号映射，其次描述里是否提及
        if not brand:
            m = re.search(r"#[^#]*(?:mitoq|granver|laife|端粒塔|赛乐瑞|塞乐瑞|科诗博缇|cosmo\s*bounty)[^#]*#",
                          desc, re.I)
            if not m:
                continue
            token = m.group(0).strip("#").lower()
            for b, rx in RX.items():
                if rx.search(token):
                    brand = b
                    break
        if not brand:
            continue
        # 正文必须真实提及该品牌（六家同口径）
        if not RX[brand].search(desc):
            continue
        vid = feed.get("id") or feed.get("objectId") or os.path.basename(f) + str(ts)
        items.append({
            "brand": brand, "title": title[:120], "body": desc[:2000], "author": nick,
            "pub": pub, "likes": num(feed.get("likeCountFmt")),
            "collects": num(feed.get("favCountFmt")), "comments": num(feed.get("commentCountFmt")),
            "shares": num(feed.get("forwardCountFmt")), "raw": f,
            "hash": f"shipinhao:{vid}",
            "url": f"https://channels.weixin.qq.com/platform/post/{vid}",
        })

    print(f"视频号候选 {len(items)} 条")
    for it in items:
        print(f"  [{it['brand']}] {it['author'][:18]:20s} {str(it['pub'])[:16]:18s} "
              f"赞{it['likes']:5d} 转{it['shares']:4d} {it['title'][:26]}")
    if not args.apply:
        print("\n(dry-run，加 --apply 执行入库)")
        return

    con = sqlite3.connect(DB)
    n = 0
    for it in items:
        cur = con.execute(
            """INSERT OR IGNORE INTO content
               (brand, platform, content_type, title, body, author, publish_time, collect_time,
                url, metrics_json, tags, raw_path, text_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (it["brand"], "shipinhao", "video", it["title"], it["body"], it["author"], it["pub"], now,
             it["url"],
             json.dumps({"likes": it["likes"], "collects": it["collects"],
                         "comments": it["comments"], "shares": it["shares"]}, ensure_ascii=False),
             "视频号", it["raw"], it["hash"]))
        n += cur.rowcount
    con.commit()
    tot = con.execute("select count(*) from content where platform='shipinhao'").fetchone()[0]
    con.close()
    print(f"\n已入库 {n} 条，视频号现有 {tot} 条")


if __name__ == "__main__":
    import json  # noqa: F401  (在 main 内部已导入，此处保证可用)
    main()
