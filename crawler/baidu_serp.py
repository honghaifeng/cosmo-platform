#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度/头条 SERP 采集脚本（需在本地带真实浏览器 cookie 运行）。
用法：
  export BAIDU_COOKIE="在浏览器登录百度后复制的 Cookie"
  python3 crawler/baidu_serp.py            # 或 python3 crawler/baidu_serp.py toutiao
说明：
  - 沙箱/无 cookie 直抓百度会命中「百度安全验证」(2026-10-21 实测)，故需浏览器同源 cookie。
  - 仅抓搜索结果前 20 条的标题/链接/摘要，限频 8-15 秒/请求，仅公开 SERP。
"""
import hashlib, json, os, re, sqlite3, sys, time, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def fetch(url, cookie=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie or "",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")

def parse_baidu(html):
    items = []
    # 结果块: <div class="result c-container ..."><h3 ...><a href="...">title</a>
    for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if title:
            items.append({"title": title, "url": m.group(1)})
    return items

def parse_toutiao(html):
    items = []
    for m in re.finditer(r'<a[^>]*href="(https?://www\.toutiao\.com/[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if len(title) > 8:
            items.append({"title": title, "url": m.group(1)})
    return items

def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "baidu"
    cookie = os.environ.get("BAIDU_COOKIE") or os.environ.get("TOUTIAO_COOKIE", "")
    brands = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))["brands"]
    con = sqlite3.connect(os.path.join(ROOT, "db", "cosmobounty.db"))
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for b in brands:
        kw = b["keywords"][0]
        if which == "baidu":
            url = f"https://www.baidu.com/s?wd={urllib.request.quote(kw)}&rn=20"
            items = parse_baidu(fetch(url, cookie))
        else:
            url = f"https://so.toutiao.com/search?keyword={urllib.request.quote(kw)}"
            items = parse_toutiao(fetch(url, cookie))
        raw_dir = os.path.join(ROOT, "data", "raw", which)
        os.makedirs(raw_dir, exist_ok=True)
        json.dump(items, open(os.path.join(raw_dir, f"{b['name']}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        for it in items:
            h = hashlib.md5(it["url"].encode()).hexdigest()
            con.execute("""INSERT OR IGNORE INTO content
                (brand, platform, content_type, title, collect_time, url, raw_path, text_hash)
                VALUES (?,?,?,?,?,?,?,?)""",
                (b["name"], which, "article", it["title"], now, it["url"],
                 os.path.join(raw_dir, f"{b['name']}.json"), h))
        con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) VALUES (?,?,?,?,?,?)",
                    (which, b["name"], kw, now, "ok" if items else "empty", f"{len(items)} results"))
        print(f"[{which}] {b['name']}: {len(items)} results")
        time.sleep(8)  # 限频
    con.commit()

if __name__ == "__main__":
    main()
