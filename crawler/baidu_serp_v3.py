#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度全量采集 v3 —— 浏览器通道（唯一可用通道）。

背景：
  · urllib/curl 直连百度 → 命中「百度安全验证」跳转（wappass.baidu.com），SERP 全空
  · 已验证：带真实 Chromium 指纹 + 持久化 profile（保留登录态与风控通过记录）可拿到 SERP
  · 旧脚本 baidu_serp_full.py --browser 每次都新建临时上下文，profile 用不上 → 仍被拦

本脚本要点：
  1. 复用 data/browser_profiles/baidu* 里的持久化 profile（保留 Cookie / 风控通行记录）
  2. 关键词覆盖：品牌名 + 别名 + 产品线词 + 品类场景词
  3. 翻页 pn=0,10,20…（百度约 10 页封顶），单页解析不到结果即停
  4. 结果按「真实落地 URL」全局去重（跳转链会变，落地 URL 才稳定）
  5. 每条结果记 keyword / page / SERP 显示的日期，便于回溯
  6. 速度：每页随机等待 6~14 秒，关键词之间额外 8~18 秒（百度对高频请求敏感）

用法：
  tools/MediaCrawler/.venv/bin/python crawler/baidu_serp_v3.py --pages 6
  tools/MediaCrawler/.venv/bin/python crawler/baidu_serp_v3.py --brand 塞乐瑞 --pages 4
  tools/MediaCrawler/.venv/bin/python crawler/baidu_serp_v3.py --profile-dir data/browser_profiles/baidu
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw", "baidu_v3")
STATE_DIR = os.path.join(ROOT, "data", "browser_profiles")

# ── SERP 解析 ───────────────────────────────────────────────────
# 结果块：<div id="1" ...> 或 class="result c-container"
BLOCK_SPLIT = re.compile(
    r'<div[^>]+(?:id="[rs_]*\d+"|class="[^"]*\b(?:result|c-container)\b[^"]*")[^>]*>', re.I)
H3_LINK = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
LANDURL = re.compile(r'data-landurl="([^"]+)"', re.I)
SHOWURL = re.compile(r'class="[^"]*c-showurl[^"]*"[^>]*>(.*?)<', re.S)
SNIPPET_RX = re.compile(
    r'class="[^"]*(?:content-right|c-span-last|c-abstract|abstract)[^"]*"[^>]*>(.*?)<', re.S)

