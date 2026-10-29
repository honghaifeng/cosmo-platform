#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书完整归档：把所有采集到的笔记合成一份权威档案，并生成本地「原文页面」。

为什么需要：
  - 搜索接口被风控时，详情页抓不全（降级路径只有标题+互动数，正文/图片/标签为空）
  - 但批次日志里保存了**完整的搜索响应原文**（含图片列表、发布时间标签、达人 ID/昵称），
    jsonl 里保存了成功抓到详情的笔记 —— 两边合并就是最全的一份
  - note_id 前 8 位是 Unix 时间戳，可在卡片只给「10-09」时精确还原发布时间

产出：
  1. data/xhs_archive/notes.jsonl   权威档案（每行一条笔记全字段）
  2. DB 表 xhs_note                 同上，可 SQL 查询
  3. content 表回填正文/图片/标签/时间/互动数（品牌维度仍走同一套提及规则）
  4. exports/xhs_archive/<note_id>.html   本地原文页面（可离线浏览）
  5. exports/xhs_archive/index.html        归档索引（可搜索/筛选）

用法：
  python3 crawler/build_xhs_archive.py --build          # 建档案 + 回填 + 出页面
  python3 crawler/build_xhs_archive.py --build --no-html # 只建档案不出页面
  python3 crawler/build_xhs_archive.py --stats           # 只看完整度统计
