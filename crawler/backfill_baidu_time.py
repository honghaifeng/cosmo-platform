#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度系（baidu / toutiao / web）发布时间回填。

背景（2026-10-24）：
  百度 SERP 抓取时，把「标题 + 摘要 + 链接」存进了库，但很多条目的
  发布时间没抓到 → verify_and_gate 的 H2 规则拦下 → 对外只剩 3 条可见。
  数据本身是好的，缺的只是时间戳这一列。

三级回退（越靠前越可信，优先级从高到低）：
  L1 URL 内嵌日期   —— /2025-11-23/detail-xxx、/202605/21/c167238.html 这类
                      路径里的日期就是发布日，置信度最高，且零网络成本
  L2 原文 meta 标签 —— article:published_time / og:published_time / pubDate /
                      datePublished（JSON-LD）—— 结构化字段，站点自己声明的
  L3 原文正文日期  —— 页面上「发布于 2026-01-23」「2026年5月21日」这类
                      显式字样；只认带「发布/发表于/来源/时间」上下文或
                      页面顶部的日期，避免把正文里提到的别的日期当成发布时间

每条回填都记录来源（pub_time_src），便于人工抽查与回溯。
时间只接受 YYYY-MM-DD 或 YYYY-MM-DD HH:MM，且不得晚于今天（与 H2 规则一致）。

用法：
  python3 crawler/backfill_baidu_time.py              # 全量回填
  python3 crawler/backfill_baidu_time.py --report     # 只看会回填什么
  python3 crawler/backfill_baidu_time.py --only 123,456
