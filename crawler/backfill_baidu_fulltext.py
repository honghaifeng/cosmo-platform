#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_baidu_fulltext.py — 把百度/百家号等站外文章从「摘要」补成「正文全文」。

背景：
  早期百度数据只落了搜索结果页的摘要（150~300 字，结尾带 ...），
  底表正文不完整，做观点/关键词分析时会失真。
  实测 baijiahao 页面正文就在 HTML 里（<span class="bjh-p">…），
  不需要浏览器渲染，直接 curl 就能拿全文。

规则：
  - 只处理 platform='baidu' 且 length(body) < 600 的已校验条目
  - 抓到正文 >= 300 字且比原文更长才更新（宁缺毋滥，不写垃圾）
  - 原始 HTML 落盘 data/raw/articles/<id>.html，便于复核
  - 幂等：body 已达标（>=600 或 == 新抓结果）自动跳过
  - 限速：默认 1.2s/条，不并发硬冲

用法：
  python crawler/backfill_baidu_fulltext.py --report
  python crawler/backfill_baidu_fulltext.py --limit 5
  python crawler/backfill_baidu_fulltext.py
"""
import argparse
import html as ihtml
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "db", "cosmobounty.db")
RAW = os.path.join(PROJ, "data", "raw", "articles")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 正文尾部噪音：版权声明、责任编辑、举报反馈
TAIL_RX = re.compile(
    r"(声明[:：]|本文来源|本网转发|责任编辑|责编[:：]|举报/反馈|"
    r"原标题[:：]|点击进入专题|海量资讯|特别声明|免责声明)")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def curl(url, timeout=20):
    """抓页面。带 Referer 降低被判定为爬虫的概率。"""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--compressed", "-A", UA,
             "--max-time", str(timeout),
             "-H", "Accept-Language: zh-CN,zh;q=0.9",
             "-H", "Referer: https://www.baidu.com/",
             url],
            capture_output=True)
        return r.stdout or b""
    except Exception:
        return b""


BLOCK_MARK = "百度安全验证"


def blocked(s):
    return BLOCK_MARK in s[:4000] or len(s) < 3000


def decode(raw):
    if not raw:
        return ""
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def _strip(frag):
    t = re.sub(r"<[^>]+>", "", frag)
    return ihtml.unescape(t).replace("\u200b", "").strip()


def extract(s):
    """从页面 HTML 抽正文。返回 (text, how)。"""
    # ① 百家号：<span class="bjh-p"> 段落
    ps = re.findall(r'<span class="bjh-p">(.*?)</span>(?=</div>|<span class="bjh-p">|$)',
                    s, re.S)
    if ps:
        txt = "\n".join(_strip(p) for p in ps if _strip(p))
        if len(txt) >= 300:
            return txt, "bjh-p"
    # ② 通用：从 data-testid="article" 起点开一个固定窗口取 <p>
    #    （不同模板 class 名会变，但正文容器这个标记是稳定的）
    i = s.find('data-testid="article"')
    if i > 0:
        seg = s[i:i + 60000]
        ps = re.findall(r"<p[^>]*>(.*?)</p>", seg, re.S)
        txt = "\n".join(_strip(p) for p in ps if len(_strip(p)) > 10)
        if len(txt) >= 300:
            return txt, "article-window"
    # ③ 兜底：正文里最长的连续中文段落群
    cand = re.findall(r"<p[^>]*>(.*?)</p>", s, re.S)
    body = []
    for c in cand:
        t = _strip(c)
        if len(t) >= 20:
            body.append(t)
    txt = "\n".join(body)
    return (txt, "p-tags") if len(txt) >= 300 else ("", "")


def cut_tail(txt):
    lines = [x.strip() for x in txt.split("\n") if x.strip()]
    out = []
    for ln in lines:
        if TAIL_RX.search(ln):
            break
        out.append(ln)
    return "\n".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min", type=int, default=600, help="低于该长度的 body 视为待补")
    ap.add_argument("--sleep", type=float, default=3.0)
    a = ap.parse_args()

    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, brand, title, body, url FROM content "
        "WHERE verified=1 AND platform='baidu' AND LENGTH(IFNULL(body,'')) < ? "
        "ORDER BY LENGTH(IFNULL(body,'')) ASC", (a.min,)).fetchall()

    print(f"百度已校验条目中正文 <{a.min} 字：{len(rows)} 条待补")
    if a.report:
        con.close()
        return
    if a.limit:
        rows = rows[:a.limit]

    ok = skip = fail = blocked_n = 0
    for r in rows:
        raw = curl(r["url"])
        s = decode(raw)
        # 撞到「百度安全验证」：不硬冲，冷却后重试一次，仍拦就整批停下
        if blocked(s):
            blocked_n += 1
            print(f"[风控] id={r['id']} 触发验证页，冷却 90s 后重试", flush=True)
            time.sleep(90)
            raw = curl(r["url"])
            s = decode(raw)
            if blocked(s):
                print("[风控] 仍被拦，本轮停止（已抓到的都已落库，下次续跑）", flush=True)
                break
        txt, how = extract(s)
        txt = cut_tail(txt)
        old = (r["body"] or "").strip()
        if len(txt) < 300 or len(txt) <= len(old) * 1.2:
            skip += 1
            print(f"[skip] id={r['id']} 抓到 {len(txt)} 字（原 {len(old)}），不足不覆盖", flush=True)
            time.sleep(a.sleep)
            continue
        open(os.path.join(RAW, f"{r['id']}.html"), "wb").write(raw)
        con.execute("UPDATE content SET body=?, raw_path=? WHERE id=?",
                    (txt, f"articles/{r['id']}.html", r["id"]))
        con.commit()
        ok += 1
        print(f"[ok] id={r['id']} {len(old)}→{len(txt)} 字 [{how}] {r['title'][:24]}", flush=True)
        time.sleep(a.sleep)

    con.close()
    print(f"\n完成：补全 {ok}，跳过 {skip}，失败 {fail}，风控 {blocked_n}")


if __name__ == "__main__":
    main()
