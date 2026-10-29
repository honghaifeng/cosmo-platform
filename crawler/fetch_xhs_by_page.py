#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书详情补全（页面版）—— 针对「没有 xsec_token、只能靠 explore 直链打开」的笔记。

为什么需要这个脚本：
  搜索/主页列表抓到的笔记只有标题卡片，没有正文、没有发布时间、没有媒体，
  而这类记录在 xhs_note 里没有 xsec_token，无法走 get_note_by_id 接口。
  唯一可靠路径是直接打开 https://www.xiaohongshu.com/explore/<note_id>，
  从页面 SSR 的 __INITIAL_STATE__ 里取 noteDetailMap，拿全 desc / time / imageList / video。

特性：
  · 断点续跑：正文已存在的自动跳过
  · 风控退避：命中「安全限制/访问频繁」自动拉长间隔，连续多次则暂停本轮
  · 媒体现采现下：图片/视频直链几小时内过期，落地到 knowledge/media/<brand>/xhs/
  · 双表回写：xhs_note + content（text_hash = xhs:<note_id>）

用法：
  tools/MediaCrawler/.venv/bin/python crawler/fetch_xhs_by_page.py --limit 20
  tools/MediaCrawler/.venv/bin/python crawler/fetch_xhs_by_page.py --brand MitoQ --limit 200 --media
