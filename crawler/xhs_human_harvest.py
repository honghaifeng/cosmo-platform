#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书拟人采集器（最终版）：用真实浏览路径逐条取详情。

为什么这样设计（都是被封出来的经验）：
  · 直接 goto 笔记 URL → 「安全限制」（反深链）
  · 直接调 /api/sns/web/v1/feed → 500 jarvis-gateway（网关要新鲜 xsec_token）
  · 批量并发请求 → 详情 JSON 被截断 → 461 验证码
  ⇒ 唯一稳定路径：**首页 → 搜索框输入 → 回车 → 点卡片 → 读 DOM → 关弹窗 → 下一个**

节奏控制（用户明确要求）：
  · 每个关键词只取前 N 张卡片（默认 3）
  · 每条之间随机停 9~20 秒，25% 概率加 18~45 秒长停顿
  · 关键词之间停 30~70 秒
  · 一轮总量上限（默认 12 条），跑完就停，绝不贪
  · 遇到安全限制/验证码立刻停止本轮并冷却 40 分钟

产出：写回 xhs_note（正文/图片/标签/真实昵称/时间/互动/IP）+ 归档原文页 HTML
"""
import argparse
import asyncio
import datetime
import re
import urllib.parse
import json
import os
import random
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
ARCHIVE = os.path.join(ROOT, "data", "xhs_archive")
RAW = os.path.join(ARCHIVE, "raw")
PROFILE = os.environ.get("CB_PROFILE", "/tmp/chrome_xhs_profile")
STATE = os.path.join(ARCHIVE, "human_state.json")

BRANDS = ["COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"]

# 每轮关键词顺序：官方号/自有品牌优先，场景词最后
KEYWORDS = [
    "科诗博缇", "COSMOBOUNTY", "黑白分时胶囊", "Cellerator赛乐瑞", "赛乐瑞",
    "MitoQ", "Granver", "吉利肝", "LAIFE乐梵", "红宝瓶", "端粒塔", "NMN 25000",
    "熬夜后怎么恢复", "入睡难怎么调理", "皮肤松弛暗沉怎么调理",
]

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
  const likes = pick(['.engage-bar .like-wrapper .count', '.interact-container .like-wrapper .count',
                      '.like-wrapper .count', '.like-active .count']);
  const cols  = pick(['.engage-bar .collect-wrapper .count', '.interact-container .collect-wrapper .count',
                      '.collect-wrapper .count']);
  const cmts  = pick(['.engage-bar .chat-wrapper .count', '.interact-container .chat-wrapper .count',
                      '.chat-wrapper .count']);
  const tags  = qa('#detail-desc a, a.topic-tag, .topic-tag, .tag').map(a => T(a)).filter(Boolean).slice(0, 24);
  const imgs  = qa('.note-slider-img, .media-container img, .img-container img, #noteContainer img, .carousel img')
                 .map(i => i.getAttribute('src') || i.getAttribute('data-src') || '')
                 .filter(u => u.startsWith('http'));
  const vids  = qa('video').map(v => v.getAttribute('src') || (v.querySelector('source')||{}).src || '')
                 .filter(u => u && u.startsWith('http'));
  const ava   = (q('#author-container img, .author-container img, .avatar img')||{}).src || '';
  const href  = location.href;
  const nid   = (href.match(/explore\/([0-9a-f]{16,32})/)||[])[1]
              || (href.match(/([0-9a-f]{24})/)||[])[1] || '';
  const cmtTexts = qa('.comment-item .content, .comment-item span.content, .comments-container .content')
                    .map(e => T(e)).filter(Boolean).slice(0, 30);
  return {title: title, desc: desc, author: author, time: time, loc: loc,
          likes: likes, collects: cols, comments: cmts,
          tags: [...new Set(tags)], imgs: [...new Set(imgs)], videos: [...new Set(vids)],
          avatar: ava, nid: nid, url: href, cmtTexts: cmtTexts};
}"""


MEDIA_DIR = os.path.join(ROOT, "data", "xhs_archive", "media")


