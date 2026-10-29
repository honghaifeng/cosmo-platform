#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场景词笔记独立分析轨道（品类场景词，非品牌内容）。

背景（2026-10-26 用户口径）：
  ① 无正文的笔记不能因为「没正文」就全部排除 —— 图片/视频里的信息
    （OCR/ASR）要参与综合判断；
  ② 非品牌笔记（场景/痛点/品类内容）也需要详细分析，不是一刀切当垃圾。

这类内容不进品牌横评（不归属品牌是事实，不是错误），但它们承载的是
「用户在什么场景下有什么痛点、用什么语言描述」—— 对内容运营是核心素材。

本脚本做三件事：
  1. 场景分类：熬夜恢复 / 睡眠改善 / 犯困疲劳 / 护肝养生 / 内服抗衰科普 /
     情绪压力 / 运动恢复 / 其他健康
  2. 痛点与用户语言提取：蜡黄、爆痘、失眠、便秘……（关键词规则，可解释）
  3. 写入 tags（追加场景标签）+ analysis_json（scene/painpoints/hits），
     供校验闸门与对外页面使用。打了场景标签的笔记才允许通过场景轨道质检。

用法：
  python3 crawler/scene_tag.py            # 全量补标（已标的跳过）
  python3 crawler/scene_tag.py --refresh  # 重标全部（规则升级后用）
"""
import argparse
import datetime
import json
import os
import re
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")

# 场景分类规则：按优先级取第一个命中的场景
SCENE_RULES = [
    ("熬夜恢复", re.compile(r"(熬夜|熬大夜|通宵|夜班|夜车|报复性熬夜)")),
    ("睡眠改善", re.compile(r"(失眠|睡不着|睡不好|早睡|助眠|深睡|睡眠质量|睡个好觉|入睡)")),
    ("犯困疲劳", re.compile(r"(犯困|困倦|疲劳|疲惫|没精神|乏力|提神|打工人|午休|下午困|精神状态)")),
    ("护肝养生", re.compile(r"(护肝|养肝|解酒|肝代谢|转氨酶|喝酒.{0,6}伤)")),
    ("情绪压力", re.compile(r"(压力|焦虑|情绪|内耗|emo|心累|精神内耗)")),
    ("运动恢复", re.compile(r"(健身|运动|跑步|撸铁|锻炼|体能)")),
    ("内服抗衰科普", re.compile(r"(抗衰|抗老|逆龄|衰老|胶囊|补剂|保健品|内服|口服|NAD|NMN|线粒体|成分|胶原蛋白)")),
]
SCENE_OTHER = "其他健康"

# 痛点/用户语言关键词（命中即记录，可多条）
PAIN_RULES = [
    ("脸色蜡黄", re.compile(r"(蜡黄|暗沉|黄垮脸|脸色差|面黄)")),
    ("痘痘肌肤", re.compile(r"(爆痘|长痘|闭口|烂脸)")),
    ("黑眼圈眼袋", re.compile(r"(黑眼圈|眼袋|熊猫眼)")),
    ("脱发发质", re.compile(r"(脱发|掉发|发际线|发质)")),
    ("入睡困难", re.compile(r"(睡不着|入睡难|翻来覆去|数羊)")),
    ("白天没精神", re.compile(r"(没精神|提不起劲|犯困|哈欠|打瞌睡)")),
    ("肠道便秘", re.compile(r"(便秘|肠胃|肠道|不通畅|排便)")),
    ("体重水肿", re.compile(r"(水肿|发胖|长胖|小肚子|代谢慢)")),
    ("精力不足", re.compile(r"(精力|体力跟不上|力不从心|疲惫感)")),
    ("情绪低落", re.compile(r"(emo|烦躁|心累|低落|焦虑)")),
]


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def analyze(title, body, tags):
    text = f"{title or ''} {body or ''} {tags or ''}"
    scene = None
    hits = []
    for name, rx in SCENE_RULES:
        m = rx.search(text)
        if m:
            scene = name
            hits.append(m.group(0))
            break
    if scene is None:
        scene = SCENE_OTHER
    pains = []
    for name, rx in PAIN_RULES:
        if rx.search(text):
            pains.append(name)
    return scene, pains, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="重标全部（默认跳过已标的）")
    args = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    if args.refresh:
        rows = con.execute(
            "SELECT id, title, body, tags, analysis_json FROM content "
            "WHERE platform='xhs' AND brand='品类场景词'").fetchall()
    else:
        # 增量：已有其他分析（SEO/关键词等）但还没打场景标签的也要补
        rows = con.execute(
            "SELECT id, title, body, tags, analysis_json FROM content "
            "WHERE platform='xhs' AND brand='品类场景词' "
            "AND (analysis_json IS NULL OR analysis_json='' OR analysis_json='{}' "
            "     OR json_extract(analysis_json,'$.scene') IS NULL)").fetchall()

    stats, pain_stats = {}, {}
    upd = []
    for r in rows:
        # 已有 analysis_json 且带 OCR/ASR 文本的，把媒体解读也并入语料再判一次
        media_text = ""
        for (mt,) in con.execute(
                "SELECT text FROM media_text WHERE content_id=?", (r["id"],)):
            media_text += mt + " "
        scene, pains, hits = analyze(r["title"], r["body"] or "", r["tags"])
        if media_text and scene == SCENE_OTHER:
            scene2, pains2, hits2 = analyze(r["title"], r["body"] + " " + media_text, r["tags"])
            if scene2 != SCENE_OTHER:
                scene, pains = scene2, sorted(set(pains) | set(pains2))
        try:
            aj = json.loads(r["analysis_json"] or "{}")
        except Exception:
            aj = {}
        aj.update({"scene": scene, "painpoints": pains, "hits": hits,
                   "media_assist": bool(media_text), "tagged_at": now()})
        # tags 追加场景标签（不覆盖原有话题标签）
        cur_tags = [t.strip() for t in (r["tags"] or "").split(",") if t.strip()]
        if scene not in cur_tags:
            cur_tags.insert(0, scene)
        upd.append((json.dumps(aj, ensure_ascii=False), ",".join(cur_tags), r["id"]))
        stats[scene] = stats.get(scene, 0) + 1
        for p in pains:
            pain_stats[p] = pain_stats.get(p, 0) + 1

    con.executemany("UPDATE content SET analysis_json=?, tags=? WHERE id=?", upd)
    con.commit()

    print(f"场景词分析完成：共 {len(upd)} 条")
    print("\n【场景分布】")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k:12s}{v:5d}")
    print("\n【痛点分布】")
    for k, v in sorted(pain_stats.items(), key=lambda x: -x[1]):
        print(f"  {k:12s}{v:5d}")
    con.close()


if __name__ == "__main__":
    main()
