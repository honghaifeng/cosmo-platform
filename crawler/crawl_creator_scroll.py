#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按创作者主页滚动抓取（绕开 API 风控）。

背景：小红书「按用户翻笔记」接口(/api/sns/web/v1/user_posted)会返回 461 验证码，
但直接用浏览器打开主页、滚动加载是正常的——等于人工浏览。
本脚本用 playwright 打开主页、滚动到不再加载，收集所有笔记卡片：
标题 / 点赞 / 收藏 / 评论 / 笔记链接（含 note_id 与 xsec_token）。

用法：
  tools/MediaCrawler/.venv/bin/python crawler/crawl_creator_scroll.py --list
  tools/MediaCrawler/.venv/bin/python crawler/crawl_creator_scroll.py --run --top 5
"""
import argparse
import json
import os
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
TOKEN_JSON = os.path.join(ROOT, "data", "creator_tokens.json")

OFFICIAL_PAT = re.compile(
    r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时|mitoq|granver|laife|乐梵|"
    r"端粒塔|w\+|赛乐瑞|塞乐瑞|cellerator|官方)", re.I)

JS_COLLECT = """
() => {
  const out = [];
  // 页面改版后卡片不再包在 section.note-item 里；直接抓笔记链接锚点（探针验证 a.cover=30）
  document.querySelectorAll("a[href*='/explore/'], a[href*='/discovery/item/']").forEach(a => {
    const href = a.getAttribute('href') || '';
    if (!href.includes('/explore/') && !href.includes('/discovery/item/')) return;
    const card = a.closest('section') || a.parentElement;
    const t = card ? (card.querySelector('span.note-item-title, .title span, a.title span')) : null;
    const like = card ? card.querySelector('.like-wrapper .count, span.count') : null;
    const cmt = card ? card.querySelector('.chat-wrapper .count') : null;
    out.push({
      href,
      title: t ? t.innerText.trim() : (a.getAttribute('title') || ''),
      likes: like ? like.innerText.trim() : '',
      comments: cmt ? cmt.innerText.trim() : ''
    });
  });
  return out;
}
"""


def num(s):
    s = (s or "").strip().replace("+", "")
    if not s:
        return 0
    if s.endswith("万"):
        try:
            return int(float(s[:-1]) * 10000)
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


def load_targets(top, official_only, ids=None):
    tokens = json.load(open(TOKEN_JSON, encoding="utf-8"))
    con = sqlite3.connect(DB)
    rows = con.execute(
        'SELECT creator_id, nickname, note_count, brands FROM creator WHERE platform="xhs"').fetchall()
    con.close()
    if ids:
        # 定点抓取：只抓指定 creator_id（六品牌官号用）
        idset = set(ids)
        rows = [r for r in rows if r[0] in idset]
    by_brand = {}
    for cid, nick, n, brands in rows:
        if cid not in tokens:
            continue
        for b in (brands or "").split(","):
            if b:
                by_brand.setdefault(b, []).append((cid, nick, n))
    targets = []
    for brand, lst in by_brand.items():
        lst.sort(key=lambda x: -(x[2] or 0))
        if ids:
            pass  # 定点模式：名单就是全部目标
        elif official_only:
            lst = [x for x in lst if OFFICIAL_PAT.search(x[1] or "")] or lst[:1]
        else:
            off = [x for x in lst if OFFICIAL_PAT.search(x[1] or "")]
            lst = off + [x for x in lst if x not in off]
        for cid, nick, n in lst[:top if not ids else len(lst)]:
            targets.append({"brand": brand, "cid": cid, "nick": nick, "seen": n,
                            "url": (f"https://www.xiaohongshu.com/user/profile/{cid}"
                                    f"?xsec_token={tokens[cid]}&xsec_source=pc_search")})
    return targets


def scrape(page, url, max_scroll=30, wait=1.6):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3500)
    seen = {}
    last = 0
    for i in range(max_scroll):
        cards = page.evaluate(JS_COLLECT)
        for c in cards:
            m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{16,32})", c.get("href") or "")
            if not m:
                continue
            nid = m.group(1)
            if nid in seen:
                continue
            seen[nid] = {
                "note_id": nid,
                "title": c.get("title", ""),
                "likes": num(c.get("likes")),
                "comments": num(c.get("comments")),
                "href": c.get("href", ""),
            }
        if len(seen) == last and i > 2:
            break
        last = len(seen)
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(int(wait * 1000))
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--official-only", action="store_true")
    ap.add_argument("--ids", type=str, default="",
                    help="逗号分隔 creator_id：定点抓指定主页（六品牌官号）")
    ap.add_argument("--max-scroll", type=int, default=30)
    ap.add_argument("--show", action="store_true", help="显示浏览器窗口（默认无头，避免弹窗干扰）")
    args = ap.parse_args()

    ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
    targets = load_targets(args.top, args.official_only, ids=ids)
    print(f"目标账号 {len(targets)} 个")
    for t in targets:
        flag = "官方" if OFFICIAL_PAT.search(t["nick"] or "") else "达人"
        print(f"  [{flag}] {t['brand']:14s} {t['nick'][:18]:20s} 已见 {t['seen']:3d} 条")
    if not args.run:
        return

    from playwright.sync_api import sync_playwright
    con = sqlite3.connect(DB)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    total_new = 0
    with sync_playwright() as p:
        # 必须复用已登录的 profile：干净上下文每次都要求重新扫码登录，
        # 而且小红书主页列表页在未登录状态下直接返回空。
        # 登录态来源：scripts/login_window.py xhs（登录一次即写入该目录）
        UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        launch_kw = dict(headless=not args.show,
                         args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                               "--disable-gpu", "--disable-dev-shm-usage"])
        ctx_kw = dict(locale="zh-CN", viewport={"width": 1280, "height": 900}, user_agent=UA)
        ctx = None
        if os.path.isdir(PROFILE):
            try:
                os.makedirs(PROFILE, exist_ok=True)
                ctx = p.chromium.launch_persistent_context(
                    PROFILE, channel="chrome", **launch_kw, **ctx_kw)
                print("内核: 本机 Chrome（复用已登录 profile）", flush=True)
            except Exception as e:
                print(f"profile 启动失败（{e}），回退干净上下文（会要求重新登录）", flush=True)
                ctx = None
        if ctx is None:
            try:
                browser = p.chromium.launch(channel="chrome", **launch_kw)
                ctx = browser.new_context(**ctx_kw)
                print("内核: 本机 Chrome（干净上下文）", flush=True)
            except Exception as e:
                print(f"Chrome 通道不可用（{e}），回退 Playwright Chromium", flush=True)
                browser = p.chromium.launch(**launch_kw)
                ctx = browser.new_context(**ctx_kw)
        for t in targets:
            pg = None
            try:
                pg = ctx.new_page()   # 每个账号独立页面，崩了不影响下一个
                notes = scrape(pg, t["url"], args.max_scroll)
            except Exception as e:
                print(f"  ! {t['nick']}: {type(e).__name__}: {e}", flush=True)
                try:
                    if pg:
                        pg.close()
                except Exception:
                    pass
                continue
            new = 0
            for nt in notes:
                href = nt["href"] or ""
                m_tok = re.search(r"[?&]xsec_token=([^&]+)", href)
                xtok = m_tok.group(1) if m_tok else ""
                full_url = ("https://www.xiaohongshu.com/" + href.lstrip("/")) if href else ""
                cur = con.execute(
                    """INSERT OR IGNORE INTO content
                       (brand, platform, content_type, title, body, author, author_id,
                        collect_time, url, metrics_json, tags, raw_path, text_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (t["brand"], "xhs", "note", nt["title"], "", t["nick"], t["cid"], now,
                     full_url,
                     json.dumps({"likes": nt["likes"], "comments": nt["comments"]}, ensure_ascii=False),
                     "创作者主页抓取", "creator_scroll", f"xhs:{nt['note_id']}"))
                if cur.rowcount:
                    new += 1
                # 关键：把主页卡片里的 xsec_token 落到 xhs_note，
                # 后续 fetch_xhs_details.py 才能走接口补正文/时间/媒体。
                # 已有正文不覆盖，只补 token 与缺失字段。
                if xtok:
                    con.execute(
                        """INSERT INTO xhs_note (note_id, title, body, author, author_id,
                                brand, note_url, xsec_token, updated_at)
                           VALUES (?,?,'',?,?,?,?,?,?)
                           ON CONFLICT(note_id) DO UPDATE SET
                             xsec_token=CASE WHEN IFNULL(xsec_token,'')='' THEN excluded.xsec_token
                                             ELSE xsec_token END,
                             note_url=CASE WHEN IFNULL(note_url,'')='' THEN excluded.note_url
                                           ELSE note_url END,
                             brand=CASE WHEN IFNULL(brand,'')='' THEN excluded.brand ELSE brand END,
                             updated_at=excluded.updated_at""",
                        (nt["note_id"], nt["title"], t["nick"], t["cid"], t["brand"],
                         full_url, xtok, now))
            con.commit()
            total_new += new
            sum_likes = sum(n["likes"] for n in notes)
            sum_cmts = sum(n["comments"] for n in notes)
            con.execute(
                """UPDATE creator SET note_count=?, total_likes=?, total_comments=?, updated_at=?
                   WHERE platform='xhs' AND creator_id=?""",
                (len(notes), sum_likes, sum_cmts, now, t["cid"]))
            print(f"  {t['nick'][:16]:18s} 主页 {len(notes):3d} 条，新增入库 {new:3d}", flush=True)
            time.sleep(2)
        ctx.close()
        try:
            browser.close()
        except Exception:
            pass
    print(f"=== 创作者主页抓取完成，共新增 {total_new} 条 ===")


if __name__ == "__main__":
    main()
