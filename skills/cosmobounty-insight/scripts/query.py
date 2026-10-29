#!/usr/bin/env python3
"""CosmoBounty 查询 CLI（零依赖，支持远程服务 / 本地库两种模式）

远程模式（朋友用法，推荐）：技能目录内 service.json 已内置服务地址与密钥
    python3 query.py stats
也可用环境变量覆盖：
    export COSMOBOUNTY_API=https://xxx   COSMOBOUNTY_TOKEN=cb-xxx
本地模式（数据维护者用法）：
    export COSMOBOUNTY_DB=/path/to/cosmobounty.db

命令：
  stats                          数据总览（版本日期 / 内容 / 评论 / 平台 / 品牌 / 性质 / 完整度）
  compare [--platform xhs]       品牌 × 平台对比（赞/藏/评/平均 SEO/GEO）
  top [--brand X] [--platform xhs] [--by likes|comments|seo|geo] [--limit 10]
  search [--query 词] [--brand X] [--platform xhs] [--limit 20]
  comments [--query 智商税] [--brand 塞乐瑞] [--limit 50]
  pending                        非全文条目清单
  item --id 301                  单条全文 + 营销分析 + 评论
输出：默认表格；--json / --csv 可切换
"""
import argparse, csv, io, json, os, re, sqlite3, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

def _cfg():
    p = os.path.join(HERE, "service.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}

CFG = _cfg()
TOKEN = os.environ.get("COSMOBOUNTY_TOKEN") or CFG.get("token") or ""

def _endpoints():
    """支持多端点（平台无关）：service.json 里 endpoints 数组按顺序试，前一个挂了自动切下一个"""
    eps = []
    env_api = os.environ.get("COSMOBOUNTY_API")
    if env_api:
        eps.append({"label": "env", "base_url": env_api, "token": TOKEN})
    for e in (CFG.get("endpoints") or []):
        if isinstance(e, str):
            eps.append({"label": "cfg", "base_url": e, "token": TOKEN})
        elif isinstance(e, dict) and e.get("base_url"):
            eps.append({"label": e.get("label", "cfg"),
                        "base_url": e["base_url"],
                        "token": e.get("token") or TOKEN})
    if CFG.get("base_url"):
        eps.append({"label": "service.json", "base_url": CFG["base_url"], "token": TOKEN})
    seen, uniq = set(), []
    for e in eps:
        u = e["base_url"].rstrip("/")
        if u and u not in seen:
            seen.add(u)
            uniq.append(dict(e, base_url=u))
    return uniq

ENDPOINTS = _endpoints()
API = ENDPOINTS[0]["base_url"] if ENDPOINTS else ""
_ACTIVE = {"ep": ENDPOINTS[0] if ENDPOINTS else None}

if not ENDPOINTS:
    sys.exit(
        "未配置数据服务地址。\n"
        "  内部/朋友版：把提供方给的 service.json 放到本目录（scripts/）即可。\n"
        "  自建或更换服务：export COSMOBOUNTY_API=<地址> COSMOBOUNTY_TOKEN=<密钥>"
    )

def _split_api(url=None):
    """base_url 可能自带查询参数（sharecode/k 等网关参数），拆出来合并到每次请求"""
    sp = urllib.parse.urlsplit(url or _ACTIVE["ep"]["base_url"])
    return sp, dict(urllib.parse.parse_qsl(sp.query))

def find_db():
    for p in (os.environ.get("COSMOBOUNTY_DB"),
              os.path.join(os.getcwd(), "db", "cosmobounty.db"),
              os.path.expanduser("~/Desktop/2026/秋峰/Demo/CosmoBounty/db/cosmobounty.db")):
        if p and os.path.exists(p):
            return p
    sys.exit("找不到数据源：请在 service.json 配置 base_url+token，或设置 COSMOBOUNTY_DB")

def _call_one(ep, path, params=None, body=None):
    sp, base_q = _split_api(ep["base_url"])
    merged = {**base_q, "k": ep.get("token") or "", **(params or {})}  # 密钥走 URL：平台代理会剥自定义请求头
    url = urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path.rstrip("/") + path,
                                   urllib.parse.urlencode(merged), ""))
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if ep.get("token"):
        req.add_header("X-API-Key", ep["token"])  # 平台代理可能剥自定义头，URL ?k= 是主通道
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)

def call(path, params=None, body=None):
    """依次尝试各端点；连不上/5xx 自动切下一个，401 直接提示换密钥"""
    errs = []
    for ep in ENDPOINTS:
        try:
            d = _call_one(ep, path, params, body)
            _ACTIVE["ep"] = ep
            return d
        except urllib.error.HTTPError as e:
            if e.code == 401:
                errs.append(f"{ep['label']}: 密钥无效")
                continue
            errs.append(f"{ep['label']}: HTTP {e.code}")
        except Exception as e:
            errs.append(f"{ep['label']}: {type(e).__name__}")
    detail = "；".join(errs)
    if all("密钥无效" in x for x in errs):
        sys.exit(f"密钥无效或已更换，请向数据提供方索取新密钥（{detail}）")
    sys.exit(f"所有数据端点均不可用（{detail}）。若长期不可用，请提供方迁移服务或发新 service.json。")

