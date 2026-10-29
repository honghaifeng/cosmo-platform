#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补最后几个脱敏昵称：按「笔记 → 搜索关键词 → 结果里定位该笔记 → 打开取真昵称」。

比逐账号试探高效得多：一条笔记只需 1 次搜索 + 1 次打开，
而且拿到的还是该笔记的完整详情（正文/图片/标签），可以顺带补正文缺失。

节奏：每条 12~26 秒随机间隔，遇安全验证立刻停（冷却 40 分钟）。
用法：
  tools/MediaCrawler/.venv/bin/python crawler/fix_last_nicknames.py
"""
import asyncio
import datetime
import json
import os
import random
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
PROFILE = "/tmp/chrome_xhs_profile"
RAW = os.path.join(ROOT, "data", "xhs_archive", "raw")

DOM_JS = r"""() => {
  const q = s => document.querySelector(s);
  const qa = s => [...document.querySelectorAll(s)];
  const T = e => (e && e.innerText ? e.innerText.trim() : '');
  const pick = (sels) => { for (const s of sels) { const e = q(s); if (e && T(e)) return T(e); } return ''; };
  const title = pick(['#detail-title', '.note-content .title', 'h1.title']);
  const desc  = pick(['#detail-desc .note-text', '#detail-desc', '.note-text', '.desc']);
  const author= pick(['#author-container .author-name .name', '#author-container .author-name',
                      '.author-container .author-name .name', '.author-wrapper .username',
                      '.author-container .name', '.author-name']);
  const time  = pick(['.bottom-container .date span', '.bottom-container .date', '.date span', '.date']);
  const loc   = pick(['.bottom-container .location', '.location']);
  const likes = pick(['.engage-bar .like-wrapper .count', '.interact-container .like-wrapper .count', '.like-wrapper .count']);
  const cols  = pick(['.engage-bar .collect-wrapper .count', '.interact-container .collect-wrapper .count', '.collect-wrapper .count']);
  const cmts  = pick(['.engage-bar .chat-wrapper .count', '.interact-container .chat-wrapper .count', '.chat-wrapper .count']);
  const tags  = qa('#detail-desc a, a.topic-tag, .topic-tag, .tag').map(a => T(a)).filter(Boolean).slice(0, 24);
  const imgs  = qa('.note-slider-img, .media-container img, .img-container img, #noteContainer img, .carousel img')
                 .map(i => i.getAttribute('src') || i.getAttribute('data-src') || '').filter(u => u.startsWith('http'));
  const vids  = qa('video').map(v => v.getAttribute('src') || (v.querySelector('source')||{}).src || '')
                 .filter(u => u && u.startsWith('http'));
  const ava   = (q('#author-container img, .author-container img, .avatar img')||{}).src || '';
  const href  = location.href;
  const nid   = (href.match(/explore\/([0-9a-f]{16,32})/)||[])[1] || '';
  const cmtTexts = qa('.comment-item .content, .comment-item span.content, .comments-container .content')
                    .map(e => T(e)).filter(Boolean).slice(0, 30);
  return {title: title, desc: desc, author: author, time: time, loc: loc, likes: likes,
          collects: cols, comments: cmts, tags: [...new Set(tags)], imgs: [...new Set(imgs)],
          videos: [...new Set(vids)], avatar: ava, nid: nid, url: href, cmtTexts: cmtTexts};
}"""


def num(s):
    s = (s or "").strip().replace("+", "")
    if s.endswith("万"):
        try:
            return int(float(s[:-1]) * 10000)
        except ValueError:
            return 0
    try:
        return int(s)
    except Exception:
        return 0


async def search_note(page, keyword, note_id):
    """搜索关键词，在结果里定位目标笔记，返回带新鲜 token 的链接。"""
    url = ("https://www.xiaohongshu.com/search_result?keyword="
           + __import__("urllib.parse", fromlist=["quote"]).quote(keyword)
           + "&source=web_explore_feed")
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(12):
                await page.wait_for_timeout(1000)
                hrefs = await page.evaluate("""() => [...document.querySelectorAll('section.note-item a.cover')]
                    .map(a => a.getAttribute('href')).filter(Boolean)""")
                for h in hrefs:
                    if note_id in h:
                        h = h.replace("/search_result/", "/explore/", 1)
                        if not h.startswith("http"):
                            h = "https://www.xiaohongshu.com" + h
                        return h
                if hrefs:
                    break
        except Exception:
            pass
        await asyncio.sleep(6 + attempt * 4)
    return None


async def main():
    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    tasks = list(con.execute("""SELECT note_id, author_id, author, source_keyword, title
        FROM xhs_note WHERE brand_mention=1 AND (author IS NULL OR author LIKE '%*%')"""))
    con.close()
    print(f"待补 {len(tasks)} 条笔记\n")
    from playwright.async_api import async_playwright
    fixed = 0
    async with async_playwright() as p:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="zh-CN",
                  viewport={"width": 1440, "height": 900},
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                        "--disable-gpu", "--disable-dev-shm-usage"])
        try:
            ctx = await p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = await p.chromium.launch_persistent_context(**kw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        for nid, aid, oldnick, keyword, title in tasks:
            href = await search_note(page, keyword or "", nid)
            if not href:
                print(f"  · {nid[:10]} 搜索未定位到（词：{keyword}）", flush=True)
                await asyncio.sleep(random.uniform(20, 40))
                continue
            npg = await ctx.new_page()
            try:
                await npg.goto(href, wait_until="domcontentloaded", timeout=45000)
                await npg.wait_for_timeout(2800 + random.randint(0, 1200))
                html = await npg.content()
                if "安全限制" in html or "访问频繁" in html:
                    print("  ⛔ 命中安全限制，停止（冷却 40 分钟）", flush=True)
                    break
                rec = await npg.evaluate(DOM_JS)
                if not rec or not rec.get("author"):
                    print(f"  · {nid[:10]} 未取到昵称", flush=True)
                    await npg.close()
                    await asyncio.sleep(8)
                    continue
                newnick = rec["author"].strip()
                # 时间补全
                if nid and len(nid) >= 8:
                    try:
                        ts = datetime.datetime.fromtimestamp(int(nid[:8], 16))
                        t = (rec.get("time") or "").strip()
                        if not t:
                            rec["time"] = ts.strftime("%Y-%m-%d %H:%M")
                        elif re.match(r"^\d{2}-\d{2}$", t):
                            rec["time"] = ts.strftime("%Y-%m-%d") + " " + t
                    except Exception:
                        pass
                rec["likes"] = num(rec.get("likes"))
                rec["collects"] = num(rec.get("collects"))
                rec["comments"] = num(rec.get("comments"))
                with open(os.path.join(RAW, f"{nid}.json"), "w", encoding="utf-8") as f:
                    json.dump(rec, f, ensure_ascii=False, indent=1)
                con = sqlite3.connect(DB)
                con.execute("""UPDATE xhs_note SET author=?, body=COALESCE(NULLIF(body,''),?),
                        images_json=?, tags_json=?, likes=?, collects=?, comments=?,
                        publish_time=COALESCE(publish_time,?) WHERE note_id=?""",
                             (newnick, rec["desc"],
                              json.dumps(rec["imgs"], ensure_ascii=False),
                              json.dumps(rec["tags"], ensure_ascii=False),
                              rec["likes"], rec["collects"], rec["comments"], rec["time"], nid))
                con.execute("""UPDATE content SET author=?, body=COALESCE(NULLIF(body,''),?),
                        media_json=COALESCE(media_json,?), tags=COALESCE(NULLIF(tags,''),?),
                        publish_time=COALESCE(publish_time,?) WHERE text_hash=?""",
                             (newnick, rec["desc"],
                              json.dumps({"images": rec["imgs"]}, ensure_ascii=False) if rec["imgs"] else None,
                              ",".join(rec["tags"]), rec["time"], f"xhs:{nid}"))
                con.execute("UPDATE creator SET nickname=? WHERE creator_id=?", (newnick, aid))
                con.commit()
                con.close()
                fixed += 1
                print(f"  ✅ {nid[:10]} 「{oldnick}」→「{newnick}」 "
                      f"正文{len(rec['desc'])} 图{len(rec['imgs'])}", flush=True)
                await npg.close()
            except Exception as e:
                print(f"  ! {type(e).__name__}: {str(e)[:80]}", flush=True)
                try:
                    await npg.close()
                except Exception:
                    pass
            d = random.uniform(12, 26)
            if random.random() < 0.25:
                d += random.uniform(18, 45)
            await asyncio.sleep(d)
        await ctx.close()
    con = sqlite3.connect(DB)
    left = con.execute("""SELECT COUNT(DISTINCT author_id) FROM xhs_note
        WHERE brand_mention=1 AND (author IS NULL OR author LIKE '%*%')""").fetchone()[0]
    con.close()
    print(f"\n=== 补齐 {fixed} 条，剩余脱敏账号 {left} 个 ===")


if __name__ == "__main__":
    asyncio.run(main())
