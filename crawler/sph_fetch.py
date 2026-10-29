#!/usr/bin/env python3
"""视频号分享链接解析器（纯 HTTP，免登录，实测可用）

原理（从 finder-preview 前端 bundle 逆出，见 merlin.208c29a2.js）：
  预览页 SPA 发的是 POST /finder-preview/api/feed/get_feed_info
  body = {"baseReq": {"generalToken": token}, "shortUri": <分享短链的 id>}
  token 可为空串，shortUri 就是 URL 里 /sph/ 后面那段 —— 无需微信登录、无需元宝 cookie。

账号主页（可选）：
  POST /finder-preview/api/feed/get_userpage
  body = {"baseReq": {}, "exportUsername": <主页分享链接里的 eid>}

用法：
  python3 sph_fetch.py https://weixin.qq.com/sph/XXXX [更多链接...]
  python3 sph_fetch.py -f links.txt
  python3 sph_fetch.py <url> --brand LAIFE --no-db
  python3 sph_fetch.py --userpage <eid>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "cosmobounty.db")
BRANDS_PATH = os.path.join(ROOT, "config", "brands.json")
RAW_DIR = os.path.join(ROOT, "data", "shipinhao", "raw")

API_BASE = "https://channels.weixin.qq.com/finder-preview/api/feed"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
CST = timezone(timedelta(hours=8))

# 认证图标：企业认证 / 个人认证
AUTH_ICONS = {
    "authentication_enterprise": "企业认证",
    "authentication_personal": "个人认证",
    "authentication_organization": "机构认证",
}


# ---------------------------------------------------------------- http

def post_api(endpoint: str, payload: dict, page: str, timeout: int = 30) -> dict:
    rid = str(uuid.uuid4())
    url = (f"{API_BASE}/{endpoint}?_rid={rid[:8]}-{rid[9:17]}"
           f"&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2F{page}")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    for k, v in {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": f"https://channels.weixin.qq.com/finder-preview/pages/{page}",
        "User-Agent": UA,
    }.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


# ---------------------------------------------------------------- 解析

def extract_id(raw: str) -> str:
    """从各种形态的分享链接里取出短链 id。"""
    raw = raw.strip()
    m = re.search(r"/sph/([A-Za-z0-9_-]+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", raw)
    if m:
        return m.group(1)
    return raw


def auth_type(auth_icon_url: str) -> str:
    for key, label in AUTH_ICONS.items():
        if key in (auth_icon_url or ""):
            return label
    return "未认证" if not auth_icon_url else "已认证"


def num(value) -> int:
    """'1.2万' / '16' -> int"""
    if value in (None, ""):
        return 0
    s = str(value).strip()
    try:
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        return int(float(s))
    except ValueError:
        return 0


def load_brands() -> list[dict]:
    with open(BRANDS_PATH, encoding="utf-8") as f:
        return json.load(f)["brands"]


def match_brand(text: str, brands: list[dict]) -> str:
    low = text.lower()
    for b in brands:
        for alias in [b["name"], *b.get("aliases", [])]:
            if alias and alias.lower() in low:
                return b["name"]
    return "未归属"


def fetch_feed(short_id: str) -> dict:
    resp = post_api("get_feed_info",
                    {"baseReq": {"generalToken": ""}, "shortUri": short_id},
                    "sph")
    data = resp.get("data") or {}
    if not data or not data.get("feedInfo"):
        raise RuntimeError(f"接口未返回内容: {json.dumps(resp, ensure_ascii=False)[:300]}")
    return data


def fetch_userpage(export_username: str) -> dict:
    return post_api("get_userpage",
                    {"baseReq": {}, "exportUsername": export_username},
                    "profile")


# ---------------------------------------------------------------- 落盘 & 入库

def save_raw(short_id: str, payload: dict) -> str:
    day = datetime.now(CST).strftime("%Y-%m-%d")
    out_dir = os.path.join(RAW_DIR, day)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{short_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def ingest(item: dict, raw_path: str) -> int | None:
    """写入 content 表；已存在则更新指标并返回原 id。"""
    if not os.path.exists(DB_PATH):
        return None
    text_hash = hashlib.sha1(f"shipinhao|{item['short_id']}".encode()).hexdigest()
    metrics = json.dumps({
        "likes": item["likes"], "favorites": item["favorites"],
        "forwards": item["forwards"], "comments": item["comments"],
        "likes_fmt": item["likes_fmt"], "favorites_fmt": item["favorites_fmt"],
        "forwards_fmt": item["forwards_fmt"], "comments_fmt": item["comments_fmt"],
    }, ensure_ascii=False)
    media = json.dumps({
        "cover": item["cover"], "author_avatar": item["avatar"],
        "auth_type": item["auth_type"], "location": item["location"],
    }, ensure_ascii=False)
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT id FROM content WHERE text_hash = ?", (text_hash,))
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE content SET metrics_json=?, media_json=?, collect_time=?, raw_path=? WHERE id=?",
                (metrics, media, now, raw_path, row[0]))
            conn.commit()
            return row[0]
        cur = conn.execute(
            """INSERT INTO content
               (brand, platform, content_type, title, body, author, publish_time,
                collect_time, url, metrics_json, tags, raw_path, text_hash, media_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item["brand"], "shipinhao", "video", item["title"], item["description"],
             item["author"], item["publish_time"], now, item["url"],
             metrics, item["tags"], raw_path, text_hash, media))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def build_item(short_id: str, data: dict, brands: list[dict], force_brand: str | None) -> dict:
    feed = data["feedInfo"]
    author = data.get("authorInfo") or {}
    desc = (feed.get("description") or "").strip()
    create_ts = feed.get("createtime") or 0
    publish_time = (datetime.fromtimestamp(create_ts, CST).strftime("%Y-%m-%d %H:%M:%S")
                    if create_ts else "")
    first_line = next((ln.strip() for ln in desc.splitlines() if ln.strip()), "(无文案)")
    title = first_line[:60]
    brand = force_brand or match_brand(f"{desc} {author.get('nickname', '')}", brands)
    tags = " ".join(re.findall(r"#(\S+)", desc))
    return {
        "short_id": short_id,
        "url": f"https://weixin.qq.com/sph/{short_id}",
        "title": title,
        "description": desc,
        "author": author.get("nickname", ""),
        "avatar": author.get("headImgUrl", ""),
        "auth_type": auth_type(author.get("authIconUrl", "")),
        "verified": bool(author.get("authIconUrl")),
        "location": (feed.get("jumpInfo") or {}).get("wording", ""),
        "publish_time": publish_time,
        "likes": num(feed.get("likeCountFmt")),
        "favorites": num(feed.get("favCountFmt")),
        "forwards": num(feed.get("forwardCountFmt")),
        "comments": num(feed.get("commentCountFmt")),
        "likes_fmt": feed.get("likeCountFmt", ""),
        "favorites_fmt": feed.get("favCountFmt", ""),
        "forwards_fmt": feed.get("forwardCountFmt", ""),
        "comments_fmt": feed.get("commentCountFmt", ""),
        "is_ad": feed.get("isHardAd", False),
        "cover": feed.get("coverUrl", ""),
        "export_id": (data.get("sceneInfo") or {}).get("dynamicExportId", ""),
        "brand": brand,
        "tags": tags,
    }


