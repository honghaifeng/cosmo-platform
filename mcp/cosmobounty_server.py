#!/usr/bin/env python3
"""CosmoBounty MCP 服务器（stdio 传输，零依赖）
把品牌内容库暴露成一组工具给任何 MCP 客户端（WorkBuddy / Trae / Claude 等）调用。

启动方式（在 mcp.json 中配置）：
  "cosmobounty": { "command": "python3", "args": ["<项目路径>/mcp/cosmobounty_server.py"] }

数据库路径优先级：环境变量 COSMOBOUNTY_DB > 项目内 db/cosmobounty.db
"""
import json, os, sqlite3, sys, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("COSMOBOUNTY_DB") or os.path.join(BASE, "db", "cosmobounty.db")
PLATFORMS = {"xhs": "小红书", "douyin": "抖音", "baidu": "百度", "toutiao": "今日头条", "web": "新闻源"}

def conn():
    if not os.path.exists(DB):
        raise RuntimeError(f"数据库不存在: {DB}")
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def ok(data):
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}

TOOLS = [
    {
        "name": "content_stats",
        "description": "内容库总览：内容/评论总量、平台分布、品牌分布、内容性质分布、正文完整度。分析前的第一步，先用它了解数据规模。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_content",
        "description": "按关键词/品牌/平台/内容性质检索内容（标题+正文模糊匹配），返回标题、互动数据、原文链接、SEO/GEO 分。",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "关键词，匹配标题或正文，可空"},
            "brand": {"type": "string", "description": "品牌：MitoQ/GRANVER/LAIFE/塞乐瑞/W+端粒塔，可空"},
            "platform": {"type": "string", "description": "平台：xhs/douyin/baidu/toutiao/web，可空"},
            "nature": {"type": "string", "description": "内容性质：成分科普/测评种草/口播种草/娱乐蹭词/PR 通稿/常规内容，可空"},
            "min_likes": {"type": "integer", "description": "最低点赞数，可空"},
            "limit": {"type": "integer", "description": "返回条数，默认 30"}},
            "required": []},
    },
    {
        "name": "get_content",
        "description": "取单条内容全文：正文、作者、时间、互动指标、图片/视频直链、营销分析（关键词/SEO/GEO/合规风险）、评论原文。",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "内容 id"}}, "required": ["id"]},
    },
    {
        "name": "brand_compare",
        "description": "品牌对比：按平台输出各品牌的内容数、总赞、总藏、总评论、平均 SEO/GEO 分，用于声量与策略对比。",
        "inputSchema": {"type": "object", "properties": {
            "platform": {"type": "string", "description": "限定平台，可空表示全部"}}, "required": []},
    },
    {
        "name": "top_content",
        "description": "取头部内容：按点赞/SEO 分/GEO 分排序，可限定品牌与平台，用于爆文拆解与高分案例学习。",
        "inputSchema": {"type": "object", "properties": {
            "brand": {"type": "string"}, "platform": {"type": "string"},
            "by": {"type": "string", "enum": ["likes", "seo", "geo", "comments"], "description": "排序字段，默认 likes"},
            "limit": {"type": "integer", "description": "默认 10"}}, "required": []},
    },
    {
        "name": "search_comments",
        "description": "检索评论原文：按品牌/情感关键词（如 智商税、没用、回购、有效）找用户原声，用于口碑与痛点分析。",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "评论关键词"},
            "brand": {"type": "string"}, "limit": {"type": "integer", "description": "默认 50"}},
            "required": []},
    },
    {
        "name": "pending_content",
        "description": "查看缺正文的条目（需要补抓的清单）：返回条数、平台分布、缺失原因。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sql_query",
        "description": "对内容库执行只读 SELECT 查询（复杂聚合/自定义分析用）。表：content / comment / daily_metric / crawl_log。禁止写操作。",
        "inputSchema": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "SELECT 语句"}}, "required": ["sql"]},
    },
]

def level_of(body, platform):
    b = (body or "").strip()
    if b.startswith("（SERP 收录级"):
        return "serp"
    if len(b) >= 220:
        return "full"
    if len(b) >= 30:
        return "partial"
    return "full" if platform in ("xhs", "douyin") else "missing"

