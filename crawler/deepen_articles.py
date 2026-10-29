#!/usr/bin/env python3
"""正文加深脚本：对库内 body 为空/过短的内容补抓原文正文
1) 头条：toutiao.com 站内文章直接抓正文
2) 百度：解析 baidu.com/link?url= 跳转拿到真实 URL，更新 url 字段并尝试抓正文
3) 站外 PR 域名：尽力抓取，失败则保留标题+来源域名（SERP 收录层级）
"""
import sqlite3, subprocess, re, html as ihtml, os, sys, json, datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
RAW = os.path.join(BASE, "data", "raw", "articles")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def fetch(url, timeout=10):
    try:
        r = subprocess.run(["curl", "-sL", "-A", UA, url, "--max-time", str(timeout),
                            "-o", "/dev/null", "-w", "%{http_code} %{url_effective}"],
                           capture_output=True, text=True)
        parts = r.stdout.strip().split(" ", 1)
        return parts[0], parts[1] if len(parts) > 1 else url
    except Exception:
        return "000", url

def fetch_body(url, timeout=10):
    try:
        r = subprocess.run(["curl", "-sL", "-A", UA, url, "--max-time", str(timeout)],
                           capture_output=True, text=True)
        raw = r.stdout
        if len(raw) < 500:
            return None
        # 去脚本/样式，提文本
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", raw)
        text = ihtml.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text if len(text) > 120 else None
    except Exception:
        return None

def main():
    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = {"resolved": 0, "fetched": 0, "failed": 0}

    rows = con.execute("""SELECT id, brand, platform, title, url, text_hash FROM content
                          WHERE LENGTH(COALESCE(body,'')) < 60 AND content_type != 'note'
                          AND platform IN ('toutiao','baidu')""").fetchall()
    print(f"待加深 {len(rows)} 条")
    for cid, brand, platform, title, url, h in rows:
        real_url = url
        # 百度跳转链解析
        if "baidu.com/link" in url:
            code, real_url = fetch(url, timeout=10)
            if code in ("200", "302") and "baidu.com/link" not in real_url:
                con.execute("UPDATE content SET url=? WHERE id=?", (real_url, cid))
                stats["resolved"] += 1
            else:
                real_url = url
        host = re.sub(r"^www\.", "", re.sub(r"https?://([^/]+).*", r"\1", real_url))
        # 头条站内文章 & 部分静态新闻站可抓
        body = None
        if re.search(r"toutiao\.com/(article|item|wap_article)|aigc\.", real_url) or platform == "baidu":
            body = fetch_body(real_url)
        if body:
            # 只留与标题相关的片段附近文本，避免整页噪音
            idx = body.find(title[:16]) if title[:16] in body else 0
            excerpt = body[max(0, idx - 100): idx + 900]
            con.execute("UPDATE content SET body=?, text_hash=text_hash WHERE id=?",
                        (excerpt, cid))
            stats["fetched"] += 1
            with open(os.path.join(RAW, f"{h.replace(':', '_')}.txt"), "w") as f:
                f.write(f"url: {real_url}\nfetched_at: {now}\n\n{body}")
        else:
            con.execute("UPDATE content SET body=? WHERE id=?",
                        (f"（SERP 收录级：仅标题+来源 {host}，正文抓取受限）", cid))
            stats["failed"] += 1

    con.commit()
    print(stats)
    print(*con.execute("""SELECT platform,
        SUM(CASE WHEN LENGTH(COALESCE(body,''))>60 AND body NOT LIKE '（SERP%' THEN 1 ELSE 0 END),
        COUNT(*) FROM content GROUP BY platform"""), sep="\n")
    con.close()

if __name__ == "__main__":
    main()