"""
import argparse
import asyncio
import datetime
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW_DIR = os.path.join(ROOT, "data", "xhs_archive", "raw")
MEDIA_ROOT = os.path.join(ROOT, "knowledge", "media")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")

sys.path.insert(0, MC)


def load_pending(limit, brands=None, ids=None):
    con = sqlite3.connect(DB)
    q = ("SELECT text_hash, url, title, brand, author, author_id FROM content "
         "WHERE platform='xhs' AND (verified IS NULL OR verified=0) "
         "AND (body IS NULL OR body='') ")
    p = []
    if brands:
        q += " AND brand IN (%s) " % ",".join("?" * len(brands))
        p += brands
    if ids:
        q += " AND text_hash IN (%s) " % ",".join("?" * len(ids))
        p += [f"xhs:{i}" for i in ids]
    q += " ORDER BY id DESC LIMIT ?"
    p.append(limit)
    rows = con.execute(q, p).fetchall()
    con.close()
    return rows


EXTRACT_JS = r"""
() => {
  const out = {};
  try {
    const st = window.__INITIAL_STATE__;
    if (st && st.note && st.note.noteDetailMap) {
      const m = st.note.noteDetailMap;
      const k = Object.keys(m)[0];
      if (k && m[k] && m[k].note) {
        const n = m[k].note;
        out.src = 'ssr';
        out.title = n.title || '';
        out.desc = n.desc || '';
        out.time = n.time || null;
        out.ip = n.ipLocation || '';
        out.type = n.type || '';
        out.liked = n.interactInfo ? n.interactInfo.likedCount : null;
        out.collected = n.interactInfo ? n.interactInfo.collectedCount : null;
        out.comment = n.interactInfo ? n.interactInfo.commentCount : null;
        out.share = n.interactInfo ? n.interactInfo.shareCount : null;
        out.user = n.user ? {nickname: n.user.nickname, user_id: n.user.userId} : {};
        out.images = (n.imageList || []).map(i => i.urlDefault || i.urlPre || (i.infoList && i.infoList[0] && i.infoList[0].url) || '').filter(Boolean);
        out.tags = (n.tagList || []).map(t => t.name).filter(Boolean);
        let v = '';
        try {
          const vv = n.video && n.video.media && n.video.media.video && n.video.media.video.streams;
          if (vv) {
            for (const kk of ['h264','av1','h265']) {
              if (vv[kk] && vv[kk].length) { v = vv[kk][0].masterUrl || vv[kk][0].backupUrls?.[0] || ''; if (v) break; }
            }
          }
        } catch(e) {}
        out.video = v;
        return out;
      }
    }
  } catch (e) { out.err = String(e); }
  // 兜底：DOM 解析
  const q = s => document.querySelector(s);
  out.src = out.src || 'dom';
  out.title = (q('#detail-title')?.innerText || q('.title')?.innerText || '').trim();
  out.desc = (q('#detail-desc')?.innerText || q('.desc')?.innerText || '').trim();
  out.images = Array.from(document.querySelectorAll('.swiper-slide img, .note-slider img'))
      .map(i => i.src).filter(s => s && s.startsWith('http'));
  out.user = (q('.username')?.innerText || '').trim();
  return out;
}
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--brand", type=str, default="")
    ap.add_argument("--ids", type=str, default="")
    ap.add_argument("--media", action="store_true", help="下载图片/视频到本地")
    ap.add_argument("--save-html", action="store_true", help="保存原文页 HTML 归档")
    ap.add_argument("--sleep", type=float, default=4.0)
    ap.add_argument("--headless", action="store_true", default=True)
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    rows = load_pending(args.limit,
                        brands=[b.strip() for b in args.brand.split(",") if b.strip()] or None,
                        ids=[x.strip() for x in args.ids.split(",") if x.strip()] or None)
    print(f"待补 {len(rows)} 条", flush=True)
    if not rows:
        return

    from playwright.async_api import async_playwright

    con = sqlite3.connect(DB)
    ok = fail = blocked_streak = 0
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=args.headless, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-gpu", "--disable-dev-shm-usage"])
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await pg.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(2500)
        except Exception:
            pass

        for i, (th, url, title, brand, author, author_id) in enumerate(rows, 1):
            if blocked_streak >= 6:
                print("  ⚠️ 连续风控，暂停本轮（可重跑续补）", flush=True)
                break
            nid = th.split(":", 1)[1]
            try:
                await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                await pg.wait_for_timeout(2600)
                html = await pg.content()
                if ("安全限制" in html) or ("访问频繁" in html) or ("验证" in html and "滑块" in html):
                    blocked_streak += 1
                    wait = min(120, 15 * blocked_streak)
                    print(f"  [{i}/{len(rows)}] 风控，等待 {wait}s …", flush=True)
                    await asyncio.sleep(wait)
                    continue
                d = await pg.evaluate(EXTRACT_JS)
            except Exception as e:
                fail += 1
                print(f"  [{i}/{len(rows)}] 打开失败 {type(e).__name__}", flush=True)
                await asyncio.sleep(args.sleep)
                continue

            desc = (d.get("desc") or "").strip()
            if not desc and not d.get("images") and not d.get("video"):
                blocked_streak += 1
                fail += 1
                print(f"  [{i}/{len(rows)}] 空页面 {nid}", flush=True)
                await asyncio.sleep(args.sleep * 2)
                continue

            blocked_streak = 0
            ok += 1
            imgs = [u for u in (d.get("images") or []) if u]
            vurl = d.get("video") or ""
            tags = d.get("tags") or []
            ts = d.get("time")
            if ts and len(str(ts)) > 10:
                ts = int(str(ts)[:10])
            pub = (datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                   if ts else None)
            nm = (d.get("user") or {}).get("nickname") or author
            uid = (d.get("user") or {}).get("user_id") or author_id
            metrics = {"likes": d.get("liked"), "collects": d.get("collected"),
                       "comments": d.get("comment"), "shares": d.get("share")}

            with open(os.path.join(RAW_DIR, f"{nid}.json"), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            if args.save_html:
                with open(os.path.join(RAW_DIR, f"{nid}.html"), "w", encoding="utf-8") as f:
                    f.write(html)

            # 媒体落地（直链会过期，必须现采现下）
            media_local = []
            if args.media:
                bdir = os.path.join(MEDIA_ROOT, brand or "未分类", "xhs")
                os.makedirs(bdir, exist_ok=True)
                for j, u in enumerate(imgs[:9]):
                    fp = os.path.join(bdir, f"{nid}_{j}.jpg")
                    if os.path.exists(fp):
                        media_local.append(os.path.relpath(fp, ROOT))
                        continue
                    try:
                        r = await pg.request.get(u, headers={"Referer": "https://www.xiaohongshu.com/"})
                        if r.ok:
                            b = await r.body()
                            if len(b) > 3000:
                                open(fp, "wb").write(b)
                                media_local.append(os.path.relpath(fp, ROOT))
                    except Exception:
                        pass
                if vurl:
                    fp = os.path.join(bdir, f"{nid}.mp4")
                    if not os.path.exists(fp):
                        try:
                            r = await pg.request.get(vurl, headers={"Referer": "https://www.xiaohongshu.com/"})
                            if r.ok:
                                b = await r.body()
                                if len(b) > 100_000:
                                    open(fp, "wb").write(b)
                                    media_local.append(os.path.relpath(fp, ROOT))
                        except Exception:
                            pass
                    elif os.path.exists(fp):
                        media_local.append(os.path.relpath(fp, ROOT))

            mj = {}
            if imgs:
                mj["images"] = imgs
            if vurl:
                mj["video"] = vurl
            if media_local:
                mj["local"] = media_local

            con.execute("""UPDATE content SET body=?, title=COALESCE(NULLIF(title,''),?),
                    media_json=?, tags=COALESCE(NULLIF(tags,''),?),
                    metrics_json=?, publish_time=COALESCE(publish_time,?),
                    author=COALESCE(NULLIF(author,''),?), author_id=COALESCE(NULLIF(author_id,''),?)
                    WHERE text_hash=?""",
                        (desc, d.get("title"), json.dumps(mj, ensure_ascii=False) if mj else None,
                         ",".join(tags), json.dumps(metrics, ensure_ascii=False),
                         pub, nm, uid, th))
            con.execute("""INSERT INTO xhs_note (note_id,title,body,publish_time,author,author_id,
                    images_json,tags_json,likes,collects,comments,shares,brand,note_url,xsec_token,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',?)
                ON CONFLICT(note_id) DO UPDATE SET
                    body=CASE WHEN IFNULL(excluded.body,'')<>'' THEN excluded.body ELSE body END,
                    publish_time=COALESCE(publish_time, excluded.publish_time),
                    images_json=CASE WHEN IFNULL(excluded.images_json,'[]') NOT IN ('','[]')
                        THEN excluded.images_json ELSE images_json END,
                    updated_at=excluded.updated_at""",
                        (nid, d.get("title"), desc, pub, nm, uid,
                         json.dumps(imgs, ensure_ascii=False), json.dumps(tags, ensure_ascii=False),
                         metrics["likes"] or 0, metrics["collects"] or 0,
                         metrics["comments"] or 0, metrics["shares"] or 0, brand, url,
                         datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
            con.commit()
            print(f"  [{i}/{len(rows)}] ✅ {nid} 正文{len(desc)} 图{len(imgs)} 视频{1 if vurl else 0} 本地{len(media_local)}",
                  flush=True)
            await asyncio.sleep(args.sleep)

        con.commit()
        con.close()
        await ctx.close()
    print(f"=== 结束：成功 {ok}，失败/跳过 {fail} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
