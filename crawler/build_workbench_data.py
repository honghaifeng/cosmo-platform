#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重建工作台 v4 的汇总数据层（data.js / people.js / plat.js / contents.js）。

为什么要这个脚本：
  之前这几个文件是分次手写/半自动生成的，数据库又一直在变（新增采集、重跑校验），
  结果页面上出现「同一个品牌三处数字不一样」——列表 848 条、KPI 说 834 条、
  分档加起来 848。用户最不能接受的就是这个。
  现在所有数字只从 db 现算一次，全部文件同一时刻生成，口径统一：
    · 只算 verified=1（对外口径）
    · 官号 = tags 含 official
    · 点赞取 metrics_json.likes

输出（workbench/v4/）：
  data.js      品牌汇总（平台分布 / 官达效率 / 分档 / 月度 / 产品词频 / 产品线）
  plat.js      PLAT_DATA 平台×官达三件套 [条数, 均赞, 爆文]
  people.js    CREATORS 达人榜 + OFFICIAL 官号榜
  contents.js  每个品牌 top/recent 内容摘要（生产作业页用）
  media-index.js 不在这里生成（由 export_workbench_v4.py 写）
"""
import sqlite3, json, os, re, collections

CB = "/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty"
DB = f"{CB}/db/cosmobounty.db"
OUT = f"{CB}/workbench/v4"
CFG = f"{CB}/config/brands.json"

CN = {"LAIFE": "乐梵", "MitoQ": "MitoQ", "GRANVER": "GRANVER",
      "塞乐瑞": "塞乐瑞", "W+端粒塔": "W+端粒塔", "COSMOBOUNTY": "科诗博缇"}
CAT = "__category"
PLAT_CN = {"xhs": "小红书", "douyin": "抖音", "shipinhao": "视频号",
           "baidu": "百度", "toutiao": "今日头条", "web": "新闻源"}
# 品类词：用来给产品线打赛道标签（没有就留空，不猜）
CAT_WORDS = [("护肝", ["护肝", "肝", "解酒"]), ("抗衰", ["抗衰", "NMN", "nad", "端粒", "线粒体"]),
             ("心血管", ["心", "辅酶", "Q10", "血压"]), ("口腔", ["皓齿", "口腔", "牙"]),
             ("肺部", ["肺", "清肺"]), ("卵巢", ["卵巢", "养巢", "巢"]),
             ("免疫", ["免疫", "抵抗"]), ("男性", ["男性", "精力", "睾酮"]),
             ("睡眠", ["睡眠", "助眠"]), ("肠道", ["肠道", "益生菌"])]


def likes_of(mj):
    try:
        return int(json.loads(mj or "{}").get("likes") or 0)
    except Exception:
        return 0


def load_rows(conn, brand):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM content WHERE brand=? AND verified=1", (brand,))]


def is_off(r):
    return "official" in (r.get("tags") or "")


def bucket(l):
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


def main():
    cfg = {b["name"]: b for b in json.load(open(CFG, encoding="utf-8"))["brands"]}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    brands, plat_data, creators, official, contents = {}, {}, {}, {}, {}
    for brand, cn in CN.items():
        rows = load_rows(conn, brand)
        tot = conn.execute("SELECT count(*) FROM content WHERE brand=?", (brand,)).fetchone()[0]
        L = [likes_of(r["metrics_json"]) for r in rows]
        off = [r for r in rows if is_off(r)]
        kol = [r for r in rows if not is_off(r)]

        # 平台分布（全量 / 验讫）
        p_all = collections.Counter(r["platform"] for r in conn.execute(
            "SELECT platform FROM content WHERE brand=?", (brand,)))
        p_v = collections.Counter(r["platform"] for r in rows)
        p_off = collections.Counter(r["platform"] for r in off)

        # 平台 × 官达效率
        pd = {}
        for p in set(p_v):
            pd[p] = {}
            for key, grp in (("off", off), ("kol", kol)):
                g = [r for r in grp if r["platform"] == p]
                if g:
                    pd[p][key] = tri(g)
        plat_data[brand] = pd

        # 达人榜 / 官号榜
        def board(grp):
            agg = {}
            for r in grp:
                a = (r["author"] or "?").strip() or "?"
                l = likes_of(r["metrics_json"])
                v = agg.setdefault(a, {"n": 0, "sum": 0, "max": 0, "last": "", "plat": collections.Counter()})
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

        # 月度 / 分档 / 产品词频
        months = collections.Counter((r["publish_time"] or "")[:7] for r in rows if r["publish_time"])
        bk = collections.Counter(bucket(x) for x in L)
        prods = cfg.get(brand, {}).get("products", []) or []
        prod_kw = {}
        for name in prods:
            core = re.sub(r"\s+", "", name)
            hit = sum(1 for r in rows if core and core.lower() in ((r["title"] or "") + (r["body"] or "")).lower())
            if hit:
                prod_kw[name] = hit
        prod_kw = dict(sorted(prod_kw.items(), key=lambda x: -x[1]))

        # 产品线：产品名 + 内容命中 + 主阵地 + 命中数量最多的官号 + 赛道标签
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
        # 官达效率：页面上「谁在真正带来声量」那张表的唯一数据源
        o_n, o_avg, o_vir = tri(off)
        k_n, k_avg, k_vir = tri(kol)
        verdict = ("无官号阵地" if o_n == 0 else
                   "达人强于官号" if k_avg > o_avg * 1.5 else
                   "官号更有效率" if o_avg >= k_avg else "官达相当")
        brands[brand]["eff"] = {"off_n": o_n, "off_avg": o_avg, "off_viral": o_vir,
                                "kol_n": k_n, "kol_avg": k_avg, "kol_viral": k_vir,
                                "verdict": verdict, "vkey": "kol" if verdict == "达人强于官号" else "none"}

        # 生产作业页要用的内容摘要
        srt = sorted(rows, key=lambda r: -likes_of(r["metrics_json"]))
        rec = sorted(rows, key=lambda r: (r["publish_time"] or ""), reverse=True)
        brief = lambda r: {"t": (r["title"] or "")[:70], "p": r["platform"], "a": r["author"] or "",
                           "l": likes_of(r["metrics_json"]), "d": (r["publish_time"] or "")[:10],
                           "u": r["url"] or "", "o": is_off(r)}
        contents[brand] = {"top": [brief(r) for r in srt[:12]],
                           "recent": [brief(r) for r in rec[:12]], "n": len(rows)}

    # 品类大盘
    cat_rows = load_rows(conn, "品类场景词")
    CL = [likes_of(r["metrics_json"]) for r in cat_rows]
    category = {"total": len(cat_rows), "likes_sum": sum(CL),
                "likes_avg": round(sum(CL) / len(CL)) if CL else 0,
                "viral1k": sum(1 for x in CL if x >= 1000),
                "note": "泛品类场景词命中内容（非品牌归属），作为赛道流量参照，不进品牌横评"}
    contents["品类场景词"] = {"top": [{"t": (r["title"] or "")[:70], "p": r["platform"],
                                    "a": r["author"] or "", "l": likes_of(r["metrics_json"]),
                                    "d": (r["publish_time"] or "")[:10], "u": r["url"] or "", "o": False}
                                    for r in sorted(cat_rows, key=lambda r: -likes_of(r["metrics_json"]))[:12]],
                              "recent": [], "n": len(cat_rows)}

    gen = conn.execute("SELECT max(collect_time) FROM content").fetchone()[0] or ""
    data = {"generated": gen[:10], "brands": brands, "category": category}
    open(f"{OUT}/data.js", "w", encoding="utf-8").write(
        "/* data.js — 由 crawler/build_workbench_data.py 从 db 现算生成，勿手改 */\n"
        "const DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    open(f"{OUT}/plat.js", "w", encoding="utf-8").write(
        "/* plat.js — 平台×官达效率，自动生成，勿手改 */\n"
        "const PLAT_DATA = " + json.dumps(plat_data, ensure_ascii=False, indent=1) + ";\n")
    open(f"{OUT}/people.js", "w", encoding="utf-8").write(
        "/* people.js — 达人与官号榜，自动生成，勿手改 */\n"
        "const CREATORS = " + json.dumps(creators, ensure_ascii=False, indent=1) + ";\n"
        "const OFFICIAL = " + json.dumps(official, ensure_ascii=False, indent=1) + ";\n")
    open(f"{OUT}/contents.js", "w", encoding="utf-8").write(
        "/* contents.js — 内容摘要，自动生成，勿手改 */\n"
        "const contents = " + json.dumps(contents, ensure_ascii=False, indent=1) + ";\n")

    print(f"{'品牌':12s} {'验讫':>5s} {'官号':>5s} {'达人':>5s} {'总赞':>8s} {'均赞':>5s} {'爆文':>4s} {'分档合计':>6s}")
    for b, s in brands.items():
        print(f"{b:12s} {s['verified']:5d} {s['official_n']:5d} {s['creators']:5d} "
              f"{s['likes_sum']:8d} {s['likes_avg']:5d} {s['viral1k']:4d} {sum(s['buckets'].values()):6d}")
    print("合计验讫", sum(s["verified"] for s in brands.values()), "+ 品类", category["total"])


if __name__ == "__main__":
    main()
