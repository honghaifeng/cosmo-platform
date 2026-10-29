#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按创作者主页「点击」抓取笔记详情（拿正文/时间/媒体，同时回收 xsec_token）。

为什么不能直接开链接：
  2025 年后主页卡片的 href 只剩 /explore/<note_id>，不带 xsec_token；
  直接访问该链接会被小红书重定向到 404「当前笔记暂时无法浏览」(error_code=300031)。
  只有从主页点进去（SPA 内部跳转）才能正常打开，跳转后 URL 上才会带出 xsec_token。

流程（拟人路径）：
  打开主页 → 滚到底收集卡片 → 逐张点击 → 读 __INITIAL_STATE__ 里 noteDetailMap
  → 回写 content + xhs_note（含 token）→ 返回列表

用法：
  python3 crawler/crawl_creator_click.py --cid 5c4a73a000000000110201d1 --brand MitoQ --limit 50
  python3 crawler/crawl_creator_click.py --from-db --brand MitoQ --limit 200   # 只补缺正文的
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "xhs_user_data_dir")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
TOKEN_JSON = os.path.join(ROOT, "data", "creator_tokens.json")
RAW_DIR = os.path.join(ROOT, "data", "xhs_archive", "raw")
MEDIA_ROOT = os.path.join(ROOT, "knowledge", "media")

JS_CARDS = r"""
() => Array.from(document.querySelectorAll("a[href*='/explore/']")).map(a => {
  const m = (a.getAttribute('href')||'').match(/\/explore\/([0-9a-f]{16,32})/);
  const card = a.closest('section') || a.parentElement;
  const t = card ? card.querySelector('span.note-item-title, .title span, a.title span') : null;
  return { nid: m ? m[1] : null, title: t ? t.innerText.trim() : (a.getAttribute('title')||'') };
}).filter(x => x.nid);
"""

JS_DETAIL = r"""
() => {
  const out = {};
  try {
    const st = window.__INITIAL_STATE__;
    const dm = st && st.note && st.note.noteDetailMap;
    if (dm) {
      const k = Object.keys(dm)[0];
      const n = k && dm[k] && dm[k].note;
      if (n) {
        out.title = n.title || '';
        out.desc = n.desc || '';
        out.time = n.time || null;
        out.ip = n.ipLocation || '';
        out.type = n.type || '';
        const ii = n.interactInfo || {};
        out.liked = ii.likedCount; out.collected = ii.collectedCount;
        out.comment = ii.commentCount; out.share = ii.shareCount;
        out.user = n.user ? { nickname: n.user.nickname, user_id: n.user.userId } : {};
        out.images = (n.imageList || []).map(i => i.urlDefault || i.urlPre ||
              (i.infoList && i.infoList[0] && i.infoList[0].url) || '').filter(Boolean);
        out.tags = (n.tagList || []).map(t => t.name).filter(Boolean);
        let v = '';
        try {
          const s = n.video && n.video.media && n.video.media.video && n.video.media.video.streams;
          if (s) for (const kk of ['h264','av1','h265']) {
            if (s[kk] && s[kk].length) { v = s[kk][0].masterUrl || (s[kk][0].backupUrls||[])[0] || ''; if (v) break; }
          }
        } catch(e) {}
        out.video = v;
        return out;
      }
    }
  } catch(e) { out.err = String(e); }
  out.desc = (document.querySelector('#detail-desc')?.innerText || '').trim();
  out.title = (document.querySelector('#detail-title')?.innerText || '').trim();
  return out;
}
"""


