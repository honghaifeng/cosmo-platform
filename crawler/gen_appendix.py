#!/usr/bin/env python3
"""从主库生成「原文链接附录」HTML，替换报告中的占位段落"""
import sqlite3, html, os, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(BASE, "report", "brand_analysis_v2_2026-10-21.html")
PLATFORMS = [("xhs", "小红书"), ("douyin", "抖音"), ("baidu", "百度"),
             ("toutiao", "今日头条"), ("web", "新闻源")]
BRANDS = ["MitoQ", "GRANVER", "LAIFE", "塞乐瑞", "W+端粒塔"]
PREVIEW_N = 4

def esc(s):
    return html.escape(s or "(无标题)", quote=True)

def fmt_metrics(metrics_json, platform):
    try:
        m = json.loads(metrics_json or "{}")
    except Exception:
        return ""
    parts = []
    if m.get("likes"): parts.append(f"{m['likes']}赞")
    if m.get("collects"): parts.append(f"{m['collects']}藏")
    if m.get("comments"): parts.append(f"{m['comments']}评")
    return " · ".join(parts)

def build():
    con = sqlite3.connect(os.path.join(BASE, "db", "cosmobounty.db"))
    out = ['<h2 id="appendix">七、附录：原文链接（全量 279 条，可展开）</h2>']
    out.append('<div class="card">')
    out.append('<p style="color:var(--sub);font-size:12.5px">按平台/品牌分组，按互动量排序。每组默认显示 4 条，点击「展开更多」查看全部。小红书链接需浏览器登录状态打开；即使链接失效，库内 raw 快照仍保留全文。</p>')
    total = 0
    for pkey, pname in PLATFORMS:
        rows_all = con.execute(
            "SELECT brand, title, url, metrics_json FROM content WHERE platform=?",
            (pkey,)).fetchall()
        out.append(f'<h3 style="margin-top:22px">{pname}（{len(rows_all)} 条）</h3>')
        for brand in BRANDS:
            rows = [r for r in rows_all if r[0] == brand]
            if not rows:
                continue
            total += len(rows)
            out.append(f'<p style="margin:12px 0 4px"><b>{esc(brand)}</b>'
                       f'<span style="color:var(--gray);font-size:12px">（{len(rows)} 条）</span></p><ul style="margin:0">')
            rows.sort(key=lambda r: -int((json.loads(r[3] or "{}").get("likes") or 0)))
            for i, (b, title, url, mj) in enumerate(rows):
                m = fmt_metrics(mj, pkey)
                li = (f'<li style="margin-bottom:3px;font-size:13px">'
                      f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(title[:60])}</a>'
                      + (f' <span style="color:var(--gray);font-size:11.5px">{m}</span>' if m else "")
                      + '</li>')
                if i < PREVIEW_N:
                    out.append(li)
            if len(rows) > PREVIEW_N:
                out.append(f'<li style="list-style:none;margin:2px 0"><details><summary style="cursor:pointer;color:var(--blue);font-size:12.5px">展开更多（{len(rows)-PREVIEW_N} 条）</summary><ul style="margin:6px 0 0">')
                for b, title, url, mj in rows[PREVIEW_N:]:
                    m = fmt_metrics(mj, pkey)
                    out.append(f'<li style="margin-bottom:3px;font-size:13px">'
                               f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(title[:60])}</a>'
                               + (f' <span style="color:var(--gray);font-size:11.5px">{m}</span>' if m else "")
                               + '</li>')
                out.append('</ul></details></li>')
            out.append('</ul>')
    out.append('</div>')
    con.close()
    print("覆盖条目数:", total)
    return "\n".join(out)

raw = open(REPORT, encoding="utf-8").read()
start = raw.find('<h2>七、附录')
end = raw.find('<footer>')
assert start > 0 and end > start, "找不到占位段落"
new_html = raw[:start] + build() + "\n" + raw[end:]
open(REPORT, "w", encoding="utf-8").write(new_html)
print("附录已写入", REPORT)