"""
import argparse
import concurrent.futures as cf
import datetime
import hashlib
import html as ihtml
import os
import re
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
CACHE = os.path.join(ROOT, "data", "raw", "pubtime_cache")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TODAY = datetime.date.today()

# ── L1：URL 内嵌日期 ──────────────────────────────────────────────
URL_DATE_RES = [
    # 2025-11-23 / 2025/11/23
    (re.compile(r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)"), 1),
    # /20251123/
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), 1),
    # /2025/11/23/ 已被上面覆盖
    # /202605/21/  六位年月 + 两位日
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})[-/](\d{1,2})(?!\d)"), 1),
]

# ── L2：meta / JSON-LD 时间标签 ───────────────────────────────────
META_TAGS = [
    re.compile(r'<meta[^>]+(?:property|name)\s*=\s*["\'](?:article:published_time|og:published_time|'
               r'pubdate|publishdate|publish_date|PubDate|firstpublishedtime|weibo:article:create_at|'
               r'og:release_date|datePublished|contentPubDate)["\'][^>]*content\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+(?:property|name)\s*=\s*["\']'
               r'(?:article:published_time|og:published_time|pubdate|publishdate|PubDate|'
               r'firstpublishedtime|datePublished)["\']', re.I),
    # JSON-LD 里 "datePublished": "2026-01-23T16:00:00+08:00"
    re.compile(r'["\']datePublished["\']\s*:\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'["\']publishTime["\']\s*:\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'["\']publish_time["\']\s*:\s*["\']?(\d{9,13})', re.I),
    # 腾讯云这类：<span class="date-text">发布<!-- -->于 <!-- -->2026-01-23 16:22:33</span>
    re.compile(r'date-text["\'][^>]*>\s*发布\s*(?:<!--\s*)?于\s*(?:<!--\s*)?'
               r'(20\d{2}[-/]\d{1,2}[-/]\d{1,2}[^<"]{0,8})', re.I),
    # JSON 里的结构化字段（比正文裸日期可信，升级为 L2）
    re.compile(r'["\']pubDate["\']\s*:\s*["\'](20\d{2}-\d{2}-\d{2}[^"\']*)["\']', re.I),
    re.compile(r'["\']publish_date["\']\s*:\s*["\'](20\d{2}-\d{2}-\d{2}[^"\']*)["\']', re.I),
    re.compile(r'["\']edit_date_str["\']\s*:\s*["\']发布于\s+(20\d{2}-\d{2}-\d{2})', re.I),
    re.compile(r'["\']created["\']\s*:\s*["\'](20\d{2}-\d{2}-\d{2}[^"\']*)["\']', re.I),
]

# ── L3：正文可见日期 ──────────────────────────────────────────────
# 必须带「发布/发表于/来源/时间/更新」上下文，否则正文里随便一个日期都会被误认
BODY_CONTEXT_RES = [
    re.compile(r'(?:发布(?:于|时间)?|发表于|来源[:：]|发布时间|更新(?:于|时间)?|'
               r'发稿(?:时间)?|首发(?:于|时间)?)[\s:：]*(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})', re.I),
    re.compile(r'(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[\s|]*(?:发布|来源|发表于)'),
]
# 页面顶部裸日期（常见于新闻站 <div class="pubtime">2026-05-21</div>）
BARE_TOP_RES = [
    re.compile(r'(?:class|id)\s*=\s*["\'][^"\']*(?:pubtime|pub-date|publish|article-time|'
               r'news-time|date-text|source-time|entry-date|news_date|art_time)[^"\']*["\'][^>]*>\s*'
               r'(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})', re.I),
]
# 评论/回复容器里的日期一律不算发布时间（实测踩过：
#   ilife.cn 点评页的 <p class="time"> 是用户点评时间；
#   wiki.smzdm.com 的 <div class="lc-time"> 是评论区时间）
COMMENT_CTX_RX = re.compile(
    r'(?:class|id)\s*=\s*["\'][^"\']*(?:comment|reply|pinglun|pl-|lc-|review|'
    r'discuss|message|timeline|user-time|post-time)[^"\']*["\']', re.I)
# 评论区整块先剥掉再找日期
COMMENT_BLOCK_RES = [
    re.compile(r'<(?:div|ul|ol|section)[^>]*(?:class|id)\s*=\s*["\'][^"\']*'
               r'(?:comment|reply|pinglun|review|discuss|message-list|reply-list)'
               r'[^"\']*["\'][^>]*>.*?</(?:div|ul|ol|section)>', re.S | re.I),
]

TXT_DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")


def norm(y, mo, d, with_time=None):
    """校验并归一化日期；不合法返回 None。"""
    try:
        y, mo, d = int(y), int(mo), int(d)
        dt = datetime.date(y, mo, d)
    except (ValueError, TypeError):
        return None
    if dt > TODAY:          # 未来时间 → 不可信
        return None
    if dt < datetime.date(2000, 1, 1):   # 太早，多半是解析错
        return None
    if with_time:
        return f"{dt.isoformat()} {with_time}"
    return dt.isoformat()


def from_url(url):
    """L1：URL 内嵌日期。命中第一个合法即返回。"""
    if not url:
        return None, None
    # 排除明显的非发布时间路径（如 /20250101/ 订单号）
    for rx, _ in URL_DATE_RES:
        for m in rx.finditer(url):
            if rx.pattern.startswith("(?<!\\d)(20\\d{2})(\\d{2})(\\d{2})"):
                v = norm(m.group(1), m.group(2), m.group(3))
            else:
                v = norm(m.group(1), m.group(2), m.group(3))
            if v:
                return v, "L1-url"
    return None, None


def parse_dt_str(s):
    """把 '2026-01-23T16:22:33+08:00' / '2025-11-23 11:21:00' 归一成标准格式。"""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})[日]?(?:[T\s]+(\d{1,2}):(\d{2}))?", s)
    if not m:
        m2 = TXT_DATE_RE.search(s)
        if not m2:
            return None
        return norm(m2.group(1), m2.group(2), m2.group(3))
    base = norm(m.group(1), m.group(2), m.group(3))
    if not base:
        return None
    if m.group(4):
        hh, mm = int(m.group(4)), int(m.group(5))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{base} {hh:02d}:{mm:02d}"
    return base


def strip_comments(html):
    """剥离评论区整块，避免把用户点评时间当成发布时间。"""
    for rx in COMMENT_BLOCK_RES:
        html = rx.sub(" ", html)
    return html


def _nearest_attrs(html, pos):
    """取候选位置往前最近的一个 class/id 值，用来判断它处在什么容器里。"""
    head = html[max(0, pos - 500):pos]
    ms = list(re.finditer(r'(?:class|id)\s*=\s*["\']([^"\']*)["\']', head, re.I))
    return ms[-1].group(1) if ms else ""


# 容器语义：命中即加减分
CTX_PUBLISH = re.compile(r'(?:pubtime|pub-date|publish|article-time|news-time|date-text|'
                         r'source-time|entry-date|news_date|art_time|time)', re.I)
CTX_LISTITEM = re.compile(r'(?:^|[\s_-])(?:item|list|li|card|entry|news|article-item|'
                           r'mb-info|t1|t2|info)([\s_-]|$)', re.I)
CTX_COMMENT = re.compile(r'(?:comment|reply|pinglun|pl-|lc-|review|discuss|message|'
                         r'timeline|user-time|post-time)', re.I)
# 上下文关键词
CTX_KEYWORD = re.compile(r'(?:发布(?:于|时间)?|发表于|来源[:：]|发布时间|更新(?:于|时间)?|'
                         r'发稿(?:时间)?|首发(?:于|时间)?|上(?:线|架)于)', re.I)


def score_candidate(html, m):
    """给一个日期候选打分。高分才可信。"""
    pos = m.start()
    attrs = _nearest_attrs(html, pos)
    score = 0
    why = []
    if CTX_PUBLISH.search(attrs):
        score += 40
        why.append("容器=发布时间位")
    if CTX_COMMENT.search(attrs):
        score -= 60
        why.append("容器=评论区")
    if CTX_LISTITEM.search(attrs):
        score -= 35
        why.append("容器=列表条目")
    window = html[max(0, pos - 60):pos + 60]
    if CTX_KEYWORD.search(window):
        score += 45
        why.append("邻域有发布/来源关键词")
    if pos < 2500:
        score += 20
        why.append("位于页面头部")
    # 明显是版权年份 / 备案号 / 促销年份的，降低权重
    if re.search(r'(?:©|版权|备案|copyright|all\s*rights)', window, re.I):
        score -= 30
        why.append("疑似版权年份")
    return score, why


def from_html(html):
    """L2/L3：从原文 HTML 提取时间。返回 (value, source)。"""
    if not html:
        return None, None
    # ── L2：结构化 meta / JSON-LD ───────────────────────────
    for rx in META_TAGS:
        m = rx.search(html)
        if m:
            v = parse_dt_str(m.group(1))
            if v:
                return v, "L2-meta"
    for rx in (re.compile(r'["\'](?:publishTime|createTime|firstPublishTime)["\']\s*:\s*["\']?(\d{10,13})', re.I),):
        m = rx.search(html)
        if m:
            ts = int(m.group(1))
            if ts > 1e12:
                ts //= 1000
            try:
                dt = datetime.datetime.fromtimestamp(ts)
                if dt.date() <= TODAY:
                    return dt.strftime("%Y-%m-%d %H:%M"), "L2-ts"
            except (ValueError, OSError):
                pass

    # ── L3：正文可见日期（先剥评论区，再按可信度打分选最优） ──
    h = strip_comments(html)
    best = None
    for m in re.finditer(r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})", h):
        v = norm(m.group(1), m.group(2), m.group(3))
        if not v:
            continue
        sc, why = score_candidate(h, m)
        if sc >= 45 and (best is None or sc > best[0]):
            best = (sc, v, why)
    if best:
        return best[1], "L3-scored"
    return None, None


def curl(url, timeout=18):
    try:
        r = subprocess.run(
            ["curl", "-sL", "--compressed", "-A", UA, "--max-time", str(timeout),
             "-H", "Accept-Language: zh-CN,zh;q=0.9", url],
            capture_output=True)
        raw = r.stdout or b""
        if not raw:
            return ""
        for enc in ("utf-8", "gb18030"):
            try:
                t = raw.decode(enc)
                if t.count("\ufffd") < 20:
                    return t
            except Exception:
                continue
        return raw.decode("utf-8", "ignore")
    except Exception:
        return ""


def resolve_baidu_jump(url, timeout=15):
    """百度跳转链解析成真实 URL（-L 跟随重定向后看最终地址）。"""
    if "baidu.com/link" not in (url or ""):
        return url
    try:
        r = subprocess.run(
            ["curl", "-sL", "-o", "/dev/null", "-w", "%{url_effective}", "-A", UA,
             "--max-time", str(timeout), url], capture_output=True, text=True)
        eff = (r.stdout or "").strip()
        if eff.startswith("http") and "baidu.com/link" not in eff:
            return eff
    except Exception:
        pass
    return url


def cache_path(url):
    return os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest() + ".html")


def fetch_cached(url):
    """带本地缓存的原文抓取。返回 (html, real_url)。"""
    os.makedirs(CACHE, exist_ok=True)
    real = resolve_baidu_jump(url)
    cp = cache_path(real)
    if os.path.exists(cp) and os.path.getsize(cp) > 400:
        try:
            return open(cp, encoding="utf-8", errors="ignore").read(), real
        except Exception:
            pass
    html = curl(real)
    if html:
        try:
            with open(cp, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass
    return html, real


def work(row):
    cid, url = row
    # L1 先走，零成本
    v, src = from_url(url)
    if v:
        return cid, v, src, None
    html, real = fetch_cached(url)
    v, src = from_html(html)
    if v:
        return cid, v, src, real
    return cid, None, None, real


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只统计不写库")
    ap.add_argument("--only", default="", help="只处理指定 id，逗号分隔")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # 加来源列
    have = {r[1] for r in con.execute("PRAGMA table_info(content)")}
    if "pub_time_src" not in have:
        con.execute("ALTER TABLE content ADD COLUMN pub_time_src TEXT")
        con.commit()

    q = """SELECT id, url, brand, platform, title FROM content
           WHERE platform IN ('baidu','toutiao','web')
             AND (publish_time IS NULL OR publish_time='')"""
    rows = con.execute(q).fetchall()
    if args.only:
        keep = {int(x) for x in args.only.split(",") if x.strip()}
        rows = [r for r in rows if r["id"] in keep]
    print(f"待回填 {len(rows)} 条", flush=True)

    # 先本地算一遍 L1（URL 内嵌日期），这部分不用网络
    todo, prefilled = [], []
    for r in rows:
        v, src = from_url(r["url"])
        if v:
            prefilled.append((r["id"], v, src))
        else:
            todo.append((r["id"], r["url"]))
    print(f"  L1-URL 直接命中 {len(prefilled)} 条，需联网 {len(todo)} 条", flush=True)

    results = []
    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(work, t): t for t in todo}
            for i, f in enumerate(cf.as_completed(futs), 1):
                try:
                    results.append(f.result())
                except Exception:
                    results.append((futs[f][0], None, None, None))
                if i % 15 == 0:
                    print(f"    进度 {i}/{len(todo)}", flush=True)

    # 汇总
    plan = {}
    for cid, v, src in prefilled:
        plan[cid] = (v, src)
    for cid, v, src, real in results:
        if v:
            plan[cid] = (v, src)
    # URL 已解析但库里还存着跳转链的，同步更新为真实 URL
    real_map = {cid: real for cid, v, src, real in results if real and "baidu.com/link" not in real}

    by_src = {}
    for cid, (v, src) in plan.items():
        by_src[src] = by_src.get(src, 0) + 1
    print("\n回填来源分布：")
    for k in sorted(by_src):
        print(f"  {k:14s}{by_src[k]:5d}")
    print(f"  合计可回填 {len(plan)} / {len(rows)}")

    if args.report:
        for r in rows:
            p = plan.get(r["id"])
            mark = "OK " if p else "-- "
            print(f"  {mark}{r['id']:6d} {r['platform']:8s} {r['brand']:12s} "
                  f"{p[0] if p else ''} {p[1] if p else ''} | {r['title'][:38]}")
        con.close()
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for cid, (v, src) in plan.items():
        cur = con.execute("UPDATE content SET publish_time=?, pub_time_src=? "
                          "WHERE id=? AND (publish_time IS NULL OR publish_time='')",
                          (v, src, cid))
        n += cur.rowcount
    for cid, real in real_map.items():
        con.execute("UPDATE content SET url=? WHERE id=? AND url LIKE '%baidu.com/link%'", (real, cid))
    con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) "
                "VALUES ('baidu','ALL','pub_time_backfill',?,?,?)",
                (now, "ok", f"回填 {n} 条 / 待处理 {len(rows)} 条 / 来源 {by_src}"))
    con.commit()

    still = con.execute("""SELECT COUNT(*) FROM content WHERE platform IN ('baidu','toutiao','web')
                            AND (publish_time IS NULL OR publish_time='')""").fetchone()[0]
    print(f"\n已回填 {n} 条，仍缺时间 {still} 条")
    con.close()


if __name__ == "__main__":
    main()