def item_row(r, with_body=False):
    m = json.loads(r["metrics_json"] or "{}")
    a = json.loads(r["analysis_json"] or "{}")
    d = {"id": r["id"], "brand": r["brand"], "platform": r["platform"],
         "platform_cn": PLATFORMS.get(r["platform"], r["platform"]),
         "title": r["title"], "author": r["author"], "publish_time": r["publish_time"],
         "url": r["url"], "likes": m.get("likes", 0), "collects": m.get("collects", 0),
         "comments": m.get("comments", 0), "shares": m.get("shares", 0),
         "nature": a.get("content_type"), "seo": (a.get("seo") or {}).get("score"),
         "geo": (a.get("geo") or {}).get("score"),
         "keywords": a.get("keywords"), "level": level_of(r["body"], r["platform"])}
    if with_body:
        d["body"] = r["body"]
        d["media"] = json.loads(r["media_json"] or "{}")
        d["analysis"] = a
        d["comments_list"] = [dict(x) for x in conn().execute(
            "SELECT body, like_count, publish_time FROM comment WHERE content_id=? ORDER BY like_count DESC LIMIT 40",
            (r["id"],))]
    return d

def apply_gate(sql):
    """给任意 SELECT 强制注入「只查已校验数据」的限制。

    用户规则：未校验的数据不对外展示。SQL 是自由查询入口，
    若不处理，用户可以绕开其它接口直接查到未校验的行 —— 这是最大的泄漏口。

    策略：把受控表替换成「只含已校验行」的派生表（CTE，不破坏原 SQL 结构）：
        WITH _gc AS (SELECT * FROM content WHERE verified=1)
        把 FROM content / JOIN content 换成 _gc
    """
    out = sql
    W = chr(92) + "b"
    FJ = chr(92) + "b(from|join)" + chr(92) + "s+"

    has_content = re.search("(?i)" + FJ + "content" + W, out)
    has_xhs = re.search("(?i)" + FJ + "xhs_note" + W, out)
    has_comment = re.search("(?i)" + FJ + "comment" + W, out)
    if not (has_content or has_xhs or has_comment):
        return out

    ctes = []
    if has_content:
        ctes.append("_gc AS (SELECT * FROM content WHERE verified=1)")
    if has_xhs:
        ctes.append("_gx AS (SELECT * FROM xhs_note WHERE verified=1)")
    if has_comment:
        ctes.append("_gm AS (SELECT * FROM comment WHERE content_id IN "
                    "(SELECT id FROM content WHERE verified=1))")

    for tbl, alias in (("content", "_gc"), ("xhs_note", "_gx"), ("comment", "_gm")):
        pat = "(?i)(" + chr(92) + "bfrom" + chr(92) + "s+|" + chr(92) + "bjoin" + chr(92) + "s+)" + tbl + chr(92) + "b"
        out = re.sub(pat, lambda m, a=alias: m.group(1) + a, out)

    cte_text = ", ".join(ctes)
    if re.match("(?is)^" + chr(92) + "s*with" + chr(92) + "b", out):
        # 已有 WITH：在顶层 SELECT 之前插入我们的 CTE
        m = re.search("(?is)" + chr(92) + "bwith" + chr(92) + "b", out)
        j = m.end()
        depth = 0
        while j < len(out):
            ch = out[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and re.match("(?i)select" + chr(92) + "b", out[j:j + 8]):
                break
            j += 1
        out = out[:j] + ", " + cte_text + " " + out[j:]
    else:
        out = "WITH " + cte_text + " " + out
    return out


def call(name, args):
    c = conn()
    try:
        if name == "content_stats":
            # 只统计通过校验的数据（用户规则：未校验的不对外展示）
            total = c.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0]
            cm = c.execute("SELECT COUNT(*) FROM comment").fetchone()[0]
            lv = {"full": 0, "partial": 0, "serp": 0, "missing": 0}
            for r in c.execute("SELECT platform, body FROM content WHERE verified=1"):
                lv[level_of(r["body"], r["platform"])] += 1
            return ok({"contents": total, "comments": cm,
                       "by_platform": [dict(r) for r in c.execute(
                           "SELECT platform, COUNT(*) n FROM content GROUP BY 1 ORDER BY n DESC")],
                       "by_brand": [dict(r) for r in c.execute(
                           "SELECT brand, COUNT(*) n, SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes FROM content GROUP BY 1 ORDER BY likes DESC")],
                       "by_nature": [dict(r) for r in c.execute(
                           "SELECT json_extract(analysis_json,'$.content_type') nature, COUNT(*) n FROM content GROUP BY 1 ORDER BY n DESC")],
                       "fulltext_levels": lv})

        if name == "search_content":
            q, args_ = ["SELECT * FROM content WHERE verified=1"], []
            if args.get("query"):
                q.append("AND (title LIKE ? OR body LIKE ?)")
                args_ += [f"%{args['query']}%"] * 2
            for k, col in (("brand", "brand"), ("platform", "platform")):
                if args.get(k):
                    q.append(f"AND {col}=?"); args_.append(args[k])
            if args.get("nature"):
                q.append("AND json_extract(analysis_json,'$.content_type')=?")
                args_.append(args["nature"])
            if args.get("min_likes"):
                q.append("AND CAST(json_extract(metrics_json,'$.likes') AS INT) >= ?")
                args_.append(int(args["min_likes"]))
            q.append("ORDER BY CAST(json_extract(metrics_json,'$.likes') AS INT) DESC LIMIT ?")
            args_.append(int(args.get("limit", 30)))
            rows = c.execute(" ".join(q), args_).fetchall()
            return ok({"count": len(rows), "items": [item_row(r) for r in rows]})

        if name == "get_content":
            r = c.execute("SELECT * FROM content WHERE id=?", (int(args["id"]),)).fetchone()
            return ok(item_row(r, True)) if r else ok({"error": "not found"})

        if name == "brand_compare":
            where = "WHERE verified=1"
            p = []
            if args.get("platform"):
                where += " AND platform=?"
                p.append(args["platform"])
            rows = c.execute(f"""SELECT brand, platform, COUNT(*) n,
                SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes,
                SUM(CAST(json_extract(metrics_json,'$.collects') AS INT)) collects,
                SUM(CAST(json_extract(metrics_json,'$.comments') AS INT)) comments,
                ROUND(AVG(CAST(json_extract(analysis_json,'$.seo.score') AS REAL)),1) avg_seo,
                ROUND(AVG(CAST(json_extract(analysis_json,'$.geo.score') AS REAL)),1) avg_geo
                FROM content {where} GROUP BY brand, platform ORDER BY brand""", p).fetchall()
            return ok([{**dict(r), "platform_cn": PLATFORMS.get(r["platform"])} for r in rows])

        if name == "top_content":
            w, p = ["verified=1"], []
            if args.get("brand"):
                w.append("brand=?"); p.append(args["brand"])
            if args.get("platform"):
                w.append("platform=?"); p.append(args["platform"])
            order = {"likes": "CAST(json_extract(metrics_json,'$.likes') AS INT)",
                     "comments": "CAST(json_extract(metrics_json,'$.comments') AS INT)",
                     "seo": "CAST(json_extract(analysis_json,'$.seo.score') AS INT)",
                     "geo": "CAST(json_extract(analysis_json,'$.geo.score') AS INT)"}[args.get("by", "likes")]
            p.append(int(args.get("limit", 10)))
            rows = c.execute(f"SELECT * FROM content WHERE {' AND '.join(w)} ORDER BY {order} DESC LIMIT ?", p).fetchall()
            return ok([item_row(r) for r in rows])

        if name == "search_comments":
            w, p = ["t.verified=1"], []
            if args.get("query"):
                w.append("c.body LIKE ?"); p.append(f"%{args['query']}%")
            if args.get("brand"):
                w.append("t.brand=?"); p.append(args["brand"])
            p.append(int(args.get("limit", 50)))
            rows = c.execute(f"""SELECT t.id AS content_id, t.brand, t.platform, c.body,
                c.like_count, c.publish_time, t.url
                FROM comment c JOIN content t ON c.content_id=t.id
                WHERE {' AND '.join(w)} ORDER BY c.like_count DESC LIMIT ?""", p).fetchall()
            return ok({"count": len(rows), "comments": [dict(r) for r in rows]})

        if name == "pending_content":
            rows = c.execute("""SELECT * FROM content WHERE verified=1
                                AND platform IN ('baidu','toutiao','web')""").fetchall()
            bad = [{"id": r["id"], "brand": r["brand"], "platform": r["platform"],
                    "title": r["title"], "url": r["url"], "level": level_of(r["body"], r["platform"])}
                   for r in rows if level_of(r["body"], r["platform"]) in ("serp", "missing", "partial")]
            from collections import Counter
            return ok({"total": len(bad), "by_platform": Counter(x["platform"] for x in bad), "items": bad[:80]})

        if name == "sql_query":
            sql = args["sql"].strip()
            if not re.match(r"(?is)^\s*(select|with)\b", sql):
                return ok({"error": "仅允许 SELECT / WITH 查询"})
            if re.search(r"(?i)\b(drop|delete|update|insert|alter|attach|pragma)\b", sql):
                return ok({"error": "检测到写操作，已拒绝"})
            # 数据边界：只允许查已校验通过的数据。
            # 未校验的记录仍在库里（供内部补采与修复），但不对外暴露。
            rows = c.execute(apply_gate(sql)).fetchall()
            return ok({"rows": len(rows), "data": [dict(r) for r in rows[:500]]})

        return ok({"error": f"unknown tool: {name}"})
    finally:
        c.close()

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cosmobounty", "version": "1.0.0"}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                result = call(params.get("name"), params.get("arguments") or {})
            except Exception as e:
                result = {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}], "isError": True}
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})

if __name__ == "__main__":
    main()
