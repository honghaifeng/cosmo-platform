#!/usr/bin/env python3
"""MediaCrawler JSONL → 主库入库脚本（小红书/抖音通用）
用法: python3 crawler/mediacrawler_ingest.py <platform> <notes.jsonl> <comments.jsonl>
"""
import json, sqlite3, sys, os, datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
BRANDS_JSON = os.path.join(BASE, "config", "brands.json")


def load_brand_map():
    """关键词/别名 → 规范品牌名。避免新增关键词（吉利肝/乐梵/塞乐瑞）导致品牌维度碎片。"""
    mapping = {}
    try:
        with open(BRANDS_JSON, encoding="utf-8") as f:
            for b in json.load(f)["brands"]:
                mapping[b["name"].lower()] = b["name"]
                words = list(b.get("aliases", [])) + list(b.get("keywords", [])) + list(b.get("products", []))
                for g in (b.get("keyword_groups") or {}).values():
                    words += list(g)
                for a in words:
                    a = str(a).strip().lower()
                    if len(a) >= 2:
                        mapping[a] = b["name"]
    except Exception:
        pass
    return mapping


BRAND_MAP = load_brand_map()

# 六家统一的「真提及」判定：品牌名 / 别名 / 产品名 / 系列名
# 规则必须对六家一致——只给自家加严会让品牌对比失真
import re as _re

_MENTION_PATTERNS = {
    "COSMOBOUNTY": r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时|分时胶囊|日活瓶|夜修瓶|"
                    r"CytoVigor|CytoRenova|BoneSync|LiverSync|CycleSync|节律抗衰|分时抗衰)",
    # 注意：不要用 \b —— 中文紧跟英文字母时 \b 不成立（"MiTo Q鱼油" 会漏判）
    "MitoQ": r"(mito\s*q)",
    "GRANVER": r"(granve|吉利肝|吉清肺|吉力威|吉瑞维)",
    "LAIFE": r"(laife|乐梵|红宝瓶|乐悦泰|乐轻肝|乐维源|乐净肺|乐返盾|紫宝瓶|橙宝瓶|乐腺舒|巢汐)",
    "W+端粒塔": r"(w\+\s*端粒塔|端粒塔|w\+\s*duanlta|duanlta|nmn\s*25000)",
    "塞乐瑞": r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱|赛特茵|愈龄菌|勉益莱|super\s*-?\s*k|巢悦莱)",
}
_MENTION_RE = {k: _re.compile(v, _re.I) for k, v in _MENTION_PATTERNS.items()}

# 场景类关键词：命中的是泛品类内容，单独成类，不进品牌维度
SCENE_KEYWORDS = {
    "熬夜后怎么恢复", "入睡难怎么调理", "睡眠浅怎么办", "下午犯困怎么办",
    "精力差吃什么", "皮肤松弛暗沉怎么调理", "昼夜节律调理", "分时营养",
    "抗衰胶囊成分党", "抗衰胶囊测评对比",
}
# 搜索词命中但正文没提品牌 → 视为蹭词/噪声；六家一律同样处理
def mentions_brand(brand: str, text: str) -> bool:
    rx = _MENTION_RE.get(brand)
    if rx is None:
        return True
    return bool(rx.search(text or ""))


def canon_brand(keyword, fallback=""):
    k = (keyword or "").strip()
    if not k:
        return fallback
    hit = BRAND_MAP.get(k.lower())
    if hit:
        return hit
    low = k.lower()
    for alias, name in BRAND_MAP.items():
        if alias and alias in low:
            return name
    return k

