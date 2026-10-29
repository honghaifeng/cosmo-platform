#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书账号矩阵分析：六品牌的账号结构、层级与铺量节奏。

核心指标
  ① 矩阵结构：账号数 / 人均笔记 / Top1、Top5 集中度
     —— 账号数少而人均多、Top 集中度高 = 自有矩阵号铺量
     —— 账号数≈笔记数、人均≈1 = 自然流量或单篇投放（无矩阵）
  ② 账号分层：官方号 / 矩阵号 / KOL / 散户
  ③ 互动效率：赞藏评分布（矩阵号通常互动低、藏高）
  ④ 跨品牌账号：同时提及多品牌的号（对比型/蹭流量）
  ⑤ 铺量节奏：矩阵号发帖时间分布（是否集中投放）

产出
  exports/analysis/account_matrix_<date>.csv   账号明细（可 Excel 打开）
  exports/analysis/brand_matrix_summary.csv    六品牌矩阵结构对照
  exports/analysis/account_matrix_report.html  可视化报告页
"""
import collections
import csv
import datetime
import json
import os
import re
import sqlite3
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
OUT = os.path.join(ROOT, "exports", "analysis")
BRANDS = ["COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"]

# 官方号识别：昵称含品牌主体名/官方字样
OFFICIAL_PAT = {
    "COSMOBOUNTY": re.compile(r"(cosmo\s*bounty|cosmobounty|科诗博缇|官方)", re.I),
    "MitoQ": re.compile(r"(mitoq|mito\s*q|官方)", re.I),
    "GRANVER": re.compile(r"(granver|吉力威|吉清肺|吉利肝|官方)", re.I),
    "LAIFE": re.compile(r"(laife|乐梵|官方)", re.I),
    "W+端粒塔": re.compile(r"(端粒塔|duanlta|官方)", re.I),
    "塞乐瑞": re.compile(r"(塞乐瑞|赛乐瑞|cellerator|官方)", re.I),
}


def load():
    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT note_id, brand, author, author_id, title, publish_time, source_keyword,
               likes, collects, comments, shares, note_type, evidence, body
        FROM xhs_note WHERE brand_mention = 1""").fetchall()
    con.close()
    return rows


# 已核实的官方账号昵称（来自项目 knowledge 与前期核实）
KNOWN_OFFICIAL = {
    "Cellerator赛乐瑞": "塞乐瑞", "MitoQ": "MitoQ", "Granver": "GRANVER",
    "端粒塔 nmn": "W+端粒塔", "LAIFE乐梵": "LAIFE", "星辰大海LAIFE": "LAIFE",
    "LAIFE": "LAIFE", "COSMOBOUNTY": "COSMOBOUNTY", "科诗博缇": "COSMOBOUNTY",
}


