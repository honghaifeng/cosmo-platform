#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重灌搜索类原始数据（百度 / 头条 / web）到主库。

背景：重灌小红书时曾清空过全库，导致早期抓到的百度/头条/web 条目丢失，
但原始 JSON 与正文都在 data/raw/ 下，本脚本把它们按统一规则恢复。

来源：
  data/raw/baidu/*.json      早期百度 SERP（标题+链接）
  data/raw/baidu_full/*.json 新版百度全量（标题+链接+摘要+发布时间+真实URL）
  data/raw/toutiao/*.json    头条 SERP
  data/raw/search/*.json     通用搜索 SERP
  data/raw/articles/*.txt    已抓回的正文（含 url / title 头）

用法：
  python3 crawler/reingest_search.py --dry-run
  python3 crawler/reingest_search.py --apply
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import hashlib
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")

BRAND_PATTERNS = {
    "COSMOBOUNTY": r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时|分时胶囊|日活瓶|夜修瓶|节律抗衰|分时抗衰)",
    "MitoQ": r"(mito\s*q)",
    "GRANVER": r"(granve|吉利肝|吉清肺|吉力威|吉瑞维)",
    "LAIFE": r"(laife|乐梵|红宝瓶|乐悦泰|乐轻肝|乐维源|乐净肺)",
    "W+端粒塔": r"(w\+\s*端粒塔|端粒塔|w\+\s*duanlta|duanlta|nmn\s*25000)",
    "塞乐瑞": r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱|赛特茵|super\s*-?\s*k)",
}
RX = {k: re.compile(v, re.I) for k, v in BRAND_PATTERNS.items()}
BLOCK = re.compile(r"(百度百科|百度图片|百度健康|百度知道|爱采购|京东商品|铃声|歌词|游戏|战队|环球|时报|招聘)")
CATEGORY = re.compile(r"(抗衰|逆龄|保健|营养|补充剂|胶囊|片剂|补剂|成分|剂量|功效|服用|口碑|测评|种草|鱼油|奶蓟|Healthy|supplement|姜黄|镁|NMN|NAD|精华|抗氧|细胞|线粒体|护肝|睡眠|精力)")


def true_mention(brand, text):
    rx = RX.get(brand)
    if not rx or not text:
        return False
    if BLOCK.search(text):
        return False
    if not rx.search(text):
        return False
    if re.fullmatch(r"[A-Za-z0-9+ ]{2,8}", brand or ""):
        return bool(CATEGORY.search(text))
    return True


def norm_date(s, today):
    if not s:
        return None
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if m:
        try:
            return datetime.date(today.year, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return (today - datetime.timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        return (today - datetime.timedelta(hours=int(m.group(1)))).isoformat()
    if "昨天" in s:
        return (today - datetime.timedelta(days=1)).isoformat()
    if "前天" in s:
        return (today - datetime.timedelta(days=2)).isoformat()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    today = datetime.date.today()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = []   # (brand, platform, title, body, url, publish_time, raw_path, keyword)

    # 1) 百度（新版全量，带发布时间与真实 URL）
    for f in glob.glob(os.path.join(ROOT, "data/raw/baidu_full/*.json")):
        brand = os.path.basename(f).replace(".json", "")
        try:
            items = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for it in items if isinstance(items, list) else []:
            url = it.get("real_url") or it.get("url") or ""
            title = (it.get("title") or "").strip()
            if not title or not url:
                continue
            rows.append((brand, "baidu", title, (it.get("snippet") or "")[:300], url,
                         it.get("publish_time"), f, it.get("keyword") or ""))

    # 2) 百度（早期 SERP，只有标题+链接）
    for f in glob.glob(os.path.join(ROOT, "data/raw/baidu/*.json")):
        brand = os.path.basename(f).replace(".json", "")
        try:
            items = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for it in items if isinstance(items, list) else []:
            url = it.get("url") or ""
            title = (it.get("title") or "").strip()
            if title and url:
                rows.append((brand, "baidu", title, "", url, None, f, ""))

    # 3) 头条 / 通用搜索
    for sub, plat in (("toutiao", "toutiao"), ("search", "web")):
        for f in glob.glob(os.path.join(ROOT, f"data/raw/{sub}/*.json")):
            brand = os.path.basename(f).replace(".json", "")
            try:
                items = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            for it in items if isinstance(items, list) else []:
                url = it.get("url") or ""
                title = (it.get("title") or "").strip()
                if title and url:
                    rows.append((brand, plat, title, "", url, None, f, ""))

    # 4) 正文回填（articles/*.txt 里有 url / title / 正文）
    art_by_url = {}
    art_by_title = {}
    for f in glob.glob(os.path.join(ROOT, "data/raw/articles/*.txt")):
        try:
            head = open(f, encoding="utf-8", errors="ignore").read(600)
        except Exception:
            continue
        m = re.search(r"^url:\s*(\S+)", head, re.M)
        t = re.search(r"^title:\s*(.+)$", head, re.M)
        if m:
            art_by_url[m.group(1).strip()] = (f, t.group(1).strip() if t else "")
        # 早期百度条目存的是 link?url= 跳转链，正文存的是解析后的真实 URL，
        # 两者对不上，只能靠标题关联
        if t:
            key = re.sub(r"\s+", "", t.group(1).strip().lower())
            art_by_title[key] = (f, t.group(1).strip())
    rows2 = []
    for r in rows:
        brand, plat, title, body, url, pub, raw, kw = r
        fpath, atitle = art_by_url.get(url, (None, None))
        if not fpath:
            fpath, atitle = art_by_title.get(re.sub(r"\s+", "", title.lower()), (None, None))
        if fpath:
            try:
                txt = open(fpath, encoding="utf-8", errors="ignore").read()
                body = txt.split("\n\n", 1)[-1][:3000] if "\n\n" in txt else txt[:3000]
            except Exception:
                pass
            if not pub:
                pm = re.search(r"published_time[=:]\s*(\S+)", txt) if 'txt' in dir() else None
                if pm:
                    pub = norm_date(pm.group(1), today)
        rows2.append((brand, plat, title or (etitle or ""), body, url, pub, raw, kw))
    rows = rows2

    # 去重 + 提及过滤
    seen, seen_title, keep, drop = set(), set(), [], 0
    for brand, plat, title, body, url, pub, raw, kw in rows:
        # 去重键不能用 url.split("?")[0]：早期百度 URL 全是 link?url=… 跳转链，
        # 截断后所有条目会撞成同一个 key 被误判为重复
        key = url
        tkey = re.sub(r"\s+", "", (title or "").lower())
        if key in seen or (tkey and tkey in seen_title):
            continue
        if key in seen:
            continue
        seen.add(key)
        if tkey:
            seen_title.add(tkey)
        text = f"{title} {body}"
        if not true_mention(brand, text):
            drop += 1
            continue
        keep.append((brand, plat, title, body[:3000], url, pub, raw, kw))

    print(f"候选 {len(rows)} 条 → 去重后 {len(seen)} 条 → 真提及 {len(keep)} 条（剔除 {drop} 条噪声）")
    byp = {}
    for b, p, *_ in keep:
        byp[p] = byp.get(p, 0) + 1
    print("  按平台:", byp)
    byb = {}
    for b, p, *_ in keep:
        byb[b] = byb.get(b, 0) + 1
    print("  按品牌:", byb)
    withtime = sum(1 for r in keep if r[5])
    print(f"  带发布时间: {withtime}")

    if not args.apply:
        print("\n(dry-run，加 --apply 执行入库)")
        return

    con = sqlite3.connect(DB)
    n = 0
    for brand, plat, title, body, url, pub, raw, kw in keep:
        h = hashlib.md5(url.encode()).hexdigest()
        cur = con.execute(
            """INSERT OR IGNORE INTO content
               (brand, platform, content_type, title, body, publish_time, collect_time,
                url, raw_path, text_hash, tags)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (brand, plat, "article", title, body, pub, now, url, raw, h, kw))
        n += cur.rowcount
    con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) "
                "VALUES (?,?,?,?,?,?)", ("reingest", "all", "raw 恢复", now, "ok", f"恢复 {n} 条"))
    con.commit()
    total = con.execute("select count(*) from content where platform in ('baidu','toutiao','web')").fetchone()[0]
    con.close()
    print(f"\n已入库 {n} 条，搜索类现有 {total} 条")


if __name__ == "__main__":
    main()
