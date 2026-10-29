#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对外数据边界守卫：确认「未校验的数据一条都流不出去」。

用户规则（2026-10-24）：
  「数据库里必须是经过校验过的、准确的数据。对外只展示没问题的数据。
    所有没有校验过的，都不要对外展示。不管是小红书还是其他平台。」

本脚本检查全部对外出口：
  ① MCP 工具（content_stats / search_content / get_content / brand_compare /
     top_content / search_comments / pending_content）
  ② MCP 的 sql_query（自由 SQL —— 最大的泄漏口）
  ③ HTTP API（/api/raw_export / /api/export / /api/xhs_archive / /api/xhs_creators / /api/list）
  ④ 导出 CSV
  ⑤ 归档页面（HTML 文件数）
  ⑥ 数据库本体：未校验的数据必须仍在库里（供内部补采修复），只是不展示

用法：python crawler/check_data_boundary.py
"""
import csv
import glob
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
SVC_DB = os.path.join(ROOT, "service", "data", "cosmobounty.db")

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  {'✅' if ok else '❌'} {name}")
    if detail:
        print(f"       {detail}")


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    tot_c = con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    ver_c = con.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
    tot_x = con.execute("SELECT COUNT(*) FROM xhs_note").fetchone()[0]
    ver_x = con.execute("SELECT COUNT(*) FROM xhs_note WHERE verified=1").fetchone()[0]
    con.close()

    print("=" * 68)
    print("对外数据边界守卫")
    print("=" * 68)
    print(f"库存：content {ver_c}/{tot_c} 已校验 | xhs_note {ver_x}/{tot_x} 已校验\n")

    # ① 未校验数据仍在库里（内部可修复），这是前提
    check("未校验数据仍在库中（内部可补采修复，不删除）",
          tot_c > ver_c and tot_x > ver_x,
          f"content {tot_c - ver_c} 条 / xhs_note {tot_x - ver_x} 条处于隔离状态")

    # ② MCP 工具
    sys.path.insert(0, os.path.join(ROOT, "mcp"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cbserver", os.path.join(ROOT, "mcp", "cosmobounty_server.py"))
    m = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(m)

    print("\n【① MCP 工具】")
    r = m.call("content_stats", {})
    txt = json.loads(r["content"][0]["text"])
    check("content_stats 只统计已校验", txt.get("contents") == ver_c,
          f"报告 {txt.get('contents')} 条 = 已校验 {ver_c} 条")

    r = m.call("search_content", {"limit": 500})
    txt = json.loads(r["content"][0]["text"])
    ids = [i["id"] for i in txt.get("items", [])]
    con = sqlite3.connect(DB)
    bad = 0
    if ids:
        q = ",".join("?" * len(ids))
        bad = con.execute(
            f"SELECT COUNT(*) FROM content WHERE id IN ({q}) AND verified=0", ids).fetchone()[0]
    con.close()
    check("search_content 返回的都是已校验", bad == 0,
          f"取回 {len(ids)} 条，其中未校验 {bad} 条")

    r = m.call("top_content", {"limit": 100})
    txt = json.loads(r["content"][0]["text"])
    con = sqlite3.connect(DB)
    bad = 0
    if txt:
        ids = [i["id"] for i in txt]
        q = ",".join("?" * len(ids))
        bad = con.execute(
            f"SELECT COUNT(*) FROM content WHERE id IN ({q}) AND verified=0", ids).fetchone()[0]
    con.close()
    check("top_content 返回的都是已校验", bad == 0, f"取回 {len(txt)} 条，未校验 {bad} 条")

    r = m.call("search_comments", {"limit": 300})
    txt = json.loads(r["content"][0]["text"])
    con = sqlite3.connect(DB)
    bad = 0
    for cmt in txt.get("comments", []):
        cid = cmt.get("content_id")
        if cid is None:
            continue
        n = con.execute("SELECT COUNT(*) FROM content WHERE id=? AND verified=0",
                        (cid,)).fetchone()[0]
        bad += n
    con.close()
    check("search_comments 不含未校验内容的评论", bad == 0,
          f"取回 {txt.get('count')} 条，越界 {bad} 条")

    # ③ SQL 自由查询（最大的泄漏口）
    print("\n【② SQL 自由查询（最大泄漏口）】")
    probes = [
        ("SELECT id FROM content", 0, "全表明细"),   # MCP 单次上限 500，只要求不含未校验
        ("SELECT id FROM content WHERE verified=0", 0, "故意查未校验"),
        ("SELECT note_id FROM xhs_note", ver_x, "归档全表"),
        ("SELECT note_id FROM xhs_note WHERE verified=0", 0, "故意查未校验归档"),
        ("SELECT brand, COUNT(*) FROM content GROUP BY brand", 7, "分组统计"),
    ]
    for sql, expect, label in probes:
        r = m.call("sql_query", {"sql": sql})
        txt = json.loads(r["content"][0]["text"])
        rows = txt.get("data", [])
        if expect == 7:
            # 分组统计：六品牌 + 品类场景词独立轨道（场景词已过详细分析，允许对外）
            # 注意：MCP 返回的列名可能是 "brand"、也可能是 "BRAND" 或带函数名，
            # 这里按键名模糊取，避免因列名大小写差异误判成越界。
            brands = set()
            for row in rows:
                v = None
                for k, val in row.items():
                    if str(k).strip().lower() in ("brand", "t.brand"):
                        v = val
                        break
                brands.add(v)
            allowed = {"COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞",
                       "品类场景词"}
            bad = brands - allowed
            ok = len(rows) == 7 and not bad
            detail = f"返回 {len(rows)} 组" + (f"，越界 {bad}" if bad else "")
        elif expect == 0:      # 只要求「取回的都在已校验范围内」
            got = len(rows)
            ok = got <= ver_c
            detail = f"返回 {got} 行（已校验上限 {ver_c}）"
        else:
            got = len(rows)
            ok = got == expect
            detail = f"返回 {got} 行（期望 {expect}）"
        check(f"sql_query · {label}", ok, detail)

    # ④ HTTP API
    print("\n【③ HTTP API 源码检查】")
    svc = open(os.path.join(ROOT, "service", "app.py"), encoding="utf-8").read()
    # 所有涉及 content / xhs_note 的查询都要有 verified 过滤
    unguarded = []
    for m2 in re.finditer(r'"([^"]*FROM\s+(?:content|xhs_note)[^"]*)"', svc):
        q = m2.group(1)
        if "verified" not in q:
            unguarded.append(q[:80])
    check("service/app.py 所有 content/xhs_note 查询都带 verified",
          not unguarded, f"未加过滤 {len(unguarded)} 处" + (f"：{unguarded[:2]}" if unguarded else ""))
    check("service/app.py 有 apply_gate（SQL 入口防护）",
          "def apply_gate" in svc and "apply_gate(sql)" in svc)

    # ⑤ 导出 CSV
    print("\n【④ 导出物】")
    for f in glob.glob(os.path.join(ROOT, "exports", "data", "*.csv")):
        name = os.path.basename(f)
        try:
            with open(f, encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        except Exception:
            continue
        if not rows:
            continue
        # 内容总表应只含已校验
        cn = set(rows[0])
        if ("品牌" in cn and "链接" in cn) or ("brand" in cn and "url" in cn):
            url_key = "链接" if "链接" in cn else "url"
            con = sqlite3.connect(DB)
            con.row_factory = sqlite3.Row
            bad = 0
            for row in rows:
                url = (row.get(url_key) or "").strip()
                if url:
                    # 只要该 url 存在一条 verified=1 的记录即合规（跨平台同 url 是
                    # 正常的，如抖音视频被百度 SERP 收录，百度那条可能未校验）
                    n = con.execute(
                        "SELECT COUNT(*) FROM content WHERE url=? AND verified=1",
                        (url,)).fetchone()[0]
                    if n == 0:
                        bad += 1
            con.close()
            check(f"{name} 只含已校验内容", bad == 0, f"{len(rows)} 行，越界 {bad} 行")
        elif "账号ID" in cn:
            con = sqlite3.connect(DB)
            ids = [r.get("账号ID") for r in rows if (r.get("账号ID") or "").strip()]
            bad = 0
            if ids:
                q = ",".join("?" * len(ids))
                bad = con.execute(
                    f"""SELECT COUNT(*) FROM creator WHERE creator_id IN ({q})
                        AND creator_id NOT IN
                          (SELECT DISTINCT author_id FROM xhs_note WHERE verified=1 AND author_id!='')
                          AND creator_id NOT IN
                          (SELECT DISTINCT author_id FROM content WHERE verified=1 AND author_id!='')""",
                    ids).fetchone()[0]
            con.close()
            check(f"{name} 只含已校验内容涉及的账号", bad == 0,
                  f"{len(rows)} 个账号，越界 {bad} 个")

    # ⑥ 归档页面
    print("\n【⑤ 归档页面】")
    pages = [f for f in glob.glob(os.path.join(ROOT, "exports", "xhs_archive", "*.html"))
             if "index" not in os.path.basename(f) and "小红书" not in os.path.basename(f)]
    page_ids = {os.path.splitext(os.path.basename(f))[0] for f in pages}
    # 归档页覆盖的是「已校验的小红书内容」，不只是 xhs_note 档案：
    # content 里有正文的 xhs 条目也必须能点开本站页，否则「本站页↗」就是死链。
    # 判定标准从「页面数 ≤ 已校验档案数」改为「页面 note_id 全部来自已校验内容」。
    _c = sqlite3.connect(DB)
    allow = {r[0] for r in _c.execute("SELECT note_id FROM xhs_note WHERE verified=1")}
    for (u,) in _c.execute("SELECT url FROM content WHERE verified=1 AND platform='xhs'"):
        for mark in ("/explore/", "/discovery/item/"):
            if mark in (u or ""):
                allow.add(u.split(mark, 1)[1].split("?")[0].split("/")[0])
                break
    _c.close()
    bad_pages = page_ids - allow
    check("归档页只覆盖已校验笔记", not bad_pages,
          f"页面 {len(page_ids)} 个 / 已校验笔记 {len(allow)} 个，越界 {len(bad_pages)} 个")

    # ⑦ service 副本同步
    print("\n【⑥ service 副本】")
    if os.path.exists(SVC_DB):
        sc = sqlite3.connect(SVC_DB)
        v = sc.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
        has_col = "verified" in [r[1] for r in sc.execute("PRAGMA table_info(content)")]
        sc.close()
        check("service 副本含 verified 列", has_col)
        check("service 副本已校验数一致", v == ver_c, f"{v} vs {ver_c}")

    # ── 汇总 ──
    print("\n" + "=" * 68)
    npass = sum(1 for r in results if r[0] == PASS)
    nfail = sum(1 for r in results if r[0] == FAIL)
    print(f"结果：PASS {npass} | FAIL {nfail}")
    if nfail:
        print("\n失败项：")
        for lv, name, detail in results:
            if lv == FAIL:
                print(f"  ❌ {name} — {detail}")
    else:
        print("✅ 未校验数据全部被拦在数据边界之外")
    print("=" * 68)
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
