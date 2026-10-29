#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书「搜索 → 详情」闭环补全。

解决的核心问题：
  主页卡片的 href 不带 xsec_token，直接开 /explore/<id> 会被重定向到 404
  （error_code=300031「当前笔记暂时无法浏览」）。而搜索接口返回的每条笔记
  都带新鲜的 xsec_token —— 这是唯一稳定可用的入口。

流程（每个关键词）：
  search(page 1..N) → 拿 note_id + xsec_token + 卡片信息
    → 库里已有正文的直接跳过（省请求）
    → 缺正文的立刻 get_note_by_id 补全（token 此刻最新，成功率最高）
    → 回写 content + xhs_note

用法：
  tools/MediaCrawler/.venv/bin/python crawler/xhs_search_fill.py --kw "MitoQ,W+端粒塔" --pages 3
  tools/MediaCrawler/.venv/bin/python crawler/xhs_search_fill.py --kw-file config/kw.txt --pages 5 --max-detail 200
"""
import argparse
import asyncio
import datetime
import json
import os
import random
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW_DIR = os.path.join(ROOT, "data", "xhs_archive", "raw")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")
STATE = os.path.join(ROOT, "data", "xhs_search_fill_state.json")

sys.path.insert(0, MC)

BRANDS = ["COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"]


def brand_of(text):
    t = (text or "").lower()
    m = {
        "COSMOBOUNTY": ["cosmobounty", "cosmo bounty", "科诗博缇", "黑白分时"],
        "MitoQ": ["mitoq", "mito q", "线粒体素"],
        "GRANVER": ["granver", "吉力威", "吉清肺", "吉利肝", "吉瑞维"],
        "LAIFE": ["laife", "乐梵", "乐悦泰", "乐轻肝", "红宝瓶", "乐维源", "乐净肺"],
        "W+端粒塔": ["端粒塔", "w+", "duanlta", "nmn 25000"],
        "塞乐瑞": ["塞乐瑞", "赛乐瑞", "cellerator", "celerui", "stemax", "莱复隆"],
    }
    for b, ks in m.items():
        if any(k in t for k in ks):
            return b
    return ""


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {"done_kw": {}, "done_note": []}


def save_state(st):
    st["done_note"] = st["done_note"][-20000:]
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", type=str, default="")
    ap.add_argument("--kw-file", type=str, default="")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--max-detail", type=int, default=150, help="本轮最多补多少条详情")
    ap.add_argument("--search-sleep", type=float, default=7.0)
    ap.add_argument("--detail-sleep", type=float, default=3.5)
    args = ap.parse_args()

    kws = [k.strip() for k in args.kw.split(",") if k.strip()]
    if args.kw_file and os.path.exists(args.kw_file):
        kws += [l.strip() for l in open(args.kw_file, encoding="utf-8") if l.strip()]
    if not kws:
        print("需要 --kw 或 --kw-file")
        return

    st = load_state()
    os.makedirs(RAW_DIR, exist_ok=True)

    import config
    config.KEYWORDS = ",".join(kws)
    config.CRAWLER_MAX_NOTES_COUNT = 20
    config.CRAWLER_TYPE = "search"
    config.ENABLE_GET_COMMENTS = False
    from playwright.async_api import async_playwright
    from media_platform.xhs.client import XiaoHongShuClient

    con = sqlite3.connect(DB)
    have = {r[0] for r in con.execute(
        "SELECT text_hash FROM content WHERE platform='xhs' AND body IS NOT NULL AND body<>''")}
    have |= {f"xhs:{r[0]}" for r in con.execute(
        "SELECT note_id FROM xhs_note WHERE body IS NOT NULL AND body<>''")}

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=True, locale="zh-CN", channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-gpu", "--disable-dev-shm-usage"])
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await pg.goto("https://www.xiaohongshu.com/explore",
                          wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(2500)
        except Exception:
            pass
        cookies = await ctx.cookies()
        cd = {c["name"]: c["value"] for c in cookies}
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in cd.items()),
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
            "Origin": "https://www.xiaohongshu.com",
            "Referer": "https://www.xiaohongshu.com/",
            "Content-Type": "application/json;charset=UTF-8",
        }
        client = XiaoHongShuClient(headers=headers, playwright_page=pg, cookie_dict=cd)

        total_detail = total_new = blocked = 0
        detail_fail = 0
        for kw in kws:
            if total_detail >= args.max_detail or blocked >= 6:
                break
            for page in range(1, args.pages + 1):
                if total_detail >= args.max_detail or blocked >= 6:
                    break
                try:
                    res = await client.get_note_by_keyword(kw, page=page, page_size=20)
                except Exception as e:
                    blocked += 1
                    print(f"  ⛔ 搜索「{kw}」p{page} 失败 {type(e).__name__}", flush=True)
                    await asyncio.sleep(min(90, 15 * blocked))
                    continue
                items = []
                for it in (res.get("items") or []):
                    nc = it.get("note_card") or {}
                    nid = it.get("id")
                    if not nid:
                        continue
                    items.append({
                        "nid": nid,
                        "tok": it.get("xsec_token") or "",
                        "title": nc.get("display_title") or "",
                        "likes": (nc.get("interact_info") or {}).get("liked_count") or "",
                        "author": (nc.get("user") or {}).get("nickname") or "",
                        "uid": (nc.get("user") or {}).get("user_id") or "",
                    })
                print(f"  搜索「{kw}」p{page}: {len(items)} 条", flush=True)
                for it in items:
                    if total_detail >= args.max_detail:
                        break
                    nid, tok = it["nid"], it["tok"]
                    th = f"xhs:{nid}"
                    brand = brand_of(it["title"] + " " + kw)
                    if th in have:
                        # 已有正文：只补 token，便于以后复查
                        if tok:
                            con.execute(
                                """UPDATE xhs_note SET xsec_token=?, brand=CASE WHEN IFNULL(brand,'')='' THEN ? ELSE brand END
                                   WHERE note_id=?""", (tok, brand, nid))
                            con.commit()
                        continue
                    if not tok:
                        continue
                    try:
                        d = await client.get_note_by_id(nid, "pc_search", tok)
                        detail_fail = 0
                    except Exception as e:
                        blocked += 1
                        detail_fail += 1
                        msg = str(e)[:120]
                        print(f"    ! 详情失败({detail_fail}) {type(e).__name__} {msg}", flush=True)
                        if detail_fail >= 5:
                            print("    ⛔ 详情接口连续失败，本轮停止（冷却后重跑）", flush=True)
                            con.commit()
                            con.close()
                            await ctx.close()
                            save_state(st)
                            print(f"=== 中断：已补 {total_detail} 条，风控 {blocked} ===", flush=True)
                            return
                        await asyncio.sleep(min(120, 20 * blocked))
                        continue
                    blocked = 0
                    desc = (d.get("desc") or "").strip()
                    if not desc and not d.get("image_list"):
                        await asyncio.sleep(args.detail_sleep)
                        continue
                    imgs = d.get("image_list") or []
                    imgs = [x if isinstance(x, str) else (x.get("url_default") or x.get("url") or "")
                            for x in imgs]
                    imgs = [u for u in imgs if u]
                    vurl = ""
                    try:
                        from media_platform.xhs.media import extract_video_urls
                        vus = extract_video_urls(d)
                        vurl = vus[0] if vus else ""
                    except Exception:
                        vurl = ""
                    tags = [t.get("name") if isinstance(t, dict) else str(t)
                            for t in (d.get("tag_list") or [])]
                    # 互动数在详情对象的 interact_info 里，不是平铺字段。
                    # 早先按 d["liked_count"] 取，取到的是 None，导致表里
                    # 赞/藏/评/转发 全显示 0。这里兼容两种结构，并把
                    # "1.2万" 这类文本转成整数。
                    def _num(v):
                        if v is None or v == "":
                            return 0
                        if isinstance(v, (int, float)):
                            return int(v)
                        s = str(v).strip().replace(",", "")
                        try:
                            if "万" in s:
                                return int(float(s.replace("万", "")) * 10000)
                            return int(float(s))
                        except (ValueError, TypeError):
                            return 0

                    ii = d.get("interact_info") or {}
                    metrics = {
                        "likes": _num(ii.get("liked_count", d.get("liked_count"))),
                        "collects": _num(ii.get("collected_count", d.get("collected_count"))),
                        "comments": _num(ii.get("comment_count", d.get("comment_count"))),
                        "shares": _num(ii.get("share_count", d.get("share_count"))),
                    }
                    user = d.get("user") or {}
                    ts = d.get("time")
                    if ts and len(str(ts)) > 10:
                        ts = int(str(ts)[:10])
                    pub = (datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                           if ts else None)
                    b2 = brand_of(desc[:1500] + " " + it["title"]) or brand
                    # 小红书很多笔记没有独立标题（正文即标题），title 会返回空串。
                    # 表里出现空标题看起来像抓漏了，这里用正文首行兜底。
                    title = d.get("title") or it["title"]
                    if not (title or "").strip():
                        for _ln in (desc or "").splitlines():
                            if len(_ln.strip()) >= 4:
                                title = _ln.strip()[:40]
                                break
                    url = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={tok}&xsec_source=pc_search"
                    with open(os.path.join(RAW_DIR, f"{nid}.json"), "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=1)
                    mj = {}
                    if imgs:
                        mj["images"] = imgs
                    if vurl:
                        mj["video"] = vurl
                    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    cur = con.execute(
                        """INSERT OR IGNORE INTO content
                           (brand, platform, content_type, title, body, author, author_id,
                            publish_time, collect_time, url, metrics_json, tags, raw_path, text_hash, media_json)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (b2 or "品类场景词", "xhs", "note", title, desc,
                         user.get("nickname") or it["author"], user.get("user_id") or it["uid"],
                         pub, now, url, json.dumps(metrics, ensure_ascii=False),
                         ",".join(tags), "xhs_search_fill", th,
                         json.dumps(mj, ensure_ascii=False) if mj else None))
                    if cur.rowcount:
                        total_new += 1
                    else:
                        con.execute(
                            """UPDATE content SET body=COALESCE(NULLIF(body,''),?),
                                 media_json=COALESCE(media_json,?),
                                 publish_time=COALESCE(publish_time,?), metrics_json=?,
                                 tags=COALESCE(NULLIF(tags,''),?) WHERE text_hash=?""",
                            (desc, json.dumps(mj, ensure_ascii=False) if mj else None,
                             pub, json.dumps(metrics, ensure_ascii=False), ",".join(tags), th))
                    con.execute(
                        """INSERT INTO xhs_note (note_id,title,body,publish_time,author,author_id,
                                images_json,tags_json,likes,collects,comments,shares,brand,note_url,xsec_token,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(note_id) DO UPDATE SET
                             body=CASE WHEN IFNULL(excluded.body,'')<>'' THEN excluded.body ELSE body END,
                             images_json=CASE WHEN IFNULL(excluded.images_json,'[]') NOT IN ('','[]')
                                              THEN excluded.images_json ELSE images_json END,
                             xsec_token=excluded.xsec_token,
                             publish_time=COALESCE(publish_time, excluded.publish_time),
                             updated_at=excluded.updated_at""",
                        (nid, title, desc, pub, user.get("nickname") or it["author"],
                         user.get("user_id") or it["uid"],
                         json.dumps(imgs, ensure_ascii=False), json.dumps(tags, ensure_ascii=False),
                         metrics["likes"] or 0, metrics["collects"] or 0,
                         metrics["comments"] or 0, metrics["shares"] or 0,
                         b2, url, tok, now))
                    con.commit()
                    have.add(th)
                    total_detail += 1
                    print(f"    [{total_detail}/{args.max_detail}] {nid} {b2 or '-'} "
                          f"正文{len(desc)} 图{len(imgs)} 视频{1 if vurl else 0} | {title[:22]}", flush=True)
                    await asyncio.sleep(args.detail_sleep + random.uniform(0, 1.5))
                st["done_kw"][kw] = page
                save_state(st)
                await asyncio.sleep(args.search_sleep + random.uniform(0, 3))
        con.commit()
        con.close()
        await ctx.close()
    print(f"=== 结束：新增/补全 {total_detail} 条（其中新增内容 {total_new} 条），风控 {blocked} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