def ms_to_str(ms):
    if not ms:
        return None
    try:
        ms = int(ms)
        return datetime.datetime.fromtimestamp(ms if ms < 10**12 else ms / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None

def to_int(v):
    try:
        return int(str(v).strip() or 0)
    except Exception:
        return 0

def main(platform, notes_file, comments_file):
    con = sqlite3.connect(DB)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_added = c_added = c_skip = n_skipped_own = 0

    # 内容入库
    note_map = {}  # note_id -> content_id
    for line in open(notes_file, encoding="utf-8"):
        n = json.loads(line)
        note_id = n.get("note_id") or n.get("aweme_id")
        brand = n.get("source_keyword", "").strip()
        # 关键词/别名统一归一到规范品牌名（吉利肝→GRANVER、乐梵→LAIFE、端粒塔→W+端粒塔 …）
        brand = canon_brand(brand)
        if not brand or not note_id:
            continue
        # 场景词（熬夜后怎么恢复/皮肤松弛暗沉怎么调理…）命中的是泛品类内容，
        # 不属于任何品牌；单独归为「品类场景词」，供内容策略参考，不进品牌横评
        is_scene = brand in SCENE_KEYWORDS
        url = n.get("note_url") or f"https://www.douyin.com/video/{note_id}"
        # 媒体：xhs image_list 为逗号拼接的 URL 串；douyin 有 cover/video_download_url
        media = {}
        img_raw = n.get("image_list")
        if isinstance(img_raw, str) and img_raw.strip():
            media["images"] = [u.strip() for u in img_raw.split(",") if u.strip().startswith("http")]
        elif isinstance(img_raw, list):
            media["images"] = [u if isinstance(u, str) else (u.get("url") or "")
                               for u in img_raw if u]
            media["images"] = [u for u in media["images"] if u.startswith("http")]
        cover = (n.get("cover_url") or "").strip()
        if cover.startswith("http"):
            media.setdefault("cover", cover)
        vurl = (n.get("video_download_url") or n.get("video_url") or "").strip()
        if vurl.startswith("http"):
            media["video"] = vurl
        metrics = {
            "likes": to_int(n.get("liked_count")),
            "collects": to_int(n.get("collected_count")),
            "comments": to_int(n.get("comment_count")),
            "shares": to_int(n.get("share_count")),
        }
        title = (n.get("title") or n.get("desc") or "(无标题)").strip()[:120]
        body = (n.get("desc") or "").strip()
        # 自有品牌归属校验：泛词（分时抗衰/黑白分时）会搜出大量别家内容（如 CPB 日夜霜），
        # 只有正文真的提到品牌/产品/系列的才归入 COSMOBOUNTY，避免品牌维度被污染
        if not is_scene and not mentions_brand(brand, f"{title} {body}"):
            n_skipped_own += 1
            continue
        if is_scene:
            brand = "品类场景词"
        # 时间字段各平台不一致：小红书 time(ms)，抖音 create_time(s)，视频号 create_time(ms)
        pub = ms_to_str(n.get("time")) or ms_to_str(n.get("create_time")) \
              or ms_to_str(n.get("last_modify_ts"))
        author_id = n.get("creator_hash") or n.get("user_id") or ""
        nickname = n.get("nickname") or ""
        cur = con.execute(
            """INSERT OR IGNORE INTO content
               (brand, platform, content_type, title, body, author, author_id, publish_time,
                collect_time, url, metrics_json, tags, raw_path, text_hash, media_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (brand, platform, ("场景内容" if is_scene else ("note" if platform == "xhs" else "video")),
             title, body, nickname, author_id, pub, now, url,
             json.dumps(metrics, ensure_ascii=False),
             n.get("tag_list"), notes_file, f"{platform}:{note_id}",
             json.dumps(media, ensure_ascii=False) if media else None))
        # 达人账号维度：累计发帖量与互动量，供「哪些账号在发」统计
        if author_id:
            prof = (f"https://www.xiaohongshu.com/user/profile/{author_id}"
                    if platform == "xhs" else "")
            row = con.execute("SELECT brands FROM creator WHERE platform=? AND creator_id=?",
                              (platform, author_id)).fetchone()
            brands_set = set((row[0] if row else "").split(",")) if row else set()
            brands_set.add(brand)
            con.execute("""INSERT INTO creator
                 (platform, creator_id, nickname, profile_url, note_count, total_likes,
                  total_collects, total_comments, first_publish, last_publish, brands, updated_at)
                 VALUES (?,?,?,?,1,?,?,?,?,?,?,?)
                 ON CONFLICT(platform, creator_id) DO UPDATE SET
                   note_count = note_count + 1,
                   total_likes = total_likes + excluded.total_likes,
                   total_collects = total_collects + excluded.total_collects,
                   total_comments = total_comments + excluded.total_comments,
                   first_publish = CASE WHEN excluded.first_publish < first_publish
                                        THEN excluded.first_publish ELSE first_publish END,
                   last_publish = CASE WHEN excluded.last_publish > last_publish
                                       THEN excluded.last_publish ELSE last_publish END,
                   brands = excluded.brands,
                   nickname = CASE WHEN excluded.nickname != '' THEN excluded.nickname ELSE nickname END,
                   updated_at = excluded.updated_at""",
                (platform, author_id, nickname, prof,
                 metrics.get("likes", 0) or 0, metrics.get("collects", 0) or 0,
                 metrics.get("comments", 0) or 0, pub, pub,
                 ",".join(sorted(brands_set)), now))
        if cur.rowcount:
            n_added += 1
        row = con.execute("SELECT id FROM content WHERE text_hash=?",
                          (f"{platform}:{note_id}",)).fetchone()
        if row:
            note_map[note_id] = row[0]

    # 评论入库（当天没有评论文件时只入库内容，不报错）
    comment_lines = []
    if comments_file and os.path.exists(comments_file):
        comment_lines = open(comments_file, encoding="utf-8")
    for line in comment_lines:
        c = json.loads(line)
        note_id = c.get("note_id") or c.get("aweme_id")
        content_id = note_map.get(note_id)
        body = (c.get("content") or "").strip()
        if not content_id or not body:
            c_skip += 1
            continue
        cur = con.execute(
            """INSERT OR IGNORE INTO comment
               (content_id, body, like_count, publish_time, text_hash)
               VALUES (?,?,?,?,?)""",
            (content_id, body, to_int(c.get("like_count")),
             ms_to_str(c.get("create_time")),
             f"{platform}:c:{c.get('comment_id') or c.get('cid')}"))
        if cur.rowcount:
            c_added += 1

    # crawl_log
    con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) VALUES (?,?,?,?,?,?)",
                (platform, "-", "全部关键词", now, "ok",
                 f"{n_added} notes, {c_added} comments from MediaCrawler"))
    con.commit()
    print(f"[{platform}] 新增内容 {n_added} 条, 新增评论 {c_added} 条 (跳过 {c_skip})"
          + (f"，自有品牌非提及过滤 {n_skipped_own} 条" if n_skipped_own else ""))
    print(*con.execute("SELECT brand, COUNT(*) FROM content WHERE platform=? GROUP BY 1",
                       (platform,)), sep="\n")
    con.close()

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