async def grab_media(ctx, rec, note_id, max_images=8):
    """趁 URL 还新鲜时立刻把图片/视频落盘。

    小红书 CDN 的图片 URL 会在约 24h 后失效（路径里的时间戳目录就是生成时间），
    所以「采集时顺便存下来」是唯一可靠的方式 —— 事后补下会 403。
    视频 URL 是逗号分隔的多段（同一视频的不同清晰度），整串请求会 403，需逐段试。
    """
    os.makedirs(MEDIA_DIR, exist_ok=True)
    got = 0
    # 图片
    for i, u in enumerate((rec.get("imgs") or [])[:max_images]):
        path = os.path.join(MEDIA_DIR, f"{note_id}_{i}.jpg")
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            continue
        try:
            resp = await ctx.request.get(u, headers={"Referer": "https://www.xiaohongshu.com/"})
            if not resp.ok:
                continue
            body = await resp.body()
            if len(body) < 1024 or len(body) > 40 * 1024 * 1024:
                continue
            with open(path, "wb") as f:
                f.write(body)
            got += 1
        except Exception:
            pass
    # 视频（多段逐个试）
    vids = [x.strip() for x in (rec.get("videos") or []) if x.strip().startswith("http")]
    if vids:
        path = os.path.join(MEDIA_DIR, f"{note_id}.mp4")
        if not (os.path.exists(path) and os.path.getsize(path) > 1024):
            for cand in vids:
                try:
                    resp = await ctx.request.get(cand, headers={"Referer": "https://www.xiaohongshu.com/"})
                    if resp.ok:
                        body = await resp.body()
                        if 1024 < len(body) <= 200 * 1024 * 1024:
                            with open(path, "wb") as f:
                                f.write(body)
                            got += 1
                            break
                except Exception:
                    pass
    return got


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


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "kw_done": [], "blocked_until": 0}


