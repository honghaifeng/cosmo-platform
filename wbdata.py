#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一数据层（工作台 v4 / 逆龄观察 / 星河共用）

为什么要有这个模块：
  工作台 v4 原来是「构建期把 SQLite 烤成 list-*.js / data.js 静态快照」，
  数据一变就得重跑脚本 + 重新 rsync，线上很容易看到昨天的数字。
  这里把「现算」这套逻辑抽成一个按请求执行的数据层，所有前端都从它拿数据，
  三个项目共用同一份 DB、同一份知识库、同一套七牛媒体映射 —— 不再各存一份。

口径（与 crawler/build_workbench_data.py 完全一致，别改）：
  · 只算 verified=1
  · 官号 = tags 含 official
  · 点赞取 metrics_json.likes
  · 品类场景词是独立轨道，不进品牌横评

对外接口：
  bundle()            品牌汇总 / 平台官达 / 达人榜 / 内容摘要
  list_rows(brand)    该品牌全部已验讫内容（精简元组，前端列表用）
  detail_map(brand)   内容详情（正文/媒体/指标/评论/归属证据）
  media_map()         本地相对路径 -> 七牛 CDN 绝对地址
"""
import collections
import glob
import json
import os
import re
import sqlite3
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _detect_root(here):
    """项目根探测：两种布局都必须活着。

    · 部署布局  /data/apps/cosmobounty/service/wbdata.py  → 根在上一层
    · 仓库布局  <repo>/wbdata.py                          → 根就是自己

    原来写死 dirname(HERE)，部署的时候是对的；一旦按仓库布局直接跑，
    ROOT 会指到仓库的父目录（比如 Desktop/Demo），
    DB / 知识库 / 导出路径全部拼错。
    判据不用 data/（两边都可能有：部署时 service/data/ 确实存在），
    改用只有仓库才有的 crawler/ 和 workbench/。
    """
    for d in ("crawler", "workbench"):
        if os.path.isdir(os.path.join(here, d)):
            return here
    return os.path.dirname(here)


ROOT = os.environ.get("COSMOBOUNTY_ROOT") or _detect_root(HERE)


def _pick(*cands):
    """按顺序取第一个存在的路径。

    这个模块原来假定 ROOT 是同级的兄弟目录（CosmoBounty/config/brands.json）。
    换到 monorepo 布局后 ROOT 变成仓库根、资源在 base/ 下面，路径就全断了 ——
    表现是工作台 /api/wb/data.js 直接 500，报 FileNotFoundError。
    所以这里不再假定单一布局，按候选顺序找。
    """
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0]


DB = os.environ.get("COSMOBOUNTY_DB") or _pick(
    os.path.join(ROOT, "db", "cosmobounty.db"),
    os.path.join(HERE, "db", "cosmobounty.db"),
    os.path.join(HERE, "data", "cosmobounty.db"),
)
MEDIA_DIR = os.environ.get("COSMOBOUNTY_MEDIA") or _pick(
    os.path.join(ROOT, "knowledge", "media"),
    os.path.join(HERE, "knowledge", "media"),
)
CFG = os.environ.get("COSMOBOUNTY_BRANDS") or _pick(
    os.path.join(ROOT, "config", "brands.json"),
    os.path.join(HERE, "config", "brands.json"),
)
CDN_MAP_FILE = os.environ.get("COSMOBOUNTY_CDN_MAP") or _pick(
    os.path.join(ROOT, "data", "media_cdn.json"),
    os.path.join(HERE, "data", "media_cdn.json"),
)

CN = {"LAIFE": "乐梵", "MitoQ": "MitoQ", "GRANVER": "GRANVER",
      "塞乐瑞": "塞乐瑞", "W+端粒塔": "W+端粒塔", "COSMOBOUNTY": "科诗博缇"}
SLUG = {"MitoQ": "mitoq", "LAIFE": "laife", "GRANVER": "granver",
        "塞乐瑞": "sailerui", "W+端粒塔": "duanlita", "COSMOBOUNTY": "cosmobounty",
        "__category": "category"}
BRAND_OF = {"__category": "品类场景词"}
SLUG_TO_BRAND = {v: k for k, v in SLUG.items()}
PLAT_CN = {"xhs": "小红书", "douyin": "抖音", "shipinhao": "视频号",
           "baidu": "百度", "toutiao": "今日头条", "web": "新闻源"}
CAT_WORDS = [("护肝", ["护肝", "肝", "解酒"]), ("抗衰", ["抗衰", "NMN", "nad", "端粒", "线粒体"]),
             ("心血管", ["心", "辅酶", "Q10", "血压"]), ("口腔", ["皓齿", "口腔", "牙"]),
             ("肺部", ["肺", "清肺"]), ("卵巢", ["卵巢", "养巢", "巢"]),
             ("免疫", ["免疫", "抵抗"]), ("男性", ["男性", "精力", "睾酮"]),
             ("睡眠", ["睡眠", "助眠"]), ("肠道", ["肠道", "益生菌"])]

_LOCK = threading.Lock()
_CACHE = {}


# ------------------------------------------------------------------ 基础
def cdn_base():
    """七牛 CDN 基址。改这里/环境变量 CB_CDN 就能整体换域名，页面不用动。

    注意：七牛测试域名（*.clouddn.com）只支持 HTTP，写 https 一律连不上。
    所以哪怕是 https:// 配置进来，也要按 http 输出 —— 这是踩过坑的硬约束。
    """
    b = (os.environ.get("CB_CDN") or "").strip().rstrip("/")
    if not b:
        try:
            cfg = json.load(open(os.path.join(ROOT, "config", "qiniu.json"), encoding="utf-8"))
            b = (cfg.get("domain") or "").strip().rstrip("/")
        except Exception:
            b = ""
    if not b:
        b = "http://tni044vir.hn-bkt.clouddn.com"
    if "clouddn.com" in b:
        b = re.sub(r"^https?://", "http://", b)
    return b


def _cached(key, ttl, fn):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _LOCK:
        _CACHE[key] = (now, val)
    return val


def cache_clear():
    with _LOCK:
        _CACHE.clear()


def connect():
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def likes_of(mj):
    try:
        return int(json.loads(mj or "{}").get("likes") or 0)
    except Exception:
        return 0


def metrics_of(mj):
    try:
        d = json.loads(mj or "{}")
    except Exception:
        return 0, 0, 0, 0
    g = lambda k: int(d.get(k) or 0)
    return g("likes"), g("collects"), g("comments"), g("shares")


def is_off(r):
    return "official" in (r.get("tags") or "")


def bucket_of(l):
    if l >= 10000:
        return "≥1w"
    if l >= 1000:
        return "1k-1w"
    if l >= 100:
        return "100-1k"
    return "<100"


def tri(rows):
    """[条数, 均赞, 爆文数]"""
    L = [likes_of(r["metrics_json"]) for r in rows]
    n = len(L)
    return [n, round(sum(L) / n) if n else 0, sum(1 for x in L if x >= 1000)]


# ------------------------------------------------------------------ 媒体
def media_map():
    """本地相对路径 -> 七牛地址。基址统一从 cdn_base() 取，换域名不动映射表。"""
    def build():
        raw = {}
        try:
            raw = json.load(open(CDN_MAP_FILE, encoding="utf-8"))
        except Exception:
            raw = {}
        base = cdn_base()
        out = {}
        for k, v in raw.items():
            if not isinstance(v, str):
                continue
            m = re.match(r"^https?://[^/]+(/.*)$", v)
            out[k] = (base + m.group(1)) if m else (base + "/" + v.lstrip("/"))
        return out
    return _cached("media_map", 300, build)


def local_media_index():
    """(品牌目录, 平台, 内容ID) -> [(相对路径, 'i'/'v', 子目录)]

    来源改为「七牛映射表 + 本地磁盘」，不再只扫磁盘：
    服务器上不落地 16.9GB 媒体（都在七牛），只扫磁盘的话封面/视频会全判成"无素材"。
    映射表的 key 就是媒体相对路径，天然带品牌目录 —— 同一内容 ID 绝不跨品牌串素材。
    """
    def build():
        idx = collections.defaultdict(list)
        rels = set()
        try:
            rels |= set(json.load(open(CDN_MAP_FILE, encoding="utf-8")).keys())
        except Exception:
            pass
        if os.path.isdir(MEDIA_DIR):  # 本地开发环境：补上还没上云的文件
            for root, _dirs, fs in os.walk(MEDIA_DIR):
                for f in fs:
                    if f.startswith(".") or f.endswith(".json"):
                        continue
                    rels.add(os.path.relpath(os.path.join(root, f), MEDIA_DIR).replace(os.sep, "/"))
        rx = re.compile(r"^(douyin|xhs|baidu|toutiao|web|bili|wx)[:_](.+)\.(jpg|jpeg|png|webp|mp4|m4s)$", re.I)
        for rel in rels:
            parts = rel.split("/")
            if len(parts) < 3:
                continue
            m = rx.match(parts[-1])
            if not m:
                continue
            plat, cid, ext = m.group(1).lower(), m.group(2), m.group(3).lower()
            cid = re.sub(r"_\d+$", "", cid)
            idx[(parts[0], plat, cid)].append((rel, "v" if ext in ("mp4", "m4s") else "i", parts[1]))
        # 同一张首图存过两份（xxx.jpg / xxx_0.jpg）时只保留一份
        for k, v in list(idx.items()):
            if len(v) < 2:
                continue
            e0 = {os.path.splitext(x[0])[1].lower() for x in v if re.search(r"_0\.[a-z0-9]+$", x[0], re.I)}
            if e0:
                idx[k] = [x for x in v if re.search(r"_\d+\.[a-z0-9]+$", x[0], re.I)
                          or os.path.splitext(x[0])[1].lower() not in e0]
        return dict(idx)
    return _cached("media_idx", 600, build)


def cid_of(row):
    for src in (row["url"] or "", row["raw_path"] or ""):
        m = re.search(r"(\d{15,20}|[0-9a-f]{24})", src)
        if m:
            return m.group(1)
    return None


# ------------------------------------------------------------------ 汇总
def bundle():
    def build():
        cfg = {b["name"]: b for b in json.load(open(CFG, encoding="utf-8"))["brands"]}
        conn = connect()
        brands, plat_data, creators, official, contents = {}, {}, {}, {}, {}
        try:
            for brand, cn in CN.items():
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM content WHERE brand=? AND verified=1", (brand,))]
                tot = conn.execute("SELECT count(*) FROM content WHERE brand=?", (brand,)).fetchone()[0]
                L = [likes_of(r["metrics_json"]) for r in rows]
                off = [r for r in rows if is_off(r)]
                kol = [r for r in rows if not is_off(r)]

                p_all = collections.Counter(r["platform"] for r in conn.execute(
                    "SELECT platform FROM content WHERE brand=?", (brand,)))
                p_v = collections.Counter(r["platform"] for r in rows)
                p_off = collections.Counter(r["platform"] for r in off)

                pd = {}
                for p in set(p_v):
                    pd[p] = {}
                    for key, grp in (("off", off), ("kol", kol)):
                        g = [r for r in grp if r["platform"] == p]
                        if g:
                            pd[p][key] = tri(g)
                plat_data[brand] = pd

                def board(grp):
                    agg = {}
                    for r in grp:
                        a = (r["author"] or "?").strip() or "?"
                        l = likes_of(r["metrics_json"])
                        v = agg.setdefault(a, {"n": 0, "sum": 0, "max": 0, "last": "",
                                               "plat": collections.Counter()})
                        v["n"] += 1
                        v["sum"] += l
                        v["max"] = max(v["max"], l)
                        v["last"] = max(v["last"], (r["publish_time"] or "")[:10])
                        v["plat"][r["platform"]] += 1
                    out = [{"a": a, "n": v["n"], "avg": round(v["sum"] / v["n"]) if v["n"] else 0,
                            "max": v["max"], "p": (v["plat"].most_common(1) or [("", 0)])[0][0],
                            "last": v["last"]} for a, v in agg.items()]
                    out.sort(key=lambda x: (-x["n"], -x["max"]))
                    return out

                creators[brand] = board(kol)
                official[brand] = [{"a": x["a"], "n": x["n"], "avg": x["avg"], "max": x["max"]}
                                   for x in board(off)]

                months = collections.Counter((r["publish_time"] or "")[:7] for r in rows if r["publish_time"])
                bk = collections.Counter(bucket_of(x) for x in L)
                prods = cfg.get(brand, {}).get("products", []) or []
                prod_kw = {}
                for name in prods:
                    core = re.sub(r"\s+", "", name)
                    hit = sum(1 for r in rows if core and core.lower()
                              in ((r["title"] or "") + (r["body"] or "")).lower())
                    if hit:
                        prod_kw[name] = hit
                prod_kw = dict(sorted(prod_kw.items(), key=lambda x: -x[1]))

                prodline = []
                for name, n in prod_kw.items():
                    core = re.sub(r"\s+", "", name).lower()
                    grp = [r for r in rows if core and core in ((r["title"] or "") + (r["body"] or "")).lower()]
                    plats = collections.Counter(r["platform"] for r in grp)
                    plat_s = " + ".join(PLAT_CN.get(p, p) for p, _ in plats.most_common(2)) or "—"
                    off_hit = collections.Counter(r["author"] for r in grp if is_off(r))
                    acc = "、".join(f"{a}（{c}）" for a, c in off_hit.most_common(2)) or "—"
                    cat = "—"
                    for lb, ws in CAT_WORDS:
                        if any(w.lower() in name.lower() for w in ws):
                            cat = lb
                            break
                    prodline.append([name, n, plat_s, acc, f"标题/正文命中 {n} 次", cat])

                brands[brand] = {
                    "name": cn, "total": tot, "verified": len(rows),
                    "platform": dict(p_all), "platform_v": dict(p_v), "platform_off": dict(p_off),
                    "official_n": len(off), "official_acc": len({(r["author"] or "") for r in off}),
                    "official_top": [[a, c] for a, c in collections.Counter(
                        r["author"] for r in off).most_common(6)],
                    "creators": len({(r["author"] or "") for r in kol}),
                    "likes_sum": sum(L), "likes_avg": round(sum(L) / len(L)) if L else 0,
                    "likes_max": max(L) if L else 0,
                    "viral1k": sum(1 for x in L if x >= 1000), "viral10k": sum(1 for x in L if x >= 10000),
                    "months": dict(sorted(months.items())), "prod_kw": prod_kw, "buckets": dict(bk),
                    "prodline": prodline,
                }
                o_n, o_avg, o_vir = tri(off)
                k_n, k_avg, k_vir = tri(kol)
                verdict = ("无官号阵地" if o_n == 0 else
                           "达人强于官号" if k_avg > o_avg * 1.5 else
                           "官号更有效率" if o_avg >= k_avg else "官达相当")
                brands[brand]["eff"] = {"off_n": o_n, "off_avg": o_avg, "off_viral": o_vir,
                                        "kol_n": k_n, "kol_avg": k_avg, "kol_viral": k_vir,
                                        "verdict": verdict, "vkey": "kol" if verdict == "达人强于官号" else "none"}

                srt = sorted(rows, key=lambda r: -likes_of(r["metrics_json"]))
                rec = sorted(rows, key=lambda r: (r["publish_time"] or ""), reverse=True)

                def brief(r):
                    return {"t": (r["title"] or "")[:70], "p": r["platform"], "a": r["author"] or "",
                            "l": likes_of(r["metrics_json"]), "d": (r["publish_time"] or "")[:10],
                            "u": r["url"] or "", "o": is_off(r)}
                contents[brand] = {"top": [brief(r) for r in srt[:12]],
                                   "recent": [brief(r) for r in rec[:12]], "n": len(rows)}

            cat_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM content WHERE brand='品类场景词' AND verified=1")]
            CL = [likes_of(r["metrics_json"]) for r in cat_rows]
            category = {"total": len(cat_rows), "likes_sum": sum(CL),
                        "likes_avg": round(sum(CL) / len(CL)) if CL else 0,
                        "viral1k": sum(1 for x in CL if x >= 1000),
                        "note": "泛品类场景词命中内容（非品牌归属），作为赛道流量参照，不进品牌横评"}
            contents["品类场景词"] = {
                "top": [{"t": (r["title"] or "")[:70], "p": r["platform"], "a": r["author"] or "",
                         "l": likes_of(r["metrics_json"]), "d": (r["publish_time"] or "")[:10],
                         "u": r["url"] or "", "o": False}
                        for r in sorted(cat_rows, key=lambda r: -likes_of(r["metrics_json"]))[:12]],
                "recent": [], "n": len(cat_rows)}

            gen = conn.execute("SELECT max(collect_time) FROM content").fetchone()[0] or ""
            data = {"generated": gen[:10], "brands": brands, "category": category,
                    "source": "live", "cdn": cdn_base()}
        finally:
            conn.close()
        return {"data": data, "plat": plat_data, "people": {"creators": creators, "official": official},
                "contents": contents}
    return _cached("bundle", 120, build)


# ------------------------------------------------------------------ 明细
def list_rows(slug):
    """品牌精简列表：与 list-<slug>.js 的元组格式完全一致。"""
    def build():
        brand = SLUG_TO_BRAND.get(slug)
        if not brand:
            return []
        dir_brand = BRAND_OF.get(brand, brand)
        midx = local_media_index()
        conn = connect()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM content WHERE brand=? AND verified=1", (dir_brand,))]
        finally:
            conn.close()
        rows.sort(key=lambda r: -likes_of(r["metrics_json"]))
        out = []
        for r in rows:
            lk = likes_of(r["metrics_json"])
            cid = cid_of(r)
            media = midx.get((dir_brand, r["platform"], cid), []) if cid else []
            video = any(m[1] == "v" for m in media)
            img = any(m[1] == "i" for m in media)
            cov = ""
            if media:
                cv = [m for m in media if m[1] == "i" and "/cover/" in m[0]]
                anyimg = [m for m in media if m[1] == "i"]
                cov = (cv or anyimg or [[""]])[0][0]
            out.append([r["id"], (r["title"] or "")[:80], r["platform"], r["author"] or "",
                        lk, (r["publish_time"] or "")[:10],
                        1 if "official" in (r["tags"] or "") else 0,
                        2 if video else (1 if img else 0), r["url"] or "", cov])
        return out
    return _cached("list:" + slug, 120, build)


def detail_map(slug):
    """内容详情：正文全文 + 媒体（七牛）+ 指标 + 评论 + 归属证据。"""
    def build():
        brand = SLUG_TO_BRAND.get(slug)
        if not brand:
            return {}
        dir_brand = BRAND_OF.get(brand, brand)
        midx = local_media_index()
        mm = media_map()
        conn = connect()
        try:
            comm = collections.defaultdict(list)
            for r in conn.execute("SELECT content_id, body, like_count, publish_time FROM comment "
                                  "ORDER BY like_count DESC"):
                comm[r["content_id"]].append([(r["body"] or "")[:400], r["like_count"] or 0,
                                              (r["publish_time"] or "")[:10]])
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM content WHERE brand=? AND verified=1", (dir_brand,))]
        finally:
            conn.close()

        det = {}
        for r in rows:
            lk, col, cm, sh = metrics_of(r["metrics_json"])
            cid = cid_of(r)
            media = midx.get((dir_brand, r["platform"], cid), []) if cid else []
            loc = [[m[0], m[1], mm.get(m[0], "")] for m in media]
            cdn = []
            try:
                mj = json.loads(r["media_json"] or "{}")
                if isinstance(mj.get("images"), list):
                    cdn += [u for u in mj["images"] if isinstance(u, str)][:6]
                if isinstance(mj.get("video"), str):
                    cdn.append(mj["video"])
            except Exception:
                pass
            cs = comm.get(r["id"], [])
            det[str(r["id"])] = {
                "b": brand, "p": r["platform"], "pc": PLAT_CN.get(r["platform"], r["platform"]),
                "t": r["title"] or "", "body": r["body"] or "",
                "a": r["author"] or "", "aid": r["author_id"] or "",
                "d": r["publish_time"] or "", "ct": (r["collect_time"] or "")[:10],
                "u": r["url"] or "",
                "m": {"likes": lk, "collects": col, "comments": cm, "shares": sh},
                "tags": [t.strip() for t in (r["tags"] or "").split(",") if t.strip()],
                "off": 1 if "official" in (r["tags"] or "") else 0,
                "vn": r["verify_note"] or "", "typ": r["content_type"] or "",
                "loc": loc, "cdn": cdn, "cm": cs[:40], "cmn": len(cs),
            }
        return det
    return _cached("detail:" + slug, 300, build)


def pending_map():
    """待核队列（verified=0）：每品牌条数 + 最近样本，review 页用。"""
    def build():
        conn = connect()
        out = {}
        try:
            for brand in list(CN) + ["品类场景词"]:
                n = conn.execute("SELECT count(*) FROM content WHERE brand=? AND (verified=0 OR verified IS NULL)",
                                 (brand,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT title, platform, author, publish_time, url, verify_note FROM content "
                    "WHERE brand=? AND (verified=0 OR verified IS NULL) "
                    "ORDER BY collect_time DESC LIMIT 12", (brand,))
                out[brand] = {"pending": n,
                              "sample": [{"t": (r["title"] or "")[:60], "p": r["platform"],
                                          "a": r["author"] or "", "d": (r["publish_time"] or "")[:10],
                                          "u": r["url"] or "", "note": r["verify_note"] or ""}
                                         for r in rows]}
        finally:
            conn.close()
        return out
    return _cached("pending", 120, build)


def media_stat():
    """媒体覆盖统计（口径页展示）：每品牌条数/有正文/有素材/含视频/有评论。"""
    out = {}
    for brand, slug in SLUG.items():
        rows = list_rows(slug)
        det = detail_map(slug)
        n_body = sum(1 for v in det.values() if (v.get("body") or "").strip())
        n_media = sum(1 for v in det.values() if v.get("loc"))
        n_video = sum(1 for v in det.values() if any(x[1] == "v" for x in v.get("loc") or []))
        n_comm = sum(1 for v in det.values() if v.get("cm"))
        out[brand] = {"n": len(rows), "media": n_media, "video": n_video,
                      "body": n_body, "comm": n_comm, "slug": slug}
    return out
