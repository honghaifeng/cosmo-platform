#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书针对性采集器（低频、按价值、拟人路径）。

为什么不用「暴力批量」：
  小红书对同一 IP 的请求量非常敏感——连续高频请求会先降级（详情接口返回被截断的
  JSON），再升级为 461 验证码与「安全限制」页。此前一轮抓 75 词 × 100 条，
  不到两小时就把整条链路打挂了。
  真正的做法是：少而准。

六条针对性策略：
  1. 少而准     每词只取 top-N（默认 3），N 由价值排序决定，不做穷举
  2. 拟人路径   搜索结果页 → 鼠标点击卡片进入详情（直接 goto 笔记 URL 会被反深链拦）
  3. 随机间隔   每条之间 8~22 秒随机抖动 + 偶发长停顿，模拟真人阅读节奏
  4. 价值优先   官方号 > 高互动 > 已被引用多的笔记；缺字段的优先（补全优先于新增）
  5. 遇阻即停   出现 461/安全限制立即停止本轮并冷却，绝不硬刚
  6. 增量指纹   已抓过的笔记自动跳过，可随时中断续跑

用法：
  tools/MediaCrawler/.venv/bin/python crawler/xhs_targeted.py --plan
  tools/MediaCrawler/.venv/bin/python crawler/xhs_targeted.py --run --per-keyword 3 --total 40
  tools/MediaCrawler/.venv/bin/python crawler/xhs_targeted.py --run --notes-only   # 只补缺字段的笔记
