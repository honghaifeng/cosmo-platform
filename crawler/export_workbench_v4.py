#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出工作台 v4 的「内容明细」数据层。

输出（workbench/v4/）：
  list-<slug>.js   —— 该品牌全部已验讫内容的精简列表（内容库全量浏览、分页）
  detail-<slug>.js —— 该品牌全部已验讫内容的完整详情（原文/媒体/指标/评论/归属证据）
  media-index.js   —— 本地媒体覆盖统计（给「口径与边界」页展示）

设计原则：
  1. 只导出 verified=1 —— 与后端出口同一道闸，工作台不出现未校验数据
  2. 详情页要能「还原原文」：正文全文 + 本地图片/视频（可离线播放）+ 原文外链兜底
  3. 媒体优先本地归档，本地没有才给 CDN 原始直链（并标注可能已过期）
可重复运行，幂等覆盖。
"""
import sqlite3, json, os, re, glob, collections

CB = "/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty"
DB = f"{CB}/db/cosmobounty.db"
OUT = f"{CB}/workbench/v4"

SLUG = {
    "MitoQ": "mitoq", "LAIFE": "laife", "GRANVER": "granver",
    "塞乐瑞": "sailerui", "W+端粒塔": "duanlita", "COSMOBOUNTY": "cosmobounty",
    "__category": "category",          # 赛道参照系（泛品类内容，不归属任何品牌）
}
BRAND_OF = {"__category": "品类场景词"}   # 导出键 → 库内 brand 值
PLAT_CN = {"xhs": "小红书", "douyin": "抖音", "shipinhao": "视频号",
           "baidu": "百度", "toutiao": "今日头条", "web": "新闻源"}


def local_media_index():
    """(品牌目录, 平台, 内容ID) -> [(相对路径, 'i'/'v', 子目录)]
    键里带品牌目录：同一内容 ID 绝不允许跨品牌串素材（宁可判为无素材）。"""
    idx = collections.defaultdict(list)
    for p in glob.glob(f"{CB}/knowledge/media/*/*/*"):
        fn = os.path.basename(p)
        m = re.match(r"^(douyin|xhs|baidu|toutiao|web|bili|wx)[:_](.+)\.(jpg|jpeg|png|webp|mp4|m4s)$", fn, re.I)
        if not m:
            continue
        plat, cid, ext = m.group(1).lower(), m.group(2), m.group(3).lower()
        # 多图笔记的配图名是 <id>_0 / <id>_1 …，末尾的序号不属于内容 ID。
        # 不去掉的话，只有「无序号」那一个文件能对上，多图内容在列表里
        # 会被判成「无素材」（COSMOBOUNTY 39 条里只认出 6 条就是这个原因）。
        cid = re.sub(r"_\d+$", "", cid)
        rel = p[len(f"{CB}/knowledge/media/"):]
        parts = rel.split("/")
        if len(parts) < 3:
            continue
        dir_brand = parts[0]
        kind = "v" if ext in ("mp4", "m4s") else "i"
        idx[(dir_brand, plat, cid)].append((rel, kind, parts[1]))
    # 同一张首图存过两份（xxx.jpg 与 xxx_0.jpg）：不合并的话详情页会出现两张一样的图。
    # 只在「后缀相同」时合并，避免把后缀不同、实际不同的两张图误删。
    for k, v in list(idx.items()):
        if len(v) < 2:
            continue
        e0 = {os.path.splitext(x[0])[1].lower() for x in v
              if re.search(r"_0\.[a-z0-9]+$", x[0], re.I)}
        if e0:
            idx[k] = [x for x in v
                      if re.search(r"_\d+\.[a-z0-9]+$", x[0], re.I)
                      or os.path.splitext(x[0])[1].lower() not in e0]
    return idx


def cid_of(row):
    """从 url 或 raw_path 提取平台内容 ID"""
    for src in (row["url"] or "", row["raw_path"] or ""):
        m = re.search(r"(\d{15,20}|[0-9a-f]{24})", src)
        if m:
            return m.group(1)
    return None


def likes_of(mj):
    try:
        d = json.loads(mj or "{}")
        return int(d.get("likes") or 0), int(d.get("collects") or 0), \
               int(d.get("comments") or 0), int(d.get("shares") or 0)
    except Exception:
        return 0, 0, 0, 0


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    midx = local_media_index()

    # 评论按内容分组
    comm = collections.defaultdict(list)
    for r in conn.execute("SELECT content_id, body, like_count, publish_time FROM comment "
                          "ORDER BY like_count DESC"):
        comm[r["content_id"]].append([(r["body"] or "")[:400], r["like_count"] or 0,
                                      (r["publish_time"] or "")[:10]])

    stat = {}
    for brand, slug in SLUG.items():
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM content WHERE brand=? AND verified=1", (BRAND_OF.get(brand, brand),))]
        rows.sort(key=lambda r: -likes_of(r["metrics_json"])[0])

        lst, det = [], {}
        n_media = n_video = n_body = n_comm = 0
        for r in rows:
            lk, col, cm, sh = likes_of(r["metrics_json"])
            cid = cid_of(r)
            dir_brand = BRAND_OF.get(brand, brand)   # 内容归属品牌 = 媒体目录名，才认这条素材
            media = midx.get((dir_brand, r["platform"], cid), []) if cid else []
            loc = [[m[0], m[1]] for m in media]
            video = any(m[1] == "v" for m in media)
            img = any(m[1] == "i" for m in media)
            if media:
                n_media += 1
            if video:
                n_video += 1
            if (r["body"] or "").strip():
                n_body += 1

            # 原始直链兜底（本地没有归档时）
            cdn = []
            try:
                mj = json.loads(r["media_json"] or "{}")
                if isinstance(mj.get("images"), list):
                    cdn += [u for u in mj["images"] if isinstance(u, str)][:6]
                if isinstance(mj.get("video"), str):
                    cdn.append(mj["video"])
            except Exception:
                pass

            # 封面：优先 cover 目录，其次任意图片
            cov = ""
            if media:
                cv = [m for m in media if m[1] == "i" and "/cover/" in m[0]]
                anyimg = [m for m in media if m[1] == "i"]
                cov = (cv or anyimg or [[""]])[0][0]

            lst.append([r["id"], (r["title"] or "")[:80], r["platform"], r["author"] or "",
                        lk, (r["publish_time"] or "")[:10],
                        1 if "official" in (r["tags"] or "") else 0,
                        2 if video else (1 if img else 0), r["url"] or "", cov])

            cs = comm.get(r["id"], [])
            if cs:
                n_comm += 1
            det[str(r["id"])] = {
                "b": brand, "p": r["platform"], "pc": PLAT_CN.get(r["platform"], r["platform"]),
                "t": r["title"] or "", "body": r["body"] or "",
                "a": r["author"] or "", "aid": r["author_id"] or "",
                "d": r["publish_time"] or "", "ct": (r["collect_time"] or "")[:10],
                "u": r["url"] or "",
                "m": {"likes": lk, "collects": col, "comments": cm, "shares": sh},
                "tags": [t.strip() for t in (r["tags"] or "").split(",") if t.strip()],
                "off": 1 if "official" in (r["tags"] or "") else 0,
                "vn": r["verify_note"] or "",
                "typ": r["content_type"] or "",
                "loc": loc, "cdn": cdn,
                "cm": cs[:40], "cmn": len(cs),
            }

        open(f"{OUT}/list-{slug}.js", "w", encoding="utf-8").write(
            f"/* {brand} 全部已验讫内容（{len(lst)} 条）— 自动生成，勿手改 */\n"
            f"var LIST_{slug} = {json.dumps(lst, ensure_ascii=False)};\n"
            f"if (typeof WB_REG !== 'undefined') WB_REG.list['{slug}'] = LIST_{slug};\n")
        open(f"{OUT}/detail-{slug}.js", "w", encoding="utf-8").write(
            f"/* {brand} 内容详情 — 自动生成，勿手改 */\n"
            f"var DETAIL_{slug} = {json.dumps(det, ensure_ascii=False)};\n"
            f"if (typeof WB_REG !== 'undefined') WB_REG.detail['{slug}'] = DETAIL_{slug};\n")
        stat[brand] = {"n": len(lst), "media": n_media, "video": n_video,
                       "body": n_body, "comm": n_comm,
                       "slug": slug,
                       "kb": os.path.getsize(f"{OUT}/detail-{slug}.js") // 1024}

    open(f"{OUT}/media-index.js", "w", encoding="utf-8").write(
        "/* 媒体与正文覆盖统计 — 自动生成 */\nconst MEDIA_STAT = "
        + json.dumps(stat, ensure_ascii=False, indent=1) + ";\n")

    print(f"{'品牌':12s} {'内容':>5s} {'有正文':>6s} {'有素材':>6s} {'含视频':>6s} {'有评论':>6s} {'详情KB':>7s}")
    for b, s in stat.items():
        print(f"{b:12s} {s['n']:5d} {s['body']:6d} {s['media']:6d} {s['video']:6d} "
              f"{s['comm']:6d} {s['kb']:7d}")
    print("合计", sum(s["n"] for s in stat.values()), "条")


if __name__ == "__main__":
    main()
