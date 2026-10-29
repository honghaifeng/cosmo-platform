#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书互动数/标题回填（纯本地，不联网）。

问题背景：
  xhs_search_fill.py 写库时从详情对象的**平铺字段**取互动数
  （d.get("liked_count")），但小红书详情接口把互动数放在
  d["interact_info"] 里，平铺字段不存在 → content.metrics_json 全是 null，
  对外页面「赞/藏/评/转发」四列全部显示 0。

  详情原始 JSON 已按 note_id 落盘在 data/xhs_archive/raw/<note_id>.json，
  里面的 interact_info 是抓取当时的真实值。本脚本从这些本地文件回填，
  不需要重新请求小红书，因此不受风控影响。

同时修两件同源问题：
  · title 为空但正文有内容 → 用正文首行（≤40 字）做标题，表里不再出现空标题
  · media_json 为空但详情里有 image_list / video → 补图片地址

用法：
  python3 crawler/backfill_xhs_metrics.py --dry-run   # 只统计不写
  python3 crawler/backfill_xhs_metrics.py
"""
import argparse
import glob
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW = os.path.join(ROOT, "data", "xhs_archive", "raw")


def num(v):
    """互动数可能是 ''、'1'、'1.2万'、None。统一成 int。"""
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    try:
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        if "w" in s.lower():
            return int(float(s.lower().replace("w", "")) * 10000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def first_line(body, n=40):
    for line in (body or "").splitlines():
        t = line.strip()
        if len(t) >= 4:
            return t[:n]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    print(f"详情 JSON：{len(files)} 个")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    stat = {"metrics": 0, "title": 0, "media": 0, "miss": 0, "note": 0}
    samples = []

    for fp in files:
        nid = os.path.basename(fp)[:-5]
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue

        ii = d.get("interact_info") or {}
        m = {
            "likes": num(ii.get("liked_count")),
            "collects": num(ii.get("collected_count")),
            "comments": num(ii.get("comment_count")),
            "shares": num(ii.get("share_count")),
        }
        title = (d.get("title") or "").strip()
        desc = (d.get("desc") or "").strip()
        if not title:
            title = first_line(desc)

        imgs = [x if isinstance(x, str) else (x.get("url_default") or x.get("url") or "")
                for x in (d.get("image_list") or [])]
        imgs = [u for u in imgs if u]

        th = f"xhs:{nid}"
        row = cur.execute("SELECT id,title,body,metrics_json,media_json FROM content "
                          "WHERE text_hash=?", (th,)).fetchone()
        if not row:
            stat["miss"] += 1
            continue

        setq, vals = [], []
        old = {}
        try:
            old = json.loads(row["metrics_json"] or "{}")
        except Exception:
            old = {}
        if all((old.get(k) or 0) == 0 for k in ("likes", "collects", "comments", "shares")) \
                and any(m.values()):
            setq.append("metrics_json=?"); vals.append(json.dumps(m, ensure_ascii=False))
            stat["metrics"] += 1
            if len(samples) < 8:
                samples.append((nid, m))

        if not (row["title"] or "").strip() and title:
            setq.append("title=?"); vals.append(title)
            stat["title"] += 1

        try:
            mj = json.loads(row["media_json"] or "{}")
        except Exception:
            mj = {}
        if not (mj.get("images") or mj.get("video")) and imgs:
            mj["images"] = imgs
            setq.append("media_json=?"); vals.append(json.dumps(mj, ensure_ascii=False))
            stat["media"] += 1

        if setq:
            vals.append(row["id"])
            if not args.dry_run:
                cur.execute(f"UPDATE content SET {', '.join(setq)} WHERE id=?", vals)

        # xhs_note 同步互动数（归档导出用）
        if not args.dry_run and any(m.values()):
            cur.execute("""UPDATE xhs_note SET likes=?, collects=?, comments=?, shares=?
                           WHERE note_id=? AND IFNULL(likes,0)+IFNULL(collects,0)
                                 +IFNULL(comments,0)+IFNULL(shares,0)=0""",
                        (m["likes"], m["collects"], m["comments"], m["shares"], nid))
        stat["note"] += 1

    if not args.dry_run:
        con.commit()
    con.close()

    print(f"  回填互动数 {stat['metrics']} 条 / 补标题 {stat['title']} 条 / "
          f"补图片 {stat['media']} 条 / 库内无对应 {stat['miss']} 个")
    for nid, m in samples:
        print(f"    {nid} → 赞{m['likes']} 藏{m['collects']} 评{m['comments']} 转{m['shares']}")
    if args.dry_run:
        print("（dry-run，未写库）")


if __name__ == "__main__":
    main()