def pending_by_creator(brand, limit):
    con = sqlite3.connect(DB)
    q = ("SELECT text_hash, url, title, author_id, author FROM content "
         "WHERE platform='xhs' AND (body IS NULL OR body='') ")
    p = []
    if brand:
        q += " AND brand IN (%s) " % ",".join("?" * len(brand.split(",")))
        p += [b.strip() for b in brand.split(",")]
    q += " ORDER BY id DESC"
    rows = con.execute(q, p).fetchall()
    con.close()
    from collections import OrderedDict
    g = OrderedDict()
    for th, u, t, aid, a in rows:
        if not aid:
            continue
        g.setdefault(aid, {"author": a, "items": []})["items"].append((th, t))
    out = []
    for aid, d in g.items():
        out.append({"cid": aid, "nick": d["author"], "items": d["items"][:limit]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", type=str, default="")
    ap.add_argument("--brand", type=str, default="")
    ap.add_argument("--from-db", action="store_true", help="从库里挑缺正文的笔记，按其作者分组点击")
    ap.add_argument("--limit", type=int, default=60, help="每个账号最多点多少条")
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--max-scroll", type=int, default=40)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    tokens = json.load(open(TOKEN_JSON, encoding="utf-8")) if os.path.exists(TOKEN_JSON) else {}

    targets = []
    if args.cid:
        brands = args.brand.split(",") if args.brand else [""]
        targets = [{"cid": args.cid, "brand": brands[0], "nick": "", "items": None}]
    elif args.from_db:
        for t in pending_by_creator(args.brand, args.limit):
            if t["cid"] in tokens:
                t["brand"] = (args.brand.split(",")[0] if args.brand else "")
                targets.append(t)
    if not targets:
        print("没有目标账号（需要 creator_tokens.json 里有对应 token）")
        return
    print(f"目标账号 {len(targets)} 个", flush=True)

    from playwright.sync_api import sync_playwright
    con = sqlite3.connect(DB)
    ok = miss = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=not args.show, channel="chrome", locale="zh-CN",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        for t in targets:
            cid, nick = t["cid"], t["nick"] or ""
            tok = tokens.get(cid, "")
            if not tok:
                continue
            need = {x[0].split(":")[1] for x in t["items"]} if t["items"] else None
            url = (f"https://www.xiaohongshu.com/user/profile/{cid}"
                   f"?xsec_token={tok}&xsec_source=pc_search")
            pg = ctx.new_page()
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3500)
                if not nick:
                    try:
                        nick = pg.evaluate("()=>document.querySelector('.user-name')?.innerText||''")
                    except Exception:
                        pass
                # 滚到底，把卡片全部加载出来
                seen = {}
                for _ in range(args.max_scroll):
                    cards = pg.evaluate(JS_CARDS)
                    for c in cards:
                        seen.setdefault(c["nid"], c["title"])
                    pg.mouse.wheel(0, 2600)
                    pg.wait_for_timeout(1200)
                    if need is not None and need.issubset(seen.keys()):
                        break
                order = [n for n in seen if (need is None or n in need)]
                order = order[:args.limit]
                print(f"  账号 {nick or cid}: 卡片 {len(seen)} 条，本轮点 {len(order)} 条", flush=True)

                for i, nid in enumerate(order, 1):
                    try:
                        sel = f"a[href*='/explore/{nid}']"
                        el = pg.query_selector(sel)
                        if not el:
                            miss += 1
                            continue
                        el.scroll_into_view_if_needed()
                        pg.wait_for_timeout(300)
                        try:
                            with ctx.expect_page(timeout=4500) as pinfo:
                                el.click()
                            np = pinfo.value
                            np.wait_for_load_state("domcontentloaded")
                            np.wait_for_timeout(2200)
                            d = np.evaluate(JS_DETAIL)
                            cur_url = np.url
                            np.close()
                        except Exception:
                            el.click()
                            pg.wait_for_timeout(2600)
                            d = pg.evaluate(JS_DETAIL)
                            cur_url = pg.url
                            pg.go_back(wait_until="domcontentloaded")
                            pg.wait_for_timeout(1500)
                    except Exception as e:
                        miss += 1
                        print(f"    [{i}] 点击失败 {type(e).__name__}", flush=True)
                        continue

                    desc = (d.get("desc") or "").strip()
                    if not desc and not d.get("images"):
                        miss += 1
                        print(f"    [{i}] {nid} 空详情", flush=True)
                        time.sleep(args.sleep)
                        continue

                    ok += 1
                    imgs = [u for u in (d.get("images") or []) if u]
                    vurl = d.get("video") or ""
                    tags = d.get("tags") or []
                    ts = d.get("time")
                    if ts and len(str(ts)) > 10:
                        ts = int(str(ts)[:10])
                    pub = (datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                           if ts else None)
                    nm = (d.get("user") or {}).get("nickname") or nick
                    uid = (d.get("user") or {}).get("user_id") or cid
                    metrics = {"likes": d.get("liked"), "collects": d.get("collected"),
                               "comments": d.get("comment"), "shares": d.get("share")}
                    m_tok = re.search(r"[?&]xsec_token=([^&]+)", cur_url or "")
                    xtok = m_tok.group(1) if m_tok else ""

                    with open(os.path.join(RAW_DIR, f"{nid}.json"), "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=1)

                    mj = {}
                    if imgs:
                        mj["images"] = imgs
                    if vurl:
                        mj["video"] = vurl
                    th = f"xhs:{nid}"
                    con.execute(
                        """UPDATE content SET body=?, title=COALESCE(NULLIF(title,''),?),
                             media_json=?, tags=COALESCE(NULLIF(tags,''),?),
                             metrics_json=?, publish_time=COALESCE(publish_time,?),
                             author=COALESCE(NULLIF(author,''),?),
                             author_id=COALESCE(NULLIF(author_id,''),?)
                           WHERE text_hash=?""",
                        (desc, d.get("title"), json.dumps(mj, ensure_ascii=False) if mj else None,
                         ",".join(tags), json.dumps(metrics, ensure_ascii=False),
                         pub, nm, uid, th))
                    con.execute(
                        """INSERT INTO xhs_note (note_id,title,body,publish_time,author,author_id,
                                images_json,tags_json,likes,collects,comments,shares,brand,note_url,xsec_token,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(note_id) DO UPDATE SET
                             body=CASE WHEN IFNULL(excluded.body,'')<>'' THEN excluded.body ELSE body END,
                             publish_time=COALESCE(publish_time, excluded.publish_time),
                             xsec_token=CASE WHEN IFNULL(excluded.xsec_token,'')<>'' THEN excluded.xsec_token
                                             ELSE xsec_token END,
                             images_json=CASE WHEN IFNULL(excluded.images_json,'[]') NOT IN ('','[]')
                                              THEN excluded.images_json ELSE images_json END,
                             updated_at=excluded.updated_at""",
                        (nid, d.get("title"), desc, pub, nm, uid,
                         json.dumps(imgs, ensure_ascii=False), json.dumps(tags, ensure_ascii=False),
                         metrics["likes"] or 0, metrics["collects"] or 0,
                         metrics["comments"] or 0, metrics["shares"] or 0,
                         t.get("brand") or "", cur_url or "", xtok,
                         datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
                    con.commit()
                    print(f"    [{i}/{len(order)}] ✅ {nid} 正文{len(desc)} 图{len(imgs)} "
                          f"视频{1 if vurl else 0} token{1 if xtok else 0}", flush=True)
                    time.sleep(args.sleep)
            except Exception as e:
                print(f"  ! {nick or cid}: {type(e).__name__}: {e}", flush=True)
            finally:
                try:
                    pg.close()
                except Exception:
                    pass
        con.commit()
        con.close()
        ctx.close()
    print(f"=== 结束：成功 {ok}，跳过/失败 {miss} ===", flush=True)


if __name__ == "__main__":
    main()
