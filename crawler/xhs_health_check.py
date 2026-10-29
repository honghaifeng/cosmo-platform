#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书数据完整度体检：一次跑出「哪些全了、哪些还缺」。

口径说明（避免以后再误判）：
  · 元数据层（发布时间/作者/账号ID/互动数）—— 来自搜索卡片，可全量获取
  · 内容层（正文/标签/图片URL）—— 来自详情接口，需逐条打开笔记页
  · 媒体落盘（mp4/jpg 文件）—— CDN 直链约 24h 过期，事后补下会 403
    ⇒ 唯一可靠路径是「采集时当场落盘」（xhs_human_harvest.py 的 grab_media）
"""
import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "xhs_archive")
MEDIA = os.path.join(ARCHIVE, "media")
NOTES = os.path.join(ARCHIVE, "notes.jsonl")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
BRANDS = ["COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"]


def load_notes():
    rows = []
    if os.path.exists(NOTES):
        for line in open(NOTES, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def report():
    rows = load_notes()
    total = len(rows)
    brand = [r for r in rows if r.get("brand_mention")]
    scene = [r for r in rows if r.get("brand") == "品类场景词"]

    mp4 = [f for f in os.listdir(MEDIA) if f.endswith(".mp4")] if os.path.isdir(MEDIA) else []
    jpg = [f for f in os.listdir(MEDIA) if f.endswith(".jpg")] if os.path.isdir(MEDIA) else []
    size_mb = sum(os.path.getsize(os.path.join(MEDIA, f)) for f in os.listdir(MEDIA)) / 1024 / 1024 \
        if os.path.isdir(MEDIA) else 0

    def n(fn):
        return sum(1 for r in rows if fn(r))

    print("=" * 62)
    print(f"小红书数据体检 · 采集 {total} 条 / 已归属品牌 {len(brand)} 条 / 品类场景 {len(scene)} 条")
    print("=" * 62)

    print("\n【一、元数据层 — 已达标，可直接用于分析】")
    meta = [
        ("发布时间", n(lambda r: r.get("publish_time"))),
        ("账号 ID", n(lambda r: r.get("author_id"))),
        ("真实昵称", n(lambda r: r.get("author") and "*" not in r["author"])),
        ("点赞数", n(lambda r: (r.get("likes") or 0) > 0)),
        ("原文链接", n(lambda r: r.get("note_url"))),
    ]
    for k, v in meta:
        print(f"  {k:10s}{v:6d} / {total}   {100 * v / max(total, 1):5.1f}%")

    print("\n【二、内容层 — 受详情接口逐条获取限制】")
    content = [
        ("正文", n(lambda r: r.get("desc"))),
        ("话题标签", n(lambda r: r.get("tags"))),
        ("图片 URL", n(lambda r: r.get("images"))),
        ("视频 URL", n(lambda r: r.get("video_url"))),
    ]
    for k, v in content:
        print(f"  {k:10s}{v:6d} / {total}   {100 * v / max(total, 1):5.1f}%"
              f"   缺口 {total - v}")

    print("\n【三、媒体落盘 — 已下载的文件】")
    vid_need = [r for r in rows if r.get("video_url")]
    vid_have = [r for r in vid_need if os.path.exists(os.path.join(MEDIA, f"{r['note_id']}.mp4"))]
    img_ref = sum(len(r.get("images") or []) for r in rows)
    print(f"  视频 mp4 {len(mp4):4d} 个（引用 {len(vid_need)} → 落盘 {len(vid_have)}，"
          f"{100 * len(vid_have) / max(len(vid_need), 1):.0f}%）")
    print(f"  图片 jpg {len(jpg):4d} 张（引用 {img_ref} → 落盘 {len(jpg)}，"
          f"{100 * len(jpg) / max(img_ref, 1):.0f}%）")
    print(f"  占用空间 {size_mb:.0f} MB")
    if img_ref > len(jpg):
        print("  ⚠️ 未落盘图片的 CDN 直链已过期（约 24h TTL），只能靠「采集时当场落盘」补齐")

    print("\n【四、六品牌明细（仅已归属）】")
    hdr = f"  {'品牌':14s}{'条数':>5s}{'正文':>6s}{'正文率':>8s}{'视频':>5s}{'图片':>6s}{'账号':>6s}"
    print(hdr)
    for b in BRANDS:
        sub = [r for r in brand if r.get("brand") == b]
        if not sub:
            continue
        bd = sum(1 for r in sub if r.get("desc"))
        vv = sum(1 for r in sub if os.path.exists(os.path.join(MEDIA, f"{r['note_id']}.mp4")))
        ii = sum(1 for r in sub for i in range(len(r.get("images") or []))
                 if os.path.exists(os.path.join(MEDIA, f"{r['note_id']}_{i}.jpg")))
        ac = len(set(r.get("author_id") for r in sub if r.get("author_id")))
        print(f"  {b:14s}{len(sub):5d}{bd:6d}{100 * bd / len(sub):7.0f}%{vv:5d}{ii:6d}{ac:6d}")

    print("\n【五、证据分级（判断内容可信度）】")
    ev = Counter(r.get("evidence") for r in rows if r.get("brand_mention"))
    for k, label in [("strong", "强证据  正文实质提及品牌"), ("weak", "弱证据  歧义词+品类语境"),
                     ("tag", "蹭词位  品牌只在话题标签")]:
        print(f"  {label:28s}{ev.get(k, 0):5d}")

    if os.path.exists(DB):
        con = sqlite3.connect(DB)
        try:
            print("\n【六、数据库（供线上专家查询）】")
            for p, cnt, tm, bd in con.execute(
                    """SELECT platform, COUNT(*),
                              SUM(CASE WHEN publish_time IS NOT NULL AND publish_time<>'' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN body IS NOT NULL AND body<>'' THEN 1 ELSE 0 END)
                       FROM content GROUP BY 1 ORDER BY 2 DESC"""):
                print(f"  {p:12s}{cnt:6d} 条 | 有时间 {tm:5d} | 有正文 {bd:5d}")
        finally:
            con.close()

    print("\n【七、还缺什么 · 怎么补】")
    gaps = []
    if n(lambda r: r.get("desc")) < len(brand):
        gaps.append(("品牌内容正文不全",
                     f"已归属 {len(brand)} 条中 {sum(1 for r in brand if r.get('desc'))} 条有正文",
                     "每日 08:30 补抓（拟人采集 30 条/轮，命中即入库）"))
    if img_ref > len(jpg):
        gaps.append(("图片未落盘",
                     f"引用 {img_ref} 张，已存 {len(jpg)} 张",
                     "只能现采现下：采集当下写盘，历史 URL 已过期"))
    if len(vid_have) < len(vid_need):
        gaps.append(("视频未落盘",
                     f"引用 {len(vid_need)} 条，已存 {len(vid_have)} 条",
                     "重跑 download_xhs_media.py --videos（多段 URL 已修复）"))
    un = [r for r in rows if not r.get("brand_mention")]
    if un:
        gaps.append(("未归属内容",
                     f"{len(un)} 条不属任何品牌（蹭词/泛品类）",
                     "按设计不入品牌横评；如需分析可看「品类场景词」类"))
    if not gaps:
        print("  无明显缺口")
    for k, now, how in gaps:
        print(f"  · {k}\n      现状：{now}\n      补法：{how}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON（便于程序读取）")
    args = ap.parse_args()
    if args.json:
        rows = load_notes()
        print(json.dumps({
            "total": len(rows),
            "brand": len([r for r in rows if r.get("brand_mention")]),
            "with_body": len([r for r in rows if r.get("desc")]),
            "media_mp4": len([f for f in os.listdir(MEDIA) if f.endswith(".mp4")]) if os.path.isdir(MEDIA) else 0,
            "media_jpg": len([f for f in os.listdir(MEDIA) if f.endswith(".jpg")]) if os.path.isdir(MEDIA) else 0,
        }, ensure_ascii=False))
    else:
        report()