"""
import argparse
import ast
import datetime
import glob
import html
import json
import os
import re
import sqlite3
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
LOGDIR = os.path.join(ROOT, "logs")
MCJSONL = os.path.join(ROOT, "tools", "MediaCrawler", "data", "xhs", "jsonl")
ARCHIVE_DIR = os.path.join(ROOT, "data", "xhs_archive")
HTML_DIR = os.path.join(ROOT, "exports", "xhs_archive")
MEDIA_DIR = os.path.join(ROOT, "data", "xhs_archive", "media")

# 两级证据规则（防止「词相同但完全无关」——如 Super-K 是男团话题而非补剂）
#   强证据：品牌主体名/独有别名 → 出现即算品牌内容
#   弱证据：仅产品名且本身有歧义（Super-K/红宝瓶/经典胶囊…）→ 必须同时出现品类语境词
BRAND_STRONG = {
    "COSMOBOUNTY": r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时胶囊|日活瓶|夜修瓶|CytoVigor|CytoRenova|节律抗衰)",
    # 注意：不要用 \b —— 中文紧跟英文字母时 \b 不成立
    # （"LAIFE红宝瓶" 会漏判，导致正文明明写了品牌却被标成蹭词位）
    "MitoQ": r"(mito\s*q|mitoq)",
    "GRANVER": r"(granver|granve|吉利肝|吉清肺|吉力威|吉瑞维)",
    "LAIFE": r"(laife|乐梵|乐悦泰|乐轻肝|乐维源|乐净肺|乐返盾|乐腺舒|巢汐)",
    "W+端粒塔": r"(w\+\s*端粒塔|端粒塔|w\+\s*duanlta|nmn\s*25000)",
    "塞乐瑞": r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱|赛特茵|愈龄菌|勉益莱)",
}
# 歧义产品词：单独出现不足以归属
BRAND_AMBIGUOUS = {
    "COSMOBOUNTY": r"(分时胶囊|分时抗衰|节律营养)",
    "MitoQ": r"(经典胶囊\s*pure|衡糖胶囊|姜黄素\s*pro|舒心\s*pro|奶蓟草胶囊|明眸胶囊|锐智胶囊|骨赋能胶囊|解压胶囊|净肝胶囊|高纯鱼油)",
    "GRANVER": r"(护肝片|护肺片)",
    "LAIFE": r"(红宝瓶|紫宝瓶|橙宝瓶|肌龄锁|3s\s*女人味胶囊|红气瓶)",
    "W+端粒塔": r"(nmn|端粒)",
    "塞乐瑞": r"(super\s*-?\s*k|巢悦莱|精华倍莱)",
}
# 品类语境词：与歧义产品词同现才算数
CATEGORY_CTX = re.compile(
    r"(抗衰|逆龄|保健|营养|补充剂|胶囊|片剂|补剂|成分|剂量|功效|服用|口碑|测评|种草|"
    r"Healthy|health|Capsule|supplement|Supplement|姜黄|镁|奶蓟|鱼油|NMN|NAD|槲皮素|"
    r"精华|抗氧|细胞|线粒体|护肝|护肺|睡眠|精力|内服|口服|品牌|旗舰)", re.I)
RX = {k: re.compile(v, re.I) for k, v in BRAND_STRONG.items()}
RX_AMBIG = {k: re.compile(v, re.I) for k, v in BRAND_AMBIGUOUS.items()}


def brand_evidence(brand, text):
    """归属判定与讨论深度分级。

    strong —— 正文实质提到品牌（可分析）
    weak   —— 歧义产品词 + 品类语境（需人工确认）
    tag    —— 品牌只出现在话题标签里（蹭词位，不宜当作品牌口碑）
    """
    if not brand or brand == "品类场景词":
        return "strong" if brand == "品类场景词" else None
    # 去掉尾部话题标签块。两种形态都要处理：
    #   #MitoQ三重镁[话题]#   （带 [话题] 后缀）
    #   #MitoQ #抗衰老        （纯 # 标签，来自拟人采集的 DOM 提取）
    # 注意：标签必须出现在「行首或空白之后」才算标签。
    # 否则 "参加了100个Laife女孩的打卡" 里的 "个Laife女孩" 会被当成标签剥掉，
    # 导致正文里明明写了品牌名却被误判成蹭词位。
    body_part = re.split(r"#\s*\S*?\[话题\]", text)[0]
    body_part = re.sub(r"(?:^|\s)#[\w一-鿿+]+", " ", body_part)
    if RX.get(brand) and RX[brand].search(text):
        if RX[brand].search(body_part):
            return "strong"
        return "tag"                                   # 只在标签里出现
    if RX_AMBIG.get(brand) and RX_AMBIG[brand].search(text) and CATEGORY_CTX.search(text):
        return "weak"
    return None

# 关键词 → 品牌（与入库脚本同一口径）
def canon_brand(kw):
    kw = (kw or "").strip()
    if not kw:
        return ""
    low = kw.lower()
    table = [
        ("科诗博缇", "COSMOBOUNTY"), ("黑白分时胶囊", "COSMOBOUNTY"), ("日活瓶", "COSMOBOUNTY"),
        ("夜修瓶", "COSMOBOUNTY"), ("分时抗衰", "COSMOBOUNTY"), ("节律营养", "COSMOBOUNTY"),
        ("cosmo bounty", "COSMOBOUNTY"), ("cosmobounty", "COSMOBOUNTY"), ("cosmobounty", "COSMOBOUNTY"),
        ("mitoq", "MitoQ"), ("granver", "GRANVER"), ("吉利肝", "GRANVER"), ("吉清肺", "GRANVER"),
        ("吉力威", "GRANVER"), ("吉瑞维", "GRANVER"), ("laife", "LAIFE"), ("乐梵", "LAIFE"),
        ("乐悦泰", "LAIFE"), ("乐轻肝", "LAIFE"), ("红宝瓶", "LAIFE"), ("乐维源", "LAIFE"),
        ("乐净肺", "LAIFE"), ("端粒塔", "W+端粒塔"), ("w+duanlta", "W+端粒塔"), ("duanlta", "W+端粒塔"),
        ("nmn 25000", "W+端粒塔"), ("赛乐瑞", "塞乐瑞"), ("塞乐瑞", "塞乐瑞"), ("cellerator", "塞乐瑞"),
        ("stemax", "塞乐瑞"), ("莱复隆", "塞乐瑞"), ("精倍莱", "塞乐瑞"), ("赛特茵", "塞乐瑞"),
        ("愈龄菌", "塞乐瑞"), ("勉益莱", "塞乐瑞"), ("super-k", "塞乐瑞"), ("巢悦莱", "塞乐瑞"),
        ("celerui", "塞乐瑞"), ("精华倍莱", "塞乐瑞"),
        ("衡糖胶囊", "MitoQ"), ("经典胶囊 pure", "MitoQ"), ("经典胶囊pure", "MitoQ"),
        ("姜黄素pro", "MitoQ"), ("舒心pro", "MitoQ"), ("奶蓟草胶囊", "MitoQ"),
        ("明眸胶囊", "MitoQ"), ("锐智胶囊", "MitoQ"), ("骨赋能胶囊", "MitoQ"),
        ("解压胶囊", "MitoQ"), ("净肝胶囊", "MitoQ"), ("高纯鱼油", "MitoQ"),
    ]
    for k, b in table:
        if low == k:
            return b
    for k, b in table:
        if k in low:
            return b
    # 场景/品类词：命中的是泛品类内容，不属于任何品牌，单列为品类内容
    for k in ("熬夜后怎么恢复", "入睡难怎么调理", "睡眠浅怎么办", "下午犯困怎么办",
              "精力差吃什么", "皮肤松弛暗沉怎么调理", "昼夜节律 调理", "分时营养",
              "抗衰胶囊 成分党", "抗衰胶囊 测评 对比", "抗衰胶囊测评对比",
              "nr 和 nmn 哪个好", "分时抗衰 哪个牌子好"):
        if k in low:
            return "品类场景词"
    return ""


# 采集基准日：相对时间（"N天前"）以此回推
COLLECT_DAY = datetime.datetime(2026, 10, 23, 20, 0)


def nid_time(nid):
    """note_id 前 8 位十六进制 = Unix 秒 → 精确发布时间。"""
    try:
        return datetime.datetime.fromtimestamp(int(nid[:8], 16))
    except Exception:
        return None


def norm_pub(v, nid):
    """把各种发布时间写法归一为 'YYYY-MM-DD HH:MM'。

    数据事实（2026-10-24 审计得出）：
      · note_id 前 8 位十六进制 = 创建时间，与平台返回的精确发布时间
        93.5% 落在 ±3 天内、中位差 0.06 天 → 它是权威来源
      · 卡片/详情的 time 字段形态混杂：精确时间戳(13位)、完整日期、
        「月日」MM-DD、「N天前」等
      · 旧实现对「月日」型用 note_id 的年份硬套，导致 57% 的记录偏差 1~13 天

    现在的规则：
      1. 精确时间戳 → 直接用（最权威）
      2. 完整日期 YYYY-MM-DD → 直接用
      3. 相对时间（N天前/N小时前/昨天）→ 以采集日回推
      4. 月日 MM-DD → 只在同月内可信；跨月则用 note_id 的月日
      5. 兜底：note_id 时间戳
    """
    t_nid = nid_time(nid)

    def _fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M") if dt else None

    if v is None:
        return _fmt(t_nid)
    if isinstance(v, (int, float)):
        ts = v / 1000 if v > 1e12 else v
        try:
            return _fmt(datetime.datetime.fromtimestamp(ts))
        except Exception:
            return _fmt(t_nid)
    s = str(v).strip()
    if not s:
        return _fmt(t_nid)

    # ① 精确时间戳（秒/毫秒）
    if re.fullmatch(r"\d{10,13}", s):
        ts = int(s) / 1000 if len(s) > 10 else int(s)
        try:
            return _fmt(datetime.datetime.fromtimestamp(ts))
        except Exception:
            return _fmt(t_nid)

    # ② 完整日期 YYYY-MM-DD[ HH:MM]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?", s)
    if m:
        try:
            d = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                  int(m.group(4) or 0), int(m.group(5) or 0))
            return d.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return _fmt(t_nid)

    # ③ 相对时间（以采集日为基准回推）
    m = re.fullmatch(r"(\d+)天前", s)
    if m:
        base = COLLECT_DAY
        return _fmt(base - datetime.timedelta(days=int(m.group(1))))
    m = re.fullmatch(r"(\d+)小时前", s)
    if m:
        base = COLLECT_DAY
        return _fmt(base - datetime.timedelta(hours=int(m.group(1))))
    if s.startswith("昨天"):
        return _fmt(COLLECT_DAY - datetime.timedelta(days=1))
    if s.startswith("前天"):
        return _fmt(COLLECT_DAY - datetime.timedelta(days=2))

    # ④ 月日 MM-DD：直接用 note_id。
    #    审计发现：月日型 time 与 note_id 的月日只有 52% 一致（卡片可能是「编辑时间」），
    #    而 note_id 与平台精确时间 93.5% 落在 ±3 天内 → 月日型一律以 note_id 为准，
    #    这样才能拿到「同一天 + 精确时分」而非「月日 + 00:00」。
    m = re.match(r"^(\d{2})-(\d{2})", s)
    if m:
        if t_nid:
            return _fmt(t_nid)
        mo, dy = int(m.group(1)), int(m.group(2))
        try:
            return _fmt(datetime.datetime(2026, mo, dy))
        except Exception:
            return None

    return _fmt(t_nid)


def to_int(v):
    try:
        return int(str(v).strip() or 0)
    except Exception:
        return 0


def parse_logs():
    """从批次日志里解析完整搜索响应与笔记详情。"""
    notes = {}
    files = (glob.glob(os.path.join(LOGDIR, "full_xhs_batch*.log"))
             + glob.glob(os.path.join(LOGDIR, "full_xhs*.log"))
             + glob.glob(os.path.join(LOGDIR, "collect_xhs*.log")))
    for path in files:
        try:
            fh = open(path, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                # ① 搜索响应：卡片带图片列表/发布时间标签/达人昵称与 ID
                i = line.find("Search notes response: ")
                if i >= 0:
                    s = line[i + len("Search notes response: "):].strip()
                    if s.startswith("{"):
                        try:
                            resp = ast.literal_eval(s)
                        except Exception:
                            resp = None
                        if resp:
                            for it in (resp.get("items") or []):
                                if not isinstance(it, dict):
                                    continue
                                nid = it.get("id")
                                card = it.get("note_card") or {}
                                if not nid or not card:
                                    continue
                                user = card.get("user") or {}
                                inter = card.get("interact_info") or {}
                                imgs = [x.get("url_default") or x.get("url") or ""
                                        for x in (card.get("image_list") or []) if isinstance(x, dict)]
                                imgs = [u for u in imgs if u]
                                pub = ""
                                for tg in (card.get("corner_tag_info") or []):
                                    if tg.get("type") == "publish_time":
                                        pub = tg.get("text") or ""
                                rec = notes.setdefault(nid, {"note_id": nid})
                                rec.setdefault("title", card.get("display_title") or "")
                                rec["images"] = rec.get("images") or imgs
                                rec["cover"] = (card.get("cover") or {}).get("url_default") or rec.get("cover") or ""
                                rec["nickname"] = user.get("nickname") or user.get("nick_name") or rec.get("nickname") or ""
                                rec["avatar"] = user.get("avatar") or rec.get("avatar") or ""
                                rec["user_id"] = user.get("user_id") or rec.get("user_id") or ""
                                rec["xsec_token"] = it.get("xsec_token") or rec.get("xsec_token") or ""
                                rec["publish_hint"] = pub or rec.get("publish_hint") or ""
                                rec["likes"] = max(rec.get("likes", 0), to_int(inter.get("liked_count")))
                                rec["collects"] = max(rec.get("collects", 0), to_int(inter.get("collected_count")))
                                rec["comments"] = max(rec.get("comments", 0), to_int(inter.get("comment_count")))
                                rec["shares"] = max(rec.get("shares", 0), to_int(inter.get("shared_count")))
                # ② 笔记详情
                i = line.find("[store.xhs.update_xhs_note] xhs note: ")
                if i >= 0:
                    s = line[i + len("[store.xhs.update_xhs_note] xhs note: "):].strip()
                    if s.startswith("{"):
                        try:
                            d = ast.literal_eval(s)
                        except Exception:
                            d = None
                        if isinstance(d, dict) and d.get("note_id"):
                            nid = d["note_id"]
                            rec = notes.setdefault(nid, {"note_id": nid})
                            for k in ("title", "desc", "nickname", "creator_hash", "note_url",
                                      "source_keyword", "type", "video_url"):
                                if d.get(k):
                                    rec[k] = d[k]
                            if d.get("image_list"):
                                im = d["image_list"]
                                im = im if isinstance(im, list) else str(im).split(",")
                                im = [x if isinstance(x, str) else (x.get("url") or "") for x in im]
                                rec["images"] = [u for u in im if u] or rec.get("images", [])
                            if d.get("tag_list"):
                                tl = d["tag_list"]
                                rec["tags"] = parse_tags(tl) or rec.get("tags", [])
                            for src, dst in (("liked_count", "likes"), ("collected_count", "collects"),
                                             ("comment_count", "comments"), ("share_count", "shares")):
                                if d.get(src) is not None:
                                    rec[dst] = max(rec.get(dst, 0), to_int(d[src]))
                            if d.get("time"):
                                # 详情 time 形态混杂：精确时间戳/完整日期/月日/相对时间。
                                # 月日型（MM-DD）会覆盖卡片上的相对时间，反而丢精度 → 只在
                                # 「形态更完整」时覆盖（时间戳 > 完整日期 > 月日 > 相对时间）。
                                new_t = str(d["time"]).strip()
                                old_t = str(rec.get("publish_hint") or "").strip()
                                def _rank(x):
                                    if re.fullmatch(r"\d{10,13}", x):
                                        return 4
                                    if re.match(r"^\d{4}-\d{2}-\d{2}", x):
                                        return 3
                                    if re.match(r"^\d{2}-\d{2}$", x):
                                        return 2
                                    if re.match(r"^\d+[天小]时?前", x) or x.startswith(("昨天", "前天", "今天")):
                                        return 1
                                    return 0
                                if _rank(new_t) >= _rank(old_t):
                                    rec["publish_hint"] = new_t
    return notes


def parse_jsonl(notes):
    """jsonl 里有 source_keyword 等补充信息。"""
    for f in glob.glob(os.path.join(MCJSONL, "search_contents_*.jsonl")):
        for line in open(f, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            nid = d.get("note_id")
            if not nid:
                continue
            rec = notes.setdefault(nid, {"note_id": nid})
            for k in ("title", "desc", "nickname", "note_url", "source_keyword", "type", "video_url"):
                if d.get(k) and not rec.get(k):
                    rec[k] = d[k]
            if d.get("creator_hash") and not rec.get("user_id"):
                rec["user_id"] = d["creator_hash"]
            if d.get("xsec_token") and not rec.get("xsec_token"):
                rec["xsec_token"] = d["xsec_token"]
            if d.get("tag_list") and not rec.get("tags"):
                tl = d["tag_list"]
                rec["tags"] = parse_tags(tl)
            if d.get("image_list") and not rec.get("images"):
                il = d["image_list"]
                il = il if isinstance(il, list) else str(il).split(",")
                rec["images"] = [x if isinstance(x, str) else (x.get("url") or "") for x in il]
            if d.get("time"):
                rec["publish_hint"] = d["time"]
            for src, dst in (("liked_count", "likes"), ("collected_count", "collects"),
                             ("comment_count", "comments"), ("share_count", "shares")):
                if d.get(src) is not None:
                    rec[dst] = max(rec.get(dst, 0), to_int(d[src]))
    return notes


def load_db_prior():
    """从 xhs_note 表读回「此前已采到的高质量字段」。

    归档脚本的原始数据源是批次日志 + jsonl，而拟人采集器（xhs_human_harvest.py）
    直接把新采到的正文/标签/真实昵称写进 xhs_note。
    若只用日志重建，会把这些新数据覆盖掉 —— 所以这里做「数据库优先」的合并：
    数据库里已有的正文/标签/昵称优先级高于日志。
    """
    prior = {}
    if not os.path.exists(DB):
        return prior
    con = sqlite3.connect(DB)
    try:
        for nid, body, tags, author, aid, imgs, vid, pub, likes, coll, cmts, vf in con.execute(
                """SELECT note_id, body, tags_json, author, author_id, images_json,
                          video_url, publish_time, likes, collects, comments, verified
                   FROM xhs_note"""):
            d = {}
            if body:
                d["desc"] = body
            try:
                t = json.loads(tags or "[]")
                if t:
                    d["tags"] = t
            except Exception:
                pass
            if author and "*" not in author:
                d["nickname"] = author
            if aid:
                d["user_id"] = aid
            try:
                im = json.loads(imgs or "[]")
                if im:
                    d["images"] = im
            except Exception:
                pass
            if vid:
                d["video_url"] = vid
            if pub:
                d["publish_hint"] = pub
            if likes:
                d["likes"] = likes
            if coll:
                d["collects"] = coll
            if cmts:
                d["comments"] = cmts
            d["verified"] = vf or 0
            if d:
                prior[nid] = d
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return prior


def parse_tags(tl):
    """解析 tag_list —— 可能是逗号分隔字符串，也可能是 list[dict] / list[str]。"""
    if not tl:
        return []
    if isinstance(tl, str):
        return [x.strip() for x in tl.split(",") if x.strip()]
    if isinstance(tl, list):
        out = []
        for t in tl:
            if isinstance(t, dict):
                n = t.get("name") or t.get("tag_name") or ""
                if n:
                    out.append(str(n).strip())
            elif isinstance(t, str):
                out.append(t.strip())
        return [x for x in out if x]
    return []


def build(notes, db_prior=None):
    """整理每条笔记的最终字段。db_prior 里的字段优先于日志（见 load_db_prior）。"""
    db_prior = db_prior or {}
    out = {}
    for nid, rec in notes.items():
        title = (rec.get("title") or "").strip()
        desc = (rec.get("desc") or "").strip()
        # 数据库优先：拟人采集器新采到的字段优先于日志原始数据
        pri = db_prior.get(nid) or {}
        if pri:
            desc = pri.get("desc") or desc
            title = title or pri.get("desc", "")[:60]
        if not title and not desc:
            continue
        brand = canon_brand(rec.get("source_keyword") or "")
        text = f"{title} {desc}"
        # 早期数据没有 source_keyword（约 1400 条），用正文提及反推品牌
        if not brand:
            hits = {b: len(rx.findall(text)) for b, rx in RX.items() if rx.search(text)}
            if hits:
                brand = max(hits.items(), key=lambda x: x[1])[0]
        # 品牌归属：关键词归属 + 正文真实提及（六家同口径）
        ev = brand_evidence(brand, text)
        mention_ok = ev is not None
        out[nid] = {
            "note_id": nid,
            "title": title[:200],
            "desc": desc,
            "publish_time": norm_pub(pri.get("publish_hint") or rec.get("publish_hint"), nid),
            "author": pri.get("nickname") or rec.get("nickname") or "",
            "avatar": rec.get("avatar") or "",
            "author_id": pri.get("user_id") or rec.get("user_id") or "",
            "images": [u for u in (pri.get("images") or rec.get("images") or []) if u],
            "tags": [t for t in (pri.get("tags") or rec.get("tags") or []) if t],
            "likes": pri.get("likes") or rec.get("likes", 0),
            "collects": pri.get("collects") or rec.get("collects", 0),
            "comments": pri.get("comments") or rec.get("comments", 0),
            "shares": rec.get("shares", 0),
            "source_keyword": rec.get("source_keyword") or "",
            "note_type": rec.get("type") or "",
            "video_url": pri.get("video_url") or rec.get("video_url") or "",
            "brand": brand,
            "brand_mention": mention_ok,
            "evidence": ev or "",
            "verified": pri.get("verified", 0),   # 由 verify_and_gate.py 打标
            "note_url": rec.get("note_url") or f"https://www.xiaohongshu.com/explore/{nid}",
            "xsec_token": rec.get("xsec_token") or "",
        }
    return out


def save_db(arch):
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS xhs_note (
        note_id TEXT PRIMARY KEY, title TEXT, body TEXT, publish_time TEXT,
        author TEXT, author_id TEXT, images_json TEXT, tags_json TEXT,
        likes INTEGER, collects INTEGER, comments INTEGER, shares INTEGER,
        source_keyword TEXT, brand TEXT, brand_mention INTEGER,
        note_url TEXT, xsec_token TEXT, updated_at TEXT)""")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for nid, r in arch.items():
        con.execute("""INSERT OR REPLACE INTO xhs_note
            (note_id,title,body,publish_time,author,author_id,images_json,tags_json,
             likes,collects,comments,shares,source_keyword,brand,brand_mention,note_url,xsec_token,
             note_type,video_url,evidence,verified,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nid, r["title"], r["desc"], r["publish_time"], r["author"], r["author_id"],
             json.dumps(r["images"], ensure_ascii=False), json.dumps(r["tags"], ensure_ascii=False),
             r["likes"], r["collects"], r["comments"], r["shares"], r["source_keyword"],
             r["brand"], 1 if r["brand_mention"] else 0, r["note_url"], r["xsec_token"],
             r.get("note_type") or "", r.get("video_url") or "", r.get("evidence") or "",
            r.get("verified", 0), now))
    # 回填 content 表（品牌维度）：正文/图片/标签/时间/互动数
    n_up = 0
    for nid, r in arch.items():
        if not r["brand_mention"] or not r["brand"]:
            continue
        m = json.dumps({"likes": r["likes"], "collects": r["collects"],
                        "comments": r["comments"], "shares": r["shares"]}, ensure_ascii=False)
        media = json.dumps({"images": r["images"]}, ensure_ascii=False) if r["images"] else None
        cur = con.execute("""UPDATE content SET
                body=COALESCE(NULLIF(body,''), ?),
                publish_time=COALESCE(publish_time, ?),
                author=COALESCE(NULLIF(author,''), ?),
                author_id=COALESCE(NULLIF(author_id,''), ?),
                metrics_json=?, media_json=COALESCE(media_json, ?),
                tags=COALESCE(NULLIF(tags,''), ?)
            WHERE text_hash=?""",
            (r["desc"], r["publish_time"], r["author"], r["author_id"], m, media,
             ",".join(r["tags"]), f"xhs:{nid}"))
        n_up += cur.rowcount
    con.commit()
    con.close()
    return n_up


def load_media_text():
    """OCR/ASR 提取的图片/视频文字 —— 笔记正文之外的第二层信息源。"""
    con = sqlite3.connect(DB)
    out = collections.defaultdict(list)
    try:
        rows = list(con.execute(
            "SELECT c.id, m.kind, m.source, m.text FROM media_text m "
            "JOIN content c ON c.id = m.content_id"))
        for cid, kind, src, txt in rows:
            row = con.execute("SELECT text_hash FROM content WHERE id=?", (cid,)).fetchone()
            if row and txt:
                out[row[0]].append({"kind": kind, "source": src, "text": txt})
    finally:
        con.close()
    return out


def html_page(r):
    """按小红书笔记详情页的真实版式还原。"""
    e = html.escape
    avatar = (f'<img class="ava" src="{e(r["avatar"])}" referrerpolicy="no-referrer">' if r.get("avatar")
              else f'<div class="ava ph">{(e(r["author"]) or "·")[0]}</div>')
    # 图片墙：小红书详情页单图大图，多图两列
    imgs = r["images"][:18]
    if imgs:
        cls = "imgs single" if len(imgs) == 1 else "imgs grid"
        parts = []
        for i, u in enumerate(imgs):
            lf = os.path.join(MEDIA_DIR, f"{r['note_id']}_{i}.jpg")
            local = f"media/{r['note_id']}_{i}.jpg"
            if os.path.exists(lf):
                # 本地已有存档：优先引用本地（离线可看）
                src = local
            else:
                # 未落盘：CDN 直链可能已过期（TTL 约 24h），标注出来避免误以为图裂了
                src = u
            onerr = "this.parentElement.classList.add('imgfail');this.style.display='none'"
            parts.append(f'<a href="{e(u)}" target="_blank">'
                         f'<img loading="lazy" src="{e(src)}" referrerpolicy="no-referrer" '
                         f'onerror="{onerr}"></a>')
        imgs_html = f'<div class="{cls}">{"".join(parts)}</div>'
    else:
        imgs_html = '<div class="noimg">（该笔记图片未能获取：详情接口采集时受风控拦截）</div>'
    tags = "".join(f'<a class="topic" href="#" onclick="return false">{e(t)}</a>' for t in r["tags"][:24])
    body = e(r["desc"]) if r["desc"] else '<span class="muted">（正文未获取到）</span>'
    comments = r.get("comments_list") or []
    if comments:
        cl = "".join(
            f'<div class="cmt"><img class="cava" src="{e(c.get("avatar") or "")}" referrerpolicy="no-referrer">'
            f'<div class="cmain"><div class="cname">{e(c.get("author") or "")}</div>'
            f'<div class="ctext">{e((c.get("body") or "")[:500])}</div>'
            f'<div class="cmeta">{e(str(c.get("publish_time") or ""))} · 赞 {c.get("likes") or 0}</div></div></div>'
            for c in comments[:30])
        cmt_html = f'<div class="cmts"><div class="cmts-h">共 {len(comments)} 条评论</div>{cl}</div>'
    else:
        cmt_html = '<div class="cmts muted">（评论未采集）</div>'
    ocr = ""
    if r.get("media_text"):
        blocks = "".join(
            f'<div class="ocr"><b>[{e(m["source"])}]</b> {e((m["text"] or "")[:1500])}</div>'
            for m in r["media_text"][:8])
        ocr = f'<div class="ocrwrap"><div class="ocr-h">图片 / 视频文字（OCR·ASR 提取）</div>{blocks}</div>'
    vurl = r.get("video_url") or ""
    local_v = os.path.join(MEDIA_DIR, f"{r['note_id']}.mp4")
    if os.path.exists(local_v):
        video_block = ('<video class="vplayer" controls preload="metadata" src="media/%s.mp4"></video>'
                       '<div class="vnote">本地存档视频（原始直链：<a href="%s" target="_blank">CDN</a>）</div>'
                       % (r["note_id"], e(vurl)))
    elif vurl:
        video_block = ('<a class="vlink" href="%s" target="_blank">▶ 视频原文件</a>' % e(vurl))
    else:
        video_block = ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(r['title'][:60] or r['note_id'])} - 小红书</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#fff;color:#333;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
.page{{max-width:640px;margin:0 auto;padding:0 16px 60px}}
.nav{{display:flex;align-items:center;gap:10px;padding:14px 0;border-bottom:1px solid #f0f0f0;position:sticky;top:0;background:#fff;z-index:5}}
.back{{font-size:20px;color:#333;text-decoration:none}} .nav b{{font-weight:600;font-size:16px}}
.author{{display:flex;align-items:center;gap:10px;padding:14px 0 10px}}
.ava{{width:38px;height:38px;border-radius:50%;object-fit:cover;background:#f0f0f0;flex:0 0 auto}}
.ava.ph{{display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px;background:#ff2e4d}}
.aname{{font-size:15px;font-weight:500}}
.aid{{font-size:12px;color:#999;margin-top:2px}}
.follow{{margin-left:auto;border:1px solid #ff2e4d;color:#ff2e4d;background:#fff;border-radius:14px;padding:4px 14px;font-size:13px}}
h1{{font-size:17px;line-height:1.5;margin:6px 0 10px;font-weight:600}}
.txt{{font-size:15px;line-height:1.75;white-space:pre-wrap;word-break:break-word}}
.topic{{color:#13386c;text-decoration:none;font-size:15px;margin-right:8px}}
.meta{{color:#999;font-size:12px;margin:10px 0 16px}}
.imgs{{margin:14px 0 18px}} .imgs img{{width:100%;display:block;border-radius:6px}}
.imgs.grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px}} .imgs.grid img{{height:210px;object-fit:cover}}
.noimg{{background:#f7f7f9;color:#aaa;font-size:13px;padding:26px;text-align:center;border-radius:8px;margin:14px 0}}
.imgs.imgfail:after{{content:"（部分图片 CDN 直链已过期，未落盘部分无法显示）";display:block;font-size:12px;color:#aaa;padding:10px;text-align:center}}
.imgs.grid.imgfail{{grid-template-columns:1fr}}
.bar{{display:flex;gap:28px;align-items:center;padding:14px 0;border-top:1px solid #f0f0f0;color:#666;font-size:13px}}
.bar span{{display:flex;align-items:center;gap:5px}}
.cmts{{margin-top:22px;border-top:1px solid #f0f0f0;padding-top:14px}}
.cmts-h{{font-size:14px;font-weight:600;margin-bottom:12px}}
.cmt{{display:flex;gap:10px;margin-bottom:16px}}
.cava{{width:28px;height:28px;border-radius:50%;background:#eee;object-fit:cover;flex:0 0 auto}}
.cname{{font-size:13px;color:#999}} .ctext{{font-size:14px;line-height:1.6;margin:3px 0}}
.cmeta{{font-size:11px;color:#bbb}}
.ocrwrap{{background:#f7f9fc;border-radius:8px;padding:12px 14px;margin:16px 0}}
.ocr-h{{font-size:13px;font-weight:600;color:#555;margin-bottom:8px}}
.ocr{{font-size:12.5px;line-height:1.7;color:#556;white-space:pre-wrap;background:#fff;border-left:3px solid #b6cdf5;padding:8px 10px;margin:6px 0;border-radius:4px}}
.foot{{margin-top:28px;padding-top:14px;border-top:1px solid #f0f0f0;color:#aaa;font-size:12px;line-height:1.8}}
.muted{{color:#aaa}}
.tag-ev{{color:#0a7c42;margin:0 4px}} .tag-evw{{color:#c47f00;margin:0 4px}} .tag-evt{{color:#999;margin:0 4px}}
.vlink{{display:inline-block;margin:8px 0;padding:6px 12px;background:#ff2e4d;color:#fff;border-radius:14px;font-size:13px;text-decoration:none}}
.vplayer{{width:100%;max-height:520px;background:#000;border-radius:8px;margin:10px 0}}
.vnote{{font-size:12px;color:#999;margin-bottom:10px}}
</style></head><body><div class="page">
<div class="nav"><a class="back" href="index.html">‹</a><b>笔记</b></div>
<div class="author">{avatar}
 <div><div class="aname">{e(r["author"] or "未知作者")}</div>
 <div class="aid">小红书号：{e(r["author_id"][:12] or "-")}</div></div>
 <button class="follow">关注</button></div>
<h1>{e(r["title"] or "(无标题)")}</h1>
<div class="txt">{body}</div>
<div class="meta">{e(str(r["publish_time"] or "-"))} · 归属 {e(r["brand"] or "-")}"
 {("<span class=tag-ev>正文实质提及</span>" if r.get("evidence")=="strong" else ("<span class=tag-evw>弱证据·需确认</span>" if r.get("evidence")=="weak" else ("<span class=tag-evt>仅标签提及·蹭词位</span>" if r.get("evidence")=="tag" else "")))}
 · 采集自「{e(r["source_keyword"] or "-")}」</div>
{imgs_html}
{video_block}
{ocr}
<div class="bar">
 <span>♡ 赞 {r["likes"]}</span><span>☆ 收藏 {r["collects"]}</span>
 <span>💬 评论 {r["comments"]}</span><span>↗ 分享 {r["shares"]}</span>
</div>
{cmt_html}
<div class="foot">
本页面为小红书笔记的<b>本地存档</b>（抓取时间 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}）。<br>
原文链接：<a href="{e(r["note_url"])}" target="_blank" style="color:#13386c">{e(r["note_url"])}</a><br>
图片为小红书 CDN 直链，需联网且带 referer 才能显示；失效时点击可尝试打开原图。
</div>
</div></body></html>"""


def load_comments():
    """按笔记载入评论（小红书评论目前尚未采集，抖音评论另存）。"""
    con = sqlite3.connect(DB)
    out = collections.defaultdict(list)
    try:
        for th, body, likes, pt in con.execute(
                "SELECT t.text_hash, cc.body, cc.like_count, cc.publish_time FROM comment cc "
                "JOIN content t ON t.id = cc.content_id WHERE t.platform='xhs'"):
            out[th].append({"body": body, "likes": likes, "publish_time": pt})
    finally:
        con.close()
    return out


def build_html(arch, limit=None):
    os.makedirs(HTML_DIR, exist_ok=True)
    mt = load_media_text()
    cmt = load_comments()
    for nid, r in arch.items():
        if mt.get(f"xhs:{nid}"):
            r["media_text"] = mt[f"xhs:{nid}"]
        if cmt.get(f"xhs:{nid}"):
            r["comments_list"] = cmt[f"xhs:{nid}"]
    # 对外只展示已校验通过的内容（用户规则：未校验的不对外展示）
    arch = {k: v for k, v in arch.items() if v.get("verified", 0) == 1}
    items = sorted(arch.values(), key=lambda r: r["publish_time"] or "", reverse=True)
    if limit:
        items = items[:limit]
    for r in items:
        with open(os.path.join(HTML_DIR, f"{r['note_id']}.html"), "w", encoding="utf-8") as f:
            f.write(html_page(r))
    # 索引页
    rows = []
    for r in items:
        ev = r.get("evidence") or ""
        ev_txt = {"strong": "强", "weak": "弱", "tag": "蹭词"}.get(ev, "")
        has_v = os.path.exists(os.path.join(MEDIA_DIR, f"{r['note_id']}.mp4"))
        n_img_local = sum(1 for i in range(len(r["images"][:18]))
                          if os.path.exists(os.path.join(MEDIA_DIR, f"{r['note_id']}_{i}.jpg")))
        local_flag = (f'<span class=lv>视频</span>' if has_v else "") + \
                    (f'<span class=li>图×{n_img_local}</span>' if n_img_local else "")
        typ = "视频" if (r.get("note_type") == "video" or r.get("video_url")) else "图文"
        rows.append(
            f'<tr data-brand="{html.escape(r["brand"] or "")}" data-kw="{html.escape(r["source_keyword"] or "")}" '
            f'data-ev="{ev}" data-own="{"1" if r["brand_mention"] else "0"}" data-typ="{typ}">'
            f'<td>{html.escape(str(r["publish_time"] or "-"))}</td>'
            f'<td><a href="{r["note_id"]}.html" target="_blank">{html.escape((r["title"] or r["note_id"])[:42])}</a>'
            f'<span class="ty">{typ}</span></td>'
            f'<td>{html.escape(r["author"] or "-")}</td>'
            f'<td>{html.escape(r["brand"] or "-")}'
            f'{"<b class=ev>强</b>" if ev == "strong" else ("<b class=evw>弱</b>" if ev == "weak" else "")}</td>'
            f'<td>{r["likes"]}</td><td>{r["collects"]}</td><td>{r["comments"]}</td>'
            f'<td>{len(r["images"])}</td><td>{html.escape(r["source_keyword"] or "-")}</td>'
            f'<td>{local_flag}</td></tr>')
    index = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>小红书原文存档索引（{len(items)} 条）</title><style>
 body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f7f7f9;padding:24px;color:#222}}
 h1{{font-size:20px}} .bar{{margin:14px 0}} input,select{{padding:7px 10px;border:1px solid #ddd;border-radius:6px;margin-right:8px;font-size:13px}}
 table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}
 th,td{{padding:8px 10px;border-bottom:1px solid #f0f0f0;text-align:left}}
 th{{background:#fafafa;position:sticky;top:0}} a{{color:#2a6ef4;text-decoration:none}}
 .ty{{display:inline-block;font-size:11px;color:#999;border:1px solid #ddd;border-radius:3px;padding:0 4px;margin-left:6px}}
 .ev{{color:#0a7c42;font-size:11px;margin-left:5px}} .evw{{color:#c47f00;font-size:11px;margin-left:5px}} .evt{{color:#999;font-size:11px;margin-left:5px}}
.lv{{display:inline-block;font-size:11px;color:#fff;background:#ff2e4d;border-radius:3px;padding:0 4px;margin-right:3px}}
.li{{display:inline-block;font-size:11px;color:#0a7c42;border:1px solid #0a7c42;border-radius:3px;padding:0 4px}}
 .ck{{font-size:13px;color:#555;margin-right:10px;cursor:pointer}}
</style></head><body>
<h1>小红书原文存档索引 · 共 {len(items)} 条</h1>
<div class="bar"><input id="q" placeholder="搜索标题/作者/关键词" style="width:230px">
<select id="b"><option value="">全部品牌</option>{''.join(f'<option>{b}</option>' for b in sorted(set(r['brand'] for r in items if r['brand'])))}</select>
<select id="t"><option value="">图文+视频</option><option value="视频">仅视频</option><option value="图文">仅图文</option></select>
<label class="ck"><input type="checkbox" id="own" checked> 只看已归属品牌内容</label>
<label class="ck"><input type="checkbox" id="strong"> 只看强证据</label>
<span id="cnt"></span></div>
<table><thead><tr><th>发布时间</th><th>标题</th><th>作者</th><th>归属/证据</th><th>赞</th><th>藏</th><th>评</th><th>图</th><th>采集词</th><th>本地存档</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<script>
const q=document.getElementById('q'),b=document.getElementById('b'),t=document.getElementById('t'),
      own=document.getElementById('own'),st=document.getElementById('strong'),tb=document.querySelector('tbody');
function f(){{const s=q.value.toLowerCase(),bs=b.value,ts=t.value,ob=own.checked,sb=st.checked;let n=0;
 tb.querySelectorAll('tr').forEach(tr=>{{
   const ok=(!s||tr.innerText.toLowerCase().includes(s))&&(!bs||tr.dataset.brand===bs)
     &&(!ts||tr.dataset.typ===ts)&&(!ob||tr.dataset.own==='1')&&(!sb||tr.dataset.ev==='strong');
   tr.style.display=ok?'':'none';if(ok)n++;}});
 document.getElementById('cnt').textContent='  显示 '+n+' / {len(items)} 条';}}
q.oninput=f;b.onchange=f;t.onchange=f;own.onchange=f;st.onchange=f;f();
</script></body></html>"""
    with open(os.path.join(HTML_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)
    return len(items)


def stats(arch):
    n = len(arch)
    has_body = sum(1 for r in arch.values() if r["desc"])
    has_img = sum(1 for r in arch.values() if r["images"])
    has_tag = sum(1 for r in arch.values() if r["tags"])
    has_time = sum(1 for r in arch.values() if r["publish_time"])
    has_author = sum(1 for r in arch.values() if r["author"])
    brand_ok = sum(1 for r in arch.values() if r["brand_mention"])
    with_likes = sum(1 for r in arch.values() if r["likes"] > 0)
    print(f"=== 归档完整度（{n} 条）===")
    for label, v in (("发布时间", has_time), ("作者昵称", has_author), ("正文", has_body),
                     ("图片列表", has_img), ("话题标签", has_tag), ("点赞>0", with_likes),
                     ("品牌正文可核", brand_ok)):
        print(f"  {label:12s} {v:5d}  ({100*v/max(n,1):5.1f}%)")
    kw = collections.Counter(r["source_keyword"] for r in arch.values())
    print(f"\n  覆盖关键词 {len(kw)} 个；品牌归属分布：")
    bd = collections.Counter(r["brand"] or "（未归属）" for r in arch.values())
    for k, v in bd.most_common():
        print(f"    {k:14s}{v:5d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--html-limit", type=int, default=0, help="只生成前 N 条页面，0=全部")
    args = ap.parse_args()

    notes = parse_logs()
    print(f"日志解析到 {len(notes)} 条笔记")
    notes = parse_jsonl(notes)
    print(f"合并 jsonl 后 {len(notes)} 条")
    prior = load_db_prior()
    print(f"数据库已有高质量字段 {len(prior)} 条（将优先于日志）")
    arch = build(notes, prior)
    print(f"有效笔记 {len(arch)} 条")

    stats(arch)
    if not args.build:
        return

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(os.path.join(ARCHIVE_DIR, "notes.jsonl"), "w", encoding="utf-8") as f:
        for nid, r in arch.items():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n档案已写: {ARCHIVE_DIR}/notes.jsonl")

    n_up = save_db(arch)
    print(f"xhs_note 表已重建，content 回填 {n_up} 条")

    if not args.no_html:
        k = build_html(arch, args.html_limit or None)
        # 清理上一轮遗留的页面：只保留本次已校验内容对应的页面
        keep = {f"{nid}.html" for nid, r in arch.items() if r.get("verified", 0) == 1}
        removed = 0
        for fn in os.listdir(HTML_DIR):
            if not fn.endswith(".html"):
                continue
            if fn == "index.html" or fn.endswith("索引.html"):
                continue
            if fn not in keep:
                try:
                    os.remove(os.path.join(HTML_DIR, fn))
                    removed += 1
                except OSError:
                    pass
        print(f"原文页面已生成 {k} 个 → {HTML_DIR}/index.html"
              + (f"（清理未校验的旧页面 {removed} 个）" if removed else ""))


if __name__ == "__main__":
    main()
