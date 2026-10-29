#!/usr/bin/env python3
"""批量补抓正文：对 level 为 serp/missing/partial 的条目抓取原文全文
- 处理压缩、GBK 编码、反爬 UA
- 提取正文启发式：标题命中附近文本 / 最长文本块
- 原始 HTML 落盘 data/raw/articles/
"""
import sqlite3, subprocess, re, os, json, sys, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import html as ihtml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
RAW = os.path.join(BASE, "data", "raw", "articles")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

def level_of(body, platform):
    b = (body or "").strip()
    if b.startswith("（SERP 收录级"):
        return "serp"
    if len(b) >= 120:
        return "full"
    if len(b) >= 30:
        return "partial"
    return "missing" if platform not in ("xhs", "douyin") else "full"

def curl(url, timeout=15):
    r = subprocess.run(["curl", "-sL", "--compressed", "-A", UA,
                        "--max-time", str(timeout),
                        "-H", "Accept-Language: zh-CN,zh;q=0.9",
                        url], capture_output=True)
    return r.stdout

def decode(raw):
    if not raw:
        return ""
    for enc in ("utf-8", "gb18030"):
        try:
            t = raw.decode(enc)
            if t.count("\ufffd") < 20:
                return t
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")

def extract(html_text, title):
    if not html_text:
        return None
    t = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text, flags=re.S | re.I)
    # 常见正文容器优先
    block = None
    m = re.search(r'<article[^>]*>(.*?)</article>', t, re.S | re.I)
    if m and len(m.group(1)) > 600:
        block = m.group(1)
    if not block:
        cands = re.findall(r'<div[^>]+(?:class|id)="[^"]*(?:article|content|detail|main|text|post|body)[^"]*"[^>]*>(.*?)</div>',
                           t, re.S | re.I)
        block = max(cands, key=len) if cands else t
    txt = re.sub(r"<br\s*/?>|</p>", "\n", block, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = ihtml.unescape(txt)
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt).strip()
    txt = re.sub(r"\s{2,}", " ", txt)
    if len(txt) < 150:
        return None
    key = (title or "")[:14]
    if key and key in txt:
        i = txt.find(key)
        txt = txt[max(0, i - 60):]
    # 去掉常见导航尾巴
    for tail in ("相关推荐", "热门推荐", "免责声明", "版权声明", "责任编辑"):
        j = txt.rfind(tail)
        if j > len(txt) * 0.6:
            txt = txt[:j]
    return txt[:3000].strip()

def work(row):
    cid, platform, title, url, body = row
    real = url
    if "baidu.com/link" in url:
        out = subprocess.run(["curl", "-sL", "-A", UA, url, "--max-time", "12",
                              "-o", "/dev/null", "-w", "%{url_effective}"],
                             capture_output=True, text=True).stdout.strip()
        if out and "baidu.com/link" not in out:
            real = out
    raw = curl(real)
    txt = extract(decode(raw), title)
    if not txt:
        raw2 = curl(real, timeout=20)
        txt = extract(decode(raw2), title)
    return cid, platform, title, real, txt, len(raw or b"")

def _bump_skip(con, cid, reason, now):
    """失败计数；连续 2 次失败即拉黑，避免每天对着 404 页面重试。"""
    row = con.execute("SELECT attempts FROM fetch_skip WHERE content_id=?", (cid,)).fetchone()
    n = (row[0] if row else 0) + 1
    if n >= 2:
        con.execute("INSERT OR REPLACE INTO fetch_skip (content_id,attempts,reason,updated_at)"
                    " VALUES (?,?,?,?)", (cid, n, reason, now))
    else:
        con.execute("INSERT OR REPLACE INTO fetch_skip (content_id,attempts,reason,updated_at)"
                    " VALUES (?,?,?,?)", (cid, n, reason, now))


def main(limit=None):
    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    # 连续两次抓不到（404 / 反爬跳转 / 正文过短）就记进放弃台账，不再每天重试
    con.execute("""CREATE TABLE IF NOT EXISTS fetch_skip (
        content_id INTEGER PRIMARY KEY,
        attempts   INTEGER NOT NULL DEFAULT 0,
        reason     TEXT,
        updated_at TEXT
    )""")
    con.commit()
    skipped = {r[0] for r in con.execute("SELECT content_id FROM fetch_skip WHERE attempts>=2")}
    rows = []
    for r in con.execute("SELECT id, platform, title, url, body FROM content"):
        # 小红书/抖音正文即平台原文（短是正常的），且详情页要登录态+JS 渲染，
        # 用 curl 抓必然失败——不走这条链路，避免每天刷出上百条假失败。
        if r[1] in ("xhs", "douyin", "shipinhao"):
            continue
        if r[0] in skipped:
            continue
        if level_of(r[4], r[1]) in ("serp", "missing", "partial"):
            rows.append(r)
    if limit:
        rows = rows[:limit]
    print(f"待补抓 {len(rows)} 条", flush=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, r): r for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                cid, platform, title, real, txt, rawlen = f.result()
            except Exception as e:
                fail += 1
                _bump_skip(con, r[0], f"异常:{type(e).__name__}", now)
                continue
            if txt and len(txt) > 200:
                con.execute("UPDATE content SET body=?, url=? WHERE id=?", (txt, real, cid))
                with open(os.path.join(RAW, f"{cid}.txt"), "w") as fh:
                    fh.write(f"url: {real}\nfetched: {now}\ntitle: {title}\n\n{txt}")
                ok += 1
                con.execute("DELETE FROM fetch_skip WHERE content_id=?", (cid,))
            else:
                fail += 1
                reason = "正文过短/反爬跳转" if txt else "无响应或已删除"
                _bump_skip(con, cid, reason, now)
            if i % 25 == 0:
                con.commit()
                print(f"  进度 {i}/{len(rows)} 成功 {ok} 失败 {fail}", flush=True)
    con.commit()
    n_skip = con.execute("SELECT COUNT(*) FROM fetch_skip").fetchone()[0]
    print(f"完成：成功 {ok}，失败 {fail}")
    if n_skip:
        print(f"累计放弃 {n_skip} 条（源站 404 / 反爬，连续 2 次失败即不再重试）")
    print(*con.execute("""SELECT platform,
        SUM(CASE WHEN LENGTH(COALESCE(body,''))>200 AND body NOT LIKE '（SERP%' THEN 1 ELSE 0 END),
        COUNT(*) FROM content GROUP BY platform"""), sep="\n")
    con.close()

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
