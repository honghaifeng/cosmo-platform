#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品牌归属清洗：SERP 类平台（baidu/web/toutiao）会把「Cosmo」匹配到游戏/杂志、
把「LAIFE」匹配到无关词条，直接按关键词入库会污染品牌维度。

规则与小红书入库一致：**正文/标题真实提及品牌名、别名或产品名，才算该品牌的内容**。
用法：
  python3 crawler/clean_brand_mentions.py            # 默认 dry-run，只报告
  python3 crawler/clean_brand_mentions.py --apply    # 真正删除噪声
"""
import argparse
import json
import os
import re
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
SERP_PLATFORMS = ("baidu", "web", "toutiao")


def brand_patterns():
    """从 brands.json 生成每个品牌的「真提及」正则。"""
    cfg = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))
    out = {}
    for b in cfg["brands"]:
        words = set()
        for w in [b["name"]] + list(b.get("aliases", [])) + list(b.get("products", [])):
            w = (w or "").strip()
            if len(w) >= 2:
                words.add(w)
        # 去掉过泛的单词（如 COSMOBOUNTY 之外单独的 "Cosmo"）
        words = {w for w in words if len(w.replace(" ", "")) >= 3 or re.search(r"[一-龥]", w)}
        pat = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        out[b["name"]] = re.compile(pat, re.I)
    return out


# 品类语境词：品牌短词（如 MitoQ）必须与品类词同现才算真提及，
# 否则「线粒体呼吸功能测定」「雀巢pure」「ELLE香水」都会误判
CATEGORY_HINTS = re.compile(
    r"(抗衰|逆龄|保健|营养|补充剂|胶囊|片剂|补剂|成分|剂量|功效|服用|口碑|测评|种草|"
    r"Healthy|health|Capsule|capsule|supplement|Supplement|姜黄|镁|奶蓟|鱼油|NMN|NAD|槲皮素|"
    r"精华|抗氧|细胞|线粒体|护肝|睡眠|精力)", re.I)
# 明确与品牌无关的干扰词
BLOCKLIST = re.compile(r"(百度百科|百度图片|百度健康|百度知道|爱采购|京东商品|铃声|歌词|游戏|战队|环球|时报)")


def is_true_mention(text, pattern, brand):
    if BLOCKLIST.search(text):
        return False
    if not pattern.search(text):
        return False
    # 品牌名较短（英文缩写）时要求同时出现品类语境
    if re.fullmatch(r"[A-Za-z0-9+ ]{2,8}", brand or ""):
        return bool(CATEGORY_HINTS.search(text))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除噪声行")
    args = ap.parse_args()

    pats = brand_patterns()
    con = sqlite3.connect(DB)
    ph = ",".join("?" * len(SERP_PLATFORMS))
    rows = con.execute(
        f"SELECT id, brand, platform, title, body, url FROM content WHERE platform IN ({ph})",
        SERP_PLATFORMS).fetchall()

    noise, keep = [], []
    for cid, brand, platform, title, body, url in rows:
        p = pats.get(brand)
        text = f"{title or ''} {body or ''}"
        if p is None or not is_true_mention(text, p, brand):
            noise.append((cid, brand, platform, title))
        else:
            keep.append((cid, brand, platform, title))

    print(f"SERP 类内容合计 {len(rows)} 条：真提及 {len(keep)}，噪声 {len(noise)}")
    by = {}
    for cid, brand, platform, title in noise:
        by.setdefault((brand, platform), 0)
        by[(brand, platform)] += 1
    for (brand, platform), n in sorted(by.items(), key=lambda x: -x[1]):
        print(f"  噪声 {brand:14s} {platform:8s} {n:4d}")

    if not args.apply:
        print("\n（dry-run，加 --apply 执行删除）")
        con.close()
        return

    ids = [c[0] for c in noise]
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        con.execute(f"DELETE FROM content WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    now = con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    con.commit()
    con.close()
    print(f"\n已删除 {len(ids)} 条噪声，当前内容总数 {now}")


if __name__ == "__main__":
    main()