def save_state(st):
    os.makedirs(ARCHIVE, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def save_db(rec, note_id):
    con = sqlite3.connect(DB)
    metrics = {"likes": rec["likes"], "collects": rec["collects"],
               "comments": rec["comments"], "shares": 0}
    con.execute("""UPDATE xhs_note SET title=COALESCE(NULLIF(title,''),?), body=COALESCE(NULLIF(body,''),?),
            images_json=?, tags_json=?, likes=?, collects=?, comments=?,
            author=COALESCE(NULLIF(author,''),?), publish_time=COALESCE(publish_time,?)
            WHERE note_id=?""",
                (rec["title"], rec["desc"],
                 json.dumps(rec["imgs"], ensure_ascii=False),
                 json.dumps(rec["tags"], ensure_ascii=False),
                 metrics["likes"], metrics["collects"], metrics["comments"],
                 rec["author"], rec["time"], note_id))
    con.execute("""UPDATE content SET body=COALESCE(NULLIF(body,''),?),
            media_json=COALESCE(media_json,?), tags=COALESCE(NULLIF(tags,''),?),
            metrics_json=?, publish_time=COALESCE(publish_time,?),
            author=COALESCE(NULLIF(author,''),?) WHERE text_hash=?""",
                (rec["desc"],
                 json.dumps({"images": rec["imgs"]}, ensure_ascii=False) if rec["imgs"] else None,
                 ",".join(rec["tags"]), json.dumps(metrics, ensure_ascii=False),
                 rec["time"], rec["author"], f"xhs:{note_id}"))
    con.commit()
    con.close()


async def human_search(page, keyword):
    """拿一批带新鲜 xsec_token 的笔记链接。

    优先：直接开搜索结果页（带重试，偶发加载超时）
    备选：首页点搜索框（复制来的 profile 里页头可能不可见，用 force 点击兜底）
    """
    url = ("https://www.xiaohongshu.com/search_result?keyword="
           + urllib.parse.quote(keyword) + "&source=web_explore_feed")
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(14):
                await page.wait_for_timeout(1000)
                cards = await page.query_selector_all("section.note-item")
                if cards:
                    hrefs = []
                    for c in cards:
                        a = await c.query_selector("a.cover")
                        h = (await a.get_attribute("href")) if a else ""
                        if h:
                            if h.startswith("/search_result/"):
                                h = h.replace("/search_result/", "/explore/", 1)
                            if not h.startswith("http"):
                                h = "https://www.xiaohongshu.com" + h
                            hrefs.append(h)
                    if hrefs:
                        return hrefs
        except Exception:
            pass
        await asyncio.sleep(6 + attempt * 4)
    return []


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-keyword", type=int, default=3)
    ap.add_argument("--total", type=int, default=12, help="本轮总条数上限")
    ap.add_argument("--keywords", default="", help="逗号分隔，覆盖默认词表")
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--fix-nicknames", action="store_true",
                    help="只补脱敏昵称：每个脱敏账号取 1 条笔记即可补全")
    ap.add_argument("--accounts", type=int, default=20, help="本轮最多补多少个账号")
    args = ap.parse_args()
    kws = [k.strip() for k in args.keywords.split(",")] if args.keywords else KEYWORDS
    # 补昵称模式：从库里找出昵称脱敏的账号，每个账号挑 1 条笔记（一条就够拿真昵称）
    nick_tasks = []
    if args.fix_nicknames:
        con0 = sqlite3.connect(DB)
        for aid, nick, cnt in con0.execute(
                """SELECT author_id, author, COUNT(*) FROM xhs_note
                   WHERE brand_mention=1 AND author_id!='' AND (author IS NULL OR author LIKE '%*%')
                   GROUP BY author_id ORDER BY COUNT(*) DESC"""):
            row = con0.execute("""SELECT note_id, note_url, xsec_token FROM xhs_note
                                  WHERE author_id=? AND xsec_token!='' LIMIT 1""", (aid,)).fetchone()
            if row:
                nick_tasks.append({"aid": aid, "nick": nick, "cnt": cnt,
                                   "nid": row[0], "url": row[1], "tok": row[2]})
        con0.close()
        print(f"待补昵称账号 {len(nick_tasks)} 个（本轮最多 {args.accounts} 个）")

    st = load_state()
    if st.get("blocked_until", 0) > time.time():
        print(f"⛔ 冷却中，还需 {int(st['blocked_until'] - time.time()) // 60} 分钟")
        return
    os.makedirs(RAW, exist_ok=True)

    from playwright.async_api import async_playwright
    ok = blocked = 0
    with_hits = 0
    async with async_playwright() as p:
        kw = dict(user_data_dir=args.profile, headless=False, locale="zh-CN",
                  viewport={"width": 1440, "height": 900},
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                        "--disable-gpu", "--disable-dev-shm-usage"])
        try:
            ctx = await p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = await p.chromium.launch_persistent_context(**kw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        if args.fix_nicknames:
            for task in nick_tasks[: args.accounts]:
                if ok >= args.total:
                    break
                nid = task["nid"]
                href = task["url"] or ""
                if not href.startswith("http"):
                    href = f"https://www.xiaohongshu.com/explore/{nid}"
                npg = await ctx.new_page()
                try:
                    await npg.goto(href, wait_until="domcontentloaded", timeout=45000)
                    await npg.wait_for_timeout(2600 + random.randint(0, 1200))
                    html = await npg.content()
                    if "安全限制" in html or "访问频繁" in html:
                        st["blocked_until"] = time.time() + 2400
                        save_state(st)
                        print("  ⛔ 命中安全限制，停止本轮（冷却 40 分钟）", flush=True)
                        blocked += 1
                        break
                    rec = await npg.evaluate(DOM_JS)
                    if not rec or not rec.get("author"):
                        print(f"  · {nid[:10]} 未取到昵称（可能 token 过期）", flush=True)
                        try:
                            await npg.close()
                        except Exception:
                            pass
                        continue
                    newnick = rec["author"].strip()
                    con = sqlite3.connect(DB)
                    con.execute("UPDATE xhs_note SET author=? WHERE author_id=?", (newnick, task["aid"]))
                    con.execute("UPDATE content SET author=? WHERE author_id=?", (newnick, task["aid"]))
                    con.execute("""UPDATE creator SET nickname=? WHERE creator_id=?""", (newnick, task["aid"]))
                    con.commit()
                    con.close()
                    st.setdefault("done", []).append(nid)
                    ok += 1
                    print(f"  ✅ [{ok}/{args.accounts}] 账号{task['aid'][:10]} "
                          f"「{task['nick']}」→「{newnick}」（{task['cnt']} 条）", flush=True)
                    save_state(st)
                    try:
                        await npg.close()
                    except Exception:
                        pass
                    d = random.uniform(9, 20)
                    if random.random() < 0.25:
                        d += random.uniform(18, 45)
                    await asyncio.sleep(d)
                except Exception as e:
                    print(f"  ! {type(e).__name__}: {str(e)[:80]}", flush=True)
                    try:
                        await npg.close()
                    except Exception:
                        pass
                    await asyncio.sleep(5)
            await ctx.close()
            save_state(st)
            print(f"\n=== 本轮结束：补齐 {ok} 个账号昵称，命中风控 {blocked} ===", flush=True)
            return

        for keyword in kws:
            if ok >= args.total:
                break
            if keyword in st.get("kw_done", []) and keyword in st.get("kw_retry", []):
                continue
            print(f"\n=== 关键词：{keyword} ===", flush=True)
            hrefs = await human_search(page, keyword)
            if not hrefs:
                print("  搜索页未出结果，跳过该词", flush=True)
                st.setdefault("kw_done", []).append(keyword)
                save_state(st)
                await asyncio.sleep(random.uniform(25, 50))
                continue
            print(f"  结果 {len(hrefs)} 条，取前 {args.per_keyword} 条", flush=True)

            for ci, href in enumerate(hrefs[: args.per_keyword]):
                if ok >= args.total:
                    break
                # 取新鲜 xsec_token 后直接导航（点击易因重渲染失败）
                npg = await ctx.new_page()
                try:
                    await npg.goto(href, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"  ! 打开失败 {type(e).__name__}", flush=True)
                    try:
                        await npg.close()
                    except Exception:
                        pass
                    await asyncio.sleep(4)
                    continue
                try:
                    await npg.wait_for_timeout(2600 + random.randint(0, 1200))
                    html = await npg.content()
                    if "安全限制" in html or "访问频繁" in html or "验证码" in html:
                        st["blocked_until"] = time.time() + 2400
                        save_state(st)
                        print("  ⛔ 命中安全限制，停止本轮（冷却 40 分钟）", flush=True)
                        blocked += 1
                        break
                    rec = await npg.evaluate(DOM_JS)
                    if not rec or not rec.get("nid"):
                        await npg.close()
                        continue
                    nid = rec["nid"]
                    rec["likes"] = num(rec.get("likes"))
                    # 时间：DOM 只有「05-21」时用 note_id 时间戳补全
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
                    rec["collects"] = num(rec.get("collects"))
                    rec["comments"] = num(rec.get("comments"))
                    with open(os.path.join(RAW, f"{nid}.json"), "w", encoding="utf-8") as f:
                        json.dump(rec, f, ensure_ascii=False, indent=1)
                    save_db(rec, nid)
                    # 趁 URL 新鲜立刻落盘（过期后补下会 403）
                    try:
                        nmedia = await grab_media(ctx, rec, nid)
                        if nmedia:
                            print(f"     本地存档 {nmedia} 个媒体文件", flush=True)
                    except Exception as e:
                        print(f"     媒体下载异常 {type(e).__name__}", flush=True)
                    st.setdefault("done", []).append(nid)
                    ok += 1
                    with_hits += 1 if (rec.get("desc") or rec.get("imgs")) else 0
                    print(f"  ✅ [{ok}/{args.total}] {keyword} | {nid[:10]} | "
                          f"{rec.get('author','')[:14]} | 正文{len(rec.get('desc') or '')} "
                          f"图{len(rec.get('imgs') or [])} 标签{len(rec.get('tags') or [])} "
                          f"时间{rec.get('time','')[:10]}", flush=True)
                    save_state(st)
                    # 评论文本也留档
                    if rec.get("cmtTexts"):
                        with open(os.path.join(RAW, f"{nid}.comments.json"), "w", encoding="utf-8") as f:
                            json.dump(rec["cmtTexts"], f, ensure_ascii=False, indent=1)
                    # 关掉弹窗，回到搜索结果
                    try:
                        await npg.close()
                    except Exception:
                        pass
                    d = random.uniform(9, 20)
                    if random.random() < 0.25:
                        d += random.uniform(18, 45)
                        await page.mouse.wheel(0, 500)
                    await asyncio.sleep(d)
                except Exception as e:
                    print(f"  ! {type(e).__name__}: {str(e)[:90]}", flush=True)
                    try:
                        await npg.close()
                    except Exception:
                        pass
                    await asyncio.sleep(5)
            st.setdefault("kw_done", []).append(keyword)
            save_state(st)
            await asyncio.sleep(random.uniform(30, 70))
        await ctx.close()
    save_state(st)
    print(f"\n=== 本轮结束：成功 {ok}（其中有正文/图片 {with_hits}），命中风控 {blocked} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