# ------------------------------------------------------------------ local
def run(sql, args=()):
    c = sqlite3.connect(find_db())
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(sql, args)]
    c.close()
    return rows

def out(rows, fmt):
    if not rows:
        print("(无数据)"); return
    if fmt == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=1)); return
    if fmt == "csv":
        buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows); print(buf.getvalue().rstrip()); return
    cols = list(rows[0])
    w = {c: min(max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)), 44) for c in cols}
    print(" | ".join(c.ljust(w[c]) for c in cols))
    print("-+-".join("-" * w[c] for c in cols))
    for r in rows:
        print(" | ".join(str(r.get(c, "")).replace("\n", " ")[:44].ljust(w[c]) for c in cols))

def section(title):
    print(f"\n== {title} ==")

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("sql_or_value", nargs="?")
    ap.add_argument("--platform"); ap.add_argument("--brand"); ap.add_argument("--query")
    ap.add_argument("--by", default="likes"); ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--id", type=int)
    ap.add_argument("--json", action="store_const", dest="fmt", const="json")
    ap.add_argument("--csv", action="store_const", dest="fmt", const="csv")
    a = ap.parse_args()
    fmt = a.fmt or "table"
    remote = bool(API)

    if a.cmd == "guide":
        if remote:
            d = call("/api/guide")
            print(f"<!-- 口径版本 {d.get('guide_version')} · 数据版本 {d.get('data_version')} -->")
            print(d.get("markdown") or "(服务端暂无口径文件)")
            mv = d.get("skill_min_version")
            local = "1.2.0"
            if mv and tuple(map(int, mv.split("."))) > tuple(map(int, local.split("."))):
                print(f"\n[提示] 技能包版本 {local} 低于要求 {mv}，建议更新技能包；"
                      f"本次仍按远程口径执行。")
        else:
            print("(本地模式无远程口径；请参考技能包内置说明)")
        return

    if a.cmd == "stats":
        if remote:
            d = call("/api/stats")
            v = d.get("data_version") or {}
            print(f"数据版本 {v.get('date', '—')}（同步于 {v.get('generated_at', '—')}）")
            print(f"内容 {d['contents']} 条 · 评论 {d['comments']} 条")
            section("平台分布"); out(d["by_platform"], fmt)
            section("品牌分布"); out(d["by_brand"], fmt)
            section("内容性质"); out([x for x in d["by_nature"] if x.get("nature")], fmt)
            lv = d["fulltext_levels"]
            section("正文完整度"); out([{"level": k, "n": lv[k]} for k in lv], fmt)
        else:
            rows = [{"metric": "内容总数", "value": run("SELECT COUNT(*) n FROM content")[0]["n"]},
                    {"metric": "评论总数", "value": run("SELECT COUNT(*) n FROM comment")[0]["n"]}]
            out(rows, fmt)
            section("平台分布"); out(run("SELECT platform, COUNT(*) n FROM content GROUP BY 1 ORDER BY n DESC"), fmt)
            section("品牌分布"); out(run("SELECT brand, COUNT(*) n, SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes FROM content GROUP BY 1 ORDER BY likes DESC"), fmt)
            section("内容性质"); out(run("SELECT json_extract(analysis_json,'$.content_type') nature, COUNT(*) n FROM content GROUP BY 1 ORDER BY n DESC"), fmt)

    elif a.cmd == "compare":
        if remote:
            out(call("/api/compare", {"platform": a.platform or ""}), fmt)
        else:
            w, p = ("WHERE platform=?", [a.platform]) if a.platform else ("", [])
            out(run(f"""SELECT brand, platform, COUNT(*) n,
                SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes,
                SUM(CAST(json_extract(metrics_json,'$.collects') AS INT)) collects,
                SUM(CAST(json_extract(metrics_json,'$.comments') AS INT)) comments,
                ROUND(AVG(CAST(json_extract(analysis_json,'$.seo.score') AS REAL)),1) seo,
                ROUND(AVG(CAST(json_extract(analysis_json,'$.geo.score') AS REAL)),1) geo
                FROM content {w} GROUP BY 1,2 ORDER BY likes DESC""", p), fmt)

    elif a.cmd == "top":
        if remote:
            out(call("/api/top", {"brand": a.brand or "", "platform": a.platform or "",
                                  "by": a.by, "limit": a.limit}), fmt)
        else:
            w, p = ["1=1"], []
            if a.brand: w.append("brand=?"); p.append(a.brand)
            if a.platform: w.append("platform=?"); p.append(a.platform)
            order = {"likes": "CAST(json_extract(metrics_json,'$.likes') AS INT)",
                     "comments": "CAST(json_extract(metrics_json,'$.comments') AS INT)",
                     "seo": "CAST(json_extract(analysis_json,'$.seo.score') AS INT)",
                     "geo": "CAST(json_extract(analysis_json,'$.geo.score') AS INT)"}.get(a.by, "CAST(json_extract(metrics_json,'$.likes') AS INT)")
            p.append(a.limit)
            out(run(f"""SELECT id, brand, platform, substr(title,1,40) title,
                CAST(json_extract(metrics_json,'$.likes') AS INT) likes,
                CAST(json_extract(analysis_json,'$.seo.score') AS INT) seo,
                CAST(json_extract(analysis_json,'$.geo.score') AS INT) geo, url
                FROM content WHERE {' AND '.join(w)} ORDER BY {order} DESC LIMIT ?""", p), fmt)

    elif a.cmd == "search":
        if remote:
            d = call("/api/list", {"q": a.query or "", "brand": a.brand or "",
                                   "platform": a.platform or "", "limit": a.limit})
            out([{k: v for k, v in i.items() if k in ("id", "brand", "platform", "title", "likes", "collects", "comments", "seo", "geo", "url")}
                 for i in d["items"]], fmt)
        else:
            w, p = ["1=1"], []
            if a.query: w.append("(title LIKE ? OR body LIKE ?)"); p += [f"%{a.query}%"] * 2
            if a.brand: w.append("brand=?"); p.append(a.brand)
            if a.platform: w.append("platform=?"); p.append(a.platform)
            p.append(a.limit)
            out(run(f"""SELECT id, brand, platform, substr(title,1,36) title,
                CAST(json_extract(metrics_json,'$.likes') AS INT) likes, url
                FROM content WHERE {' AND '.join(w)} ORDER BY likes DESC LIMIT ?""", p), fmt)

    elif a.cmd == "comments":
        if remote:
            out(call("/api/comments", {"q": a.query or "", "brand": a.brand or "", "limit": a.limit})["comments"], fmt)
        else:
            w, p = ["1=1"], []
            if a.query: w.append("c.body LIKE ?"); p.append(f"%{a.query}%")
            if a.brand: w.append("t.brand=?"); p.append(a.brand)
            p.append(a.limit)
            out(run(f"""SELECT t.brand, t.platform, substr(c.body,1,60) body, c.like_count, c.publish_time
                FROM comment c JOIN content t ON c.content_id=t.id WHERE {' AND '.join(w)}
                ORDER BY c.like_count DESC LIMIT ?""", p), fmt)

    elif a.cmd == "pending":
        if remote:
            d = call("/api/pending")
            print(f"非全文条目 {d['total']} 条（分布 {d['by_platform']}）")
            out(d["items"], fmt)
        else:
            rows = run("SELECT id, brand, platform, substr(title,1,40) title, LENGTH(COALESCE(body,'')) blen FROM content WHERE platform IN ('baidu','toutiao','web') ORDER BY blen")
            rows = [r for r in rows if r["blen"] < 220]
            print(f"缺正文 {len(rows)} 条"); out(rows[:a.limit], fmt)

    elif a.cmd == "item":
        if remote:
            d = call("/api/item", {"id": a.id})
            if fmt == "json":
                print(json.dumps(d, ensure_ascii=False, indent=1)); return
            print(f"# {d['title']}\n{d['brand']} · {d['platform_cn']} · {d['publish_time']} · {d['url']}")
            m = d.get("metrics", {})
            print(f"赞 {m.get('likes',0)} · 藏 {m.get('collects',0)} · 评 {m.get('comments',0)} · 转 {m.get('shares',0)}")
            an = d.get("analysis", {})
            print(f"性质 {an.get('content_type')} ｜ SEO {an.get('seo',{}).get('score')} ｜ GEO {an.get('geo',{}).get('score')}")
            print(f"\n{d.get('body','')}")
            if d.get("comments_list"):
                section("评论（按赞）")
                for c in d["comments_list"][:15]:
                    print(f"· [{c.get('like_count',0)}赞] {c.get('body','')}")
        else:
            r = run("SELECT * FROM content WHERE id=?", (a.id,))
            if not r: sys.exit("未找到")
            d = r[0]
            print(f"# {d['title']}\n{d['brand']} · {d['platform']} · {d['url']}\n\n{d['body']}")
            if fmt == "json": print(json.dumps(d, ensure_ascii=False, indent=1))

    elif a.cmd == "sql":
        sql = a.sql_or_value or ""
        if not re.match(r"(?is)^\s*(select|with)\b", sql):
            sys.exit("仅允许 SELECT 查询")
        if remote:
            d = call("/api/sql", body={"sql": sql})
            if "error" in d: sys.exit(d["error"])
            out(d["data"], fmt)
        else:
            out(run(sql), fmt)
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