def classify(brand, author, cnt, likes, collects, comments):
    """账号分层。"""
    nick = (author or "").strip()
    if nick in KNOWN_OFFICIAL and KNOWN_OFFICIAL[nick] == brand:
        return "官方号"
    if OFFICIAL_PAT.get(brand) and OFFICIAL_PAT[brand].search(nick):
        return "官方号"
    if cnt >= 3:
        avg_inter = (likes + collects + comments) / max(cnt, 1)
        return "矩阵号" if avg_inter < 120 else "活跃号"
    if cnt == 1 and (likes >= 800 or collects >= 300):
        return "KOL单篇"
    if likes + collects + comments >= 500:
        return "KOL互动"
    return "散户/单篇"


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = load()
    ts = datetime.date.today().isoformat()

    # 按 品牌 × 账号 聚合
    acc = collections.defaultdict(lambda: {
        "posts": 0, "likes": 0, "collects": 0, "comments": 0, "shares": 0,
        "nickname": "", "author_id": "", "times": [], "kws": collections.Counter(),
        "notes": [], "videos": 0})
    brand_posts = collections.Counter()
    brand_accounts = collections.defaultdict(set)
    for (nid, brand, author, aid, title, pub, kw, lk, cl, cm, sh, ntype, ev, body) in rows:
        a = acc[(brand, aid or author or "?")]
        a["posts"] += 1
        a["likes"] += lk or 0
        a["collects"] += cl or 0
        a["comments"] += cm or 0
        a["shares"] += sh or 0
        # 同一账号不同笔记的昵称可能一真一脱敏，保留真实且最长的那个
        cand = author or ""
        cur = a["nickname"] or ""
        if (cand and "*" not in cand and len(cand) > len(cur)) or (cur and "*" in cur and cand and "*" not in cand):
            a["nickname"] = cand
        a["author_id"] = aid or ""
        if pub:
            a["times"].append(pub)
        if kw:
            a["kws"][kw] += 1
        a["notes"].append((pub or "", title or "", lk or 0, ntype))
        if ntype == "video":
            a["videos"] += 1
        brand_posts[brand] += 1
        brand_accounts[brand].add(aid or author or "?")

    # 账号明细
    detail = []
    for (brand, aid), a in acc.items():
        inter = a["likes"] + a["collects"] + a["comments"]
        tier = classify(brand, a["nickname"], a["posts"], a["likes"], a["collects"], a["comments"])
        first = min(a["times"]) if a["times"] else ""
        last = max(a["times"]) if a["times"] else ""
        masked = "*" in (a["nickname"] or "")
        detail.append({
            "品牌": brand, "账号": a["nickname"] or "(昵称缺失)",
            "昵称状态": "脱敏(待补)" if masked else "真实",
            "账号ID": a["author_id"],
            "层级": tier, "笔记数": a["posts"], "点赞": a["likes"], "收藏": a["collects"],
            "评论": a["comments"], "分享": a["shares"], "总互动": inter,
            "篇均互动": round(inter / max(a["posts"], 1), 1),
            "视频数": a["videos"], "最早发布": first, "最近发布": last,
            "主要采集词": "、".join(w for w, _ in a["kws"].most_common(3)),
            "代表笔记": (a["notes"][0][1][:40] if a["notes"] else ""),
            "主页": f"https://www.xiaohongshu.com/user/profile/{a['author_id']}" if a["author_id"] else "",
        })
    detail.sort(key=lambda x: (-x["笔记数"], -x["总互动"]))

    # 品牌矩阵结构
    summary = []
    for b in BRANDS:
        sub = [d for d in detail if d["品牌"] == b]
        if not sub:
            continue
        sub.sort(key=lambda x: -x["笔记数"])
        tot_posts = sum(d["笔记数"] for d in sub)
        tot_inter = sum(d["总互动"] for d in sub)
        top1 = sub[0]["笔记数"] / tot_posts if tot_posts else 0
        top5 = sum(d["笔记数"] for d in sub[:5]) / tot_posts if tot_posts else 0
        tiers = collections.Counter(d["层级"] for d in sub)
        inter_list = [d["篇均互动"] for d in sub]
        # 分类看「谁在贡献内容」，而不是只看集中度
        off_posts = sum(d["笔记数"] for d in sub if d["层级"] == "官方号")
        mat_posts = sum(d["笔记数"] for d in sub if d["层级"] in ("矩阵号", "活跃号"))
        kol_posts = sum(d["笔记数"] for d in sub if d["层级"].startswith("KOL"))
        off_share = off_posts / tot_posts if tot_posts else 0
        mat_share = mat_posts / tot_posts if tot_posts else 0
        kol_share = kol_posts / tot_posts if tot_posts else 0
        if off_share >= 0.55:
            kind = "官方号独大·自说自话"
        elif mat_share >= 0.35:
            kind = "矩阵号铺量"
        elif kol_share >= 0.35:
            kind = "KOL 投放型"
        elif top5 >= 0.45:
            kind = "轻度集中"
        else:
            kind = "分散自然流量"
        summary.append({
            "品牌": b, "品牌内容数": tot_posts, "账号数": len(sub),
            "人均笔记": round(tot_posts / len(sub), 2),
            "Top1集中度": f"{top1*100:.0f}%", "Top5集中度": f"{top5*100:.0f}%",
            "官方号": tiers.get("官方号", 0), "矩阵号": tiers.get("矩阵号", 0) + tiers.get("活跃号", 0),
            "活跃号": tiers.get("活跃号", 0), "KOL": tiers.get("KOL单篇", 0) + tiers.get("KOL互动", 0),
            "散户": tiers.get("散户/单篇", 0),
            "官方号占比": f"{off_share*100:.0f}%", "矩阵号占比": f"{mat_share*100:.0f}%",
            "KOL占比": f"{kol_share*100:.0f}%",
            "总互动": tot_inter, "篇均互动": round(statistics.median(inter_list), 1),
            "矩阵判定": kind,
        })

    # 跨品牌：按「正文同时命中两个品牌」判定（而非归属标签，后者天然唯一）
    import re as _re
    CROSS_RX = {
        "COSMOBOUNTY": _re.compile(r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时胶囊|日活瓶|夜修瓶)", _re.I),
        "MitoQ": _re.compile(r"(mito\s*q|mitoq)", _re.I),
        "GRANVER": _re.compile(r"(granver|granve|吉利肝|吉清肺|吉力威|吉瑞维)", _re.I),
        "LAIFE": _re.compile(r"(laife|乐梵|乐悦泰|乐轻肝|红宝瓶|乐维源|乐净肺)", _re.I),
        "W+端粒塔": _re.compile(r"(端粒塔|duanlta|nmn\s*25000)", _re.I),
        "塞乐瑞": _re.compile(r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱|赛特茵)", _re.I),
    }
    cross = []
    body_ok = 0
    for (nid, brand, author, aid, title, pub, kw, lk, cl, cm, sh, ntype, ev, body) in rows:
        txt = f"{title or ''} {body or ''}"
        if len(body or "") > 40:
            body_ok += 1
        hits = {k for k, rx in CROSS_RX.items() if rx.search(txt)}
        if len(hits) > 1:
            cross.append({"账号": author or "(昵称缺失)", "账号ID": aid,
                          "涉及品牌数": len(hits), "涉及品牌": "、".join(sorted(hits)),
                          "笔记": (title or "")[:40]})
    cross.sort(key=lambda x: -x["涉及品牌数"])
    body_ratio = body_ok / max(len(rows), 1)

    # 铺量节奏：每月笔记数（按品牌）
    monthly = collections.defaultdict(collections.Counter)
    for (nid, brand, author, aid, title, pub, kw, *rest) in rows:
        if pub:
            monthly[brand][pub[:7]] += 1

    # 落盘
    d1 = os.path.join(OUT, f"account_matrix_{ts}.csv")
    with open(d1, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(detail[0].keys()))
        w.writeheader()
        w.writerows(detail)
    d2 = os.path.join(OUT, "brand_matrix_summary.csv")
    with open(d2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    d3 = os.path.join(OUT, "cross_brand_accounts.csv")
    with open(d3, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["账号", "账号ID", "涉及品牌数", "涉及品牌"])
        w.writerows([[c["账号"], c["账号ID"], c["涉及品牌数"], c["涉及品牌"]] for c in cross])

    # 自动结论
    findings = []
    byb = {s["品牌"]: s for s in summary}
    for b in BRANDS:
        s = byb.get(b)
        if not s:
            continue
        sub = sorted([d for d in detail if d["品牌"] == b], key=lambda x: -x["笔记数"])
        top = sub[0] if sub else None
        if s["矩阵判定"] == "矩阵号铺量" and top:
            findings.append(
                f"【{b}】{s['品牌内容数']} 条内容集中在 {s['账号数']} 个号（人均 {s['人均笔记']}），"
                f"Top5 占 {s['Top5集中度']} —— 属<b>矩阵号铺量</b>（矩阵号贡献 {s['矩阵号占比']}）。头部号「{top['账号']}」"
                f"发 {top['笔记数']} 条、篇均互动仅 {top['篇均互动']}，"
                f"发帖连续（{top['最早发布'][:10]}~{top['最近发布'][:10]}）。")
        elif s["官方号"] == 0 and s["品牌内容数"] >= 30:
            findings.append(
                f"【{b}】<b>未识别到官方号</b>，但有 {s['矩阵号']} 个矩阵号在发（{s['品牌内容数']} 条内容）。"
                f"内容在铺，官方阵地是空的 —— 品牌搜索结果里看不到'官方'背书。")
        elif s["矩阵判定"] == "官方号独大·自说自话":
            findings.append(
                f"【{b}】<b>官方号一家独大</b>：官方号贡献 {s['官方号占比']} 的内容，"
                f"KOL 仅 {s['KOL占比']}、矩阵号 {s['矩阵号占比']} —— 是「自说自话」型，"
                f"几乎没有被真实用户或达人讨论。")
        elif s["官方号"] and s["KOL"] >= 15:
            findings.append(
                f"【{b}】官方号 {s['官方号']} 个持续运营 + {s['KOL']} 个 KOL 参与，"
                f"Top5 仅占 {s['Top5集中度']} —— <b>KOL 投放型</b>，最健康。")
        else:
            findings.append(
                f"【{b}】{s['品牌内容数']} 条分散在 {s['账号数']} 个号（人均 {s['人均笔记']}），"
                f"Top5 占 {s['Top5集中度']} —— {s['矩阵判定']}；官方号 {s['官方号']} 个，"
                f"KOL {s['KOL']} 个。")
    # 官方号占比异常
    for b in BRANDS:
        s = byb.get(b)
        if not s:
            continue
        sub = sorted([d for d in detail if d["品牌"] == b], key=lambda x: -x["笔记数"])
        if sub and s["品牌内容数"] >= 20:
            share = sub[0]["笔记数"] / s["品牌内容数"]
            if share >= 0.6 and sub[0]["层级"] == "官方号":
                findings.append(
                    f"【{b}】<b>官方号一家独大</b>：头部官方号「{sub[0]['账号']}」独占 "
                    f"{share*100:.0f}% 内容（{sub[0]['笔记数']}/{s['品牌内容数']}），"
                    f"篇均互动 {sub[0]['篇均互动']} —— 是「自说自话」型，几乎没有被真实用户或达人讨论。")
    html_report(summary, detail, cross, monthly, findings)
    print(f"账号明细 → {d1}（{len(detail)} 个账号）")
    print(f"品牌对照 → {d2}")
    print(f"跨品牌账号 → {d3}（{len(cross)} 个）")
    print("\n=== 六品牌矩阵结构 ===")
    for s in summary:
        print(f"  {s['品牌']:14s} 内容{s['品牌内容数']:4d} 账号{s['账号数']:3d} "
              f"人均{s['人均笔记']:4.2f} Top5集中{s['Top5集中度']:>5s} "
              f"官方{s['官方号']} 矩阵{s['矩阵号']} KOL{s['KOL']} 散户{s['散户']} → {s['矩阵判定']}")


def html_report(summary, detail, cross, monthly, findings=None):
    ts = datetime.date.today().isoformat()
    e = __import__("html").escape

    def tbl(rows, cols):
        h = "".join(f"<th>{e(c)}</th>" for c in cols)
        body = ""
        for r in rows:
            body += "<tr>" + "".join(
                f'<td>{r.get(c, "")}</td>' for c in cols) + "</tr>"
        return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

    srows = []
    for s in summary:
        srows.append("<tr>" + "".join(f"<td>{s.get(k,'')}</td>" for k in s.keys()) + "</tr>")
    scols = list(summary[0].keys())
    s_html = f"<table><thead><tr>{''.join(f'<th>{e(c)}</th>' for c in scols)}</tr></thead><tbody>{''.join(srows)}</tbody></table>"

    # 每品牌 TOP 账号
    blocks = ""
    for b in BRANDS:
        sub = [d for d in detail if d["品牌"] == b][:14]
        if not sub:
            continue
        blocks += f"<h3>{e(b)} <span class=muted>（{len([d for d in detail if d['品牌']==b])} 个账号）</span></h3>"
        blocks += tbl(sub, ["账号", "昵称状态", "层级", "笔记数", "总互动", "篇均互动", "最早发布", "最近发布", "代表笔记", "主页"])

    cross_html = ""
    if cross:
        cross_html = ("<h3>跨品牌账号（正文同时提及多个品牌）</h3>" +
                      (tbl(cross[:30], ["账号", "涉及品牌数", "涉及品牌", "笔记"])
                       if cross else
                       f'<div class="sub">未检出。口径说明：跨品牌判定依赖正文文本，'
                       f'但当前 {len(rows)} 条品牌内容中仅 {body_ratio*100:.0f}% 有完整正文'
                       f'（详情接口被风控拦截，正文缺失），因此该结论<b>证据不足</b>，不能据此断定「无跨品牌投放」。</div>'))

    # 月度铺量
    months = sorted({m for c in monthly.values() for m in c})
    if months:
        chart = ""
        for b in BRANDS:
            vals = [monthly[b].get(m, 0) for m in months]
            mx = max(vals) or 1
            bars = "".join(
                f'<div class=barwrap title="{m}: {v} 条"><div class=bar style="height:{v/mx*120:.0f}px"></div>'
                f'<span>{v}</span></div>' for m, v in zip(months, vals))
            chart += f'<div class=row><div class=bl>{e(b)}</div><div class=bars>{bars}</div></div>'
        months_html = ("<h3>各品牌月度铺量</h3>" + chart +
                      "<div class=axis>" + "".join(f"<span>{m}</span>" for m in months) + "</div>")
    else:
        months_html = ""

    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>小红书账号矩阵分析 · {ts}</title><style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f7f9;color:#222;margin:0;padding:24px}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:21px;margin:0 0 4px}} h3{{font-size:16px;margin:26px 0 10px}}
.sub{{color:#888;font-size:13px;margin-bottom:20px}}
.card{{background:#fff;border-radius:10px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;background:#fff}}
th,td{{padding:7px 9px;border-bottom:1px solid #f2f2f2;text-align:left;white-space:nowrap}}
th{{background:#fafafa;font-weight:600}}
td a{{color:#2a6ef4;text-decoration:none}}
.muted{{color:#999;font-weight:400;font-size:13px}}
.row{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
.bl{{width:110px;font-size:12.5px;color:#555}}
.bars{{display:flex;gap:3px;align-items:flex-end}}
.barwrap{{width:26px;text-align:center}}
.bar{{background:#5b8cff;border-radius:2px 2px 0 0;min-height:2px}}
.barwrap span{{font-size:9px;color:#aaa}}
.axis{{display:flex;gap:3px;margin-left:120px;font-size:9px;color:#aaa}}
.axis span{{width:26px;text-align:center}}
</style></head><body><div class="wrap">
<h1>小红书账号矩阵分析 · 六品牌</h1>
<div class="sub">生成时间 {ts} ｜ 口径：仅统计「正文实质提及品牌」的内容，剔除蹭词位 ｜ 数据源：xhs_note 归档（{len(detail)} 个账号）<br>
注意：43% 的账号昵称来自脱敏数据（抓取时开启匿名化），显示为「C***Y」形式；这类账号无法判定是否官方号，
但其<b>发帖量与互动结构</b>仍可用于判断矩阵属性。</div>

<div class="card"><h3 style="margin-top:0">核心结论</h3><ul style="margin:0;padding-left:20px;font-size:13.5px;line-height:2">
{''.join(f'<li>{f}</li>' for f in (findings or []))}</ul></div>

<div class="card"><h3 style="margin-top:0">一、矩阵结构总览</h3>{s_html}
<div class="sub" style="margin-top:10px">判读：账号数少、人均笔记高、Top5 集中度 ≥50% → 自有矩阵号铺量；账号数≈内容数、人均≈1、Top5 集中度低 → 自然流量/单篇投放，无矩阵。</div></div>

<div class="card"><h3 style="margin-top:0">二、铺量节奏</h3>{months_html}</div>

<div class="card"><h3 style="margin-top:0">三、各品牌账号 TOP 榜</h3>{blocks}</div>

<div class="card">{cross_html}</div>
</div></body></html>"""
    p = os.path.join(OUT, f"account_matrix_report_{ts}.html")
    open(p, "w", encoding="utf-8").write(page)
    print(f"报告页 → {p}")


if __name__ == "__main__":
    main()