# ---------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description="视频号分享链接解析（纯 HTTP，免登录）")
    ap.add_argument("links", nargs="*", help="分享链接或短链 id，可多个")
    ap.add_argument("-f", "--file", help="每行一个链接的文件")
    ap.add_argument("--brand", help="强制指定品牌")
    ap.add_argument("--no-db", action="store_true", help="只输出，不写库")
    ap.add_argument("--userpage", help="解析账号主页（传主页分享链接里的 eid）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    if args.userpage:
        print(json.dumps(fetch_userpage(args.userpage), ensure_ascii=False, indent=2))
        return 0

    links = list(args.links)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            links += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not links:
        ap.print_help()
        return 1

    brands = load_brands()
    rows, failures = [], []
    for raw in links:
        sid = extract_id(raw)
        try:
            data = fetch_feed(sid)
        except Exception as exc:                                  # noqa: BLE001
            failures.append((raw, str(exc)))
            print(f"[FAIL] {raw} -> {exc}", file=sys.stderr)
            continue
        raw_path = save_raw(sid, data)
        item = build_item(sid, data, brands, args.brand)
        if args.json:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            rows.append(item)
        if not args.no_db:
            cid = ingest(item, raw_path)
            print(f"[OK] {item['author']}（{item['auth_type']}）| {item['brand']} | "
                  f"{item['publish_time']} | 赞{item['likes']}/藏{item['favorites']}/"
                  f"转{item['forwards']}/评{item['comments']} | content.id={cid}", file=sys.stderr)
        time.sleep(0.6)

    if rows:
        print(f"\n{'品牌':<10}{'账号':<16}{'认证':<8}{'发布':<12}{'赞':>6}{'藏':>5}{'转':>5}{'评':>5}  标题")
        print("-" * 110)
        for r in rows:
            print(f"{r['brand']:<10}{r['author']:<16}{r['auth_type']:<8}"
                  f"{r['publish_time'][:10]:<12}{r['likes']:>6}{r['favorites']:>5}"
                  f"{r['forwards']:>5}{r['comments']:>5}  {r['title'][:34]}")
    if failures:
        print(f"\n失败 {len(failures)} 条", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
