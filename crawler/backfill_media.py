#!/usr/bin/env python3
"""一次性回填：从 MediaCrawler 原始 jsonl 给存量 content 补 media_json（图片/视频/封面）。"""
import json, sqlite3, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
JSONL_DIRS = {
    "xhs": os.path.join(BASE, "tools/MediaCrawler/data/xhs/jsonl"),
    "douyin": os.path.join(BASE, "tools/MediaCrawler/data/douyin/jsonl"),
}


def extract(n):
    media = {}
    img_raw = n.get("image_list")
    if isinstance(img_raw, str) and img_raw.strip():
        media["images"] = [u.strip() for u in img_raw.split(",") if u.strip().startswith("http")]
    elif isinstance(img_raw, list):
        urls = [u if isinstance(u, str) else (u.get("url") or "") for u in img_raw if u]
        media["images"] = [u for u in urls if u.startswith("http")]
    cover = (n.get("cover_url") or "").strip()
    if cover.startswith("http"):
        media.setdefault("cover", cover)
    vurl = (n.get("video_download_url") or n.get("video_url") or "").strip()
    if vurl.startswith("http"):
        media["video"] = vurl
    return media


def main():
    # 1. 建索引: text_hash -> 原始记录
    raw = {}  # hash -> media dict
    for platform, d in JSONL_DIRS.items():
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not (fn.startswith("search_contents") and fn.endswith(".jsonl")):
                continue
            for line in open(os.path.join(d, fn), encoding="utf-8"):
                try:
                    n = json.loads(line)
                except Exception:
                    continue
                nid = n.get("note_id") or n.get("aweme_id")
                if not nid:
                    continue
                m = extract(n)
                if m:
                    raw.setdefault(f"{platform}:{nid}", m)

    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id, text_hash, media_json FROM content WHERE platform IN ('xhs','douyin')"
    ).fetchall()
    filled = kept = 0
    for cid, h, mj in rows:
        if h not in raw:
            continue
        old = json.loads(mj) if mj else {}
        if old.get("images") or old.get("video"):
            kept += 1
            continue
        con.execute("UPDATE content SET media_json=? WHERE id=?",
                    (json.dumps(raw[h], ensure_ascii=False), cid))
        filled += 1
    con.commit()
    n_img = con.execute(
        "SELECT COUNT(*) FROM content WHERE media_json LIKE '%images%'").fetchone()[0]
    n_vid = con.execute(
        "SELECT COUNT(*) FROM content WHERE media_json LIKE '%video%'").fetchone()[0]
    print(f"回填 {filled} 条，已有跳过 {kept} 条；现库存：带图片 {n_img} 条，带视频/封面 {n_vid} 条")
    con.close()


if __name__ == "__main__":
    main()
