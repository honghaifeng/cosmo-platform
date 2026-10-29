#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书/抖音详情页正文补齐（Playwright + 项目登录态，代替裸 curl）

为什么不用 fetch_fulltext.py 的 curl：
  小红书/抖音详情页要登录态 + 前端渲染，裸 curl 拿不到签名接口，
  只会每天刷出上百条假失败。本脚本直接复用 MediaCrawler 的浏览器 profile，
  带 cookie 打开详情页，从小红书 __INITIAL_STATE__.note.noteDetailMap 取正文；
  遇到 300013（访问频繁）/验证码就整体停下，不反复重试。

用法：
  tools/MediaCrawler/.venv/bin/python crawler/fetch_detail_browser.py --platform xhs
  ... --platform xhs --limit 20 --dry          # 只看不写
  ... --platform douyin --limit 50
退出码：0 正常；2 撞到风控（需等冷却）
"""
import argparse
import datetime
import json
import os
import random
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
PROFILES = {
    "xhs": os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "xhs_user_data_dir"),
    "douyin": os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "dy_user_data_dir"),
}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

XHS_STATE_JS = """() => {
  try {
    const st = window.__INITIAL_STATE__;
    if (!st || !st.note || !st.note.noteDetailMap) return null;
    const m = st.note.noteDetailMap;
    const k = Object.keys(m).find(x => m[x] && m[x].note);
    if (!k) return null;
    const n = m[k].note;
    // 图片：imageList 里每项的 urlDefault/url（页面当前签名，直接可下）
    const imgs = (n.imageList || []).map(x => x.urlDefault || x.url || '').filter(Boolean);
    // 视频：video.media.videoStreams 里挑 masterUrl（H264 主流）
    let vurl = '';
    try {
      const vs = (n.video && n.video.media && n.video.media.videoStreams) || [];
      const v = vs.find(s => s.masterUrl) || vs[0];
      vurl = (v && (v.masterUrl || v.url)) || '';
    } catch (e) {}
    return {desc: n.desc || '', title: n.title || '',
            time: n.time || n.lastUpdateTime || null,
            user: (n.user && n.user.nickname) || '',
            images: imgs, video: vurl,
            type: n.type || '',
            interact: n.interactInfo || n.interact_info || null};
  } catch (e) { return {err: String(e)}; }
}"""

WEB_TEXT_JS = """() => {
  const bad = ['script','style','nav','header','footer','aside','form'];
  for (const t of bad) document.querySelectorAll(t).forEach(e => e.remove());
  const cands = [document.querySelector('article'), document.querySelector('#content'),
                 document.querySelector('.article'), document.querySelector('.content'),
                 document.body];
  let best = '';
  for (const c of cands) {
    if (!c) continue;
    const t = (c.innerText || '').replace(/\n{2,}/g, '\n').trim();
    if (t.length > best.length) best = t;
  }
  return {desc: best.slice(0, 12000)};
}"""

DY_STATE_JS = """() => {
  try {
    for (const s of document.querySelectorAll('script')) {
      const t = s.textContent || '';
      if (t.includes('_ROUTER_DATA')) {
        const i = t.indexOf('{');
        const data = JSON.parse(t.slice(i, t.lastIndexOf('}') + 1));
        const ld = data.loaderData || {};
        for (const k of Object.keys(ld)) {
          const v = ld[k];
          const info = v && (v.videoInfoRes || v.awemeDetail);
          if (info) {
            const item = (info.item_list && info.item_list[0]) || info.aweme_detail || info;
            if (item) return {desc: item.desc || '', title: item.desc || '',
                              user: '', time: item.create_time || null, interact: null};
          }
        }
      }
    }
    return null;
  } catch (e) { return {err: String(e)}; }
}"""


def targets(con, platform, limit, ignore_skip=False, ids=None):
    if ids:
        ph = ",".join("?" * len(ids))
        rows = con.execute(
            f"""SELECT id, url, body FROM content
                WHERE platform=? AND id IN ({ph})
                ORDER BY id""", (platform, *ids)).fetchall()
        return rows
    if platform == "web":
        sql = ("""SELECT id, url, body FROM content
                  WHERE platform IN ('baidu','toutiao','web')
                    AND (body IS NULL OR TRIM(body)='' OR LENGTH(TRIM(body))<200)
                  ORDER BY LENGTH(COALESCE(body,'')) ASC""")
        if not ignore_skip:
            sql = sql.replace("ORDER BY", "AND id NOT IN (SELECT content_id FROM fetch_skip WHERE attempts>=3) ORDER BY")
        rows = con.execute(sql).fetchall()
        return rows[:limit] if limit else rows
    rows = con.execute(
        """SELECT id, url, body FROM content
           WHERE platform=? AND (body IS NULL OR TRIM(body)='' OR LENGTH(TRIM(body))<40)
             AND id NOT IN (SELECT content_id FROM fetch_skip WHERE attempts>=2)
           ORDER BY id DESC""", (platform,)).fetchall()
    return rows[:limit] if limit else rows


def bump(con, cid, reason, now):
    row = con.execute("SELECT attempts FROM fetch_skip WHERE content_id=?", (cid,)).fetchone()
    n = (row[0] if row else 0) + 1
    con.execute("INSERT OR REPLACE INTO fetch_skip (content_id,attempts,reason,updated_at) VALUES (?,?,?,?)",
                (cid, n, reason, now))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["xhs", "douyin", "web"])
    ap.add_argument("--ignore-skip", action="store_true", help="忽略 fetch_skip 已放弃的记录（浏览器常能救回 curl 抓不到的）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", type=str, default="",
                    help="逗号分隔的 content.id：定点补抓（媒体救回用）")
    ap.add_argument("--media", action="store_true",
                    help="同时下载图片/视频到本地（CDN 直链会过期，现采现下）")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--headless", action="store_true", default=False)
    ap.add_argument("--max-block", type=int, default=3, help="连续多少次风控就整体停")
    ap.add_argument("--sleep-min", type=float, default=4, help="拟人间隔下限秒")
    ap.add_argument("--sleep-max", type=float, default=9, help="拟人间隔上限秒")
    ap.add_argument("--profile", default="", help="浏览器 profile 目录（默认用 MediaCrawler 的登录态目录）")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ids = [int(x) for x in args.ids.split(",") if x.strip().isdigit()] if args.ids else None
    rows = targets(con, args.platform, args.limit, args.ignore_skip, ids=ids)
    print(f"[{args.platform}] 待补正文 {len(rows)} 条", flush=True)
    if not rows:
        return 0

    js = XHS_STATE_JS if args.platform == "xhs" else (DY_STATE_JS if args.platform == "douyin" else WEB_TEXT_JS)
    ok = fail = block = 0
    with sync_playwright() as p:
        if args.platform == "web":
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 900}, user_agent=UA)
        else:
            browser = None
            ctx = p.chromium.launch_persistent_context(
                args.profile or PROFILES[args.platform], headless=args.headless,
                args=["--disable-blink-features=AutomationControlled"], locale="zh-CN",
                viewport={"width": 1440, "height": 900})
        pg = ctx.pages[0] if getattr(ctx, "pages", None) else ctx.new_page()
        for cid, url, body in rows:
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3000 if args.platform == "web" else 2500)
                cur = pg.url
                if "website-login/error" in cur or "error_code=300013" in cur:
                    block += 1
                    print(f"  x id={cid} 风控拦截：{cur.split('error_msg=')[-1][:40] or '300013'}", flush=True)
                else:
                    data = None
                    if args.platform == "web":
                        # 通用网页不做 DOM 抽取脚本，直接在 Python 侧取渲染后的可见文本，
                        # 避开重定向时 evaluate 执行上下文被销毁的问题
                        txt = ""
                        for _try in range(3):
                            try:
                                txt = pg.inner_text("body") or ""
                                break
                            except Exception:
                                pg.wait_for_timeout(1500)
                        data = {"desc": txt}
                    else:
                        for _try in range(3):
                            try:
                                data = pg.evaluate(js)
                                break
                            except Exception:
                                pg.wait_for_timeout(1500)
                    data = data or {}
                    desc = (data.get("desc") or "").strip()
                    imgs = data.get("images") or []
                    vurl = (data.get("video") or "").strip()
                    # 媒体现采现下：CDN 直链签名几小时就过期，拿到当刻必须立刻落盘
                    got_media = 0
                    if args.media and (imgs or vurl):
                        mdir = os.path.join(ROOT, "data", "xhs_archive", "media")
                        os.makedirs(mdir, exist_ok=True)
                        nid = ""
                        m = __import__("re").search(r"/(?:explore|discovery/item)/([0-9a-f]{16,32})", url)
                        if m:
                            nid = m.group(1)
                        for j, iu in enumerate(imgs[:6]):
                            fp = os.path.join(mdir, f"{nid}_{j}.jpg") if nid else ""
                            if fp and not os.path.exists(fp):
                                try:
                                    r = pg.request.get(iu, headers={"Referer": "https://www.xiaohongshu.com/"})
                                    if r.ok and len(r.body()) > 2000:
                                        with open(fp, "wb") as f:
                                            f.write(r.body())
                                        got_media += 1
                                except Exception:
                                    pass
                        if vurl and nid:
                            fp = os.path.join(mdir, f"{nid}.mp4")
                            if not os.path.exists(fp):
                                try:
                                    r = pg.request.get(vurl, headers={"Referer": "https://www.xiaohongshu.com/"})
                                    if r.ok and len(r.body()) > 100_000:
                                        with open(fp, "wb") as f:
                                            f.write(r.body())
                                        got_media += 1
                                except Exception:
                                    pass
                    need = 200 if args.platform == "web" else 20
                    # 有实质正文，或抓到了图片/视频素材（媒体本身就是内容），都算成功
                    if len(desc) >= need or got_media:
                        if not args.dry:
                            con.execute("UPDATE content SET body=? WHERE id=?", (desc, cid))
                            if imgs or vurl:
                                try:
                                    old = con.execute("SELECT media_json FROM content WHERE id=?", (cid,)).fetchone()
                                    old_mj = json.loads(old[0] or "{}") if old else {}
                                except Exception:
                                    old_mj = {}
                                mj = {**old_mj}
                                if imgs:
                                    mj["images"] = imgs
                                if vurl:
                                    mj["video"] = vurl
                                con.execute("UPDATE content SET media_json=? WHERE id=?",
                                            (json.dumps(mj, ensure_ascii=False), cid))
                            con.execute("DELETE FROM fetch_skip WHERE content_id=?", (cid,))
                        ok += 1
                        print(f"  ok id={cid} len={len(desc)} 图{len(imgs)} 视频{'✓' if vurl else '—'} 落盘{got_media}", flush=True)
                    else:
                        text = pg.inner_text("body")[:120].replace("\n", " ")
                        # 软降级识别：连续高频访问后详情接口被截断，页面只剩导航壳
                        # （「首页 点点 ai RED…」），不是笔记被删除——不能记 fetch_skip
                        nav_only = text.startswith("首页 点点") or "沪ICP备" in text[:120]
                        if "验证" in text or "频繁" in text or nav_only:
                            block += 1
                            print(f"  x id={cid} 页面疑似降级/风控：{text[:50]}", flush=True)
                        else:
                            fail += 1
                            if not args.dry:
                                bump(con, cid, "详情页无正文/已删除", now)
                            print(f"  - id={cid} 无正文（页面首屏：{text[:40]}）", flush=True)
            except Exception as e:
                fail += 1
                if not args.dry:
                    bump(con, cid, f"异常:{type(e).__name__}", now)
                print(f"  ! id={cid} {type(e).__name__}", flush=True)
            if not args.dry:
                con.commit()
            if block >= args.max_block:
                print(f"连续 {block} 次风控，停止本轮（等冷却后重跑）", flush=True)
                break
            # 拟人节奏：随机间隔，避免固定节拍被识别
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))
        ctx.close()
        if browser:
            browser.close()
    con.close()
    print(f"完成：成功 {ok}，无正文 {fail}，风控 {block}", flush=True)
    return 2 if block >= args.max_block else 0


if __name__ == "__main__":
    sys.exit(main())
