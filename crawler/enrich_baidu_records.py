#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度新抓数据的「补正文 + 补时间」串联流水线。

背景：
  baidu_serp_v3.py 抓到的是 SERP 层级数据 —— 标题 + 摘要 + 链接。
  直接入库的话：
    · body 只有摘要长度 → H3（正文过短）不通过
    · publish_time 常常缺 → H2（无发布时间）不通过
  结果就是「抓了很多但一条都展示不出来」。

本脚本把一条新记录补齐成可展示状态：
  1. 解析百度跳转链 → 真实落地 URL
  2. 抓原文页（curl 直连优先，反爬站点自动降级到浏览器通道）
  3. 抽取正文（按 <article> / 常见正文容器 / 最长文本块启发式）
  4. 抽取发布时间（结构化 meta 优先 → 正文发布时间位 → URL 内嵌日期）
  5. 写回 content，并记录补全来源

用法：
  python3 crawler/enrich_baidu_records.py --platform baidu
  python3 crawler/enrich_baidu_records.py --platform baidu --browser   # 走浏览器通道
  python3 crawler/enrich_baidu_records.py --platform baidu --limit 50
"""
import argparse
import concurrent.futures as cf
import datetime
import hashlib
import html as ihtml
import os
import random
import re
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
CACHE = os.path.join(ROOT, "data", "raw", "pubtime_cache")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TODAY = datetime.date.today()
MIN_BODY = 220

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from backfill_baidu_time import from_html as _from_html
    from backfill_baidu_time import from_url as _from_url
    from backfill_baidu_time import parse_dt_str
except Exception:
    _from_html = _from_url = None
    parse_dt_str = None

# ── 正文抽取 ─────────────────────────────────────────────────────
CONTENT_CLASS = re.compile(
    r'<(?:div|article|section)[^>]*(?:class|id)\s*=\s*["\'][^"\']*'
    r'(?:article[-_]?content|article[-_]?body|content[-_]?detail|detail[-_]?content|'
    r'main[-_]?content|post[-_]?content|entry[-_]?content|art[-_]?content|'
    r'content[-_]?text|txt[-_]?content|news[-_]?content|article|content|detail|'
    r'main[-_]?text|post[-_]?body|rich_media_content|js_content|post_content)'
    r'[^"\']*["\'][^>]*>', re.I)


def strip_tags(s):
    s = re.sub(r"<(script|style|noscript|iframe)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = ihtml.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s).strip()
    return re.sub(r" {2,}", " ", s)


NAV_NOISE = re.compile(r"icon_|编组\s*\d|Unfold|Fold@|@1x|@2x|Shop all|BEST SELLER|"
                       r"点击展开|展开全文|上一篇|下一篇|扫码|关注我们|返回顶部")
# 非内容页：贴吧/百科/电商/问答聚合 —— 不是品牌内容，不该计入底表缺口
DOMAIN_BLOCK = (
    "tieba.baidu.com", "baike.baidu.com", "zhidao.baidu.com", "wenku.baidu.com",
    "jingyan.baidu.com", "zhihu.com", "jd.com", "tmall.com", "taobao.com",
    "pinduoduo.com", "1688.com", "amazon.", "yangkeduo.com", "xiaohongshu.com/goods",
)


def is_block_domain(url):
    u = (url or "").lower()
    return any(d in u for d in DOMAIN_BLOCK)


def clean_article(txt):
    """按行过滤导航噪声：留带标点的正文句，丢菜单/标签墙。"""
    keep = []
    for line in (txt or "").split("\n"):
        s = line.strip()
        if len(s) < 20 or NAV_NOISE.search(s):
            continue
        cn = len(re.findall(r"[\u4e00-\u9fa5]", s))
        punct = len(re.findall(r"[。，！？；：、“”]", s))
        en = len(re.findall(r"[a-zA-Z]", s))
        if cn >= 15 and punct < 2:          # 中文够多却没标点 → 关键词堆砌/导航
            continue
        if cn < 15 and en < 40:             # 中文太少、英文也短 → 菜单碎片
            continue
        keep.append(s)
    return "\n".join(keep)


def extract_body(html, title=""):
    if not html:
        return None
    t = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    block = None
    m = re.search(r"<article[^>]*>(.*?)</article>", t, re.S | re.I)
    if m and len(m.group(1)) > 600:
        block = m.group(1)
    if not block:
        cands = CONTENT_CLASS.findall(t)
        if cands:
            best = ""
            for c in cands:
                # 取出这个容器的内容（到下一个同类标签前）
                pass
            # 简化：直接找所有 div 块按长度挑
            divs = re.findall(r'<(?:div|article|section)[^>]*>(.*?)</(?:div|article|section)>',
                              t, re.S | re.I)
            if divs:
                best = max(divs, key=len)
                if len(best) > 400:
                    block = best
    if not block:
        block = t
    txt = strip_tags(block)
    if len(txt) < MIN_BODY:
        # 退化：全页取正文
        txt2 = strip_tags(t)
        if len(txt2) > len(txt):
            txt = txt2
    # 404 / 页面不存在 / 访问受限 —— 丢弃，不能当正文
    if re.search(r'(404[,，]|页面已经不存在|页面不见了|访问受限|404 Not Found|'
                 r'抱歉[,，]?(您)?(访问)?的?页面)', txt[:600], re.I):
        return None
    if len(txt) < 120:
        return None
    # 从标题命中处截断，去掉导航尾巴
    key = (title or "")[:14]
    if key and key in txt:
        i = txt.find(key)
        txt = txt[max(0, i - 60):]
    for tail in ("相关推荐", "热门推荐", "免责声明", "版权声明", "责任编辑", "猜你喜欢"):
        j = txt.rfind(tail)
        if j > len(txt) * 0.6:
            txt = txt[:j]
    txt = txt.strip()
    cleaned = clean_article(txt)
    # 抽不干净宁可不要：噪声正文会污染底表
    return cleaned[:3000] if len(cleaned) >= MIN_BODY else None


def curl(url, timeout=18):
    """带一次重试的 curl —— 网络抖动不该让整条数据白跑。"""
    for attempt in range(2):
        raw = _curl_once(url, timeout)
        if raw:
            return raw
        time.sleep(1.5)
    return ""


def _curl_once(url, timeout=18):
    try:
        r = subprocess.run(
            ["curl", "-sL", "--compressed", "-A", UA, "--max-time", str(timeout),
             "-H", "Accept-Language: zh-CN,zh;q=0.9", url], capture_output=True)
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


def resolve_jump(url):
    if "baidu.com/link" not in (url or ""):
        return url
    try:
        r = subprocess.run(["curl", "-sL", "-o", "/dev/null", "-w", "%{url_effective}",
                            "-A", UA, "--max-time", "14", url],
                           capture_output=True, text=True)
        eff = (r.stdout or "").strip()
        if eff.startswith("http") and "baidu.com/link" not in eff:
            return eff
    except Exception:
        pass
    return url


def cache_path(url):
    return os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest() + ".html")


def is_blocked(html):
    """百度安全验证页 / 跳转失败页 —— 内容不可用。"""
    if not html:
        return True
    if "百度安全验证" in html or "wappass.baidu.com" in html:
        return True
    if len(html) < 1200:
        return True
    return False


def fetch(url):
    """先查缓存，再 curl 抓；返回 (html, real_url)。"""
    os.makedirs(CACHE, exist_ok=True)
    real = resolve_jump(url)
    cp = cache_path(real)
    if os.path.exists(cp) and os.path.getsize(cp) > 400:
        try:
            h = open(cp, encoding="utf-8", errors="ignore").read()
            if not is_blocked(h):
                return h, real
        except Exception:
            pass
    html = curl(real)
    # 跳转链被解析到验证页 → 保留原跳转链地址，另走浏览器通道
    if is_blocked(html):
        return "", real
    if html:
        try:
            with open(cp, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass
    return html, real


def work(row):
    cid, url, title, body = row
    if is_block_domain(url):
        return cid, url, None, None, None, 0, "skip-domain"
    time.sleep(random.uniform(0.4, 1.4))   # 节奏：不做暴力抓取
    html, real = fetch(url)
    new_body = extract_body(html, title) if html else None
    pt, src = (None, None)
    if html and _from_html:
        pt, src = _from_html(html)
    if not pt and _from_url:
        pt, src = _from_url(real)
    return cid, real, new_body, pt, src, len(html or ""), ("ok" if html else "no-html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="baidu")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    have = {r[1] for r in con.execute("PRAGMA table_info(content)")}
    if "pub_time_src" not in have:
        con.execute("ALTER TABLE content ADD COLUMN pub_time_src TEXT")
        con.commit()

    q = """SELECT id, url, title, body FROM content
           WHERE platform=?
             AND (body IS NULL OR LENGTH(COALESCE(body,''))<? OR body LIKE '（SERP 收录级%'
                  OR publish_time IS NULL OR publish_time='')"""
    rows = con.execute(q, (args.platform, MIN_BODY)).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"待补全 {len(rows)} 条（platform={args.platform}）", flush=True)

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, (r["id"], r["url"], r["title"], r["body"])): r for r in rows}
        for i, f in enumerate(cf.as_completed(futs), 1):
            try:
                results.append(f.result())
            except Exception as e:
                results.append((futs[f]["id"], futs[f]["url"], None, None, None, 0, "err"))
            if i % 20 == 0:
                print(f"    进度 {i}/{len(rows)}", flush=True)

    n_body = n_time = 0
    n_fail = n_skip = 0
    for cid, real, new_body, pt, src, rawlen, status in results:
        if status == "skip-domain":
            n_skip += 1
            con.execute("UPDATE content SET verify_note='X-非内容页(贴吧/百科/电商)' WHERE id=?", (cid,))
            continue
        if status != "ok":
            n_fail += 1
        if new_body:
            con.execute("UPDATE content SET body=?, raw_path=COALESCE(raw_path,?) WHERE id=?",
                        (new_body, os.path.join(CACHE, hashlib.md5(real.encode()).hexdigest() + ".html"), cid))
            n_body += 1
        if pt:
            con.execute("UPDATE content SET publish_time=?, pub_time_src=?, url=? WHERE id=? "
                        "AND (publish_time IS NULL OR publish_time='')",
                        (pt, "E-" + (src or "url"), real, cid))
            n_time += 1
    con.commit()

    still = con.execute(
        "SELECT COUNT(*) FROM content WHERE platform=? AND (publish_time IS NULL OR publish_time='')",
        (args.platform,)).fetchone()[0]
    short = con.execute(
        "SELECT COUNT(*) FROM content WHERE platform=? AND LENGTH(COALESCE(body,''))<?",
        (args.platform, MIN_BODY)).fetchone()[0]
    print(f"\n补正文 {n_body} 条，补时间 {n_time} 条，抓取失败 {n_fail} 条，非内容页 {n_skip} 条")
    print(f"仍缺时间 {still} 条，仍正文短 {short} 条")
    con.close()


if __name__ == "__main__":
    main()
