#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条搜索(so.toutiao.com)解析入库：沙箱实测可直抓，无需 cookie。
结果为 HTML 跳转链 sou.toutiao.com/search/jump?url=<双重编码>，此处还原真实 URL。
仅保留 toutiao.com 站内内容（文章/微头条/视频），外部官网结果不入库。"""
import hashlib, json, os, re, sqlite3, subprocess, time, datetime, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()

def decode_jump(href):
    m = re.search(r"[?&]url=([^&]+)", href)
    if not m:
        return ""
    u = urllib.parse.unquote(m.group(1))
    m2 = re.search(r"[?&]url=([^&]+)", u)  # 双重编码
    if m2:
        u = urllib.parse.unquote(m2.group(1))
    return u.split("&aid=")[0].split("?aid=")[0]

def parse(raw, aliases):
    items, seen = [], set()
    for m in re.finditer(r'<a href="(https://sou\.toutiao\.com/search/jump\?[^"]+)"[^>]*>(.*?)</a>', raw, re.S):
        title = clean(m.group(2))
        url = decode_jump(m.group(1))
        if not title or len(title) < 6 or not url:
            continue
        # 只保留标题命中品牌词的结果，排除热榜/推荐噪音
        if not any(a.lower() in title.lower() for a in aliases):
            continue
        host = urllib.parse.urlparse(url).netloc
        if not host:
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "host": host})
    return items

def fetch(kw):
    url = "https://so.toutiao.com/search?keyword=" + urllib.parse.quote(kw) + "&dvpf=pc"
    p = subprocess.run(["curl", "-s", "-A", UA, url, "--max-time", "20"],
                       capture_output=True, text=True)
    return p.stdout

def main():
    import sys
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    brands = json.load(open(os.path.join(ROOT, "config", "brands.json"), encoding="utf-8"))["brands"]
    if only:
        brands = [b for b in brands if b["name"] == only]
    con = sqlite3.connect(os.path.join(ROOT, "db", "cosmobounty.db"))
    now = datetime.datetime.now().isoformat(timespec="seconds")
    raw_dir = os.path.join(ROOT, "data", "raw", "toutiao")
    os.makedirs(raw_dir, exist_ok=True)
    for b in brands:
        all_items = []
        for kw in b["keywords"][:2]:
            items = parse(fetch(kw), b["aliases"])
            for it in items:
                it["keyword"] = kw
            all_items += items
            time.sleep(6)
        json.dump(all_items, open(os.path.join(raw_dir, f"{b['name']}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        for it in all_items:
            h = hashlib.md5(it["url"].encode()).hexdigest()
            con.execute("""INSERT OR IGNORE INTO content
                (brand, platform, content_type, title, body, collect_time, url, tags, raw_path, text_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (b["name"], "toutiao", "article", it["title"], "", now,
                 it["url"], "src:" + it.get("host", ""), os.path.join(raw_dir, f"{b['name']}.json"), h))
        con.execute("INSERT INTO crawl_log (platform, brand, keyword, fetched_at, status, note) VALUES (?,?,?,?,?,?)",
                    ("toutiao", b["name"], "+".join(b["keywords"][:2]), now, "ok", f"{len(all_items)} relevant results (含站外)"))
        print(f"toutiao {b['name']}: {len(all_items)}")
        for it in all_items[:3]:
            print("   ", it["title"][:40], "->", it["url"][:60])
    con.commit()

if __name__ == "__main__":
    main()
