#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据资产诊断 —— 回答一个问题：往哪投入，才能让数据库里有「更多且更准确」的数据？

不新增任何数据，只盘存量与缺口，给出可执行的优先级排序。

用法：
  tools/MediaCrawler/.venv/bin/python crawler/diagnose_data.py
"""
import json
import os
import sqlite3
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
MEDIA = os.path.join(ROOT, "data", "xhs_archive", "media")


def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    print("=" * 74)
    print("① content 表：各平台存量与缺口")
    print("=" * 74)
    print(f"  {'平台':<11}{'总数':>7}{'已校验':>8}{'缺时间':>8}{'正文<220':>10}{'正文空':>8}{'可展示率':>9}")
    for r in q(con, """SELECT platform, COUNT(*) t, SUM(verified=1) v,
                              SUM(publish_time IS NULL OR publish_time='') nt,
                              SUM(LENGTH(COALESCE(body,''))<220) sb,
                              SUM(COALESCE(body,'')='') eb
                       FROM content GROUP BY platform ORDER BY t DESC"""):
        rate = f"{100*r['v']/max(r['t'],1):.0f}%"
        print(f"  {r['platform']:<11}{r['t']:>7}{r['v']:>8}{r['nt']:>8}{r['sb']:>10}{r['eb']:>8}{rate:>9}")

    print()
    print("=" * 74)
    print("② xhs_note 表：3592 条笔记到底卡在哪")
    print("=" * 74)
    print("  按 verify_note 归因（未通过的原因）：")
    for r in q(con, """SELECT COALESCE(verify_note,'(未打标)') reason, COUNT(*) n
                       FROM xhs_note WHERE verified=0 GROUP BY reason ORDER BY n DESC LIMIT 12"""):
        print(f"    {r['reason']:<26}{r['n']:>6}")

    print()
    print("  关键拆分：未归属 vs 已归属但不合格")
    r = q(con, """SELECT
                  SUM(brand_mention=0)                                   AS not_claimed,
                  SUM(brand_mention=1 AND verified=0)                     AS claimed_but_fail,
                  SUM(brand_mention=1 AND verified=1)                     AS claimed_ok,
                  SUM(brand_mention=1 AND LENGTH(COALESCE(desc,''))=0)    AS claimed_no_desc,
                  COUNT(*)                                                AS total
                  FROM xhs_note""")[0]
    print(f"    未归属品牌（不是该品牌内容，不该进品牌库）：{r['not_claimed']}")
    print(f"    已归属但未过校验（可攻破）：            {r['claimed_but_fail']}")
    print(f"    其中正文为空（补正文即可解锁）：        {r['claimed_no_desc']}")
    print(f"    已归属且过校验（当前可用）：            {r['claimed_ok']}")
    print(f"    合计：                                {r['total']}")

    print()
    print("  已归属但未过校验的明细（缺什么）：")
    for r in q(con, """SELECT COALESCE(verify_note,'(未打标)') reason, COUNT(*) n
                       FROM xhs_note WHERE brand_mention=1 AND verified=0
                       GROUP BY reason ORDER BY n DESC LIMIT 10"""):
        print(f"    {r['reason']:<26}{r['n']:>6}")

    print()
    print("=" * 74)
    print("③ 媒体与解读：视频/图片是否落盘、OCR/ASR 是否做了")
    print("=" * 74)
    mp4 = jpg = 0
    if os.path.isdir(MEDIA):
        for f in os.listdir(MEDIA):
            if f.endswith(".mp4"):
                mp4 += 1
            elif f.endswith((".jpg", ".jpeg", ".png", ".webp")):
                jpg += 1
    print(f"  本地视频 {mp4} 个 / 本地图片 {jpg} 张")
    for r in q(con, "SELECT kind, COUNT(*) n, COUNT(DISTINCT content_id) c FROM media_text GROUP BY kind"):
        print(f"  media_text {r['kind']}: {r['n']} 条 / 覆盖 {r['c']} 个内容")
    covered = q(con, "SELECT COUNT(DISTINCT content_id) FROM media_text")[0][0]
    print(f"  有解读的内容数：{covered}")

    print()
    print("=" * 74)
    print("④ 六品牌 × 平台覆盖（已校验口径）")
    print("=" * 74)
    print(f"  {'品牌':<16}{'百度':>6}{'头条':>6}{'web':>6}{'小红书':>8}{'抖音':>6}{'合计':>7}")
    brands = [r["brand"] for r in q(con, "SELECT DISTINCT brand FROM content ORDER BY brand")]
    for b in brands:
        cells = {}
        for p in ("baidu", "toutiao", "web", "xhs", "douyin"):
            cells[p] = q(con, "SELECT COUNT(*) FROM content WHERE brand=? AND platform=? AND verified=1",
                         (b, p))[0][0]
        tot = sum(cells.values())
        print(f"  {b:<16}{cells['baidu']:>6}{cells['toutiao']:>6}{cells['web']:>6}"
              f"{cells['xhs']:>8}{cells['douyin']:>6}{tot:>7}")
    print("  小红书口径为 content 表（非 xhs_note 归档表）")

    print()
    print("=" * 74)
    print("⑤ 百度数据的「性质」分布 —— 决定它能不能用于横评")
    print("=" * 74)
    pr_rx = ("十大|排行榜|榜单|测评榜|横评|权威|公认|首选|哪个好|哪个牌子|"
             "值得信|脱颖而出|登顶|领先|推荐榜|排行榜")
    import re
    prx = re.compile(prx_rx)
    rows = q(con, """SELECT id, title, body, brand, publish_time FROM content
                     WHERE platform IN ('baidu','toutiao','web')""")
    bucket = collections.Counter()
    for r in rows:
        t = (r["title"] or "") + " " + (r["body"] or "")[:200]
        if prx.search(t):
            bucket["榜单/横评型（多为PR软文）"] += 1
        elif re.search(r"(京东|天猫|淘宝|商品|旗舰店|价格|多少钱|优惠)", t):
            bucket["商品/电商页"] += 1
        elif re.search(r"(百科|词条|官网|品牌故事|产品系列)", t):
            bucket["百科/官网（品牌自述）"] += 1
        else:
            bucket["其他（疑似真实测评/资讯）"] += 1
    for k, v in bucket.most_common():
        print(f"    {k:<28}{v:>5}")
    print()
    print("  判定：榜单/横评型内容若混入库，六品牌会「人人都是第一名」——")
    print("        因为每个品牌都单独发过一篇把自己排第一的稿。这类数据可用于")
    print("        分析「投放行为」，但绝不能直接当「用户口碑/品牌强弱」对比。")

    print()
    print("=" * 74)
    print("⑥ 投入产出排序（同一份投入，能多换多少条可用数据）")
    print("=" * 74)
    items = [
        ("小红书补正文（3237 条未过闸里已归属的部分）",
         r["claimed_no_desc"], "抓笔记页取正文，1 条 = 1 条可用内容", "高"),
        ("百度库内补时间（已补 45 条，剩 35 条）",
         35, "多为商品页/百科/列表页，结构性无时间", "低"),
        ("百度新抓 SERP（每轮 60~120 条）",
         0, "多数是榜单软文，且需再分类", "中（做投放分析）"),
        ("抖音补正文（269 条里 7 条正文短）",
         7, "抖音已有 91% 可展示，边际收益极小", "低"),
    ]
    for name, n, note, pri in items:
        print(f"  [{pri:>10}] {name}")
        print(f"               可增量 {n} 条 —— {note}")
    con.close()


if __name__ == "__main__":
    main()