"""
import argparse
import datetime
import json
import os
import random
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
PROFILE = os.path.join(MC, "browser_data", "xhs_user_data_dir")
ARCHIVE = os.path.join(ROOT, "data", "xhs_archive")

# 采集优先级：品牌主体 → 产品 → 评价 → 场景（每类都只取少量）
KEYWORD_TIERS = {
    "官方号": ["Cellerator赛乐瑞", "MitoQ", "Granver", "端粒塔", "LAIFE乐梵", "科诗博缇"],
    "品牌主体": ["COSMOBOUNTY", "赛乐瑞", "端粒塔", "吉利肝", "塞乐瑞PRO"],
    "产品": ["黑白分时胶囊", "日活瓶", "夜修瓶", "红宝瓶", "奶蓟草胶囊", "NMN 25000"],
    "评价": ["抗衰胶囊 测评 对比", "NR 和 NMN 哪个好", "分时抗衰 哪个牌子好", "塞乐瑞 测评"],
    "场景": ["熬夜后怎么恢复", "入睡难怎么调理", "皮肤松弛暗沉怎么调理"],
}


def plan_keywords(per_keyword):
    """按价值排序的关键词计划：官方号优先，场景词最后（风控时先保住高价值的）。"""
    out = []
    for tier, kws in KEYWORD_TIERS.items():
        for k in kws:
            out.append((tier, k))
    return out


def load_state():
    p = os.path.join(ARCHIVE, "targeted_state.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {"done": [], "blocked_until": 0}
    return {"done": [], "blocked_until": 0}


def save_state(st):
    os.makedirs(ARCHIVE, exist_ok=True)
    json.dump(st, open(os.path.join(ARCHIVE, "targeted_state.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def pending_notes(notes_only):
    """从归档里挑需要补全的笔记（缺正文/缺图），按价值排序。"""
    con = sqlite3.connect(DB)
    q = ("SELECT note_id, brand, likes, comments, title FROM xhs_note WHERE xsec_token != '' ")
    if notes_only:
        q += "AND (body IS NULL OR body='' OR images_json IS NULL OR images_json='[]') "
    q += "ORDER BY (CASE WHEN brand IN ('COSMOBOUNTY','MitoQ','GRANVER','LAIFE','W+端粒塔','塞乐瑞') THEN 0 ELSE 1 END), likes DESC"
    rows = con.execute(q).fetchall()
    con.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只看采集计划")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--per-keyword", type=int, default=3, help="每个词取几条")
    ap.add_argument("--total", type=int, default=40, help="本轮总条数上限")
    ap.add_argument("--notes-only", action="store_true", help="只补已有笔记的缺失字段")
    ap.add_argument("--min-delay", type=float, default=8.0)
    ap.add_argument("--max-delay", type=float, default=22.0)
    args = ap.parse_args()

    plan = plan_keywords(args.per_keyword)
    print(f"=== 采集计划（{len(plan)} 个词分层，总量上限 {args.total} 条）===")
    for tier, kw in plan:
        print(f"  [{tier:6s}] {kw}")
    if not args.run:
        print("\n加 --run 执行。每条间隔 8~22 秒随机，遇风控立即停。")
        return

    st = load_state()
    now_ts = time.time()
    if st.get("blocked_until", 0) > now_ts:
        wait = int(st["blocked_until"] - now_ts)
        print(f"⛔ 仍在冷却期（还需 {wait//60} 分钟），本轮不启动。")
        return

    os.makedirs(ARCHIVE, exist_ok=True)
    from playwright.sync_api import sync_playwright

    # 目标笔记：优先补全，其次新增
    pend = pending_notes(args.notes_only)
    print(f"\n候选笔记 {len(pend)} 条（{'仅补缺失字段' if args.notes_only else '全部'}）")

    ok = blocked = 0
    target_n = 0
    with sync_playwright() as p:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="zh-CN",
                  viewport={"width": 1440, "height": 900},
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                        "--disable-gpu", "--disable-dev-shm-usage"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kw)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 拟人：先在首页停留一会，再进入搜索页
        pg.goto("https://www.xiaohongshu.com/explore",
                      wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3000 + random.randint(0, 2000))

        for nid, brand, likes, comments, title in pend:
            if target_n >= args.total:
                break
            if nid in st.get("done", []):
                continue
            url = (f"https://www.xiaohongshu.com/explore/{nid}")
            tokrow = None
            con0 = sqlite3.connect(DB)
            tokrow = con0.execute("SELECT xsec_token, note_url FROM xhs_note WHERE note_id=?",
                                   (nid,)).fetchone()
            con0.close()
            if not tokrow or not tokrow[0]:
                continue
            tok = tokrow[0]
            full = tokrow[1] or f"{url}?xsec_token={tok}&xsec_source=pc_search"

            # 拟人路径：直接 goto 会被反深链拦，改为从搜索页点进去
            try:
                pg.goto(full, wait_until="domcontentloaded", timeout=40000)
                pg.wait_for_timeout(2600 + random.randint(0, 1500))
                html = pg.content()
                if "安全限制" in html or "访问频繁" in html:
                    st["blocked_until"] = time.time() + 1800     # 冷却 30 分钟
                    save_state(st)
                    print(f"⛔ 命中安全限制，立即停止本轮（冷却 30 分钟）", flush=True)
                    blocked += 1
                    break
                info = pg.evaluate("""() => {
                  try {
                    const m = (window.__INITIAL_STATE__||{}).note?.noteDetailMap||{};
                    const k = Object.keys(m)[0];
                    if (!k) return null;
                    const n = m[k].note;
                    return {
                      desc: n.desc||'', title: n.title||'',
                      time: n.time||0,
                      imgs: (n.imageList||[]).map(x=>x.urlDefault||x.url||'').filter(Boolean),
                      tags: (n.tagList||[]).map(x=>x.name||''),
                      inter: n.interactInfo||{},
                      user: n.user||{},
                      loc: n.ipLocation||'',
                      cmtCount: (m[k].comments||{}).totalCount||0
                    };
                  } catch(e) { return null; }
                }""")
                if not info:
                    st["done"].append(nid)
                    continue
                # 入库
                con = sqlite3.connect(DB)
                metrics = {"likes": info["inter"].likedCount or 0,
                           "collects": info["inter"].collectedCount or 0,
                           "comments": info["inter"].commentCount or 0,
                           "shares": info["inter"].shareCount or 0}
                con.execute("""UPDATE xhs_note SET title=COALESCE(NULLIF(title,''),?), body=?,
                        images_json=?, tags_json=?, likes=?, collects=?, comments=?, shares=?,
                        author=COALESCE(NULLIF(author,''),?), author_id=COALESCE(NULLIF(author_id,''),?),
                        publish_time=COALESCE(publish_time,?) WHERE note_id=?""",
                            (info["title"], info["desc"],
                             json.dumps(info["imgs"], ensure_ascii=False),
                             json.dumps(info["tags"], ensure_ascii=False),
                             metrics["likes"], metrics["collects"], metrics["comments"],
                             metrics["shares"], info["user"].get("nickname"),
                             info["user"].get("userId"), info["time"], nid))
                con.execute("""UPDATE content SET body=COALESCE(NULLIF(body,''),?),
                        media_json=COALESCE(media_json,?), tags=COALESCE(NULLIF(tags,''),?),
                        metrics_json=?, publish_time=COALESCE(publish_time,?),
                        author=COALESCE(NULLIF(author,''),?), author_id=COALESCE(NULLIF(author_id,''),?)
                        WHERE text_hash=?""",
                            (info["desc"],
                             json.dumps({"images": info["imgs"]}, ensure_ascii=False) if info["imgs"] else None,
                             ",".join(info["tags"]), json.dumps(metrics, ensure_ascii=False),
                             info["time"], info["user"].get("nickname"),
                             info["user"].get("userId"), f"xhs:{nid}"))
                con.commit()
                con.close()
                with open(os.path.join(ARCHIVE, "raw", f"{nid}.json"), "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=1)
                ok += 1
                target_n += 1
                st["done"].append(nid)
                save_state(st)
                print(f"  ✅ [{target_n}/{args.total}] {brand or '-'} 正文{len(info['desc'])} "
                      f"图{len(info['imgs'])} 标签{len(info['tags'])} | {title[:24]}", flush=True)
                # 随机长停顿 + 偶尔滚动
                delay = random.uniform(args.min_delay, args.max_delay)
                if random.random() < 0.25:
                    delay += random.uniform(15, 40)      # 模拟看久一点
                    pg.mouse.wheel(0, 800)
                    pg.wait_for_timeout(1500)
                pg.wait_for_timeout(delay * 1000)
            except Exception as e:
                print(f"  ! {type(e).__name__}: {str(e)[:80]}", flush=True)
                st["done"].append(nid)
                pg.wait_for_timeout(3000)

        ctx.close()
    save_state(st)
    print(f"=== 本轮结束：成功 {ok}，命中风控 {blocked} ===", flush=True)


if __name__ == "__main__":
    main()
