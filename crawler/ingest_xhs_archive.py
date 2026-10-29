#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把已落盘但未入库的小红书详情归档（data/xhs_archive/raw/*.json）回灌进 content。

背景：
  详情抓取脚本会把原始 JSON 存档到 data/xhs_archive/raw/<note_id>.json，
  但入库环节偶尔漏写（搜索补全只 upsert 命中的那条），
  结果磁盘上有正文、库里却是空的 —— 等于白抓。

规则（与 verify_and_gate.py 同口径，避免两套标准）：
  · 正文 < 30 字 → 不入库（无实质内容）
  · 标题+正文必须命中且只命中一个品牌别名 → 才入库；
    命中 0 个（泛流量/无关内容）或命中多个（横评/多品牌同篇）→ 跳过，
    不入库，不污染 brand 维度
  · publish_time 取详情 JSON 的 time（毫秒），不猜、不用 note_id 反推
  · 入库后 verified 留空，由 verify_and_gate.py 统一判定

用法：
  python crawler/ingest_xhs_archive.py --dry-run
  python crawler/ingest_xhs_archive.py
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW_DIR = os.path.join(ROOT, "data", "xhs_archive", "raw")

sys.path.insert(0, os.path.join(ROOT, "crawler"))
from verify_and_gate import BRAND_RX, MIN_BODY  # noqa: E402

MIN_LEN = MIN_BODY["xhs"]


def brand_of(text):
    hits = [b for b, rx in BRAND_RX.items() if rx.search(text or "")]
    return hits[0] if len(hits) == 1 else None


def to_time(v):
    if v in (None, "", 0):
        return ""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return ""
    if n > 10_000_000_000:      # 毫秒
        n = n / 1000
    try:
        return datetime.datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def images_of(d):
    out = []
    for x in (d.get("image_list") or []):
        if isinstance(x, str):
            if x:
                out.append(x)
        elif isinstance(x, dict):
            u = x.get("url_default") or x.get("url") or ""
            if u:
                out.append(u)
    return out


def clean_title(t):
    return (t or "").strip()[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    c = con.cursor()
    known = {r[0] for r in c.execute("SELECT text_hash FROM content")}
    tok_map = {r[0]: r[1] for r in c.execute("SELECT note_id, xsec_token FROM xhs_note")}

    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    stat = {"files": len(files), "在库": 0, "正文过短": 0, "品牌不明": 0,
            "坏文件": 0, "新增": 0}
    to_add = []

    for fp in files:
        nid = os.path.basename(fp)[:-5]
        th = f"xhs:{nid}"
        if th in known:
            stat["在库"] += 1
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            stat["坏文件"] += 1
            continue
        if not isinstance(d, dict):
            stat["坏文件"] += 1
            continue
        desc = (d.get("desc") or "").strip()
        title = clean_title(d.get("title"))
        if len(desc) < MIN_LEN:
            stat["正文过短"] += 1
            continue
        brand = brand_of(f"{title} {desc}")
        if not brand:
            stat["品牌不明"] += 1
            continue
        user = d.get("user") if isinstance(d.get("user"), dict) else {}
        tok = tok_map.get(nid, "") or d.get("xsec_token") or ""
        url = f"https://www.xiaohongshu.com/explore/{nid}"
        if tok:
            url += f"?xsec_token={tok}&xsec_source=pc_search"
        metrics = {
            "likes": d.get("liked_count"), "collects": d.get("collected_count"),
            "comments": d.get("comment_count"), "shares": d.get("share_count"),
        }
        tags = " ".join(
            t.get("name", "") for t in (d.get("tag_list") or []) if isinstance(t, dict)
        )
        to_add.append((brand, title, desc, user.get("nickname"), user.get("user_id"),
                       to_time(d.get("time")), url, json.dumps(metrics, ensure_ascii=False),
                       tags, nid, images_of(d), d.get("ip_location")))

    stat["新增"] = len(to_add)
    print(json.dumps(stat, ensure_ascii=False, indent=1), flush=True)
    if args.dry_run:
        for r in to_add[:8]:
            print(f"  {r[0]:12s} | {r[5]} | {str(r[1])[:24]:24s} | len={len(r[2])}", flush=True)
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for (brand, title, desc, nick, uid, pt, url, mj, tags, nid, imgs, ip) in to_add:
        c.execute(
            """INSERT INTO content
               (brand, platform, content_type, title, body, author, author_id,
                collect_time, publish_time, url, metrics_json, tags, media_json,
                raw_path, text_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (brand, "xhs", "note", title, desc, nick, uid, now, pt, url, mj, tags,
             json.dumps({"images": imgs}, ensure_ascii=False) if imgs else "",
             "xhs_archive", f"xhs:{nid}"),
        )
        # 同步资产到 xhs_note，供归档页与图片/视频补全复用
        imgs_json = json.dumps(imgs, ensure_ascii=False) if imgs else ""
        row = c.execute("SELECT note_id FROM xhs_note WHERE note_id=?", (nid,)).fetchone()
        if row:
            c.execute(
                """UPDATE xhs_note SET body=?, title=COALESCE(NULLIF(title,''),?),
                   publish_time=COALESCE(NULLIF(publish_time,''),?),
                   images_json=COALESCE(NULLIF(?, ''), images_json),
                   note_url=COALESCE(NULLIF(note_url,''),?), brand=?
                   WHERE note_id=?""",
                (desc, title, pt, imgs_json, url, brand, nid),
            )
        else:
            c.execute(
                """INSERT OR IGNORE INTO xhs_note
                   (note_id, title, body, brand, author, author_id, publish_time,
                    images_json, note_url, xsec_token, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (nid, title, desc, brand, nick, uid, pt,
                 imgs_json, url, tok_map.get(nid, ""), now),
            )
    con.commit()
    print(f"已写入 {len(to_add)} 条；下一步跑 crawler/verify_and_gate.py", flush=True)


if __name__ == "__main__":
    main()
