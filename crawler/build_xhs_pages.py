#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_xhs_pages.py — 给「有正文但没有归档页」的小红书内容补本地仿原页。

背景：
  build_xhs_archive.py 只给 xhs_note 档案里 verified 的笔记出页（244 个），
  但 content 表里已校验的小红书内容是 753 条（去重 753 个 note_id），
  中间 520 条在原始数据表里点「本站页」是 404 —— 链接看着有，点开没有。

规则：
  · 只处理 platform='xhs' 且 verified=1 的内容（边界不变）
  · 已有 exports/xhs_archive/<note_id>.html 的不重写（幂等）
  · 图片优先用七牛映射（/cdn/media/...），拿不到映射就不放图，绝不塞过期签名链
  · 正文/评论/OCR/ASR 全量落地，正文不截断

用法：
  python3 crawler/build_xhs_pages.py --report
  python3 crawler/build_xhs_pages.py
"""
import argparse
import html
import json
import os
import re
import sqlite3
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "db", "cosmobounty.db")
OUT = os.path.join(PROJ, "exports", "xhs_archive")
CDN = os.path.join(PROJ, "data", "media_cdn.json")

NOTE_RX = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]{16,32})")

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#333;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}
.page{max-width:640px;margin:0 auto;padding:0 16px 60px}
.nav{display:flex;align-items:center;gap:10px;padding:14px 0;border-bottom:1px solid #f0f0f0;position:sticky;top:0;background:#fff;z-index:5}
.back{font-size:20px;color:#333;text-decoration:none} .nav b{font-weight:600;font-size:16px}
.author{display:flex;align-items:center;gap:10px;padding:14px 0 10px}
.ava{width:38px;height:38px;border-radius:50%;object-fit:cover;background:#f0f0f0;flex:0 0 auto}
.ava.ph{display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px;background:#ff2e4d}
.aname{font-size:15px;font-weight:500}
.aid{font-size:12px;color:#999;margin-top:2px}
h1{font-size:17px;line-height:1.5;margin:6px 0 10px;font-weight:600}
.txt{font-size:15px;line-height:1.75;white-space:pre-wrap;word-break:break-word}
.meta{color:#999;font-size:12px;margin:10px 0 16px}
.imgs{margin:14px 0 18px} .imgs img{width:100%;display:block;border-radius:6px}
.imgs.grid{display:grid;grid-template-columns:1fr 1fr;gap:6px} .imgs.grid img{height:210px;object-fit:cover}
.noimg{background:#f7f7f9;color:#aaa;font-size:13px;padding:26px;text-align:center;border-radius:8px;margin:14px 0}
.bar{display:flex;gap:28px;align-items:center;padding:14px 0;border-top:1px solid #f0f0f0;color:#666;font-size:13px}
.cmts{margin-top:22px;border-top:1px solid #f0f0f0;padding-top:14px}
.cmts-h{font-size:14px;font-weight:600;margin-bottom:12px}
.cmt{display:flex;gap:10px;margin-bottom:16px}
.cname{font-size:13px;color:#999} .ctext{font-size:14px;line-height:1.6;margin:3px 0}
.cmeta{font-size:11px;color:#bbb}
.ocrwrap{background:#f7f9fc;border-radius:8px;padding:12px 14px;margin:16px 0}
.ocr-h{font-size:13px;font-weight:600;color:#555;margin-bottom:8px}
.ocr{font-size:12.5px;line-height:1.7;color:#556;white-space:pre-wrap;background:#fff;border-left:3px solid #b6cdf5;padding:8px 10px;margin:6px 0;border-radius:4px}
.foot{margin-top:28px;padding-top:14px;border-top:1px solid #f0f0f0;color:#aaa;font-size:12px;line-height:1.8}
.muted{color:#aaa}
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def note_id_of(url):
    m = NOTE_RX.search(url or "")
    return m.group(1) if m else ""


def page_html(c, brand, title, body, author, ptime, url, nid, metrics,
              imgs, cmts, media_texts):
    like = metrics.get("likes") or 0
    coll = metrics.get("collects") or 0
    cm = metrics.get("comments") or 0
    sh = metrics.get("shares") or 0
    ava = (author or "?")[:1]
    img_html = ""
    if imgs:
        cls = "grid" if len(imgs) > 1 else "single"
        img_html = (f'<div class="imgs {cls}">' +
                    "".join(f'<a href="{esc(u)}" target="_blank">'
                            f'<img loading="lazy" src="{esc(u)}" referrerpolicy="no-referrer" '
                            f'onerror="this.parentElement.style.display=\'none\'"></a>' for u in imgs) +
                    "</div>")
    cmt_html = ""
    if cmts:
        cmt_html = '<div class="cmts"><div class="cmts-h">评论（%d）</div>%s</div>' % (
            len(cmts), "".join(
                f'<div class="cmt"><div><div class="cname">{esc(x[0] or "用户")}</div>'
                f'<div class="ctext">{esc(x[1])}</div>'
                f'<div class="cmeta">{x[2] or 0} 赞 · {esc(x[3] or "")}</div></div></div>'
                for x in cmts))
    ocr = [t for k, t in media_texts if k == "ocr"]
    asr = [t for k, t in media_texts if k == "asr"]
    mt_html = ""
    if ocr or asr:
        inner = ""
        if ocr:
            inner += '<div class="ocr-h">图片文字（OCR 提取）</div>' + "".join(
                f'<div class="ocr">{esc(t)}</div>' for t in ocr[:8])
        if asr:
            inner += '<div class="ocr-h">视频口播（ASR 转写）</div>' + "".join(
                f'<div class="ocr">{esc(t)}</div>' for t in asr[:3])
        mt_html = f'<div class="ocrwrap">{inner}</div>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title or '笔记')} - 小红书</title>
<style>{CSS}</style></head><body><div class="page">
<div class="nav"><a class="back" href="index.html">‹</a><b>笔记</b></div>
<div class="author"><div class="ava ph">{esc(ava)}</div>
 <div><div class="aname">{esc(author or '未知作者')}</div>
 <div class="aid">笔记 ID {esc(nid)}</div></div></div>
<h1>{esc(title or '（无标题）')}</h1>
<div class="txt">{esc(body)}</div>
<div class="meta">{esc(ptime or '')} · 归属 {esc(brand)}</div>
{img_html or '<div class="noimg">这条的图片未落盘（采集时只拿到临时签名链接，已过期）</div>'}
{mt_html}
<div class="bar">
 <span>♡ 赞 {like}</span><span>☆ 收藏 {coll}</span><span>💬 评论 {cm}</span><span>↗ 转发 {sh}</span>
</div>
{cmt_html}
<div class="foot">本页是「逆龄观察」在采集当下保存的内容快照，用于数据可复核；原文可能已修改或删除。<br>
原文链接：<a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(url)}</a><br>
内容归属按统一规则判定，六个品牌同一把尺子。</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的页面")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    cdn = json.load(open(CDN, encoding="utf-8")) if os.path.exists(CDN) else {}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT id, brand, title, body, author, publish_time, url, metrics_json, media_json "
        "FROM content WHERE platform='xhs' AND verified=1").fetchall()

    todo = []
    for r in rows:
        nid = note_id_of(r["url"])
        if not nid:
            continue
        if os.path.isfile(os.path.join(OUT, nid + ".html")) and not a.force:
            continue
        todo.append((nid, r))

    print(f"待生成 {len(todo)} 个（已有页面 {len(set(note_id_of(r['url']) for r in rows)) - len(todo)} 个）")
    if a.report:
        con.close()
        return

    made = 0
    for nid, r in todo:
        try:
            metrics = json.loads(r["metrics_json"] or "{}")
        except Exception:
            metrics = {}
        try:
            media = json.loads(r["media_json"] or "{}")
        except Exception:
            media = {}
        # 图片：只有能在七牛映射里查到的才放（其余是过期签名链，放上去就是破图）。
        # 落盘扩展名不固定（webp 占多数，早期 jpg），按顺序都试一遍。
        imgs = []
        for ext in ("webp", "jpg", "jpeg", "png"):
            key = f"{r['brand']}/post/xhs:{nid}.{ext}"
            if key in cdn:
                imgs.append("/cdn/media/" + cdn[key].split("/media/", 1)[-1])
                break
        cmts = [(None, x["body"], x["like_count"], x["publish_time"])
                for x in con.execute(
                    "SELECT body, like_count, publish_time FROM comment "
                    "WHERE content_id=? ORDER BY like_count DESC LIMIT 20", (r["id"],))]
        mts = [(x["kind"], x["text"]) for x in con.execute(
            "SELECT kind, text FROM media_text WHERE content_id=? ORDER BY source", (r["id"],))]
        h = page_html(c=None, brand=r["brand"], title=r["title"], body=r["body"],
                      author=r["author"], ptime=r["publish_time"], url=r["url"],
                      nid=nid, metrics=metrics, imgs=imgs, cmts=cmts, media_texts=mts)
        with open(os.path.join(OUT, nid + ".html"), "w", encoding="utf-8") as f:
            f.write(h)
        made += 1
        if made % 50 == 0:
            print(f"  {made}/{len(todo)}", flush=True)

    con.close()
    print(f"完成：新生成 {made} 个仿原页 → {OUT}")


if __name__ == "__main__":
    main()