DATE_FULL = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
DATE_MD = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日(?!\d)")
DATE_YMD = re.compile(r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
REL_DAY = re.compile(r"(\d+)\s*天前")
REL_HOUR = re.compile(r"(\d+)\s*小时前")
REL_MONTH = re.compile(r"(\d+)\s*个月前")
REL_YEAR = re.compile(r"(\d+)\s*年前")


def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = s.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", s).strip()


def norm_date(text, today):
    if not text:
        return None
    m = DATE_FULL.search(text)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
    else:
        m = DATE_YMD.search(text)
        if m:
            y, mo, d = m.group(1), m.group(2), m.group(3)
        else:
            m = DATE_MD.search(text)
            if m:
                y, mo, d = today.year, m.group(1), m.group(2)
            else:
                m = REL_DAY.search(text)
                if m:
                    return (today - datetime.timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
                m = REL_HOUR.search(text)
                if m:
                    return (today - datetime.timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
                m = REL_MONTH.search(text)
                if m:
                    return (today - datetime.timedelta(days=30 * int(m.group(1)))).strftime("%Y-%m-%d")
                m = REL_YEAR.search(text)
                if m:
                    return (today - datetime.timedelta(days=365 * int(m.group(1)))).strftime("%Y-%m-%d")
                if "昨天" in text:
                    return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                if "前天" in text:
                    return (today - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
                return None
    try:
        dt = datetime.date(int(y), int(mo), int(d))
    except ValueError:
        return None
    if dt > today:
        return None
    return dt.strftime("%Y-%m-%d")


def parse_serp(html, today):
    items = []
    blocks = BLOCK_SPLIT.split(html)
    for blk in blocks[1:]:
        m = H3_LINK.search(blk)
        if not m:
            continue
        url, title = m.group(1), strip_tags(m.group(2))
        if not title or not url.startswith("http"):
            continue
        real = ""
        lm = LANDURL.search(blk)
        if lm:
            real = lm.group(1).strip()
        else:
            sm = SHOWURL.search(blk)
            if sm:
                cand = strip_tags(sm.group(1))
                if cand.startswith("http"):
                    real = cand
        snippet = ""
        cands = SNIPPET_RX.findall(blk)
        if cands:
            snippet = max((strip_tags(c) for c in cands), key=len)
        pub = None
        for pat in (DATE_FULL, DATE_YMD, DATE_MD, REL_DAY, REL_HOUR):
            mm = pat.search(blk)
            if mm:
                pub = norm_date(mm.group(0), today)
                if pub:
                    break
        if pub is None and ("昨天" in blk or "前天" in blk):
            pub = norm_date("昨天", today)
        items.append({
            "title": title, "url": url, "real_url": real,
            "snippet": snippet[:400], "publish_time": pub,
        })
    return items


# ── DOM 抽取（正则跟不上百度新结构，优先用 DOM）────────────────
DOM_EXTRACT_JS = """() => {
  const root = document.querySelector('#content_left');
  if (!root) return [];
  const blocks = root.querySelectorAll('div.result, div.c-container, div[class*="c-container"]');
  const out = [];
  const seen = new Set();
  blocks.forEach((b) => {
    const h3 = b.querySelector('h3');
    const a = h3 ? h3.querySelector('a') : b.querySelector('a[href]');
    if (!a) return;
    const title = (a.innerText || '').trim();
    if (!title) return;
    const href = a.getAttribute('href') || '';
    const land = b.getAttribute('data-landurl') || b.getAttribute('mu') || '';
    // 摘要：优先已知容器类；否则用「整块文本去掉标题首行」
    let snip = '';
    const cand = b.querySelectorAll(
      '[class*="content-right"], [class*="c-abstract"], [class*="abstract"], ' +
      '[class*="summary"], [class*="c-span-last"], [class*="content_kj8hy"], ' +
      '[class*="_content_"], [class*="cos-row"], [class*="c-line-clamp"]');
    cand.forEach((c) => {
      const t = (c.innerText || '').trim();
      if (t.length > snip.length) snip = t;
    });
    if (snip.length < 40) {
      // 兜底：整块 innerText 按行拆，去掉标题行、来源域名行、纯链接行
      const lines = (b.innerText || '').split(/\\r?\\n/).map(x => x.trim())
        .filter(x => x && x !== title);
      const dom = (b.getAttribute('data-landurl') || '');
      const filtered = lines.filter(x => {
        if (x === title) return false;
        if (/^https?:\/\//.test(x)) return false;
        if (dom && x === dom) return false;
        try { if (new URL(x).hostname) return false; } catch (e) {}
        return true;
      });
      snip = filtered.join(' ').trim();
    }
    if (snip.startsWith(title)) snip = snip.slice(title.length).trim();
    // 时间：块内第一个可识别的日期文本
    let pub = '';
    const txt = (b.innerText || '');
    const m = txt.match(/20\d{2}[年\/-]\d{1,2}[月\/-]\d{1,2}/);
    if (m) pub = m[0];
    if (!pub) {
      const m2 = txt.match(/\d{1,2}[月]\d{1,2}[日]/);
      if (m2) pub = m2[0];
    }
    const key = (land || href) + '|' + title;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({title: title, url: href, real_url: land, snippet: snip.slice(0, 500), pub_raw: pub});
  });
  return out;
}"""


def extract_dom(page, today):
    """用浏览器 DOM 抽取 SERP 结果，返回解析后的 item 列表。"""
    try:
        raw = page.evaluate(DOM_EXTRACT_JS)
    except Exception as e:
        print(f"    (DOM 抽取失败：{type(e).__name__})", flush=True)
        return []
    items = []
    for it in raw:
        pub = norm_date(it.get("pub_raw") or "", today)
        if pub is None:
            # 「10-13」这类无年份写法 → 补当年
            m = re.search(r"(\d{1,2})[月/-](\d{1,2})[日]?", it.get("pub_raw") or "")
            if m:
                try:
                    d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
                    if d <= today:
                        pub = d.strftime("%Y-%m-%d")
                except ValueError:
                    pub = None
        items.append({
            "title": it["title"], "url": it["url"], "real_url": it.get("real_url") or "",
            "snippet": it.get("snippet") or "", "publish_time": pub,
        })
    return items


# ── 内容页过滤（重抓核心：只留真实内容，抛弃非内容页）─────────────
# 非内容页域名/路径 —— 百科、电商、贴吧、问答、聚合收录、官网栏目页
NON_CONTENT_HOSTS = {
    # 百科 / 问答（纯知识条目，非品牌内容）
    "baike.baidu.com", "baike.com", "baike.sogou.com", "baike.so.com",
    "zhidao.baidu.com", "zhihu.com", "zhuanlan.zhihu.com", "wenku.baidu.com",
    # 电商（商品页 / 店铺页 / 评价页）
    "item.jd.com", "jd.com", "taobao.com", "tmall.com", "weidian.com",
    "union-click.jd.com", "pingjia.taobao.com", "youfanerbuy.com",
    "ziyimall.com", "sunbue.com",
    # 贴吧 / 论坛 / 什么值得买社区（非品牌权威内容）
    "tieba.baidu.com", "bbs", "post.smzdm.com", "wiki.smzdm.com",
    "m.smzdm.com", "www.smzdm.com",
    # 导流页 / 黑产页
    "wap.hbystf.cn",
    # 百度自身聚合/信息流页（无独立原文）—— 注意：不含 baijiahao/health.baidu
    "recommend_list.baidu.com", "image.baidu.com",
}
# 明确的内容页域名 —— 新闻 / 资讯 / 自媒体 / 医疗科普 / 百度内容平台
CONTENT_HOSTS = {
    # 新闻门户
    "news.163.com", "www.163.com", "m.163.com", "finance.sina.com.cn",
    "finance.sina.cn", "news.sina.cn", "news.bioon.com", "news.pedaily.cn",
    "sd.ifeng.com", "ifeng.com", "baby.ifeng.com", "sxrb.com", "dahenews.cn",
    "bozhou.cn", "ws.sdnews.com.cn", "sdnews.com.cn", "longlin.gov.cn",
    "m.tech.china.com", "china.com", "pcpop.com", "sina.com.cn",
    "sohu.com", "chihe.sohu.com", "news.sohu.com", "baby.sohu.com",
    # 医疗健康科普（大量品牌软文/评测的阵地）
    "39.net", "news.39.net", "zl.39.net", "m.39.net", "m-mip.39.net",
    "cm.39.net", "health.baidu.com",
    # 百度内容平台（百家号=软文主阵地，百度健康=医学科普）
    "baijiahao.baidu.com",
    # 自媒体 / 内容社区
    "mp.weixin.qq.com", "cnblogs.com", "meipian.cn", "cloud.tencent.com",
    "developer.aliyun.com", "toutiao.com", "www.toutiao.com",
    "ilife.cn", "xhpy.cn", "snow.com.cn",
    # 品牌官网内容页（含评测/资讯栏目，但排除纯栏目列表页）
    "mitoqcn.com", "granverus.com",
}


def is_content_page(real_url, title=""):
    """判断落地 URL 是否为「真实内容页」。
    返回 True 表示保留；False 表示是百科/电商/贴吧/问答/聚合页，抛弃。"""
    if not real_url:
        # real_url 为空：无真实落地链接，一律丢弃（用户要求「要有原文链接」）
        return False
    try:
        host = urllib.parse.urlparse(real_url).netloc.lower()
    except Exception:
        return False
    # 去掉端口
    host = host.split(":")[0]
    # 百度站内聚合页（/s? 搜索页、精选笔记、最新相关信息）—— 无独立原文
    if host in ("www.baidu.com", "m.baidu.com"):
        path = urllib.parse.urlparse(real_url).path
        if path in ("/s", "/sf", "") or "vsearch" in real_url or "tn=news" in real_url:
            return False
    # 命中非内容域名 → 丢弃
    for h in NON_CONTENT_HOSTS:
        if host == h or host.endswith("." + h):
            return False
    # 命中内容域名 → 保留
    for h in CONTENT_HOSTS:
        if host == h or host.endswith("." + h):
            # 官网栏目页（无具体文章 ID 的列表/栏目页）要排除
            if h in ("mitoqcn.com", "granverus.com", "ilife.cn"):
                path = urllib.parse.urlparse(real_url).path
                # 具体文章页特征：路径含数字 ID 或 .html 结尾；纯栏目（斜杠结尾）丢弃
                has_article_id = bool(re.search(r"(\d{3,}|\.html?$|/p/|/a/)", path))
                if not has_article_id:
                    return False
            return True
    # 未知域名：看标题是否像内容（含评测/测评/怎么样/解析/实测/盘点等词）
    content_hint = re.search(
        r"(测评|评测|怎么样|好不好|解析|实测|盘点|避坑|智商税|"
        r"原理|成分|功效|科普|真相|揭秘|深度|体验|对比|选购)", title or "")
    return bool(content_hint)


def build_keywords(brand_cfg):
    """品牌名 + 别名 + 产品词 + 品牌配置的 baidu 关键词组。"""
    kws = [brand_cfg["name"]]
    kws += list(brand_cfg.get("aliases") or [])
    kws += list(brand_cfg.get("keywords") or [])
    for g in (brand_cfg.get("keyword_groups") or {}).get("baidu", []) or []:
        kws.append(g)
    for g in (brand_cfg.get("keyword_groups") or {}).values():
        kws += list(g or [])
    seen, out = set(), []
    for k in kws:
        k = (k or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=6, help="每个关键词翻多少页")
    ap.add_argument("--brand", default="", help="只跑指定品牌")
    ap.add_argument("--profile-dir", default=os.path.join(STATE_DIR, "baidu"),
                    help="Chromium 持久化 profile 目录")
    ap.add_argument("--headful", action="store_true", help="显示浏览器窗口（默认无头）")
    ap.add_argument("--wait-min", type=float, default=6.0)
    ap.add_argument("--wait-max", type=float, default=14.0)
    ap.add_argument("--max-kw", type=int, default=0, help="每个品牌最多几个关键词，0=不限")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    os.makedirs(RAW_DIR, exist_ok=True)
    brands = json.load(open(os.path.join(ROOT, "config", "brands.json"),
                           encoding="utf-8"))["brands"]
    if args.brand:
        brands = [b for b in brands if b["name"] == args.brand]

    con = sqlite3.connect(os.path.join(ROOT, "db", "cosmobounty.db"))
    today = datetime.date.today()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    pw = sync_playwright().start()
    ctx = None
    launch_args = ["--disable-blink-features=AutomationControlled",
                   "--no-sandbox",
                   "--disable-features=IsolateOrigins,site-per-process"]
    if os.path.isdir(args.profile_dir):
        ctx = pw.chromium.launch_persistent_context(
            args.profile_dir, headless=not args.headful, args=launch_args,
            locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    else:
        ctx = pw.chromium.launch(headless=not args.headful, args=launch_args).new_context(
            locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(30000)

    grand_new = 0
    grand_seen = 0
    for b in brands:
        kws = build_keywords(b)
        if args.max_kw:
            kws = kws[:args.max_kw]
        seen_url = set()
        all_items, blocked, pages_ok = [], 0, 0
        for kw in kws:
            for pno in range(args.pages):
                url = (f"https://www.baidu.com/s?wd={urllib.parse.quote(kw)}"
                       f"&rn=20&pn={pno * 10}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=35000)
                except Exception as e:
                    print(f"  ! [{kw} p{pno+1}] {type(e).__name__}: {e}", flush=True)
                    time.sleep(random.uniform(4, 9))
                    continue
                # 等结果区渲染
                try:
                    page.wait_for_selector("#content_left, .result, .c-container",
                                           timeout=12000)
                except Exception:
                    pass
                html = page.content()
                if "百度安全验证" in html or "wappass.baidu.com" in html:
                    blocked += 1
                    print(f"  × [{kw} p{pno+1}] 命中安全验证 → 冷却 90s", flush=True)
                    time.sleep(90)
                    break
                items = extract_dom(page, today) or parse_serp(html, today)
                if not items:
                    print(f"  · [{kw} p{pno+1}] 0 条（页已到底或结构变化）", flush=True)
                    break
                pages_ok += 1
                new = 0
                dropped = 0
                for it in items:
                    key = it.get("real_url") or it["url"]
                    if key in seen_url:
                        continue
                    seen_url.add(key)
                    # 内容页过滤：百科/电商/贴吧/问答/聚合页直接丢，不入库
                    if not is_content_page(it.get("real_url"), it.get("title")):
                        dropped += 1
                        continue
                    it["keyword"] = kw
                    it["page"] = pno + 1
                    it["brand"] = b["name"]
                    all_items.append(it)
                    new += 1
                print(f"  · [{kw} p{pno+1}] {len(items)} 条，内容页新增 {new}，过滤非内容页 {dropped}", flush=True)
                if new == 0:
                    break
                time.sleep(random.uniform(args.wait_min, args.wait_max))
            time.sleep(random.uniform(8, 18))

        # 落盘
        raw_file = os.path.join(RAW_DIR, f"{b['name']}.json")
        old = []
        if os.path.exists(raw_file):
            try:
                old = json.load(open(raw_file, encoding="utf-8"))
            except Exception:
                old = []
        known = {(x.get("real_url") or x.get("url")) for x in old}
        merged = old + [x for x in all_items
                        if (x.get("real_url") or x.get("url")) not in known]
        json.dump(merged, open(raw_file, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

        # 入库
        cur_new = 0
        for it in all_items:
            link = it.get("real_url") or it["url"]
            h = hashlib.md5(link.encode()).hexdigest()
            cur = con.execute(
                """INSERT OR IGNORE INTO content
                   (brand, platform, content_type, title, body, publish_time,
                    collect_time, url, raw_path, text_hash, tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (b["name"], "baidu", "article", it["title"], it.get("snippet"),
                 it.get("publish_time"), now, link, raw_file, h,
                 (it.get("keyword") or "") + ("|" + it["url"] if it.get("real_url") else "")))
            if cur.rowcount:
                cur_new += 1
        grand_new += cur_new
        grand_seen += len(all_items)
        withtime = sum(1 for x in all_items if x.get("publish_time"))
        con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) "
                    "VALUES (?,?,?,?,?,?)",
                    ("baidu", b["name"], f"{len(kws)}词x{args.pages}页", now, "ok",
                     f"命中 {len(all_items)} / 新增 {cur_new} / 有时间 {withtime} / "
                     f"有效页 {pages_ok} / 安全验证 {blocked}"))
        con.commit()
        print(f"[baidu] {b['name']}: 命中 {len(all_items)}，新增入库 {cur_new}，带时间 {withtime}\n",
              flush=True)

    try:
        ctx.close()
    except Exception:
        pass
    pw.stop()
    print(f"=== 全部完成：命中 {grand_seen}，新增入库 {grand_new} ===", flush=True)
    con.close()


if __name__ == "__main__":
    main()
